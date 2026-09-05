#!/usr/local/bin/python3
"""CLI for the unified "block rules" feature (host-only or host+domain
blocks, each with an optional schedule) -- invoked via configd (see
actions_gowiththeflow.conf's `rule_create`/`rule_edit`/`rule_delete`/
`rule_set_enabled`/`rule_override` actions), same reasoning as
block_host.py: PHP runs as `www` and can't touch pf, write to this
plugin's own database, or (for domain rules) drive Unbound's own model
directly. Prints one JSON object to stdout and always exits 0 -- see
block_host.py's own module docstring for why (configdpRun() silently
discards stdout on a non-zero exit).

The client-address guard ("don't let the browsing session block its own
current IP") is PHP-only -- only PHP can see who's making the HTTP
request -- and lives in Api/BlockrulesController.php. Everything else
(own-address/subnet-edge refusal for host rules, schedule validation) is
enforced here too, defense-in-depth, the same posture block_host.py
already takes for its own guards.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import block_rules_engine
import block_schedule
import blocklist
import db

DB_PATH = "/var/db/gowiththeflow/flows.db"
SETTINGS_PATH = "/var/etc/gowiththeflow.json"


def _load_local_subnets() -> list[str]:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f).get("local_subnets", [])
    except (OSError, ValueError):
        return []


def _lookup_identity(conn, ip: str) -> tuple[str | None, str | None]:
    """Same "most-recent-wins" lookup block_host.py already uses."""
    row = conn.execute(
        "SELECT hostname, mac FROM local_host_identity WHERE ip = ? ORDER BY updated_at DESC LIMIT 1",
        (ip,),
    ).fetchone()
    if row is None:
        return None, None
    return row["hostname"], row["mac"]


def _parse_devices(conn, raw: str) -> tuple[list[dict] | None, str | None]:
    """`raw` is a comma-separated list of already-resolved IPs (the PHP
    controller resolves whatever the user typed -- IP or known hostname
    -- to a definite IP per device before this ever runs, same split
    block_host.py already has for one device). Returns (devices, error);
    each device gets its own hostname/mac snapshot via the same lookup a
    single device has always used. Rejects an empty list outright -- a
    rule with no devices does nothing and almost certainly means a
    client-side bug, not an intentional empty group."""
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        return None, "at least one device is required"
    devices = []
    for token in tokens:
        ip = blocklist.normalize_ip(token)
        if ip is None:
            return None, f"not a valid single IP address: {token!r}"
        hostname, mac = _lookup_identity(conn, ip)
        devices.append({"ip": ip, "hostname": hostname, "mac": mac})
    return devices, None


def _format_conflicts(conflicts: dict[str, str], devices: list[dict]) -> str:
    """{ip: owning rule's name}, plus the devices just being checked (so
    a known hostname can be shown instead of a bare IP) -> 'kids-tablet
    is already being blocked by rule "Test Group"' -- one sentence per
    conflicting device (several of the devices just submitted could each
    already belong to a *different* rule) -- naming the actual
    conflicting rule, not just the device, so the user knows exactly
    where to go to resolve it rather than having to go hunt for which
    rule owns that device themselves."""
    hostnames = {d["ip"]: d["hostname"] for d in devices}
    return "; ".join(
        f'{hostnames.get(ip) or ip} is already being blocked by rule "{rule_name}"'
        for ip, rule_name in sorted(conflicts.items())
    )


def _validate_schedule(raw: str | None) -> tuple[str | None, str | None]:
    """Returns (schedule_json_to_store, error) -- an empty/absent schedule
    is valid and means "always" (schedule_json=NULL), matching this
    rule's behavior before this feature existed."""
    if not raw:
        return None, None
    try:
        block_schedule.parse_schedule(raw)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        return None, f"invalid schedule: {e}"
    return raw, None


def cmd_create(args: argparse.Namespace) -> dict:
    conn = db.connect(DB_PATH)
    db.init_schema(conn)

    devices, devices_error = _parse_devices(conn, args.devices)
    if devices_error is not None:
        return {"status": "error", "error": devices_error}

    schedule_json, schedule_error = _validate_schedule(args.schedule)
    if schedule_error is not None:
        return {"status": "error", "error": schedule_error}

    now = int(time.time())
    ips = [d["ip"] for d in devices]

    if args.type == "host":
        local_subnets = _load_local_subnets()
        for ip in ips:
            refusal = blocklist.refuse_reason_for_host_block(ip, local_subnets)
            if refusal is not None:
                return {"status": "error", "error": f"{ip}: {refusal}"}
        conflicts = block_rules_engine.devices_conflicting_with_other_host_rules(conn, ips)
        if conflicts:
            return {"status": "error", "error": _format_conflicts(conflicts, devices)}
        rule_id = block_rules_engine.create_host_rule(conn, args.name, devices, args.by, args.reason, now)
        # A host rule's own schedule, if any, is applied via a follow-up
        # `edit` (create_host_rule() always inserts the "always" shape
        # first) -- keeps this one insert path single-purpose rather
        # than juggling two shapes.
        if schedule_json is not None:
            block_rules_engine.update_rule(conn, rule_id, args.name, devices, None, schedule_json, now)
    else:
        domains = (args.domains or "").strip()
        if not domains:
            return {"status": "error", "error": "at least one domain is required for a domain rule"}
        rule_id = block_rules_engine.create_domain_rule(
            conn, args.name, devices, domains, schedule_json, args.by, args.reason, now
        )

    decision = block_rules_engine.apply_rule(conn, rule_id, now)
    return {
        "status": "ok", "id": rule_id, "name": args.name, "devices": ips,
        "blocked": decision.should_be_blocked if decision else None,
    }


def cmd_edit(args: argparse.Namespace) -> dict:
    conn = db.connect(DB_PATH)
    db.init_schema(conn)

    row = block_rules_engine.get_rule(conn, args.id)
    if row is None:
        return {"status": "error", "error": f"no such rule: {args.id}"}

    devices, devices_error = _parse_devices(conn, args.devices)
    if devices_error is not None:
        return {"status": "error", "error": devices_error}

    schedule_json, schedule_error = _validate_schedule(args.schedule)
    if schedule_error is not None:
        return {"status": "error", "error": schedule_error}

    if row["rule_type"] == "host":
        ips = [d["ip"] for d in devices]
        local_subnets = _load_local_subnets()
        for ip in ips:
            refusal = blocklist.refuse_reason_for_host_block(ip, local_subnets)
            if refusal is not None:
                return {"status": "error", "error": f"{ip}: {refusal}"}
        conflicts = block_rules_engine.devices_conflicting_with_other_host_rules(
            conn, ips, exclude_rule_id=args.id
        )
        if conflicts:
            return {"status": "error", "error": _format_conflicts(conflicts, devices)}

    now = int(time.time())
    ok = block_rules_engine.update_rule(conn, args.id, args.name, devices, args.domains, schedule_json, now)
    if not ok:
        return {"status": "error", "error": f"no such rule: {args.id}"}
    return {"status": "ok", "id": args.id}


def cmd_duplicate(args: argparse.Namespace) -> dict:
    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    new_id = block_rules_engine.duplicate_rule(conn, args.id, int(time.time()))
    if new_id is None:
        return {"status": "error", "error": f"no such rule: {args.id}"}
    return {"status": "ok", "id": new_id}


def cmd_delete(args: argparse.Namespace) -> dict:
    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    ok = block_rules_engine.delete_rule(conn, args.id, int(time.time()))
    if not ok:
        return {"status": "error", "error": f"no such rule: {args.id}"}
    return {"status": "ok", "id": args.id}


def cmd_set_enabled(args: argparse.Namespace) -> dict:
    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    enabled = bool(int(args.enabled))

    # Only create/edit ever ran this guard before -- enabling a rule
    # (most notably a freshly-duplicated one, which starts disabled
    # *specifically* to avoid instantly double-blocking its own source
    # rule's devices) could silently flip a second host rule on for a
    # device another enabled rule already fully covers, with no error at
    # all: found live after duplicating a rule and enabling it without
    # first disabling (or re-pointing the devices of) the original.
    if enabled:
        row = block_rules_engine.get_rule(conn, args.id)
        if row is None:
            return {"status": "error", "error": f"no such rule: {args.id}"}
        if row["rule_type"] == "host":
            conflicts = block_rules_engine.devices_conflicting_with_other_host_rules(
                conn, block_rules_engine.device_ips(row), exclude_rule_id=args.id
            )
            if conflicts:
                return {"status": "error", "error": _format_conflicts(conflicts, json.loads(row["devices"]))}

    ok = block_rules_engine.set_enabled(conn, args.id, enabled, int(time.time()))
    if not ok:
        return {"status": "error", "error": f"no such rule: {args.id}"}
    return {"status": "ok", "id": args.id, "enabled": enabled}


def cmd_override(args: argparse.Namespace) -> dict:
    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    result = block_rules_engine.set_override(conn, args.id, args.state, int(time.time()))
    result.setdefault("id", args.id)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create")
    p_create.add_argument("--type", choices=["host", "domain"], required=True)
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--devices", required=True, help="comma-separated IPs")
    p_create.add_argument("--domains", default=None)
    p_create.add_argument("--schedule", default=None)
    p_create.add_argument("--by", default=None)
    p_create.add_argument("--reason", default=None)
    p_create.set_defaults(func=cmd_create)

    p_edit = sub.add_parser("edit")
    p_edit.add_argument("--id", type=int, required=True)
    p_edit.add_argument("--name", required=True)
    p_edit.add_argument("--devices", required=True, help="comma-separated IPs")
    p_edit.add_argument("--domains", default=None)
    p_edit.add_argument("--schedule", default=None)
    p_edit.set_defaults(func=cmd_edit)

    p_delete = sub.add_parser("delete")
    p_delete.add_argument("--id", type=int, required=True)
    p_delete.set_defaults(func=cmd_delete)

    p_duplicate = sub.add_parser("duplicate")
    p_duplicate.add_argument("--id", type=int, required=True)
    p_duplicate.set_defaults(func=cmd_duplicate)

    p_set_enabled = sub.add_parser("set_enabled")
    p_set_enabled.add_argument("--id", type=int, required=True)
    p_set_enabled.add_argument("--enabled", choices=["0", "1"], required=True)
    p_set_enabled.set_defaults(func=cmd_set_enabled)

    p_override = sub.add_parser("override")
    p_override.add_argument("--id", type=int, required=True)
    p_override.add_argument("--state", choices=["blocked", "unblocked"], required=True)
    p_override.set_defaults(func=cmd_override)

    args = parser.parse_args(argv)
    result = args.func(args)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

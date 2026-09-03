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
    ip = blocklist.normalize_ip(args.ip)
    if ip is None:
        return {"status": "error", "error": f"not a valid single IP address: {args.ip!r}"}

    schedule_json, schedule_error = _validate_schedule(args.schedule)
    if schedule_error is not None:
        return {"status": "error", "error": schedule_error}

    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    hostname, mac = _lookup_identity(conn, ip)
    now = int(time.time())

    if args.type == "host":
        refusal = blocklist.refuse_reason_for_host_block(ip, _load_local_subnets())
        if refusal is not None:
            return {"status": "error", "error": refusal}
        rule_id = block_rules_engine.create_host_rule(conn, ip, hostname, mac, args.by, args.reason, now)
        # A host rule's own schedule, if any, is applied via a follow-up
        # `edit` (create_host_rule() upserts the "always" shape, matching
        # block_host.py's own quick-block action) -- keeps this one
        # upsert path single-purpose rather than juggling two shapes.
        if schedule_json is not None:
            block_rules_engine.update_rule(conn, rule_id, None, schedule_json, now)
    else:
        domains = (args.domains or "").strip()
        if not domains:
            return {"status": "error", "error": "at least one domain is required for a domain rule"}
        rule_id = block_rules_engine.create_domain_rule(
            conn, ip, hostname, mac, domains, schedule_json, args.by, args.reason, now
        )

    decision = block_rules_engine.apply_rule(conn, rule_id, now)
    return {
        "status": "ok", "id": rule_id, "ip": ip, "hostname": hostname,
        "blocked": decision.should_be_blocked if decision else None,
    }


def cmd_edit(args: argparse.Namespace) -> dict:
    schedule_json, schedule_error = _validate_schedule(args.schedule)
    if schedule_error is not None:
        return {"status": "error", "error": schedule_error}

    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    now = int(time.time())
    ok = block_rules_engine.update_rule(conn, args.id, args.domains, schedule_json, now)
    if not ok:
        return {"status": "error", "error": f"no such rule: {args.id}"}
    return {"status": "ok", "id": args.id}


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
    ok = block_rules_engine.set_enabled(conn, args.id, bool(int(args.enabled)), int(time.time()))
    if not ok:
        return {"status": "error", "error": f"no such rule: {args.id}"}
    return {"status": "ok", "id": args.id, "enabled": bool(int(args.enabled))}


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
    p_create.add_argument("--ip", required=True)
    p_create.add_argument("--domains", default=None)
    p_create.add_argument("--schedule", default=None)
    p_create.add_argument("--by", default=None)
    p_create.add_argument("--reason", default=None)
    p_create.set_defaults(func=cmd_create)

    p_edit = sub.add_parser("edit")
    p_edit.add_argument("--id", type=int, required=True)
    p_edit.add_argument("--domains", default=None)
    p_edit.add_argument("--schedule", default=None)
    p_edit.set_defaults(func=cmd_edit)

    p_delete = sub.add_parser("delete")
    p_delete.add_argument("--id", type=int, required=True)
    p_delete.set_defaults(func=cmd_delete)

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

#!/usr/local/bin/python3
"""CLI wrapping blocklist.py's block/unblock/sync operations, invoked via
configd (see actions_gowiththeflow.conf's `block`/`unblock`/`sync_blocked`
actions) since PHP runs as `www` and can't touch pf or write to this
plugin's own database directly -- the same "PHP reads, Python writes"
split this project already follows everywhere else.

Prints one JSON object to stdout and always exits 0 -- confirmed live
(instantiating OPNsense\\Core\\Backend directly and calling
configdpRun() against this exact action) that configd's PHP client
returns an EMPTY string, not the script's real stdout, when the
underlying process exits non-zero. A non-zero exit here would silently
throw away the very error detail (e.g. "refusing to block the
firewall's own address") the caller needs to show the user, so success
vs. failure is encoded *only* in the JSON `status` field
("ok"/"error"), never in the process exit code.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import blocklist
import block_rules_engine
import db

DB_PATH = "/var/db/gowiththeflow/flows.db"
TABLE_FILE_PATH = "/var/db/gowiththeflow/blocked_hosts.tbl"
SETTINGS_PATH = "/var/etc/gowiththeflow.json"


def _load_local_subnets() -> list[str]:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f).get("local_subnets", [])
    except (OSError, ValueError):
        return []


def _lookup_identity(conn, ip: str) -> tuple[str | None, str | None]:
    """(hostname, mac) for the most recently updated local_host_identity
    row for this IP, if any -- same "most recent wins" idiom
    LiveController.php's own local_host_identity lookups already use,
    since a MAC can be reassigned a different IP over time and multiple
    rows could technically share one."""
    row = conn.execute(
        "SELECT hostname, mac FROM local_host_identity WHERE ip = ? ORDER BY updated_at DESC LIMIT 1",
        (ip,),
    ).fetchone()
    if row is None:
        return None, None
    return row["hostname"], row["mac"]


def cmd_block(args: argparse.Namespace) -> dict:
    ip = blocklist.normalize_ip(args.ip)
    if ip is None:
        return {"status": "error", "error": f"not a valid single IP address: {args.ip!r}"}

    refusal = blocklist.refuse_reason_for_host_block(ip, _load_local_subnets())
    if refusal is not None:
        return {"status": "error", "error": refusal}

    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    hostname, mac = _lookup_identity(conn, ip)
    now = int(time.time())
    blocklist.add_block(conn, ip, hostname, mac, args.by, args.reason, now)
    # Keeps the block-rules feature's unified page in lockstep with this
    # pre-existing quick-block action -- a block made here shows up there
    # too, as an "always" host rule, with no separate migration step.
    block_rules_engine.create_host_rule(conn, ip, hostname, mac, args.by, args.reason, now)

    warnings = []
    if not blocklist.rules_present():
        # Not fatal -- the DB row and table file are already correct, so
        # this self-heals the moment `configctl filter reload` next runs
        # (e.g. the next post-install/upgrade, or the daemon's own
        # periodic reconcile calling sync_pf() again does NOT fix this
        # specific gap by itself, since the rules -- not just the table
        # contents -- are what's missing; surfaced as a warning so the
        # caller knows to investigate rather than assuming the block is
        # live).
        warnings.append(
            "gowiththeflow_blocked has no matching rules in the live ruleset yet -- "
            "run 'configctl filter reload' (this block is recorded but may not be enforced)"
        )
    sync_result = blocklist.sync_pf(conn, TABLE_FILE_PATH)
    if sync_result.returncode != 0:
        warnings.append(f"pfctl -T replace reported an error: {sync_result.stderr.strip()}")

    for kill_result in blocklist.kill_states(ip):
        if kill_result.returncode not in (0, 1):
            # pfctl -k exits 1 for "0 states killed" (nothing matched) --
            # only surface a genuinely unexpected exit code.
            warnings.append(f"pfctl -k reported an error: {kill_result.stderr.strip()}")

    return {"status": "ok", "ip": ip, "hostname": hostname, "warnings": warnings}


def cmd_unblock(args: argparse.Namespace) -> dict:
    ip = blocklist.normalize_ip(args.ip)
    if ip is None:
        return {"status": "error", "error": f"not a valid single IP address: {args.ip!r}"}

    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    blocklist.remove_block(conn, ip)
    # Same lockstep reasoning as cmd_block above -- without this, the
    # mirrored "always" host rule would still be enabled, and the
    # scheduler's own reconcile tick would silently re-block this exact
    # IP within the next ~60s of the user unblocking it here.
    conn.execute("DELETE FROM block_rules WHERE rule_type = 'host' AND local_ip = ?", (ip,))
    conn.commit()
    sync_result = blocklist.sync_pf(conn, TABLE_FILE_PATH)

    warnings = []
    if sync_result.returncode != 0:
        warnings.append(f"pfctl -T replace reported an error: {sync_result.stderr.strip()}")
    return {"status": "ok", "ip": ip, "warnings": warnings}


def cmd_sync(_args: argparse.Namespace) -> dict:
    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    sync_result = blocklist.sync_pf(conn, TABLE_FILE_PATH)
    if sync_result.returncode != 0:
        return {"status": "error", "error": sync_result.stderr.strip()}
    return {"status": "ok", "count": len(blocklist.list_blocked(conn))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_block = sub.add_parser("block")
    p_block.add_argument("--ip", required=True)
    p_block.add_argument("--by", default=None)
    p_block.add_argument("--reason", default=None)
    p_block.set_defaults(func=cmd_block)

    p_unblock = sub.add_parser("unblock")
    p_unblock.add_argument("--ip", required=True)
    p_unblock.set_defaults(func=cmd_unblock)

    p_sync = sub.add_parser("sync")
    p_sync.set_defaults(func=cmd_sync)

    args = parser.parse_args(argv)
    result = args.func(args)
    print(json.dumps(result))
    # Always 0 -- see the module docstring on why a non-zero exit here
    # would silently discard the JSON error detail before it ever
    # reaches PHP.
    return 0


if __name__ == "__main__":
    sys.exit(main())

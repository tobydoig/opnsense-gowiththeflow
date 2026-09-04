"""CLI: lists currently-uncategorized hostnames by real traffic volume
(the same query ToptalkersController::uncategorizedAction() uses for
the GUI's own "Uncategorized Hosts" tab), and re-applies today's
category logic across already-recorded history.

Category is stamped once when a connection is first written and never
revisited (see manual_categories.py's own docstring) -- growing
domain_categories/ (or a v2fly upstream refresh) only affects
*newly-observed* traffic until this is run. Not a live daemon action:
run by hand, or via the Settings page's "Recategorize History" button
(see actions_gowiththeflow.conf).

`list-uncategorized` replaces manually exporting the GUI's own tab to a
CSV for review -- same data, straight from the database. `apply` uses
categories.resolve_category() (manual overrides, then the v2fly-based
matcher) -- the exact same precedence gowiththeflowd.py uses live, via
the same shared function, so an already-recorded connection's category
can never drift from what a freshly-observed one right now would get.

Same shape as block_host.py/block_rules.py: always exits 0, JSON
output only.
"""

from __future__ import annotations

import argparse
import json
import time

import categories
import category_updater
import db

DB_PATH = "/var/db/gowiththeflow/flows.db"
CATEGORY_CACHE_DIR = "/var/db/gowiththeflow/categories"
# Matches DbApiControllerBase::HOURLY_RETENTION_DAYS -- same threshold
# the GUI uses to pick which rollup table can actually answer a given
# date range.
HOURLY_RETENTION_DAYS = 8

# Every table that carries a peer_hostname/category pair (see db.py's
# own schema) -- listed explicitly since nothing there already exposes
# "every table with these two columns" as its own thing.
CATEGORIZED_TABLES = ("connections_raw", "live_sessions", "rollup_hourly", "rollup_daily")


def _build_matcher() -> categories.CategoryMatcher:
    return categories.CategoryMatcher(category_updater.load_cached_files(CATEGORY_CACHE_DIR))


def cmd_list_uncategorized(args: argparse.Namespace) -> dict:
    conn = db.connect(DB_PATH)
    cutoff = int(time.time()) - args.days * 86400
    table = "rollup_hourly" if args.days <= HOURLY_RETENTION_DAYS else "rollup_daily"
    rows = conn.execute(
        f"""
        SELECT
          r1.peer_hostname,
          SUM(r1.conn_count) AS connections,
          SUM(r1.bytes_in) AS bytes_in, SUM(r1.bytes_out) AS bytes_out
        FROM {table} r1
        WHERE r1.bucket_start >= ? AND r1.peer_hostname IS NOT NULL
        GROUP BY r1.peer_hostname
        HAVING (
          SELECT r2.category FROM {table} r2
          WHERE r2.peer_hostname = r1.peer_hostname AND r2.bucket_start >= ?
          ORDER BY r2.bucket_start DESC LIMIT 1
        ) IS NULL
        ORDER BY (bytes_in + bytes_out) DESC
        LIMIT ?
        """,
        (cutoff, cutoff, args.limit),
    ).fetchall()
    hosts = [
        {
            "hostname": row["peer_hostname"],
            "connections": row["connections"],
            "bytes_in": row["bytes_in"],
            "bytes_out": row["bytes_out"],
            "bytes_total": row["bytes_in"] + row["bytes_out"],
        }
        for row in rows
    ]
    return {"status": "ok", "days": args.days, "table": table, "hosts": hosts}


def cmd_apply(args: argparse.Namespace) -> dict:
    conn = db.connect(DB_PATH)
    matcher = _build_matcher()

    hostnames: set[str] = set()
    for table in CATEGORIZED_TABLES:
        hostnames.update(
            row["peer_hostname"]
            for row in conn.execute(
                f"SELECT DISTINCT peer_hostname FROM {table} WHERE peer_hostname IS NOT NULL"
            )
        )

    hosts_changed = 0
    rows_updated = 0
    for hostname in sorted(hostnames):
        new_category = categories.resolve_category(hostname, matcher)
        host_rows_updated = 0
        for table in CATEGORIZED_TABLES:
            # "IS NOT ?" (not "!= ?") so this correctly counts a row as
            # changed when moving to/from NULL, not just between two
            # non-null categories.
            cur = conn.execute(
                f"UPDATE {table} SET category = ? WHERE peer_hostname = ? AND category IS NOT ?",
                (new_category, hostname, new_category),
            )
            host_rows_updated += cur.rowcount
        if host_rows_updated:
            hosts_changed += 1
            rows_updated += host_rows_updated

    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()
    return {
        "status": "ok",
        "dry_run": args.dry_run,
        "hostnames_checked": len(hostnames),
        "hostnames_changed": hosts_changed,
        "rows_updated": rows_updated,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-uncategorized")
    p_list.add_argument("--days", type=int, default=32)
    p_list.add_argument("--limit", type=int, default=500)
    p_list.set_defaults(func=cmd_list_uncategorized)

    p_apply = sub.add_parser("apply")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.set_defaults(func=cmd_apply)

    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except Exception as e:
        result = {"status": "error", "error": repr(e)}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

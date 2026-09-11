#!/usr/local/bin/python3
"""CLI entrypoint for the "Sync now" button on the Settings page (see
actions_gowiththeflow.conf's `sync_reservations` action) -- a thin
wrapper over reservation_gate.reconcile(), invoked via configd since PHP
can't touch pf/arp/this plugin's own database directly, same "PHP reads,
Python writes" split this project follows everywhere else.

Prints one JSON object to stdout and always exits 0 -- see
block_host.py's own module docstring for why (configdpRun() silently
discards stdout on a non-zero exit).
"""

from __future__ import annotations

import json
import sys
import time

import db
import reservation_gate

DB_PATH = "/var/db/gowiththeflow/flows.db"
SETTINGS_PATH = "/var/etc/gowiththeflow.json"


def _load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main() -> int:
    conn = db.connect(DB_PATH)
    db.init_schema(conn)
    result = reservation_gate.reconcile(conn, int(time.time()), _load_settings())
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())

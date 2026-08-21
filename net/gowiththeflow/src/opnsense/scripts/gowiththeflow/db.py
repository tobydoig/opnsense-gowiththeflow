"""SQLite connection/schema management and the write path from a
pf_state_poller.DiffResult into live_sessions/connections_raw.

Stage A2 wires this up to pf_state_poller's output only -- no hostname
resolution yet, so remote_hostname/hostname_source are left NULL here and
filled in later by correlator.py.
"""

from __future__ import annotations

import sqlite3
import time

from pf_state_poller import DiffResult

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS live_sessions (
  id INTEGER PRIMARY KEY,
  proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, local_port INTEGER NOT NULL,
  remote_ip TEXT NOT NULL, remote_port INTEGER NOT NULL,
  remote_hostname TEXT, hostname_source TEXT,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
  bytes_in INTEGER NOT NULL DEFAULT 0, bytes_out INTEGER NOT NULL DEFAULT 0,
  pkts_in INTEGER NOT NULL DEFAULT 0, pkts_out INTEGER NOT NULL DEFAULT 0,
  -- pf's counters are cumulative-since-creation and never reset, so an
  -- hourly checkpoint (rollup.py) can't zero bytes_in/out -- it records how
  -- much of the cumulative total is already reflected in connections_raw.
  last_checkpoint_at INTEGER NOT NULL DEFAULT 0,
  baseline_bytes_in INTEGER NOT NULL DEFAULT 0, baseline_bytes_out INTEGER NOT NULL DEFAULT 0,
  baseline_pkts_in INTEGER NOT NULL DEFAULT 0, baseline_pkts_out INTEGER NOT NULL DEFAULT 0,
  UNIQUE(proto, local_ip, local_port, remote_ip, remote_port)
);
CREATE INDEX IF NOT EXISTS idx_live_local ON live_sessions(local_ip);

CREATE TABLE IF NOT EXISTS connections_raw (
  id INTEGER PRIMARY KEY,
  proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, local_mac TEXT,
  remote_ip TEXT NOT NULL, remote_port INTEGER NOT NULL,
  remote_hostname TEXT, hostname_source TEXT,
  started_at INTEGER NOT NULL, ended_at INTEGER NOT NULL, duration_s INTEGER NOT NULL,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  pkts_in INTEGER NOT NULL, pkts_out INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_local_end ON connections_raw(local_ip, ended_at);
CREATE INDEX IF NOT EXISTS idx_raw_remote_end ON connections_raw(remote_ip, ended_at);
CREATE INDEX IF NOT EXISTS idx_raw_end ON connections_raw(ended_at);

CREATE TABLE IF NOT EXISTS rollup_hourly (
  bucket_start INTEGER NOT NULL, proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, remote_ip TEXT NOT NULL,
  remote_hostname TEXT, hostname_source TEXT,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  pkts_in INTEGER NOT NULL, pkts_out INTEGER NOT NULL, conn_count INTEGER NOT NULL,
  PRIMARY KEY (bucket_start, proto, local_ip, remote_ip)
);
CREATE INDEX IF NOT EXISTS idx_ru_h_local ON rollup_hourly(bucket_start, local_ip);
CREATE INDEX IF NOT EXISTS idx_ru_h_remote ON rollup_hourly(bucket_start, remote_ip);

CREATE TABLE IF NOT EXISTS rollup_daily (
  bucket_start INTEGER NOT NULL, proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, remote_ip TEXT NOT NULL,
  remote_hostname TEXT, hostname_source TEXT,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  pkts_in INTEGER NOT NULL, pkts_out INTEGER NOT NULL, conn_count INTEGER NOT NULL,
  PRIMARY KEY (bucket_start, proto, local_ip, remote_ip)
);
CREATE INDEX IF NOT EXISTS idx_ru_d_local ON rollup_daily(bucket_start, local_ip);
CREATE INDEX IF NOT EXISTS idx_ru_d_remote ON rollup_daily(bucket_start, remote_ip);

CREATE TABLE IF NOT EXISTS ip_hostname_cache (
  ip TEXT PRIMARY KEY, hostname TEXT NOT NULL, source TEXT NOT NULL,
  ambiguous INTEGER NOT NULL DEFAULT 0, ttl_expires_at INTEGER NOT NULL,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, hit_count INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS local_host_identity (
  mac TEXT PRIMARY KEY, ip TEXT, hostname TEXT, source TEXT,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lhi_ip ON local_host_identity(ip);

CREATE TABLE IF NOT EXISTS rollup_state (
  bucket_kind TEXT PRIMARY KEY, last_bucket_start INTEGER NOT NULL
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def record_diff(
    conn: sqlite3.Connection,
    diff: DiffResult,
    now: float | None = None,
    resolve_hostname=None,
) -> None:
    """Writes one poll's open/update/close events into live_sessions and
    connections_raw. Byte/packet counters are pf's own cumulative values
    (see pf_state_poller), so this never sums deltas itself.

    `resolve_hostname`, if given, is called as resolve_hostname(snap) for
    every opened/updated snapshot and should return (hostname, source) or
    (None, None) -- see correlator.py. A None result never blanks out a
    hostname a session already had (e.g. a live SNI hint expiring between
    polls shouldn't make a session's display flicker back to a bare IP),
    via COALESCE against the existing column value. Closed sessions simply
    carry forward whatever hostname/source their live_sessions row already
    had -- the "hostname snapshotted at write time" behavior from the
    project plan."""
    now_i = int(now if now is not None else time.time())

    def _resolve(snap):
        if resolve_hostname is None:
            return None, None
        return resolve_hostname(snap)

    for snap in diff.opened:
        first_seen = now_i - snap.age_s
        hostname, source = _resolve(snap)
        conn.execute(
            """
            INSERT INTO live_sessions
                (proto, local_ip, local_port, remote_ip, remote_port,
                 remote_hostname, hostname_source,
                 first_seen, last_seen, bytes_in, bytes_out, pkts_in, pkts_out,
                 last_checkpoint_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(proto, local_ip, local_port, remote_ip, remote_port)
            DO UPDATE SET
                last_seen=excluded.last_seen,
                bytes_in=excluded.bytes_in,
                bytes_out=excluded.bytes_out,
                pkts_in=excluded.pkts_in,
                pkts_out=excluded.pkts_out,
                remote_hostname=COALESCE(excluded.remote_hostname, remote_hostname),
                hostname_source=COALESCE(excluded.hostname_source, hostname_source)
            """,
            (
                snap.key.proto, snap.key.local_ip, snap.key.local_port,
                snap.key.remote_ip, snap.key.remote_port,
                hostname, source,
                first_seen, now_i, snap.bytes_in, snap.bytes_out,
                snap.pkts_in, snap.pkts_out, first_seen,
            ),
        )

    for snap in diff.updated:
        hostname, source = _resolve(snap)
        conn.execute(
            """
            UPDATE live_sessions
            SET last_seen=?, bytes_in=?, bytes_out=?, pkts_in=?, pkts_out=?,
                remote_hostname=COALESCE(?, remote_hostname),
                hostname_source=COALESCE(?, hostname_source)
            WHERE proto=? AND local_ip=? AND local_port=? AND remote_ip=? AND remote_port=?
            """,
            (
                now_i, snap.bytes_in, snap.bytes_out, snap.pkts_in, snap.pkts_out,
                hostname, source,
                snap.key.proto, snap.key.local_ip, snap.key.local_port,
                snap.key.remote_ip, snap.key.remote_port,
            ),
        )

    for snap in diff.closed:
        row = conn.execute(
            """
            SELECT first_seen, last_seen, remote_hostname, hostname_source FROM live_sessions
            WHERE proto=? AND local_ip=? AND local_port=? AND remote_ip=? AND remote_port=?
            """,
            (
                snap.key.proto, snap.key.local_ip, snap.key.local_port,
                snap.key.remote_ip, snap.key.remote_port,
            ),
        ).fetchone()
        if row is None:
            # Never actually recorded as opened (e.g. daemon restarted
            # mid-session) -- fall back to the closed snapshot's own age;
            # no prior hostname to carry forward either.
            first_seen = now_i - snap.age_s
            ended_at = now_i
            hostname, source = None, None
        else:
            first_seen, ended_at = row["first_seen"], row["last_seen"]
            hostname, source = row["remote_hostname"], row["hostname_source"]

        conn.execute(
            """
            INSERT INTO connections_raw
                (proto, local_ip, remote_ip, remote_port, remote_hostname, hostname_source,
                 started_at, ended_at, duration_s, bytes_in, bytes_out, pkts_in, pkts_out)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap.key.proto, snap.key.local_ip, snap.key.remote_ip, snap.key.remote_port,
                hostname, source,
                first_seen, ended_at, max(ended_at - first_seen, 0),
                snap.bytes_in, snap.bytes_out, snap.pkts_in, snap.pkts_out,
            ),
        )
        conn.execute(
            """
            DELETE FROM live_sessions
            WHERE proto=? AND local_ip=? AND local_port=? AND remote_ip=? AND remote_port=?
            """,
            (
                snap.key.proto, snap.key.local_ip, snap.key.local_port,
                snap.key.remote_ip, snap.key.remote_port,
            ),
        )

    conn.commit()

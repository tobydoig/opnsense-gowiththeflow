"""SQLite connection/schema management and the write path from a
pf_state_poller.DiffResult into live_sessions/connections_raw.

Stage A2 wires this up to pf_state_poller's output only -- no hostname
resolution yet, so remote_hostname/hostname_source are left NULL here and
filled in later by correlator.py.
"""

from __future__ import annotations

import os
import sqlite3
import time

from pf_state_poller import (
    DiffResult,
    InternalPairDiffResult,
    InternalPairKey,
    InternalPairSnapshot,
    StateKey,
    StateSnapshot,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS live_sessions (
  id INTEGER PRIMARY KEY,
  proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, local_port INTEGER NOT NULL,
  remote_ip TEXT NOT NULL, remote_port INTEGER NOT NULL,
  remote_hostname TEXT, hostname_source TEXT, category TEXT,
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
  remote_hostname TEXT, hostname_source TEXT, category TEXT,
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
  remote_hostname TEXT, hostname_source TEXT, category TEXT,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  pkts_in INTEGER NOT NULL, pkts_out INTEGER NOT NULL, conn_count INTEGER NOT NULL,
  PRIMARY KEY (bucket_start, proto, local_ip, remote_ip)
);
CREATE INDEX IF NOT EXISTS idx_ru_h_local ON rollup_hourly(bucket_start, local_ip);
CREATE INDEX IF NOT EXISTS idx_ru_h_remote ON rollup_hourly(bucket_start, remote_ip);

CREATE TABLE IF NOT EXISTS rollup_daily (
  bucket_start INTEGER NOT NULL, proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, remote_ip TEXT NOT NULL,
  remote_hostname TEXT, hostname_source TEXT, category TEXT,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  pkts_in INTEGER NOT NULL, pkts_out INTEGER NOT NULL, conn_count INTEGER NOT NULL,
  PRIMARY KEY (bucket_start, proto, local_ip, remote_ip)
);
CREATE INDEX IF NOT EXISTS idx_ru_d_local ON rollup_daily(bucket_start, local_ip);
CREATE INDEX IF NOT EXISTS idx_ru_d_remote ON rollup_daily(bucket_start, remote_ip);

-- "Internal" traffic: both endpoints local (different VLANs/subnets that
-- still route through the firewall -- same-L2-subnet traffic never
-- reaches pf at all, a documented, unfixable limitation). Neither side is
-- "more local" than the other, so there's no local_ip/remote_ip split
-- here -- ip_a/ip_b, uncanonicalized (whichever side pf called src/dst),
-- matching pf_state_poller.InternalPairKey. No hostname/category columns:
-- both endpoints are named via the existing local_host_identity IP join
-- at query time (no new resolver needed), and "category" (what internet
-- service is this?) doesn't apply to a device-to-device flow.
CREATE TABLE IF NOT EXISTS internal_live_sessions (
  id INTEGER PRIMARY KEY,
  proto TEXT NOT NULL,
  ip_a TEXT NOT NULL, port_a INTEGER NOT NULL,
  ip_b TEXT NOT NULL, port_b INTEGER NOT NULL,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
  bytes_a_to_b INTEGER NOT NULL DEFAULT 0, bytes_b_to_a INTEGER NOT NULL DEFAULT 0,
  pkts_a_to_b INTEGER NOT NULL DEFAULT 0, pkts_b_to_a INTEGER NOT NULL DEFAULT 0,
  last_checkpoint_at INTEGER NOT NULL DEFAULT 0,
  baseline_bytes_a_to_b INTEGER NOT NULL DEFAULT 0, baseline_bytes_b_to_a INTEGER NOT NULL DEFAULT 0,
  baseline_pkts_a_to_b INTEGER NOT NULL DEFAULT 0, baseline_pkts_b_to_a INTEGER NOT NULL DEFAULT 0,
  UNIQUE(proto, ip_a, port_a, ip_b, port_b)
);
CREATE INDEX IF NOT EXISTS idx_internal_live_ip_a ON internal_live_sessions(ip_a);
CREATE INDEX IF NOT EXISTS idx_internal_live_ip_b ON internal_live_sessions(ip_b);

-- port_a (the ephemeral/initiating side's port) is dropped here, mirroring
-- connections_raw's own precedent of keeping only the "service side" port
-- -- this only holds because ip_a/port_a stay src-anchored/uncanonicalized
-- at this layer (see pf_state_poller.InternalPairKey and rollup.py's
-- rollup_internal_hourly). Don't "fix" pair ordering here -- that's done
-- deliberately one layer up, in the hourly rollup, not this raw table.
CREATE TABLE IF NOT EXISTS internal_connections_raw (
  id INTEGER PRIMARY KEY,
  proto TEXT NOT NULL,
  ip_a TEXT NOT NULL, ip_b TEXT NOT NULL, port_b INTEGER NOT NULL,
  started_at INTEGER NOT NULL, ended_at INTEGER NOT NULL, duration_s INTEGER NOT NULL,
  bytes_a_to_b INTEGER NOT NULL, bytes_b_to_a INTEGER NOT NULL,
  pkts_a_to_b INTEGER NOT NULL, pkts_b_to_a INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_internal_raw_ip_a_end ON internal_connections_raw(ip_a, ended_at);
CREATE INDEX IF NOT EXISTS idx_internal_raw_ip_b_end ON internal_connections_raw(ip_b, ended_at);
CREATE INDEX IF NOT EXISTS idx_internal_raw_end ON internal_connections_raw(ended_at);

-- Unlike internal_connections_raw, rows here ARE canonicalized (ip_a <
-- ip_b, numerically) by rollup.rollup_internal_hourly() -- otherwise the
-- same device pair fragments into two rollup rows whenever traffic is
-- initiated from both directions across different flows.
CREATE TABLE IF NOT EXISTS internal_rollup_hourly (
  bucket_start INTEGER NOT NULL, proto TEXT NOT NULL,
  ip_a TEXT NOT NULL, ip_b TEXT NOT NULL,
  bytes_a_to_b INTEGER NOT NULL, bytes_b_to_a INTEGER NOT NULL,
  pkts_a_to_b INTEGER NOT NULL, pkts_b_to_a INTEGER NOT NULL, conn_count INTEGER NOT NULL,
  PRIMARY KEY (bucket_start, proto, ip_a, ip_b)
);
CREATE INDEX IF NOT EXISTS idx_iru_h_ip_a ON internal_rollup_hourly(bucket_start, ip_a);
CREATE INDEX IF NOT EXISTS idx_iru_h_ip_b ON internal_rollup_hourly(bucket_start, ip_b);

CREATE TABLE IF NOT EXISTS internal_rollup_daily (
  bucket_start INTEGER NOT NULL, proto TEXT NOT NULL,
  ip_a TEXT NOT NULL, ip_b TEXT NOT NULL,
  bytes_a_to_b INTEGER NOT NULL, bytes_b_to_a INTEGER NOT NULL,
  pkts_a_to_b INTEGER NOT NULL, pkts_b_to_a INTEGER NOT NULL, conn_count INTEGER NOT NULL,
  PRIMARY KEY (bucket_start, proto, ip_a, ip_b)
);
CREATE INDEX IF NOT EXISTS idx_iru_d_ip_a ON internal_rollup_daily(bucket_start, ip_a);
CREATE INDEX IF NOT EXISTS idx_iru_d_ip_b ON internal_rollup_daily(bucket_start, ip_b);

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
    # sqlite3.connect() doesn't create missing parent directories, and
    # nothing else in the install path does either -- a genuinely fresh
    # install (unlike the dev VM's /var/db/gowiththeflow, which has existed
    # since early manual testing, long before packaging did) has no reason
    # to have this directory, and connect() failing here means the daemon
    # dies before Daemonize even logs anything, silently.
    parent_dir = os.path.dirname(db_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    conn.row_factory = sqlite3.Row
    return conn


_CATEGORY_TABLES = ("live_sessions", "connections_raw", "rollup_hourly", "rollup_daily")


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    # SCHEMA_SQL's CREATE TABLE IF NOT EXISTS is a no-op against a
    # database that already has these tables from before "category" was
    # added -- ALTER TABLE is the only way to add it to an existing
    # install rather than just new ones. SQLite has no "ADD COLUMN IF NOT
    # EXISTS", so this catches the one specific error a repeat run raises
    # instead (a no-op on a fresh install, where the column already
    # exists via the CREATE TABLE above).
    for table in _CATEGORY_TABLES:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN category TEXT")
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def load_live_sessions_as_snapshots(conn: sqlite3.Connection) -> list[StateSnapshot]:
    """For seeding PfStatePoller.seed() at daemon startup -- see its
    docstring for why a restart needs this to correctly close out
    sessions that stopped existing while the daemon was down."""
    rows = conn.execute(
        """
        SELECT proto, local_ip, local_port, remote_ip, remote_port,
               bytes_in, bytes_out, pkts_in, pkts_out
        FROM live_sessions
        """
    ).fetchall()
    return [
        StateSnapshot(
            key=StateKey(
                r["proto"], r["local_ip"], r["local_port"], r["remote_ip"], r["remote_port"]
            ),
            bytes_out=r["bytes_out"],
            bytes_in=r["bytes_in"],
            pkts_out=r["pkts_out"],
            pkts_in=r["pkts_in"],
            age_s=0,
        )
        for r in rows
    ]


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
    every opened/updated snapshot and should return (hostname, source,
    category) or (None, None, None) -- see correlator.py. A None result
    never blanks out a hostname/category a session already had (e.g. a
    live SNI hint expiring between polls shouldn't make a session's
    display flicker back to a bare IP), via COALESCE against the
    existing column value. Closed sessions simply carry forward whatever
    hostname/source/category their live_sessions row already had -- the
    "hostname snapshotted at write time" behavior from the project
    plan."""
    now_i = int(now if now is not None else time.time())

    def _resolve(snap):
        if resolve_hostname is None:
            return None, None, None
        return resolve_hostname(snap)

    for snap in diff.opened:
        first_seen = now_i - snap.age_s
        hostname, source, category = _resolve(snap)
        conn.execute(
            """
            INSERT INTO live_sessions
                (proto, local_ip, local_port, remote_ip, remote_port,
                 remote_hostname, hostname_source, category,
                 first_seen, last_seen, bytes_in, bytes_out, pkts_in, pkts_out,
                 last_checkpoint_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(proto, local_ip, local_port, remote_ip, remote_port)
            DO UPDATE SET
                last_seen=excluded.last_seen,
                bytes_in=excluded.bytes_in,
                bytes_out=excluded.bytes_out,
                pkts_in=excluded.pkts_in,
                pkts_out=excluded.pkts_out,
                remote_hostname=COALESCE(excluded.remote_hostname, remote_hostname),
                hostname_source=COALESCE(excluded.hostname_source, hostname_source),
                category=COALESCE(excluded.category, category)
            """,
            (
                snap.key.proto, snap.key.local_ip, snap.key.local_port,
                snap.key.remote_ip, snap.key.remote_port,
                hostname, source, category,
                first_seen, now_i, snap.bytes_in, snap.bytes_out,
                snap.pkts_in, snap.pkts_out, first_seen,
            ),
        )

    for snap in diff.updated:
        hostname, source, category = _resolve(snap)
        conn.execute(
            """
            UPDATE live_sessions
            SET last_seen=?, bytes_in=?, bytes_out=?, pkts_in=?, pkts_out=?,
                remote_hostname=COALESCE(?, remote_hostname),
                hostname_source=COALESCE(?, hostname_source),
                category=COALESCE(?, category)
            WHERE proto=? AND local_ip=? AND local_port=? AND remote_ip=? AND remote_port=?
            """,
            (
                now_i, snap.bytes_in, snap.bytes_out, snap.pkts_in, snap.pkts_out,
                hostname, source, category,
                snap.key.proto, snap.key.local_ip, snap.key.local_port,
                snap.key.remote_ip, snap.key.remote_port,
            ),
        )

    for snap in diff.closed:
        row = conn.execute(
            """
            SELECT first_seen, last_seen, remote_hostname, hostname_source, category
            FROM live_sessions
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
            # no prior hostname/category to carry forward either.
            first_seen = now_i - snap.age_s
            ended_at = now_i
            hostname, source, category = None, None, None
        else:
            first_seen, ended_at = row["first_seen"], row["last_seen"]
            hostname, source = row["remote_hostname"], row["hostname_source"]
            category = row["category"]

        conn.execute(
            """
            INSERT INTO connections_raw
                (proto, local_ip, remote_ip, remote_port, remote_hostname, hostname_source, category,
                 started_at, ended_at, duration_s, bytes_in, bytes_out, pkts_in, pkts_out)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap.key.proto, snap.key.local_ip, snap.key.remote_ip, snap.key.remote_port,
                hostname, source, category,
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


def load_internal_live_sessions_as_snapshots(conn: sqlite3.Connection) -> list[InternalPairSnapshot]:
    """For seeding PfStatePoller.seed_internal_pairs() at daemon startup --
    same restart-safety reasoning as load_live_sessions_as_snapshots()."""
    rows = conn.execute(
        """
        SELECT proto, ip_a, port_a, ip_b, port_b,
               bytes_a_to_b, bytes_b_to_a, pkts_a_to_b, pkts_b_to_a
        FROM internal_live_sessions
        """
    ).fetchall()
    return [
        InternalPairSnapshot(
            key=InternalPairKey(
                r["proto"], r["ip_a"], r["port_a"], r["ip_b"], r["port_b"]
            ),
            bytes_a_to_b=r["bytes_a_to_b"],
            bytes_b_to_a=r["bytes_b_to_a"],
            pkts_a_to_b=r["pkts_a_to_b"],
            pkts_b_to_a=r["pkts_b_to_a"],
            age_s=0,
        )
        for r in rows
    ]


def record_internal_diff(
    conn: sqlite3.Connection,
    diff: InternalPairDiffResult,
    now: float | None = None,
) -> None:
    """Mirror of record_diff() for the internal (local<->local) pipeline --
    simpler, since there's no hostname to resolve (both endpoints are named
    via local_host_identity at query time in PHP) and so no COALESCE
    dance needed."""
    now_i = int(now if now is not None else time.time())

    for snap in diff.opened:
        first_seen = now_i - snap.age_s
        conn.execute(
            """
            INSERT INTO internal_live_sessions
                (proto, ip_a, port_a, ip_b, port_b,
                 first_seen, last_seen, bytes_a_to_b, bytes_b_to_a, pkts_a_to_b, pkts_b_to_a,
                 last_checkpoint_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(proto, ip_a, port_a, ip_b, port_b)
            DO UPDATE SET
                last_seen=excluded.last_seen,
                bytes_a_to_b=excluded.bytes_a_to_b,
                bytes_b_to_a=excluded.bytes_b_to_a,
                pkts_a_to_b=excluded.pkts_a_to_b,
                pkts_b_to_a=excluded.pkts_b_to_a
            """,
            (
                snap.key.proto, snap.key.ip_a, snap.key.port_a,
                snap.key.ip_b, snap.key.port_b,
                first_seen, now_i, snap.bytes_a_to_b, snap.bytes_b_to_a,
                snap.pkts_a_to_b, snap.pkts_b_to_a, first_seen,
            ),
        )

    for snap in diff.updated:
        conn.execute(
            """
            UPDATE internal_live_sessions
            SET last_seen=?, bytes_a_to_b=?, bytes_b_to_a=?, pkts_a_to_b=?, pkts_b_to_a=?
            WHERE proto=? AND ip_a=? AND port_a=? AND ip_b=? AND port_b=?
            """,
            (
                now_i, snap.bytes_a_to_b, snap.bytes_b_to_a, snap.pkts_a_to_b, snap.pkts_b_to_a,
                snap.key.proto, snap.key.ip_a, snap.key.port_a,
                snap.key.ip_b, snap.key.port_b,
            ),
        )

    for snap in diff.closed:
        row = conn.execute(
            """
            SELECT first_seen, last_seen
            FROM internal_live_sessions
            WHERE proto=? AND ip_a=? AND port_a=? AND ip_b=? AND port_b=?
            """,
            (
                snap.key.proto, snap.key.ip_a, snap.key.port_a,
                snap.key.ip_b, snap.key.port_b,
            ),
        ).fetchone()
        if row is None:
            # Never actually recorded as opened (e.g. daemon restarted
            # mid-session) -- fall back to the closed snapshot's own age.
            first_seen = now_i - snap.age_s
            ended_at = now_i
        else:
            first_seen, ended_at = row["first_seen"], row["last_seen"]

        conn.execute(
            """
            INSERT INTO internal_connections_raw
                (proto, ip_a, ip_b, port_b,
                 started_at, ended_at, duration_s, bytes_a_to_b, bytes_b_to_a, pkts_a_to_b, pkts_b_to_a)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap.key.proto, snap.key.ip_a, snap.key.ip_b, snap.key.port_b,
                first_seen, ended_at, max(ended_at - first_seen, 0),
                snap.bytes_a_to_b, snap.bytes_b_to_a, snap.pkts_a_to_b, snap.pkts_b_to_a,
            ),
        )
        conn.execute(
            """
            DELETE FROM internal_live_sessions
            WHERE proto=? AND ip_a=? AND port_a=? AND ip_b=? AND port_b=?
            """,
            (
                snap.key.proto, snap.key.ip_a, snap.key.port_a,
                snap.key.ip_b, snap.key.port_b,
            ),
        )

    conn.commit()

"""SQLite connection/schema management and the write path from a
pf_state_poller.DiffResult into live_sessions/connections_raw.

Every surviving session (pf_state_poller.classify_sessions() already
discards the only case that's genuinely out of scope -- neither endpoint
local, e.g. the firewall's own outbound traffic) has exactly one
genuinely local endpoint: local_ip/local_port. The other side, peer_ip/
peer_port, is either a real internet host or another local host --
`peer_is_local` tells you which. A hostname is only ever resolved (via
correlator.py) for a genuinely remote peer; a local peer is named via a
local_host_identity lookup at PHP query time instead, same as local_ip
always has been.
"""

from __future__ import annotations

import os
import sqlite3
import time

from pf_state_poller import DiffResult, StateKey, StateSnapshot
from rollup import floor_to

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS live_sessions (
  id INTEGER PRIMARY KEY,
  proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, local_port INTEGER NOT NULL,
  peer_ip TEXT NOT NULL, peer_port INTEGER NOT NULL,
  peer_is_local INTEGER NOT NULL DEFAULT 0,
  peer_hostname TEXT, hostname_source TEXT, category TEXT,
  -- Set by dpi_classifier.py's periodic batch nDPI classification, not
  -- the live per-poll path everything else here uses -- see its module
  -- docstring for why (ndpiReader's JSON output is batch-only, not a
  -- live stream). NULL until a capture burst happens to classify this
  -- session; may never populate for a short-lived one.
  dpi_protocol TEXT,
  state TEXT,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
  -- last_seen bumps on every poll a session is still present in pf's own
  -- state table, regardless of whether any real traffic happened --
  -- last_activity only bumps when bytes_in/bytes_out/state actually
  -- changed since the previous poll, so the gap between the two is
  -- itself the "how long has this been sitting idle" signal.
  last_activity INTEGER NOT NULL,
  bytes_in INTEGER NOT NULL DEFAULT 0, bytes_out INTEGER NOT NULL DEFAULT 0,
  pkts_in INTEGER NOT NULL DEFAULT 0, pkts_out INTEGER NOT NULL DEFAULT 0,
  -- pf's counters are cumulative-since-creation and never reset, so an
  -- hourly checkpoint (rollup.py) can't zero bytes_in/out -- it records how
  -- much of the cumulative total is already reflected in connections_raw.
  last_checkpoint_at INTEGER NOT NULL DEFAULT 0,
  baseline_bytes_in INTEGER NOT NULL DEFAULT 0, baseline_bytes_out INTEGER NOT NULL DEFAULT 0,
  baseline_pkts_in INTEGER NOT NULL DEFAULT 0, baseline_pkts_out INTEGER NOT NULL DEFAULT 0,
  UNIQUE(proto, local_ip, local_port, peer_ip, peer_port)
);
CREATE INDEX IF NOT EXISTS idx_live_local ON live_sessions(local_ip);

CREATE TABLE IF NOT EXISTS connections_raw (
  id INTEGER PRIMARY KEY,
  proto TEXT NOT NULL,
  local_ip TEXT NOT NULL,
  peer_ip TEXT NOT NULL, peer_port INTEGER NOT NULL,
  peer_is_local INTEGER NOT NULL DEFAULT 0,
  peer_hostname TEXT, hostname_source TEXT, category TEXT, dpi_protocol TEXT,
  state TEXT,
  started_at INTEGER NOT NULL, ended_at INTEGER NOT NULL, duration_s INTEGER NOT NULL,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  pkts_in INTEGER NOT NULL, pkts_out INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_local_end ON connections_raw(local_ip, ended_at);
CREATE INDEX IF NOT EXISTS idx_raw_peer_end ON connections_raw(peer_ip, ended_at);
CREATE INDEX IF NOT EXISTS idx_raw_end ON connections_raw(ended_at);

-- peer_is_local=1 rows are canonicalized here (local_ip < peer_ip
-- numerically) by rollup.rollup_hourly() -- otherwise the same device
-- pair fragments into two rollup rows depending on which side initiated
-- a given flow. peer_is_local=0 rows are never swapped -- they're already
-- canonical by role (local_ip is always the genuinely local side,
-- decided once in pf_state_poller.classify_sessions()), not by IP value.
-- Two different index shapes are both needed here: the bucket_start-
-- leading ones for "all hosts, chronological" queries (the History/Live
-- Overview timeseries charts), and the local_ip/peer_ip-leading ones for
-- "this specific pair, most recent" correlated-subquery lookups (History/
-- Top Talkers' hostname/category resolution) -- confirmed via a real
-- reproduction that the latter access pattern is unusable without them
-- (7.5s -> 0.089s on a realistic 45-day dataset).
CREATE TABLE IF NOT EXISTS rollup_hourly (
  bucket_start INTEGER NOT NULL, proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, peer_ip TEXT NOT NULL,
  peer_is_local INTEGER NOT NULL DEFAULT 0,
  peer_hostname TEXT, hostname_source TEXT, category TEXT, dpi_protocol TEXT,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  pkts_in INTEGER NOT NULL, pkts_out INTEGER NOT NULL, conn_count INTEGER NOT NULL,
  PRIMARY KEY (bucket_start, proto, local_ip, peer_ip)
);
CREATE INDEX IF NOT EXISTS idx_ru_h_local ON rollup_hourly(bucket_start, local_ip);
CREATE INDEX IF NOT EXISTS idx_ru_h_peer ON rollup_hourly(bucket_start, peer_ip);
CREATE INDEX IF NOT EXISTS idx_ru_h_local_peer_recency ON rollup_hourly(local_ip, peer_ip, bucket_start);
CREATE INDEX IF NOT EXISTS idx_ru_h_peer_recency ON rollup_hourly(peer_ip, bucket_start);
-- Same reasoning/shape as idx_ru_*_peer_recency -- ToptalkersController's
-- uncategorizedAction() filters/sorts a correlated subquery by
-- (peer_hostname, bucket_start) exactly, and had no index at all to
-- support it (confirmed live on nostromo: ~2 minutes per page over
-- ~2000 uncategorized hosts, low CPU load throughout -- an I/O-bound
-- full-table-scan-per-group signature, not a CPU one).
CREATE INDEX IF NOT EXISTS idx_ru_h_hostname_recency ON rollup_hourly(peer_hostname, bucket_start);

CREATE TABLE IF NOT EXISTS rollup_daily (
  bucket_start INTEGER NOT NULL, proto TEXT NOT NULL,
  local_ip TEXT NOT NULL, peer_ip TEXT NOT NULL,
  peer_is_local INTEGER NOT NULL DEFAULT 0,
  peer_hostname TEXT, hostname_source TEXT, category TEXT, dpi_protocol TEXT,
  bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL,
  pkts_in INTEGER NOT NULL, pkts_out INTEGER NOT NULL, conn_count INTEGER NOT NULL,
  PRIMARY KEY (bucket_start, proto, local_ip, peer_ip)
);
CREATE INDEX IF NOT EXISTS idx_ru_d_local ON rollup_daily(bucket_start, local_ip);
CREATE INDEX IF NOT EXISTS idx_ru_d_peer ON rollup_daily(bucket_start, peer_ip);
CREATE INDEX IF NOT EXISTS idx_ru_d_local_peer_recency ON rollup_daily(local_ip, peer_ip, bucket_start);
CREATE INDEX IF NOT EXISTS idx_ru_d_peer_recency ON rollup_daily(peer_ip, bucket_start);
CREATE INDEX IF NOT EXISTS idx_ru_d_hostname_recency ON rollup_daily(peer_hostname, bucket_start);

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

-- Feeds the Live Overview chart directly, server-side -- computed once
-- per poll (live_ticks.compute_tick_deltas(), called from
-- gowiththeflowd.py) and pruned continuously to a short rolling window
-- (see rollup.prune_live_ticks()), so every viewer reads the same
-- recorded history instead of each browser tab independently diffing
-- its own poll of live_sessions. Grouped by (local_ip, peer_port) --
-- the two dimensions the chart's own grouping toggle needs -- not full
-- session granularity; the Graph view still reads live_sessions/
-- overview directly for per-edge detail, this table has no hostnames.
CREATE TABLE IF NOT EXISTS live_ticks (
  tick_time INTEGER NOT NULL,
  local_ip TEXT NOT NULL, peer_port INTEGER NOT NULL,
  delta_bytes_in INTEGER NOT NULL, delta_bytes_out INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_live_ticks_time ON live_ticks(tick_time);

-- Fed by dns_sniffer.py's extract_query_events() (a query/response
-- transaction per row), via an hourly-bucketed upsert
-- (record_dns_query_event()) rather than one row per query seen --
-- DNS lookups happen far more often than actual connections (repeat/
-- cached lookups, background app chatter), so row growth here is
-- bounded by distinct (host, query, type) combinations per hour, not
-- raw query frequency -- a device polling the same hostname every 30s
-- all day produces one row with a growing `count`, not thousands of
-- rows. Real write cost of this (an INSERT...ON CONFLICT per distinct
-- combo per drain cycle, not a batched executemany) hasn't been
-- measured on a real busy network yet -- same "real cost, unmeasured"
-- caveat DPI shipped with.
CREATE TABLE IF NOT EXISTS dns_query_log (
  bucket_start INTEGER NOT NULL,
  local_ip TEXT NOT NULL,
  query_name TEXT NOT NULL,
  query_type TEXT NOT NULL,
  rcode TEXT NOT NULL,
  answers TEXT,
  count INTEGER NOT NULL DEFAULT 1,
  first_seen INTEGER NOT NULL,
  last_seen INTEGER NOT NULL,
  PRIMARY KEY (bucket_start, local_ip, query_name, query_type)
);
CREATE INDEX IF NOT EXISTS idx_dns_query_log_time ON dns_query_log(bucket_start);
CREATE INDEX IF NOT EXISTS idx_dns_query_log_local ON dns_query_log(local_ip, bucket_start);

-- Source of truth for what's blocked; the pf table (gowiththeflow_blocked)
-- and its on-disk backing file are always derived FROM this
-- (blocklist.sync_pf() rewrites both from a full read of this table),
-- never the other way round, so any drift self-heals instead of
-- accumulating. Keyed by IP, not MAC like local_host_identity, because
-- an IP is what pf actually filters on and what every other table/UI
-- here is keyed by. The first table in this schema written by a user
-- action rather than the daemon's own poll loop -- writes come from
-- block_host.py under configd (see blocklist.py), not from PHP, the
-- same "PHP reads, Python writes" split this project already follows
-- everywhere else.
CREATE TABLE IF NOT EXISTS blocked_hosts (
  local_ip TEXT PRIMARY KEY,
  -- hostname/mac are snapshotted at block time rather than joined live
  -- from local_host_identity, for the same reason connections_raw
  -- snapshots peer_hostname: local_host_identity.ip is a *current*
  -- DHCP-derived mapping that can be reassigned, so a live join would
  -- relabel an old block with whichever device now holds that IP.
  hostname TEXT,
  mac TEXT,
  blocked_at INTEGER NOT NULL,
  blocked_by TEXT,
  reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_blocked_hosts_at ON blocked_hosts(blocked_at);
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
    # exists via the CREATE TABLE above). Kept even though the local/peer
    # unification's own schema change has no migration path (existing
    # tracking data is dropped, not migrated, per project decision) --
    # this loop predates that and remains a harmless no-op going forward.
    for table in _CATEGORY_TABLES:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN category TEXT")
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

    # Same pattern again for dpi_protocol -- added later still, same
    # nullable/unconstrained shape as category, same four tables.
    for table in _CATEGORY_TABLES:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN dpi_protocol TEXT")
            conn.commit()
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise

    # Same pattern for last_activity, added later still -- but this one is
    # NOT NULL, so SQLite requires a DEFAULT to add it to a non-empty
    # table; backfill real rows from last_seen (the best available guess:
    # "as far as we knew, it was active as of the last time we saw it")
    # rather than leaving them at the placeholder 0 (1970) forever. Only
    # ever runs once -- every later call hits "duplicate column" and is a
    # no-op, same as the category migration above.
    try:
        conn.execute("ALTER TABLE live_sessions ADD COLUMN last_activity INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        conn.execute("UPDATE live_sessions SET last_activity = last_seen WHERE last_activity = 0")
        conn.commit()
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise


def load_live_sessions_as_snapshots(conn: sqlite3.Connection) -> list[StateSnapshot]:
    """For seeding PfStatePoller.seed() at daemon startup -- see its
    docstring for why a restart needs this to correctly close out
    sessions that stopped existing while the daemon was down.

    Must round-trip `peer_is_local` -- otherwise every session seeded at
    restart would silently default to peer_is_local=False regardless of
    what it actually is, and record_diff()'s resolver short-circuit for
    local peers would misfire on the (rare) restart-edge-case path where a
    seeded session closes without ever appearing in a real poll again."""
    rows = conn.execute(
        """
        SELECT proto, local_ip, local_port, peer_ip, peer_port,
               peer_is_local, bytes_in, bytes_out, pkts_in, pkts_out
        FROM live_sessions
        """
    ).fetchall()
    return [
        StateSnapshot(
            key=StateKey(
                r["proto"], r["local_ip"], r["local_port"], r["peer_ip"], r["peer_port"]
            ),
            bytes_out=r["bytes_out"],
            bytes_in=r["bytes_in"],
            pkts_out=r["pkts_out"],
            pkts_in=r["pkts_in"],
            age_s=0,
            peer_is_local=bool(r["peer_is_local"]),
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
    every opened/updated snapshot whose peer is NOT local, and should
    return (hostname, source, category) or (None, None, None) -- see
    correlator.py. For a snap whose peer IS local, the resolver is never
    called at all -- a local peer's IP would never resolve to anything via
    DNS/SNI/hostcache anyway, and short-circuiting keeps that explicit
    rather than relying on the resolver happening to return nothing.
    category is set to the literal sentinel 'Internal' in that case. A
    None result from the resolver never blanks out a hostname/category a
    session already had (e.g. a live SNI hint expiring between polls
    shouldn't make a session's display flicker back to a bare IP), via
    COALESCE against the existing column value. Closed sessions simply
    carry forward whatever hostname/source/category/state their
    live_sessions row already had -- the "hostname snapshotted at write
    time" behavior from the project plan."""
    now_i = int(now if now is not None else time.time())

    def _resolve(snap):
        if snap.peer_is_local:
            return None, None, "Internal"
        if resolve_hostname is None:
            return None, None, None
        return resolve_hostname(snap)

    for snap in diff.opened:
        first_seen = now_i - snap.age_s
        hostname, source, category = _resolve(snap)
        conn.execute(
            """
            INSERT INTO live_sessions
                (proto, local_ip, local_port, peer_ip, peer_port, peer_is_local,
                 peer_hostname, hostname_source, category, state,
                 first_seen, last_seen, last_activity, bytes_in, bytes_out, pkts_in, pkts_out,
                 last_checkpoint_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(proto, local_ip, local_port, peer_ip, peer_port)
            DO UPDATE SET
                last_seen=excluded.last_seen,
                last_activity = CASE
                    WHEN bytes_in != excluded.bytes_in OR bytes_out != excluded.bytes_out
                         OR state IS NOT excluded.state
                    THEN excluded.last_seen ELSE last_activity END,
                bytes_in=excluded.bytes_in,
                bytes_out=excluded.bytes_out,
                pkts_in=excluded.pkts_in,
                pkts_out=excluded.pkts_out,
                state=excluded.state,
                peer_hostname=COALESCE(excluded.peer_hostname, peer_hostname),
                hostname_source=COALESCE(excluded.hostname_source, hostname_source),
                category=COALESCE(excluded.category, category)
            """,
            (
                snap.key.proto, snap.key.local_ip, snap.key.local_port,
                snap.key.peer_ip, snap.key.peer_port, int(snap.peer_is_local),
                hostname, source, category, snap.state,
                first_seen, now_i, now_i, snap.bytes_in, snap.bytes_out,
                snap.pkts_in, snap.pkts_out, first_seen,
            ),
        )

    for snap in diff.updated:
        hostname, source, category = _resolve(snap)
        conn.execute(
            """
            UPDATE live_sessions
            SET last_seen=?,
                last_activity = CASE
                    WHEN bytes_in != ? OR bytes_out != ? OR state IS NOT ?
                    THEN ? ELSE last_activity END,
                bytes_in=?, bytes_out=?, pkts_in=?, pkts_out=?, state=?,
                peer_hostname=COALESCE(?, peer_hostname),
                hostname_source=COALESCE(?, hostname_source),
                category=COALESCE(?, category)
            WHERE proto=? AND local_ip=? AND local_port=? AND peer_ip=? AND peer_port=?
            """,
            (
                now_i,
                snap.bytes_in, snap.bytes_out, snap.state, now_i,
                snap.bytes_in, snap.bytes_out, snap.pkts_in, snap.pkts_out, snap.state,
                hostname, source, category,
                snap.key.proto, snap.key.local_ip, snap.key.local_port,
                snap.key.peer_ip, snap.key.peer_port,
            ),
        )

    for snap in diff.closed:
        row = conn.execute(
            """
            SELECT first_seen, last_seen, peer_is_local, peer_hostname, hostname_source,
                   category, dpi_protocol
            FROM live_sessions
            WHERE proto=? AND local_ip=? AND local_port=? AND peer_ip=? AND peer_port=?
            """,
            (
                snap.key.proto, snap.key.local_ip, snap.key.local_port,
                snap.key.peer_ip, snap.key.peer_port,
            ),
        ).fetchone()
        if row is None:
            # Never actually recorded as opened (e.g. daemon restarted
            # mid-session) -- fall back to the closed snapshot's own age;
            # no prior hostname/category/dpi_protocol to carry forward either.
            first_seen = now_i - snap.age_s
            ended_at = now_i
            peer_is_local = snap.peer_is_local
            hostname, source = None, None
            category = "Internal" if snap.peer_is_local else None
            dpi_protocol = None
        else:
            first_seen, ended_at = row["first_seen"], row["last_seen"]
            peer_is_local = bool(row["peer_is_local"])
            hostname, source = row["peer_hostname"], row["hostname_source"]
            category = row["category"]
            dpi_protocol = row["dpi_protocol"]

        conn.execute(
            """
            INSERT INTO connections_raw
                (proto, local_ip, peer_ip, peer_port, peer_is_local,
                 peer_hostname, hostname_source, category, dpi_protocol, state,
                 started_at, ended_at, duration_s, bytes_in, bytes_out, pkts_in, pkts_out)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap.key.proto, snap.key.local_ip, snap.key.peer_ip, snap.key.peer_port,
                int(peer_is_local), hostname, source, category, dpi_protocol, snap.state,
                first_seen, ended_at, max(ended_at - first_seen, 0),
                snap.bytes_in, snap.bytes_out, snap.pkts_in, snap.pkts_out,
            ),
        )
        conn.execute(
            """
            DELETE FROM live_sessions
            WHERE proto=? AND local_ip=? AND local_port=? AND peer_ip=? AND peer_port=?
            """,
            (
                snap.key.proto, snap.key.local_ip, snap.key.local_port,
                snap.key.peer_ip, snap.key.peer_port,
            ),
        )

    conn.commit()


def record_live_ticks(conn: sqlite3.Connection, tick_time: int, rows: list["TickRow"]) -> None:
    """Writes one poll cycle's worth of live_ticks.compute_tick_deltas()
    output. A no-op on an empty list (nothing happened this tick worth
    charting) rather than an empty executemany, though executemany
    against an empty sequence is itself already a harmless no-op."""
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO live_ticks (tick_time, local_ip, peer_port, delta_bytes_in, delta_bytes_out)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (tick_time, row.local_ip, row.peer_port, row.delta_bytes_in, row.delta_bytes_out)
            for row in rows
        ],
    )
    conn.commit()


def update_dpi_protocol(
    conn: sqlite3.Connection,
    proto: str,
    local_ip: str,
    local_port: int,
    peer_ip: str,
    peer_port: int,
    dpi_protocol: str,
) -> None:
    """Best-effort: a batch nDPI classification can arrive up to a whole
    capture-burst duration (tens of seconds) after the session it
    describes was seen, so it may already have closed by the time this
    runs -- the UPDATE then simply matches zero rows. Not worth also
    patching connections_raw retroactively for that case, same "miss it,
    move on" acceptance as the existing close-partial-interval gap."""
    conn.execute(
        """
        UPDATE live_sessions SET dpi_protocol=?
        WHERE proto=? AND local_ip=? AND local_port=? AND peer_ip=? AND peer_port=?
        """,
        (dpi_protocol, proto, local_ip, local_port, peer_ip, peer_port),
    )
    conn.commit()


def record_dns_query_event(
    conn: sqlite3.Connection, ev: "QueryEvent", bucket_size_s: int = 3600
) -> None:
    """Upserts one dns_sniffer.QueryEvent into dns_query_log, keyed by
    (bucket_start, local_ip, query_name, query_type) -- a repeat of the
    same lookup within the same hour bumps `count` and refreshes
    `rcode`/`answers`/`last_seen` in place rather than inserting a new
    row, same idiom record_diff() already uses for live_sessions. This
    is what keeps this table's growth bounded by distinct (host, query,
    type) combinations per hour rather than raw query frequency -- see
    dns_query_log's own schema comment for why that distinction matters
    here specifically."""
    bucket_start = floor_to(ev.seen_at, bucket_size_s)
    conn.execute(
        """
        INSERT INTO dns_query_log
            (bucket_start, local_ip, query_name, query_type, rcode, answers,
             count, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(bucket_start, local_ip, query_name, query_type)
        DO UPDATE SET
            count = count + 1,
            rcode = excluded.rcode,
            answers = excluded.answers,
            last_seen = excluded.last_seen
        """,
        (
            bucket_start, ev.local_ip, ev.query_name, ev.query_type, ev.rcode, ev.answers,
            ev.seen_at, ev.seen_at,
        ),
    )
    conn.commit()

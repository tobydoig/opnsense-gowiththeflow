"""Hourly/daily rollup aggregation, long-lived-session checkpointing, and
retention pruning.

Stage A3: exercised against synthetic connections_raw/rollup_hourly rows,
no networking code involved. All bucket math is done in whole seconds
against caller-supplied `now` (never Python's own time.time() inside this
module), so tests are fully deterministic.
"""

from __future__ import annotations

import ipaddress
import sqlite3

HOUR = 3600
DAY = 86400


def floor_to(ts: int, size: int) -> int:
    return ts - (ts % size)


def _watermark(conn: sqlite3.Connection, bucket_kind: str) -> int:
    row = conn.execute(
        "SELECT last_bucket_start FROM rollup_state WHERE bucket_kind=?",
        (bucket_kind,),
    ).fetchone()
    return row[0] if row else 0


def _set_watermark(conn: sqlite3.Connection, bucket_kind: str, bucket_start: int) -> None:
    conn.execute(
        """
        INSERT INTO rollup_state (bucket_kind, last_bucket_start) VALUES (?, ?)
        ON CONFLICT(bucket_kind) DO UPDATE SET last_bucket_start=excluded.last_bucket_start
        """,
        (bucket_kind, bucket_start),
    )


def checkpoint_long_lived_sessions(conn: sqlite3.Connection, now: int) -> int:
    """For any live_sessions row whose last checkpoint predates the most
    recently completed hour boundary, writes a synthetic connections_raw
    row covering bytes-since-last-checkpoint (using the baseline columns,
    since pf's own counters never reset) and advances the baseline -- so a
    long-lived session (a big download, a VPN tunnel) isn't invisible to
    hourly rollups until it finally closes. Returns the number of sessions
    checkpointed."""
    boundary = floor_to(now, HOUR)
    rows = conn.execute(
        "SELECT * FROM live_sessions WHERE last_checkpoint_at < ?", (boundary,)
    ).fetchall()

    for r in rows:
        delta_bytes_in = r["bytes_in"] - r["baseline_bytes_in"]
        delta_bytes_out = r["bytes_out"] - r["baseline_bytes_out"]
        delta_pkts_in = r["pkts_in"] - r["baseline_pkts_in"]
        delta_pkts_out = r["pkts_out"] - r["baseline_pkts_out"]

        conn.execute(
            """
            INSERT INTO connections_raw
                (proto, local_ip, remote_ip, remote_port, remote_hostname, hostname_source, category,
                 started_at, ended_at, duration_s, bytes_in, bytes_out, pkts_in, pkts_out)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["proto"], r["local_ip"], r["remote_ip"], r["remote_port"],
                r["remote_hostname"], r["hostname_source"], r["category"],
                r["last_checkpoint_at"], now, max(now - r["last_checkpoint_at"], 0),
                delta_bytes_in, delta_bytes_out, delta_pkts_in, delta_pkts_out,
            ),
        )
        conn.execute(
            """
            UPDATE live_sessions
            SET last_checkpoint_at=?,
                baseline_bytes_in=bytes_in, baseline_bytes_out=bytes_out,
                baseline_pkts_in=pkts_in, baseline_pkts_out=pkts_out
            WHERE id=?
            """,
            (now, r["id"]),
        )

    conn.commit()
    return len(rows)


def rollup_hourly(conn: sqlite3.Connection, now: int) -> list[int]:
    """Aggregates every fully-elapsed, not-yet-processed hour bucket of
    connections_raw into rollup_hourly. Returns the bucket_start values
    processed (empty if nothing new was ready)."""
    watermark = _watermark(conn, "hourly")
    last_complete_bucket = floor_to(now, HOUR) - HOUR
    processed = []

    if watermark == 0:
        min_ended = conn.execute("SELECT MIN(ended_at) FROM connections_raw").fetchone()[0]
        bucket = floor_to(min_ended, HOUR) if min_ended is not None else now
    else:
        bucket = watermark + HOUR

    while bucket <= last_complete_bucket:
        bucket_end = bucket + HOUR
        rows = conn.execute(
            """
            SELECT proto, local_ip, remote_ip, remote_hostname, hostname_source, category,
                   bytes_in, bytes_out, pkts_in, pkts_out, ended_at
            FROM connections_raw
            WHERE ended_at >= ? AND ended_at < ?
            """,
            (bucket, bucket_end),
        ).fetchall()

        groups: dict[tuple, dict] = {}
        for r in rows:
            key = (r["proto"], r["local_ip"], r["remote_ip"])
            g = groups.setdefault(
                key,
                {
                    "bytes_in": 0, "bytes_out": 0, "pkts_in": 0, "pkts_out": 0,
                    "conn_count": 0, "hostname": None, "hostname_source": None, "category": None,
                    "hostname_rank": -1,
                },
            )
            g["bytes_in"] += r["bytes_in"]
            g["bytes_out"] += r["bytes_out"]
            g["pkts_in"] += r["pkts_in"]
            g["pkts_out"] += r["pkts_out"]
            g["conn_count"] += 1
            if r["remote_hostname"] is not None and r["ended_at"] >= g["hostname_rank"]:
                g["hostname"] = r["remote_hostname"]
                g["hostname_source"] = r["hostname_source"]
                g["category"] = r["category"]
                g["hostname_rank"] = r["ended_at"]

        for (proto, local_ip, remote_ip), g in groups.items():
            conn.execute(
                """
                INSERT INTO rollup_hourly
                    (bucket_start, proto, local_ip, remote_ip, remote_hostname, hostname_source, category,
                     bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_start, proto, local_ip, remote_ip) DO UPDATE SET
                    bytes_in = bytes_in + excluded.bytes_in,
                    bytes_out = bytes_out + excluded.bytes_out,
                    pkts_in = pkts_in + excluded.pkts_in,
                    pkts_out = pkts_out + excluded.pkts_out,
                    conn_count = conn_count + excluded.conn_count,
                    remote_hostname = COALESCE(excluded.remote_hostname, remote_hostname),
                    hostname_source = COALESCE(excluded.hostname_source, hostname_source),
                    category = COALESCE(excluded.category, category)
                """,
                (
                    bucket, proto, local_ip, remote_ip, g["hostname"], g["hostname_source"], g["category"],
                    g["bytes_in"], g["bytes_out"], g["pkts_in"], g["pkts_out"], g["conn_count"],
                ),
            )

        processed.append(bucket)
        _set_watermark(conn, "hourly", bucket)
        bucket += HOUR

    conn.commit()
    return processed


def rollup_daily(conn: sqlite3.Connection, now: int) -> list[int]:
    """Aggregates every fully-elapsed, not-yet-processed day bucket of
    rollup_hourly into rollup_daily."""
    watermark = _watermark(conn, "daily")
    last_complete_bucket = floor_to(now, DAY) - DAY
    processed = []

    if watermark == 0:
        min_bucket = conn.execute("SELECT MIN(bucket_start) FROM rollup_hourly").fetchone()[0]
        bucket = floor_to(min_bucket, DAY) if min_bucket is not None else now
    else:
        bucket = watermark + DAY

    while bucket <= last_complete_bucket:
        bucket_end = bucket + DAY
        rows = conn.execute(
            """
            SELECT proto, local_ip, remote_ip, remote_hostname, hostname_source, category,
                   bytes_in, bytes_out, pkts_in, pkts_out, conn_count, bucket_start
            FROM rollup_hourly
            WHERE bucket_start >= ? AND bucket_start < ?
            """,
            (bucket, bucket_end),
        ).fetchall()

        groups: dict[tuple, dict] = {}
        for r in rows:
            key = (r["proto"], r["local_ip"], r["remote_ip"])
            g = groups.setdefault(
                key,
                {
                    "bytes_in": 0, "bytes_out": 0, "pkts_in": 0, "pkts_out": 0,
                    "conn_count": 0, "hostname": None, "hostname_source": None, "category": None,
                    "hostname_rank": -1,
                },
            )
            g["bytes_in"] += r["bytes_in"]
            g["bytes_out"] += r["bytes_out"]
            g["pkts_in"] += r["pkts_in"]
            g["pkts_out"] += r["pkts_out"]
            g["conn_count"] += r["conn_count"]
            if r["remote_hostname"] is not None and r["bucket_start"] >= g["hostname_rank"]:
                g["hostname"] = r["remote_hostname"]
                g["hostname_source"] = r["hostname_source"]
                g["category"] = r["category"]
                g["hostname_rank"] = r["bucket_start"]

        for (proto, local_ip, remote_ip), g in groups.items():
            conn.execute(
                """
                INSERT INTO rollup_daily
                    (bucket_start, proto, local_ip, remote_ip, remote_hostname, hostname_source, category,
                     bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_start, proto, local_ip, remote_ip) DO UPDATE SET
                    bytes_in = bytes_in + excluded.bytes_in,
                    bytes_out = bytes_out + excluded.bytes_out,
                    pkts_in = pkts_in + excluded.pkts_in,
                    pkts_out = pkts_out + excluded.pkts_out,
                    conn_count = conn_count + excluded.conn_count,
                    remote_hostname = COALESCE(excluded.remote_hostname, remote_hostname),
                    hostname_source = COALESCE(excluded.hostname_source, hostname_source),
                    category = COALESCE(excluded.category, category)
                """,
                (
                    bucket, proto, local_ip, remote_ip, g["hostname"], g["hostname_source"], g["category"],
                    g["bytes_in"], g["bytes_out"], g["pkts_in"], g["pkts_out"], g["conn_count"],
                ),
            )

        processed.append(bucket)
        _set_watermark(conn, "daily", bucket)
        bucket += DAY

    conn.commit()
    return processed


def checkpoint_long_lived_internal_sessions(conn: sqlite3.Connection, now: int) -> int:
    """Mirror of checkpoint_long_lived_sessions() for the internal
    (local<->local) pipeline -- no hostname/category fields to carry."""
    boundary = floor_to(now, HOUR)
    rows = conn.execute(
        "SELECT * FROM internal_live_sessions WHERE last_checkpoint_at < ?", (boundary,)
    ).fetchall()

    for r in rows:
        delta_bytes_a_to_b = r["bytes_a_to_b"] - r["baseline_bytes_a_to_b"]
        delta_bytes_b_to_a = r["bytes_b_to_a"] - r["baseline_bytes_b_to_a"]
        delta_pkts_a_to_b = r["pkts_a_to_b"] - r["baseline_pkts_a_to_b"]
        delta_pkts_b_to_a = r["pkts_b_to_a"] - r["baseline_pkts_b_to_a"]

        conn.execute(
            """
            INSERT INTO internal_connections_raw
                (proto, ip_a, ip_b, port_b,
                 started_at, ended_at, duration_s, bytes_a_to_b, bytes_b_to_a, pkts_a_to_b, pkts_b_to_a)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["proto"], r["ip_a"], r["ip_b"], r["port_b"],
                r["last_checkpoint_at"], now, max(now - r["last_checkpoint_at"], 0),
                delta_bytes_a_to_b, delta_bytes_b_to_a, delta_pkts_a_to_b, delta_pkts_b_to_a,
            ),
        )
        conn.execute(
            """
            UPDATE internal_live_sessions
            SET last_checkpoint_at=?,
                baseline_bytes_a_to_b=bytes_a_to_b, baseline_bytes_b_to_a=bytes_b_to_a,
                baseline_pkts_a_to_b=pkts_a_to_b, baseline_pkts_b_to_a=pkts_b_to_a
            WHERE id=?
            """,
            (now, r["id"]),
        )

    conn.commit()
    return len(rows)


def _canonicalize_pair(ip_a, ip_b, bytes_a_to_b, bytes_b_to_a, pkts_a_to_b, pkts_b_to_a):
    """internal_connections_raw is uncanonicalized (ip_a/ip_b are whichever
    side pf called src/dst for that particular flow) -- without this, the
    same device pair fragments into two rollup rows whenever traffic is
    initiated from both directions across different flows (e.g. host A
    mounts a share on host B during the day, host B backs up to host A
    overnight), splitting their combined total instead of ranking them as
    one pair. Comparing as strings would be wrong here -- dotted-quad IPs
    don't sort numerically ("192.168.1.10" < "192.168.1.2" as strings)."""
    if ipaddress.ip_address(ip_a) > ipaddress.ip_address(ip_b):
        return ip_b, ip_a, bytes_b_to_a, bytes_a_to_b, pkts_b_to_a, pkts_a_to_b
    return ip_a, ip_b, bytes_a_to_b, bytes_b_to_a, pkts_a_to_b, pkts_b_to_a


def rollup_internal_hourly(conn: sqlite3.Connection, now: int) -> list[int]:
    """Mirror of rollup_hourly() for the internal pipeline -- canonicalizes
    each pair (see _canonicalize_pair) before grouping, since the source
    data isn't. No hostname_rank bookkeeping needed (no hostname/category
    fields at all in this pipeline)."""
    watermark = _watermark(conn, "internal_hourly")
    last_complete_bucket = floor_to(now, HOUR) - HOUR
    processed = []

    if watermark == 0:
        min_ended = conn.execute("SELECT MIN(ended_at) FROM internal_connections_raw").fetchone()[0]
        bucket = floor_to(min_ended, HOUR) if min_ended is not None else now
    else:
        bucket = watermark + HOUR

    while bucket <= last_complete_bucket:
        bucket_end = bucket + HOUR
        rows = conn.execute(
            """
            SELECT proto, ip_a, ip_b, bytes_a_to_b, bytes_b_to_a, pkts_a_to_b, pkts_b_to_a
            FROM internal_connections_raw
            WHERE ended_at >= ? AND ended_at < ?
            """,
            (bucket, bucket_end),
        ).fetchall()

        groups: dict[tuple, dict] = {}
        for r in rows:
            ip_a, ip_b, bytes_a_to_b, bytes_b_to_a, pkts_a_to_b, pkts_b_to_a = _canonicalize_pair(
                r["ip_a"], r["ip_b"], r["bytes_a_to_b"], r["bytes_b_to_a"],
                r["pkts_a_to_b"], r["pkts_b_to_a"],
            )
            key = (r["proto"], ip_a, ip_b)
            g = groups.setdefault(
                key,
                {"bytes_a_to_b": 0, "bytes_b_to_a": 0, "pkts_a_to_b": 0, "pkts_b_to_a": 0, "conn_count": 0},
            )
            g["bytes_a_to_b"] += bytes_a_to_b
            g["bytes_b_to_a"] += bytes_b_to_a
            g["pkts_a_to_b"] += pkts_a_to_b
            g["pkts_b_to_a"] += pkts_b_to_a
            g["conn_count"] += 1

        for (proto, ip_a, ip_b), g in groups.items():
            conn.execute(
                """
                INSERT INTO internal_rollup_hourly
                    (bucket_start, proto, ip_a, ip_b,
                     bytes_a_to_b, bytes_b_to_a, pkts_a_to_b, pkts_b_to_a, conn_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_start, proto, ip_a, ip_b) DO UPDATE SET
                    bytes_a_to_b = bytes_a_to_b + excluded.bytes_a_to_b,
                    bytes_b_to_a = bytes_b_to_a + excluded.bytes_b_to_a,
                    pkts_a_to_b = pkts_a_to_b + excluded.pkts_a_to_b,
                    pkts_b_to_a = pkts_b_to_a + excluded.pkts_b_to_a,
                    conn_count = conn_count + excluded.conn_count
                """,
                (
                    bucket, proto, ip_a, ip_b,
                    g["bytes_a_to_b"], g["bytes_b_to_a"], g["pkts_a_to_b"], g["pkts_b_to_a"],
                    g["conn_count"],
                ),
            )

        processed.append(bucket)
        _set_watermark(conn, "internal_hourly", bucket)
        bucket += HOUR

    conn.commit()
    return processed


def rollup_internal_daily(conn: sqlite3.Connection, now: int) -> list[int]:
    """Mirror of rollup_daily() for the internal pipeline. No canonicalization
    needed here -- internal_rollup_hourly rows are already canonical."""
    watermark = _watermark(conn, "internal_daily")
    last_complete_bucket = floor_to(now, DAY) - DAY
    processed = []

    if watermark == 0:
        min_bucket = conn.execute("SELECT MIN(bucket_start) FROM internal_rollup_hourly").fetchone()[0]
        bucket = floor_to(min_bucket, DAY) if min_bucket is not None else now
    else:
        bucket = watermark + DAY

    while bucket <= last_complete_bucket:
        bucket_end = bucket + DAY
        rows = conn.execute(
            """
            SELECT proto, ip_a, ip_b, bytes_a_to_b, bytes_b_to_a, pkts_a_to_b, pkts_b_to_a, conn_count
            FROM internal_rollup_hourly
            WHERE bucket_start >= ? AND bucket_start < ?
            """,
            (bucket, bucket_end),
        ).fetchall()

        groups: dict[tuple, dict] = {}
        for r in rows:
            key = (r["proto"], r["ip_a"], r["ip_b"])
            g = groups.setdefault(
                key,
                {"bytes_a_to_b": 0, "bytes_b_to_a": 0, "pkts_a_to_b": 0, "pkts_b_to_a": 0, "conn_count": 0},
            )
            g["bytes_a_to_b"] += r["bytes_a_to_b"]
            g["bytes_b_to_a"] += r["bytes_b_to_a"]
            g["pkts_a_to_b"] += r["pkts_a_to_b"]
            g["pkts_b_to_a"] += r["pkts_b_to_a"]
            g["conn_count"] += r["conn_count"]

        for (proto, ip_a, ip_b), g in groups.items():
            conn.execute(
                """
                INSERT INTO internal_rollup_daily
                    (bucket_start, proto, ip_a, ip_b,
                     bytes_a_to_b, bytes_b_to_a, pkts_a_to_b, pkts_b_to_a, conn_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_start, proto, ip_a, ip_b) DO UPDATE SET
                    bytes_a_to_b = bytes_a_to_b + excluded.bytes_a_to_b,
                    bytes_b_to_a = bytes_b_to_a + excluded.bytes_b_to_a,
                    pkts_a_to_b = pkts_a_to_b + excluded.pkts_a_to_b,
                    pkts_b_to_a = pkts_b_to_a + excluded.pkts_b_to_a,
                    conn_count = conn_count + excluded.conn_count
                """,
                (
                    bucket, proto, ip_a, ip_b,
                    g["bytes_a_to_b"], g["bytes_b_to_a"], g["pkts_a_to_b"], g["pkts_b_to_a"],
                    g["conn_count"],
                ),
            )

        processed.append(bucket)
        _set_watermark(conn, "internal_daily", bucket)
        bucket += DAY

    conn.commit()
    return processed


def prune_raw(
    conn: sqlite3.Connection,
    now: int,
    raw_retention_days: int,
    table: str = "connections_raw",
    rollup_watermark_kind: str = "hourly",
) -> int:
    """Deletes rows older than raw_retention_days from `table`, but never
    past the end of the last hourly-rolled-up bucket for
    `rollup_watermark_kind` (and not at all if that rollup has never run)
    -- raw data is never dropped before it's actually been aggregated,
    even if rollup has fallen behind. Parameterized on table/watermark
    kind rather than duplicated -- unlike checkpoint/rollup, prune's SQL
    shape is identical regardless of which pipeline it's pruning (no
    column-set differences), so this is a pure, risk-free generalization;
    every existing call site keeps working unchanged via the defaults."""
    hourly_watermark = _watermark(conn, rollup_watermark_kind)
    if hourly_watermark == 0:
        return 0
    cutoff = now - raw_retention_days * DAY
    safe_cutoff = min(cutoff, hourly_watermark + HOUR)
    cur = conn.execute(f"DELETE FROM {table} WHERE ended_at < ?", (safe_cutoff,))
    conn.commit()
    return cur.rowcount


def prune_hourly(
    conn: sqlite3.Connection,
    now: int,
    hourly_retention_days: int,
    table: str = "rollup_hourly",
    rollup_watermark_kind: str = "daily",
) -> int:
    """Deletes rows older than hourly_retention_days from `table`, but
    never past the end of the last daily-rolled-up bucket for
    `rollup_watermark_kind`, and not at all if that rollup has never run."""
    daily_watermark = _watermark(conn, rollup_watermark_kind)
    if daily_watermark == 0:
        return 0
    cutoff = now - hourly_retention_days * DAY
    safe_cutoff = min(cutoff, daily_watermark + DAY)
    cur = conn.execute(f"DELETE FROM {table} WHERE bucket_start < ?", (safe_cutoff,))
    conn.commit()
    return cur.rowcount


def prune_daily(conn: sqlite3.Connection, now: int, daily_retention_days: int, table: str = "rollup_daily") -> int:
    cutoff = now - daily_retention_days * DAY
    cur = conn.execute(f"DELETE FROM {table} WHERE bucket_start < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def incremental_vacuum(conn: sqlite3.Connection, pages: int = 1000) -> None:
    conn.execute(f"PRAGMA incremental_vacuum({pages})")
    conn.commit()

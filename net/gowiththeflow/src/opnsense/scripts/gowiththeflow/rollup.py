"""Hourly/daily rollup aggregation, long-lived-session checkpointing, and
retention pruning.

Stage A3: exercised against synthetic connections_raw/rollup_hourly rows,
no networking code involved. All bucket math is done in whole seconds
against caller-supplied `now` (never Python's own time.time() inside this
module), so tests are fully deterministic.
"""

from __future__ import annotations

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
                (proto, local_ip, remote_ip, remote_port, remote_hostname, hostname_source,
                 started_at, ended_at, duration_s, bytes_in, bytes_out, pkts_in, pkts_out)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["proto"], r["local_ip"], r["remote_ip"], r["remote_port"],
                r["remote_hostname"], r["hostname_source"],
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
            SELECT proto, local_ip, remote_ip, remote_hostname, hostname_source,
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
                    "conn_count": 0, "hostname": None, "hostname_source": None,
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
                g["hostname_rank"] = r["ended_at"]

        for (proto, local_ip, remote_ip), g in groups.items():
            conn.execute(
                """
                INSERT INTO rollup_hourly
                    (bucket_start, proto, local_ip, remote_ip, remote_hostname, hostname_source,
                     bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_start, proto, local_ip, remote_ip) DO UPDATE SET
                    bytes_in = bytes_in + excluded.bytes_in,
                    bytes_out = bytes_out + excluded.bytes_out,
                    pkts_in = pkts_in + excluded.pkts_in,
                    pkts_out = pkts_out + excluded.pkts_out,
                    conn_count = conn_count + excluded.conn_count,
                    remote_hostname = COALESCE(excluded.remote_hostname, remote_hostname),
                    hostname_source = COALESCE(excluded.hostname_source, hostname_source)
                """,
                (
                    bucket, proto, local_ip, remote_ip, g["hostname"], g["hostname_source"],
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
            SELECT proto, local_ip, remote_ip, remote_hostname, hostname_source,
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
                    "conn_count": 0, "hostname": None, "hostname_source": None,
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
                g["hostname_rank"] = r["bucket_start"]

        for (proto, local_ip, remote_ip), g in groups.items():
            conn.execute(
                """
                INSERT INTO rollup_daily
                    (bucket_start, proto, local_ip, remote_ip, remote_hostname, hostname_source,
                     bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_start, proto, local_ip, remote_ip) DO UPDATE SET
                    bytes_in = bytes_in + excluded.bytes_in,
                    bytes_out = bytes_out + excluded.bytes_out,
                    pkts_in = pkts_in + excluded.pkts_in,
                    pkts_out = pkts_out + excluded.pkts_out,
                    conn_count = conn_count + excluded.conn_count,
                    remote_hostname = COALESCE(excluded.remote_hostname, remote_hostname),
                    hostname_source = COALESCE(excluded.hostname_source, hostname_source)
                """,
                (
                    bucket, proto, local_ip, remote_ip, g["hostname"], g["hostname_source"],
                    g["bytes_in"], g["bytes_out"], g["pkts_in"], g["pkts_out"], g["conn_count"],
                ),
            )

        processed.append(bucket)
        _set_watermark(conn, "daily", bucket)
        bucket += DAY

    conn.commit()
    return processed


def prune_raw(conn: sqlite3.Connection, now: int, raw_retention_days: int) -> int:
    """Deletes connections_raw rows older than raw_retention_days, but
    never past the end of the last hourly-rolled-up bucket (and not at all
    if hourly rollup has never run) -- raw data is never dropped before
    it's actually been aggregated, even if rollup has fallen behind."""
    hourly_watermark = _watermark(conn, "hourly")
    if hourly_watermark == 0:
        return 0
    cutoff = now - raw_retention_days * DAY
    safe_cutoff = min(cutoff, hourly_watermark + HOUR)
    cur = conn.execute("DELETE FROM connections_raw WHERE ended_at < ?", (safe_cutoff,))
    conn.commit()
    return cur.rowcount


def prune_hourly(conn: sqlite3.Connection, now: int, hourly_retention_days: int) -> int:
    """Deletes rollup_hourly rows older than hourly_retention_days, but
    never past the end of the last daily-rolled-up bucket, and not at all
    if daily rollup has never run."""
    daily_watermark = _watermark(conn, "daily")
    if daily_watermark == 0:
        return 0
    cutoff = now - hourly_retention_days * DAY
    safe_cutoff = min(cutoff, daily_watermark + DAY)
    cur = conn.execute("DELETE FROM rollup_hourly WHERE bucket_start < ?", (safe_cutoff,))
    conn.commit()
    return cur.rowcount


def prune_daily(conn: sqlite3.Connection, now: int, daily_retention_days: int) -> int:
    cutoff = now - daily_retention_days * DAY
    cur = conn.execute("DELETE FROM rollup_daily WHERE bucket_start < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def incremental_vacuum(conn: sqlite3.Connection, pages: int = 1000) -> None:
    conn.execute(f"PRAGMA incremental_vacuum({pages})")
    conn.commit()

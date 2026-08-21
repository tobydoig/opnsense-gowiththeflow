"""Durable IP -> hostname cache (ip_hostname_cache), combining DNS/SNI/PTR
observations under a source-priority hierarchy: DNS > SNI > PTR. A more
trusted source always wins outright. A less-trusted source is ignored
while a more-trusted entry is still valid, but can fill the gap once that
entry expires. A source disagreeing with its *own* prior entry for the
same IP is a genuine ambiguity signal (e.g. a shared CDN edge serving
several different sites) and is excluded from generic lookups -- a
per-flow SNI hint (sni_sniffer.py's FlowHintCache), checked earlier in
correlator.py's priority order, is unaffected and still resolves that one
flow correctly.
"""

from __future__ import annotations

import sqlite3

_SOURCE_RANK = {"dns": 3, "sni": 2, "ptr": 1}


def upsert_hostname(
    conn: sqlite3.Connection, ip: str, hostname: str, source: str, ttl: int, now: int
) -> None:
    row = conn.execute("SELECT * FROM ip_hostname_cache WHERE ip=?", (ip,)).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO ip_hostname_cache
                (ip, hostname, source, ambiguous, ttl_expires_at, first_seen, last_seen, hit_count)
            VALUES (?, ?, ?, 0, ?, ?, ?, 1)
            """,
            (ip, hostname, source, now + ttl, now, now),
        )
        conn.commit()
        return

    existing_rank = _SOURCE_RANK[row["source"]]
    new_rank = _SOURCE_RANK[source]

    if new_rank > existing_rank or (new_rank < existing_rank and now >= row["ttl_expires_at"]):
        # A more trusted source speaks (or the prior trusted entry expired
        # and a lower-priority source can now fill the gap) -- wins
        # outright, clearing any prior ambiguity.
        conn.execute(
            """
            UPDATE ip_hostname_cache
            SET hostname=?, source=?, ambiguous=0, ttl_expires_at=?, last_seen=?, hit_count=1
            WHERE ip=?
            """,
            (hostname, source, now + ttl, now, ip),
        )
    elif new_rank < existing_rank:
        pass  # lower-priority source, higher-priority entry still valid -- ignored
    elif hostname == row["hostname"]:
        conn.execute(
            """
            UPDATE ip_hostname_cache
            SET ttl_expires_at=?, last_seen=?, hit_count=hit_count+1
            WHERE ip=?
            """,
            (now + ttl, now, ip),
        )
    else:
        # Same-tier source now reports a DIFFERENT hostname for this IP --
        # a genuine conflict. Recorded, but flagged so get_hostname()
        # excludes it from generic (non-flow-specific) lookups.
        conn.execute(
            """
            UPDATE ip_hostname_cache
            SET hostname=?, ambiguous=1, ttl_expires_at=?, last_seen=?, hit_count=1
            WHERE ip=?
            """,
            (hostname, now + ttl, now, ip),
        )
    conn.commit()


def get_hostname(conn: sqlite3.Connection, ip: str, now: int) -> tuple[str | None, str | None]:
    """Returns (hostname, source), or (None, None) if there's no entry, it
    has expired, or it's flagged ambiguous."""
    row = conn.execute("SELECT * FROM ip_hostname_cache WHERE ip=?", (ip,)).fetchone()
    if row is None:
        return None, None
    if now >= row["ttl_expires_at"] or row["ambiguous"]:
        return None, None
    return row["hostname"], row["source"]

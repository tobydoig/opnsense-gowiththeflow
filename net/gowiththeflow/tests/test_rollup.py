import db
import rollup

HOUR = rollup.HOUR
DAY = rollup.DAY

BUCKET0 = 3_600_000  # exactly on an hour boundary, for arithmetic clarity
BUCKET1 = BUCKET0 + HOUR


def _fresh_conn(tmp_path):
    conn = db.connect(str(tmp_path / "flows.db"))
    db.init_schema(conn)
    return conn


def _insert_raw(conn, *, local_ip, peer_ip, ended_at, bytes_in, bytes_out,
                 pkts_in=1, pkts_out=1, hostname=None, hostname_source=None, category=None,
                 peer_is_local=0, proto="tcp", peer_port=443, duration_s=10):
    conn.execute(
        """
        INSERT INTO connections_raw
            (proto, local_ip, peer_ip, peer_port, peer_is_local, peer_hostname, hostname_source, category,
             started_at, ended_at, duration_s, bytes_in, bytes_out, pkts_in, pkts_out)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            proto, local_ip, peer_ip, peer_port, peer_is_local, hostname, hostname_source, category,
            ended_at - duration_s, ended_at, duration_s, bytes_in, bytes_out, pkts_in, pkts_out,
        ),
    )
    conn.commit()


def test_rollup_hourly_sums_within_bucket_and_picks_most_recent_hostname(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_raw(conn, local_ip="192.168.1.10", peer_ip="1.2.3.4",
                ended_at=BUCKET0 + 100, bytes_in=100, bytes_out=200,
                hostname="a.com", category="Cloud/Productivity")
    _insert_raw(conn, local_ip="192.168.1.10", peer_ip="1.2.3.4",
                ended_at=BUCKET0 + 200, bytes_in=50, bytes_out=60,
                hostname="a2.com", category="Shopping")  # later ended_at -> should win as the group's hostname/category
    _insert_raw(conn, local_ip="192.168.1.10", peer_ip="1.2.3.4",
                ended_at=BUCKET1 + 50, bytes_in=10, bytes_out=20,
                hostname=None)  # separate bucket, no hostname/category resolved this time

    now = BUCKET1 + HOUR + 10  # both buckets fully elapsed
    processed = rollup.rollup_hourly(conn, now)
    assert processed == [BUCKET0, BUCKET1]

    rows = {r["bucket_start"]: r for r in conn.execute("SELECT * FROM rollup_hourly")}
    assert rows[BUCKET0]["conn_count"] == 2
    assert rows[BUCKET0]["bytes_in"] == 150
    assert rows[BUCKET0]["bytes_out"] == 260
    assert rows[BUCKET0]["peer_hostname"] == "a2.com"
    assert rows[BUCKET0]["category"] == "Shopping"
    assert rows[BUCKET0]["peer_is_local"] == 0

    assert rows[BUCKET1]["conn_count"] == 1
    assert rows[BUCKET1]["bytes_in"] == 10
    assert rows[BUCKET1]["peer_hostname"] is None
    assert rows[BUCKET1]["category"] is None


def test_rollup_hourly_is_idempotent_when_nothing_new_is_ready(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_raw(conn, local_ip="192.168.1.10", peer_ip="1.2.3.4",
                ended_at=BUCKET0 + 100, bytes_in=100, bytes_out=200)

    now = BUCKET0 + HOUR + 10
    assert rollup.rollup_hourly(conn, now) == [BUCKET0]
    assert rollup.rollup_hourly(conn, now) == []  # nothing new the second time


def test_rollup_hourly_does_not_touch_the_still_filling_current_bucket(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_raw(conn, local_ip="192.168.1.10", peer_ip="1.2.3.4",
                ended_at=BUCKET0 + 100, bytes_in=100, bytes_out=200)

    now = BUCKET0 + 500  # still inside the same hour -- not complete yet
    assert rollup.rollup_hourly(conn, now) == []
    assert conn.execute("SELECT COUNT(*) FROM rollup_hourly").fetchone()[0] == 0


def test_rollup_hourly_merges_local_peer_pairs_regardless_of_initiation_direction(tmp_path):
    # The critical regression this whole local/peer unification must not
    # lose: without canonicalizing local_ip/peer_ip before grouping, the
    # same device pair fragments into two rollup rows whenever traffic is
    # initiated from both directions across different flows (e.g. host A
    # mounts a share on host B during the day, host B backs up to host A
    # overnight) -- undermining the point of a "which pairs talk the most"
    # ranking.
    conn = _fresh_conn(tmp_path)
    # Flow 1, initiated by .10: local_ip=.10, so bytes_out=.10->.20=100,
    # bytes_in=.20->.10=50.
    _insert_raw(conn, local_ip="192.168.1.10", peer_ip="192.168.1.20",
                ended_at=BUCKET0 + 100, bytes_in=50, bytes_out=100, peer_is_local=1)
    # The SAME physical pair, but this flow was initiated by .20: local_ip=.20,
    # so bytes_out=.20->.10=30, bytes_in=.10->.20=15.
    _insert_raw(conn, local_ip="192.168.1.20", peer_ip="192.168.1.10",
                ended_at=BUCKET0 + 200, bytes_in=15, bytes_out=30, peer_is_local=1)

    now = BUCKET0 + HOUR + 10
    processed = rollup.rollup_hourly(conn, now)
    assert processed == [BUCKET0]

    rows = conn.execute("SELECT * FROM rollup_hourly").fetchall()
    assert len(rows) == 1  # one merged pair, not two
    row = rows[0]
    # Canonical order: .10 < .20 numerically -> local_ip=.10, peer_ip=.20.
    assert row["local_ip"] == "192.168.1.10"
    assert row["peer_ip"] == "192.168.1.20"
    assert row["peer_is_local"] == 1
    # Flow 1 contributed as-is (already canonical): bytes_in=50, bytes_out=100.
    # Flow 2 was stored from .20's perspective (the "wrong" side of
    # canonical order), so its bytes_in/out swap when canonicalized:
    # its bytes_out=30 (.20->.10) becomes canonical bytes_in, and its
    # bytes_in=15 (.10->.20) becomes canonical bytes_out.
    assert row["bytes_in"] == 50 + 30
    assert row["bytes_out"] == 100 + 15
    assert row["conn_count"] == 2


def test_rollup_hourly_does_not_canonicalize_remote_peer_pairs_even_when_local_ip_is_numerically_larger(tmp_path):
    # peer_is_local=0 rows must NEVER be swapped, even when local_ip is
    # numerically larger than peer_ip -- they're already canonical by
    # role (local_ip is always the genuinely local side, decided once in
    # pf_state_poller.classify_sessions()), not by IP value. Pins the
    # branch condition itself, not just the swap math.
    conn = _fresh_conn(tmp_path)
    _insert_raw(conn, local_ip="192.168.1.200", peer_ip="1.2.3.4",
                ended_at=BUCKET0 + 100, bytes_in=50, bytes_out=100, peer_is_local=0)

    now = BUCKET0 + HOUR + 10
    rollup.rollup_hourly(conn, now)

    row = conn.execute("SELECT * FROM rollup_hourly").fetchone()
    assert row["local_ip"] == "192.168.1.200"
    assert row["peer_ip"] == "1.2.3.4"
    assert row["bytes_in"] == 50
    assert row["bytes_out"] == 100


def test_rollup_daily_sums_hourly_buckets_within_a_day(tmp_path):
    conn = _fresh_conn(tmp_path)
    day_start = 10 * DAY  # aligned to a day boundary
    conn.execute(
        """
        INSERT INTO rollup_hourly
            (bucket_start, proto, local_ip, peer_ip, peer_hostname, hostname_source, category,
             bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
        VALUES (?, 'tcp', '192.168.1.10', '1.2.3.4', 'a.com', 'dns', 'Cloud/Productivity', 100, 200, 1, 2, 3)
        """,
        (day_start,),
    )
    conn.execute(
        """
        INSERT INTO rollup_hourly
            (bucket_start, proto, local_ip, peer_ip, peer_hostname, hostname_source, category,
             bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
        VALUES (?, 'tcp', '192.168.1.10', '1.2.3.4', 'a2.com', 'sni', 'Shopping', 50, 60, 1, 1, 1)
        """,
        (day_start + HOUR,),
    )
    conn.commit()

    now = day_start + DAY + 10
    processed = rollup.rollup_daily(conn, now)
    assert processed == [day_start]

    row = conn.execute("SELECT * FROM rollup_daily").fetchone()
    assert row["bytes_in"] == 150
    assert row["bytes_out"] == 260
    assert row["conn_count"] == 4
    assert row["peer_hostname"] == "a2.com"  # from the later hourly bucket
    assert row["category"] == "Shopping"


def test_prune_raw_never_deletes_past_the_hourly_watermark(tmp_path):
    conn = _fresh_conn(tmp_path)
    old_ended_at = 100 * DAY
    _insert_raw(conn, local_ip="192.168.1.10", peer_ip="1.2.3.4",
                ended_at=old_ended_at, bytes_in=1, bytes_out=1)

    now = 130 * DAY  # 30 days later; well past a 10-day retention window

    # Hourly rollup has never run -- must refuse to delete anything at all.
    assert rollup.prune_raw(conn, now, raw_retention_days=10) == 0
    assert conn.execute("SELECT COUNT(*) FROM connections_raw").fetchone()[0] == 1

    # Now roll it up, which makes it safe to prune.
    rollup.rollup_hourly(conn, now)
    deleted = rollup.prune_raw(conn, now, raw_retention_days=10)
    assert deleted == 1
    assert conn.execute("SELECT COUNT(*) FROM connections_raw").fetchone()[0] == 0


def test_prune_hourly_refuses_when_daily_rollup_has_never_run(tmp_path):
    conn = _fresh_conn(tmp_path)
    conn.execute(
        """
        INSERT INTO rollup_hourly
            (bucket_start, proto, local_ip, peer_ip, bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
        VALUES (0, 'tcp', '192.168.1.10', '1.2.3.4', 1, 1, 1, 1, 1)
        """
    )
    conn.commit()
    now = 100 * DAY
    assert rollup.prune_hourly(conn, now, hourly_retention_days=1) == 0
    assert conn.execute("SELECT COUNT(*) FROM rollup_hourly").fetchone()[0] == 1


def test_prune_daily_deletes_buckets_older_than_retention(tmp_path):
    conn = _fresh_conn(tmp_path)
    conn.execute(
        """
        INSERT INTO rollup_daily
            (bucket_start, proto, local_ip, peer_ip, bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
        VALUES (0, 'tcp', '192.168.1.10', '1.2.3.4', 1, 1, 1, 1, 1)
        """
    )
    conn.commit()
    assert rollup.prune_daily(conn, 100 * DAY, daily_retention_days=10) == 1


def test_checkpoint_long_lived_session_writes_delta_and_advances_baseline(tmp_path):
    conn = _fresh_conn(tmp_path)
    t0 = BUCKET0
    conn.execute(
        """
        INSERT INTO live_sessions
            (proto, local_ip, local_port, peer_ip, peer_port, category,
             first_seen, last_seen, bytes_in, bytes_out, pkts_in, pkts_out, last_checkpoint_at)
        VALUES ('tcp', '192.168.1.10', 5000, '9.9.9.9', 443, 'Cloud/Productivity', ?, ?, 100000, 200000, 100, 200, ?)
        """,
        (t0, t0, t0),
    )
    conn.commit()

    now_1 = t0 + 2 * HOUR
    count = rollup.checkpoint_long_lived_sessions(conn, now_1)
    assert count == 1

    raw_rows = conn.execute("SELECT * FROM connections_raw").fetchall()
    assert len(raw_rows) == 1
    assert raw_rows[0]["started_at"] == t0
    assert raw_rows[0]["ended_at"] == now_1
    assert raw_rows[0]["bytes_in"] == 100000  # full amount: baseline was 0
    assert raw_rows[0]["bytes_out"] == 200000
    assert raw_rows[0]["category"] == "Cloud/Productivity"  # carried from the live session

    live = conn.execute("SELECT * FROM live_sessions").fetchone()
    assert live["baseline_bytes_in"] == 100000
    assert live["last_checkpoint_at"] == now_1

    # Calling again immediately (same hour boundary) must not double-checkpoint.
    assert rollup.checkpoint_long_lived_sessions(conn, now_1) == 0
    assert conn.execute("SELECT COUNT(*) FROM connections_raw").fetchone()[0] == 1

    # Session keeps running and gains more bytes; next hour boundary checkpoints again.
    conn.execute("UPDATE live_sessions SET bytes_in=150000, bytes_out=260000")
    conn.commit()
    now_2 = now_1 + 2 * HOUR
    assert rollup.checkpoint_long_lived_sessions(conn, now_2) == 1

    raw_rows = conn.execute(
        "SELECT * FROM connections_raw ORDER BY ended_at"
    ).fetchall()
    assert len(raw_rows) == 2
    second = raw_rows[1]
    assert second["started_at"] == now_1
    assert second["ended_at"] == now_2
    assert second["bytes_in"] == 50000  # delta since the first checkpoint's baseline
    assert second["bytes_out"] == 60000


def test_checkpoint_long_lived_session_carries_peer_is_local_and_state_through(tmp_path):
    conn = _fresh_conn(tmp_path)
    t0 = BUCKET0
    conn.execute(
        """
        INSERT INTO live_sessions
            (proto, local_ip, local_port, peer_ip, peer_port, peer_is_local, category, state,
             first_seen, last_seen, bytes_in, bytes_out, pkts_in, pkts_out, last_checkpoint_at)
        VALUES ('tcp', '192.168.1.10', 5000, '192.168.1.20', 445, 1, 'Internal', 'ESTABLISHED:ESTABLISHED',
                ?, ?, 1000, 2000, 10, 20, ?)
        """,
        (t0, t0, t0),
    )
    conn.commit()

    now_1 = t0 + 2 * HOUR
    assert rollup.checkpoint_long_lived_sessions(conn, now_1) == 1

    row = conn.execute("SELECT * FROM connections_raw").fetchone()
    assert row["peer_is_local"] == 1
    assert row["category"] == "Internal"
    assert row["state"] == "ESTABLISHED:ESTABLISHED"

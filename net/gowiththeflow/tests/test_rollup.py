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


def _insert_raw(conn, *, local_ip, remote_ip, ended_at, bytes_in, bytes_out,
                 pkts_in=1, pkts_out=1, hostname=None, hostname_source=None, category=None,
                 proto="tcp", remote_port=443, duration_s=10):
    conn.execute(
        """
        INSERT INTO connections_raw
            (proto, local_ip, remote_ip, remote_port, remote_hostname, hostname_source, category,
             started_at, ended_at, duration_s, bytes_in, bytes_out, pkts_in, pkts_out)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            proto, local_ip, remote_ip, remote_port, hostname, hostname_source, category,
            ended_at - duration_s, ended_at, duration_s, bytes_in, bytes_out, pkts_in, pkts_out,
        ),
    )
    conn.commit()


def test_rollup_hourly_sums_within_bucket_and_picks_most_recent_hostname(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_raw(conn, local_ip="192.168.1.10", remote_ip="1.2.3.4",
                ended_at=BUCKET0 + 100, bytes_in=100, bytes_out=200,
                hostname="a.com", category="Cloud/Productivity")
    _insert_raw(conn, local_ip="192.168.1.10", remote_ip="1.2.3.4",
                ended_at=BUCKET0 + 200, bytes_in=50, bytes_out=60,
                hostname="a2.com", category="Shopping")  # later ended_at -> should win as the group's hostname/category
    _insert_raw(conn, local_ip="192.168.1.10", remote_ip="1.2.3.4",
                ended_at=BUCKET1 + 50, bytes_in=10, bytes_out=20,
                hostname=None)  # separate bucket, no hostname/category resolved this time

    now = BUCKET1 + HOUR + 10  # both buckets fully elapsed
    processed = rollup.rollup_hourly(conn, now)
    assert processed == [BUCKET0, BUCKET1]

    rows = {r["bucket_start"]: r for r in conn.execute("SELECT * FROM rollup_hourly")}
    assert rows[BUCKET0]["conn_count"] == 2
    assert rows[BUCKET0]["bytes_in"] == 150
    assert rows[BUCKET0]["bytes_out"] == 260
    assert rows[BUCKET0]["remote_hostname"] == "a2.com"
    assert rows[BUCKET0]["category"] == "Shopping"

    assert rows[BUCKET1]["conn_count"] == 1
    assert rows[BUCKET1]["bytes_in"] == 10
    assert rows[BUCKET1]["remote_hostname"] is None
    assert rows[BUCKET1]["category"] is None


def test_rollup_hourly_is_idempotent_when_nothing_new_is_ready(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_raw(conn, local_ip="192.168.1.10", remote_ip="1.2.3.4",
                ended_at=BUCKET0 + 100, bytes_in=100, bytes_out=200)

    now = BUCKET0 + HOUR + 10
    assert rollup.rollup_hourly(conn, now) == [BUCKET0]
    assert rollup.rollup_hourly(conn, now) == []  # nothing new the second time


def test_rollup_hourly_does_not_touch_the_still_filling_current_bucket(tmp_path):
    conn = _fresh_conn(tmp_path)
    _insert_raw(conn, local_ip="192.168.1.10", remote_ip="1.2.3.4",
                ended_at=BUCKET0 + 100, bytes_in=100, bytes_out=200)

    now = BUCKET0 + 500  # still inside the same hour -- not complete yet
    assert rollup.rollup_hourly(conn, now) == []
    assert conn.execute("SELECT COUNT(*) FROM rollup_hourly").fetchone()[0] == 0


def test_rollup_daily_sums_hourly_buckets_within_a_day(tmp_path):
    conn = _fresh_conn(tmp_path)
    day_start = 10 * DAY  # aligned to a day boundary
    conn.execute(
        """
        INSERT INTO rollup_hourly
            (bucket_start, proto, local_ip, remote_ip, remote_hostname, hostname_source, category,
             bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
        VALUES (?, 'tcp', '192.168.1.10', '1.2.3.4', 'a.com', 'dns', 'Cloud/Productivity', 100, 200, 1, 2, 3)
        """,
        (day_start,),
    )
    conn.execute(
        """
        INSERT INTO rollup_hourly
            (bucket_start, proto, local_ip, remote_ip, remote_hostname, hostname_source, category,
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
    assert row["remote_hostname"] == "a2.com"  # from the later hourly bucket
    assert row["category"] == "Shopping"


def test_prune_raw_never_deletes_past_the_hourly_watermark(tmp_path):
    conn = _fresh_conn(tmp_path)
    old_ended_at = 100 * DAY
    _insert_raw(conn, local_ip="192.168.1.10", remote_ip="1.2.3.4",
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
            (bucket_start, proto, local_ip, remote_ip, bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
        VALUES (0, 'tcp', '192.168.1.10', '1.2.3.4', 1, 1, 1, 1, 1)
        """
    )
    conn.commit()
    now = 100 * DAY
    assert rollup.prune_hourly(conn, now, hourly_retention_days=1) == 0
    assert conn.execute("SELECT COUNT(*) FROM rollup_hourly").fetchone()[0] == 1


def test_checkpoint_long_lived_session_writes_delta_and_advances_baseline(tmp_path):
    conn = _fresh_conn(tmp_path)
    t0 = BUCKET0
    conn.execute(
        """
        INSERT INTO live_sessions
            (proto, local_ip, local_port, remote_ip, remote_port, category,
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

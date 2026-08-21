import os

import db
from pf_state_poller import PfStatePoller

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
LOCAL_SUBNETS = ["192.168.1.0/24"]

NOW1 = 1_000_000
NOW2 = 1_000_005  # 5s later, matching the age deltas baked into the fixtures


def _load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


def _fresh_conn(tmp_path):
    conn = db.connect(str(tmp_path / "flows.db"))
    db.init_schema(conn)
    return conn


def test_opened_sessions_land_in_live_sessions_with_backdated_first_seen(tmp_path):
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)

    diff = poller.poll(_load_fixture("pfctl_state_poll_1.txt"))
    db.record_diff(conn, diff, now=NOW1)

    rows = {
        r["remote_ip"]: r
        for r in conn.execute("SELECT * FROM live_sessions").fetchall()
    }
    assert set(rows) == {"93.184.216.34", "8.8.8.8"}

    tcp_row = rows["93.184.216.34"]
    assert tcp_row["local_ip"] == "192.168.1.50"
    assert tcp_row["first_seen"] == NOW1 - 12  # age was 00:00:12
    assert tcp_row["last_seen"] == NOW1
    assert tcp_row["bytes_out"] == 9843
    assert tcp_row["bytes_in"] == 1420
    assert tcp_row["remote_hostname"] is None  # no hostname resolution yet

    assert conn.execute("SELECT COUNT(*) FROM connections_raw").fetchone()[0] == 0


def test_second_poll_updates_persisted_session_and_closes_vanished_one(tmp_path):
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)

    db.record_diff(conn, poller.poll(_load_fixture("pfctl_state_poll_1.txt")), now=NOW1)
    db.record_diff(conn, poller.poll(_load_fixture("pfctl_state_poll_2.txt")), now=NOW2)

    live_rows = {
        r["remote_ip"]: r
        for r in conn.execute("SELECT * FROM live_sessions").fetchall()
    }
    # 8.8.8.8 closed and must be gone from live_sessions.
    assert set(live_rows) == {"93.184.216.34", "151.101.1.140"}

    updated = live_rows["93.184.216.34"]
    assert updated["first_seen"] == NOW1 - 12  # preserved across the update
    assert updated["last_seen"] == NOW2
    assert updated["bytes_out"] == 25000
    assert updated["bytes_in"] == 3100

    opened = live_rows["151.101.1.140"]
    assert opened["first_seen"] == NOW2 - 1  # age was 00:00:01 at poll 2
    assert opened["local_ip"] == "192.168.1.71"

    raw_rows = conn.execute("SELECT * FROM connections_raw").fetchall()
    assert len(raw_rows) == 1
    closed = raw_rows[0]
    assert closed["remote_ip"] == "8.8.8.8"
    assert closed["started_at"] == NOW1 - 2  # age was 00:00:02 at poll 1
    assert closed["ended_at"] == NOW1  # last time it was actually seen alive
    assert closed["duration_s"] == 2
    assert closed["bytes_out"] == 128
    assert closed["bytes_in"] == 256


def test_schema_init_is_idempotent(tmp_path):
    conn = db.connect(str(tmp_path / "flows.db"))
    db.init_schema(conn)
    db.init_schema(conn)  # must not raise on a second call
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "live_sessions",
        "connections_raw",
        "rollup_hourly",
        "rollup_daily",
        "ip_hostname_cache",
        "local_host_identity",
        "rollup_state",
    } <= tables

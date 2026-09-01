import os

import db
from dns_sniffer import QueryEvent
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


def test_load_live_sessions_as_snapshots_round_trips_for_seeding(tmp_path):
    # Regression test companion to pf_state_poller's seed() -- confirms
    # what actually gets persisted to live_sessions round-trips back into
    # StateSnapshot form correctly (right key, right cumulative counters)
    # so seed() can hand it straight back to PfStatePoller at daemon
    # restart.
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)
    diff = poller.poll(_load_fixture("pfctl_state_poll_1.txt"))
    db.record_diff(conn, diff, now=NOW1)

    snapshots = db.load_live_sessions_as_snapshots(conn)

    assert len(snapshots) == len(diff.opened)
    by_key = {s.key: s for s in snapshots}
    for opened in diff.opened:
        loaded = by_key[opened.key]
        assert loaded.bytes_in == opened.bytes_in
        assert loaded.bytes_out == opened.bytes_out
        assert loaded.pkts_in == opened.pkts_in
        assert loaded.pkts_out == opened.pkts_out


def test_load_live_sessions_as_snapshots_round_trips_peer_is_local(tmp_path):
    # Both values must round-trip correctly -- a restart that silently
    # defaulted every seeded session to peer_is_local=False would make
    # record_diff()'s resolver short-circuit misfire for a local-peer
    # session that closes without ever reappearing in a real poll.
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)
    diff = poller.poll(
        "tcp 192.168.1.50:1234 -> 93.184.216.34:443       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
        "tcp 192.168.1.10:5000 -> 192.168.1.20:22       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
    )
    db.record_diff(conn, diff, now=NOW1)

    snapshots = db.load_live_sessions_as_snapshots(conn)
    by_peer = {s.key.peer_ip: s for s in snapshots}
    assert by_peer["93.184.216.34"].peer_is_local is False
    assert by_peer["192.168.1.20"].peer_is_local is True


def test_connect_creates_missing_parent_directory(tmp_path):
    # Regression test: a fresh install has no reason to have
    # /var/db/gowiththeflow already -- only pytest's tmp_path (already a
    # real directory) or the dev VM's long-since-manually-created
    # /var/db/gowiththeflow ever masked this. sqlite3.connect() doesn't
    # create missing parent directories itself.
    db_path = tmp_path / "nested" / "does" / "not" / "exist" / "flows.db"
    conn = db.connect(str(db_path))
    db.init_schema(conn)
    assert db_path.exists()


def test_opened_sessions_land_in_live_sessions_with_backdated_first_seen(tmp_path):
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)

    diff = poller.poll(_load_fixture("pfctl_state_poll_1.txt"))
    db.record_diff(conn, diff, now=NOW1)

    rows = {
        r["peer_ip"]: r
        for r in conn.execute("SELECT * FROM live_sessions").fetchall()
    }
    assert set(rows) == {"93.184.216.34", "8.8.8.8"}

    tcp_row = rows["93.184.216.34"]
    assert tcp_row["local_ip"] == "192.168.1.50"
    assert tcp_row["first_seen"] == NOW1 - 12  # age was 00:00:12
    assert tcp_row["last_seen"] == NOW1
    assert tcp_row["bytes_out"] == 9843
    assert tcp_row["bytes_in"] == 1420
    assert tcp_row["peer_hostname"] is None  # no hostname resolution yet
    assert tcp_row["peer_is_local"] == 0

    assert conn.execute("SELECT COUNT(*) FROM connections_raw").fetchone()[0] == 0


def test_second_poll_updates_persisted_session_and_closes_vanished_one(tmp_path):
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)

    db.record_diff(conn, poller.poll(_load_fixture("pfctl_state_poll_1.txt")), now=NOW1)
    db.record_diff(conn, poller.poll(_load_fixture("pfctl_state_poll_2.txt")), now=NOW2)

    live_rows = {
        r["peer_ip"]: r
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
    assert closed["peer_ip"] == "8.8.8.8"
    assert closed["started_at"] == NOW1 - 2  # age was 00:00:02 at poll 1
    assert closed["ended_at"] == NOW1  # last time it was actually seen alive
    assert closed["duration_s"] == 2
    assert closed["bytes_out"] == 128
    assert closed["bytes_in"] == 256


def test_record_diff_never_resolves_hostname_for_a_local_peer(tmp_path):
    # A local peer's IP would never resolve to anything via DNS/SNI/
    # hostcache anyway, but the short-circuit in record_diff()'s _resolve
    # must skip calling the resolver entirely for peer_is_local=True
    # snapshots, rather than relying on it happening to return nothing --
    # confirmed here by passing a resolver that fails loudly if called.
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)
    diff = poller.poll(
        "tcp 192.168.1.10:1234 -> 192.168.1.20:445       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
    )

    def _resolver_that_must_not_be_called(snap):
        raise AssertionError("resolve_hostname must not be called for a local peer")

    db.record_diff(conn, diff, now=NOW1, resolve_hostname=_resolver_that_must_not_be_called)

    row = conn.execute("SELECT * FROM live_sessions").fetchone()
    assert row["peer_hostname"] is None
    assert row["hostname_source"] is None
    assert row["category"] == "Internal"


def test_last_activity_does_not_advance_when_nothing_actually_changed(tmp_path):
    # last_seen bumps on every poll a session is still present, regardless
    # of real traffic -- last_activity must only bump when bytes/state
    # actually differ from the previous poll, so it stays a genuine "last
    # real activity" signal distinct from "still in pf's state table."
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)

    idle_state = (
        "tcp 192.168.1.10:1234 -> 192.168.1.20:445       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
    )
    db.record_diff(conn, poller.poll(idle_state), now=NOW1)
    row = conn.execute("SELECT * FROM live_sessions").fetchone()
    assert row["last_activity"] == NOW1

    # Same bytes/state on the second poll -- session is still open (pf
    # still reports it), but nothing actually happened.
    db.record_diff(conn, poller.poll(idle_state), now=NOW2)
    row = conn.execute("SELECT * FROM live_sessions").fetchone()
    assert row["last_seen"] == NOW2
    assert row["last_activity"] == NOW1  # unchanged -- no real activity


def test_last_activity_advances_when_bytes_change(tmp_path):
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)

    db.record_diff(
        conn,
        poller.poll(
            "tcp 192.168.1.10:1234 -> 192.168.1.20:445       ESTABLISHED:ESTABLISHED\n"
            "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
        ),
        now=NOW1,
    )
    db.record_diff(
        conn,
        poller.poll(
            "tcp 192.168.1.10:1234 -> 192.168.1.20:445       ESTABLISHED:ESTABLISHED\n"
            "   age 00:00:06, expires in 100s, 2:2 pkts, 200:200 bytes, rule 1\n"
        ),
        now=NOW2,
    )

    row = conn.execute("SELECT * FROM live_sessions").fetchone()
    assert row["last_seen"] == NOW2
    assert row["last_activity"] == NOW2  # bytes grew -- real activity


def test_last_activity_advances_when_only_state_changes(tmp_path):
    # A connection winding down (e.g. FIN_WAIT_2) with byte counters that
    # happen to be identical between two polls is still real activity --
    # the state transition itself is meaningful, not just byte deltas.
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)

    db.record_diff(
        conn,
        poller.poll(
            "tcp 192.168.1.10:1234 -> 192.168.1.20:445       ESTABLISHED:ESTABLISHED\n"
            "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
        ),
        now=NOW1,
    )
    db.record_diff(
        conn,
        poller.poll(
            "tcp 192.168.1.10:1234 -> 192.168.1.20:445       FIN_WAIT_2:FIN_WAIT_2\n"
            "   age 00:00:06, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
        ),
        now=NOW2,
    )

    row = conn.execute("SELECT * FROM live_sessions").fetchone()
    assert row["last_seen"] == NOW2
    assert row["last_activity"] == NOW2  # state changed even though bytes didn't


def test_init_schema_migrates_last_activity_column_backfilled_from_last_seen(tmp_path):
    # Simulates a pre-1.2.2 install: create live_sessions by hand without
    # last_activity, insert a row with a real last_seen, then confirm
    # init_schema()'s ALTER TABLE migration adds the column AND backfills
    # it from last_seen -- not just from the placeholder 0/1970 default,
    # which would show as a nonsensical timestamp in the UI.
    conn = db.connect(str(tmp_path / "flows.db"))
    conn.execute(
        """
        CREATE TABLE live_sessions (
          id INTEGER PRIMARY KEY,
          proto TEXT NOT NULL,
          local_ip TEXT NOT NULL, local_port INTEGER NOT NULL,
          peer_ip TEXT NOT NULL, peer_port INTEGER NOT NULL,
          peer_is_local INTEGER NOT NULL DEFAULT 0,
          peer_hostname TEXT, hostname_source TEXT, category TEXT, state TEXT,
          first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
          bytes_in INTEGER NOT NULL DEFAULT 0, bytes_out INTEGER NOT NULL DEFAULT 0,
          pkts_in INTEGER NOT NULL DEFAULT 0, pkts_out INTEGER NOT NULL DEFAULT 0,
          last_checkpoint_at INTEGER NOT NULL DEFAULT 0,
          baseline_bytes_in INTEGER NOT NULL DEFAULT 0, baseline_bytes_out INTEGER NOT NULL DEFAULT 0,
          baseline_pkts_in INTEGER NOT NULL DEFAULT 0, baseline_pkts_out INTEGER NOT NULL DEFAULT 0,
          UNIQUE(proto, local_ip, local_port, peer_ip, peer_port)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO live_sessions
            (proto, local_ip, local_port, peer_ip, peer_port, first_seen, last_seen)
        VALUES ('tcp', '192.168.1.10', 1234, '1.2.3.4', 443, 500, 12345)
        """
    )
    conn.commit()

    db.init_schema(conn)  # must not raise, and must add + backfill the column

    row = conn.execute("SELECT last_seen, last_activity FROM live_sessions").fetchone()
    assert row["last_activity"] == row["last_seen"] == 12345


def test_init_schema_migrates_category_column_onto_a_pre_existing_install(tmp_path):
    # Simulates an install from before "category" existed: create the
    # tables by hand without it, then confirm init_schema's ALTER TABLE
    # migration adds it rather than relying on CREATE TABLE IF NOT
    # EXISTS, which is a no-op against tables that already exist. Uses the
    # pre-unification column names deliberately -- this test is only
    # about the ALTER TABLE mechanism, unrelated to the local/peer rename.
    conn = db.connect(str(tmp_path / "flows.db"))
    conn.execute(
        """
        CREATE TABLE live_sessions (
          id INTEGER PRIMARY KEY,
          proto TEXT NOT NULL,
          local_ip TEXT NOT NULL, local_port INTEGER NOT NULL,
          remote_ip TEXT NOT NULL, remote_port INTEGER NOT NULL,
          remote_hostname TEXT, hostname_source TEXT,
          first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
          bytes_in INTEGER NOT NULL DEFAULT 0, bytes_out INTEGER NOT NULL DEFAULT 0,
          pkts_in INTEGER NOT NULL DEFAULT 0, pkts_out INTEGER NOT NULL DEFAULT 0,
          last_checkpoint_at INTEGER NOT NULL DEFAULT 0,
          baseline_bytes_in INTEGER NOT NULL DEFAULT 0, baseline_bytes_out INTEGER NOT NULL DEFAULT 0,
          baseline_pkts_in INTEGER NOT NULL DEFAULT 0, baseline_pkts_out INTEGER NOT NULL DEFAULT 0,
          UNIQUE(proto, local_ip, local_port, remote_ip, remote_port)
        )
        """
    )
    conn.commit()

    db.init_schema(conn)  # must not raise, and must add the missing column

    columns = {r["name"] for r in conn.execute("PRAGMA table_info(live_sessions)")}
    assert "category" in columns

    # And the column is actually usable, not just present.
    conn.execute(
        """
        INSERT INTO live_sessions
            (proto, local_ip, local_port, remote_ip, remote_port, category, first_seen, last_seen)
        VALUES ('tcp', '192.168.1.10', 1, '1.2.3.4', 443, 'Shopping', 0, 0)
        """
    )
    conn.commit()
    row = conn.execute("SELECT category FROM live_sessions").fetchone()
    assert row["category"] == "Shopping"


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
    assert "internal_live_sessions" not in tables
    assert "internal_connections_raw" not in tables
    assert "internal_rollup_hourly" not in tables
    assert "internal_rollup_daily" not in tables


def _query_event(**overrides):
    fields = dict(
        local_ip="192.168.1.50", query_name="example.com", query_type="A",
        rcode="NOERROR", answers="A:93.184.216.34", seen_at=NOW1,
    )
    fields.update(overrides)
    return QueryEvent(**fields)


def test_record_dns_query_event_fresh_insert(tmp_path):
    conn = _fresh_conn(tmp_path)
    db.record_dns_query_event(conn, _query_event())
    row = conn.execute("SELECT * FROM dns_query_log").fetchone()
    assert row["local_ip"] == "192.168.1.50"
    assert row["query_name"] == "example.com"
    assert row["query_type"] == "A"
    assert row["rcode"] == "NOERROR"
    assert row["answers"] == "A:93.184.216.34"
    assert row["count"] == 1
    assert row["first_seen"] == NOW1
    assert row["last_seen"] == NOW1


def test_record_dns_query_event_same_bucket_repeat_increments_count(tmp_path):
    conn = _fresh_conn(tmp_path)
    db.record_dns_query_event(conn, _query_event(seen_at=NOW1))
    # Still within the same (default 1-hour) bucket, and a different
    # result this time (an NXDOMAIN where it used to succeed, say) --
    # the upsert should bump count and refresh the mutable fields in
    # place, not touch first_seen.
    db.record_dns_query_event(conn, _query_event(
        seen_at=NOW2, rcode="NXDOMAIN", answers=None,
    ))
    rows = conn.execute("SELECT * FROM dns_query_log").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["count"] == 2
    assert row["rcode"] == "NXDOMAIN"
    assert row["answers"] is None
    assert row["first_seen"] == NOW1
    assert row["last_seen"] == NOW2


def test_record_dns_query_event_different_bucket_is_a_separate_row(tmp_path):
    conn = _fresh_conn(tmp_path)
    db.record_dns_query_event(conn, _query_event(seen_at=NOW1), bucket_size_s=3600)
    db.record_dns_query_event(conn, _query_event(seen_at=NOW1 + 3600), bucket_size_s=3600)
    rows = conn.execute("SELECT count FROM dns_query_log ORDER BY bucket_start").fetchall()
    assert [r["count"] for r in rows] == [1, 1]


def test_record_dns_query_event_different_query_name_or_type_is_a_separate_row(tmp_path):
    conn = _fresh_conn(tmp_path)
    db.record_dns_query_event(conn, _query_event(query_name="example.com", query_type="A"))
    db.record_dns_query_event(conn, _query_event(query_name="example.com", query_type="AAAA"))
    db.record_dns_query_event(conn, _query_event(query_name="other.com", query_type="A"))
    rows = conn.execute("SELECT query_name, query_type FROM dns_query_log").fetchall()
    assert len(rows) == 3


def test_record_dns_query_event_different_local_host_is_a_separate_row(tmp_path):
    conn = _fresh_conn(tmp_path)
    db.record_dns_query_event(conn, _query_event(local_ip="192.168.1.50"))
    db.record_dns_query_event(conn, _query_event(local_ip="192.168.1.51"))
    rows = conn.execute("SELECT local_ip FROM dns_query_log").fetchall()
    assert {r["local_ip"] for r in rows} == {"192.168.1.50", "192.168.1.51"}

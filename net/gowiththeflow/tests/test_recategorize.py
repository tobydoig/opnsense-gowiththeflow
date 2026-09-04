import argparse

import db
import manual_categories
import recategorize


def _fresh_conn(tmp_path, monkeypatch):
    path = str(tmp_path / "flows.db")
    monkeypatch.setattr(recategorize, "DB_PATH", path)
    conn = db.connect(path)
    db.init_schema(conn)
    return conn


def _insert_connections_raw(conn, peer_hostname, category, bytes_total=1000):
    conn.execute(
        """
        INSERT INTO connections_raw
            (proto, local_ip, peer_ip, peer_port, peer_is_local, peer_hostname,
             hostname_source, category, dpi_protocol, state, started_at, ended_at,
             duration_s, bytes_in, bytes_out, pkts_in, pkts_out)
        VALUES ('tcp', '10.0.0.5', '1.2.3.4', 443, 0, ?, 'dns', ?, NULL, 'closed', 0, 100, 100, ?, 0, 1, 1)
        """,
        (peer_hostname, category, bytes_total),
    )
    conn.commit()


def _insert_rollup_hourly(conn, bucket_start, peer_hostname, category, bytes_total=1000, peer_ip=None):
    # peer_ip is part of rollup_hourly's own primary key, so each
    # distinct hostname in a test needs a distinct peer_ip -- derived
    # from the hostname itself by default so callers don't need to
    # invent one just to avoid a collision.
    peer_ip = peer_ip or f"10.1.{abs(hash(peer_hostname)) % 250}.{abs(hash(peer_hostname)) % 250}"
    conn.execute(
        """
        INSERT INTO rollup_hourly
            (bucket_start, proto, local_ip, peer_ip, peer_is_local, peer_hostname,
             hostname_source, category, dpi_protocol, bytes_in, bytes_out, pkts_in, pkts_out, conn_count)
        VALUES (?, 'tcp', '10.0.0.5', ?, 0, ?, 'dns', ?, NULL, ?, 0, 1, 1, 1)
        """,
        (bucket_start, peer_ip, peer_hostname, category, bytes_total),
    )
    conn.commit()


def _no_op_matcher(monkeypatch):
    # Empty automated rules -- tests drive categorization purely through
    # manual_categories.OVERRIDES, which is what categories.resolve_category()
    # checks first anyway.
    import categories

    monkeypatch.setattr(recategorize, "_build_matcher", lambda: categories.CategoryMatcher({}))


def test_list_uncategorized_excludes_hosts_with_a_category(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path, monkeypatch)
    now = 2_000_000_000
    _insert_rollup_hourly(conn, now, "uncategorized.example.com", None, bytes_total=500)
    _insert_rollup_hourly(conn, now, "already-categorized.example.com", "Shopping", bytes_total=999999)

    result = recategorize.cmd_list_uncategorized(argparse.Namespace(days=1, limit=500))

    hostnames = [h["hostname"] for h in result["hosts"]]
    assert hostnames == ["uncategorized.example.com"]


def test_list_uncategorized_orders_by_total_bytes_descending(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path, monkeypatch)
    now = 2_000_000_000
    _insert_rollup_hourly(conn, now, "small.example.com", None, bytes_total=100)
    _insert_rollup_hourly(conn, now, "big.example.com", None, bytes_total=100000)

    result = recategorize.cmd_list_uncategorized(argparse.Namespace(days=1, limit=500))

    assert [h["hostname"] for h in result["hosts"]] == ["big.example.com", "small.example.com"]


def test_apply_updates_a_stale_category_across_every_table(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path, monkeypatch)
    _no_op_matcher(monkeypatch)
    monkeypatch.setitem(manual_categories.OVERRIDES, "reclassified.example.com", "AI")
    _insert_connections_raw(conn, "reclassified.example.com", "Cloud/Productivity")
    _insert_rollup_hourly(conn, 2_000_000_000, "reclassified.example.com", "Cloud/Productivity")

    result = recategorize.cmd_apply(argparse.Namespace(dry_run=False))

    assert result["hostnames_changed"] == 1
    assert result["rows_updated"] == 2
    assert conn.execute(
        "SELECT category FROM connections_raw WHERE peer_hostname = ?", ("reclassified.example.com",)
    ).fetchone()["category"] == "AI"
    assert conn.execute(
        "SELECT category FROM rollup_hourly WHERE peer_hostname = ?", ("reclassified.example.com",)
    ).fetchone()["category"] == "AI"


def test_apply_leaves_already_correct_categories_untouched(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path, monkeypatch)
    _no_op_matcher(monkeypatch)
    monkeypatch.setitem(manual_categories.OVERRIDES, "already-right.example.com", "Shopping")
    _insert_connections_raw(conn, "already-right.example.com", "Shopping")

    result = recategorize.cmd_apply(argparse.Namespace(dry_run=False))

    assert result["hostnames_changed"] == 0
    assert result["rows_updated"] == 0


def test_apply_dry_run_reports_changes_but_does_not_commit_them(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path, monkeypatch)
    _no_op_matcher(monkeypatch)
    monkeypatch.setitem(manual_categories.OVERRIDES, "preview-only.example.com", "AI")
    _insert_connections_raw(conn, "preview-only.example.com", "Cloud/Productivity")

    result = recategorize.cmd_apply(argparse.Namespace(dry_run=True))

    assert result["dry_run"] is True
    assert result["hostnames_changed"] == 1
    # A fresh connection proves this was actually rolled back, not just
    # left uncommitted on the same in-memory connection.
    fresh = db.connect(recategorize.DB_PATH)
    assert fresh.execute(
        "SELECT category FROM connections_raw WHERE peer_hostname = ?", ("preview-only.example.com",)
    ).fetchone()["category"] == "Cloud/Productivity"


def test_apply_moving_to_and_from_null_counts_as_a_change(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path, monkeypatch)
    _no_op_matcher(monkeypatch)
    # No override and an empty automated matcher -- resolves to None,
    # a real transition away from a previously-set (now-stale) category.
    _insert_connections_raw(conn, "no-longer-categorized.example.com", "Shopping")

    result = recategorize.cmd_apply(argparse.Namespace(dry_run=False))

    assert result["hostnames_changed"] == 1
    assert conn.execute(
        "SELECT category FROM connections_raw WHERE peer_hostname = ?",
        ("no-longer-categorized.example.com",),
    ).fetchone()["category"] is None


def test_main_list_uncategorized_prints_json(tmp_path, monkeypatch, capsys):
    _fresh_conn(tmp_path, monkeypatch)
    exit_code = recategorize.main(["list-uncategorized"])
    assert exit_code == 0
    import json

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"


def test_main_apply_prints_json(tmp_path, monkeypatch, capsys):
    _fresh_conn(tmp_path, monkeypatch)
    _no_op_matcher(monkeypatch)
    exit_code = recategorize.main(["apply", "--dry-run"])
    assert exit_code == 0
    import json

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert output["dry_run"] is True

import json

import db
import reservation_gate

NOW = 1_000_000


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fresh_conn(tmp_path):
    conn = db.connect(str(tmp_path / "flows.db"))
    db.init_schema(conn)
    return conn


def _identity_row(conn, ip, mac, updated_at):
    conn.execute(
        "INSERT INTO local_host_identity (mac, ip, hostname, source, updated_at) VALUES (?, ?, NULL, 'dhcp_lease', ?)",
        (mac, ip, updated_at),
    )
    conn.commit()


# --- build_protected_dests ---------------------------------------------

def test_build_protected_dests_dedupes_and_sorts():
    assert reservation_gate.build_protected_dests(
        ["10.0.0.0/24", "192.168.1.0/24", "10.0.0.0/24"]
    ) == ["10.0.0.0/24", "192.168.1.0/24"]


def test_build_protected_dests_empty_local_subnets_is_not_an_error():
    assert reservation_gate.build_protected_dests([]) == []


# --- choose_pin_target ---------------------------------------------------

def test_choose_pin_target_single_candidate_mac(tmp_path):
    conn = _fresh_conn(tmp_path)
    _identity_row(conn, "10.0.0.20", "aa:bb:cc:dd:ee:01", NOW)
    rows = conn.execute("SELECT ip, mac, updated_at FROM local_host_identity").fetchall()
    target = reservation_gate.choose_pin_target([("aa:bb:cc:dd:ee:01", "10.0.0.20")], rows)
    assert target == {"10.0.0.20": "aa:bb:cc:dd:ee:01"}


def test_choose_pin_target_multi_mac_one_observed(tmp_path):
    conn = _fresh_conn(tmp_path)
    _identity_row(conn, "10.0.0.20", "aa:bb:cc:dd:ee:01", NOW)  # only the wifi mac ever seen
    rows = conn.execute("SELECT ip, mac, updated_at FROM local_host_identity").fetchall()
    reservations = [("aa:bb:cc:dd:ee:01", "10.0.0.20"), ("aa:bb:cc:dd:ee:02", "10.0.0.20")]
    assert reservation_gate.choose_pin_target(reservations, rows) == {"10.0.0.20": "aa:bb:cc:dd:ee:01"}


def test_choose_pin_target_multi_mac_none_observed_is_skipped(tmp_path):
    conn = _fresh_conn(tmp_path)
    # Neither candidate mac has ever been seen at this ip -- a real,
    # accepted gap (see the module's own docstring): the ip stays
    # allowed on the pf table regardless, just not ARP-pinned yet.
    rows = conn.execute("SELECT ip, mac, updated_at FROM local_host_identity").fetchall()
    reservations = [("aa:bb:cc:dd:ee:01", "10.0.0.20"), ("aa:bb:cc:dd:ee:02", "10.0.0.20")]
    assert reservation_gate.choose_pin_target(reservations, rows) == {}


def test_choose_pin_target_multi_mac_multiple_observed_most_recent_wins(tmp_path):
    conn = _fresh_conn(tmp_path)
    _identity_row(conn, "10.0.0.20", "aa:bb:cc:dd:ee:01", NOW)
    _identity_row(conn, "10.0.0.20", "aa:bb:cc:dd:ee:02", NOW + 100)  # more recently seen at this ip
    rows = conn.execute("SELECT ip, mac, updated_at FROM local_host_identity").fetchall()
    reservations = [("aa:bb:cc:dd:ee:01", "10.0.0.20"), ("aa:bb:cc:dd:ee:02", "10.0.0.20")]
    assert reservation_gate.choose_pin_target(reservations, rows) == {"10.0.0.20": "aa:bb:cc:dd:ee:02"}


# --- diff_arp_pins ---------------------------------------------------------

def test_diff_arp_pins_fresh_apply(tmp_path):
    conn = _fresh_conn(tmp_path)
    to_add, to_remove = reservation_gate.diff_arp_pins(conn, {"10.0.0.20": "aa:bb:cc:dd:ee:01"})
    assert to_add == {"10.0.0.20": "aa:bb:cc:dd:ee:01"}
    assert to_remove == {}


def test_diff_arp_pins_no_op_when_target_matches(tmp_path):
    conn = _fresh_conn(tmp_path)
    conn.execute("INSERT INTO arp_pins (ip, mac, applied_at) VALUES ('10.0.0.20', 'aa:bb:cc:dd:ee:01', ?)", (NOW,))
    conn.commit()
    to_add, to_remove = reservation_gate.diff_arp_pins(conn, {"10.0.0.20": "aa:bb:cc:dd:ee:01"})
    assert to_add == {}
    assert to_remove == {}


def test_diff_arp_pins_removed_reservation(tmp_path):
    conn = _fresh_conn(tmp_path)
    conn.execute("INSERT INTO arp_pins (ip, mac, applied_at) VALUES ('10.0.0.20', 'aa:bb:cc:dd:ee:01', ?)", (NOW,))
    conn.commit()
    to_add, to_remove = reservation_gate.diff_arp_pins(conn, {})
    assert to_add == {}
    assert to_remove == {"10.0.0.20": "aa:bb:cc:dd:ee:01"}


def test_diff_arp_pins_changed_target_mac_appears_in_both(tmp_path):
    conn = _fresh_conn(tmp_path)
    conn.execute("INSERT INTO arp_pins (ip, mac, applied_at) VALUES ('10.0.0.20', 'aa:bb:cc:dd:ee:01', ?)", (NOW,))
    conn.commit()
    to_add, to_remove = reservation_gate.diff_arp_pins(conn, {"10.0.0.20": "aa:bb:cc:dd:ee:02"})
    assert to_add == {"10.0.0.20": "aa:bb:cc:dd:ee:02"}
    assert to_remove == {"10.0.0.20": "aa:bb:cc:dd:ee:01"}


# --- apply_arp_pins (subprocess mocked) -------------------------------

def test_apply_arp_pins_adds_and_removes(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    conn.execute("INSERT INTO arp_pins (ip, mac, applied_at) VALUES ('10.0.0.99', 'aa:bb:cc:dd:ee:99', ?)", (NOW,))
    conn.commit()

    calls = []
    kill_calls = []
    monkeypatch.setattr(reservation_gate.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    monkeypatch.setattr(reservation_gate.blocklist, "kill_states", lambda ip: kill_calls.append(ip))

    warnings = reservation_gate.apply_arp_pins(
        conn, to_add={"10.0.0.20": "aa:bb:cc:dd:ee:01"}, to_remove={"10.0.0.99": "aa:bb:cc:dd:ee:99"}, now=NOW,
    )

    assert warnings == []
    assert ["/usr/sbin/arp", "-d", "10.0.0.99"] in calls
    assert ["/usr/sbin/arp", "-S", "10.0.0.20", "aa:bb:cc:dd:ee:01"] in calls
    assert kill_calls == ["10.0.0.99"]

    rows = {r["ip"]: r["mac"] for r in conn.execute("SELECT ip, mac FROM arp_pins")}
    assert rows == {"10.0.0.20": "aa:bb:cc:dd:ee:01"}


def test_apply_arp_pins_surfaces_a_genuine_arp_s_failure(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    monkeypatch.setattr(
        reservation_gate.subprocess, "run",
        lambda args, **kw: _FakeCompletedProcess(returncode=1, stderr="permission denied"),
    )
    warnings = reservation_gate.apply_arp_pins(conn, to_add={"10.0.0.20": "aa:bb:cc:dd:ee:01"}, to_remove={}, now=NOW)
    assert len(warnings) == 1
    assert "10.0.0.20" in warnings[0]


def test_apply_arp_pins_does_not_record_a_pin_that_actually_failed(tmp_path, monkeypatch):
    # Found live on a real reboot: the daemon's own startup-time
    # reconcile can run arp -S before the interface is fully up, so the
    # command itself fails at that exact moment even though the
    # identical command succeeds moments later. If arp_pins recorded
    # success regardless, the next reconcile pass would never retry it
    # -- diff_arp_pins() would already believe it was correctly pinned.
    conn = _fresh_conn(tmp_path)
    monkeypatch.setattr(
        reservation_gate.subprocess, "run",
        lambda args, **kw: _FakeCompletedProcess(returncode=1, stderr="permission denied"),
    )
    reservation_gate.apply_arp_pins(conn, to_add={"10.0.0.20": "aa:bb:cc:dd:ee:01"}, to_remove={}, now=NOW)
    assert conn.execute("SELECT * FROM arp_pins").fetchall() == []
    # And since the DB still shows nothing pinned, the next diff would
    # correctly retry it rather than treating it as already handled.
    to_add, _ = reservation_gate.diff_arp_pins(conn, {"10.0.0.20": "aa:bb:cc:dd:ee:01"})
    assert to_add == {"10.0.0.20": "aa:bb:cc:dd:ee:01"}


def test_apply_arp_pins_does_not_clear_tracking_when_removal_actually_fails(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    conn.execute("INSERT INTO arp_pins (ip, mac, applied_at) VALUES ('10.0.0.20', 'aa:bb:cc:dd:ee:01', ?)", (NOW,))
    conn.commit()
    monkeypatch.setattr(
        reservation_gate.subprocess, "run",
        lambda args, **kw: _FakeCompletedProcess(returncode=2, stderr="some genuine failure"),
    )
    monkeypatch.setattr(reservation_gate.blocklist, "kill_states", lambda ip: None)
    warnings = reservation_gate.apply_arp_pins(conn, to_add={}, to_remove={"10.0.0.20": "aa:bb:cc:dd:ee:01"}, now=NOW)
    assert len(warnings) == 1
    rows = {r["ip"]: r["mac"] for r in conn.execute("SELECT ip, mac FROM arp_pins")}
    assert rows == {"10.0.0.20": "aa:bb:cc:dd:ee:01"}  # still tracked -- will be retried next cycle


def test_reset_pin_tracking_forgets_bookkeeping_without_touching_the_arp_table(tmp_path, monkeypatch):
    # arp_pins (SQLite) survives a daemon restart/reboot; the kernel's
    # own ARP cache never does -- reset_pin_tracking() must clear only
    # the former, and must not shell out to `arp` at all (there's
    # nothing real to undo, the kernel table is already empty).
    conn = _fresh_conn(tmp_path)
    conn.execute("INSERT INTO arp_pins (ip, mac, applied_at) VALUES ('10.0.0.20', 'aa:bb:cc:dd:ee:01', ?)", (NOW,))
    conn.commit()
    calls = []
    monkeypatch.setattr(reservation_gate.subprocess, "run", lambda args, **kw: calls.append(args))

    reservation_gate.reset_pin_tracking(conn)

    assert conn.execute("SELECT * FROM arp_pins").fetchall() == []
    assert calls == []


def test_reset_pin_tracking_then_reconcile_reapplies_a_still_valid_pin(tmp_path, monkeypatch):
    # The actual bug this fixes, end to end: a reservation that never
    # changed across a reboot must still get a fresh arp -S, not be
    # silently skipped because arp_pins still remembers it as pinned.
    conn = _fresh_conn(tmp_path)
    conn.execute("INSERT INTO arp_pins (ip, mac, applied_at) VALUES ('10.0.0.20', 'aa:bb:cc:dd:ee:01', ?)", (NOW,))
    conn.commit()
    _identity_row(conn, "10.0.0.20", "aa:bb:cc:dd:ee:01", NOW)
    monkeypatch.setattr(reservation_gate, "fetch_reservations", lambda: [("aa:bb:cc:dd:ee:01", "10.0.0.20")])
    calls = []
    monkeypatch.setattr(reservation_gate.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    monkeypatch.setattr(reservation_gate, "ALLOWED_TABLE_FILE", str(tmp_path / "allowed.tbl"))
    monkeypatch.setattr(reservation_gate, "PROTECTED_TABLE_FILE", str(tmp_path / "protected.tbl"))

    reservation_gate.reset_pin_tracking(conn)
    result = reservation_gate.reconcile(
        conn, NOW, {"enable_reservation_gate": True, "enable_arp_pinning": True, "local_subnets": []},
    )

    assert result["pins_added"] == 1
    assert ["/usr/sbin/arp", "-S", "10.0.0.20", "aa:bb:cc:dd:ee:01"] in calls


def test_purge_all_removes_every_pin(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    conn.execute("INSERT INTO arp_pins (ip, mac, applied_at) VALUES ('10.0.0.20', 'aa:bb:cc:dd:ee:01', ?)", (NOW,))
    conn.commit()
    calls = []
    monkeypatch.setattr(reservation_gate.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    reservation_gate.purge_all(conn)
    assert calls == [["/usr/sbin/arp", "-d", "10.0.0.20"]]
    assert conn.execute("SELECT * FROM arp_pins").fetchall() == []


# --- fetch_reservations (subprocess mocked) -----------------------------

def test_fetch_reservations_parses_valid_json(monkeypatch):
    monkeypatch.setattr(
        reservation_gate.subprocess, "run",
        lambda args, **kw: _FakeCompletedProcess(stdout=json.dumps([{"mac": "AA:BB", "ip": "10.0.0.20"}])),
    )
    assert reservation_gate.fetch_reservations() == [("AA:BB", "10.0.0.20")]


def test_fetch_reservations_returns_none_on_nonzero_exit(monkeypatch):
    def _raise(*a, **k):
        import subprocess as real_subprocess
        raise real_subprocess.CalledProcessError(1, ["php"])
    monkeypatch.setattr(reservation_gate.subprocess, "run", _raise)
    assert reservation_gate.fetch_reservations() is None


def test_fetch_reservations_returns_none_on_bad_json(monkeypatch):
    monkeypatch.setattr(reservation_gate.subprocess, "run", lambda args, **kw: _FakeCompletedProcess(stdout="not json"))
    assert reservation_gate.fetch_reservations() is None


def test_fetch_reservations_returns_none_on_timeout(monkeypatch):
    def _raise(*a, **k):
        import subprocess as real_subprocess
        raise real_subprocess.TimeoutExpired(cmd=["php"], timeout=15)
    monkeypatch.setattr(reservation_gate.subprocess, "run", _raise)
    assert reservation_gate.fetch_reservations() is None


# --- reconcile (top-level orchestrator) ---------------------------------

def test_reconcile_gate_off_empties_table_and_purges_pins(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    conn.execute("INSERT INTO arp_pins (ip, mac, applied_at) VALUES ('10.0.0.20', 'aa:bb:cc:dd:ee:01', ?)", (NOW,))
    conn.commit()
    calls = []
    monkeypatch.setattr(reservation_gate.blocklist.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    monkeypatch.setattr(reservation_gate.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    monkeypatch.setattr(reservation_gate, "ALLOWED_TABLE_FILE", str(tmp_path / "allowed.tbl"))

    result = reservation_gate.reconcile(conn, NOW, {"enable_reservation_gate": False})

    assert result == {"status": "ok", "enabled": False, "allowed_count": 0, "pinned_count": 0}
    assert conn.execute("SELECT * FROM arp_pins").fetchall() == []
    assert any("gowiththeflow_allowed" in c for c in calls)


def test_reconcile_fetch_failure_touches_nothing(tmp_path, monkeypatch):
    # The critical safety-net test: a failed Dnsmasq read must NEVER be
    # allowed to look like "zero reservations" -- no pfctl/arp calls at
    # all, no arp_pins changes.
    conn = _fresh_conn(tmp_path)
    conn.execute("INSERT INTO arp_pins (ip, mac, applied_at) VALUES ('10.0.0.20', 'aa:bb:cc:dd:ee:01', ?)", (NOW,))
    conn.commit()
    calls = []
    monkeypatch.setattr(reservation_gate, "fetch_reservations", lambda: None)
    monkeypatch.setattr(reservation_gate.blocklist.subprocess, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(reservation_gate.subprocess, "run", lambda args, **kw: calls.append(args))

    result = reservation_gate.reconcile(conn, NOW, {"enable_reservation_gate": True, "enable_arp_pinning": True})

    assert result["status"] == "error"
    assert calls == []
    rows = {r["ip"]: r["mac"] for r in conn.execute("SELECT ip, mac FROM arp_pins")}
    assert rows == {"10.0.0.20": "aa:bb:cc:dd:ee:01"}  # untouched -- still exactly what it was before


def test_reconcile_gate_on_arp_pinning_off_syncs_table_and_purges_pins(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    conn.execute("INSERT INTO arp_pins (ip, mac, applied_at) VALUES ('10.0.0.20', 'aa:bb:cc:dd:ee:01', ?)", (NOW,))
    conn.commit()
    monkeypatch.setattr(reservation_gate, "fetch_reservations", lambda: [("aa:bb:cc:dd:ee:01", "10.0.0.20")])
    # blocklist.subprocess and reservation_gate.subprocess are the same
    # module object (both just `import subprocess`) -- one monkeypatch
    # covers every subprocess.run call from either module.
    calls = []
    monkeypatch.setattr(reservation_gate.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    monkeypatch.setattr(reservation_gate, "ALLOWED_TABLE_FILE", str(tmp_path / "allowed.tbl"))
    monkeypatch.setattr(reservation_gate, "PROTECTED_TABLE_FILE", str(tmp_path / "protected.tbl"))

    result = reservation_gate.reconcile(
        conn, NOW, {"enable_reservation_gate": True, "enable_arp_pinning": False, "local_subnets": []},
    )

    assert result["status"] == "ok"
    assert result["allowed_count"] == 1
    assert result["pinned_count"] == 0
    assert ["/usr/sbin/arp", "-d", "10.0.0.20"] in calls  # the stale pin from before was purged
    assert not any(c[0] == "/usr/sbin/arp" and c[1] == "-S" for c in calls)  # arp-pinning is off
    assert conn.execute("SELECT * FROM arp_pins").fetchall() == []


def test_reconcile_full_happy_path_with_arp_pinning(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    _identity_row(conn, "10.0.0.20", "aa:bb:cc:dd:ee:01", NOW)
    monkeypatch.setattr(reservation_gate, "fetch_reservations", lambda: [("aa:bb:cc:dd:ee:01", "10.0.0.20")])
    monkeypatch.setattr(reservation_gate.blocklist.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    monkeypatch.setattr(reservation_gate.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    monkeypatch.setattr(reservation_gate, "ALLOWED_TABLE_FILE", str(tmp_path / "allowed.tbl"))
    monkeypatch.setattr(reservation_gate, "PROTECTED_TABLE_FILE", str(tmp_path / "protected.tbl"))

    result = reservation_gate.reconcile(
        conn, NOW, {"enable_reservation_gate": True, "enable_arp_pinning": True, "local_subnets": ["10.0.0.0/24"]},
    )

    assert result["status"] == "ok"
    assert result["allowed_count"] == 1
    assert result["pinned_count"] == 1
    assert result["pins_added"] == 1
    rows = {r["ip"]: r["mac"] for r in conn.execute("SELECT ip, mac FROM arp_pins")}
    assert rows == {"10.0.0.20": "aa:bb:cc:dd:ee:01"}

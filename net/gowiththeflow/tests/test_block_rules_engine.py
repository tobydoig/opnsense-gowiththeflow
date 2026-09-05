import json
from datetime import datetime

import pytest

import block_rules_engine
import blocklist
import db

NOW_DT = datetime(2026, 9, 7, 21, 0)  # a Monday, 21:00
NOW = int(NOW_DT.timestamp())

ALWAYS_ON_SCHEDULE = json.dumps({"windows": [{"days": ["mon"], "start": "20:00", "end": "23:00"}]})  # covers NOW
GAP_SCHEDULE = json.dumps({"windows": [{"days": ["mon"], "start": "01:00", "end": "02:00"}]})  # does not cover NOW


def _device(ip="10.0.0.5", hostname="kids-tablet", mac=None):
    return {"ip": ip, "hostname": hostname, "mac": mac}


def _fresh_conn(tmp_path):
    conn = db.connect(str(tmp_path / "flows.db"))
    db.init_schema(conn)
    return conn


def _insert_rule(conn, **overrides):
    devices = overrides.pop("devices", [_device()])
    row = {
        "rule_type": "host",
        "name": "Kids devices",
        "devices": json.dumps(devices),
        "domains": None,
        "schedule_json": None,
        "enabled": 1,
        "manual_override_state": None,
        "override_until": None,
        "unbound_description": "gowiththeflow:rule:test",
        "created_at": NOW,
        "created_by": "admin",
        "reason": None,
        "updated_at": NOW,
    }
    row.update(overrides)
    cur = conn.execute(
        """
        INSERT INTO block_rules
            (rule_type, name, devices, domains, schedule_json, enabled,
             manual_override_state, override_until, unbound_description,
             created_at, created_by, reason, updated_at)
        VALUES (:rule_type, :name, :devices, :domains, :schedule_json, :enabled,
                :manual_override_state, :override_until, :unbound_description,
                :created_at, :created_by, :reason, :updated_at)
        """,
        row,
    )
    conn.commit()
    return cur.lastrowid


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --- CRUD -------------------------------------------------------------

def test_create_host_rule_inserts_a_new_row_each_time(tmp_path):
    conn = _fresh_conn(tmp_path)
    id1 = block_rules_engine.create_host_rule(conn, "Rule A", [_device()], "admin", "r1", NOW)
    id2 = block_rules_engine.create_host_rule(conn, "Rule B", [_device(ip="10.0.0.6")], "admin", "r2", NOW + 10)
    assert id1 != id2
    assert len(block_rules_engine.list_rules(conn)) == 2


def test_create_host_rule_stores_multiple_devices(tmp_path):
    conn = _fresh_conn(tmp_path)
    devices = [_device(ip="10.0.0.5", hostname="quest3s"), _device(ip="10.0.0.6", hostname="ps5")]
    rule_id = block_rules_engine.create_host_rule(conn, "Consoles", devices, "admin", None, NOW)
    row = block_rules_engine.get_rule(conn, rule_id)
    assert row["name"] == "Consoles"
    assert block_rules_engine.device_ips(row) == ["10.0.0.5", "10.0.0.6"]


def test_create_domain_rule_allows_multiple_rules_for_one_device(tmp_path):
    conn = _fresh_conn(tmp_path)
    devices = [_device(ip="10.0.0.9", hostname="phone")]
    id1 = block_rules_engine.create_domain_rule(conn, "No YouTube", devices, "youtube.com", None, "admin", None, NOW)
    id2 = block_rules_engine.create_domain_rule(conn, "No Reddit", devices, "reddit.com", None, "admin", None, NOW)
    assert id1 != id2
    assert len(block_rules_engine.list_rules(conn)) == 2


def test_create_domain_rule_sets_a_stable_unbound_description(tmp_path):
    conn = _fresh_conn(tmp_path)
    rule_id = block_rules_engine.create_domain_rule(
        conn, "No YouTube", [_device(ip="10.0.0.9", hostname=None)], "youtube.com", None, None, None, NOW
    )
    row = block_rules_engine.get_rule(conn, rule_id)
    assert row["unbound_description"] == f"gowiththeflow:rule:{rule_id}"


def test_update_rule_changes_name_devices_domains_and_reapplies(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    calls = []
    monkeypatch.setattr(block_rules_engine.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    rule_id = block_rules_engine.create_domain_rule(
        conn, "No YouTube", [_device(ip="10.0.0.9", hostname=None)], "youtube.com", None, None, None, NOW
    )

    new_devices = [_device(ip="10.0.0.9", hostname=None), _device(ip="10.0.0.10", hostname="tablet")]
    ok = block_rules_engine.update_rule(
        conn, rule_id, "No YouTube or TikTok", new_devices, "youtube.com,tiktok.com", None, NOW + 5
    )
    assert ok is True

    row = block_rules_engine.get_rule(conn, rule_id)
    assert row["name"] == "No YouTube or TikTok"
    assert row["domains"] == "youtube.com,tiktok.com"
    assert block_rules_engine.device_ips(row) == ["10.0.0.9", "10.0.0.10"]
    assert len(calls) == 2  # apply_rule ran immediately, once per device


def test_update_rule_returns_false_for_a_missing_rule(tmp_path):
    conn = _fresh_conn(tmp_path)
    assert block_rules_engine.update_rule(conn, 999999, "x", [_device()], "x.com", None, NOW) is False


def test_update_rule_unblocks_a_host_device_dropped_from_the_group(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    monkeypatch.setattr(block_rules_engine, "TABLE_FILE_PATH", str(tmp_path / "blocked_hosts.tbl"))
    monkeypatch.setattr(block_rules_engine.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    devices = [_device(ip="10.0.0.5"), _device(ip="10.0.0.6")]
    rule_id = block_rules_engine.create_host_rule(conn, "Two devices", devices, "admin", None, NOW)
    block_rules_engine.apply_rule(conn, rule_id, NOW)
    assert conn.execute("SELECT 1 FROM blocked_hosts WHERE local_ip = '10.0.0.6'").fetchone() is not None

    ok = block_rules_engine.update_rule(conn, rule_id, "One device", [_device(ip="10.0.0.5")], None, None, NOW + 5)
    assert ok is True

    # The dropped device must actually be unblocked, not just removed from
    # the rule's own device list -- _apply_host_rule() only ever loops a
    # rule's *current* devices, so without an explicit diff against the
    # old device set this device's block would stay stuck forever.
    assert conn.execute("SELECT 1 FROM blocked_hosts WHERE local_ip = '10.0.0.6'").fetchone() is None
    assert conn.execute("SELECT 1 FROM blocked_hosts WHERE local_ip = '10.0.0.5'").fetchone() is not None


def test_update_rule_removes_dnsbl_row_for_a_domain_device_dropped_from_the_group(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    calls = []
    monkeypatch.setattr(block_rules_engine.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    devices = [_device(ip="10.0.0.9"), _device(ip="10.0.0.10")]
    rule_id = block_rules_engine.create_domain_rule(conn, "Two devices", devices, "youtube.com", None, None, None, NOW)
    calls.clear()

    ok = block_rules_engine.update_rule(conn, rule_id, "One device", [_device(ip="10.0.0.9")], "youtube.com", None, NOW + 5)
    assert ok is True

    remove_calls = [c for c in calls if "--action" in c and c[c.index("--action") + 1] == "remove"]
    assert len(remove_calls) == 1
    removed_description = remove_calls[0][remove_calls[0].index("--description") + 1]
    assert removed_description.endswith(":10.0.0.10")


def test_duplicate_rule_copies_everything_appends_copy_and_starts_disabled(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    calls = []
    monkeypatch.setattr(block_rules_engine.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    devices = [_device(ip="10.0.0.9", hostname="phone")]
    original_id = block_rules_engine.create_domain_rule(
        conn, "No YouTube", devices, "youtube.com", ALWAYS_ON_SCHEDULE, "admin", "school night", NOW
    )
    calls.clear()

    new_id = block_rules_engine.duplicate_rule(conn, original_id, NOW + 5)

    assert new_id is not None
    assert new_id != original_id
    copy = block_rules_engine.get_rule(conn, new_id)
    assert copy["name"] == "No YouTube (copy)"
    assert copy["enabled"] == 0
    assert copy["domains"] == "youtube.com"
    assert copy["schedule_json"] == ALWAYS_ON_SCHEDULE
    assert copy["reason"] == "school night"
    assert block_rules_engine.device_ips(copy) == ["10.0.0.9"]
    assert copy["unbound_description"] == f"gowiththeflow:rule:{new_id}"
    assert calls == []  # disabled -- no enforcement side effect from duplicating


def test_duplicate_rule_returns_none_for_a_missing_rule(tmp_path):
    conn = _fresh_conn(tmp_path)
    assert block_rules_engine.duplicate_rule(conn, 999999, NOW) is None


def test_devices_conflicting_with_other_host_rules_flags_an_overlapping_device(tmp_path):
    conn = _fresh_conn(tmp_path)
    block_rules_engine.create_host_rule(conn, "Existing", [_device(ip="10.0.0.5")], None, None, NOW)

    conflicts = block_rules_engine.devices_conflicting_with_other_host_rules(conn, ["10.0.0.5", "10.0.0.99"])

    assert conflicts == {"10.0.0.5": "Existing"}


def test_devices_conflicting_with_other_host_rules_excludes_the_rule_being_edited(tmp_path):
    conn = _fresh_conn(tmp_path)
    rule_id = block_rules_engine.create_host_rule(conn, "Existing", [_device(ip="10.0.0.5")], None, None, NOW)

    conflicts = block_rules_engine.devices_conflicting_with_other_host_rules(
        conn, ["10.0.0.5"], exclude_rule_id=rule_id
    )

    assert conflicts == {}


def test_devices_conflicting_with_other_host_rules_ignores_domain_rules_and_disabled_rules(tmp_path):
    conn = _fresh_conn(tmp_path)
    block_rules_engine.create_domain_rule(
        conn, "Domain rule", [_device(ip="10.0.0.5")], "youtube.com", None, None, None, NOW
    )
    disabled_id = block_rules_engine.create_host_rule(conn, "Disabled", [_device(ip="10.0.0.6")], None, None, NOW)
    block_rules_engine.set_enabled(conn, disabled_id, False, NOW)

    conflicts = block_rules_engine.devices_conflicting_with_other_host_rules(conn, ["10.0.0.5", "10.0.0.6"])

    assert conflicts == {}


def test_set_enabled_false_unwinds_a_blocked_host_immediately(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    monkeypatch.setattr(blocklist.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    monkeypatch.setattr(block_rules_engine, "TABLE_FILE_PATH", str(tmp_path / "blocked_hosts.tbl"))
    rule_id = block_rules_engine.create_host_rule(conn, "Rule", [_device(hostname=None)], None, None, NOW)
    block_rules_engine.apply_rule(conn, rule_id, NOW)
    assert blocklist.list_blocked(conn) != []

    assert block_rules_engine.set_enabled(conn, rule_id, False, NOW + 5) is True

    assert blocklist.list_blocked(conn) == []
    assert block_rules_engine.get_rule(conn, rule_id)["enabled"] == 0


def test_set_enabled_missing_rule_returns_false(tmp_path):
    conn = _fresh_conn(tmp_path)
    assert block_rules_engine.set_enabled(conn, 999999, True, NOW) is False


def test_delete_rule_unwinds_enforcement_then_removes_the_row(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    monkeypatch.setattr(blocklist.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    monkeypatch.setattr(block_rules_engine, "TABLE_FILE_PATH", str(tmp_path / "blocked_hosts.tbl"))
    rule_id = block_rules_engine.create_host_rule(conn, "Rule", [_device(hostname=None)], None, None, NOW)
    block_rules_engine.apply_rule(conn, rule_id, NOW)

    assert block_rules_engine.delete_rule(conn, rule_id, NOW + 5) is True

    assert blocklist.list_blocked(conn) == []
    assert block_rules_engine.get_rule(conn, rule_id) is None


def test_delete_rule_on_a_domain_rule_removes_every_devices_unbound_row(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    calls = []
    monkeypatch.setattr(block_rules_engine.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    devices = [_device(ip="10.0.0.9", hostname=None), _device(ip="10.0.0.10", hostname=None)]
    rule_id = block_rules_engine.create_domain_rule(conn, "Rule", devices, "youtube.com", None, None, None, NOW)

    assert block_rules_engine.delete_rule(conn, rule_id, NOW + 5) is True

    assert len(calls) == 2
    for call in calls:
        assert call[call.index("--action") + 1] == "remove"
    assert block_rules_engine.get_rule(conn, rule_id) is None


def test_delete_rule_missing_rule_returns_false(tmp_path):
    conn = _fresh_conn(tmp_path)
    assert block_rules_engine.delete_rule(conn, 999999, NOW) is False


def test_set_override_rejects_a_schedule_less_rule(tmp_path):
    conn = _fresh_conn(tmp_path)
    rule_id = block_rules_engine.create_host_rule(conn, "Rule", [_device(hostname=None)], None, None, NOW)
    result = block_rules_engine.set_override(conn, rule_id, "unblocked", NOW)
    assert result["status"] == "error"


def test_set_override_rejects_an_invalid_state(tmp_path):
    conn = _fresh_conn(tmp_path)
    rule_id = block_rules_engine.create_domain_rule(
        conn, "Rule", [_device(ip="10.0.0.9", hostname=None)], "x.com", ALWAYS_ON_SCHEDULE, None, None, NOW
    )
    result = block_rules_engine.set_override(conn, rule_id, "sideways", NOW)
    assert result["status"] == "error"


def test_set_override_unblock_sets_override_until_the_windows_end_and_applies(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    calls = []
    monkeypatch.setattr(block_rules_engine.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    rule_id = block_rules_engine.create_domain_rule(
        conn, "Rule", [_device(ip="10.0.0.9", hostname=None)], "youtube.com", ALWAYS_ON_SCHEDULE, None, None, NOW
    )
    block_rules_engine.apply_rule(conn, rule_id, NOW)  # inside the window -- gets blocked
    calls.clear()

    result = block_rules_engine.set_override(conn, rule_id, "unblocked", NOW)

    window_end = int(NOW_DT.replace(hour=23, minute=0).timestamp())
    assert result == {"status": "ok", "override_until": window_end}
    row = block_rules_engine.get_rule(conn, rule_id)
    assert row["manual_override_state"] == "unblocked"
    assert row["last_effective_state"] == "unblocked"
    assert calls[-1][calls[-1].index("--action") + 1] == "disable"


# --- resolve_rule_state (pure) --------------------------------------------

def test_always_rule_is_always_blocked(tmp_path):
    conn = _fresh_conn(tmp_path)
    rule_id = _insert_rule(conn, schedule_json=None)
    row = conn.execute("SELECT * FROM block_rules WHERE id = ?", (rule_id,)).fetchone()
    decision = block_rules_engine.resolve_rule_state(row, NOW)
    assert decision.should_be_blocked is True
    assert decision.clear_override is False


def test_scheduled_rule_blocked_inside_its_window(tmp_path):
    conn = _fresh_conn(tmp_path)
    rule_id = _insert_rule(conn, schedule_json=ALWAYS_ON_SCHEDULE)
    row = conn.execute("SELECT * FROM block_rules WHERE id = ?", (rule_id,)).fetchone()
    assert block_rules_engine.resolve_rule_state(row, NOW).should_be_blocked is True


def test_scheduled_rule_not_blocked_outside_its_window(tmp_path):
    conn = _fresh_conn(tmp_path)
    rule_id = _insert_rule(conn, schedule_json=GAP_SCHEDULE)
    row = conn.execute("SELECT * FROM block_rules WHERE id = ?", (rule_id,)).fetchone()
    assert block_rules_engine.resolve_rule_state(row, NOW).should_be_blocked is False


def test_active_unblock_override_suppresses_a_scheduled_block(tmp_path):
    conn = _fresh_conn(tmp_path)
    rule_id = _insert_rule(
        conn, schedule_json=ALWAYS_ON_SCHEDULE,
        manual_override_state="unblocked", override_until=NOW + 3600,
    )
    row = conn.execute("SELECT * FROM block_rules WHERE id = ?", (rule_id,)).fetchone()
    decision = block_rules_engine.resolve_rule_state(row, NOW)
    assert decision.should_be_blocked is False
    assert decision.clear_override is False


def test_active_block_override_forces_a_block_during_a_gap(tmp_path):
    conn = _fresh_conn(tmp_path)
    rule_id = _insert_rule(
        conn, schedule_json=GAP_SCHEDULE,
        manual_override_state="blocked", override_until=NOW + 3600,
    )
    row = conn.execute("SELECT * FROM block_rules WHERE id = ?", (rule_id,)).fetchone()
    assert block_rules_engine.resolve_rule_state(row, NOW).should_be_blocked is True


def test_expired_override_is_cleared_and_falls_back_to_the_schedule(tmp_path):
    conn = _fresh_conn(tmp_path)
    rule_id = _insert_rule(
        conn, schedule_json=ALWAYS_ON_SCHEDULE,
        manual_override_state="unblocked", override_until=NOW - 1,  # already expired
    )
    row = conn.execute("SELECT * FROM block_rules WHERE id = ?", (rule_id,)).fetchone()
    decision = block_rules_engine.resolve_rule_state(row, NOW)
    assert decision.clear_override is True
    assert decision.should_be_blocked is True  # back to the schedule's own (blocked) state


# --- apply_rule (pf side real, subprocess mocked) -------------------------

def test_apply_rule_blocks_every_device_in_the_group(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    calls = []
    monkeypatch.setattr(blocklist.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    monkeypatch.setattr(block_rules_engine, "TABLE_FILE_PATH", str(tmp_path / "blocked_hosts.tbl"))
    devices = [_device(ip="10.0.0.5", hostname=None), _device(ip="10.0.0.6", hostname=None)]
    rule_id = _insert_rule(conn, rule_type="host", devices=devices, schedule_json=None)

    decision = block_rules_engine.apply_rule(conn, rule_id, NOW)

    assert decision.should_be_blocked is True
    blocked_ips = {row["local_ip"] for row in blocklist.list_blocked(conn)}
    assert blocked_ips == {"10.0.0.5", "10.0.0.6"}
    assert any(call[0] == blocklist.PFCTL for call in calls)  # sync_pf/kill_states actually ran
    row = conn.execute("SELECT last_effective_state FROM block_rules WHERE id = ?", (rule_id,)).fetchone()
    assert row["last_effective_state"] == "blocked"


def test_apply_rule_only_syncs_pf_once_regardless_of_group_size(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    sync_calls = []
    monkeypatch.setattr(blocklist.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    monkeypatch.setattr(block_rules_engine, "TABLE_FILE_PATH", str(tmp_path / "blocked_hosts.tbl"))
    real_sync_pf = blocklist.sync_pf
    monkeypatch.setattr(blocklist, "sync_pf", lambda *a, **kw: sync_calls.append(1) or real_sync_pf(*a, **kw))
    devices = [_device(ip="10.0.0.5", hostname=None), _device(ip="10.0.0.6", hostname=None), _device(ip="10.0.0.7", hostname=None)]
    rule_id = _insert_rule(conn, rule_type="host", devices=devices, schedule_json=None)

    block_rules_engine.apply_rule(conn, rule_id, NOW)

    assert len(sync_calls) == 1


def test_apply_rule_does_not_reblock_an_already_blocked_host(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    add_block_calls = []
    monkeypatch.setattr(blocklist.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    monkeypatch.setattr(block_rules_engine, "TABLE_FILE_PATH", str(tmp_path / "blocked_hosts.tbl"))
    real_add_block = blocklist.add_block

    def _tracking_add_block(*args, **kwargs):
        add_block_calls.append(args)
        return real_add_block(*args, **kwargs)

    monkeypatch.setattr(blocklist, "add_block", _tracking_add_block)
    rule_id = _insert_rule(conn, rule_type="host", schedule_json=None)

    block_rules_engine.apply_rule(conn, rule_id, NOW)
    block_rules_engine.apply_rule(conn, rule_id, NOW + 60)

    assert len(add_block_calls) == 1


def test_apply_rule_unblocks_a_host_whose_window_has_ended(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    monkeypatch.setattr(blocklist.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    monkeypatch.setattr(block_rules_engine, "TABLE_FILE_PATH", str(tmp_path / "blocked_hosts.tbl"))
    rule_id = _insert_rule(conn, rule_type="host", schedule_json=ALWAYS_ON_SCHEDULE)
    block_rules_engine.apply_rule(conn, rule_id, NOW)  # inside the window -- gets blocked
    assert blocklist.list_blocked(conn) != []

    after_window = int(NOW_DT.replace(hour=23, minute=30).timestamp())
    decision = block_rules_engine.apply_rule(conn, rule_id, after_window)

    assert decision.should_be_blocked is False
    assert blocklist.list_blocked(conn) == []


def test_apply_rule_applies_a_domain_rule_via_the_php_script_once_per_device(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    calls = []
    monkeypatch.setattr(block_rules_engine.subprocess, "run", lambda args, **kw: calls.append(args) or _FakeCompletedProcess())
    devices = [_device(ip="10.0.0.9", hostname=None), _device(ip="10.0.0.10", hostname=None)]
    rule_id = _insert_rule(
        conn, rule_type="domain", devices=devices, domains="youtube.com",
        schedule_json=None, unbound_description="gowiththeflow:rule:1",
    )

    block_rules_engine.apply_rule(conn, rule_id, NOW)

    assert len(calls) == 2
    source_ips = set()
    for argv in calls:
        assert argv[0] == block_rules_engine.PHP_BIN
        assert argv[argv.index("--action") + 1] == "enable"
        assert argv[argv.index("--domains") + 1] == "youtube.com"
        source_ips.add(argv[argv.index("--source-ip") + 1])
        assert argv[argv.index("--description") + 1].startswith("gowiththeflow:rule:1:")
    assert source_ips == {"10.0.0.9", "10.0.0.10"}


def test_apply_rule_logs_but_does_not_raise_when_the_php_script_fails(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    monkeypatch.setattr(
        block_rules_engine.subprocess, "run",
        lambda args, **kw: _FakeCompletedProcess(returncode=1, stderr="boom"),
    )
    rule_id = _insert_rule(
        conn, rule_type="domain", devices=[_device(ip="10.0.0.9", hostname=None)],
        domains="youtube.com", schedule_json=None,
    )
    block_rules_engine.apply_rule(conn, rule_id, NOW)  # must not raise


def test_apply_rule_returns_none_for_a_disabled_or_missing_rule(tmp_path):
    conn = _fresh_conn(tmp_path)
    rule_id = _insert_rule(conn, enabled=0)
    assert block_rules_engine.apply_rule(conn, rule_id, NOW) is None
    assert block_rules_engine.apply_rule(conn, 999999, NOW) is None


# --- reconcile_all ---------------------------------------------------------

def test_reconcile_all_skips_a_bad_rule_without_aborting_the_others(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    monkeypatch.setattr(blocklist.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    monkeypatch.setattr(block_rules_engine, "TABLE_FILE_PATH", str(tmp_path / "blocked_hosts.tbl"))
    good_id = _insert_rule(conn, rule_type="host", devices=[_device(ip="10.0.0.5")], schedule_json=None)
    bad_id = _insert_rule(conn, rule_type="host", devices=[_device(ip="10.0.0.6")], schedule_json="not valid json")

    decisions = block_rules_engine.reconcile_all(conn, NOW)

    assert {d.rule_id for d in decisions} == {good_id}
    good_row = conn.execute("SELECT last_effective_state FROM block_rules WHERE id = ?", (good_id,)).fetchone()
    assert good_row["last_effective_state"] == "blocked"
    bad_row = conn.execute("SELECT last_effective_state FROM block_rules WHERE id = ?", (bad_id,)).fetchone()
    assert bad_row["last_effective_state"] is None

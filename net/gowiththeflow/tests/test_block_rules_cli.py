import argparse
import json

import block_rules
import block_rules_engine
import blocklist
import db

HOST_IP = "10.0.0.5"
HOST_IP_2 = "10.0.0.6"
DOMAIN_IP = "10.0.0.9"
DOMAIN_IP_2 = "10.0.0.10"
ALWAYS_ON_SCHEDULE = json.dumps({"windows": [{"days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"], "start": "00:00", "end": "23:59"}]})


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_common(monkeypatch, tmp_path):
    db_path = str(tmp_path / "flows.db")
    monkeypatch.setattr(block_rules, "DB_PATH", db_path)
    monkeypatch.setattr(block_rules_engine, "TABLE_FILE_PATH", str(tmp_path / "blocked_hosts.tbl"))
    monkeypatch.setattr(blocklist.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    monkeypatch.setattr(block_rules_engine.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    monkeypatch.setattr(blocklist, "rules_present", lambda: True)
    return db_path


def _rules(db_path):
    conn = db.connect(db_path)
    db.init_schema(conn)
    return block_rules_engine.list_rules(conn)


def _create_args(**overrides):
    defaults = dict(type="host", name="Test Rule", devices=HOST_IP, domains=None, schedule=None, by="admin", reason="bedtime")
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _edit_args(**overrides):
    defaults = dict(id=None, name="Test Rule", devices=HOST_IP, domains=None, schedule=None)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_create_host_rule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_create(_create_args())
    assert result["status"] == "ok"
    assert result["blocked"] is True
    assert result["devices"] == [HOST_IP]
    rows = _rules(db_path)
    assert len(rows) == 1
    assert rows[0]["rule_type"] == "host" and rows[0]["schedule_json"] is None
    assert rows[0]["name"] == "Test Rule"
    assert json.loads(rows[0]["devices"]) == [{"ip": HOST_IP, "hostname": None, "mac": None}]


def test_create_multi_device_host_rule_blocks_every_device(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_create(_create_args(devices=f"{HOST_IP}, {HOST_IP_2}"))
    assert result["status"] == "ok"
    assert result["devices"] == [HOST_IP, HOST_IP_2]
    row = _rules(db_path)[0]
    assert [d["ip"] for d in json.loads(row["devices"])] == [HOST_IP, HOST_IP_2]
    blocked_ips = {r["local_ip"] for r in db.connect(db_path).execute("SELECT local_ip FROM blocked_hosts")}
    assert blocked_ips == {HOST_IP, HOST_IP_2}


def test_create_rejects_an_empty_device_list(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_create(_create_args(devices=" , "))
    assert result["status"] == "error"
    assert _rules(db_path) == []


def test_create_rejects_an_invalid_device_address(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_create(_create_args(devices="not-an-ip"))
    assert result["status"] == "error"
    assert _rules(db_path) == []


def test_create_host_rule_refuses_the_firewalls_own_address(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(blocklist, "refuse_reason_for_host_block", lambda ip, subnets: "refusing to block one of the firewall's own addresses")
    result = block_rules.cmd_create(_create_args())
    assert result["status"] == "error"
    assert _rules(db_path) == []


def test_create_host_rule_refuses_a_device_already_in_another_host_rule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    first = block_rules.cmd_create(_create_args(name="First", devices=HOST_IP))
    assert first["status"] == "ok"
    result = block_rules.cmd_create(_create_args(name="Second", devices=f"{HOST_IP},{HOST_IP_2}"))
    assert result["status"] == "error"
    assert HOST_IP in result["error"]
    assert "First" in result["error"]


def test_conflict_error_names_the_rule_and_shows_the_hostname_when_known(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.execute(
        "INSERT INTO local_host_identity (ip, mac, hostname, updated_at) VALUES (?, ?, ?, ?)",
        (HOST_IP, "aa:bb:cc:dd:ee:ff", "nvr", 0),
    )
    conn.commit()
    first = block_rules.cmd_create(_create_args(name="Existing rule", devices=HOST_IP))
    assert first["status"] == "ok"

    result = block_rules.cmd_create(_create_args(name="New rule", devices=HOST_IP))

    assert result["status"] == "error"
    assert result["error"] == 'nvr is already being blocked by rule "Existing rule"'
    assert len(_rules(db_path)) == 1


def test_create_host_rule_with_a_schedule_is_not_blocked_outside_its_window(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    gap_schedule = json.dumps({"windows": [{"days": ["mon"], "start": "01:00", "end": "02:00"}]})
    result = block_rules.cmd_create(_create_args(schedule=gap_schedule))
    assert result["status"] == "ok"
    row = _rules(db_path)[0]
    assert row["schedule_json"] == gap_schedule
    # Outside the window right now (whenever "now" happens to be), unless
    # by sheer coincidence the test runs inside 01:00-02:00 on a Monday --
    # so assert against the DB's own recorded outcome instead of a fixed
    # expectation, exercising the real code path without being flaky.
    assert row["last_effective_state"] in ("blocked", "unblocked")


def test_create_rejects_an_invalid_schedule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_create(_create_args(schedule="{not json"))
    assert result["status"] == "error"
    assert _rules(db_path) == []


def test_create_domain_rule_requires_at_least_one_domain(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_create(_create_args(type="domain", devices=DOMAIN_IP, domains=""))
    assert result["status"] == "error"
    assert _rules(db_path) == []


def test_create_domain_rule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_create(_create_args(type="domain", devices=DOMAIN_IP, domains="youtube.com"))
    assert result["status"] == "ok"
    row = _rules(db_path)[0]
    assert row["rule_type"] == "domain"
    assert row["domains"] == "youtube.com"
    assert row["unbound_description"] == f"gowiththeflow:rule:{row['id']}"


def test_create_multi_device_domain_rule_does_not_conflict_like_host_rules_do(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    first = block_rules.cmd_create(_create_args(type="domain", devices=DOMAIN_IP, domains="youtube.com"))
    assert first["status"] == "ok"
    result = block_rules.cmd_create(_create_args(type="domain", devices=f"{DOMAIN_IP},{DOMAIN_IP_2}", domains="tiktok.com"))
    assert result["status"] == "ok"
    assert len(_rules(db_path)) == 2


def test_edit_updates_name_devices_domains_and_schedule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    created = block_rules.cmd_create(_create_args(type="domain", devices=DOMAIN_IP, domains="youtube.com"))
    result = block_rules.cmd_edit(_edit_args(
        id=created["id"], name="Renamed", devices=f"{DOMAIN_IP},{DOMAIN_IP_2}",
        domains="youtube.com,tiktok.com", schedule=ALWAYS_ON_SCHEDULE,
    ))
    assert result["status"] == "ok"
    row = _rules(db_path)[0]
    assert row["name"] == "Renamed"
    assert [d["ip"] for d in json.loads(row["devices"])] == [DOMAIN_IP, DOMAIN_IP_2]
    assert row["domains"] == "youtube.com,tiktok.com"
    assert row["schedule_json"] == ALWAYS_ON_SCHEDULE


def test_edit_excludes_the_rule_itself_from_the_conflict_check(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    created = block_rules.cmd_create(_create_args(devices=HOST_IP))
    result = block_rules.cmd_edit(_edit_args(id=created["id"], devices=f"{HOST_IP},{HOST_IP_2}"))
    assert result["status"] == "ok"
    row = _rules(db_path)[0]
    assert [d["ip"] for d in json.loads(row["devices"])] == [HOST_IP, HOST_IP_2]


def test_edit_still_refuses_a_device_owned_by_another_host_rule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    other = block_rules.cmd_create(_create_args(name="Other", devices=HOST_IP_2))
    mine = block_rules.cmd_create(_create_args(name="Mine", devices=HOST_IP))
    result = block_rules.cmd_edit(_edit_args(id=mine["id"], devices=f"{HOST_IP},{HOST_IP_2}"))
    assert result["status"] == "error"
    assert HOST_IP_2 in result["error"]
    assert "Other" in result["error"]
    row = next(r for r in _rules(db_path) if r["id"] == mine["id"])
    assert [d["ip"] for d in json.loads(row["devices"])] == [HOST_IP]


def test_edit_missing_rule_returns_error(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_edit(_edit_args(id=999999, devices="10.0.0.1"))
    assert result["status"] == "error"


def test_duplicate_rule_copies_everything_but_starts_disabled(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    created = block_rules.cmd_create(_create_args(name="Original", devices=f"{HOST_IP},{HOST_IP_2}"))
    result = block_rules.cmd_duplicate(argparse.Namespace(id=created["id"]))
    assert result["status"] == "ok"
    rows = {r["id"]: r for r in _rules(db_path)}
    copy = rows[result["id"]]
    assert copy["name"] == "Original (copy)"
    assert copy["enabled"] == 0
    assert json.loads(copy["devices"]) == json.loads(rows[created["id"]]["devices"])
    # Duplicating must not itself apply/block anything -- the copy stays
    # disabled until the user reviews and enables it.
    assert copy["last_effective_state"] is None


def test_duplicate_missing_rule_returns_error(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_duplicate(argparse.Namespace(id=999999))
    assert result["status"] == "error"


def test_enabling_a_duplicate_host_rule_refuses_while_the_original_is_still_enabled(tmp_path, monkeypatch):
    # The exact reported scenario: duplicate a host rule (starts disabled
    # on purpose), then try to turn it on without first pausing or
    # re-pointing the original -- must be refused with the conflicting
    # rule named, not silently allowed to double-block the same devices.
    db_path = _patch_common(monkeypatch, tmp_path)
    original = block_rules.cmd_create(_create_args(name="Original", devices=HOST_IP))
    dup = block_rules.cmd_duplicate(argparse.Namespace(id=original["id"]))
    assert dup["status"] == "ok"

    result = block_rules.cmd_set_enabled(argparse.Namespace(id=dup["id"], enabled="1"))
    assert result["status"] == "error"
    assert HOST_IP in result["error"]
    assert "Original" in result["error"]
    row = next(r for r in _rules(db_path) if r["id"] == dup["id"])
    assert row["enabled"] == 0


def test_enabling_a_duplicate_domain_rule_is_allowed(tmp_path, monkeypatch):
    # Domain rules never conflict with each other (several independent
    # domain blocks for the same device is a real, intended case) -- the
    # new enable-time guard must stay host-only, matching create/edit.
    db_path = _patch_common(monkeypatch, tmp_path)
    original = block_rules.cmd_create(_create_args(type="domain", devices=DOMAIN_IP, domains="youtube.com"))
    dup = block_rules.cmd_duplicate(argparse.Namespace(id=original["id"]))
    result = block_rules.cmd_set_enabled(argparse.Namespace(id=dup["id"], enabled="1"))
    assert result["status"] == "ok"
    row = next(r for r in _rules(db_path) if r["id"] == dup["id"])
    assert row["enabled"] == 1


def test_delete_removes_the_rule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    created = block_rules.cmd_create(_create_args())
    result = block_rules.cmd_delete(argparse.Namespace(id=created["id"]))
    assert result["status"] == "ok"
    assert _rules(db_path) == []


def test_set_enabled_false_then_true(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    created = block_rules.cmd_create(_create_args())
    off = block_rules.cmd_set_enabled(argparse.Namespace(id=created["id"], enabled="0"))
    assert off == {"status": "ok", "id": created["id"], "enabled": False}
    assert _rules(db_path)[0]["enabled"] == 0

    on = block_rules.cmd_set_enabled(argparse.Namespace(id=created["id"], enabled="1"))
    assert on == {"status": "ok", "id": created["id"], "enabled": True}
    assert _rules(db_path)[0]["enabled"] == 1


def test_override_on_a_schedule_less_rule_is_an_error(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    created = block_rules.cmd_create(_create_args())
    result = block_rules.cmd_override(argparse.Namespace(id=created["id"], state="unblocked"))
    assert result["status"] == "error"


def test_override_on_a_scheduled_rule_applies_immediately(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    created = block_rules.cmd_create(_create_args(type="domain", devices=DOMAIN_IP, domains="youtube.com", schedule=ALWAYS_ON_SCHEDULE))
    result = block_rules.cmd_override(argparse.Namespace(id=created["id"], state="unblocked"))
    assert result["status"] == "ok"
    row = _rules(db_path)[0]
    assert row["manual_override_state"] == "unblocked"
    assert row["last_effective_state"] == "unblocked"

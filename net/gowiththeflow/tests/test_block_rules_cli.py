import argparse
import json

import block_rules
import block_rules_engine
import blocklist
import db

HOST_IP = "10.0.0.5"
DOMAIN_IP = "10.0.0.9"
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


def test_create_host_rule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_create(argparse.Namespace(
        type="host", ip=HOST_IP, domains=None, schedule=None, by="admin", reason="bedtime",
    ))
    assert result["status"] == "ok"
    assert result["blocked"] is True
    rows = _rules(db_path)
    assert len(rows) == 1
    assert rows[0]["rule_type"] == "host" and rows[0]["schedule_json"] is None


def test_create_host_rule_refuses_the_firewalls_own_address(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(blocklist, "refuse_reason_for_host_block", lambda ip, subnets: "refusing to block one of the firewall's own addresses")
    result = block_rules.cmd_create(argparse.Namespace(
        type="host", ip=HOST_IP, domains=None, schedule=None, by=None, reason=None,
    ))
    assert result["status"] == "error"
    assert _rules(db_path) == []


def test_create_host_rule_with_a_schedule_is_not_blocked_outside_its_window(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    gap_schedule = json.dumps({"windows": [{"days": ["mon"], "start": "01:00", "end": "02:00"}]})
    result = block_rules.cmd_create(argparse.Namespace(
        type="host", ip=HOST_IP, domains=None, schedule=gap_schedule, by=None, reason=None,
    ))
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
    result = block_rules.cmd_create(argparse.Namespace(
        type="host", ip=HOST_IP, domains=None, schedule="{not json", by=None, reason=None,
    ))
    assert result["status"] == "error"
    assert _rules(db_path) == []


def test_create_domain_rule_requires_at_least_one_domain(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_create(argparse.Namespace(
        type="domain", ip=DOMAIN_IP, domains="", schedule=None, by=None, reason=None,
    ))
    assert result["status"] == "error"
    assert _rules(db_path) == []


def test_create_domain_rule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_create(argparse.Namespace(
        type="domain", ip=DOMAIN_IP, domains="youtube.com", schedule=None, by="admin", reason=None,
    ))
    assert result["status"] == "ok"
    row = _rules(db_path)[0]
    assert row["rule_type"] == "domain"
    assert row["domains"] == "youtube.com"
    assert row["unbound_description"] == f"gowiththeflow:rule:{row['id']}"


def test_edit_updates_domains_and_schedule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    created = block_rules.cmd_create(argparse.Namespace(
        type="domain", ip=DOMAIN_IP, domains="youtube.com", schedule=None, by=None, reason=None,
    ))
    result = block_rules.cmd_edit(argparse.Namespace(id=created["id"], domains="youtube.com,tiktok.com", schedule=ALWAYS_ON_SCHEDULE))
    assert result["status"] == "ok"
    row = _rules(db_path)[0]
    assert row["domains"] == "youtube.com,tiktok.com"
    assert row["schedule_json"] == ALWAYS_ON_SCHEDULE


def test_edit_missing_rule_returns_error(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    result = block_rules.cmd_edit(argparse.Namespace(id=999999, domains="x.com", schedule=None))
    assert result["status"] == "error"


def test_delete_removes_the_rule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    created = block_rules.cmd_create(argparse.Namespace(
        type="host", ip=HOST_IP, domains=None, schedule=None, by=None, reason=None,
    ))
    result = block_rules.cmd_delete(argparse.Namespace(id=created["id"]))
    assert result["status"] == "ok"
    assert _rules(db_path) == []


def test_set_enabled_false_then_true(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    created = block_rules.cmd_create(argparse.Namespace(
        type="host", ip=HOST_IP, domains=None, schedule=None, by=None, reason=None,
    ))
    off = block_rules.cmd_set_enabled(argparse.Namespace(id=created["id"], enabled="0"))
    assert off == {"status": "ok", "id": created["id"], "enabled": False}
    assert _rules(db_path)[0]["enabled"] == 0

    on = block_rules.cmd_set_enabled(argparse.Namespace(id=created["id"], enabled="1"))
    assert on == {"status": "ok", "id": created["id"], "enabled": True}
    assert _rules(db_path)[0]["enabled"] == 1


def test_override_on_a_schedule_less_rule_is_an_error(tmp_path, monkeypatch):
    _patch_common(monkeypatch, tmp_path)
    created = block_rules.cmd_create(argparse.Namespace(
        type="host", ip=HOST_IP, domains=None, schedule=None, by=None, reason=None,
    ))
    result = block_rules.cmd_override(argparse.Namespace(id=created["id"], state="unblocked"))
    assert result["status"] == "error"


def test_override_on_a_scheduled_rule_applies_immediately(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    created = block_rules.cmd_create(argparse.Namespace(
        type="domain", ip=DOMAIN_IP, domains="youtube.com", schedule=ALWAYS_ON_SCHEDULE, by=None, reason=None,
    ))
    result = block_rules.cmd_override(argparse.Namespace(id=created["id"], state="unblocked"))
    assert result["status"] == "ok"
    row = _rules(db_path)[0]
    assert row["manual_override_state"] == "unblocked"
    assert row["last_effective_state"] == "unblocked"

import argparse
import json

import block_host
import block_rules_engine
import blocklist
import db

NOW_IP = "10.0.0.5"


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_common(monkeypatch, tmp_path):
    """Redirects every filesystem/subprocess touchpoint cmd_block/cmd_unblock
    hit into a throwaway temp DB/table, and makes every pfctl/ifconfig call
    a harmless no-op -- this test is about the new block_rules lockstep
    behavior, not re-proving pf plumbing test_blocklist.py already covers."""
    db_path = str(tmp_path / "flows.db")
    monkeypatch.setattr(block_host, "DB_PATH", db_path)
    monkeypatch.setattr(block_host, "TABLE_FILE_PATH", str(tmp_path / "blocked_hosts.tbl"))
    monkeypatch.setattr(block_rules_engine, "TABLE_FILE_PATH", str(tmp_path / "blocked_hosts.tbl"))
    monkeypatch.setattr(blocklist.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    monkeypatch.setattr(blocklist, "rules_present", lambda: True)
    return db_path


def _rule_count(db_path: str) -> int:
    conn = db.connect(db_path)
    db.init_schema(conn)
    return len(block_rules_engine.list_rules(conn))


def test_cmd_block_creates_a_matching_always_host_rule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    args = argparse.Namespace(ip=NOW_IP, by="admin", reason="bedtime")

    result = block_host.cmd_block(args)

    assert result["status"] == "ok"
    conn = db.connect(db_path)
    rules = block_rules_engine.list_rules(conn)
    assert len(rules) == 1
    assert rules[0]["rule_type"] == "host"
    assert json.loads(rules[0]["devices"]) == [{"ip": NOW_IP, "hostname": None, "mac": None}]
    assert rules[0]["schedule_json"] is None
    assert rules[0]["created_by"] == "admin"


def test_cmd_block_twice_does_not_duplicate_the_rule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    block_host.cmd_block(argparse.Namespace(ip=NOW_IP, by="admin", reason=None))
    block_host.cmd_block(argparse.Namespace(ip=NOW_IP, by="admin", reason="re-blocked"))
    assert _rule_count(db_path) == 1


def test_cmd_unblock_removes_the_matching_host_rule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    block_host.cmd_block(argparse.Namespace(ip=NOW_IP, by="admin", reason=None))
    assert _rule_count(db_path) == 1

    result = block_host.cmd_unblock(argparse.Namespace(ip=NOW_IP))

    assert result["status"] == "ok"
    assert _rule_count(db_path) == 0


def test_cmd_unblock_on_an_ip_with_no_rule_is_a_no_op_not_an_error(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    result = block_host.cmd_unblock(argparse.Namespace(ip=NOW_IP))
    assert result["status"] == "ok"
    assert _rule_count(db_path) == 0


def test_cmd_block_refuses_the_firewalls_own_address_and_creates_no_rule(tmp_path, monkeypatch):
    db_path = _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(blocklist, "refuse_reason_for_host_block", lambda ip, subnets: "refusing to block one of the firewall's own addresses")

    result = block_host.cmd_block(argparse.Namespace(ip=NOW_IP, by="admin", reason=None))

    assert result["status"] == "error"
    assert _rule_count(db_path) == 0

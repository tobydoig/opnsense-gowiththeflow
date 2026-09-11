import json

import db
import reservation_gate
import reservation_sync


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_common(monkeypatch, tmp_path):
    db_path = str(tmp_path / "flows.db")
    settings_path = str(tmp_path / "gowiththeflow.json")
    monkeypatch.setattr(reservation_sync, "DB_PATH", db_path)
    monkeypatch.setattr(reservation_sync, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(reservation_gate, "ALLOWED_TABLE_FILE", str(tmp_path / "allowed.tbl"))
    monkeypatch.setattr(reservation_gate, "PROTECTED_TABLE_FILE", str(tmp_path / "protected.tbl"))
    monkeypatch.setattr(reservation_gate.subprocess, "run", lambda args, **kw: _FakeCompletedProcess())
    return db_path, settings_path


def test_main_prints_ok_when_gate_disabled(tmp_path, monkeypatch, capsys):
    db_path, settings_path = _patch_common(monkeypatch, tmp_path)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump({"enable_reservation_gate": False}, f)

    exit_code = reservation_sync.main()

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "ok", "enabled": False, "allowed_count": 0, "pinned_count": 0}


def test_main_reads_settings_and_reconciles(tmp_path, monkeypatch, capsys):
    db_path, settings_path = _patch_common(monkeypatch, tmp_path)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump({"enable_reservation_gate": True, "enable_arp_pinning": False, "local_subnets": []}, f)
    monkeypatch.setattr(reservation_gate, "fetch_reservations", lambda: [("aa:bb:cc:dd:ee:01", "10.0.0.20")])

    exit_code = reservation_sync.main()

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"
    assert result["enabled"] is True
    assert result["allowed_count"] == 1


def test_main_missing_settings_file_defaults_to_disabled(tmp_path, monkeypatch, capsys):
    db_path, settings_path = _patch_common(monkeypatch, tmp_path)
    # Never written -- matches a fresh install before the first
    # ServiceController::reconfigureAction() has ever rendered it.

    exit_code = reservation_sync.main()

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "ok", "enabled": False, "allowed_count": 0, "pinned_count": 0}


def test_main_always_exits_0_even_on_a_fetch_failure(tmp_path, monkeypatch, capsys):
    # Same "always exit 0" reasoning as block_host.py's own module
    # docstring -- configdpRun() discards stdout on a non-zero exit,
    # which would silently swallow the real error detail.
    db_path, settings_path = _patch_common(monkeypatch, tmp_path)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump({"enable_reservation_gate": True}, f)
    monkeypatch.setattr(reservation_gate, "fetch_reservations", lambda: None)

    exit_code = reservation_sync.main()

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"

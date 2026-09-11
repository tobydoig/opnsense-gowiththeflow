import json

from gowiththeflowd import Config


def test_config_load_defaults_reservation_gate_fields_off_when_absent(tmp_path):
    # Matches a settings file rendered before these two fields existed
    # (or a fresh install before the first reconfigure) -- must default
    # to fully off, not crash on a missing key.
    path = str(tmp_path / "gowiththeflow.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({}, f)
    config = Config.load(path)
    assert config.enable_reservation_gate is False
    assert config.enable_arp_pinning is False


def test_config_load_reads_reservation_gate_fields_when_present(tmp_path):
    path = str(tmp_path / "gowiththeflow.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"enable_reservation_gate": 1, "enable_arp_pinning": 1}, f)
    config = Config.load(path)
    assert config.enable_reservation_gate is True
    assert config.enable_arp_pinning is True


def test_config_load_missing_file_defaults_to_fully_off(tmp_path):
    config = Config.load(str(tmp_path / "does_not_exist.json"))
    assert config.enable_reservation_gate is False
    assert config.enable_arp_pinning is False

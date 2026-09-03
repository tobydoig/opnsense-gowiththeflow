import os

import pytest

import blocklist
import db

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
NOW = 1_000_000


def _load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


def _fresh_conn(tmp_path):
    conn = db.connect(str(tmp_path / "flows.db"))
    db.init_schema(conn)
    return conn


# --- normalize_ip -----------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10.0.0.5", "10.0.0.5"),
        ("192.168.1.1", "192.168.1.1"),
        ("fe80::1", "fe80::1"),
        ("FE80::0:1", "fe80::1"),  # case and zero-compression both canonicalize
        ("::1", "::1"),
    ],
)
def test_normalize_ip_accepts_and_canonicalizes_valid_addresses(raw, expected):
    assert blocklist.normalize_ip(raw) == expected


def test_normalize_ip_rejects_ambiguous_leading_zero_octets():
    # Python's ipaddress module deliberately refuses "10.0.0.05" rather
    # than guessing whether it means decimal 5 or (as some other
    # parsers/libc historically treat a leading zero) octal -- exactly
    # the kind of parser-confusion ambiguity worth staying strict about
    # here, since this value goes straight into a firewall rule.
    assert blocklist.normalize_ip("10.0.0.05") is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "10.0.0.0/24",  # CIDR, not a single host
        "10.0.0.1-10.0.0.5",  # a range
        "example.com",  # a hostname, not an address
        "10.0.0.5; rm -rf /",  # shell metacharacters
        "not an ip",
        "999.999.999.999",
    ],
)
def test_normalize_ip_rejects_everything_else(raw):
    assert blocklist.normalize_ip(raw) is None


def test_normalize_ip_strips_surrounding_whitespace():
    assert blocklist.normalize_ip("  10.0.0.5  ") == "10.0.0.5"


# --- parse_own_addresses -----------------------------------------------

def test_parse_own_addresses_finds_every_inet_and_inet6_including_loopback():
    addresses = blocklist.parse_own_addresses(_load_fixture("ifconfig_a_sample.txt"))
    assert "10.0.0.1" in addresses  # le0 (LAN)
    assert "10.0.3.15" in addresses  # le1 (WAN)
    assert "127.0.0.1" in addresses  # lo0
    assert "::1" in addresses  # lo0 inet6
    assert "fd17:625c:f037:3:a00:27ff:fe7e:ddbc" in addresses  # le1 autoconf inet6


def test_parse_own_addresses_strips_the_zone_id_from_link_local_addresses():
    addresses = blocklist.parse_own_addresses(_load_fixture("ifconfig_a_sample.txt"))
    # The raw line is "fe80::a00:27ff:feac:8923%le0" -- the %le0 zone
    # suffix isn't part of the address and must not survive into the set
    # (normalize_ip() can't parse it with the suffix attached at all).
    assert "fe80::a00:27ff:feac:8923" in addresses
    assert not any("%" in a for a in addresses)


def test_parse_own_addresses_empty_input_returns_empty_set():
    assert blocklist.parse_own_addresses("") == set()


# --- is_subnet_edge_address ----------------------------------------------

def test_is_subnet_edge_address_flags_network_and_broadcast():
    assert blocklist.is_subnet_edge_address("10.0.0.0", ["10.0.0.0/24"])
    assert blocklist.is_subnet_edge_address("10.0.0.255", ["10.0.0.0/24"])


def test_is_subnet_edge_address_does_not_flag_a_real_host():
    assert not blocklist.is_subnet_edge_address("10.0.0.9", ["10.0.0.0/24"])


def test_is_subnet_edge_address_checks_every_configured_subnet():
    subnets = ["10.0.0.0/24", "192.168.1.0/24"]
    assert blocklist.is_subnet_edge_address("192.168.1.255", subnets)
    assert not blocklist.is_subnet_edge_address("192.168.1.5", subnets)


def test_is_subnet_edge_address_ignores_point_to_point_prefixes():
    # A /31 or /32 has no distinct network/broadcast address (RFC 3021) --
    # both addresses in a /31 are real, assignable hosts.
    assert not blocklist.is_subnet_edge_address("10.0.0.1", ["10.0.0.0/31"])
    assert not blocklist.is_subnet_edge_address("10.0.0.1", ["10.0.0.1/32"])


def test_is_subnet_edge_address_never_flags_ipv6():
    assert not blocklist.is_subnet_edge_address("fd00::", ["fd00::/64"])


# --- refuse_reason_for_host_block -----------------------------------------

def test_refuse_reason_for_host_block_refuses_the_firewalls_own_address(monkeypatch):
    monkeypatch.setattr(
        blocklist.subprocess, "run",
        lambda args, **kw: _FakeCompletedProcess(stdout=_load_fixture("ifconfig_a_sample.txt")),
    )
    reason = blocklist.refuse_reason_for_host_block("10.0.0.1", ["10.0.0.0/24"])
    assert reason is not None and "firewall" in reason


def test_refuse_reason_for_host_block_refuses_a_broadcast_address(monkeypatch):
    monkeypatch.setattr(
        blocklist.subprocess, "run",
        lambda args, **kw: _FakeCompletedProcess(stdout=_load_fixture("ifconfig_a_sample.txt")),
    )
    reason = blocklist.refuse_reason_for_host_block("10.0.0.255", ["10.0.0.0/24"])
    assert reason is not None and "broadcast" in reason


def test_refuse_reason_for_host_block_allows_a_real_device(monkeypatch):
    monkeypatch.setattr(
        blocklist.subprocess, "run",
        lambda args, **kw: _FakeCompletedProcess(stdout=_load_fixture("ifconfig_a_sample.txt")),
    )
    assert blocklist.refuse_reason_for_host_block("10.0.0.9", ["10.0.0.0/24"]) is None


def test_is_subnet_edge_address_rejects_garbage_ip_or_subnet():
    assert not blocklist.is_subnet_edge_address("not-an-ip", ["10.0.0.0/24"])
    assert not blocklist.is_subnet_edge_address("10.0.0.255", ["not-a-subnet"])
    assert not blocklist.is_subnet_edge_address("10.0.0.255", [])


# --- render_table_file / write_table_file -------------------------------

def test_render_table_file_sorts_and_deduplicates():
    text = blocklist.render_table_file(["10.0.0.5", "10.0.0.2", "10.0.0.5"])
    assert text == "10.0.0.2\n10.0.0.5\n"


def test_render_table_file_empty_list_is_empty_string():
    assert blocklist.render_table_file([]) == ""


def test_render_table_file_sorts_numerically_not_lexically():
    # Lexical sort would put "10.0.0.10" before "10.0.0.2" -- pf doesn't
    # care about ordering, but a human reading the file via
    # `pfctl -t ... -T show` shouldn't see it scrambled either.
    text = blocklist.render_table_file(["10.0.0.10", "10.0.0.2"])
    assert text == "10.0.0.2\n10.0.0.10\n"


def test_write_table_file_round_trips(tmp_path):
    path = str(tmp_path / "blocked_hosts.tbl")
    blocklist.write_table_file(path, ["10.0.0.5", "10.0.0.2"])
    with open(path, encoding="utf-8") as f:
        assert f.read() == "10.0.0.2\n10.0.0.5\n"


def test_write_table_file_leaves_original_intact_if_the_write_raises(tmp_path, monkeypatch):
    path = str(tmp_path / "blocked_hosts.tbl")
    blocklist.write_table_file(path, ["10.0.0.5"])

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(blocklist, "render_table_file", _boom)
    with pytest.raises(OSError):
        blocklist.write_table_file(path, ["10.0.0.9"])

    # The original file (and the temp file) must not be left behind in a
    # broken state -- the atomic rename never happened, so the old
    # content survives untouched, and the stray temp file was cleaned up.
    with open(path, encoding="utf-8") as f:
        assert f.read() == "10.0.0.5\n"
    assert os.listdir(tmp_path) == ["blocked_hosts.tbl"]


# --- DB CRUD -------------------------------------------------------------

def test_add_block_then_list_blocked(tmp_path):
    conn = _fresh_conn(tmp_path)
    blocklist.add_block(conn, "10.0.0.5", "kids-tablet", "aa:bb:cc:dd:ee:01", "admin", "bedtime", NOW)
    rows = blocklist.list_blocked(conn)
    assert len(rows) == 1
    assert rows[0]["local_ip"] == "10.0.0.5"
    assert rows[0]["hostname"] == "kids-tablet"
    assert rows[0]["mac"] == "aa:bb:cc:dd:ee:01"
    assert rows[0]["blocked_by"] == "admin"
    assert rows[0]["reason"] == "bedtime"
    assert rows[0]["blocked_at"] == NOW


def test_add_block_is_an_upsert_not_a_duplicate_row(tmp_path):
    conn = _fresh_conn(tmp_path)
    blocklist.add_block(conn, "10.0.0.5", "old-name", None, None, None, NOW)
    blocklist.add_block(conn, "10.0.0.5", "new-name", None, "admin", "re-blocked", NOW + 10)
    rows = blocklist.list_blocked(conn)
    assert len(rows) == 1
    assert rows[0]["hostname"] == "new-name"
    assert rows[0]["blocked_at"] == NOW + 10


def test_remove_block_removes_the_row(tmp_path):
    conn = _fresh_conn(tmp_path)
    blocklist.add_block(conn, "10.0.0.5", None, None, None, None, NOW)
    blocklist.remove_block(conn, "10.0.0.5")
    assert blocklist.list_blocked(conn) == []


def test_remove_block_on_an_unblocked_ip_is_a_no_op_not_an_error(tmp_path):
    conn = _fresh_conn(tmp_path)
    blocklist.remove_block(conn, "10.0.0.99")  # must not raise
    assert blocklist.list_blocked(conn) == []


def test_list_blocked_orders_most_recently_blocked_first(tmp_path):
    conn = _fresh_conn(tmp_path)
    blocklist.add_block(conn, "10.0.0.1", None, None, None, None, NOW)
    blocklist.add_block(conn, "10.0.0.2", None, None, None, None, NOW + 100)
    rows = blocklist.list_blocked(conn)
    assert [r["local_ip"] for r in rows] == ["10.0.0.2", "10.0.0.1"]


# --- sync_pf / kill_states (subprocess mocked) ---------------------------

class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_sync_pf_writes_the_file_and_calls_pfctl_replace(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    blocklist.add_block(conn, "10.0.0.5", None, None, None, None, NOW)
    tbl_path = str(tmp_path / "blocked_hosts.tbl")

    calls = []

    def _fake_run(args, **kwargs):
        calls.append(args)
        return _FakeCompletedProcess()

    monkeypatch.setattr(blocklist.subprocess, "run", _fake_run)
    blocklist.sync_pf(conn, tbl_path)

    with open(tbl_path, encoding="utf-8") as f:
        assert f.read() == "10.0.0.5\n"
    assert calls == [["/sbin/pfctl", "-t", "gowiththeflow_blocked", "-T", "replace", "-f", tbl_path]]


def test_sync_pf_does_not_raise_on_a_nonzero_pfctl_exit(tmp_path, monkeypatch):
    conn = _fresh_conn(tmp_path)
    tbl_path = str(tmp_path / "blocked_hosts.tbl")
    monkeypatch.setattr(blocklist.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(returncode=1))
    result = blocklist.sync_pf(conn, tbl_path)  # must not raise
    assert result.returncode == 1


def test_kill_states_v4_kills_both_directions(monkeypatch):
    calls = []
    monkeypatch.setattr(
        blocklist.subprocess, "run",
        lambda args, **k: calls.append(args) or _FakeCompletedProcess(),
    )
    blocklist.kill_states("10.0.0.5")
    assert calls == [
        ["/sbin/pfctl", "-k", "10.0.0.5"],
        ["/sbin/pfctl", "-k", "0.0.0.0/0", "-k", "10.0.0.5"],
    ]


def test_kill_states_v6_uses_the_v6_wildcard(monkeypatch):
    calls = []
    monkeypatch.setattr(
        blocklist.subprocess, "run",
        lambda args, **k: calls.append(args) or _FakeCompletedProcess(),
    )
    blocklist.kill_states("fe80::1")
    assert calls == [
        ["/sbin/pfctl", "-k", "fe80::1"],
        ["/sbin/pfctl", "-k", "::/0", "-k", "fe80::1"],
    ]


def test_rules_present_true_when_table_name_appears_in_ruleset(monkeypatch):
    monkeypatch.setattr(
        blocklist.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(stdout="block quick from <gowiththeflow_blocked> to any\n"),
    )
    assert blocklist.rules_present() is True


def test_rules_present_false_when_absent(monkeypatch):
    monkeypatch.setattr(blocklist.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(stdout="pass all\n"))
    assert blocklist.rules_present() is False

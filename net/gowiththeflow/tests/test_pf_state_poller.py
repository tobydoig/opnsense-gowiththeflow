import os

import pytest

from pf_state_poller import PfStatePoller, StateKey, classify_local_remote, parse_pfctl_state_text

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
LOCAL_SUBNETS = ["192.168.1.0/24"]


def _load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


def test_parse_pfctl_state_text_extracts_all_fields():
    records = parse_pfctl_state_text(_load_fixture("pfctl_state_poll_1.txt"))

    assert len(records) == 2
    tcp, udp = records
    assert tcp["proto"] == "tcp"
    assert tcp["src_ip"] == "192.168.1.50"
    assert tcp["src_port"] == "52341"
    assert tcp["dst_ip"] == "93.184.216.34"
    assert tcp["dst_port"] == "443"
    assert tcp["pkts_a"] == "14"
    assert tcp["pkts_b"] == "10"
    assert tcp["bytes_a"] == "9843"
    assert tcp["bytes_b"] == "1420"
    assert tcp["age"] == "00:00:12"

    assert udp["proto"] == "udp"
    assert udp["dst_ip"] == "8.8.8.8"


def test_classify_local_remote_orients_bytes_by_local_side():
    records = parse_pfctl_state_text(_load_fixture("pfctl_state_poll_1.txt"))
    snapshots = classify_local_remote(records, LOCAL_SUBNETS)

    assert len(snapshots) == 2
    by_remote = {s.key.remote_ip: s for s in snapshots}

    tcp_snap = by_remote["93.184.216.34"]
    assert tcp_snap.key.local_ip == "192.168.1.50"
    assert tcp_snap.key.local_port == 52341
    assert tcp_snap.key.remote_port == 443
    assert tcp_snap.bytes_out == 9843
    assert tcp_snap.bytes_in == 1420
    assert tcp_snap.pkts_out == 14
    assert tcp_snap.pkts_in == 10
    assert tcp_snap.age_s == 12

    udp_snap = by_remote["8.8.8.8"]
    assert udp_snap.key.local_ip == "192.168.1.60"
    assert udp_snap.bytes_out == 128
    assert udp_snap.bytes_in == 256


def test_classify_local_remote_skips_states_with_no_local_side():
    # Neither endpoint falls in the configured local subnet -- e.g. a state
    # observed on a segment that isn't one of ours -- must not be reported
    # as a local<->remote flow.
    text = (
        "10.0.0.5:1234 -> 10.0.0.9:443       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
    ).replace("10.0.0.5:1234 -> 10.0.0.9:443", "tcp 10.0.0.5:1234 -> 10.0.0.9:443")
    records = parse_pfctl_state_text(text)
    snapshots = classify_local_remote(records, LOCAL_SUBNETS)
    assert snapshots == []


def test_classify_local_remote_skips_states_with_both_sides_local():
    text = (
        "tcp 192.168.1.10:1234 -> 192.168.1.20:445       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
    )
    records = parse_pfctl_state_text(text)
    snapshots = classify_local_remote(records, LOCAL_SUBNETS)
    assert snapshots == []


def test_poller_reports_open_update_close_across_polls():
    poller = PfStatePoller(LOCAL_SUBNETS)

    result_1 = poller.poll(_load_fixture("pfctl_state_poll_1.txt"))
    assert len(result_1.opened) == 2
    assert result_1.updated == []
    assert result_1.closed == []

    result_2 = poller.poll(_load_fixture("pfctl_state_poll_2.txt"))

    # The 8.8.8.8 UDP state vanished between polls -> closed, and its final
    # snapshot must carry the LAST KNOWN cumulative counters (not deltas).
    assert len(result_2.closed) == 1
    closed = result_2.closed[0]
    assert closed.key.remote_ip == "8.8.8.8"
    assert closed.bytes_out == 128
    assert closed.bytes_in == 256

    # The 93.184.216.34 TCP state persisted with higher cumulative counters.
    assert len(result_2.updated) == 1
    updated = result_2.updated[0]
    assert updated.key.remote_ip == "93.184.216.34"
    assert updated.bytes_out == 25000
    assert updated.bytes_in == 3100
    assert updated.pkts_out == 30
    assert updated.pkts_in == 22

    # A brand new state to 151.101.1.140 appeared.
    assert len(result_2.opened) == 1
    opened = result_2.opened[0]
    assert opened.key.remote_ip == "151.101.1.140"
    assert opened.key.local_ip == "192.168.1.71"
    assert opened.bytes_out == 1200
    assert opened.bytes_in == 800


def test_parses_ipv6_bracket_port_notation():
    # Real pfctl output uses "ip[port]" for IPv6 (the address itself
    # contains colons), unlike IPv4's "ip:port" -- captured verbatim from
    # the OPNsense 26.7 test VM.
    text = (
        "all udp fd17:625c:f037:3:a00:27ff:fe7e:ddbc[54731] -> "
        "2001:503:231d::2:30[53]       SINGLE:NO_TRAFFIC\n"
        "   age 00:00:53, expires in 00:00:07, 1:1 pkts, 91:139 bytes, "
        "rule 89, allow-opts\n"
    )
    records = parse_pfctl_state_text(text)
    assert len(records) == 1
    rec = records[0]
    assert rec["proto"] == "udp"
    assert rec["src_ip"] == "fd17:625c:f037:3:a00:27ff:fe7e:ddbc"
    assert rec["src_port"] == "54731"
    assert rec["dst_ip"] == "2001:503:231d::2:30"
    assert rec["dst_port"] == "53"
    assert rec["bytes_a"] == "91"
    assert rec["bytes_b"] == "139"


def test_real_capture_admin_plane_traffic_to_opnsense_itself_is_skipped():
    # Real capture: a LAN client (10.0.0.9) reaching OPNsense's own LAN IP
    # (10.0.0.1) for SSH/HTTPS admin access or DNS -- both endpoints match
    # the local subnet, so this must be excluded (it isn't a
    # local-client-to-remote-destination flow to track at all).
    text = (
        "all tcp 10.0.0.1:443 <- 10.0.0.9:50843       ESTABLISHED:ESTABLISHED\n"
        "   [2723053261 + 2102272] wscale 7  [3974263217 + 65792] wscale 8\n"
        "   age 00:01:24, expires in 23:59:59, 809:1380 pkts, "
        "45698:1647388 bytes, rule 87\n"
        "   id: 7193896a00000000 creatorid: ea8fae34\n"
        "   origif: le0\n"
    )
    records = parse_pfctl_state_text(text)
    snapshots = classify_local_remote(records, ["10.0.0.0/24"])
    assert snapshots == []


def test_skips_states_with_a_bare_portless_ipv6_address():
    # Real bug caught on the OPNsense 26.7 test VM: a transient link-local
    # IPv6 state (neighbor discovery) with no port on either side. A naive
    # "split on the last colon" would corrupt 'fe80::1' into ip='fe80:',
    # port='1' since IPv6 addresses already contain colons -- this must
    # instead be recognized as portless and the whole record dropped, not
    # crash and not silently produce a wrong IP.
    text = (
        "all ipv6-icmp fe80::1 <- fe80::2       NO_TRAFFIC:NO_TRAFFIC\n"
        "   age 00:00:01, expires in 00:00:09, 1:0 pkts, 64:0 bytes, rule 5\n"
    )
    assert parse_pfctl_state_text(text) == []


def test_skips_bare_ipv6_address_with_scope_id():
    text = (
        "all ipv6-icmp fe80::1%le0 <- fe80::2%le0       NO_TRAFFIC:NO_TRAFFIC\n"
        "   age 00:00:01, expires in 00:00:09, 1:0 pkts, 64:0 bytes, rule 5\n"
    )
    assert parse_pfctl_state_text(text) == []


def test_state_key_is_hashable_and_stable_across_equal_snapshots():
    key_a = StateKey("tcp", "192.168.1.50", 52341, "93.184.216.34", 443)
    key_b = StateKey("tcp", "192.168.1.50", 52341, "93.184.216.34", 443)
    assert key_a == key_b
    assert hash(key_a) == hash(key_b)

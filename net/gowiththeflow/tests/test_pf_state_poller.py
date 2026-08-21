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


def test_state_key_is_hashable_and_stable_across_equal_snapshots():
    key_a = StateKey("tcp", "192.168.1.50", 52341, "93.184.216.34", 443)
    key_b = StateKey("tcp", "192.168.1.50", 52341, "93.184.216.34", 443)
    assert key_a == key_b
    assert hash(key_a) == hash(key_b)

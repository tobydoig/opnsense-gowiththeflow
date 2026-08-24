import os

import pytest

from pf_state_poller import (
    PfStatePoller,
    StateKey,
    StateSnapshot,
    classify_sessions,
    parse_pfctl_state_text,
)

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


def test_parse_pfctl_state_text_captures_the_pf_state_token():
    # pf's own connection state (e.g. "ESTABLISHED:ESTABLISHED") sits right
    # after dst on the header line -- previously parsed and discarded
    # entirely; now captured so callers can show it directly.
    text = (
        "tcp 192.168.1.10:1234 -> 192.168.1.20:445       TIME_WAIT:TIME_WAIT\n"
        "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
    )
    records = parse_pfctl_state_text(text)
    assert records[0]["state"] == "TIME_WAIT:TIME_WAIT"


def test_classify_sessions_orients_bytes_by_local_side():
    records = parse_pfctl_state_text(_load_fixture("pfctl_state_poll_1.txt"))
    snapshots = classify_sessions(records, LOCAL_SUBNETS)

    assert len(snapshots) == 2
    by_peer = {s.key.peer_ip: s for s in snapshots}

    tcp_snap = by_peer["93.184.216.34"]
    assert tcp_snap.key.local_ip == "192.168.1.50"
    assert tcp_snap.key.local_port == 52341
    assert tcp_snap.key.peer_port == 443
    assert tcp_snap.bytes_out == 9843
    assert tcp_snap.bytes_in == 1420
    assert tcp_snap.pkts_out == 14
    assert tcp_snap.pkts_in == 10
    assert tcp_snap.age_s == 12
    assert tcp_snap.peer_is_local is False

    udp_snap = by_peer["8.8.8.8"]
    assert udp_snap.key.local_ip == "192.168.1.60"
    assert udp_snap.bytes_out == 128
    assert udp_snap.bytes_in == 256


def test_classify_sessions_carries_pf_state_through_to_the_snapshot():
    text = (
        "tcp 192.168.1.10:1234 -> 93.184.216.34:443       FIN_WAIT_2:CLOSE_WAIT\n"
        "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
    )
    records = parse_pfctl_state_text(text)
    snapshots = classify_sessions(records, LOCAL_SUBNETS)
    assert snapshots[0].state == "FIN_WAIT_2:CLOSE_WAIT"


def test_classify_sessions_skips_states_with_no_local_side():
    # Neither endpoint falls in the configured local subnet -- e.g. the
    # firewall's own outbound traffic -- must not be reported at all.
    text = (
        "10.0.0.5:1234 -> 10.0.0.9:443       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
    ).replace("10.0.0.5:1234 -> 10.0.0.9:443", "tcp 10.0.0.5:1234 -> 10.0.0.9:443")
    records = parse_pfctl_state_text(text)
    snapshots = classify_sessions(records, LOCAL_SUBNETS)
    assert snapshots == []


def test_classify_sessions_keeps_both_local_states_as_an_internal_pair():
    # Both endpoints local (e.g. two devices on different VLANs/subnets
    # routed through the firewall) is no longer discarded -- it's kept,
    # uncanonicalized (local_ip = whichever side pf called src), with
    # peer_is_local=True marking it as a local<->local pair rather than
    # local<->internet.
    text = (
        "tcp 192.168.1.10:1234 -> 192.168.1.20:445       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 100s, 3:4 pkts, 300:400 bytes, rule 1\n"
    )
    records = parse_pfctl_state_text(text)
    snapshots = classify_sessions(records, LOCAL_SUBNETS)
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.key == StateKey("tcp", "192.168.1.10", 1234, "192.168.1.20", 445)
    assert snap.peer_is_local is True
    assert snap.bytes_out == 300
    assert snap.bytes_in == 400
    assert snap.pkts_out == 3
    assert snap.pkts_in == 4


def test_classify_sessions_treats_hairpin_self_traffic_as_a_degenerate_pair():
    # Pins down current behavior for src_ip == dst_ip (e.g. a hairpin NAT
    # state) rather than leaving it undefined -- both sides are "local" by
    # the subnet check, so it's reported as a peer_is_local pair with
    # local_ip == peer_ip instead of being silently dropped.
    text = (
        "tcp 192.168.1.10:1234 -> 192.168.1.10:445       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
    )
    records = parse_pfctl_state_text(text)
    snapshots = classify_sessions(records, LOCAL_SUBNETS)
    assert len(snapshots) == 1
    assert snapshots[0].key.local_ip == snapshots[0].key.peer_ip == "192.168.1.10"
    assert snapshots[0].peer_is_local is True


def test_poller_reports_both_local_pair_open_update_close_across_polls():
    poller = PfStatePoller(LOCAL_SUBNETS)

    poll_1 = (
        "tcp 192.168.1.10:1234 -> 192.168.1.20:445       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 100s, 1:1 pkts, 100:100 bytes, rule 1\n"
        "tcp 192.168.1.30:5000 -> 192.168.1.40:22       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 100s, 2:2 pkts, 200:200 bytes, rule 1\n"
    )
    result_1 = poller.poll(poll_1)
    assert len(result_1.opened) == 2
    assert all(s.peer_is_local for s in result_1.opened)
    assert result_1.updated == []
    assert result_1.closed == []

    # 192.168.1.30<->192.168.1.40 vanished (closed); 192.168.1.10<->.20
    # persisted with higher counters; a brand new pair appeared.
    poll_2 = (
        "tcp 192.168.1.10:1234 -> 192.168.1.20:445       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:06, expires in 100s, 10:10 pkts, 1000:1000 bytes, rule 1\n"
        "tcp 192.168.1.50:6000 -> 192.168.1.60:80       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 100s, 1:1 pkts, 50:60 bytes, rule 1\n"
    )
    result_2 = poller.poll(poll_2)

    assert len(result_2.closed) == 1
    closed = result_2.closed[0]
    assert closed.key == StateKey("tcp", "192.168.1.30", 5000, "192.168.1.40", 22)
    assert closed.bytes_out == 200

    assert len(result_2.updated) == 1
    updated = result_2.updated[0]
    assert updated.key.local_ip == "192.168.1.10"
    assert updated.bytes_out == 1000
    assert updated.bytes_in == 1000

    assert len(result_2.opened) == 1
    opened = result_2.opened[0]
    assert opened.key == StateKey("tcp", "192.168.1.50", 6000, "192.168.1.60", 80)
    assert opened.peer_is_local is True


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
    assert closed.key.peer_ip == "8.8.8.8"
    assert closed.bytes_out == 128
    assert closed.bytes_in == 256

    # The 93.184.216.34 TCP state persisted with higher cumulative counters.
    assert len(result_2.updated) == 1
    updated = result_2.updated[0]
    assert updated.key.peer_ip == "93.184.216.34"
    assert updated.bytes_out == 25000
    assert updated.bytes_in == 3100
    assert updated.pkts_out == 30
    assert updated.pkts_in == 22

    # A brand new state to 151.101.1.140 appeared.
    assert len(result_2.opened) == 1
    opened = result_2.opened[0]
    assert opened.key.peer_ip == "151.101.1.140"
    assert opened.key.local_ip == "192.168.1.71"
    assert opened.bytes_out == 1200
    assert opened.bytes_in == 800


def test_seed_lets_first_poll_after_restart_close_out_gone_sessions():
    # Regression test for a real bug found on a live production box: every
    # daemon restart starts _prev empty, so a session that was open before
    # the restart and had genuinely closed for real (in pf) by the time
    # polling resumed was invisible to poll()'s diff -- missing from
    # `current`, but with nothing in `_prev` to compare against, it was
    # never reported as closed either. It just stayed in live_sessions
    # forever, since nothing else ever removed it. seed() (called with
    # whatever was persisted in live_sessions at daemon startup) fixes
    # this by giving the first real poll something to diff against.
    #
    # Covers both an ordinary local<->internet stale session AND a
    # local<->local (peer_is_local=True) one in the same test -- since
    # v1.1.0's separate Internal Traffic pipeline merged into this one,
    # the restart-safety fix needs to hold for both shapes through the
    # same seed()/poll() mechanism.
    stale_key = StateKey("tcp", "192.168.1.99", 54321, "203.0.113.5", 443)
    stale_snapshot = StateSnapshot(
        key=stale_key, bytes_out=4075, bytes_in=1361, pkts_out=12, pkts_in=13, age_s=0
    )
    stale_internal_key = StateKey("tcp", "192.168.1.77", 4321, "192.168.1.88", 8080)
    stale_internal_snapshot = StateSnapshot(
        key=stale_internal_key, bytes_out=500, bytes_in=700, pkts_out=5, pkts_in=7,
        age_s=0, peer_is_local=True,
    )

    poller = PfStatePoller(LOCAL_SUBNETS)
    poller.seed([stale_snapshot, stale_internal_snapshot])

    # pfctl_state_poll_1.txt contains no state at all for either stale
    # session -- simulating that both genuinely closed while the daemon
    # was down.
    result = poller.poll(_load_fixture("pfctl_state_poll_1.txt"))

    assert len(result.closed) == 2
    closed_by_key = {s.key: s for s in result.closed}
    assert closed_by_key[stale_key].bytes_out == 4075
    assert closed_by_key[stale_key].bytes_in == 1361
    assert closed_by_key[stale_internal_key].bytes_out == 500
    assert closed_by_key[stale_internal_key].bytes_in == 700
    assert closed_by_key[stale_internal_key].peer_is_local is True

    # The fixture's own two real states are still correctly new opens,
    # not swallowed by the seeded entries.
    assert len(result.opened) == 2
    assert result.updated == []


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


def test_real_capture_admin_plane_traffic_to_opnsense_itself_is_captured_as_internal():
    # Real capture: a LAN client (10.0.0.9) reaching OPNsense's own LAN IP
    # (10.0.0.1) for SSH/HTTPS admin access or DNS -- both endpoints match
    # the local subnet. Previously excluded entirely (this was the
    # remote-tracking pipeline's job, and this isn't a local-to-internet
    # flow); now captured as a peer_is_local=True session instead, same as
    # any other local<->local pair -- not new *behavior* (v1.1.0's
    # Internal Traffic pipeline already captured this), just no longer a
    # second, separate pipeline's job.
    text = (
        "all tcp 10.0.0.1:443 <- 10.0.0.9:50843       ESTABLISHED:ESTABLISHED\n"
        "   [2723053261 + 2102272] wscale 7  [3974263217 + 65792] wscale 8\n"
        "   age 00:01:24, expires in 23:59:59, 809:1380 pkts, "
        "45698:1647388 bytes, rule 87\n"
        "   id: 7193896a00000000 creatorid: ea8fae34\n"
        "   origif: le0\n"
    )
    records = parse_pfctl_state_text(text)
    snapshots = classify_sessions(records, ["10.0.0.0/24"])
    assert len(snapshots) == 1
    assert snapshots[0].peer_is_local is True


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

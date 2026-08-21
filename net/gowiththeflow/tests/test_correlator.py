import correlator
import db
import hostcache
from pf_state_poller import PfStatePoller
from sni_sniffer import FlowHintCache

LOCAL_SUBNETS = ["192.168.1.0/24"]


def _fresh_conn(tmp_path):
    conn = db.connect(str(tmp_path / "flows.db"))
    db.init_schema(conn)
    return conn


def test_static_override_wins_over_sni_hint_and_hostcache(tmp_path):
    conn = _fresh_conn(tmp_path)
    now = 1000
    hostcache.upsert_hostname(conn, "93.184.216.34", "dns-name.example", "dns", 300, now)
    flow_hints = FlowHintCache()
    flow_hints.put("192.168.1.50", 52341, "93.184.216.34", 443, "sni-name.example", now)
    overrides = correlator.parse_static_overrides([("93.184.216.34/32", "my-override.local")])

    hostname, source = correlator.resolve_remote_hostname(
        conn, "192.168.1.50", 52341, "93.184.216.34", 443, overrides, flow_hints, now
    )
    assert (hostname, source) == ("my-override.local", "static")


def test_static_override_matches_a_cidr_range():
    overrides = correlator.parse_static_overrides([("10.0.0.0/8", "internal-vpn-peer")])
    hostname, source = correlator.resolve_remote_hostname(
        db.connect(":memory:"), "192.168.1.50", 1, "10.1.2.3", 443, overrides, FlowHintCache(), 1000
    )
    assert (hostname, source) == ("internal-vpn-peer", "static")


def test_live_sni_hint_wins_over_a_stale_but_still_valid_dns_cache_entry(tmp_path):
    # The exact "SNI vs. stale DNS cache" conflict point called out in the
    # project plan: DNS resolved this IP a while ago to one name, but the
    # live flow just presented a different SNI (e.g. a shared-IP CDN) --
    # the fresher, flow-specific signal must win.
    conn = _fresh_conn(tmp_path)
    now = 1000
    hostcache.upsert_hostname(conn, "203.0.113.9", "old-dns-name.example", "dns", 3600, now)
    flow_hints = FlowHintCache()
    flow_hints.put("192.168.1.50", 52341, "203.0.113.9", 443, "fresh-sni-name.example", now)

    hostname, source = correlator.resolve_remote_hostname(
        conn, "192.168.1.50", 52341, "203.0.113.9", 443, [], flow_hints, now
    )
    assert (hostname, source) == ("fresh-sni-name.example", "sni")


def test_falls_back_to_hostcache_when_no_override_or_live_hint(tmp_path):
    conn = _fresh_conn(tmp_path)
    now = 1000
    hostcache.upsert_hostname(conn, "93.184.216.34", "example.com", "dns", 300, now)

    hostname, source = correlator.resolve_remote_hostname(
        conn, "192.168.1.50", 52341, "93.184.216.34", 443, [], FlowHintCache(), now
    )
    assert (hostname, source) == ("example.com", "dns")


def test_falls_through_to_none_when_nothing_resolved(tmp_path):
    conn = _fresh_conn(tmp_path)
    hostname, source = correlator.resolve_remote_hostname(
        conn, "192.168.1.50", 52341, "198.51.100.1", 443, [], FlowHintCache(), 1000
    )
    assert (hostname, source) == (None, None)


def test_end_to_end_pf_state_plus_sni_hint_lands_a_resolved_hostname_in_the_db(tmp_path):
    """Ties together A1 (pf_state_poller), A2 (db), A5 (sni_sniffer's
    FlowHintCache) and A7 (correlator) -- the first point in the daemon
    where everything actually combines."""
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)
    flow_hints = FlowHintCache()
    now = 1000

    # The SNI sniffer saw this flow's ClientHello moments before pf's state
    # table reflects the new connection.
    flow_hints.put("192.168.1.50", 52341, "93.184.216.34", 443, "example.com", now)

    diff = poller.poll(
        "tcp 192.168.1.50:52341 -> 93.184.216.34:443       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 86399s, 2:1 pkts, 500:200 bytes, rule 8\n"
    )
    resolver = correlator.make_resolver(conn, [], flow_hints, now)
    db.record_diff(conn, diff, now=now, resolve_hostname=resolver)

    row = conn.execute("SELECT * FROM live_sessions").fetchone()
    assert row["remote_hostname"] == "example.com"
    assert row["hostname_source"] == "sni"


def test_hostname_is_not_blanked_out_when_the_sni_hint_expires_between_polls(tmp_path):
    """A live session's displayed hostname must not flicker back to a bare
    IP just because the short-lived SNI hint expired before the durable
    hostcache had a chance to take over -- db.record_diff's COALESCE
    handling must preserve the previously-resolved value."""
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)
    flow_hints = FlowHintCache()
    now1 = 1000
    flow_hints.put("192.168.1.50", 52341, "93.184.216.34", 443, "example.com", now1)

    diff1 = poller.poll(
        "tcp 192.168.1.50:52341 -> 93.184.216.34:443       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 86399s, 2:1 pkts, 500:200 bytes, rule 8\n"
    )
    db.record_diff(conn, diff1, now=now1, resolve_hostname=correlator.make_resolver(conn, [], flow_hints, now1))

    # Second poll: the SNI hint has expired (>60s later) and DNS never
    # observed this IP either -- resolver now returns (None, None).
    now2 = now1 + 120
    diff2 = poller.poll(
        "tcp 192.168.1.50:52341 -> 93.184.216.34:443       ESTABLISHED:ESTABLISHED\n"
        "   age 00:02:01, expires in 86279s, 20:15 pkts, 9000:4000 bytes, rule 8\n"
    )
    db.record_diff(conn, diff2, now=now2, resolve_hostname=correlator.make_resolver(conn, [], flow_hints, now2))

    row = conn.execute("SELECT * FROM live_sessions").fetchone()
    assert row["remote_hostname"] == "example.com"  # preserved, not blanked
    assert row["bytes_in"] == 4000  # counters still updated normally

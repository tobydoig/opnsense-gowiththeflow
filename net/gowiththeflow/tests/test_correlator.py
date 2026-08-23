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

    hostname, source, category = correlator.resolve_remote_hostname(
        conn, "192.168.1.50", 52341, "93.184.216.34", 443, overrides, flow_hints, now
    )
    assert (hostname, source, category) == ("my-override.local", "static", None)


def test_static_override_matches_a_cidr_range():
    overrides = correlator.parse_static_overrides([("10.0.0.0/8", "internal-vpn-peer")])
    hostname, source, category = correlator.resolve_remote_hostname(
        db.connect(":memory:"), "192.168.1.50", 1, "10.1.2.3", 443, overrides, FlowHintCache(), 1000
    )
    assert (hostname, source, category) == ("internal-vpn-peer", "static", None)


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

    hostname, source, category = correlator.resolve_remote_hostname(
        conn, "192.168.1.50", 52341, "203.0.113.9", 443, [], flow_hints, now
    )
    assert (hostname, source, category) == ("fresh-sni-name.example", "sni", None)


def test_falls_back_to_hostcache_when_no_override_or_live_hint(tmp_path):
    conn = _fresh_conn(tmp_path)
    now = 1000
    hostcache.upsert_hostname(conn, "93.184.216.34", "example.com", "dns", 300, now)

    hostname, source, category = correlator.resolve_remote_hostname(
        conn, "192.168.1.50", 52341, "93.184.216.34", 443, [], FlowHintCache(), now
    )
    assert (hostname, source, category) == ("example.com", "dns", None)


def test_falls_through_to_none_when_nothing_resolved(tmp_path):
    conn = _fresh_conn(tmp_path)
    hostname, source, category = correlator.resolve_remote_hostname(
        conn, "192.168.1.50", 52341, "198.51.100.1", 443, [], FlowHintCache(), 1000
    )
    assert (hostname, source, category) == (None, None, None)


def test_categorize_fn_is_applied_regardless_of_which_source_resolved_the_hostname(tmp_path):
    conn = _fresh_conn(tmp_path)
    now = 1000
    categorize_fn = lambda hostname: "Streaming/Video" if "netflix" in hostname else None

    # via static override
    overrides = correlator.parse_static_overrides([("93.184.216.34/32", "netflix.com")])
    _, _, category = correlator.resolve_remote_hostname(
        conn, "192.168.1.50", 1, "93.184.216.34", 443, overrides, FlowHintCache(), now, categorize_fn
    )
    assert category == "Streaming/Video"

    # via a live SNI hint
    flow_hints = FlowHintCache()
    flow_hints.put("192.168.1.50", 2, "203.0.113.1", 443, "www.netflix.com", now)
    _, _, category = correlator.resolve_remote_hostname(
        conn, "192.168.1.50", 2, "203.0.113.1", 443, [], flow_hints, now, categorize_fn
    )
    assert category == "Streaming/Video"

    # via the durable hostcache
    hostcache.upsert_hostname(conn, "198.51.100.5", "nflxvideo.example", "dns", 300, now)
    _, _, category = correlator.resolve_remote_hostname(
        conn, "192.168.1.50", 3, "198.51.100.5", 443, [], FlowHintCache(), now, categorize_fn
    )
    assert category is None  # "nflxvideo.example" doesn't contain "netflix" -- honest negative

    # a hostname the categorize_fn doesn't recognize resolves to no category, not an error
    hostcache.upsert_hostname(conn, "198.51.100.6", "unrelated.example", "dns", 300, now)
    _, _, category = correlator.resolve_remote_hostname(
        conn, "192.168.1.50", 4, "198.51.100.6", 443, [], FlowHintCache(), now, categorize_fn
    )
    assert category is None


def test_categorize_fn_result_lands_in_the_db_via_make_resolver(tmp_path):
    conn = _fresh_conn(tmp_path)
    poller = PfStatePoller(LOCAL_SUBNETS)
    flow_hints = FlowHintCache()
    now = 1000
    flow_hints.put("192.168.1.50", 52341, "93.184.216.34", 443, "netflix.com", now)

    diff = poller.poll(
        "tcp 192.168.1.50:52341 -> 93.184.216.34:443       ESTABLISHED:ESTABLISHED\n"
        "   age 00:00:01, expires in 86399s, 2:1 pkts, 500:200 bytes, rule 8\n"
    )
    resolver = correlator.make_resolver(
        conn, [], flow_hints, now, categorize_fn=lambda h: "Streaming/Video" if h == "netflix.com" else None
    )
    db.record_diff(conn, diff, now=now, resolve_hostname=resolver)

    row = conn.execute("SELECT * FROM live_sessions").fetchone()
    assert row["category"] == "Streaming/Video"


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

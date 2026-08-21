import db
import hostcache


def _fresh_conn(tmp_path):
    conn = db.connect(str(tmp_path / "flows.db"))
    db.init_schema(conn)
    return conn


def test_first_observation_is_stored_as_is(tmp_path):
    conn = _fresh_conn(tmp_path)
    hostcache.upsert_hostname(conn, "93.184.216.34", "example.com", "dns", 300, now=1000)
    assert hostcache.get_hostname(conn, "93.184.216.34", now=1100) == ("example.com", "dns")


def test_entry_expires_after_its_ttl(tmp_path):
    conn = _fresh_conn(tmp_path)
    hostcache.upsert_hostname(conn, "93.184.216.34", "example.com", "dns", 300, now=1000)
    assert hostcache.get_hostname(conn, "93.184.216.34", now=1000 + 300 - 1) == ("example.com", "dns")
    assert hostcache.get_hostname(conn, "93.184.216.34", now=1000 + 300 + 1) == (None, None)


def test_missing_ip_returns_none(tmp_path):
    conn = _fresh_conn(tmp_path)
    assert hostcache.get_hostname(conn, "1.2.3.4", now=1000) == (None, None)


def test_sni_does_not_override_a_still_valid_dns_entry(tmp_path):
    conn = _fresh_conn(tmp_path)
    hostcache.upsert_hostname(conn, "203.0.113.9", "dns-name.example", "dns", 300, now=1000)
    hostcache.upsert_hostname(conn, "203.0.113.9", "sni-name.example", "sni", 3600, now=1010)

    assert hostcache.get_hostname(conn, "203.0.113.9", now=1020) == ("dns-name.example", "dns")


def test_lower_priority_source_fills_gap_once_higher_priority_entry_expires(tmp_path):
    conn = _fresh_conn(tmp_path)
    hostcache.upsert_hostname(conn, "203.0.113.9", "dns-name.example", "dns", 100, now=1000)
    # DNS entry expires at 1100. A PTR observation just after that should
    # now be allowed to fill the gap.
    hostcache.upsert_hostname(conn, "203.0.113.9", "ptr-name.example", "ptr", 3600, now=1150)

    assert hostcache.get_hostname(conn, "203.0.113.9", now=1200) == ("ptr-name.example", "ptr")


def test_higher_priority_source_overrides_a_lower_priority_entry_immediately(tmp_path):
    conn = _fresh_conn(tmp_path)
    hostcache.upsert_hostname(conn, "203.0.113.9", "ptr-name.example", "ptr", 3600, now=1000)
    hostcache.upsert_hostname(conn, "203.0.113.9", "dns-name.example", "dns", 300, now=1010)

    assert hostcache.get_hostname(conn, "203.0.113.9", now=1020) == ("dns-name.example", "dns")


def test_same_tier_conflicting_hostname_flags_ambiguous_and_is_excluded(tmp_path):
    conn = _fresh_conn(tmp_path)
    hostcache.upsert_hostname(conn, "203.0.113.9", "a.example.com", "sni", 3600, now=1000)
    hostcache.upsert_hostname(conn, "203.0.113.9", "b.example.net", "sni", 3600, now=1010)

    # Shared-IP-CDN scenario: the generic cache can no longer vouch for
    # this IP once two distinct SNI hostnames have shown up for it.
    assert hostcache.get_hostname(conn, "203.0.113.9", now=1020) == (None, None)


def test_same_tier_matching_hostname_just_refreshes_ttl_and_hit_count(tmp_path):
    conn = _fresh_conn(tmp_path)
    hostcache.upsert_hostname(conn, "93.184.216.34", "example.com", "dns", 300, now=1000)
    hostcache.upsert_hostname(conn, "93.184.216.34", "example.com", "dns", 300, now=1200)

    row = conn.execute(
        "SELECT * FROM ip_hostname_cache WHERE ip=?", ("93.184.216.34",)
    ).fetchone()
    assert row["hit_count"] == 2
    assert row["ambiguous"] == 0
    assert row["ttl_expires_at"] == 1200 + 300

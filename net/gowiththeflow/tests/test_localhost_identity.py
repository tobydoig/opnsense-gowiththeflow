import os

import db
from localhost_identity import (
    LocalHostIdentity,
    merge_identities,
    parse_arp_output,
    parse_leases_json,
    write_identities,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


def test_parse_leases_json_extracts_hostname_ip_and_source():
    identities = parse_leases_json(_load_fixture("dnsmasq_leases.json"))
    by_mac = {i.mac: i for i in identities}
    assert len(identities) == 3

    laptop = by_mac["aa:bb:cc:dd:ee:01"]
    assert laptop.ip == "192.168.1.50"
    assert laptop.hostname == "laptop"
    assert laptop.source == "dhcp_lease"

    nas = by_mac["aa:bb:cc:dd:ee:03"]
    assert nas.hostname == "nas"
    assert nas.source == "dhcp_lease"


def test_parse_leases_json_treats_dnsmasq_wildcard_hostname_as_unknown():
    identities = parse_leases_json(_load_fixture("dnsmasq_leases.json"))
    by_mac = {i.mac: i for i in identities}
    assert by_mac["aa:bb:cc:dd:ee:02"].hostname is None


def test_parse_leases_json_skips_records_without_a_mac():
    raw = '{"records": [{"address": "192.168.1.5", "hostname": "x"}]}'
    assert parse_leases_json(raw) == []


def test_parse_leases_json_handles_the_real_empty_response():
    # Verbatim output from `configctl dnsmasq list leases` on an OPNsense
    # 26.7 test VM with no active leases yet.
    assert parse_leases_json('{"records":[]}') == []


def test_parse_arp_output_extracts_ip_mac_pairs_and_skips_incomplete():
    identities = parse_arp_output(_load_fixture("arp_an_sample.txt"))
    by_mac = {i.mac: i for i in identities}
    assert set(by_mac) == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:99"}
    assert by_mac["aa:bb:cc:dd:ee:99"].ip == "192.168.1.99"
    assert by_mac["aa:bb:cc:dd:ee:99"].hostname is None
    assert by_mac["aa:bb:cc:dd:ee:99"].source == "arp"


def test_merge_identities_prefers_lease_data_over_arp_for_the_same_mac():
    leases = [
        LocalHostIdentity(mac="aa:bb:cc:dd:ee:01", ip="192.168.1.50", hostname="laptop", source="dhcp_lease")
    ]
    arp = [
        LocalHostIdentity(mac="aa:bb:cc:dd:ee:01", ip="192.168.1.50", hostname=None, source="arp"),
        LocalHostIdentity(mac="aa:bb:cc:dd:ee:99", ip="192.168.1.99", hostname=None, source="arp"),
    ]
    merged = merge_identities(leases, arp)
    assert merged["aa:bb:cc:dd:ee:01"].source == "dhcp_lease"
    assert merged["aa:bb:cc:dd:ee:01"].hostname == "laptop"
    # A device with no lease at all is still picked up via ARP.
    assert merged["aa:bb:cc:dd:ee:99"].source == "arp"


def test_write_identities_populates_local_host_identity_table(tmp_path):
    conn = db.connect(str(tmp_path / "flows.db"))
    db.init_schema(conn)

    merged = merge_identities(
        parse_leases_json(_load_fixture("dnsmasq_leases.json")),
        parse_arp_output(_load_fixture("arp_an_sample.txt")),
    )
    write_identities(conn, merged, now=1_000_000)

    rows = {r["mac"]: r for r in conn.execute("SELECT * FROM local_host_identity")}
    assert rows["aa:bb:cc:dd:ee:01"]["hostname"] == "laptop"
    assert rows["aa:bb:cc:dd:ee:99"]["source"] == "arp"
    assert rows["aa:bb:cc:dd:ee:01"]["updated_at"] == 1_000_000


def test_write_identities_upserts_on_second_call(tmp_path):
    conn = db.connect(str(tmp_path / "flows.db"))
    db.init_schema(conn)

    first = {
        "aa:bb:cc:dd:ee:01": LocalHostIdentity(
            mac="aa:bb:cc:dd:ee:01", ip="192.168.1.50", hostname="old-name", source="dhcp_lease"
        )
    }
    write_identities(conn, first, now=1000)

    second = {
        "aa:bb:cc:dd:ee:01": LocalHostIdentity(
            mac="aa:bb:cc:dd:ee:01", ip="192.168.1.51", hostname="new-name", source="dhcp_lease"
        )
    }
    write_identities(conn, second, now=2000)

    row = conn.execute(
        "SELECT * FROM local_host_identity WHERE mac=?", ("aa:bb:cc:dd:ee:01",)
    ).fetchone()
    assert row["hostname"] == "new-name"
    assert row["ip"] == "192.168.1.51"
    assert row["updated_at"] == 2000
    assert conn.execute("SELECT COUNT(*) FROM local_host_identity").fetchone()[0] == 1

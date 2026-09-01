from scapy.layers.dns import DNS, DNSQR, DNSRR
from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.inet6 import IPv6

from dns_sniffer import clamp_ttl, extract_observations, extract_query_events

SEEN_AT = 1_000_000


def _dns_response(qname="example.com", answers=None, rcode=0, qr=1, qtype=1, dst="192.168.1.50"):
    an = [
        DNSRR(rrname=a["name"], type=a["type"], ttl=a["ttl"], rdata=a["ip"])
        for a in (answers or [])
    ]
    return (
        IP(src="8.8.8.8", dst=dst)
        / UDP(sport=53, dport=51234)
        / DNS(
            id=1,
            qr=qr,
            rcode=rcode,
            qd=[DNSQR(qname=qname, qtype=qtype)],
            an=an,
            ancount=len(an),
        )
    )


def _real_roundtrip(pkt):
    # Matches how a packet is actually seen off the wire (and how
    # sniff_loop() itself re-parses captured bytes) -- some field values
    # (e.g. name-shaped rdata coming back as bytes rather than the str a
    # freshly-constructed scapy object holds) only appear after a real
    # serialize/reparse round-trip, not on the in-memory object as built.
    return IP(bytes(pkt))


def test_extracts_a_record_answer_attributed_to_queried_name():
    pkt = _dns_response(
        qname="example.com",
        answers=[{"name": "example.com", "type": "A", "ttl": 300, "ip": "93.184.216.34"}],
    )
    observations = extract_observations(pkt, SEEN_AT)
    assert len(observations) == 1
    obs = observations[0]
    assert obs.ip == "93.184.216.34"
    assert obs.hostname == "example.com"
    assert obs.ttl == 300
    assert obs.seen_at == SEEN_AT


def test_extracts_aaaa_record():
    pkt = _dns_response(
        qname="example.com",
        answers=[{"name": "example.com", "type": "AAAA", "ttl": 120, "ip": "2606:2800:220:1::248"}],
    )
    observations = extract_observations(pkt, SEEN_AT)
    assert len(observations) == 1
    assert observations[0].ip == "2606:2800:220:1::248"


def test_multiple_answers_all_attributed_to_the_queried_name():
    pkt = _dns_response(
        qname="example.com",
        answers=[
            {"name": "example.com", "type": "A", "ttl": 300, "ip": "93.184.216.34"},
            {"name": "example.com", "type": "A", "ttl": 300, "ip": "93.184.216.35"},
        ],
    )
    observations = extract_observations(pkt, SEEN_AT)
    assert {o.ip for o in observations} == {"93.184.216.34", "93.184.216.35"}
    assert all(o.hostname == "example.com" for o in observations)


def test_ttl_is_clamped_to_configured_bounds():
    assert clamp_ttl(5) == 60
    assert clamp_ttl(300) == 300
    assert clamp_ttl(999_999) == 24 * 3600

    pkt = _dns_response(
        answers=[{"name": "example.com", "type": "A", "ttl": 5, "ip": "93.184.216.34"}],
    )
    assert extract_observations(pkt, SEEN_AT)[0].ttl == 60


def test_ignores_dns_queries():
    pkt = _dns_response(qr=0, answers=[])
    assert extract_observations(pkt, SEEN_AT) == []


def test_ignores_non_success_rcodes():
    pkt = _dns_response(
        rcode=3,  # NXDOMAIN
        answers=[{"name": "example.com", "type": "A", "ttl": 300, "ip": "93.184.216.34"}],
    )
    assert extract_observations(pkt, SEEN_AT) == []


def test_ignores_non_dns_packets():
    pkt = IP(src="1.2.3.4", dst="192.168.1.50") / TCP(sport=443, dport=54321)
    assert extract_observations(pkt, SEEN_AT) == []


def test_works_against_packets_read_back_from_a_real_pcap_file(tmp_path):
    from scapy.utils import rdpcap, wrpcap

    pkt = _dns_response(
        qname="example.com",
        answers=[{"name": "example.com", "type": "A", "ttl": 300, "ip": "93.184.216.34"}],
    )
    pcap_path = tmp_path / "dns_capture.pcap"
    wrpcap(str(pcap_path), [pkt])

    packets = rdpcap(str(pcap_path))
    assert len(packets) == 1
    observations = extract_observations(packets[0], SEEN_AT)
    assert len(observations) == 1
    assert observations[0].ip == "93.184.216.34"
    assert observations[0].hostname == "example.com"


def test_query_event_captures_a_successful_lookup():
    pkt = _real_roundtrip(_dns_response(
        qname="example.com",
        answers=[{"name": "example.com", "type": "A", "ttl": 300, "ip": "93.184.216.34"}],
    ))
    event = extract_query_events(pkt, SEEN_AT)
    assert event.local_ip == "192.168.1.50"
    assert event.query_name == "example.com"
    assert event.query_type == "A"
    assert event.rcode == "NOERROR"
    assert event.answers == "A:93.184.216.34"
    assert event.seen_at == SEEN_AT


def test_query_event_uses_conventional_rcode_names_not_scapys_own_wording():
    pkt = _real_roundtrip(_dns_response(qname="nonexistent.example.com", rcode=3, answers=[]))
    event = extract_query_events(pkt, SEEN_AT)
    assert event.rcode == "NXDOMAIN"
    assert event.answers is None


def test_query_event_captures_servfail():
    pkt = _real_roundtrip(_dns_response(qname="broken.example.com", rcode=2, answers=[]))
    event = extract_query_events(pkt, SEEN_AT)
    assert event.rcode == "SERVFAIL"


def test_query_event_captures_query_type_other_than_a():
    pkt = _real_roundtrip(_dns_response(qname="example.com", qtype=28, answers=[]))
    event = extract_query_events(pkt, SEEN_AT)
    assert event.query_type == "AAAA"


def test_query_event_captures_mixed_answer_types_in_order():
    # A real CNAME-chain response -- confirmed live that name-shaped
    # rdata (CNAME here) comes back from scapy as bytes with a trailing
    # dot only after a genuine serialize/reparse round-trip, unlike a
    # freshly-constructed object held in memory.
    pkt = _real_roundtrip(_dns_response(
        qname="www.example.com",
        answers=[
            {"name": "www.example.com", "type": "CNAME", "ttl": 300, "ip": "example.com"},
            {"name": "example.com", "type": "A", "ttl": 300, "ip": "93.184.216.34"},
        ],
    ))
    event = extract_query_events(pkt, SEEN_AT)
    assert event.answers == "CNAME:example.com,A:93.184.216.34"


def test_query_event_truncates_a_pathologically_large_answer_set():
    answers = [
        {"name": "example.com", "type": "A", "ttl": 300, "ip": f"10.0.0.{i}"}
        for i in range(25)
    ]
    pkt = _real_roundtrip(_dns_response(qname="example.com", answers=answers))
    event = extract_query_events(pkt, SEEN_AT)
    assert event.answers.count(",") == 20  # 20 shown + the "+N more" marker
    assert event.answers.endswith("+5 more")


def test_query_event_returns_none_for_queries_not_responses():
    pkt = _real_roundtrip(_dns_response(qr=0, answers=[]))
    assert extract_query_events(pkt, SEEN_AT) is None


def test_query_event_returns_none_for_non_dns_packets():
    pkt = IP(src="1.2.3.4", dst="192.168.1.50") / TCP(sport=443, dport=54321)
    assert extract_query_events(pkt, SEEN_AT) is None


def test_query_event_works_over_ipv6():
    pkt = (
        IPv6(src="2001:4860:4860::8888", dst="fe80::1")
        / UDP(sport=53, dport=51234)
        / DNS(
            id=1, qr=1, rcode=0,
            qd=[DNSQR(qname="example.com", qtype=1)],
            an=[DNSRR(rrname="example.com", type=1, ttl=300, rdata="93.184.216.34")],
            ancount=1,
        )
    )
    event = extract_query_events(IPv6(bytes(pkt)), SEEN_AT)
    assert event.local_ip == "fe80::1"

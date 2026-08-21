from scapy.layers.dns import DNS, DNSQR, DNSRR
from scapy.layers.inet import IP, TCP, UDP

from dns_sniffer import clamp_ttl, extract_observations

SEEN_AT = 1_000_000


def _dns_response(qname="example.com", answers=None, rcode=0, qr=1):
    an = [
        DNSRR(rrname=a["name"], type=a["type"], ttl=a["ttl"], rdata=a["ip"])
        for a in (answers or [])
    ]
    return (
        IP(src="8.8.8.8", dst="192.168.1.50")
        / UDP(sport=53, dport=51234)
        / DNS(
            id=1,
            qr=qr,
            rcode=rcode,
            qd=[DNSQR(qname=qname)],
            an=an,
            ancount=len(an),
        )
    )


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

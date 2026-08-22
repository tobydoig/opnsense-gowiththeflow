from scapy.layers.inet import IP, TCP
from scapy.packet import Padding

from sni_sniffer import _tcp_payload_bytes


def _build_ip_tcp(payload: bytes = b"", pad: bytes = b""):
    pkt = IP(src="10.0.0.9", dst="10.0.0.1") / TCP(sport=12345, dport=443, flags="A") / payload
    if pad:
        pkt = pkt / Padding(load=pad)
    raw = bytes(pkt)  # forces field computation (ip.len, tcp.dataofs, ...)
    reparsed = IP(raw)
    return reparsed, reparsed[TCP]


def test_strips_ethernet_padding_from_a_bare_ack():
    # Real bug caught on the OPNsense 26.7 test VM: a bare ACK (no real
    # payload) gets Ethernet-padded to the minimum frame size, and scapy
    # dissects that padding into its own Padding layer -- naive
    # bytes(tcp.payload) picks it up anyway since .payload just means
    # "whatever the next layer is". Must come back empty, not 6 zero bytes.
    ip, tcp = _build_ip_tcp(payload=b"", pad=b"\x00" * 6)
    assert _tcp_payload_bytes(ip, tcp) == b""


def test_returns_real_payload_unaffected_when_there_is_no_padding():
    ip, tcp = _build_ip_tcp(payload=b"hello-world")
    assert _tcp_payload_bytes(ip, tcp) == b"hello-world"


def test_strips_padding_that_follows_a_real_payload():
    ip, tcp = _build_ip_tcp(payload=b"hello", pad=b"\x00" * 10)
    assert _tcp_payload_bytes(ip, tcp) == b"hello"

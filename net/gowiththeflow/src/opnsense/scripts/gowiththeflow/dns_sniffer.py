"""Extracts (ip, hostname, ttl) hostname observations from passively
captured DNS response traffic.

Stage A4: the parsing logic (extract_observations) is deliberately pure
and pcap/live-capture-agnostic, so it's exercised here against synthetic
and real pcap-file packets with no live capture involved. The live-capture
entrypoint (sniff_loop) is wired into gowiththeflowd.py and only proven
against real traffic in Phase B.
"""

from __future__ import annotations

from dataclasses import dataclass

from scapy.layers.dns import DNS
from scapy.packet import Packet

MIN_TTL = 60
MAX_TTL = 24 * 3600

_A = 1
_AAAA = 28


@dataclass(frozen=True)
class HostnameObservation:
    ip: str
    hostname: str
    ttl: int
    seen_at: int


def clamp_ttl(ttl: int) -> int:
    return max(MIN_TTL, min(ttl, MAX_TTL))


def _decode_name(name) -> str:
    if isinstance(name, bytes):
        name = name.decode("ascii", errors="replace")
    return name.rstrip(".")


def extract_observations(packet: Packet, seen_at: int) -> list[HostnameObservation]:
    """Extracts one HostnameObservation per A/AAAA answer record in a
    NOERROR DNS response, all attributed to the name the client actually
    queried (dns.qd.qname) -- not any intermediate CNAME target -- since
    that's the hostname a user would recognize. Returns [] for anything
    that isn't such a response (queries, non-DNS packets, NXDOMAIN/SERVFAIL,
    empty answer sections)."""
    if DNS not in packet:
        return []
    dns = packet[DNS]
    if dns.qr != 1 or dns.rcode != 0 or dns.ancount == 0 or not dns.qd:
        return []

    # scapy represents qd/an as PacketListField -- a list, even for the
    # historically-always-one query record.
    query_name = _decode_name(dns.qd[0].qname)
    observations = []
    for i in range(dns.ancount):
        rr = dns.an[i]
        if rr.type not in (_A, _AAAA):
            continue
        ip = rr.rdata if isinstance(rr.rdata, str) else str(rr.rdata)
        observations.append(
            HostnameObservation(
                ip=ip, hostname=query_name, ttl=clamp_ttl(int(rr.ttl)), seen_at=seen_at
            )
        )
    return observations


def sniff_loop(interfaces: list[str], on_observation, bpf_filter: str = "udp port 53 or tcp port 53") -> None:
    """Live capture entrypoint, wired up by gowiththeflowd.py. Not
    exercised by Stage A4's unit tests -- proven against real traffic in
    Phase B, once an actual OPNsense VM is involved."""
    import time

    from scapy.sendrecv import sniff

    def _handle(pkt: Packet) -> None:
        for obs in extract_observations(pkt, int(time.time())):
            on_observation(obs)

    sniff(iface=interfaces, filter=bpf_filter, prn=_handle, store=False)

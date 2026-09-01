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

from scapy.layers.dns import DNS, dnstypes
from scapy.layers.inet import IP
from scapy.layers.inet6 import IPv6
from scapy.packet import Packet

MIN_TTL = 60
MAX_TTL = 24 * 3600

_A = 1
_AAAA = 28

# RFC1035/2308 names, keyed by the raw numeric rcode -- deliberately not
# scapy's own rcode field i2s table, which uses its own wording
# ("name-error", "server-failure") rather than the conventional DNS
# terms ("NXDOMAIN", "SERVFAIL") a user actually recognizes.
_RCODE_NAMES = {
    0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN",
    4: "NOTIMP", 5: "REFUSED", 6: "YXDOMAIN", 7: "YXRRSET",
    8: "NXRRSET", 9: "NOTAUTH", 10: "NOTZONE",
}

# How many individual answer records get folded into one QueryEvent's
# `answers` string -- defends against a pathological large response
# (a real ANY-type query, or a long CNAME chain) producing one
# unreasonably wide value.
_MAX_ANSWERS_SHOWN = 20


def _rcode_name(rcode: int) -> str:
    return _RCODE_NAMES.get(rcode, f"RCODE{rcode}")


def _type_name(type_id: int) -> str:
    return dnstypes.get(type_id) or f"TYPE{type_id}"


@dataclass(frozen=True)
class HostnameObservation:
    ip: str
    hostname: str
    ttl: int
    seen_at: int


@dataclass(frozen=True)
class QueryEvent:
    local_ip: str
    query_name: str
    query_type: str
    rcode: str
    answers: str | None
    seen_at: int


def clamp_ttl(ttl: int) -> int:
    return max(MIN_TTL, min(ttl, MAX_TTL))


def _decode_name(name) -> str:
    if isinstance(name, bytes):
        name = name.decode("ascii", errors="replace")
    return name.rstrip(".")


def _decode_rdata(rdata) -> str:
    # Name-shaped rdata (CNAME/NS/PTR/...) comes back from scapy as
    # bytes, same as a qname; a plain address (A/AAAA) already comes
    # back as a str. Anything else (a record type this project has no
    # specific handling for) falls back to a raw str() rather than
    # crashing -- same defensiveness extract_observations() already
    # applies to A/AAAA's own rdata.
    if isinstance(rdata, bytes):
        return _decode_name(rdata)
    if isinstance(rdata, str):
        return rdata
    return str(rdata)


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


def extract_query_events(packet: Packet, seen_at: int) -> QueryEvent | None:
    """Extracts one QueryEvent per DNS response transaction -- unlike
    extract_observations(), this is NOT filtered to successful (NOERROR)
    A/AAAA-only responses: NXDOMAIN/SERVFAIL/etc. and every answer record
    type (CNAME, TXT, ...) are captured too, since "what got queried and
    what came back" is exactly what a query-log view needs to show,
    including failures. Returns None for anything that isn't a DNS
    response (queries, non-DNS packets) or that's missing an IP layer to
    attribute to a local host.

    local_ip is the packet's own destination address, not something
    separately captured from the outbound query -- a DNS response is
    addressed back to whoever asked, so this needs only the response
    packet already being sniffed here, the same one
    extract_observations() looks at."""
    if DNS not in packet:
        return None
    dns = packet[DNS]
    if dns.qr != 1 or not dns.qd:
        return None

    if IP in packet:
        local_ip = packet[IP].dst
    elif IPv6 in packet:
        local_ip = packet[IPv6].dst
    else:
        return None

    qd = dns.qd[0]
    query_name = _decode_name(qd.qname)
    query_type = _type_name(int(qd.qtype))

    answer_strs = []
    for i in range(dns.ancount):
        rr = dns.an[i]
        rtype = _type_name(int(rr.type))
        answer_strs.append(f"{rtype}:{_decode_rdata(rr.rdata)}")
    truncated = len(answer_strs) > _MAX_ANSWERS_SHOWN
    shown = answer_strs[:_MAX_ANSWERS_SHOWN]
    if truncated:
        shown.append(f"+{len(answer_strs) - _MAX_ANSWERS_SHOWN} more")
    answers = ",".join(shown) if shown else None

    return QueryEvent(
        local_ip=local_ip,
        query_name=query_name,
        query_type=query_type,
        rcode=_rcode_name(int(dns.rcode)),
        answers=answers,
        seen_at=seen_at,
    )


def sniff_loop(
    interfaces: list[str],
    on_observation,
    bpf_filter: str = "udp port 53 or tcp port 53",
    on_query_event=None,
) -> None:
    """Live capture entrypoint, wired up by gowiththeflowd.py. Not
    exercised by Stage A4's unit tests -- proven against real traffic in
    Phase B, once an actual OPNsense VM is involved.

    Confirmed on the OPNsense 26.7 test VM: scapy's sniff() can fail to
    auto-guess the capture datalink type depending on which scapy
    submodules happened to be imported first elsewhere in the process
    (observed: silently falls back to an undissected Raw packet, with a
    console warning, no exception) -- so this always explicitly re-parses
    the raw bytes as Ethernet itself rather than trusting sniff()'s own
    dissection to have succeeded."""
    import time

    from scapy.layers.l2 import Ether
    from scapy.sendrecv import sniff

    def _handle(pkt: Packet) -> None:
        eth = Ether(bytes(pkt))
        now_i = int(time.time())
        for obs in extract_observations(eth, now_i):
            on_observation(obs)
        # Same re-parsed packet feeds both extractors -- avoids a second
        # sniff()/Ether() reparse just to also log the raw query/response,
        # since on_query_event is optional (only set when
        # enable_dns_query_log is on).
        if on_query_event is not None:
            event = extract_query_events(eth, now_i)
            if event is not None:
                on_query_event(event)

    sniff(iface=interfaces, filter=bpf_filter, prn=_handle, store=False)

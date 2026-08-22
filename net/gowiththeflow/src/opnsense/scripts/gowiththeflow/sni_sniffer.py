"""Extracts the SNI (Server Name Indication) hostname from a TLS
ClientHello by parsing the plaintext handshake bytes directly -- no TLS
decryption is needed, since SNI is sent unencrypted in the ClientHello.

Stage A5: extract_sni() is pure bytes-in/hostname-out and is tested against
synthetic ClientHello byte sequences built by hand (no scapy TLS layer, to
avoid pulling in the `cryptography` dependency for something this small),
including a synthetic shared-IP-CDN scenario. The live-capture wiring
(sniff_loop) is only proven against real traffic in Phase B.
"""

from __future__ import annotations

import struct

_HANDSHAKE_RECORD_TYPE = 0x16
_CLIENT_HELLO_MSG_TYPE = 0x01
_SNI_EXTENSION_TYPE = 0x0000
_HOST_NAME_TYPE = 0x00

SNI_HINT_TTL_S = 60


def extract_sni(payload: bytes) -> str | None:
    """Returns the SNI hostname from a single, complete TLS ClientHello
    record starting at offset 0 of `payload`, or None if it's not a
    (complete, well-formed) ClientHello, or it has no SNI extension.
    Never raises -- malformed/truncated input just yields None, since a
    live sniffer must never crash on network junk."""
    try:
        return _extract_sni_unsafe(payload)
    except (struct.error, IndexError, UnicodeDecodeError):
        return None


def _extract_sni_unsafe(payload: bytes) -> str | None:
    if len(payload) < 5:
        return None
    record_type, _version, record_len = struct.unpack_from(">BHH", payload, 0)
    if record_type != _HANDSHAKE_RECORD_TYPE:
        return None
    if len(payload) < 5 + record_len:
        return None  # fragmented across TCP segments -- caller must reassemble

    body = payload[5 : 5 + record_len]
    if len(body) < 4:
        return None
    msg_type = body[0]
    msg_len = int.from_bytes(body[1:4], "big")
    if msg_type != _CLIENT_HELLO_MSG_TYPE:
        return None
    hello = body[4 : 4 + msg_len]

    offset = 2 + 32  # client_version (2 bytes) + random (32 bytes)
    if offset >= len(hello):
        return None
    session_id_len = hello[offset]
    offset += 1 + session_id_len

    if offset + 2 > len(hello):
        return None
    cipher_suites_len = int.from_bytes(hello[offset : offset + 2], "big")
    offset += 2 + cipher_suites_len

    if offset >= len(hello):
        return None
    compression_len = hello[offset]
    offset += 1 + compression_len

    if offset + 2 > len(hello):
        return None  # no extensions present
    extensions_len = int.from_bytes(hello[offset : offset + 2], "big")
    offset += 2
    extensions_end = offset + extensions_len

    while offset + 4 <= extensions_end:
        ext_type = int.from_bytes(hello[offset : offset + 2], "big")
        ext_len = int.from_bytes(hello[offset + 2 : offset + 4], "big")
        ext_data = hello[offset + 4 : offset + 4 + ext_len]
        if ext_type == _SNI_EXTENSION_TYPE:
            return _parse_sni_extension(ext_data)
        offset += 4 + ext_len

    return None


def _parse_sni_extension(ext_data: bytes) -> str | None:
    if len(ext_data) < 2:
        return None
    list_len = int.from_bytes(ext_data[0:2], "big")
    pos, end = 2, 2 + list_len
    while pos + 3 <= end:
        name_type = ext_data[pos]
        name_len = int.from_bytes(ext_data[pos + 1 : pos + 3], "big")
        name = ext_data[pos + 3 : pos + 3 + name_len]
        if name_type == _HOST_NAME_TYPE:
            return name.decode("ascii", errors="strict")
        pos += 3 + name_len
    return None


FlowHintKey = tuple[str, int, str, int]  # (local_ip, local_port, remote_ip, remote_port)


class FlowHintCache:
    """Short-lived, per-flow SNI hints, keyed by the full 4-tuple
    (local_ip, local_port, remote_ip, remote_port) -- deliberately including
    local_port, unlike the original 3-tuple sketched in the project plan.
    Omitting local_port would let two *concurrent* connections from the
    same client to the same shared-IP CDN edge (an ordinary occurrence, not
    a rare edge case -- e.g. loading two different sites behind the same
    CDN POP at once) overwrite each other's hint."""

    def __init__(self, ttl_s: int = SNI_HINT_TTL_S):
        self._ttl_s = ttl_s
        self._hints: dict[FlowHintKey, tuple[str, float]] = {}

    def put(self, local_ip: str, local_port: int, remote_ip: str, remote_port: int,
            hostname: str, now: float) -> None:
        key: FlowHintKey = (local_ip, local_port, remote_ip, remote_port)
        self._hints[key] = (hostname, now + self._ttl_s)

    def get(self, local_ip: str, local_port: int, remote_ip: str, remote_port: int,
            now: float) -> str | None:
        key: FlowHintKey = (local_ip, local_port, remote_ip, remote_port)
        entry = self._hints.get(key)
        if entry is None:
            return None
        hostname, expires_at = entry
        if now >= expires_at:
            del self._hints[key]
            return None
        return hostname

    def purge_expired(self, now: float) -> int:
        expired = [k for k, (_, exp) in self._hints.items() if now >= exp]
        for k in expired:
            del self._hints[k]
        return len(expired)


class ClientHelloReassembler:
    """Buffers a flow's client->server bytes until a complete ClientHello
    is available (or a size cap is hit), since it can straddle more than
    one TCP segment. Not itself capped by real network timing -- the live
    sniff_loop is responsible for evicting stale/abandoned flows."""

    MAX_BUFFER = 8192

    def __init__(self):
        self._buffers: dict[tuple, bytes] = {}

    def feed(self, key: tuple, data: bytes) -> str | None:
        """Appends `data` to the flow's buffer and attempts extraction.
        Returns the hostname once found (and stops tracking that flow).
        Returns None while still waiting for more bytes, or once the
        buffer cap is hit without ever finding an SNI extension (also
        stops tracking that flow at that point, to bound memory use)."""
        buf = self._buffers.get(key, b"") + data
        hostname = extract_sni(buf)
        if hostname is not None:
            self._buffers.pop(key, None)
            return hostname
        if len(buf) >= self.MAX_BUFFER:
            self._buffers.pop(key, None)
            return None
        self._buffers[key] = buf
        return None


def _tcp_payload_bytes(ip, tcp) -> bytes:
    """Extracts exactly the real TCP payload bytes, using IP's own total
    length field to bound it -- rather than trusting scapy's automatic
    layer-boundary guess for "whatever comes after TCP".

    Real bug caught on the OPNsense 26.7 test VM: a bare ACK segment (no
    real payload) was Ethernet-padded to the minimum frame size, and
    scapy dissected that padding into its own `Padding` layer -- which
    naive code (`bytes(tcp.payload)`) picks up anyway, since `.payload`
    just means "whatever the next layer is". That silently fed a handful
    of zero bytes into the ClientHello reassembler ahead of the real
    ClientHello, corrupting every subsequent byte offset for that flow."""
    ip_total_len = ip.len - ip.ihl * 4
    tcp_header_len = tcp.dataofs * 4
    tcp_data_len = max(ip_total_len - tcp_header_len, 0)
    raw_tcp = bytes(tcp)
    return raw_tcp[tcp_header_len:tcp_header_len + tcp_data_len]


def sniff_loop(interfaces: list[str], on_hint, extra_ports: list[int] | None = None) -> None:
    """Live capture entrypoint, wired up by gowiththeflowd.py: reassembles
    per-flow TLS ClientHello bytes and calls on_hint(local_ip, local_port,
    remote_ip, remote_port, hostname) whenever SNI is found. Not exercised
    by Stage A5's unit tests -- proven against real traffic in Phase B.

    Confirmed on the OPNsense 26.7 test VM: scapy's sniff() can fail to
    auto-guess the capture datalink type depending on which scapy
    submodules happened to be imported first elsewhere in the process
    (observed: silently falls back to an undissected Raw packet, with a
    console warning, no exception) -- so this always explicitly re-parses
    the raw bytes as Ethernet itself rather than trusting sniff()'s own
    dissection to have succeeded."""
    import time

    from scapy.layers.inet import IP, TCP
    from scapy.layers.l2 import Ether
    from scapy.sendrecv import sniff

    ports = [443] + list(extra_ports or [])
    bpf_filter = "tcp and (" + " or ".join(f"port {p}" for p in ports) + ")"
    reassembler = ClientHelloReassembler()

    def _handle(pkt) -> None:
        eth = Ether(bytes(pkt))
        if IP not in eth or TCP not in eth:
            return
        payload = _tcp_payload_bytes(eth[IP], eth[TCP])
        if not payload:
            return
        key = (eth[IP].src, eth[TCP].sport, eth[IP].dst, eth[TCP].dport)
        hostname = reassembler.feed(key, payload)
        if hostname is not None:
            on_hint(eth[IP].src, eth[TCP].sport, eth[IP].dst, eth[TCP].dport, hostname, int(time.time()))

    sniff(iface=interfaces, filter=bpf_filter, prn=_handle, store=False)

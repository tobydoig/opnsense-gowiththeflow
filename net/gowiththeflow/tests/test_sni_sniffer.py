import struct

import pytest

from sni_sniffer import ClientHelloReassembler, FlowHintCache, SNI_HINT_TTL_S, extract_sni


def _build_client_hello(hostname: bytes | None) -> bytes:
    client_version = b"\x03\x03"
    random_bytes = b"\x00" * 32
    session_id = b""
    cipher_suites = b"\x00\x2f\x00\x35"
    compression = b"\x00"

    extensions = b""
    if hostname is not None:
        server_name_entry = bytes([0]) + struct.pack(">H", len(hostname)) + hostname
        server_name_list = struct.pack(">H", len(server_name_entry)) + server_name_entry
        extensions += struct.pack(">HH", 0x0000, len(server_name_list)) + server_name_list

    hello_body = (
        client_version
        + random_bytes
        + bytes([len(session_id)]) + session_id
        + struct.pack(">H", len(cipher_suites)) + cipher_suites
        + compression
        + struct.pack(">H", len(extensions)) + extensions
    )
    handshake = bytes([0x01]) + len(hello_body).to_bytes(3, "big") + hello_body
    record = bytes([0x16]) + b"\x03\x01" + struct.pack(">H", len(handshake)) + handshake
    return record


def test_extracts_sni_from_a_well_formed_client_hello():
    payload = _build_client_hello(b"example.com")
    assert extract_sni(payload) == "example.com"


def test_returns_none_when_no_sni_extension_present():
    payload = _build_client_hello(None)
    assert extract_sni(payload) is None


def test_returns_none_for_non_handshake_record_type():
    # 0x17 = application data, not a handshake record at all.
    payload = _build_client_hello(b"example.com")
    tampered = bytes([0x17]) + payload[1:]
    assert extract_sni(tampered) is None


def test_returns_none_for_truncated_fragmented_payload():
    payload = _build_client_hello(b"example.com")
    # Only the first third arrived so far (as if split across TCP segments).
    assert extract_sni(payload[: len(payload) // 3]) is None


def test_returns_none_for_garbage_bytes_without_crashing():
    assert extract_sni(b"\x00" * 5) is None
    assert extract_sni(b"not tls at all, just some http maybe") is None
    assert extract_sni(b"") is None


def test_client_hello_reassembler_waits_for_the_rest_of_a_split_record():
    payload = _build_client_hello(b"example.com")
    split_at = len(payload) // 2
    reassembler = ClientHelloReassembler()
    key = ("192.168.1.50", 51234, "203.0.113.9", 443)

    assert reassembler.feed(key, payload[:split_at]) is None
    assert reassembler.feed(key, payload[split_at:]) == "example.com"


def test_client_hello_reassembler_gives_up_after_max_buffer_without_crashing():
    reassembler = ClientHelloReassembler()
    key = ("192.168.1.50", 51234, "203.0.113.9", 443)
    # A record header claiming a huge length that never actually completes,
    # already past MAX_BUFFER in a single feed.
    huge_chunk = b"\x16\x03\x01\xff\xff" + b"\xff" * (ClientHelloReassembler.MAX_BUFFER + 100)

    result = reassembler.feed(key, huge_chunk)
    assert result is None
    assert key not in reassembler._buffers  # gave up and stopped tracking it, not growing forever


def test_flow_hint_cache_distinguishes_concurrent_flows_to_a_shared_ip():
    # The shared-IP-CDN scenario: two different hostnames observed on the
    # same (remote_ip, remote_port) at the same time, from the same client,
    # distinguished only by local_port -- both must be retrievable.
    cache = FlowHintCache()
    now = 1_000_000
    cache.put("192.168.1.50", 51234, "203.0.113.9", 443, "a.example.com", now)
    cache.put("192.168.1.50", 51999, "203.0.113.9", 443, "b.example.net", now)

    assert cache.get("192.168.1.50", 51234, "203.0.113.9", 443, now) == "a.example.com"
    assert cache.get("192.168.1.50", 51999, "203.0.113.9", 443, now) == "b.example.net"
    # A third, unrelated flow to the same shared IP is a clean miss.
    assert cache.get("192.168.1.50", 52222, "203.0.113.9", 443, now) is None


def test_flow_hint_cache_expires_after_ttl():
    cache = FlowHintCache()
    now = 1_000_000
    cache.put("192.168.1.50", 51234, "203.0.113.9", 443, "a.example.com", now)

    assert cache.get("192.168.1.50", 51234, "203.0.113.9", 443, now + SNI_HINT_TTL_S - 1) == "a.example.com"
    assert cache.get("192.168.1.50", 51234, "203.0.113.9", 443, now + SNI_HINT_TTL_S + 1) is None


def test_flow_hint_cache_purge_expired_removes_stale_entries():
    cache = FlowHintCache()
    now = 1_000_000
    cache.put("192.168.1.50", 1, "203.0.113.9", 443, "a.example.com", now)
    cache.put("192.168.1.50", 2, "203.0.113.9", 443, "b.example.com", now)

    removed = cache.purge_expired(now + SNI_HINT_TTL_S + 1)
    assert removed == 2
    assert cache.get("192.168.1.50", 1, "203.0.113.9", 443, now + SNI_HINT_TTL_S + 1) is None

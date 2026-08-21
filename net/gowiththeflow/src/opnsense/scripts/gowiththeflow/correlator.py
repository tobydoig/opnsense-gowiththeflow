"""Resolves the best available remote hostname for a connection, combining
every hostname source built so far -- the first point in the daemon where
all of Phase A's modules come together.

Priority order (project plan): user-configured static override (explicit
user intent always wins) -> live per-flow SNI hint (sni_sniffer.FlowHintCache)
-> durable hostcache entry (DNS/SNI/PTR, hostcache.py) -> None, meaning the
caller displays the raw IP. For historical (closed) rows, db.record_diff
carries forward whatever hostname a session's live_sessions row already
had -- this module is only responsible for resolving *fresh* hostnames for
open/updated sessions.
"""

from __future__ import annotations

import ipaddress
import sqlite3

import hostcache
from sni_sniffer import FlowHintCache

StaticOverrides = list[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str]]


def parse_static_overrides(entries: list[tuple[str, str]]) -> StaticOverrides:
    """Turns a list of (cidr_or_ip, hostname) config entries into parsed
    networks. The first matching entry wins, so more specific entries
    should be listed first if overlapping ranges are configured."""
    return [(ipaddress.ip_network(cidr, strict=False), hostname) for cidr, hostname in entries]


def resolve_remote_hostname(
    conn: sqlite3.Connection,
    local_ip: str,
    local_port: int,
    remote_ip: str,
    remote_port: int,
    static_overrides: StaticOverrides,
    flow_hints: FlowHintCache,
    now: int,
) -> tuple[str | None, str | None]:
    """Returns (hostname, source) for a remote endpoint, or (None, None)
    if nothing resolved it -- the caller should display the raw IP."""
    remote_addr = ipaddress.ip_address(remote_ip)
    for network, hostname in static_overrides:
        if remote_addr in network:
            return hostname, "static"

    hint = flow_hints.get(local_ip, local_port, remote_ip, remote_port, now)
    if hint is not None:
        return hint, "sni"

    hostname, source = hostcache.get_hostname(conn, remote_ip, now)
    if hostname is not None:
        return hostname, source

    return None, None


def make_resolver(
    conn: sqlite3.Connection,
    static_overrides: StaticOverrides,
    flow_hints: FlowHintCache,
    now: int,
):
    """Builds the `resolve_hostname(snap)` callable db.record_diff expects,
    closing over the shared state for one poll cycle."""

    def _resolve(snap):
        return resolve_remote_hostname(
            conn,
            snap.key.local_ip, snap.key.local_port,
            snap.key.remote_ip, snap.key.remote_port,
            static_overrides, flow_hints, now,
        )

    return _resolve

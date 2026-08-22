"""Refreshes local IP/MAC -> hostname identity from OPNsense's Dnsmasq
service, via the same backend the GUI/API uses (`configctl dnsmasq list
leases`) rather than parsing dnsmasq's raw lease file directly, so this
keeps working if the on-disk format ever changes.

Confirmed against the real backend script on an OPNsense 26.7 test VM
(/usr/local/opnsense/scripts/dnsmasq/get_dnsmasq_leases.py). Two
corrections from Stage A6's original mocked-fixture assumption: the
top-level JSON key is `records`, not `leases`; and there is no
`is_reserved` field at this layer at all (that only exists in the richer
PHP web API controller, OPNsense\\Dnsmasq\\Api\\LeasesController, which
enriches this same raw data -- not in what `configctl` itself returns, which
is what a daemon actually shells out to). So the dhcp_lease/static_mapping
distinction this module originally planned to surface isn't obtainable
from this source; every lease-derived record is just labeled "dhcp_lease"
regardless of whether it happens to be a static reservation -- a
documented simplification, not a silent gap. `arp -an` parsing is the
last-resort fallback for devices with no lease record at all.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

_NO_HOSTNAME_PLACEHOLDER = "*"


@dataclass(frozen=True)
class LocalHostIdentity:
    mac: str
    ip: str | None
    hostname: str | None
    source: str  # dhcp_lease | arp


def parse_leases_json(raw_json: str) -> list[LocalHostIdentity]:
    """Parses the JSON returned by `configctl dnsmasq list leases` into
    LocalHostIdentity records. Skips any record missing a MAC address
    (nothing to key on -- e.g. IPv6-only leases, which key on an IAID
    instead); treats dnsmasq's '*' hostname placeholder, and any
    blank/whitespace-only hostname, as unknown (None)."""
    data = json.loads(raw_json)
    identities = []
    for lease in data.get("records", []):
        mac = lease.get("hwaddr")
        if not mac:
            continue
        hostname = lease.get("hostname")
        if hostname is not None:
            hostname = hostname.strip()
            if not hostname or hostname == _NO_HOSTNAME_PLACEHOLDER:
                hostname = None
        identities.append(
            LocalHostIdentity(
                mac=mac.lower(),
                ip=lease.get("address"),
                hostname=hostname,
                source="dhcp_lease",
            )
        )
    return identities


def parse_arp_output(arp_text: str) -> list[LocalHostIdentity]:
    """Parses `arp -an` output as a last-resort fallback for devices with
    no lease at all. FreeBSD's format looks like:
    '? (192.168.1.99) at aa:bb:cc:dd:ee:ff on igb0 expires in 900 seconds
    [ethernet]'. ARP never carries a hostname -- this only ever contributes
    IP/MAC pairs, and skips incomplete ("at (incomplete)") entries."""
    identities = []
    for line in arp_text.splitlines():
        line = line.strip()
        if "(" not in line or ") at " not in line:
            continue
        try:
            ip = line.split("(", 1)[1].split(")", 1)[0]
            mac = line.split(") at ", 1)[1].split(" ", 1)[0]
        except IndexError:
            continue
        if mac.lower() in ("(incomplete)", "ff:ff:ff:ff:ff:ff"):
            continue
        identities.append(LocalHostIdentity(mac=mac.lower(), ip=ip, hostname=None, source="arp"))
    return identities


def merge_identities(
    lease_identities: list[LocalHostIdentity], arp_identities: list[LocalHostIdentity]
) -> dict[str, LocalHostIdentity]:
    """Merges lease-derived and ARP-derived identities keyed by MAC, with
    lease data always winning for a MAC that has one (leases can carry a
    hostname; ARP never does) -- ARP only fills in devices Dnsmasq has no
    lease record for at all (e.g. a statically-IP-configured device that
    never went through DHCP)."""
    merged: dict[str, LocalHostIdentity] = {}
    for identity in lease_identities:
        merged[identity.mac] = identity
    for identity in arp_identities:
        merged.setdefault(identity.mac, identity)
    return merged


def write_identities(
    conn: sqlite3.Connection, identities: dict[str, LocalHostIdentity], now: int
) -> None:
    for identity in identities.values():
        conn.execute(
            """
            INSERT INTO local_host_identity (mac, ip, hostname, source, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(mac) DO UPDATE SET
                ip=excluded.ip, hostname=excluded.hostname,
                source=excluded.source, updated_at=excluded.updated_at
            """,
            (identity.mac, identity.ip, identity.hostname, identity.source, now),
        )
    conn.commit()


def refresh(conn: sqlite3.Connection, now: int) -> int:
    """Live entrypoint, wired up by gowiththeflowd.py on a 5-minute timer:
    runs `configctl dnsmasq list leases` and `arp -an`, merges, and writes.
    Not exercised by Stage A6's unit tests -- proven in Phase B."""
    import subprocess

    leases_raw = subprocess.run(
        ["configctl", "dnsmasq", "list", "leases"], capture_output=True, text=True, check=True
    ).stdout
    arp_raw = subprocess.run(["arp", "-an"], capture_output=True, text=True, check=True).stdout

    merged = merge_identities(parse_leases_json(leases_raw), parse_arp_output(arp_raw))
    write_identities(conn, merged, now)
    return len(merged)

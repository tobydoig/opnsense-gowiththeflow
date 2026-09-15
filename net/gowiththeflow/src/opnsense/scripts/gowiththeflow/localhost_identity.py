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

A third, lowest-priority source fills in hostname only (never IP): a
Dnsmasq reservation's own configured "Host" name (see
fetch_reservation_hostnames()), for a MAC whose live lease/ARP data
carries no hostname at all -- e.g. a device that never sends a DHCP
hostname (client-shaped devices like VR headsets commonly don't), but
that the user has still given a real name in its reservation.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace

_NO_HOSTNAME_PLACEHOLDER = "*"
PHP_BIN = "/usr/local/bin/php"
DNSMASQ_RESERVATIONS_SCRIPT = "/usr/local/opnsense/scripts/gowiththeflow/dnsmasq_reservations.php"


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


def fetch_reservation_hostnames() -> dict[str, str]:
    """mac -> a Dnsmasq reservation's own configured "Host" name, for
    every reservation that has one set -- read via dnsmasq_reservations.php
    (Python has no other way to reach Dnsmasq's PHP-model-owned config,
    same reasoning reservation_gate.py's own fetch_reservations() already
    established). Used only to backfill a hostname when the live DHCP
    lease/ARP data has none at all (see merge_identities()) -- a real gap
    found live: a device that never sends a DHCP hostname (e.g. a VR
    headset with no option-12 support) showed as a bare IP forever in
    the GUI even though the user had given it a perfectly good name in
    its own reservation.

    Unlike fetch_reservations()'s own None-vs-[] sentinel distinction,
    a failure here has no dangerous blast radius -- it can only leave a
    device showing as a bare IP for one more 5-minute refresh cycle,
    exactly as it already would without this feature at all -- so
    returning {} on any failure (rather than a None sentinel the caller
    has to specially handle) is the simpler, still-correct choice."""
    import subprocess

    try:
        result = subprocess.run(
            [PHP_BIN, "-f", DNSMASQ_RESERVATIONS_SCRIPT],
            capture_output=True, text=True, check=True, timeout=15,
        )
        rows = json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
        return {}

    hostnames: dict[str, str] = {}
    for row in rows:
        mac = str(row.get("mac") or "").strip().lower()
        host = str(row.get("host") or "").strip()
        if mac and host:
            hostnames[mac] = host
    return hostnames


def merge_identities(
    lease_identities: list[LocalHostIdentity],
    arp_identities: list[LocalHostIdentity],
    reservation_hostnames: dict[str, str] | None = None,
) -> dict[str, LocalHostIdentity]:
    """Merges lease-derived and ARP-derived identities keyed by MAC, with
    lease data always winning for a MAC that has one (leases can carry a
    hostname; ARP never does) -- ARP only fills in devices Dnsmasq has no
    lease record for at all (e.g. a statically-IP-configured device that
    never went through DHCP).

    `reservation_hostnames` (see fetch_reservation_hostnames()) then
    backfills the hostname -- and only the hostname, IP/source stay
    exactly as observed live -- for any resulting identity that still
    has none at all, whether that's a lease with no reported hostname or
    an ARP-only entry. A hostname genuinely observed live always wins
    over a configured one; this only ever fills a gap, never overrides."""
    merged: dict[str, LocalHostIdentity] = {}
    for identity in lease_identities:
        merged[identity.mac] = identity
    for identity in arp_identities:
        merged.setdefault(identity.mac, identity)
    if reservation_hostnames:
        for mac, identity in merged.items():
            if identity.hostname is None:
                host = reservation_hostnames.get(mac)
                if host:
                    merged[mac] = replace(identity, hostname=host)
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
    runs `configctl dnsmasq list leases` and `arp -an`, merges (backfilling
    any still-missing hostname from Dnsmasq's own reservation names, see
    fetch_reservation_hostnames()), and writes. Not exercised by Stage A6's
    unit tests -- proven in Phase B.

    Uses absolute paths for both commands -- real bug caught running this
    under rc.d on the OPNsense 26.7 test VM: the service's PATH doesn't
    include /usr/local/sbin, so plain "configctl" raised FileNotFoundError
    and killed the daemon's main thread. That's since become the main
    loop's own top-level catch-all's job to survive (see
    gowiththeflowd.py), which is also why `timeout=` matters here now --
    an unbounded subprocess.run() call inside that loop can freeze the
    *entire* daemon (nothing to catch, since it never raises) rather than
    just this one refresh failing. See gowiththeflowd.py's own pfctl call
    for the real incident this was found from: a genuinely huge, sustained
    single-connection transfer intermittently made `pfctl -vvs state`
    (kernel-level, not this file, but the exact same class of gap) take
    long enough that nothing here would have caught it either without
    this."""
    import subprocess

    leases_raw = subprocess.run(
        ["/usr/local/sbin/configctl", "dnsmasq", "list", "leases"],
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout
    arp_raw = subprocess.run(
        ["/usr/sbin/arp", "-an"], capture_output=True, text=True, check=True, timeout=15,
    ).stdout
    reservation_hostnames = fetch_reservation_hostnames()

    merged = merge_identities(parse_leases_json(leases_raw), parse_arp_output(arp_raw), reservation_hostnames)
    write_identities(conn, merged, now)
    return len(merged)

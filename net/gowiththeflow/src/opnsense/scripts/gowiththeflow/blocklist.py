"""Blocking a local host's traffic via a pf table this plugin owns
independently of OPNsense's own Alias system.

The pf table (`gowiththeflow_blocked`) and the two `block ... quick`
rules referencing it (from both directions) are declared by
`etc/inc/plugins.inc.d/gowiththeflow.inc`, loaded into the compiled
ruleset via OPNsense's own plugin-firewall-hook mechanism -- resolved
after confirming directly (reading `OPNsense\\Firewall\\Plugin.php` and
the live compiled ruleset on the test VM) that a pf table with no rule
referencing it blocks nothing, and that an independently-loaded pf
anchor is never actually evaluated unless the main ruleset has a call
point for it, which nothing does for a homegrown one.

`blocked_hosts` (db.py) is the one and only source of truth for what's
blocked -- the pf table's on-disk backing file is always *derived from*
it via sync_pf(), never the other way round, so any drift (a manual
`pfctl` edit, a file that predates a DB row) self-heals on the next
sync rather than accumulating. This module is pure logic + the pf/DB
primitives, importable by both block_host.py's CLI (invoked via configd
from PHP) and gowiththeflowd.py (startup replay + periodic reconcile).
"""

from __future__ import annotations

import ipaddress
import os
import sqlite3
import subprocess
import tempfile

PF_TABLE = "gowiththeflow_blocked"
PFCTL = "/sbin/pfctl"


def normalize_ip(value: str | None) -> str | None:
    """Validates and canonicalizes a single host address -- returns None
    for anything that isn't exactly one valid IPv4/IPv6 address (CIDR
    ranges, hostnames, empty/whitespace, garbage). This is the one gate
    every address reaches before it's ever passed to a shell command or
    used as a SQLite primary key, independent of whatever validation (or
    lack of it) happens upstream in PHP/configd."""
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def parse_own_addresses(ifconfig_output: str) -> set[str]:
    """Parses `ifconfig -a` output for every inet/inet6 address
    configured on this box, across every interface including loopback --
    used to refuse blocking an address that belongs to the firewall
    itself. An IPv6 link-local's `%zone` suffix (e.g.
    'fe80::1%le0') is stripped, since it's an interface-scope
    annotation, not part of the address itself, and normalize_ip()
    can't parse it as-is."""
    addresses = set()
    for line in ifconfig_output.splitlines():
        line = line.strip()
        if line.startswith("inet6 "):
            addr = line.split()[1].split("%")[0]
        elif line.startswith("inet "):
            addr = line.split()[1]
        else:
            continue
        normalized = normalize_ip(addr)
        if normalized is not None:
            addresses.add(normalized)
    return addresses


def is_subnet_edge_address(ip: str, local_subnets: list[str]) -> bool:
    """True if `ip` is the network or broadcast address of any configured
    local subnet (e.g. 10.0.0.255 for 10.0.0.0/24) -- confirmed live that
    broadcast traffic (a real local host talking to its subnet's
    broadcast address) gets classified the same as any other local<->local
    pf state (see pf_state_poller.classify_sessions()), so the broadcast
    address itself can genuinely show up as a session's `local_ip` and
    therefore as a "host" a user could try to block. It isn't a real
    device -- a block rule naming it would either match nothing
    meaningful or interfere with the broadcast discovery/DHCP traffic
    every device on the subnet relies on, so it's refused the same way
    the firewall's own addresses are. IPv6 has no broadcast concept, and
    a /31 or /32 has no distinct network/broadcast address (RFC 3021),
    so neither is ever flagged here."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version != 4:
        return False
    for subnet in local_subnets:
        try:
            network = ipaddress.ip_network(subnet, strict=False)
        except ValueError:
            continue
        if network.version != 4 or network.num_addresses < 4:
            continue
        if addr in (network.network_address, network.broadcast_address):
            return True
    return False


def refuse_reason_for_host_block(ip: str, local_subnets: list[str]) -> str | None:
    """Returns a human-readable refusal reason if `ip` must never be
    host-blocked (the firewall's own address, or a subnet's network/
    broadcast address), or None if blocking it is fine. Shared by every
    caller that creates a host-type block (block_host.py's own cmd_block,
    block_rules.py's `create --type host`) so this guard can't drift
    between the two entry points -- there was only ever meant to be one
    place that decides this."""
    ifconfig_output = subprocess.run(
        ["/sbin/ifconfig", "-a"], capture_output=True, text=True, check=False
    ).stdout
    if ip in parse_own_addresses(ifconfig_output):
        return "refusing to block one of the firewall's own addresses"
    if is_subnet_edge_address(ip, local_subnets):
        return "refusing to block a network/broadcast address -- not a real device"
    return None


def render_table_file(ips: list[str]) -> str:
    """One address per line, deduplicated and sorted, trailing newline --
    matches pf's own table-file format (see pfctl(8)'s TABLES section).
    An empty list renders as an empty string, a valid (empty) table."""
    unique_sorted = sorted(set(ips), key=ipaddress.ip_address)
    if not unique_sorted:
        return ""
    return "\n".join(unique_sorted) + "\n"


def write_table_file(path: str, ips: list[str]) -> None:
    """Writes render_table_file()'s output atomically -- a temp file in
    the SAME directory (so os.replace() is a same-filesystem rename, not
    a copy that could be interrupted partway) followed by an atomic
    rename, so pf (or a human) reading this file can never observe a
    torn, half-written table."""
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".blocked_hosts_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(render_table_file(ips))
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def list_blocked(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT local_ip, hostname, mac, blocked_at, blocked_by, reason
        FROM blocked_hosts ORDER BY blocked_at DESC
        """
    ).fetchall()


def add_block(
    conn: sqlite3.Connection,
    ip: str,
    hostname: str | None,
    mac: str | None,
    blocked_by: str | None,
    reason: str | None,
    now: int,
) -> None:
    """Upserts -- re-blocking an already-blocked host refreshes its
    snapshot (hostname/mac/blocked_at/blocked_by/reason) rather than
    crashing on the primary key."""
    conn.execute(
        """
        INSERT INTO blocked_hosts (local_ip, hostname, mac, blocked_at, blocked_by, reason)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(local_ip) DO UPDATE SET
            hostname=excluded.hostname, mac=excluded.mac, blocked_at=excluded.blocked_at,
            blocked_by=excluded.blocked_by, reason=excluded.reason
        """,
        (ip, hostname, mac, now, blocked_by, reason),
    )
    conn.commit()


def remove_block(conn: sqlite3.Connection, ip: str) -> None:
    """A no-op, not an error, if the address isn't currently blocked."""
    conn.execute("DELETE FROM blocked_hosts WHERE local_ip = ?", (ip,))
    conn.commit()


def sync_pf(conn: sqlite3.Connection, tbl_path: str) -> subprocess.CompletedProcess:
    """Rewrites the pf table's backing file from blocked_hosts (the
    source of truth) and tells pf to reload just that one table --
    idempotent and cheap to call repeatedly (a `-T replace` against an
    already-correct table is a no-op), which is what lets the daemon's
    own startup replay and periodic reconcile share this exact function
    with block_host.py's CLI actions without needing to coordinate.
    Returns the CompletedProcess rather than raising on a non-zero exit
    -- callers decide whether/how to surface a pfctl failure."""
    ips = [row["local_ip"] for row in list_blocked(conn)]
    write_table_file(tbl_path, ips)
    return subprocess.run(
        [PFCTL, "-t", PF_TABLE, "-T", "replace", "-f", tbl_path],
        capture_output=True, text=True, check=False,
    )


def kill_states(ip: str) -> list[subprocess.CompletedProcess]:
    """Kills pf states involving this host in both directions --
    `pfctl -k <ip>` alone only kills states where the host is the
    *source* (per pfctl(8)); a second call naming the address family's
    wildcard network as source and this host as destination catches
    states where it's on the receiving end instead (e.g. behind a port
    forward). "0 states killed" is pf's normal response when nothing
    matches, not a failure -- callers should not treat a non-zero exit
    here as exceptional."""
    is_v6 = ipaddress.ip_address(ip).version == 6
    wildcard = "::/0" if is_v6 else "0.0.0.0/0"
    return [
        subprocess.run([PFCTL, "-k", ip], capture_output=True, text=True, check=False),
        subprocess.run([PFCTL, "-k", wildcard, "-k", ip], capture_output=True, text=True, check=False),
    ]


def rules_present() -> bool:
    """Sanity check for whether the block table/rules actually made it
    into the live ruleset (e.g. right after a fresh install before the
    first `configctl filter reload`, or if the .inc file failed to
    load) -- checked defensively rather than assumed."""
    result = subprocess.run([PFCTL, "-sr"], capture_output=True, text=True, check=False)
    return PF_TABLE in result.stdout

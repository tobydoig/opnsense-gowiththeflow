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
import re
import sqlite3
import subprocess
import tempfile

PF_TABLE = "gowiththeflow_blocked"
PFCTL = "/sbin/pfctl"
_STATE_ID_RE = re.compile(r"\bid:\s*([0-9a-f]+)\s+creatorid:\s*([0-9a-f]+)")


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
    """One address (or CIDR network) per line, deduplicated and sorted,
    trailing newline -- matches pf's own table-file format (see
    pfctl(8)'s TABLES section, which accepts both plain addresses and
    networks as table entries). An empty list renders as an empty
    string, a valid (empty) table. Sorted via ip_network(..., strict=False)
    rather than ip_address() -- a plain address parses fine as a /32 (or
    /128) network too, so this one sort key correctly handles both this
    module's own plain-IP blocklist entries and reservation_gate.py's
    CIDR-network protected-destinations entries without needing two
    code paths."""
    unique_sorted = sorted(set(ips), key=lambda x: ipaddress.ip_network(x, strict=False))
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


def sync_pf_table(pf_table: str, tbl_path: str, ips: list[str]) -> subprocess.CompletedProcess:
    """The generic primitive underneath sync_pf() -- rewrites `pf_table`'s
    backing file to exactly `ips` and tells pf to reload just that one
    table. Idempotent and cheap to call repeatedly (a `-T replace`
    against an already-correct table is a no-op). Factored out (rather
    than reservation_gate.py duplicating sync_pf()'s own pfctl-invocation
    shape for its own, differently-sourced tables) so there is exactly
    one place that knows how to talk to pfctl about table contents.
    Returns the CompletedProcess rather than raising on a non-zero exit
    -- callers decide whether/how to surface a pfctl failure."""
    write_table_file(tbl_path, ips)
    return subprocess.run(
        [PFCTL, "-t", pf_table, "-T", "replace", "-f", tbl_path],
        capture_output=True, text=True, check=False,
    )


def sync_pf(conn: sqlite3.Connection, tbl_path: str) -> subprocess.CompletedProcess:
    """Rewrites the pf table's backing file from blocked_hosts (the
    source of truth) and tells pf to reload just that one table --
    idempotent and cheap to call repeatedly, which is what lets the
    daemon's own startup replay and periodic reconcile share this exact
    function with block_host.py's CLI actions without needing to
    coordinate."""
    ips = [row["local_ip"] for row in list_blocked(conn)]
    return sync_pf_table(PF_TABLE, tbl_path, ips)


def kill_states(ip: str) -> list[subprocess.CompletedProcess]:
    """Kills pf states involving this host in both directions --
    `pfctl -k <ip>` alone only kills states where the host is the
    *source* (per pfctl(8)); a second call naming the address family's
    wildcard network as source and this host as destination catches
    states where it's on the receiving end instead (e.g. behind a port
    forward). "0 states killed" is pf's normal response when nothing
    matches, not a failure -- callers should not treat a non-zero exit
    here as exceptional.

    Both of those match a state's *post*-NAT addresses, so neither ever
    matches the WAN-side state of an outbound NAT'd connection (its
    source there is the firewall's own WAN address, not this host) --
    a third call, `-k nat`, matches on the pre-NAT address instead.
    Found live: a blocked phone's VPN tunnel kept its 3h+ old WAN state
    through every kill, so the connection never actually dropped.

    Even all three miss some states, so kill_states_for() finishes by
    killing whatever is left by state id -- see its docstring."""
    return kill_states_for([ip])


def kill_states_for(ips: list[str]) -> list[subprocess.CompletedProcess]:
    """kill_states() for several hosts at once, sharing one state-table
    listing between them (the periodic sweep calls this with every
    blocked host each tick).

    The host-matching `pfctl -k` forms run first, then any state still
    involving one of these hosts -- in any address slot, pre- or
    post-NAT, either direction -- is killed by id. Found live: a LAN-side
    state created *outbound* towards a blocked iPad (`185.184.195.132:4500
    -> 192.168.200.226:53146`, an IPsec NAT-T tunnel) matched none of
    `-k <ip>`, `-k 0.0.0.0/0 -k <ip>` or `-k nat -k <ip>` ("killed 0
    states" from each, run by hand on nostromo), and carried 1.6GB
    through a block for 1h25m. Matching on the listing ourselves doesn't
    depend on how pf maps a state's direction onto "source"."""
    results = []
    for ip in ips:
        is_v6 = ipaddress.ip_address(ip).version == 6
        wildcard = "::/0" if is_v6 else "0.0.0.0/0"
        results.append(subprocess.run([PFCTL, "-k", ip], capture_output=True, text=True, check=False))
        results.append(subprocess.run([PFCTL, "-k", wildcard, "-k", ip], capture_output=True, text=True, check=False))
        results.append(subprocess.run([PFCTL, "-k", "nat", "-k", ip], capture_output=True, text=True, check=False))
    if not ips:
        return results

    listing = subprocess.run([PFCTL, "-vvs", "state"], capture_output=True, text=True, check=False, timeout=15)
    if listing.returncode != 0:
        results.append(listing)
        return results
    for state_id in state_ids_involving(listing.stdout, ips):
        results.append(subprocess.run([PFCTL, "-k", "id", "-k", state_id], capture_output=True, text=True, check=False))
    return results


def _normalize_ip(text: str) -> str | None:
    try:
        return str(ipaddress.ip_address(text.split("%", 1)[0]))
    except ValueError:
        return None


def _addr_of(token: str) -> str | None:
    """The address in one `pfctl -s state` endpoint token: 'ip:port'
    (IPv4), 'ip[port]' (IPv6), or a bare address, optionally wrapped in
    parentheses (the pre-NAT address on a NAT'd state's line)."""
    token = token.strip("()")
    if "[" in token:
        token = token.split("[", 1)[0]
    bare = _normalize_ip(token)
    if bare is not None:
        return bare
    if ":" in token:
        return _normalize_ip(token.rpartition(":")[0])
    return None


def state_ids_involving(text: str, ips: list[str]) -> list[str]:
    """Parses `pfctl -vvs state` output and returns "id/creatorid" (the
    form `pfctl -k id -k ...` takes) for every state with one of `ips`
    anywhere on its header line. Header lines start in column 0; the
    detail lines under them, including "id: ... creatorid: ...", are
    indented."""
    wanted = {_normalize_ip(ip) for ip in ips}
    found = []
    involved = False
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            tokens = line.split()
            involved = ("<-" in tokens or "->" in tokens) and any(_addr_of(tok) in wanted for tok in tokens)
            continue
        if involved:
            match = _STATE_ID_RE.search(line)
            if match:
                found.append(f"{match.group(1)}/{match.group(2)}")
                involved = False
    return found


def rules_present() -> bool:
    """Sanity check for whether the block table/rules actually made it
    into the live ruleset (e.g. right after a fresh install before the
    first `configctl filter reload`, or if the .inc file failed to
    load) -- checked defensively rather than assumed."""
    result = subprocess.run([PFCTL, "-sr"], capture_output=True, text=True, check=False)
    return PF_TABLE in result.stdout

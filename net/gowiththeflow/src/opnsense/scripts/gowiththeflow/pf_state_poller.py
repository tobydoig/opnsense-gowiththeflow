"""Parses `pfctl -vvs state` output and diffs successive snapshots into
open/update/close events for connection tracking.

pf reports two packet/byte counters per state, corresponding to traffic in
the state's original (matching) direction and its reverse. For a
LAN-initiated outbound state this is assumed to map directly to
(local->remote, remote->local) -- confirmed against real pfctl output on
an OPNsense 26.7 test VM during Phase B.

The real output format (confirmed against that VM, and meaningfully
different from this module's original Phase A assumptions) is:
    <prefix-token> <proto> <src> <-|-> <dst>       <STATE>:<STATE>
       [optional TCP-only window-scale detail line]
       age HH:MM:SS, expires in HH:MM:SS, N:N pkts, N:N bytes, rule N[, ...]
       id: ... creatorid: ...
       origif: ...
- <prefix-token> is a variable leading field (observed as literally "all"
  on this VM; treated generically here since its exact value isn't
  semantically used) that this module's original header regex didn't
  expect at all.
- <src>/<dst> are IPv4 as "ip:port" but IPv6 as "ip[port]" (brackets,
  since the address itself contains colons).
- The stats line is not always immediately after the header line (TCP
  states have an extra window-scale line first), so this module scans all
  lines belonging to a block rather than assuming a fixed offset.
- "expires in" is HH:MM:SS, not a bare seconds count as originally
  assumed; unused by this module either way, so the stats regex accepts
  any non-space token there instead of a specific format.

This class does not run `pfctl` itself -- callers feed in already-fetched
text, which keeps it trivially testable without shelling out and lets the
actual polling loop/subprocess call live in the daemon entrypoint instead.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Iterable

_STATE_STATS_RE = re.compile(
    r"age\s+(?P<age>[\d:]+),\s+expires in\s+(?P<expires>\S+?),\s+"
    r"(?P<pkts_a>\d+):(?P<pkts_b>\d+)\s+pkts,\s+"
    r"(?P<bytes_a>\d+):(?P<bytes_b>\d+)\s+bytes,\s+rule\s+(?P<rule>\d+)"
)


def _split_addr_port(token: str) -> tuple[str, str] | tuple[None, None]:
    """Splits 'ip:port' (IPv4) or 'ip[port]' (IPv6) into (ip, port). Also
    handles a *bare* address with no port at all -- observed transiently
    for IPv6 link-local states (e.g. neighbor discovery) with no
    src/dst port, which a naive "split on the last colon" would silently
    corrupt (an IPv6 address already contains colons, so e.g. bare
    'fe80::1' would wrongly become ip='fe80:', port='1'). A bare address
    with no port isn't something this module can key a connection on, so
    it's reported as (ip, None) and the caller drops the whole record."""
    if "[" in token and token.endswith("]"):
        ip, _, port_str = token.rpartition("[")
        port_str = port_str[:-1]
        if not ip or not port_str.isdigit():
            return None, None
        return ip, port_str

    try:
        ipaddress.ip_address(token.split("%", 1)[0])
        return token, None  # a bare address (optionally with an IPv6 %scope), no port
    except ValueError:
        pass

    if ":" in token:
        ip, _, port_str = token.rpartition(":")
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return None, None
        if not port_str.isdigit():
            return None, None
        return ip, port_str

    return None, None


def _parse_header_line(line: str) -> dict | None:
    """Locates proto/src/dst by anchoring on the '<-'/'->' arrow token,
    rather than assuming a fixed number of leading fields -- robust to
    whatever variable prefix token(s) pf puts before the protocol. Returns
    None for states with no port on either side (e.g. ICMP/ICMPv6,
    protocol-only states) -- this module's connection model requires both."""
    tokens = line.split()
    arrow_idx = None
    for i, tok in enumerate(tokens):
        if tok in ("<-", "->"):
            arrow_idx = i
            break
    if arrow_idx is None or arrow_idx < 2 or arrow_idx + 1 >= len(tokens):
        return None

    proto = tokens[arrow_idx - 2]
    src_ip, src_port = _split_addr_port(tokens[arrow_idx - 1])
    dst_ip, dst_port = _split_addr_port(tokens[arrow_idx + 1])
    if src_ip is None or dst_ip is None or src_port is None or dst_port is None:
        return None
    return {
        "proto": proto,
        "src_ip": src_ip, "src_port": src_port,
        "dst_ip": dst_ip, "dst_port": dst_port,
    }


@dataclass(frozen=True)
class StateKey:
    proto: str
    local_ip: str
    local_port: int
    remote_ip: str
    remote_port: int


@dataclass
class StateSnapshot:
    key: StateKey
    bytes_out: int
    bytes_in: int
    pkts_out: int
    pkts_in: int
    age_s: int


def _parse_age(age_str: str) -> int:
    h, m, s = (int(p) for p in age_str.split(":"))
    return h * 3600 + m * 60 + s


def parse_pfctl_state_text(text: str) -> list[dict]:
    """Parses raw `pfctl -vvs state` text into one dict per state block.
    Groups each header line with every following line up to the next
    header (or EOF), then searches that whole group for the stats line --
    robust to extra detail lines (TCP's window-scale line, id/creatorid,
    origif, route-to, ...) appearing in between, in any order."""
    blocks: list[tuple[dict, list[str]]] = []
    current: tuple[dict, list[str]] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        header = _parse_header_line(line)
        if header is not None:
            current = (header, [])
            blocks.append(current)
        elif current is not None:
            current[1].append(line)

    records = []
    for header, detail_lines in blocks:
        rec = dict(header)
        for line in detail_lines:
            stats_match = _STATE_STATS_RE.search(line)
            if stats_match:
                rec.update(stats_match.groupdict())
                break
        records.append(rec)
    return records


def classify_local_remote(
    records: Iterable[dict], local_subnets: list[str]
) -> list[StateSnapshot]:
    """Reorients each parsed pf state record onto (local, remote) using the
    configured local subnets, discarding states where both or neither
    endpoint is local (e.g. two local hosts talking directly, or a hairpin
    state with neither side in a configured subnet)."""
    networks = [ipaddress.ip_network(s) for s in local_subnets]
    snapshots = []
    for rec in records:
        if "pkts_a" not in rec:
            continue  # no stats line parsed; incomplete record, skip
        src_ip = ipaddress.ip_address(rec["src_ip"])
        dst_ip = ipaddress.ip_address(rec["dst_ip"])
        src_local = any(src_ip in n for n in networks)
        dst_local = any(dst_ip in n for n in networks)
        if src_local == dst_local:
            continue
        if src_local:
            local_ip, local_port = rec["src_ip"], int(rec["src_port"])
            remote_ip, remote_port = rec["dst_ip"], int(rec["dst_port"])
            bytes_out, bytes_in = int(rec["bytes_a"]), int(rec["bytes_b"])
            pkts_out, pkts_in = int(rec["pkts_a"]), int(rec["pkts_b"])
        else:
            local_ip, local_port = rec["dst_ip"], int(rec["dst_port"])
            remote_ip, remote_port = rec["src_ip"], int(rec["src_port"])
            bytes_in, bytes_out = int(rec["bytes_a"]), int(rec["bytes_b"])
            pkts_in, pkts_out = int(rec["pkts_a"]), int(rec["pkts_b"])
        key = StateKey(rec["proto"], local_ip, local_port, remote_ip, remote_port)
        snapshots.append(
            StateSnapshot(
                key=key,
                bytes_out=bytes_out,
                bytes_in=bytes_in,
                pkts_out=pkts_out,
                pkts_in=pkts_in,
                age_s=_parse_age(rec["age"]),
            )
        )
    return snapshots


@dataclass
class DiffResult:
    opened: list[StateSnapshot] = field(default_factory=list)
    updated: list[StateSnapshot] = field(default_factory=list)
    closed: list[StateSnapshot] = field(default_factory=list)


class PfStatePoller:
    """Maintains the previous snapshot set and diffs each new poll against
    it, keyed by 4-tuple + proto so a session survives across polls even as
    its counters change."""

    def __init__(self, local_subnets: list[str]):
        self._local_subnets = local_subnets
        self._prev: dict[StateKey, StateSnapshot] = {}

    def poll(self, pfctl_output_text: str) -> DiffResult:
        records = parse_pfctl_state_text(pfctl_output_text)
        current = {
            s.key: s for s in classify_local_remote(records, self._local_subnets)
        }

        result = DiffResult()
        for key, snap in current.items():
            if key not in self._prev:
                result.opened.append(snap)
            else:
                result.updated.append(snap)
        for key, snap in self._prev.items():
            if key not in current:
                result.closed.append(snap)

        self._prev = current
        return result

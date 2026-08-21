"""Parses `pfctl -vvs state` output and diffs successive snapshots into
open/update/close events for connection tracking.

pf reports two packet/byte counters per state, corresponding to traffic in
the state's original (matching) direction and its reverse. For a
LAN-initiated outbound state this is assumed to map directly to
(local->remote, remote->local) -- this assumption gets confirmed against
real pfctl output during Phase B VM testing (see the project plan); until
then it is a documented assumption, not a silent guess.

This class does not run `pfctl` itself -- callers feed in already-fetched
text, which keeps it trivially testable without shelling out and lets the
actual polling loop/subprocess call live in the daemon entrypoint instead.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Iterable

_STATE_HEADER_RE = re.compile(
    r"^(?P<proto>\w+)\s+"
    r"(?P<src_ip>[0-9a-fA-F:.]+):(?P<src_port>\d+)\s+"
    r"(?P<arrow>->|<-)\s+"
    r"(?P<dst_ip>[0-9a-fA-F:.]+):(?P<dst_port>\d+)\s+"
    r"\S+$"
)

_STATE_STATS_RE = re.compile(
    r"age\s+(?P<age>[\d:]+),\s+expires in\s+(?P<expires>\d+)s,\s+"
    r"(?P<pkts_a>\d+):(?P<pkts_b>\d+)\s+pkts,\s+"
    r"(?P<bytes_a>\d+):(?P<bytes_b>\d+)\s+bytes,\s+rule\s+(?P<rule>\d+)"
)


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
    """Parses raw `pfctl -vvs state` text into one dict per state block."""
    records = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        header_match = _STATE_HEADER_RE.match(lines[i].strip())
        if not header_match:
            i += 1
            continue
        rec = header_match.groupdict()
        if i + 1 < len(lines):
            stats_match = _STATE_STATS_RE.search(lines[i + 1].strip())
            if stats_match:
                rec.update(stats_match.groupdict())
                i += 1
        records.append(rec)
        i += 1
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

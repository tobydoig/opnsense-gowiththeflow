"""Periodic batch DPI (deep packet inspection) classification via nDPI's
`ndpiReader` CLI tool.

`ndpiReader -k <file> -K json` is batch-only, not a live stream --
confirmed directly against a real capture: polling its output file every
5s during a run showed 0 lines at every checkpoint, with all classified
flows appearing only once the process exits cleanly at the end. So this
module runs it in a loop of back-to-back bounded captures (each a fresh
process, no continuity of flow state across bursts) rather than treating
it like the continuous packet-level sniffers in dns_sniffer.py/
sni_sniffer.py. Same shape as those two modules regardless (a loop
function run as a background thread, calling back per result) so
gowiththeflowd.py wires it up the same way.

Known, accepted limitations from this design (see DESIGN.md): a
long-lived connection can take several bursts, or never, to reach a
confident classification, since each burst starts detection from
scratch; a short-lived connection that closes between bursts may never
get classified at all.
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Iterable

NDPI_READER_PATH = "/usr/local/bin/ndpiReader"


@dataclass(frozen=True)
class DpiClassification:
    proto: str
    local_ip: str
    local_port: int
    peer_ip: str
    peer_port: int
    dpi_protocol: str


def parse_ndpi_output(raw: bytes, local_subnets: list[str]) -> list[DpiClassification]:
    """Parses `ndpiReader -K json`'s output -- one JSON object per line,
    each describing one flow. Reorients onto (local, peer) the same way
    pf_state_poller.classify_sessions() does for pf state records (small
    enough to duplicate rather than share -- these two parse genuinely
    different input shapes), discarding flows where neither side is
    local. A line with no "ndpi" key (captured but not classified) is
    skipped, not an error."""
    networks = [ipaddress.ip_network(s) for s in local_subnets]
    results = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ndpi = rec.get("ndpi")
        if not ndpi or "proto" not in ndpi:
            continue
        try:
            src_ip = ipaddress.ip_address(rec["src_ip"])
            dst_ip = ipaddress.ip_address(rec["dest_ip"])
        except (KeyError, ValueError):
            continue
        src_local = any(src_ip in n for n in networks)
        dst_local = any(dst_ip in n for n in networks)
        if not src_local and not dst_local:
            continue
        proto = str(rec.get("proto", "")).lower()
        if src_local:
            local_ip, local_port = rec["src_ip"], int(rec["src_port"])
            peer_ip, peer_port = rec["dest_ip"], int(rec["dst_port"])
        else:
            local_ip, local_port = rec["dest_ip"], int(rec["dst_port"])
            peer_ip, peer_port = rec["src_ip"], int(rec["src_port"])
        results.append(
            DpiClassification(
                proto=proto,
                local_ip=local_ip,
                local_port=local_port,
                peer_ip=peer_ip,
                peer_port=peer_port,
                dpi_protocol=ndpi["proto"],
            )
        )
    return results


def run_capture_burst(interfaces: Iterable[str], burst_duration_s: int) -> bytes:
    """Runs one bounded ndpiReader capture and returns its raw JSON-lines
    output. A dedicated function (not inlined in capture_loop) so tests
    can exercise capture_loop's looping/threading shape without actually
    shelling out."""
    with tempfile.NamedTemporaryFile(suffix=".json") as out:
        subprocess.run(
            [
                NDPI_READER_PATH,
                "-i", ",".join(interfaces),
                "-s", str(burst_duration_s),
                "-k", out.name,
                "-K", "json",
                "-q",
            ],
            capture_output=True,
            check=False,
        )
        return out.read()


def capture_loop(
    interfaces: list[str],
    local_subnets: list[str],
    on_classification: Callable[[DpiClassification], None],
    burst_duration_s: int = 60,
) -> None:
    """Runs bounded ndpiReader captures back-to-back forever, calling
    on_classification(...) once per classified flow from each completed
    burst. Never exits on its own -- matches dns_sniffer.sniff_loop/
    sni_sniffer.sniff_loop, both run as daemon threads that live for the
    process lifetime."""
    while True:
        raw = run_capture_burst(interfaces, burst_duration_s)
        for record in parse_ndpi_output(raw, local_subnets):
            on_classification(record)

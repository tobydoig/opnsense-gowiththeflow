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
import time
from dataclasses import dataclass
from typing import Callable, Iterable

try:
    import syslog
except ImportError:  # syslog is POSIX-only -- this module's own tests run on Windows
    syslog = None

NDPI_READER_PATH = "/usr/local/bin/ndpiReader"


def _log_error(message: str) -> None:
    if syslog is not None:
        syslog.syslog(syslog.LOG_ERR, message)


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
        # Bare dict indexing here (not .get()) is deliberate -- a record
        # missing any of these means it's not a connection this project
        # can represent at all (e.g. ICMP has no ports), so it's skipped
        # the same way pf_state_poller.classify_sessions() already skips
        # a pf state with no port on either side. Confirmed for real: an
        # unguarded version of this exact lookup crashed the capture
        # thread in production on the first real flow lacking a port,
        # silently killing DPI classification after one successful burst.
        try:
            src_ip = ipaddress.ip_address(rec["src_ip"])
            dst_ip = ipaddress.ip_address(rec["dest_ip"])
            src_port = int(rec["src_port"])
            dst_port = int(rec["dst_port"])
        except (KeyError, ValueError, TypeError):
            continue
        src_local = any(src_ip in n for n in networks)
        dst_local = any(dst_ip in n for n in networks)
        if not src_local and not dst_local:
            continue
        proto = str(rec.get("proto", "")).lower()
        if src_local:
            local_ip, local_port = rec["src_ip"], src_port
            peer_ip, peer_port = rec["dest_ip"], dst_port
        else:
            local_ip, local_port = rec["dest_ip"], dst_port
            peer_ip, peer_port = rec["src_ip"], src_port
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
    shelling out.

    Logs (rather than silently ignoring) a non-zero exit -- this daemon
    runs under OPNsense's Daemonize helper, which redirects stdin/stdout/
    stderr to /dev/null unconditionally, so without an explicit log call
    a failing ndpiReader invocation would be completely invisible (a real
    gap found and fixed live against nostromo, where this exact silence
    made a startup failure impossible to diagnose from the running
    daemon)."""
    with tempfile.NamedTemporaryFile(suffix=".json") as out:
        result = subprocess.run(
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
        if result.returncode != 0:
            _log_error(
                "gowiththeflow: ndpiReader exited %d: %s"
                % (result.returncode, result.stderr.decode("utf-8", errors="replace")[:500])
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
    process lifetime.

    Catches broad exceptions around each burst (rather than letting one
    propagate and permanently kill this thread with zero trace -- see
    run_capture_burst()'s docstring on why silent failure is worse here
    than in most places) and backs off for a full burst duration before
    retrying, so a persistent failure logs steadily rather than spinning
    in a tight, syslog-flooding crash loop."""
    while True:
        try:
            raw = run_capture_burst(interfaces, burst_duration_s)
            for record in parse_ndpi_output(raw, local_subnets):
                on_classification(record)
        except Exception as e:
            _log_error("gowiththeflow: DPI capture burst failed: %r" % (e,))
            time.sleep(burst_duration_s)

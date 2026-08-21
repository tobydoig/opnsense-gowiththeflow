"""Daemon entrypoint: wires up all of Phase A's modules into a running
process -- pf state polling, DNS/SNI sniffing, hostname resolution,
rollup/retention, and local-host identity refresh.

This file is glue code: every piece it calls is already unit-tested
against fixtures (Phase A). Actually *running* it needs root/pcap
privileges, a real pf state table, and a real OPNsense box, so this is
only exercised end-to-end in Phase B, per the project plan.
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field

import correlator
import db
import dns_sniffer
import hostcache
import localhost_identity
import ptr_resolver
import rollup
import sni_sniffer
from pf_state_poller import PfStatePoller
from sni_sniffer import FlowHintCache

POLL_INTERVAL_S = 5
LOCALHOST_REFRESH_INTERVAL_S = 5 * 60
HOURLY_JOB_INTERVAL_S = 60 * 60
DAILY_JOB_INTERVAL_S = 24 * 60 * 60
PTR_TTL_S = 24 * 3600


@dataclass
class Config:
    db_path: str = "/var/db/gowiththeflow/flows.db"
    capture_interfaces: list[str] = field(default_factory=list)
    local_subnets: list[str] = field(default_factory=list)
    extra_tls_ports: list[int] = field(default_factory=list)
    static_overrides: list[tuple[str, str]] = field(default_factory=list)
    enable_dns_sniffing: bool = True
    enable_sni_sniffing: bool = True
    enable_ptr_fallback: bool = True
    raw_retention_days: int = 10
    rollup_hourly_retention_days: int = 45
    rollup_daily_retention_days: int = 730


def run(config: Config) -> None:
    conn = db.connect(config.db_path)
    db.init_schema(conn)

    poller = PfStatePoller(config.local_subnets)
    flow_hints = FlowHintCache()
    static_overrides = correlator.parse_static_overrides(config.static_overrides)
    ptr = ptr_resolver.PtrResolver(ptr_resolver.live_resolve_fn) if config.enable_ptr_fallback else None

    dns_observations: queue.Queue = queue.Queue()
    sni_hints: queue.Queue = queue.Queue()

    if config.enable_dns_sniffing:
        threading.Thread(
            target=dns_sniffer.sniff_loop,
            args=(config.capture_interfaces, dns_observations.put),
            daemon=True,
        ).start()
    if config.enable_sni_sniffing:
        threading.Thread(
            target=sni_sniffer.sniff_loop,
            args=(
                config.capture_interfaces,
                lambda *args: sni_hints.put(args),
                config.extra_tls_ports,
            ),
            daemon=True,
        ).start()

    last_localhost_refresh = 0.0
    last_hourly_job = 0.0
    last_daily_job = 0.0

    while True:
        now = time.time()
        now_i = int(now)

        while not dns_observations.empty():
            obs = dns_observations.get_nowait()
            hostcache.upsert_hostname(conn, obs.ip, obs.hostname, "dns", obs.ttl, now_i)

        while not sni_hints.empty():
            local_ip, local_port, remote_ip, remote_port, hostname, ts = sni_hints.get_nowait()
            flow_hints.put(local_ip, local_port, remote_ip, remote_port, hostname, ts)

        flow_hints.purge_expired(now)

        pfctl_output = subprocess.run(
            ["pfctl", "-vvs", "state"], capture_output=True, text=True, check=True
        ).stdout
        diff = poller.poll(pfctl_output)
        resolver = correlator.make_resolver(conn, static_overrides, flow_hints, now_i)
        db.record_diff(conn, diff, now=now_i, resolve_hostname=resolver)

        if ptr is not None:
            # Any newly-opened session the resolver couldn't name gets a
            # rate-limited, best-effort PTR attempt; a hit is cached so the
            # *next* poll picks it up via the normal hostcache path.
            for snap in diff.opened:
                hostname, _source = resolver(snap)
                if hostname is None:
                    ptr_hostname = ptr.resolve(snap.key.remote_ip, now)
                    if ptr_hostname is not None:
                        hostcache.upsert_hostname(
                            conn, snap.key.remote_ip, ptr_hostname, "ptr", PTR_TTL_S, now_i
                        )

        if now - last_localhost_refresh >= LOCALHOST_REFRESH_INTERVAL_S:
            localhost_identity.refresh(conn, now_i)
            last_localhost_refresh = now

        if now - last_hourly_job >= HOURLY_JOB_INTERVAL_S:
            rollup.checkpoint_long_lived_sessions(conn, now_i)
            rollup.rollup_hourly(conn, now_i)
            rollup.prune_raw(conn, now_i, config.raw_retention_days)
            last_hourly_job = now

        if now - last_daily_job >= DAILY_JOB_INTERVAL_S:
            rollup.rollup_daily(conn, now_i)
            rollup.prune_hourly(conn, now_i, config.rollup_hourly_retention_days)
            rollup.prune_daily(conn, now_i, config.rollup_daily_retention_days)
            rollup.incremental_vacuum(conn)
            last_daily_job = now

        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    run(Config())

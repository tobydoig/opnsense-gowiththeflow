#!/usr/local/bin/python3
"""Daemon entrypoint: wires up all of Phase A's modules into a running
process -- pf state polling, DNS/SNI sniffing, hostname resolution,
rollup/retention, and local-host identity refresh.

This file is glue code: every piece it calls is already unit-tested
against fixtures (Phase A). Actually *running* it needs root/pcap
privileges, a real pf state table, and a real OPNsense box, so this is
only exercised end-to-end in Phase B, per the project plan.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field

import categories
import category_updater
import correlator
import db
import dns_sniffer
import hostcache
import localhost_identity
import manual_categories
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
CATEGORY_CACHE_DIR = "/var/db/gowiththeflow/categories"


class _CategoryMatcherHolder:
    """Reassigned wholesale by a background refresh thread (see
    _refresh_categories_in_background) rather than mutated in place, so
    the main loop always reads either the old or the fully-rebuilt new
    matcher, never one half-built from a partially merged files dict."""

    def __init__(self, matcher: categories.CategoryMatcher):
        self.matcher = matcher

    def categorize(self, hostname: str | None) -> str | None:
        # A hand-curated call (manual_categories.py) always wins over the
        # v2fly-derived lookup -- same precedence static_overrides gets
        # over the automated hostname resolvers in correlator.py.
        override = manual_categories.categorize(hostname)
        if override is not None:
            return override
        return self.matcher.categorize(hostname)


def _refresh_categories_in_background(holder: "_CategoryMatcherHolder") -> None:
    def _do_refresh():
        files = category_updater.refresh(CATEGORY_CACHE_DIR)
        holder.matcher = categories.CategoryMatcher(files)

    threading.Thread(target=_do_refresh, daemon=True).start()


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

    @classmethod
    def load(cls, path: str) -> "Config":
        """Load settings rendered by the Settings model's Jinja config
        template (service/templates/OPNsense/GoWithTheFlow/config.json),
        written to disk on every ServiceController::reconfigureAction()
        call. Falls back to all-defaults (equivalent to a disabled plugin)
        if reconfigure has never run yet, e.g. right after a fresh install.
        """
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return cls()

        return cls(
            capture_interfaces=data.get("capture_interfaces", []),
            local_subnets=data.get("local_subnets", []),
            extra_tls_ports=data.get("extra_tls_ports", []),
            static_overrides=[tuple(row) for row in data.get("static_overrides", [])],
            enable_dns_sniffing=bool(data.get("enable_dns_sniffing", True)),
            enable_sni_sniffing=bool(data.get("enable_sni_sniffing", True)),
            enable_ptr_fallback=bool(data.get("enable_ptr_fallback", True)),
            raw_retention_days=int(data.get("raw_retention_days", 10)),
            rollup_hourly_retention_days=int(data.get("rollup_hourly_retention_days", 45)),
            rollup_daily_retention_days=int(data.get("rollup_daily_retention_days", 730)),
        )


def run(config: Config) -> None:
    conn = db.connect(config.db_path)
    db.init_schema(conn)

    poller = PfStatePoller(config.local_subnets)
    poller.seed(db.load_live_sessions_as_snapshots(conn))
    poller.seed_internal_pairs(db.load_internal_live_sessions_as_snapshots(conn))
    flow_hints = FlowHintCache()
    static_overrides = correlator.parse_static_overrides(config.static_overrides)
    ptr = ptr_resolver.PtrResolver(ptr_resolver.live_resolve_fn) if config.enable_ptr_fallback else None

    # Whatever's on disk from a previous run, if anything -- categorize()
    # just returns None for everything until the background refresh below
    # completes, rather than blocking startup on a network fetch.
    category_holder = _CategoryMatcherHolder(
        categories.CategoryMatcher(category_updater.load_cached_files(CATEGORY_CACHE_DIR))
    )
    _refresh_categories_in_background(category_holder)

    dns_observations: queue.Queue = queue.Queue()
    sni_hints: queue.Queue = queue.Queue()

    # scapy's sniff() raises StopIteration given an empty interface list
    # (real crash caught running this under rc.d with no interfaces
    # configured yet -- a legitimate state right after a fresh install,
    # before Settings has ever been saved) -- so both sniffers additionally
    # require at least one configured interface, not just their own toggle.
    if config.enable_dns_sniffing and config.capture_interfaces:
        threading.Thread(
            target=dns_sniffer.sniff_loop,
            args=(config.capture_interfaces, dns_observations.put),
            daemon=True,
        ).start()
    if config.enable_sni_sniffing and config.capture_interfaces:
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

        # Absolute path, not just "pfctl" -- rc.d's PATH is minimal (this
        # is what caught localhost_identity.refresh()'s equivalent bug
        # with "configctl"; fixed proactively here for the same reason).
        pfctl_output = subprocess.run(
            ["/sbin/pfctl", "-vvs", "state"], capture_output=True, text=True, check=True
        ).stdout
        diff = poller.poll(pfctl_output)
        resolver = correlator.make_resolver(
            conn, static_overrides, flow_hints, now_i, categorize_fn=category_holder.categorize
        )
        db.record_diff(conn, diff, now=now_i, resolve_hostname=resolver)

        # Same already-fetched pfctl_output text, re-parsed by
        # poll_internal_pairs() itself -- no new subprocess call. Internal
        # (local<->local) pairs have no hostname to resolve, so there's no
        # equivalent of the PTR-fallback block below for this pipeline.
        internal_diff = poller.poll_internal_pairs(pfctl_output)
        db.record_internal_diff(conn, internal_diff, now=now_i)

        if ptr is not None:
            # Any newly-opened session the resolver couldn't name gets a
            # rate-limited, best-effort PTR attempt; a hit is cached so the
            # *next* poll picks it up via the normal hostcache path.
            for snap in diff.opened:
                hostname, _source, _category = resolver(snap)
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
            rollup.checkpoint_long_lived_internal_sessions(conn, now_i)
            rollup.rollup_internal_hourly(conn, now_i)
            rollup.prune_raw(
                conn, now_i, config.raw_retention_days,
                table="internal_connections_raw", rollup_watermark_kind="internal_hourly",
            )
            last_hourly_job = now

        if now - last_daily_job >= DAILY_JOB_INTERVAL_S:
            rollup.rollup_daily(conn, now_i)
            rollup.prune_hourly(conn, now_i, config.rollup_hourly_retention_days)
            rollup.prune_daily(conn, now_i, config.rollup_daily_retention_days)
            rollup.rollup_internal_daily(conn, now_i)
            rollup.prune_hourly(
                conn, now_i, config.rollup_hourly_retention_days,
                table="internal_rollup_hourly", rollup_watermark_kind="internal_daily",
            )
            rollup.prune_daily(conn, now_i, config.rollup_daily_retention_days, table="internal_rollup_daily")
            rollup.incremental_vacuum(conn)
            _refresh_categories_in_background(category_holder)
            last_daily_job = now

        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    # Real bug caught running this under rc.d on the OPNsense 26.7 test
    # VM: rc.subr's default "start" handling just does `eval "$command
    # $args"` synchronously (see _run_rc_doit in /etc/rc.subr) -- it does
    # NOT fork/detach the command itself, unlike what "command_interpreter"
    # in the rc.d script might suggest (that variable is only used for
    # status-checking via check_pidfile/check_process, not for launching).
    # A script that never exits (this one loops forever) hangs the rc.d
    # "start" action forever, and no pidfile ever gets written. The real
    # os-netflow plugin's flowd_aggregate.py -- the closest cousin to this
    # daemon -- solves this with OPNsense core's own bundled Daemonize
    # helper (fork + setsid + fd redirection + pidfile writing), imported
    # from /usr/local/opnsense/site-python -- which, confirmed on the real
    # VM, is *not* on Python's default sys.path; every script that uses it
    # inserts that directory itself first.
    import sys

    sys.path.insert(0, "/usr/local/opnsense/site-python")
    from daemonize import Daemonize

    _config = Config.load("/var/etc/gowiththeflow.json")
    _daemon = Daemonize(app="gowiththeflow", pid="/var/run/gowiththeflow.pid", action=lambda: run(_config))
    _daemon.start()

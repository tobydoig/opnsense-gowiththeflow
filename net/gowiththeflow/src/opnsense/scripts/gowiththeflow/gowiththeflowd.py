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

import itertools
import json
import queue
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, field

import block_rules_engine
import categories
import category_updater
import correlator
import db
import dns_sniffer
import dpi_classifier
import hostcache
import live_ticks
import localhost_identity
import manual_categories
import ptr_resolver
import rollup
import sni_sniffer
from pf_state_poller import PfStatePoller
from sni_sniffer import FlowHintCache

try:
    import syslog
except ImportError:  # syslog is POSIX-only -- this module's own tests run on Windows
    syslog = None


def _log_error(message: str) -> None:
    if syslog is not None:
        syslog.syslog(syslog.LOG_ERR, message)


POLL_INTERVAL_S = 5
LOCALHOST_REFRESH_INTERVAL_S = 5 * 60
HOURLY_JOB_INTERVAL_S = 60 * 60
DAILY_JOB_INTERVAL_S = 24 * 60 * 60
SCHEDULE_RECONCILE_INTERVAL_S = 60
PTR_TTL_S = 24 * 3600
# Comfortably above the Live Overview chart's fixed 30-minute range --
# pruned every poll cycle (not just hourly, see the prune_live_ticks
# call below), so there's no large lag to buffer against.
LIVE_TICK_RETENTION_S = 35 * 60
# An `opened` pf state this young could plausibly have opened within
# roughly the last poll interval, so its full cumulative bytes are
# charged to this tick as a genuinely new contribution -- see
# live_ticks.compute_tick_deltas()'s docstring for why an older one
# instead only establishes a baseline.
LIVE_TICK_NEW_SESSION_MAX_AGE_S = POLL_INTERVAL_S * 2
# nDPI needs several packets of a flow to classify it, and ndpiReader's
# JSON output is only written once the process itself exits (confirmed
# directly -- see dpi_classifier.py's module docstring), so this is a
# batch cadence, not a poll interval: how long each back-to-back capture
# burst runs before its results become available.
DPI_BURST_DURATION_S = 60
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
    # Unlike the sniffers above, real CPU cost on a busy network hasn't
    # been measured yet -- default this one off, opt-in.
    enable_dpi: bool = False
    # Unlike enable_dpi, this rides the already-running DNS sniffer
    # thread rather than adding new capture cost -- the unmeasured part
    # here is DB write volume (see dns_query_log's own schema comment),
    # mitigated by an hourly-bucketed upsert rather than a raw log, so
    # this defaults on.
    enable_dns_query_log: bool = True
    raw_retention_days: int = 10
    rollup_hourly_retention_days: int = 8
    rollup_daily_retention_days: int = 32
    dns_query_log_retention_days: int = 7

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
            enable_dpi=bool(data.get("enable_dpi", False)),
            enable_dns_query_log=bool(data.get("enable_dns_query_log", True)),
            raw_retention_days=int(data.get("raw_retention_days", 10)),
            rollup_hourly_retention_days=int(data.get("rollup_hourly_retention_days", 8)),
            rollup_daily_retention_days=int(data.get("rollup_daily_retention_days", 32)),
            dns_query_log_retention_days=int(data.get("dns_query_log_retention_days", 7)),
        )


def _reconcile_schedules(conn, now_i: int) -> None:
    """Wraps block_rules_engine.reconcile_all() so a genuinely unexpected
    failure (as opposed to one bad rule's own data, which reconcile_all()
    already isolates and logs itself) logs and lets the main loop
    continue, rather than taking the *entire* daemon down silently --
    the exact class of bug this project already found and fixed once
    this session for the DNS sniffer threads (Daemonize redirects
    stderr to /dev/null, so an unhandled exception anywhere up here
    would otherwise vanish with zero trace)."""
    try:
        block_rules_engine.reconcile_all(conn, now_i)
    except Exception as e:
        _log_error("gowiththeflow: schedule reconcile failed: %r" % (e,))


def run(config: Config) -> None:
    conn = db.connect(config.db_path)
    db.init_schema(conn)

    poller = PfStatePoller(config.local_subnets)
    seed_snapshots = db.load_live_sessions_as_snapshots(conn)
    poller.seed(seed_snapshots)
    # Seeds live_ticks' own per-key baseline from the same snapshot --
    # without this, a daemon *restart* (as opposed to a genuinely cold
    # table) would re-trigger the "long-lived session newly entering
    # tracking" age_s guard in compute_tick_deltas() for every
    # already-open session, discarding real bytes it should have
    # correctly diffed against. The `True` marks these as *seeded*
    # (last written to the DB by a possibly-much-earlier process, not
    # actually observed this run) -- see compute_tick_deltas()'s
    # docstring for why that's diffed against differently than a normal
    # tick-to-tick baseline.
    tick_prev_bytes = {snap.key: (snap.bytes_in, snap.bytes_out, True) for snap in seed_snapshots}
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
    dns_query_events: queue.Queue = queue.Queue()
    sni_hints: queue.Queue = queue.Queue()
    dpi_results: queue.Queue = queue.Queue()
    ptr_work: queue.Queue = queue.Queue()
    ptr_results: queue.Queue = queue.Queue()
    # Dedups against re-enqueueing a peer already queued/in-flight on the
    # PTR worker thread -- without this, a peer that stays unresolved
    # across several back-to-back polls (retried every poll now, see
    # ptr_resolver.py) would pile up redundant queue entries faster than
    # a slow lookup could drain them.
    ptr_in_flight: set[str] = set()

    # scapy's sniff() raises StopIteration given an empty interface list
    # (real crash caught running this under rc.d with no interfaces
    # configured yet -- a legitimate state right after a fresh install,
    # before Settings has ever been saved) -- so both sniffers additionally
    # require at least one configured interface, not just their own toggle.
    if config.enable_dns_sniffing and config.capture_interfaces:
        # Meaningless without the DNS sniffer thread itself running --
        # on_query_event is just an extra callback fed from the same
        # packets that thread is already dissecting, not a separate
        # capture path. Passed as None (a no-op inside sniff_loop) rather
        # than skipping the thread entirely when the toggle is off.
        on_query_event = dns_query_events.put if config.enable_dns_query_log else None
        threading.Thread(
            target=dns_sniffer.sniff_loop,
            args=(config.capture_interfaces, dns_observations.put),
            kwargs={"on_query_event": on_query_event},
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
    if config.enable_dpi and config.capture_interfaces:
        threading.Thread(
            target=dpi_classifier.capture_loop,
            args=(config.capture_interfaces, config.local_subnets, dpi_results.put, DPI_BURST_DURATION_S),
            daemon=True,
        ).start()
    if ptr is not None:
        threading.Thread(
            target=ptr_resolver.resolve_loop,
            args=(ptr_work, ptr_results.put, ptr),
            daemon=True,
        ).start()

    last_localhost_refresh = 0.0
    last_hourly_job = 0.0
    last_daily_job = 0.0

    # Reconciled once here, unlike the hourly/daily jobs above, so a
    # daemon restart mid-window re-asserts the correct blocked/unblocked
    # state immediately rather than leaving it stale for up to a full
    # SCHEDULE_RECONCILE_INTERVAL_S.
    last_schedule_reconcile = time.time()
    _reconcile_schedules(conn, int(last_schedule_reconcile))

    while True:
        try:
            now = time.time()
            now_i = int(now)

            while not dns_observations.empty():
                obs = dns_observations.get_nowait()
                hostcache.upsert_hostname(conn, obs.ip, obs.hostname, "dns", obs.ttl, now_i)

            while not dns_query_events.empty():
                db.record_dns_query_event(conn, dns_query_events.get_nowait())

            while not sni_hints.empty():
                local_ip, local_port, remote_ip, remote_port, hostname, ts = sni_hints.get_nowait()
                flow_hints.put(local_ip, local_port, remote_ip, remote_port, hostname, ts)

            flow_hints.purge_expired(now)

            # dpi_results only gets new entries roughly once per
            # DPI_BURST_DURATION_S (a batch cadence, not a live one -- see
            # dpi_classifier.py) -- draining every poll cycle regardless is
            # still correct and cheap, same as the other queues above.
            while not dpi_results.empty():
                rec = dpi_results.get_nowait()
                db.update_dpi_protocol(
                    conn, rec.proto, rec.local_ip, rec.local_port,
                    rec.peer_ip, rec.peer_port, rec.dpi_protocol,
                )

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

            tick_rows, tick_prev_bytes = live_ticks.compute_tick_deltas(
                diff, tick_prev_bytes, LIVE_TICK_NEW_SESSION_MAX_AGE_S
            )
            db.record_live_ticks(conn, now_i, tick_rows)
            # Retention here is minutes, not days like the rollup tables --
            # pruned every cycle so the table never balloons between prunes.
            rollup.prune_live_ticks(conn, now_i, LIVE_TICK_RETENTION_S)

            if ptr is not None:
                # Every still-open session the resolver couldn't name (not
                # just newly-opened ones -- a real gap found live: a burst of
                # simultaneous new sessions from several devices could exceed
                # one poll's worth of PTR budget, and a peer that missed its
                # one-shot attempt at open time was never retried for the
                # life of that flow) gets queued for a background, rate-
                # limited, best-effort PTR attempt on ptr_resolver.resolve_loop's
                # own thread -- never inline here, so a slow upstream resolver
                # can't stall pf state polling for the whole network. A hit is
                # cached so the *next* poll picks it up via the normal
                # hostcache path. Skipped entirely for a local peer -- its IP
                # would never PTR-resolve to anything meaningful, and
                # db.record_diff never calls the resolver for one anyway.
                for snap in itertools.chain(diff.opened, diff.updated):
                    if snap.peer_is_local:
                        continue
                    peer_ip = snap.key.peer_ip
                    if peer_ip in ptr_in_flight:
                        continue
                    hostname, _source, _category = resolver(snap)
                    if hostname is None:
                        ptr_in_flight.add(peer_ip)
                        ptr_work.put(peer_ip)

                while not ptr_results.empty():
                    peer_ip, ptr_hostname = ptr_results.get_nowait()
                    ptr_in_flight.discard(peer_ip)
                    if ptr_hostname is not None:
                        hostcache.upsert_hostname(
                            conn, peer_ip, ptr_hostname, "ptr", PTR_TTL_S, now_i
                        )

            if now - last_localhost_refresh >= LOCALHOST_REFRESH_INTERVAL_S:
                localhost_identity.refresh(conn, now_i)
                last_localhost_refresh = now

            if now - last_schedule_reconcile >= SCHEDULE_RECONCILE_INTERVAL_S:
                _reconcile_schedules(conn, now_i)
                last_schedule_reconcile = now

            if now - last_hourly_job >= HOURLY_JOB_INTERVAL_S:
                rollup.checkpoint_long_lived_sessions(conn, now_i)
                rollup.rollup_hourly(conn, now_i)
                rollup.prune_raw(conn, now_i, config.raw_retention_days)
                last_hourly_job = now

            if now - last_daily_job >= DAILY_JOB_INTERVAL_S:
                rollup.rollup_daily(conn, now_i)
                rollup.prune_hourly(conn, now_i, config.rollup_hourly_retention_days)
                rollup.prune_daily(conn, now_i, config.rollup_daily_retention_days)
                # dns_query_log has no rollup step of its own (see its schema
                # comment) -- prune_daily()'s own `table=` param already
                # generalizes to it unmodified, since it just does a plain
                # DELETE ... WHERE bucket_start < ? regardless of table.
                rollup.prune_daily(conn, now_i, config.dns_query_log_retention_days, table="dns_query_log")
                rollup.incremental_vacuum(conn)
                _refresh_categories_in_background(category_holder)
                last_daily_job = now

            time.sleep(POLL_INTERVAL_S)
        except Exception:
            # Last-resort net around the *entire* loop body -- every call
            # above this point runs unguarded except for the few specific
            # spots (schedule reconcile, DNS/SNI/DPI/PTR threads) already
            # wrapped individually elsewhere in this project. Confirmed for
            # real: an overnight run with schedule-driven blocking active
            # for the first time died with absolutely zero trace anywhere
            # (Daemonize redirects stderr to /dev/null, and nothing here
            # caught whatever it was), so the daemon's own log had nothing
            # in it at all -- the only symptom was the Live page going
            # stale. Logs the full traceback, not just repr(e), since this
            # is the catch-all of last resort and needs to actually explain
            # itself when every more specific guard has already had its
            # turn. Sleeps the same as a normal cycle before retrying so a
            # deterministic failure (e.g. a missing binary) logs steadily
            # rather than spinning in a tight, syslog-flooding crash loop.
            _log_error("gowiththeflow: main loop iteration failed:\n%s" % traceback.format_exc())
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

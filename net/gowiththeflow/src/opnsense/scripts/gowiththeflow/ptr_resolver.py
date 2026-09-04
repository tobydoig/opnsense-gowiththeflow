"""Rate-limited, last-resort reverse-DNS (PTR) lookups with negative
caching -- only ever consulted when DNS/SNI have nothing.

Stage A7: exercised with an injected `resolve_fn` in place of a real
`socket.gethostbyaddr` call, so tests are fully deterministic and never
touch the network. The live entrypoint wires a real resolver in
gowiththeflowd.py, proven against real traffic in Phase B.

`resolve_loop()` (added after a real gap found live: a burst of new
sessions from several devices at once could exhaust a single poll cycle's
lookup budget, and a peer that missed its one-shot attempt at flow-open
was never retried for the life of that flow) runs PTR lookups on a
dedicated background thread rather than inline in gowiththeflowd.py's main
poll loop. That's the actual reason for the rate limit below -- not
memory or CPU cost (the cache is a couple of tiny in-process dicts) but
that `socket.gethostbyaddr()` is a blocking network call, and one running
inline in the poll loop could stall pf state polling for every device on
the network if the upstream resolver is slow. Off the hot path, the
budget can be far more generous, and gowiththeflowd.py now retries any
still-open, still-unresolved session's peer on every poll rather than
only once at open time.
"""

from __future__ import annotations

import time
from typing import Callable

try:
    import syslog
except ImportError:  # syslog is POSIX-only -- this module's own tests run on Windows
    syslog = None


def _log_error(message: str) -> None:
    if syslog is not None:
        syslog.syslog(syslog.LOG_ERR, message)


# Generous now that lookups run off the main poll loop (see module
# docstring) -- was 10/60s when this ran inline and needed to bound how
# long a single poll cycle could be blocked.
DEFAULT_MAX_LOOKUPS_PER_WINDOW = 60
DEFAULT_WINDOW_S = 60
# Short enough to act as a retry backoff rather than an hour-long lockout
# -- a still-open session's peer gets re-enqueued on every poll, so a
# transient failure (a momentary resolver hiccup, not a real NXDOMAIN)
# recovers in minutes instead of blocking the rest of that flow's life.
NEGATIVE_CACHE_TTL_S = 300


class PtrResolver:
    def __init__(
        self,
        resolve_fn: Callable[[str], str | None],
        max_lookups_per_window: int = DEFAULT_MAX_LOOKUPS_PER_WINDOW,
        window_s: int = DEFAULT_WINDOW_S,
    ):
        self._resolve_fn = resolve_fn
        self._max_lookups = max_lookups_per_window
        self._window_s = window_s
        self._window_start: float | None = None
        self._window_count = 0
        self._negative_cache: dict[str, float] = {}

    def resolve(self, ip: str, now: float) -> str | None:
        if ip in self._negative_cache:
            if now < self._negative_cache[ip]:
                return None
            del self._negative_cache[ip]

        if self._window_start is None or now - self._window_start >= self._window_s:
            self._window_start = now
            self._window_count = 0

        if self._window_count >= self._max_lookups:
            return None  # rate-limited -- don't attempt a lookup this cycle

        self._window_count += 1
        hostname = self._resolve_fn(ip)
        if hostname is None:
            self._negative_cache[ip] = now + NEGATIVE_CACHE_TTL_S
        return hostname


def resolve_loop(
    work_queue,
    on_result: "Callable[[tuple[str, str | None]], None]",
    resolver: PtrResolver,
) -> None:
    """Background thread loop: pulls candidate peer IPs off `work_queue`
    and resolves each via `resolver`, off gowiththeflowd.py's main poll
    loop thread -- see module docstring for why a blocking
    socket.gethostbyaddr() must never run inline there.

    Always calls on_result((ip, hostname_or_None)) -- a single tuple
    argument, matching how sni_sniffer.py's own callback packs its
    result before queuing -- exactly once per dequeued item, even on a
    miss or a rate-limited skip. The caller uses this to clear its own
    in-flight tracking so a still-unresolved, still-open session's peer
    is eligible to be retried on a later poll rather than being stuck
    forever once dequeued.

    A REAL production bug, caught live: this used to call
    on_result(ip, hostname) as two positional arguments so that
    gowiththeflowd.py could wire `ptr_results.put` directly as
    `on_result`. That silently broke, because queue.Queue.put(item,
    block=True, timeout=None) treats a second positional argument as
    `block`, not a second queued value -- so the queue only ever held a
    bare IP string, never a tuple. The instant a real PTR result came
    back on a busy network, the main loop's `peer_ip, ptr_hostname =
    ptr_results.get_nowait()` tried to unpack that string's individual
    characters and blew up with "too many values to unpack", killing the
    whole daemon before this file's own top-level catch-all existed to
    even log it. Passing a single tuple is directly compatible with
    Queue.put with no wrapping needed, and is intrinsically unambiguous.

    Never exits on its own, matching dns_sniffer.sniff_loop/
    sni_sniffer.sniff_loop/dpi_classifier.capture_loop. Wraps each item in
    its own try/except -- Daemonize redirects stderr to /dev/null, so an
    unhandled exception here would otherwise silently kill this thread
    for the rest of the process's life."""
    while True:
        ip = work_queue.get()
        try:
            hostname = resolver.resolve(ip, time.time())
        except Exception as e:
            _log_error("gowiththeflow: PTR lookup for %s failed: %r" % (ip, e))
            hostname = None
        on_result((ip, hostname))


def live_resolve_fn(ip: str) -> str | None:
    """The real PTR lookup, wired up in gowiththeflowd.py. Not exercised
    by Stage A7's unit tests."""
    import socket

    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return None

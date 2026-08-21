"""Rate-limited, last-resort reverse-DNS (PTR) lookups with negative
caching -- only ever consulted when DNS/SNI have nothing.

Stage A7: exercised with an injected `resolve_fn` in place of a real
`socket.gethostbyaddr` call, so tests are fully deterministic and never
touch the network. The live entrypoint wires a real resolver in
gowiththeflowd.py, proven against real traffic in Phase B.
"""

from __future__ import annotations

from typing import Callable

DEFAULT_MAX_LOOKUPS_PER_WINDOW = 10
DEFAULT_WINDOW_S = 60
NEGATIVE_CACHE_TTL_S = 3600


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


def live_resolve_fn(ip: str) -> str | None:
    """The real PTR lookup, wired up in gowiththeflowd.py. Not exercised
    by Stage A7's unit tests."""
    import socket

    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return None

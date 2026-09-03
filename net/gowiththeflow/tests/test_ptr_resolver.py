import queue

from ptr_resolver import NEGATIVE_CACHE_TTL_S, PtrResolver, resolve_loop


def test_returns_hostname_from_the_injected_resolver():
    resolver = PtrResolver(resolve_fn=lambda ip: "resolved.example" if ip == "1.2.3.4" else None)
    assert resolver.resolve("1.2.3.4", now=1000) == "resolved.example"


def test_negative_result_is_cached_and_resolver_not_called_again():
    calls = []

    def fake_resolve(ip):
        calls.append(ip)
        return None

    resolver = PtrResolver(resolve_fn=fake_resolve)
    assert resolver.resolve("1.2.3.4", now=1000) is None
    assert resolver.resolve("1.2.3.4", now=1010) is None
    assert calls == ["1.2.3.4"]  # second call served from the negative cache


def test_negative_cache_expires_and_resolver_is_retried(tmp_path=None):
    calls = []

    def fake_resolve(ip):
        calls.append(ip)
        return None

    resolver = PtrResolver(resolve_fn=fake_resolve)
    resolver.resolve("1.2.3.4", now=1000)
    resolver.resolve("1.2.3.4", now=1000 + NEGATIVE_CACHE_TTL_S + 1)
    assert calls == ["1.2.3.4", "1.2.3.4"]


def test_rate_limit_blocks_further_lookups_within_the_same_window():
    calls = []

    def fake_resolve(ip):
        calls.append(ip)
        return f"host-{ip}"

    resolver = PtrResolver(resolve_fn=fake_resolve, max_lookups_per_window=2, window_s=60)
    assert resolver.resolve("1.1.1.1", now=1000) == "host-1.1.1.1"
    assert resolver.resolve("2.2.2.2", now=1010) == "host-2.2.2.2"
    # Third distinct IP within the same 60s window is rate-limited.
    assert resolver.resolve("3.3.3.3", now=1020) is None
    assert calls == ["1.1.1.1", "2.2.2.2"]


def test_rate_limit_window_resets_after_window_s():
    calls = []

    def fake_resolve(ip):
        calls.append(ip)
        return f"host-{ip}"

    resolver = PtrResolver(resolve_fn=fake_resolve, max_lookups_per_window=1, window_s=60)
    assert resolver.resolve("1.1.1.1", now=1000) == "host-1.1.1.1"
    assert resolver.resolve("2.2.2.2", now=1010) is None  # still in the same window

    # A new window starts.
    assert resolver.resolve("3.3.3.3", now=1061) == "host-3.3.3.3"
    assert calls == ["1.1.1.1", "3.3.3.3"]


class _StopLoop(Exception):
    """resolve_loop() never returns on its own -- raised from on_result
    to unwind it after the test has seen what it needs to see."""


def test_resolve_loop_reports_a_hit():
    resolver = PtrResolver(resolve_fn=lambda ip: "resolved.example")
    work: queue.Queue = queue.Queue()
    results = []
    work.put("1.2.3.4")

    def on_result(ip, hostname):
        results.append((ip, hostname))
        raise _StopLoop

    try:
        resolve_loop(work, on_result, resolver)
    except _StopLoop:
        pass
    assert results == [("1.2.3.4", "resolved.example")]


def test_resolve_loop_reports_a_miss_not_just_a_hit():
    """A miss must still call on_result (with None) exactly once -- the
    caller relies on this to clear its own in-flight tracking so the peer
    is eligible to be retried on a later poll, not stuck forever."""
    resolver = PtrResolver(resolve_fn=lambda ip: None)
    work: queue.Queue = queue.Queue()
    results = []
    work.put("1.2.3.4")

    def on_result(ip, hostname):
        results.append((ip, hostname))
        raise _StopLoop

    try:
        resolve_loop(work, on_result, resolver)
    except _StopLoop:
        pass
    assert results == [("1.2.3.4", None)]


def test_resolve_loop_survives_a_raising_resolver_and_keeps_processing():
    """A crash inside the resolver for one item must not kill the whole
    background thread -- Daemonize redirects stderr to /dev/null, so an
    unhandled exception here would otherwise silently end PTR resolution
    for the rest of the process's life."""

    calls = []

    def flaky_resolve(ip):
        calls.append(ip)
        if ip == "1.2.3.4":
            raise RuntimeError("boom")
        return "resolved.example"

    resolver = PtrResolver(resolve_fn=flaky_resolve)
    work: queue.Queue = queue.Queue()
    results = []
    work.put("1.2.3.4")
    work.put("5.6.7.8")

    def on_result(ip, hostname):
        results.append((ip, hostname))
        if len(results) == 2:
            raise _StopLoop

    try:
        resolve_loop(work, on_result, resolver)
    except _StopLoop:
        pass
    assert results == [("1.2.3.4", None), ("5.6.7.8", "resolved.example")]

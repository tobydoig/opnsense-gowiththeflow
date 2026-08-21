from ptr_resolver import PtrResolver


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
    resolver.resolve("1.2.3.4", now=1000 + 3600 + 1)  # past NEGATIVE_CACHE_TTL_S
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

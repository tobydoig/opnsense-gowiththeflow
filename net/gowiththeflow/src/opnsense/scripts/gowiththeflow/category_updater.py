"""Fetches and disk-caches the v2fly domain list files categories.py
needs, refreshing periodically. Network-fetch logic lives here, kept
separate from categories.py so that module stays fully offline-testable.
"""

from __future__ import annotations

import concurrent.futures
import os
import urllib.request

from categories import CATEGORY_SOURCES, parse_file

BASE_URL = "https://raw.githubusercontent.com/v2fly/domain-list-community/master/data/"
FETCH_TIMEOUT_S = 10
MAX_CONCURRENT_FETCHES = 12


def _http_get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as resp:  # noqa: S310 (fixed https, not user input)
        return resp.read().decode("utf-8")


def _fetch_or_none(fetch_fn, name: str) -> str | None:
    # One retry -- CATEGORY_SOURCES's full include chain is ~280 files
    # fetched over real, not-always-pristine home internet connections
    # (confirmed slower/less reliable than the isolated lab VM this was
    # first tested against), and a single transient failure otherwise
    # leaves that file's domains uncategorized for a full day, until the
    # next scheduled refresh.
    for _attempt in range(2):
        try:
            text = fetch_fn(name)
        except Exception:
            continue
        if text is not None:
            return text
    return None


def resolve_top_level_files(category_sources: dict[str, list[str]] | None = None) -> set[str]:
    """The file names named directly in CATEGORY_SOURCES. Nested
    `include:` targets aren't known until after fetching, so fetch_all()
    discovers and fetches those iteratively."""
    names: set[str] = set()
    for sources in (category_sources or CATEGORY_SOURCES).values():
        names.update(sources)
    return names


def fetch_all(
    category_sources: dict[str, list[str]] | None = None,
    fetch_fn=None,
) -> dict[str, str]:
    """Fetches every file CATEGORY_SOURCES needs, following `include:`
    chains iteratively until no new file names appear. `fetch_fn(name) ->
    str` is injectable for tests; defaults to a real HTTP GET against
    BASE_URL. Returns only what was successfully fetched -- a file that
    fails to fetch (even after one retry) is just missing from the
    result, not an exception; callers merge this with whatever's already
    cached so one bad fetch doesn't wipe out previously-good data for
    that file.

    Fetches within a round concurrently (bounded by
    MAX_CONCURRENT_FETCHES) -- CATEGORY_SOURCES's full include chain is
    ~280 files, and fetching those one at a time made the very first
    refresh after an install/upgrade take long enough on a real home
    connection that most traffic seen in the meantime went
    uncategorized. New `include:` targets are only known after parsing
    a round's fetched text, so this still proceeds in rounds -- just
    with each round's own fetches happening in parallel rather than
    serially."""
    fetch_fn = fetch_fn or (lambda name: _http_get(BASE_URL + name))
    seen: set[str] = set()
    fetched: dict[str, str] = {}
    to_fetch = resolve_top_level_files(category_sources)

    while to_fetch:
        round_names = list(to_fetch)
        seen.update(round_names)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(MAX_CONCURRENT_FETCHES, len(round_names))
        ) as pool:
            texts = pool.map(lambda name: _fetch_or_none(fetch_fn, name), round_names)

        to_fetch = set()
        for name, text in zip(round_names, texts):
            if text is None:
                continue
            fetched[name] = text
            for inc in parse_file(text).includes:
                if inc.name not in seen:
                    to_fetch.add(inc.name)

    return fetched


def _safe_cache_filename(name: str) -> str:
    # Include-target names ultimately come from remote file content (a
    # nested `include:` line), not just our own static CATEGORY_SOURCES
    # list -- cheap defensive sanitizing against a path-traversal-shaped
    # name before it ever becomes a filesystem path.
    return name.replace("/", "_").replace("\\", "_").replace("..", "_")


def load_cached_files(cache_dir: str) -> dict[str, str]:
    files: dict[str, str] = {}
    if not os.path.isdir(cache_dir):
        return files
    for entry in os.listdir(cache_dir):
        try:
            with open(os.path.join(cache_dir, entry), encoding="utf-8") as f:
                files[entry] = f.read()
        except OSError:
            continue
    return files


def save_cached_files(cache_dir: str, files: dict[str, str]) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    for name, text in files.items():
        with open(os.path.join(cache_dir, _safe_cache_filename(name)), "w", encoding="utf-8") as f:
            f.write(text)


def refresh(
    cache_dir: str,
    category_sources: dict[str, list[str]] | None = None,
    fetch_fn=None,
) -> dict[str, str]:
    """Fetches fresh copies of everything CATEGORY_SOURCES needs, merges
    with whatever's already cached on disk (so a transient fetch failure
    for one file doesn't lose that file's previously-cached data), writes
    the merged result back to disk, and returns it ready to hand to
    CategoryMatcher(). Safe to call with no network access at all -- on
    total failure this just returns/re-saves the existing cache
    unchanged."""
    cached = load_cached_files(cache_dir)
    fetched = fetch_all(category_sources, fetch_fn)
    merged = {**cached, **fetched}  # fresh fetches win over stale cache
    save_cached_files(cache_dir, merged)
    return merged

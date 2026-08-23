"""Fetches and disk-caches the v2fly domain list files categories.py
needs, refreshing periodically. Network-fetch logic lives here, kept
separate from categories.py so that module stays fully offline-testable.
"""

from __future__ import annotations

import os
import urllib.request

from categories import CATEGORY_SOURCES, parse_file

BASE_URL = "https://raw.githubusercontent.com/v2fly/domain-list-community/master/data/"
FETCH_TIMEOUT_S = 10


def _http_get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as resp:  # noqa: S310 (fixed https, not user input)
        return resp.read().decode("utf-8")


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
    fails to fetch is just missing from the result, not an exception;
    callers merge this with whatever's already cached so one bad fetch
    doesn't wipe out previously-good data for that file."""
    fetch_fn = fetch_fn or (lambda name: _http_get(BASE_URL + name))
    to_fetch = resolve_top_level_files(category_sources)
    fetched: dict[str, str] = {}
    seen: set[str] = set()
    while to_fetch:
        name = to_fetch.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            text = fetch_fn(name)
        except Exception:
            continue
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

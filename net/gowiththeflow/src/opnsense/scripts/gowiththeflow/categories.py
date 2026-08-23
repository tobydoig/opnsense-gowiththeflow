"""Hostname -> category classification.

Not DPI -- a lookup enrichment on hostnames already resolved via
DNS/SNI/PTR (see hostcache.py), matched against the free,
actively-maintained domain lists from v2fly/domain-list-community
(github.com/v2fly/domain-list-community, MIT licensed). That project is
organized per-service (one file per site, e.g. "netflix", "youtube") plus
a smaller set of broad "category-*" files, not by the kind of everyday
category ("Streaming", "Social Media") a home network dashboard wants --
so CATEGORY_SOURCES below is our own small, curated mapping of *which*
upstream files feed *our* categories. We're not hand-listing domains
ourselves, just which files to pull.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Order matters: categorize() returns the first match, and company-wide
# files (google, microsoft, amazon, apple, ...) include their own
# ad-serving subdomains -- or, in amazon's case, its entire cloud
# infrastructure business -- as one bare (untagged) blob when we pull
# them in for Cloud/Productivity or Shopping. Confirmed against real
# data twice now: doubleclick.net is only @ads-tagged inside google's
# own file with no untagged entry, so a plain include:google (no tag
# filter, since it's just a bare name in *our* mapping) still catches
# it; and amazon's own file does a bare include:aws, so *.amazonaws.com
# and cloudfront.net were landing in Shopping until this was caught
# against a real "ec2-...amazonaws.com" hostname on the user's actual
# box. Ads/Tracking and Cloud Infrastructure both have to come before
# the broader company categories they'd otherwise be shadowed by.
CATEGORY_SOURCES: dict[str, list[str]] = {
    "Ads/Tracking": ["category-ads"],
    "Cloud Infrastructure": ["category-cdn-!cn", "cloudflare", "akamai", "fastly", "aws"],
    "Social Media": [
        "category-social-media-!cn", "tiktok", "snapchat", "pinterest", "reddit", "oculus",
    ],
    "Streaming/Video": ["netflix", "youtube", "disney", "hulu", "primevideo", "twitch"],
    "Music": ["spotify", "soundcloud", "pandora"],
    "Gaming": ["category-games-!cn", "steam", "xbox", "playstation", "nintendo", "epicgames"],
    "Communication": ["whatsapp", "telegram", "signal", "discord", "zoom", "slack"],
    "AI": ["anthropic", "openai"],
    "Cloud/Productivity": ["google", "microsoft", "apple", "dropbox"],
    "Shopping": ["amazon", "ebay", "etsy"],
}


@dataclass(frozen=True)
class Entry:
    value: str
    tags: frozenset[str]


@dataclass(frozen=True)
class IncludeDirective:
    name: str
    # A bare `include:x` line takes every entry in x. `include:x @ads`
    # (seen throughout category-ads, e.g. "include:google @ads") takes
    # only x's entries that are themselves tagged "@ads" -- individual
    # domain lines inside a file can carry their own tags too (e.g.
    # google's own file has lines like "admob.com @ads" mixed in with
    # untagged ones), which is the whole point of the tag: pulling out
    # just the ad-serving subset of an otherwise-general company file
    # rather than that file's every domain.
    tag: str | None


@dataclass
class ParsedFile:
    suffixes: list[Entry] = field(default_factory=list)
    fulls: list[Entry] = field(default_factory=list)
    regexes: list[tuple[re.Pattern, frozenset[str]]] = field(default_factory=list)
    includes: list[IncludeDirective] = field(default_factory=list)


def _split_tags(line: str) -> tuple[str, frozenset[str]]:
    parts = line.split()
    tags = frozenset(p[1:] for p in parts[1:] if p.startswith("@"))
    return parts[0], tags


def parse_file(text: str) -> ParsedFile:
    """Parses one v2fly-format domain list file's raw text.

    Format per line: a bare domain (suffix match -- also matches
    subdomains), `full:exact.host` (exact match only), `regexp:pattern`,
    `include:other_file_name` (pull in another file's rules -- not
    resolved here, since that needs the file-loading context the caller
    has), `#` comments, and optional trailing `@tag` annotations (see
    IncludeDirective's docstring)."""
    result = ParsedFile()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        value, tags = _split_tags(line)
        if value.startswith("include:"):
            # `include:x @tag1 @tag2` isn't a real thing upstream -- an
            # include's own filter is always at most one tag -- but take
            # just the first if it somehow shows up rather than crash.
            result.includes.append(
                IncludeDirective(name=value[len("include:"):], tag=next(iter(tags), None))
            )
        elif value.startswith("full:"):
            result.fulls.append(Entry(value[len("full:"):].lower(), tags))
        elif value.startswith("regexp:"):
            try:
                result.regexes.append((re.compile(value[len("regexp:"):]), tags))
            except re.error:
                continue  # malformed upstream pattern -- skip, don't crash
        else:
            result.suffixes.append(Entry(value.lower(), tags))
    return result


class CategoryMatcher:
    """Matches hostnames against rules assembled from named source files
    (already fetched to text and handed in via `files`), grouped into
    the categories from CATEGORY_SOURCES. `include:` directives are
    resolved against that same `files` mapping, respecting any `@tag`
    filter on the include itself."""

    def __init__(self, files: dict[str, str], category_sources: dict[str, list[str]] | None = None):
        self._category_rules: dict[str, ParsedFile] = {}
        for category, source_names in (category_sources or CATEGORY_SOURCES).items():
            merged = ParsedFile()
            includes = [IncludeDirective(name=n, tag=None) for n in source_names]
            self._merge_sources(includes, files, merged, set())
            self._category_rules[category] = merged

    def _merge_sources(
        self,
        includes: list[IncludeDirective],
        files: dict[str, str],
        merged: ParsedFile,
        seen: set[tuple[str, str | None]],
        inherited_tag: str | None = None,
    ) -> None:
        for inc in includes:
            # A nested include with no tag of its own (e.g. meta's bare
            # `include:facebook`) inherits whatever tag got us here in
            # the first place, rather than defaulting to "take
            # everything" -- otherwise resolving category-ads's
            # `include:meta @ads` down through meta's own untagged
            # `include:facebook` would (and, before this fix, did) pull
            # in every facebook.com domain instead of just its
            # @ads-tagged ones.
            effective_tag = inc.tag if inc.tag is not None else inherited_tag
            seen_key = (inc.name, effective_tag)
            if seen_key in seen:
                continue
            seen.add(seen_key)
            text = files.get(inc.name)
            if text is None:
                continue
            parsed = parse_file(text)
            keep = (lambda tags: True) if effective_tag is None else (lambda tags, t=effective_tag: t in tags)
            merged.suffixes.extend(e for e in parsed.suffixes if keep(e.tags))
            merged.fulls.extend(e for e in parsed.fulls if keep(e.tags))
            merged.regexes.extend((r, t) for r, t in parsed.regexes if keep(t))
            if parsed.includes:
                self._merge_sources(parsed.includes, files, merged, seen, inherited_tag=effective_tag)

    def categorize(self, hostname: str | None) -> str | None:
        """Returns the first category (in CATEGORY_SOURCES's own order)
        whose rules match, or None. A hostname matching more than one
        category's rules is resolved by that ordering, not reported as
        ambiguous."""
        if not hostname:
            return None
        hostname = hostname.lower()
        for category, rules in self._category_rules.items():
            if any(hostname == e.value for e in rules.fulls):
                return category
            if any(hostname == e.value or hostname.endswith("." + e.value) for e in rules.suffixes):
                return category
            if any(r.search(hostname) for r, _tags in rules.regexes):
                return category
        return None

"""Hand-curated hostname -> category overrides for domains that either
have no real coverage in v2fly/domain-list-community, or where its
per-company file groups things too coarsely for our purposes (see
categories.py's own comment on the amazon/aws case for an example of
the latter, fixed there directly since a clean upstream fix existed).

This is the other half of the workflow: not every real hostname a real
network sees is going to have a matching upstream file, so this module
exists to be grown incrementally from what a real box actually
observes -- see the Top Talkers "Uncategorized Hosts" tab, which lists
real, currently-uncategorized hostnames ordered by traffic volume, so
entries can be added here for the ones that matter rather than guessed
at ahead of time.

Checked *before* the v2fly-based CategoryMatcher (see
gowiththeflowd.py's _CategoryMatcherHolder.categorize()) -- an explicit
human judgment call always wins over the automated lookup, the same
precedence static_overrides gets over the automated hostname resolvers
in correlator.py.

The actual domain -> category data lives in domain_categories/, one
plain text file per category (mirroring the shape of the upstream
v2fly files themselves), rather than a hardcoded dict here -- easier to
grow, review, and diff than a giant Python literal. Each file:
  - Starts with a `# category: <Display Name>` header line, giving the
    real category label -- the filename itself is a filesystem-safe
    slug (spaces/"/" replaced with "-"), since names like "Ads/Tracking"
    and "Smart Home/IoT" can't literally be filenames.
  - Lists one domain suffix per line after that (matches itself and any
    subdomain, same rule as categories.CategoryMatcher's suffix
    entries).
  - Supports "#"-prefixed full-line comments and trailing "# ..." notes
    on a domain line, for exactly the same "why is this suffix safe"
    rationale this file used to carry inline.
"""

from __future__ import annotations

from pathlib import Path

DOMAIN_CATEGORIES_DIR = Path(__file__).parent / "domain_categories"


def _load_overrides(directory: Path) -> dict[str, str]:
    """Reads every file in `directory` into one suffix -> category dict.
    Longest-suffix-first iteration order (see categorize() below) means
    a more specific entry (e.g. "gfe.nvidia.com") always wins over a
    broader one for the same hostname, if both ever exist at once --
    not currently exercised by any real data here, but a cheap
    correctness guarantee to have regardless."""
    entries: dict[str, str] = {}
    if not directory.is_dir():
        return entries
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        category: str | None = None
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                if category is None and line[1:].strip().lower().startswith("category:"):
                    category = line.split(":", 1)[1].strip()
                continue
            domain = line.split("#", 1)[0].strip()
            if not domain:
                continue
            if category is None:
                raise ValueError(f"{path}: domain {domain!r} appears before a '# category: <Name>' header")
            entries[domain.lower()] = category
    return dict(sorted(entries.items(), key=lambda kv: len(kv[0]), reverse=True))


OVERRIDES: dict[str, str] = _load_overrides(DOMAIN_CATEGORIES_DIR)


def categorize(hostname: str | None) -> str | None:
    if not hostname:
        return None
    hostname = hostname.lower()
    for suffix, category in OVERRIDES.items():
        if hostname == suffix or hostname.endswith("." + suffix):
            return category
    return None

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
"""

from __future__ import annotations

# domain suffix (matches itself and any subdomain, same rule as
# categories.CategoryMatcher's suffix entries) -> category label.
OVERRIDES: dict[str, str] = {}


def categorize(hostname: str | None) -> str | None:
    if not hostname:
        return None
    hostname = hostname.lower()
    for suffix, category in OVERRIDES.items():
        if hostname == suffix or hostname.endswith("." + suffix):
            return category
    return None

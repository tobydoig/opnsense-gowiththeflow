from categories import CATEGORY_SOURCES, CategoryMatcher, parse_file


def test_parse_file_handles_suffix_full_regexp_and_comments():
    text = """
    # a comment
    netflix.com

    full:exact-only.example.com
    regexp:(^|\\.)dynamic-.+\\.example\\.net$
    """
    parsed = parse_file(text)
    assert [e.value for e in parsed.suffixes] == ["netflix.com"]
    assert [e.value for e in parsed.fulls] == ["exact-only.example.com"]
    assert len(parsed.regexes) == 1
    pattern, tags = parsed.regexes[0]
    assert pattern.search("dynamic-foo.example.net")
    assert not pattern.search("example.net")
    assert tags == frozenset()


def test_parse_file_collects_includes_without_resolving_them():
    parsed = parse_file("include:facebook\ninclude:instagram\ntiktok.com")
    assert [i.name for i in parsed.includes] == ["facebook", "instagram"]
    assert [i.tag for i in parsed.includes] == [None, None]
    assert [e.value for e in parsed.suffixes] == ["tiktok.com"]


def test_parse_file_captures_tags_on_entries_and_includes():
    parsed = parse_file("admob.com @ads\ninclude:google @ads\nplain.example.com")
    assert parsed.suffixes[0].value == "admob.com"
    assert parsed.suffixes[0].tags == frozenset({"ads"})
    assert parsed.suffixes[1].value == "plain.example.com"
    assert parsed.suffixes[1].tags == frozenset()
    assert parsed.includes[0] == parsed.includes[0]  # sanity
    assert parsed.includes[0].name == "google"
    assert parsed.includes[0].tag == "ads"


def test_parse_file_skips_malformed_regexp_without_crashing():
    parsed = parse_file("regexp:(unclosed\ngood.example.com")
    assert parsed.regexes == []
    assert [e.value for e in parsed.suffixes] == ["good.example.com"]


def test_categorize_matches_suffix_including_subdomains():
    files = {"netflix": "netflix.com\nnflxvideo.net"}
    matcher = CategoryMatcher(files, {"Streaming/Video": ["netflix"]})
    assert matcher.categorize("netflix.com") == "Streaming/Video"
    assert matcher.categorize("www.netflix.com") == "Streaming/Video"
    assert matcher.categorize("ipv4_1-lagg0-c001.1.nflxvideo.net") == "Streaming/Video"
    assert matcher.categorize("notnetflix.com") is None


def test_categorize_resolves_includes_recursively():
    files = {
        "category-social-media-!cn": "include:facebook\ninclude:instagram\nthreads.net",
        "facebook": "facebook.com\nfbcdn.net",
        "instagram": "instagram.com",
    }
    matcher = CategoryMatcher(files, {"Social Media": ["category-social-media-!cn"]})
    assert matcher.categorize("facebook.com") == "Social Media"
    assert matcher.categorize("edge-oculus-shv-01-lhr11.fbcdn.net") == "Social Media"
    assert matcher.categorize("instagram.com") == "Social Media"
    assert matcher.categorize("threads.net") == "Social Media"


def test_categorize_tag_scoped_include_takes_only_matching_entries():
    # Real-world shape: category-ads does `include:google @ads` to pull
    # only google's own ad-serving domains, not google.com/gmail/etc.
    # Verifies the tag filter actually excludes untagged entries in the
    # target file, not just passes everything through.
    files = {
        "category-ads": "include:google @ads",
        "google": "admob.com @ads\nadsense.com @ads\ngoogle.com\nmail.google.com",
    }
    matcher = CategoryMatcher(files, {"Ads/Tracking": ["category-ads"]})
    assert matcher.categorize("admob.com") == "Ads/Tracking"
    assert matcher.categorize("adsense.com") == "Ads/Tracking"
    assert matcher.categorize("google.com") is None
    assert matcher.categorize("mail.google.com") is None


def test_categorize_tag_filter_propagates_through_untagged_nested_includes():
    # Real-world shape found against actual upstream data: category-ads
    # does `include:meta @ads`, and meta's own file is just a bare
    # (untagged) `include:facebook` plus a few of its own domains. The
    # @ads filter has to propagate down into facebook's entries too --
    # otherwise this resolves to "all of facebook.com", not "just
    # facebook's ad-tagged subset", which is what actually happened
    # before this was fixed.
    files = {
        "category-ads": "include:meta @ads",
        "meta": "include:facebook\nmeta.ai",
        "facebook": "facebook.com\npixel.facebook.com @ads",
    }
    matcher = CategoryMatcher(files, {"Ads/Tracking": ["category-ads"]})
    assert matcher.categorize("pixel.facebook.com") == "Ads/Tracking"
    assert matcher.categorize("facebook.com") is None
    assert matcher.categorize("meta.ai") is None


def test_categorize_bare_include_takes_tagged_and_untagged_entries():
    files = {
        "category-ads": "include:google",  # no @tag filter this time
        "google": "admob.com @ads\ngoogle.com",
    }
    matcher = CategoryMatcher(files, {"Everything": ["category-ads"]})
    assert matcher.categorize("admob.com") == "Everything"
    assert matcher.categorize("google.com") == "Everything"


def test_categorize_full_match_does_not_match_subdomains():
    files = {"pinned": "full:exact.example.com"}
    matcher = CategoryMatcher(files, {"Test": ["pinned"]})
    assert matcher.categorize("exact.example.com") == "Test"
    assert matcher.categorize("sub.exact.example.com") is None


def test_categorize_missing_source_file_is_silently_ignored():
    # A file referenced in CATEGORY_SOURCES but not (yet) downloaded --
    # e.g. a transient fetch failure -- shouldn't crash matching, just
    # contribute no rules for that category.
    matcher = CategoryMatcher({}, {"Gaming": ["steam", "xbox"]})
    assert matcher.categorize("steampowered.com") is None


def test_categorize_returns_none_for_unresolved_or_empty_hostname():
    matcher = CategoryMatcher({}, {"Streaming/Video": ["netflix"]})
    assert matcher.categorize(None) is None
    assert matcher.categorize("") is None


def test_categorize_uses_real_category_sources_and_amazon_bare_include_of_aws_lands_in_cloud_not_shopping():
    # Real-world bug found on the user's actual box: v2fly's own "amazon"
    # file does a bare `include:aws`, so without Cloud Infrastructure
    # coming first in CATEGORY_SOURCES, an EC2 hostname like
    # "ec2-3-248-160-245.eu-west-1.compute.amazonaws.com" (and
    # cloudfront.net, also inside the "aws" file) landed in Shopping
    # instead. Uses the real CATEGORY_SOURCES/ordering, not a synthetic
    # mapping, so a future reordering regresses this test directly.
    files = {
        "amazon": "amazon.com\ninclude:aws",
        "aws": "amazonaws.com\ncloudfront.net",
    }
    matcher = CategoryMatcher(files, CATEGORY_SOURCES)
    assert matcher.categorize("ec2-3-248-160-245.eu-west-1.compute.amazonaws.com") == "Cloud Infrastructure"
    assert matcher.categorize("cloudfront.net") == "Cloud Infrastructure"
    assert matcher.categorize("amazon.com") == "Shopping"


def test_categorize_covers_oculus_and_ai_companies_via_real_category_sources():
    # Also found on the user's real box: oculus.com and anthropic.com
    # had no category at all -- oculus.com because it's only reachable
    # upstream via a "meta" file our Social Media sources never included
    # (fixed by listing "oculus" directly), and anthropic.com/openai.com
    # because there was no AI category at all despite v2fly carrying
    # real files for both companies.
    files = {"oculus": "oculus.com", "anthropic": "anthropic.com", "openai": "openai.com"}
    matcher = CategoryMatcher(files, CATEGORY_SOURCES)
    assert matcher.categorize("oculus.com") == "Social Media"
    assert matcher.categorize("anthropic.com") == "AI"
    assert matcher.categorize("openai.com") == "AI"


def test_categorize_precedence_follows_category_sources_order():
    # Same hostname deliberately covered by two categories; the earlier
    # one in the ordered mapping wins, not treated as ambiguous.
    files = {"a": "shared.example.com", "b": "shared.example.com"}
    matcher = CategoryMatcher(files, {"First": ["a"], "Second": ["b"]})
    assert matcher.categorize("shared.example.com") == "First"

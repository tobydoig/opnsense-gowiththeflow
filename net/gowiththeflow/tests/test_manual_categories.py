import manual_categories


def test_categorize_matches_suffix_and_subdomains(monkeypatch):
    monkeypatch.setitem(manual_categories.OVERRIDES, "example-corp.net", "Cloud/Productivity")
    assert manual_categories.categorize("example-corp.net") == "Cloud/Productivity"
    assert manual_categories.categorize("api.example-corp.net") == "Cloud/Productivity"
    assert manual_categories.categorize("notexample-corp.net") is None


def test_categorize_returns_none_for_unresolved_or_empty_hostname():
    assert manual_categories.categorize(None) is None
    assert manual_categories.categorize("") is None
    assert manual_categories.categorize("totally-unmapped.example.com") is None


def test_real_overrides_spot_check():
    # A handful of real entries from the seeded OVERRIDES data (see its
    # own docstring/comments) -- not exhaustive, just enough to catch a
    # typo'd key or an accidentally-deleted bucket.
    assert manual_categories.categorize("a.root-servers.net") == "Network Infrastructure"
    assert manual_categories.categorize("ns1.wac-msedge.net") == "Network Infrastructure"
    assert manual_categories.categorize("pool.ntp0.cam.ac.uk") == "Network Infrastructure"
    assert manual_categories.categorize("euw1-app-server.iot.i.tplinkcloud.com") == "Smart Home/IoT"
    assert manual_categories.categorize("oauth.ring.com") == "Smart Home/IoT"
    assert manual_categories.categorize("sc-prod-public-objects.mcdn.robertsspaceindustries.com") == "Gaming"
    assert manual_categories.categorize("host25-rangeA-aanp.cdn.thlon.isp.sky.com") == "Cloud Infrastructure"
    assert manual_categories.categorize("host86-150-144-116.range86-150.btcentralplus.com") == "Peer-to-Peer"
    # Deliberately NOT categorized -- see OVERRIDES' own docstring on why
    # generic VPS/hosting-provider PTR hosts are left alone.
    assert manual_categories.categorize("172-235-142-22.ip.linodeusercontent.com") is None


def test_pass_two_overrides_spot_check():
    # Second-pass buckets (Shopping/News/Banking/etc.), seeded from a
    # later real "Uncategorized Hosts" export -- not exhaustive, same
    # spot-check purpose as the pass-one test above.
    assert manual_categories.categorize("api.edge.vinted.co.uk") == "Shopping"
    assert manual_categories.categorize("images1.vinted.net") == "Shopping"
    assert manual_categories.categorize("static01.nyt.com") == "News"
    assert manual_categories.categorize("as.coinbase.com") == "Banking"
    assert manual_categories.categorize("checkout.stripe.com") == "Banking"
    assert manual_categories.categorize("sb.scorecardresearch.com") == "Ads/Tracking"
    assert manual_categories.categorize("www.parentpay.com") == "Education"
    assert manual_categories.categorize("educationhub.blog.gov.uk") == "Government"
    assert manual_categories.categorize("wpc-download.gfe.nvidia.com") == "Gaming"
    assert (
        manual_categories.categorize("abfvqd7aaaaaaaambhbmuozgbhk2e.ta.vod-hls.main.amazon.pv-cdn.net")
        == "Streaming/Video"
    )
    # Deliberately scoped narrow, not the whole parent domain/company --
    # must NOT match anything outside the specific suffix added.
    assert manual_categories.categorize("news.ycombinator.com") == "News"
    assert manual_categories.categorize("ycombinator.com") is None
    assert manual_categories.categorize("salesiq.zoho.eu") == "Communication"
    assert manual_categories.categorize("mail.zoho.eu") is None
    # bbc.co.uk itself is still deliberately left alone (spans News/
    # Streaming/Music -- see OVERRIDES' own docstring).
    assert manual_categories.categorize("www.bbc.co.uk") is None

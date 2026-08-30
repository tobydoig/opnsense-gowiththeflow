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

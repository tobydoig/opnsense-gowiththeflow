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

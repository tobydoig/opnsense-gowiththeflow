import category_updater as cu


def test_resolve_top_level_files_flattens_all_categories():
    sources = {"A": ["one", "two"], "B": ["two", "three"]}
    assert cu.resolve_top_level_files(sources) == {"one", "two", "three"}


def test_fetch_all_follows_include_chains_iteratively():
    remote = {
        "category-social-media": "include:facebook\nthreads.net",
        "facebook": "facebook.com\ninclude:instagram",
        "instagram": "instagram.com",
    }
    sources = {"Social Media": ["category-social-media"]}
    fetched = cu.fetch_all(sources, fetch_fn=lambda name: remote[name])
    assert fetched == remote


def test_fetch_all_skips_files_that_fail_to_fetch():
    def flaky_fetch(name):
        if name == "broken":
            raise OSError("network down")
        return "some.host.example.com"

    fetched = cu.fetch_all({"X": ["ok", "broken"]}, fetch_fn=flaky_fetch)
    assert fetched == {"ok": "some.host.example.com"}


def test_fetch_all_treats_none_return_as_a_failed_fetch():
    fetched = cu.fetch_all({"X": ["missing"]}, fetch_fn=lambda name: None)
    assert fetched == {}


def test_cache_round_trips_through_disk(tmp_path):
    files = {"netflix": "netflix.com", "category-social-media-!cn": "include:facebook"}
    cu.save_cached_files(str(tmp_path), files)
    loaded = cu.load_cached_files(str(tmp_path))
    assert loaded == files


def test_load_cached_files_from_nonexistent_dir_returns_empty():
    assert cu.load_cached_files("/does/not/exist/at/all") == {}


def test_refresh_merges_fresh_fetch_over_stale_cache_without_losing_untouched_entries(tmp_path):
    # Pre-seed the cache as if from a previous run, including a file that
    # this run's fetch will fail to refresh -- it must survive untouched.
    cu.save_cached_files(
        str(tmp_path), {"steam": "old-steam-data.com", "xbox": "old-xbox-data.com"}
    )

    def fetch_fn(name):
        if name == "steam":
            return "new-steam-data.com"
        raise OSError("xbox fetch failed this time")

    result = cu.refresh(str(tmp_path), {"Gaming": ["steam", "xbox"]}, fetch_fn=fetch_fn)

    assert result["steam"] == "new-steam-data.com"  # fresh fetch wins
    assert result["xbox"] == "old-xbox-data.com"  # stale cache preserved, not dropped

    # And it's actually persisted to disk, not just returned in memory.
    on_disk = cu.load_cached_files(str(tmp_path))
    assert on_disk == result


def test_refresh_with_total_network_failure_still_returns_existing_cache(tmp_path):
    cu.save_cached_files(str(tmp_path), {"netflix": "netflix.com"})

    def always_fails(name):
        raise OSError("no network")

    result = cu.refresh(str(tmp_path), {"Streaming/Video": ["netflix"]}, fetch_fn=always_fails)
    assert result == {"netflix": "netflix.com"}


def test_safe_cache_filename_neutralizes_path_traversal_shaped_names():
    # Include-target names ultimately come from remote file content, not
    # just our own static list -- a malicious/compromised upstream entry
    # shouldn't be able to write outside the cache directory.
    assert "/" not in cu._safe_cache_filename("../../etc/passwd")
    assert "\\" not in cu._safe_cache_filename("..\\..\\windows\\system32")
    assert ".." not in cu._safe_cache_filename("../../etc/passwd")

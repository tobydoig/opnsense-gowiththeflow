from live_ticks import TickRow, compute_tick_deltas
from pf_state_poller import DiffResult, StateKey, StateSnapshot

NEW_SESSION_MAX_AGE_S = 10


def _snap(key, bytes_out, bytes_in, age_s=0, pkts_out=1, pkts_in=1):
    return StateSnapshot(
        key=key, bytes_out=bytes_out, bytes_in=bytes_in, pkts_out=pkts_out, pkts_in=pkts_in, age_s=age_s
    )


def test_opened_within_threshold_age_charges_full_cumulative_as_delta():
    key = StateKey("tcp", "192.168.1.10", 5000, "93.184.216.34", 443)
    diff = DiffResult(opened=[_snap(key, bytes_out=100, bytes_in=9000, age_s=3)])

    rows, prev = compute_tick_deltas(diff, {}, NEW_SESSION_MAX_AGE_S)

    assert rows == [TickRow(local_ip="192.168.1.10", peer_port=443, delta_bytes_in=9000, delta_bytes_out=100)]
    assert prev[key] == (9000, 100, False)


def test_opened_older_than_threshold_establishes_baseline_only():
    # A long-lived session newly entering our tracking (cold table, fresh
    # install, or a schema-migration wipe) -- charging its whole lifetime
    # total to one tick would be the "huge, meaningless spike" bug.
    key = StateKey("tcp", "192.168.1.10", 5000, "93.184.216.34", 443)
    diff = DiffResult(opened=[_snap(key, bytes_out=500_000, bytes_in=9_000_000, age_s=3600)])

    rows, prev = compute_tick_deltas(diff, {}, NEW_SESSION_MAX_AGE_S)

    assert rows == []
    assert prev[key] == (9_000_000, 500_000, False)


def test_updated_computes_diff_against_prev_bytes():
    key = StateKey("tcp", "192.168.1.10", 5000, "93.184.216.34", 443)
    diff = DiffResult(updated=[_snap(key, bytes_out=300, bytes_in=9500)])

    rows, prev = compute_tick_deltas(diff, {key: (9000, 100, False)}, NEW_SESSION_MAX_AGE_S)

    assert rows == [TickRow(local_ip="192.168.1.10", peer_port=443, delta_bytes_in=500, delta_bytes_out=200)]
    assert prev[key] == (9500, 300, False)


def test_updated_with_no_prior_baseline_defaults_to_zero_not_a_crash():
    # Pre-existing edge case (5-tuple reuse within one poll interval):
    # documented, not fixed. Must not raise, and clamps rather than
    # going negative.
    key = StateKey("tcp", "192.168.1.10", 5000, "93.184.216.34", 443)
    diff = DiffResult(updated=[_snap(key, bytes_out=50, bytes_in=100)])

    rows, prev = compute_tick_deltas(diff, {}, NEW_SESSION_MAX_AGE_S)

    assert rows == [TickRow(local_ip="192.168.1.10", peer_port=443, delta_bytes_in=100, delta_bytes_out=50)]
    assert prev[key] == (100, 50, False)


def test_updated_against_a_seeded_baseline_establishes_it_without_a_delta():
    # Real bug, found live: a restart mid-transfer seeds tick_prev_bytes
    # from whatever live_sessions last held -- possibly stale, or (as
    # happened for real when the bytes_in/bytes_out swap bug was fixed)
    # no longer even meaning the same thing. Diffing against it as if it
    # were a normal 5-second-old baseline turned a fixed mislabeling bug
    # into a multi-gigabyte phantom spike. A seeded baseline must only
    # ever be *established*, never diffed against.
    key = StateKey("tcp", "192.168.1.10", 5000, "93.184.216.34", 443)
    diff = DiffResult(updated=[_snap(key, bytes_out=50_000_000, bytes_in=2_000_000_000)])

    rows, prev = compute_tick_deltas(diff, {key: (100, 9000, True)}, NEW_SESSION_MAX_AGE_S)

    assert rows == []
    assert prev[key] == (2_000_000_000, 50_000_000, False)


def test_updated_against_a_seeded_baseline_diffs_normally_on_the_next_tick():
    key = StateKey("tcp", "192.168.1.10", 5000, "93.184.216.34", 443)
    diff1 = DiffResult(updated=[_snap(key, bytes_out=50_000_000, bytes_in=2_000_000_000)])
    _rows1, prev1 = compute_tick_deltas(diff1, {key: (100, 9000, True)}, NEW_SESSION_MAX_AGE_S)

    diff2 = DiffResult(updated=[_snap(key, bytes_out=50_000_300, bytes_in=2_000_005_000)])
    rows2, prev2 = compute_tick_deltas(diff2, prev1, NEW_SESSION_MAX_AGE_S)

    assert rows2 == [TickRow(local_ip="192.168.1.10", peer_port=443, delta_bytes_in=5000, delta_bytes_out=300)]
    assert prev2[key] == (2_000_005_000, 50_000_300, False)


def test_closed_emits_no_row_and_is_removed_from_prev_bytes():
    key = StateKey("tcp", "192.168.1.10", 5000, "93.184.216.34", 443)
    diff = DiffResult(closed=[_snap(key, bytes_out=300, bytes_in=9500)])

    rows, prev = compute_tick_deltas(diff, {key: (100, 9000, False)}, NEW_SESSION_MAX_AGE_S)

    assert rows == []
    assert key not in prev


def test_sessions_sharing_local_ip_and_peer_port_are_summed():
    key_a = StateKey("tcp", "192.168.1.10", 5000, "93.184.216.34", 443)
    key_b = StateKey("tcp", "192.168.1.10", 5001, "1.2.3.4", 443)
    diff = DiffResult(
        opened=[
            _snap(key_a, bytes_out=100, bytes_in=1000, age_s=1),
            _snap(key_b, bytes_out=50, bytes_in=500, age_s=1),
        ]
    )

    rows, _prev = compute_tick_deltas(diff, {}, NEW_SESSION_MAX_AGE_S)

    assert rows == [TickRow(local_ip="192.168.1.10", peer_port=443, delta_bytes_in=1500, delta_bytes_out=150)]


def test_zero_delta_pair_is_omitted_from_rows():
    key = StateKey("tcp", "192.168.1.10", 5000, "93.184.216.34", 443)
    diff = DiffResult(updated=[_snap(key, bytes_out=100, bytes_in=9000)])

    rows, prev = compute_tick_deltas(diff, {key: (9000, 100, False)}, NEW_SESSION_MAX_AGE_S)

    assert rows == []
    assert prev[key] == (9000, 100, False)


def test_prev_bytes_threads_correctly_across_two_poll_cycles():
    key = StateKey("tcp", "192.168.1.10", 5000, "93.184.216.34", 443)
    diff1 = DiffResult(opened=[_snap(key, bytes_out=100, bytes_in=1000, age_s=1)])
    rows1, prev1 = compute_tick_deltas(diff1, {}, NEW_SESSION_MAX_AGE_S)

    diff2 = DiffResult(updated=[_snap(key, bytes_out=250, bytes_in=2500)])
    rows2, prev2 = compute_tick_deltas(diff2, prev1, NEW_SESSION_MAX_AGE_S)

    assert rows1 == [TickRow(local_ip="192.168.1.10", peer_port=443, delta_bytes_in=1000, delta_bytes_out=100)]
    assert rows2 == [TickRow(local_ip="192.168.1.10", peer_port=443, delta_bytes_in=1500, delta_bytes_out=150)]
    assert prev2[key] == (2500, 250, False)

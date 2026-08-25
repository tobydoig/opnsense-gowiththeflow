"""Computes the Live Overview chart's per-tick throughput deltas once,
server-side, so every browser tab reads the same recorded history
instead of each independently diffing its own poll of `/live/overview`.

Pure data in, data out -- no DB/network I/O -- mirroring rollup.py's
style so this is independently unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from pf_state_poller import DiffResult, StateKey


@dataclass(frozen=True)
class TickRow:
    local_ip: str
    peer_port: int
    delta_bytes_in: int
    delta_bytes_out: int


def compute_tick_deltas(
    diff: DiffResult,
    prev_bytes: dict[StateKey, tuple[int, int]],
    new_session_max_age_s: int,
) -> tuple[list[TickRow], dict[StateKey, tuple[int, int]]]:
    """Returns (this tick's rows, the prev_bytes to pass into the *next*
    call) -- the caller threads prev_bytes through across poll cycles.

    diff.opened really means "not in the poller's previous-poll set",
    which is every currently-open pf state on a cold live_sessions table
    (fresh install, or after a schema-migration data wipe), not
    necessarily a session that just opened. age_s (pf's own reported
    connection age) distinguishes the two: only a snapshot young enough
    to have plausibly opened within roughly the last poll interval gets
    its full cumulative bytes charged to this one tick -- otherwise this
    tick establishes a baseline only, avoiding the "huge, meaningless
    spike" bug already fixed once for the equivalent client-side case
    (live.volt's old isFirstEverTick guard), just decided per-session
    instead of table-wide.

    diff.closed contributes no row: its bytes through the last real poll
    were already counted in a prior tick, and there's no new cumulative
    reading to diff against (pf no longer reports a closed state at
    all) -- the same accepted "final partial interval on close is
    dropped" limitation the client-side code already documented.

    Not handled (pre-existing, not introduced here): StateKey has no pf
    state id/creatorid, so a session that closes and a *different*
    logical connection opening on the identical 5-tuple within one poll
    interval is seen as `updated`, not `closed`+`opened` -- producing a
    spurious (clamped-to-0) delta for that one tick. Same limitation the
    client's own `Math.max(delta, 0)` clamp already had.
    """
    updated_prev = dict(prev_bytes)
    sums: dict[tuple[str, int], list[int]] = {}

    def _add(local_ip: str, peer_port: int, delta_in: int, delta_out: int) -> None:
        if delta_in == 0 and delta_out == 0:
            return
        pair_key = (local_ip, peer_port)
        if pair_key not in sums:
            sums[pair_key] = [0, 0]
        sums[pair_key][0] += delta_in
        sums[pair_key][1] += delta_out

    for snap in diff.opened:
        if snap.age_s <= new_session_max_age_s:
            delta_in, delta_out = snap.bytes_in, snap.bytes_out
        else:
            delta_in, delta_out = 0, 0
        _add(snap.key.local_ip, snap.key.peer_port, delta_in, delta_out)
        updated_prev[snap.key] = (snap.bytes_in, snap.bytes_out)

    for snap in diff.updated:
        old_in, old_out = updated_prev.get(snap.key, (0, 0))
        delta_in = max(snap.bytes_in - old_in, 0)
        delta_out = max(snap.bytes_out - old_out, 0)
        _add(snap.key.local_ip, snap.key.peer_port, delta_in, delta_out)
        updated_prev[snap.key] = (snap.bytes_in, snap.bytes_out)

    for snap in diff.closed:
        updated_prev.pop(snap.key, None)

    rows = [
        TickRow(local_ip=k[0], peer_port=k[1], delta_bytes_in=v[0], delta_bytes_out=v[1])
        for k, v in sums.items()
    ]
    return rows, updated_prev

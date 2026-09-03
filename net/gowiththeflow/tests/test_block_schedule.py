from datetime import datetime, timedelta

import pytest

from block_schedule import current_segment_end, is_blocked_now, parse_schedule

# Anchor dates with known weekdays, used throughout so test intent reads
# clearly without doing weekday arithmetic in every test:
MON = datetime(2026, 9, 7, 0, 0)  # a real Monday
TUE = datetime(2026, 9, 8, 0, 0)
FRI = datetime(2026, 9, 11, 0, 0)
SAT = datetime(2026, 9, 12, 0, 0)
SUN = datetime(2026, 9, 13, 0, 0)


def test_parse_schedule_accepts_json_string_or_dict():
    raw = '{"windows": [{"days": ["mon"], "start": "20:00", "end": "23:00"}]}'
    from_str = parse_schedule(raw)
    from_dict = parse_schedule({"windows": [{"days": ["mon"], "start": "20:00", "end": "23:00"}]})
    assert from_str == from_dict
    assert from_str.windows[0].days == frozenset({"mon"})


def test_parse_schedule_rejects_unknown_day():
    with pytest.raises(ValueError):
        parse_schedule({"windows": [{"days": ["someday"], "start": "20:00", "end": "23:00"}]})


def test_parse_schedule_rejects_empty_days():
    with pytest.raises(ValueError):
        parse_schedule({"windows": [{"days": [], "start": "20:00", "end": "23:00"}]})


def test_parse_schedule_empty_windows_list_is_valid():
    assert parse_schedule({"windows": []}).windows == ()


def test_same_day_window_blocks_only_inside_its_hours():
    schedule = parse_schedule({"windows": [{"days": ["mon"], "start": "20:00", "end": "23:00"}]})
    assert is_blocked_now(schedule, MON.replace(hour=19, minute=59)) is False
    assert is_blocked_now(schedule, MON.replace(hour=20, minute=0)) is True
    assert is_blocked_now(schedule, MON.replace(hour=22, minute=30)) is True
    assert is_blocked_now(schedule, MON.replace(hour=23, minute=0)) is False  # end is exclusive


def test_same_day_window_does_not_apply_on_a_different_day():
    schedule = parse_schedule({"windows": [{"days": ["mon"], "start": "20:00", "end": "23:00"}]})
    assert is_blocked_now(schedule, TUE.replace(hour=21, minute=0)) is False


def test_overnight_window_blocks_across_midnight():
    # The user's own real example: 8pm-8am weekdays.
    schedule = parse_schedule({"windows": [{"days": ["mon"], "start": "20:00", "end": "08:00"}]})
    assert is_blocked_now(schedule, MON.replace(hour=23, minute=0)) is True
    # Still blocked into Tuesday morning, from Monday's own window.
    assert is_blocked_now(schedule, TUE.replace(hour=3, minute=0)) is True
    assert is_blocked_now(schedule, TUE.replace(hour=7, minute=59)) is True
    assert is_blocked_now(schedule, TUE.replace(hour=8, minute=0)) is False


def test_weekday_and_weekend_windows_use_different_hours():
    # The user's exact second example: weekends run 9pm-7:30am instead.
    schedule = parse_schedule({
        "windows": [
            {"days": ["mon", "tue", "wed", "thu", "fri"], "start": "20:00", "end": "08:00"},
            {"days": ["sat", "sun"], "start": "21:00", "end": "07:30"},
        ]
    })
    assert is_blocked_now(schedule, FRI.replace(hour=20, minute=30)) is True
    assert is_blocked_now(schedule, SAT.replace(hour=20, minute=30)) is False  # weekend window starts later
    assert is_blocked_now(schedule, SAT.replace(hour=21, minute=30)) is True
    assert is_blocked_now(schedule, SUN.replace(hour=7, minute=0)) is True
    assert is_blocked_now(schedule, SUN.replace(hour=7, minute=30)) is False


def test_segment_end_inside_a_window_is_that_windows_end():
    schedule = parse_schedule({"windows": [{"days": ["mon"], "start": "20:00", "end": "23:00"}]})
    assert current_segment_end(schedule, MON.replace(hour=21, minute=0)) == MON.replace(hour=23, minute=0)


def test_segment_end_inside_an_overnight_window_is_the_next_days_end():
    schedule = parse_schedule({"windows": [{"days": ["mon"], "start": "20:00", "end": "08:00"}]})
    assert current_segment_end(schedule, TUE.replace(hour=3, minute=0)) == TUE.replace(hour=8, minute=0)


def test_segment_end_in_a_gap_is_the_next_windows_start():
    schedule = parse_schedule({"windows": [{"days": ["mon"], "start": "20:00", "end": "23:00"}]})
    # A Monday-afternoon gap should resolve to that same evening's window.
    assert current_segment_end(schedule, MON.replace(hour=12, minute=0)) == MON.replace(hour=20, minute=0)


def test_segment_end_in_a_gap_looks_a_full_week_ahead():
    schedule = parse_schedule({"windows": [{"days": ["mon"], "start": "20:00", "end": "23:00"}]})
    # Tuesday morning: last Monday's window is long over, next is next Monday.
    next_monday = MON.replace(hour=20, minute=0) + timedelta(days=7)
    assert current_segment_end(schedule, TUE.replace(hour=9, minute=0)) == next_monday


def test_overlapping_windows_merge_into_one_continuous_segment():
    # A Friday-evening window running straight into a Saturday-morning one
    # -- the override should last through the whole merged stretch, not
    # just until the first window's own end.
    schedule = parse_schedule({
        "windows": [
            {"days": ["fri"], "start": "22:00", "end": "08:00"},   # Fri 22:00 -> Sat 08:00
            {"days": ["sat"], "start": "07:00", "end": "12:00"},   # Sat 07:00 -> Sat 12:00 (overlaps the first)
        ]
    })
    at_0300 = SAT.replace(hour=3, minute=0)
    assert is_blocked_now(schedule, at_0300) is True
    assert current_segment_end(schedule, at_0300) == SAT.replace(hour=12, minute=0)


def test_schedule_with_no_windows_never_blocks():
    schedule = parse_schedule({"windows": []})
    assert is_blocked_now(schedule, MON) is False
    assert current_segment_end(schedule, MON) is None

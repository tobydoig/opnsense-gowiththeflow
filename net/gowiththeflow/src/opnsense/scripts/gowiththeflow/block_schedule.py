"""Pure schedule-window logic for `block_rules.schedule_json` -- no DB, no
pf, no Unbound, nothing but dates. Mirrors blocklist.py's own split: pure
logic here, effects (pf/Unbound) in block_rules_engine.py.

A schedule is one or more recurring weekly windows:
    {"windows": [{"days": ["mon", "tue", ...], "start": "20:00", "end": "08:00"}, ...]}

`days` names the day(s) a window *starts* on -- a window whose `end` is not
after its `start` (e.g. 20:00-08:00) spans into the following day, exactly
like the user's own "8pm-8am" example. Multiple windows may overlap or
chain (e.g. a Friday-night window running into a Saturday-morning one) --
`current_segment_end()` treats such a run as one continuous segment, not
several, since that's what "the schedule would naturally re-assert at X"
actually means for the manual-override behavior this exists to support.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# How far to look back/forward when generating concrete window instances
# around `now`. A week forward guarantees catching the next occurrence of
# any day-of-week (recurrence is weekly); a couple of days back is enough
# to catch an overnight-spanning window that started yesterday, plus any
# short chain of adjacent windows leading into today.
_LOOKBACK_DAYS = 2
_LOOKFORWARD_DAYS = 8


@dataclass(frozen=True)
class ScheduleWindow:
    days: frozenset[str]
    start: time
    end: time


@dataclass(frozen=True)
class Schedule:
    windows: tuple[ScheduleWindow, ...]


def _parse_time(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def parse_schedule(raw: str | dict) -> Schedule:
    """Accepts either a JSON string (as stored in block_rules.schedule_json)
    or an already-decoded dict. Raises ValueError/KeyError for anything
    malformed -- callers should treat a parse failure the same as "this
    rule has no schedule" (block, permanently, like before this feature
    existed) rather than silently guessing at a shape."""
    data = json.loads(raw) if isinstance(raw, str) else raw
    windows = []
    for w in data.get("windows", []):
        days = frozenset(d.lower() for d in w["days"])
        unknown = days - set(_DAY_NAMES)
        if unknown:
            raise ValueError(f"unknown day(s) in schedule window: {sorted(unknown)!r}")
        if not days:
            raise ValueError("schedule window has no days")
        windows.append(ScheduleWindow(days=days, start=_parse_time(w["start"]), end=_parse_time(w["end"])))
    return Schedule(windows=tuple(windows))


def _window_instances(window: ScheduleWindow, around: datetime) -> list[tuple[datetime, datetime]]:
    """Concrete (start, end) datetimes for every occurrence of this window
    whose start falls within the look-back/look-forward range of `around`."""
    instances = []
    for offset in range(-_LOOKBACK_DAYS, _LOOKFORWARD_DAYS + 1):
        anchor: date = (around + timedelta(days=offset)).date()
        if _DAY_NAMES[anchor.weekday()] not in window.days:
            continue
        start_dt = datetime.combine(anchor, window.start)
        end_date = anchor if window.end > window.start else anchor + timedelta(days=1)
        instances.append((start_dt, datetime.combine(end_date, window.end)))
    return instances


def _merged_intervals(schedule: Schedule, around: datetime) -> list[tuple[datetime, datetime]]:
    instances = []
    for window in schedule.windows:
        instances.extend(_window_instances(window, around))
    instances.sort()
    merged: list[tuple[datetime, datetime]] = []
    for start, end in instances:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def is_blocked_now(schedule: Schedule, now: datetime) -> bool:
    return any(start <= now < end for start, end in _merged_intervals(schedule, now))


def current_segment_end(schedule: Schedule, now: datetime) -> datetime | None:
    """The end of whichever segment `now` is currently in: if blocked, the
    end of the (possibly merged/chained) active window; if not, the start
    of the next window. None only when the schedule has no windows at all."""
    if not schedule.windows:
        return None
    for start, end in _merged_intervals(schedule, now):
        if start <= now < end:
            return end
    for start, end in _merged_intervals(schedule, now):
        if start > now:
            return start
    return None

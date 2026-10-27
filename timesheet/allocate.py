"""Turn per-client minutes into a schedule of billing slots."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .config import Workday

BREAK = "Break"


@dataclass(frozen=True)
class Row:
    start: datetime
    end: datetime
    key: str  # row key ("Site" or "Site @task"), or BREAK

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


def day_slots(active_minutes: int, workday: Workday) -> int:
    """How many slots the day gets: real active time rounded to the step, within the configured bounds."""
    step = workday.step_minutes
    slots = round(active_minutes / step)
    if workday.min_hours:
        slots = max(slots, round(workday.min_hours * 60 / step))
    if workday.max_hours:
        slots = min(slots, round(workday.max_hours * 60 / step))
    return slots


def allocate(per_key: dict[str, tuple[int, float]], slots: int) -> list[tuple[str, int]]:
    """Share `slots` between row keys by their minutes (largest remainder), one slot minimum each.

    Keys are ordered by when work on them started. A key needs at least one real minute.
    Returns [(key, slots)]; keys that do not fit (more keys than slots) are dropped from the end.
    """
    order = sorted((k for k, (_, mins) in per_key.items() if mins >= 1), key=lambda k: per_key[k][0])[:max(slots, 0)]
    if not order:
        return []
    units = {k: 1 for k in order}
    free = slots - len(order)
    total = sum(per_key[k][1] for k in order) or 1.0
    share = {k: free * per_key[k][1] / total for k in order}
    for k in order:
        units[k] += int(share[k])
    leftover = slots - sum(units.values())
    for k in sorted(order, key=lambda k: share[k] - int(share[k]), reverse=True)[:leftover]:
        units[k] += 1
    return [(k, units[k]) for k in order]


def schedule(day: date, allocation: list[tuple[str, int]], workday: Workday, break_start: time) -> list[Row]:
    """Lay the allocated slots out from the workday start, inserting the break at `break_start`."""
    step = timedelta(minutes=workday.step_minutes)
    t = datetime.combine(day, workday.start)
    brk = datetime.combine(day, break_start)
    brk_end = brk + timedelta(minutes=workday.break_minutes)
    rows: list[Row] = []
    for key, units in allocation:
        end = t + step * units
        if t < brk < end:  # a client running into the break continues after it
            rows.append(Row(t, brk, key))
            t = brk
        if t == brk and workday.break_minutes:
            rows.append(Row(brk, brk_end, BREAK))
            t, end = brk_end, end + (brk_end - brk)
        rows.append(Row(t, end, key))
        t = end
    return rows


def break_time(pause: tuple[int, int] | None, workday: Workday) -> time:
    """Snap the real pause to the step grid; without one, use the default. Never start after window end - break."""
    if pause is None:
        return workday.break_default
    start, _ = pause
    step = workday.step_minutes
    start = (start + step // 2) // step * step
    latest = workday.break_window[1].hour * 60 + workday.break_window[1].minute - workday.break_minutes
    start = min(start, latest)
    return time(start // 60, start % 60)

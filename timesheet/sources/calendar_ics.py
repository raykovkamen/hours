"""Calendar: meetings from an iCalendar feed (Google Calendar "secret address in iCal format", Outlook ICS link,
or a local .ics file), configured as `[sources] calendar_ics`.

Each timed event of the day counts as active time, even when the computer was idle - that is the point: calls
and meetings are the work no other source sees. The event title (then description and location) is matched
against sites.txt; a meeting naming no client becomes a `? meeting:` row to be named.

Skipped: all-day events, events marked free (TRANSP:TRANSPARENT), cancelled events and events you declined.
Recurring events are expanded for the day (DAILY / WEEKLY / MONTHLY / YEARLY with INTERVAL, BYDAY, BYMONTHDAY,
COUNT, UNTIL, EXDATE and moved single occurrences).
"""
from __future__ import annotations

import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

from . import Report
from ..config import Config
from ..sites import Sites
from ..timeline import Timeline

SOURCE = "calendar"
DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
MAX_EVENT = timedelta(hours=10)  # longer "meetings" are blocks or mistakes, not billable calls


@dataclass
class Event:
    uid: str = ""
    summary: str = ""
    description: str = ""
    location: str = ""
    start: datetime | None = None
    end: datetime | None = None
    all_day: bool = False
    rrule: dict[str, str] = field(default_factory=dict)
    exdates: set[date] = field(default_factory=set)
    recurrence_id: date | None = None
    transparent: bool = False
    cancelled: bool = False
    declined: bool = False


# -- parsing ------------------------------------------------------------------------------
def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _split(line: str) -> tuple[str, dict[str, str], str]:
    head, _, value = line.partition(":")
    name, *params = head.split(";")
    return name.upper(), {k.upper(): v.strip('"') for k, _, v in (p.partition("=") for p in params)}, value


def _text(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _zone(tzid: str | None, local: tzinfo) -> tzinfo:
    if not tzid:
        return local
    try:
        return ZoneInfo(tzid)
    except Exception:  # Outlook-style names ("FLE Standard Time") or no tz database: assume local time
        return local


def _when(value: str, params: dict[str, str], local: tzinfo) -> tuple[datetime | date, bool]:
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return date(int(value[:4]), int(value[4:6]), int(value[6:8])), True
    stamp = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return stamp.replace(tzinfo=ZoneInfo("UTC")), False
    return stamp.replace(tzinfo=_zone(params.get("TZID"), local)), False


def _duration(value: str) -> timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m:
        return timedelta()
    sign, w, d, h, mi, s = m.groups()
    td = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -td if sign == "-" else td


def parse(text: str, local: tzinfo, my_emails: set[str]) -> list[Event]:
    events, ev, duration = [], None, None
    for line in _unfold(text):
        if not line:
            continue
        name, params, value = _split(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            ev, duration = Event(), None
        elif name == "END" and value.upper() == "VEVENT" and ev is not None:
            if ev.start is not None and ev.end is None:
                ev.end = ev.start + (duration or timedelta())
            events.append(ev)
            ev = None
        elif ev is None:
            continue
        elif name == "UID":
            ev.uid = value
        elif name == "SUMMARY":
            ev.summary = _text(value)
        elif name == "DESCRIPTION":
            ev.description = _text(value)
        elif name == "LOCATION":
            ev.location = _text(value)
        elif name == "DTSTART":
            when, all_day = _when(value, params, local)
            ev.all_day = all_day
            ev.start = when if not all_day else None
        elif name == "DTEND":
            when, all_day = _when(value, params, local)
            ev.end = when if not all_day else None
        elif name == "DURATION":
            duration = _duration(value)
        elif name == "RRULE":
            ev.rrule = {k.upper(): v for k, _, v in (p.partition("=") for p in value.split(";")) if k}
        elif name == "EXDATE":
            for v in value.split(","):
                when, _ = _when(v, params, local)
                ev.exdates.add(when.astimezone(local).date() if isinstance(when, datetime) else when)
        elif name == "RECURRENCE-ID":
            when, _ = _when(value, params, local)
            ev.recurrence_id = when.astimezone(local).date() if isinstance(when, datetime) else when
        elif name == "TRANSP":
            ev.transparent = value.upper() == "TRANSPARENT"
        elif name == "STATUS":
            ev.cancelled = value.upper() == "CANCELLED"
        elif name == "ATTENDEE":
            if value.lower().removeprefix("mailto:") in my_emails and params.get("PARTSTAT", "").upper() == "DECLINED":
                ev.declined = True
    return events


# -- recurrence -------------------------------------------------------------------------------
def _nth_weekday(d: date, spec: str) -> bool:
    """'1MO' = first Monday of the month, '-1FR' = last Friday, 'MO' = every Monday."""
    m = re.fullmatch(r"([+-]?\d+)?([A-Z]{2})", spec)
    if not m or DAYS.index(m.group(2)) != d.weekday():
        return False
    if not m.group(1):
        return True
    n = int(m.group(1))
    if n > 0:
        return (d.day - 1) // 7 + 1 == n
    days_in_month = ((d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
    return (days_in_month - d.day) // 7 + 1 == -n


def _matches(rule: dict[str, str], first: date, d: date) -> bool:
    freq, interval = rule.get("FREQ", ""), int(rule.get("INTERVAL", "1") or 1)
    byday = [x for x in rule.get("BYDAY", "").split(",") if x]
    bymonthday = [int(x) for x in rule.get("BYMONTHDAY", "").split(",") if x]
    if freq == "DAILY":
        return (d - first).days % interval == 0 and (not byday or any(_nth_weekday(d, b) for b in byday))
    if freq == "WEEKLY":
        weeks = ((d - timedelta(days=d.weekday())) - (first - timedelta(days=first.weekday()))).days // 7
        days = byday or [DAYS[first.weekday()]]
        return weeks % interval == 0 and DAYS[d.weekday()] in [b[-2:] for b in days]
    if freq == "MONTHLY":
        months = (d.year - first.year) * 12 + d.month - first.month
        if months % interval:
            return False
        if bymonthday:
            return d.day in bymonthday
        if byday:
            return any(_nth_weekday(d, b) for b in byday)
        return d.day == first.day
    if freq == "YEARLY":
        return (d.year - first.year) % interval == 0 and (d.month, d.day) == (first.month, first.day)
    return False


def occurs_on(ev: Event, day: date, local: tzinfo) -> bool:
    first = ev.start.astimezone(local).date()
    if not ev.rrule:
        return first == day
    if day < first or day in ev.exdates:
        return False
    until = ev.rrule.get("UNTIL")
    if until:
        when, _ = _when(until, {}, local)
        last = when.astimezone(local).date() if isinstance(when, datetime) else when
        if day > last:
            return False
    count = int(ev.rrule.get("COUNT", "0") or 0)
    if not count:
        return _matches(ev.rrule, first, day)
    seen, d = 0, first
    while d <= day:  # COUNT needs the occurrences before the day
        if _matches(ev.rrule, first, d) and d not in ev.exdates:
            seen += 1
            if seen > count:
                return False
            if d == day:
                return True
        d += timedelta(days=1)
    return False


def meetings(events: list[Event], day: date, local: tzinfo) -> list[tuple[datetime, datetime, Event]]:
    """Timed occurrences on `day`, with moved/cancelled single occurrences applied."""
    moved = {(e.uid, e.recurrence_id) for e in events if e.recurrence_id}
    out = []
    for ev in events:
        if ev.start is None or ev.all_day:
            continue
        length = (ev.end - ev.start) if ev.end else timedelta()
        if ev.recurrence_id is not None:  # a single moved or edited occurrence
            if ev.start.astimezone(local).date() != day:
                continue
            start = ev.start.astimezone(local)
        else:
            if ev.rrule and (ev.uid, day) in moved:
                continue
            if not occurs_on(ev, day, local):
                continue
            wall = ev.start.astimezone(ev.start.tzinfo)
            start = datetime.combine(day, wall.time()).replace(tzinfo=ev.start.tzinfo).astimezone(local)
        if ev.cancelled or ev.transparent or ev.declined or not timedelta() < length <= MAX_EVENT:
            continue
        out.append((start, start + length, ev))
    return sorted(out, key=lambda o: o[0])


# -- source -------------------------------------------------------------------------------------
def _load(source: str) -> str:
    if re.match(r"(?i)^(https?|webcal)://", source):
        url = re.sub(r"(?i)^webcal://", "https://", source)
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.read().decode("utf-8", errors="replace")
    with open(os.path.expanduser(source), encoding="utf-8", errors="replace") as f:
        return f.read()


def collect(cfg: Config, sites: Sites, tl: Timeline) -> Report:
    source = cfg.sources.calendar_ics
    if not source:
        return Report(SOURCE, "Calendar: not configured (set [sources] calendar_ics to add meetings)", available=False)
    try:
        text = _load(source)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return Report(SOURCE, f"Calendar: could not be read ({exc.__class__.__name__})", available=False)
    found = meetings(parse(text, tl.tz, cfg.sources.my_emails), tl.day, tl.tz)
    details, needs, minutes = [], [], 0
    for start, end, ev in found:
        site = sites.match(ev.summary) or sites.match(f"{ev.description} {ev.location}")
        key = site or f"? meeting: {ev.summary[:60]}"
        tl.mark(start, end, key, SOURCE)
        tl.note(key, start, "meeting", ev.summary)
        minutes += int((end - start).total_seconds() // 60)
        line = f"  meeting {start:%H:%M}-{end:%H:%M} [{key}] {ev.summary[:80]}"
        details.append(line)
        if not site:
            needs.append(line)
    return Report(SOURCE, f"Calendar: {len(found)} meeting(s), {minutes} min", details, needs)

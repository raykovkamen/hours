"""The one-minute grid every source writes into."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, tzinfo

from .sites import UNKNOWN

MINUTES = 24 * 60


def local_midnight(day: date, tz: tzinfo | None = None) -> datetime:
    """Midnight starting `day`, aware. Without `tz`, the system's local time with that day's own UTC offset
    (summer or winter time), not today's - so days on the other side of a clock change line up."""
    naive = datetime.combine(day, time.min)
    return naive.replace(tzinfo=tz) if tz else naive.astimezone()


class Timeline:
    """Minute m of the grid is the wall-clock minute m of the day (00:00 = 0), in local time.

    `tz` is for tests and special cases; normally it is None and every timestamp is converted with the
    system's local rules for its own instant, so daylight-saving switches are handled per moment.
    """

    def __init__(self, day: date, tz: tzinfo | None = None):
        self.day = day
        self.tz = tz
        self.day_start = local_midnight(day, tz)
        self.day_end = local_midnight(day + timedelta(days=1), tz)
        # a fixed offset valid for this day (midday), for sources that need a tzinfo (calendar floating times)
        self.zone = tz or datetime.combine(day, time(12)).astimezone().tzinfo
        self.claims: dict[int, set[str]] = defaultdict(set)  # minute -> row keys claiming it
        self.active: set[int] = set()  # minutes some source saw the user working
        self.by_source: dict[str, int] = defaultdict(int)  # minutes each source contributed
        self.evidence: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)  # key -> (time, kind, text)
        self.initials: dict[str, list[tuple[datetime, str]]] = defaultdict(list)  # key -> (session start, first prompt)

    # -- writing -------------------------------------------------------------------
    def note(self, key: str, at: datetime, kind: str, text: str) -> None:
        """Remember what was done for a key (a commit subject, a prompt) - material for task titles."""
        text = " ".join(text.split())
        if key and text:
            self.evidence[key].append((at, kind, text))

    def note_initial(self, key: str, session_start: datetime, prompt: str) -> None:
        if key and prompt:
            self.initials[key].append((session_start, prompt))

    def initial_prompt(self, site: str) -> str:
        """The first prompt of the earliest session that worked on the site today (text kept as written)."""
        found = sorted((e for k, items in self.initials.items() if k == site or k.startswith(f"{site} @") for e in items),
                       key=lambda e: e[0])
        return found[0][1] if found else ""

    def evidence_for(self, site: str) -> list[tuple[datetime, str, str]]:
        """Everything noted for a site, across its task-specific keys, in time order."""
        out = [e for k, items in self.evidence.items() if k == site or k.startswith(f"{site} @") for e in items]
        return sorted(out, key=lambda e: e[0])
    def minute(self, at: datetime) -> int:
        """Wall-clock minute of the day for an instant; naive datetimes are local wall-clock time already."""
        if at.tzinfo is not None:
            at = at.astimezone(self.tz) if self.tz else at.astimezone()
        return (at.date() - self.day).days * MINUTES + at.hour * 60 + at.minute

    def mark_minutes(self, minutes, key: str | None, source: str) -> None:
        """Claim individual grid minutes (sources that already work in minutes)."""
        for m in minutes:
            if 0 <= m < MINUTES:
                if m not in self.active:
                    self.by_source[source] += 1
                self.active.add(m)
                if key:
                    self.claims[m].add(key)

    def mark(self, start: datetime, end: datetime, key: str | None, source: str) -> None:
        """Claim [start, end) for `key` (None = active, client unknown). Sub-minute spans claim one minute."""
        first, last = self.minute(start), self.minute(end)
        if last <= first:
            last = first + 1
        for m in range(max(0, first), min(MINUTES, last)):
            if m not in self.active:
                self.by_source[source] += 1
            self.active.add(m)
            if key:
                self.claims[m].add(key)

    def at(self, when: time) -> int:
        return when.hour * 60 + when.minute

    # -- resolving -----------------------------------------------------------------
    def resolve_unplaced(self, carry_minutes: int) -> None:
        """Active minutes without a client inherit the nearest placed minute within `carry_minutes`.

        Working in Slack or the browser right after a Claude session is usually the same client.
        What is still unplaced becomes UNKNOWN: real time, reported honestly as such.
        """
        placed = sorted(m for m in self.claims if self.claims[m])
        for m in sorted(self.active):
            if self.claims[m]:
                continue
            near = min(placed, key=lambda p: abs(p - m), default=None)
            if near is not None and abs(near - m) <= carry_minutes:
                self.claims[m] = set(self.claims[near])
            else:
                self.claims[m] = {UNKNOWN}

    def per_key(self) -> dict[str, tuple[int, float]]:
        """row key -> (first minute, minutes; a minute claimed by n keys gives each 1/n)."""
        out: dict[str, tuple[int, float]] = {}
        for m in sorted(self.active):
            keys = self.claims[m]
            for k in keys:
                first, total = out.get(k, (m, 0.0))
                out[k] = (first, total + 1 / len(keys))
        return out

    def active_minutes(self) -> int:
        return len(self.active)

    def longest_pause(self, window: tuple[time, time], min_minutes: int) -> tuple[int, int] | None:
        """(start minute, length) of the longest inactive stretch inside the window, if any is long enough."""
        lo, hi = self.at(window[0]), self.at(window[1])
        best, run = None, None
        for m in range(lo, hi + 1):
            if m in self.active or m == hi:
                if run is not None and m - run >= min_minutes and (best is None or m - run > best[1]):
                    best = (run, m - run)
                run = None
            elif run is None:
                run = m
        return best

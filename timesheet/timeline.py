"""The one-minute grid every source writes into."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, tzinfo

from .sites import UNKNOWN

MINUTES = 24 * 60


class Timeline:
    def __init__(self, day: date, tz: tzinfo):
        self.day = day
        self.tz = tz
        self.day_start = datetime.combine(day, time.min).replace(tzinfo=tz)
        self.claims: dict[int, set[str]] = defaultdict(set)  # minute -> row keys claiming it
        self.active: set[int] = set()  # minutes some source saw the user working
        self.by_source: dict[str, int] = defaultdict(int)  # minutes each source contributed
        self.evidence: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)  # key -> (time, kind, text)

    # -- writing -------------------------------------------------------------------
    def note(self, key: str, at: datetime, kind: str, text: str) -> None:
        """Remember what was done for a key (a commit subject, a prompt) - material for task titles."""
        text = " ".join(text.split())
        if key and text:
            self.evidence[key].append((at, kind, text))

    def evidence_for(self, site: str) -> list[tuple[datetime, str, str]]:
        """Everything noted for a site, across its task-specific keys, in time order."""
        out = [e for k, items in self.evidence.items() if k == site or k.startswith(f"{site} @") for e in items]
        return sorted(out, key=lambda e: e[0])
    def minute(self, at: datetime) -> int:
        if at.tzinfo is None:
            at = at.replace(tzinfo=self.tz)
        return int((at.astimezone(self.tz) - self.day_start).total_seconds() // 60)

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

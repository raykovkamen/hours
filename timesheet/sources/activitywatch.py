"""ActivityWatch: the window in front, minus away-from-keyboard time.

Window titles name the client: VS Code shows the workspace folder, browsers show the store or its admin.
Titles that match no sites.txt rule are counted and reported so the user can add a pattern.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import Report
from ..config import Config
from ..sites import Sites
from ..timeline import Timeline

SOURCE = "activitywatch"
MIN_EVENT_SECONDS = 5  # shorter focus changes are noise (alt-tabbing through)


def _get(base: str, path: str, **params) -> object:
    url = f"{base}/api/0/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)  # "+00:00" must be encoded or the API answers 400
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _minutes(tl: Timeline, start: datetime, seconds: float) -> range:
    """Minute indexes an event covers: [start, end) with a partial last minute counted, at least one."""
    end = start + timedelta(seconds=seconds)
    first, last = tl.minute(start), tl.minute(end)
    if (end - tl.day_start).total_seconds() % 60:
        last += 1
    return range(max(0, first), min(24 * 60, max(last, first + 1)))


def collect(cfg: Config, sites: Sites, tl: Timeline) -> Report:
    base = cfg.sources.activitywatch_url
    try:
        buckets = _get(base, "buckets/")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return Report(SOURCE, f"ActivityWatch: not reachable at {base} ({exc.__class__.__name__}); "
                      "the day rests on Claude sessions and git commits", available=False)
    window = next((b for b in buckets if b.startswith("aw-watcher-window")), None)
    afk = next((b for b in buckets if b.startswith("aw-watcher-afk")), None)
    if not window:
        return Report(SOURCE, "ActivityWatch: running, but no window watcher data yet", available=False)

    start = tl.day_start.astimezone(timezone.utc).isoformat()
    end = (tl.day_start + timedelta(days=1)).astimezone(timezone.utc).isoformat()
    away: set[int] = set()
    if afk:
        for e in _get(base, f"buckets/{afk}/events", start=start, end=end, limit=-1):
            if e.get("data", {}).get("status") == "afk":
                away.update(_minutes(tl, _ts(e["timestamp"]), e["duration"]))

    unmatched: Counter[str] = Counter()
    minutes = 0
    for e in _get(base, f"buckets/{window}/events", start=start, end=end, limit=-1):
        if e.get("duration", 0) < MIN_EVENT_SECONDS:
            continue
        t = _ts(e["timestamp"])
        data = e.get("data", {})
        title = data.get("title", "")
        site = sites.match(f"{data.get('app', '')} {title}")
        for m in _minutes(tl, t, e["duration"]):
            if m in away:
                continue
            tl.mark(tl.day_start + timedelta(minutes=m), tl.day_start + timedelta(minutes=m + 1), site, SOURCE)
            minutes += 1
            if not site:
                unmatched[title[:70]] += 1

    summary = f"ActivityWatch: ~{minutes // 60}h{minutes % 60:02d} at the keyboard"
    details = [f"  unmatched window {title!r}: {n} min" for title, n in unmatched.most_common(10)]
    needs = []
    if unmatched:
        top = ", ".join(f"{title!r} ({n} min)" for title, n in unmatched.most_common(6))
        needs.append(f"windows no sites.txt pattern matched: {top}")
    return Report(SOURCE, summary, details, needs)

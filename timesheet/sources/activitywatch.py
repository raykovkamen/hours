"""ActivityWatch: the window in front, minus away-from-keyboard time.

Three kinds of buckets are read when present:

* aw-watcher-window  - application and window title of the window in front (the base signal)
* aw-watcher-web-*   - the browser extension: the URL of the active tab. While a browser window is in front,
                       its URL is matched too, so `admin.shopify.com/store/acme-shop` places the minute even
                       when the tab title names no store.
* aw-watcher-vscode  - the VS Code extension: the project folder of the file being edited, which places
                       editing without Claude or commits.

Titles, URLs and projects that match no sites.txt rule are counted and reported so a pattern can be added.
Nothing leaves the machine.
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
BROWSERS = ("chrome", "msedge", "edge", "firefox", "brave", "opera", "vivaldi", "arc", "safari")


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
    if end.second or end.microsecond:
        last += 1
    return range(max(0, first), min(24 * 60, max(last, first + 1)))


def _is_browser(app: str) -> bool:
    app = app.lower()
    return any(b in app for b in BROWSERS)


def _short_url(url: str) -> str:
    """Host and first path segments - enough to write a rule, without query strings."""
    parts = urllib.parse.urlsplit(url)
    path = "/".join(parts.path.split("/")[:4])
    return f"{parts.netloc}{path}"


def collect(cfg: Config, sites: Sites, tl: Timeline) -> Report:
    base = cfg.sources.activitywatch_url
    try:
        buckets = _get(base, "buckets/")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return Report(SOURCE, f"ActivityWatch: not reachable at {base} ({exc.__class__.__name__}); "
                      "the day rests on Claude sessions and git commits", available=False)
    window = next((b for b in buckets if b.startswith("aw-watcher-window")), None)
    afk = next((b for b in buckets if b.startswith("aw-watcher-afk")), None)
    web = [b for b in buckets if b.startswith("aw-watcher-web")]
    vscode = [b for b in buckets if b.startswith("aw-watcher-vscode")]
    if not window:
        return Report(SOURCE, "ActivityWatch: running, but no window watcher data yet", available=False)

    start = tl.day_start.astimezone(timezone.utc).isoformat()
    end = tl.day_end.astimezone(timezone.utc).isoformat()

    def events(bucket: str) -> list[dict]:
        return [e for e in _get(base, f"buckets/{bucket}/events", start=start, end=end, limit=-1)
                if e.get("duration", 0) >= MIN_EVENT_SECONDS or bucket.startswith("aw-watcher-afk")]

    away: set[int] = set()
    if afk:
        for e in events(afk):
            if e.get("data", {}).get("status") == "afk":
                away.update(_minutes(tl, _ts(e["timestamp"]), e["duration"]))

    urls: dict[int, str] = {}  # minute -> active tab URL (browser extension)
    for b in web:
        for e in events(b):
            url = e.get("data", {}).get("url", "")
            if url and not e.get("data", {}).get("incognito"):
                for m in _minutes(tl, _ts(e["timestamp"]), e["duration"]):
                    urls[m] = url

    unmatched: Counter[str] = Counter()
    minutes = with_url = only_url = 0
    for e in events(window):
        t = _ts(e["timestamp"])
        data = e.get("data", {})
        app, title = data.get("app", ""), data.get("title", "")
        browser = _is_browser(app)
        title_site = sites.match(f"{app} {title}")
        for m in _minutes(tl, t, e["duration"]):
            if m in away:
                continue
            url = urls.get(m, "") if browser else ""
            url_site = sites.match(url) if url else None
            site = url_site or title_site  # the address is more precise than a tab title
            with_url += bool(url)
            only_url += bool(url_site and not title_site)
            tl.mark_minutes((m,), site, SOURCE)
            minutes += 1
            if not site:
                unmatched[_short_url(url) if url else title[:70]] += 1

    by_project = vscode_events = 0
    for b in vscode:
        for e in events(b):
            vscode_events += 1
            project = e.get("data", {}).get("project", "")
            site = sites.match(project) if project else None
            for m in _minutes(tl, _ts(e["timestamp"]), e["duration"]):
                if m in away:
                    continue
                tl.mark_minutes((m,), site, SOURCE)
                if site:
                    by_project += 1
                elif project:
                    unmatched[f"VS Code project {project[-60:]}"] += 1

    extras = []
    if not web:
        extras.append("browser URLs: extension not installed")
    else:
        extras.append(f"browser URLs: {with_url} min recorded, {only_url} min placed that the tab title could not")
    if not vscode:
        extras.append("VS Code: extension not installed")
    elif not vscode_events:
        extras.append("VS Code: extension installed but no data yet - reload VS Code (Developer: Reload Window)")
    else:
        extras.append(f"VS Code: {by_project} min placed")
    summary = f"ActivityWatch: ~{minutes // 60}h{minutes % 60:02d} at the keyboard ({'; '.join(extras)})"
    details = [f"  unmatched {what!r}: {n} min" for what, n in unmatched.most_common(15)]
    needs = []
    if unmatched:
        top = ", ".join(f"{what!r} ({n} min)" for what, n in unmatched.most_common(8))
        needs.append(f"windows / URLs / projects no sites.txt pattern matched: {top}")
    return Report(SOURCE, summary, details, needs, extensions={"web": bool(web), "vscode": bool(vscode)})

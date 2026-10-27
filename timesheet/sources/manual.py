"""Manual notes: time no other source can see - a phone call, a meeting without a calendar entry.

Added with `hours.py add "Josera call" 30m [--at 14:00] [DAY]` and kept in logs/manual-<day>.jsonl
(one JSON object per line; edit or delete lines to correct). The text is matched against sites.txt;
a note naming no client becomes a `? note:` row to be named.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from . import Report
from ..config import Config
from ..sites import Sites
from ..timeline import Timeline

SOURCE = "manual"


def notes_file(log_dir: Path, day: date) -> Path:
    return log_dir / f"manual-{day.isoformat()}.jsonl"


def parse_duration(text: str) -> int:
    """'30m', '1h', '1h30m', '1.5h', '90' -> minutes."""
    t = text.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+)m?)?", t)
    if not t or not m or not (m.group(1) or m.group(2)):
        raise ValueError(f"cannot read duration {text!r} (use 30m, 1h, 1h30m)")
    return int(round(float(m.group(1) or 0) * 60)) + int(m.group(2) or 0)


def add(log_dir: Path, day: date, text: str, minutes: int, at: datetime) -> dict:
    if minutes <= 0 or minutes > 12 * 60:
        raise ValueError("duration must be between 1 minute and 12 hours")
    rec = {"start": at.isoformat(timespec="minutes"), "minutes": minutes, "text": text.strip(),
           "added": datetime.now().isoformat(timespec="seconds")}
    log_dir.mkdir(exist_ok=True)
    with open(notes_file(log_dir, day), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def collect(cfg: Config, sites: Sites, tl: Timeline) -> Report:
    path = notes_file(cfg.log_dir, tl.day)
    if not path.exists():
        return Report(SOURCE, "Notes: none (hours.py add \"Client call\" 30m)", available=True)
    details, needs, total = [], [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            start = datetime.fromisoformat(rec["start"])
            minutes, text = int(rec["minutes"]), str(rec["text"])
        except (ValueError, KeyError, TypeError):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=tl.tz)
        site = sites.match(text)
        key = site or f"? note: {text[:60]}"
        tl.mark(start, start + timedelta(minutes=minutes), key, SOURCE)
        tl.note(key, start, "note", text)
        total += minutes
        entry = f"  note {start:%H:%M} {minutes} min [{key}] {text[:80]}"
        details.append(entry)
        if not site:
            needs.append(entry)
    return Report(SOURCE, f"Notes: {len(details)} note(s), {total} min", details, needs)

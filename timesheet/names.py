"""Names for `?` rows: "the 11:00 meeting was Shelly".

Added with `hours.py name 11:00 Shelly [DAY]` (or a span, `11:00-11:30`) and kept in logs/names-<day>.jsonl,
one JSON object per line; edit or delete lines to correct. Applied after every source has written the grid
and before unplaced minutes inherit a client, so the table and the push both see the client.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

from .sources import Report
from .timeline import Timeline

SOURCE = "names"


def names_file(log_dir: Path, day: date) -> Path:
    return log_dir / f"names-{day.isoformat()}.jsonl"


def parse_span(text: str) -> tuple[int, int | None]:
    """'11:00' -> (660, None); '11:00-11:30' -> (660, 690). Minutes of the day."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?:-(\d{1,2}):(\d{2}))?", text.strip())
    if not m:
        raise ValueError(f"cannot read time {text!r} (use 11:00 or 11:00-11:30)")
    h1, m1, h2, m2 = (int(x) if x is not None else None for x in m.groups())
    if h1 > 23 or m1 > 59 or (h2 is not None and (h2 > 24 or m2 > 59)):
        raise ValueError(f"cannot read time {text!r}")
    start = h1 * 60 + m1
    end = h2 * 60 + m2 if h2 is not None else None
    if end is not None and end <= start:
        raise ValueError(f"{text!r} ends before it starts")
    return start, end


def add(log_dir: Path, day: date, span: str, site: str) -> dict:
    parse_span(span)
    if not site.strip() or site.strip().startswith("?"):
        raise ValueError("name the client, e.g. Shelly")
    rec = {"at": span.strip(), "site": site.strip(), "added": datetime.now().isoformat(timespec="seconds")}
    log_dir.mkdir(exist_ok=True)
    with open(names_file(log_dir, day), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def apply(log_dir: Path, tl: Timeline) -> Report:
    path = names_file(log_dir, tl.day)
    if not path.exists():
        return Report(SOURCE, "Names: none (hours.py name 11:00 Client)", available=True)
    details = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            start, end = parse_span(rec["at"])
            site = str(rec["site"]).strip()
        except (ValueError, KeyError, TypeError):
            continue
        done = tl.rename(start, site, end)
        if done:
            for old, first, last in done:
                details.append(f"  named {first // 60:02d}:{first % 60:02d}-{last // 60:02d}:{last % 60:02d} [{old}] -> {site}")
        else:
            details.append(f"  name {rec['at']} -> {site}: no ? row at that time (nothing changed)")
    return Report(SOURCE, f"Names: {len(details)} named row(s)", details, details)

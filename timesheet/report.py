"""Output: the Markdown listing and table, or JSON for the ClickUp push."""
from __future__ import annotations

import json
from datetime import date

from .allocate import BREAK, Row
from .config import Config
from .sites import Sites, is_unnamed
from .sources import Report
from .timeline import Timeline


LABELS = {"activitywatch": "ActivityWatch", "claude": "Claude Code", "git": "Git", "calendar": "Calendar", "manual": "Notes", "names": "Named rows"}


def hours(minutes: float) -> str:
    return f"{minutes / 60:.2f}".rstrip("0").rstrip(".")  # 0.25, 0.5, 1, 1.25 ...


def listing(day: date, reports: list[Report], show_all: bool) -> str:
    out = [f"Timesheet sources for {day.isoformat()} (local time)", ""]
    out += [f"- {r.summary}" for r in reports]
    out.append("")
    for r in reports:
        lines = r.details if show_all else r.needs_naming
        if lines:
            out.append(f"## {LABELS.get(r.name, r.name)}")
            out += lines
            out.append("")
    return "\n".join(out)


def table(rows: list[Row], sites: Sites, tl: Timeline, cfg: Config, slots: int) -> str:
    w = cfg.workday
    # In create mode the push makes the tasks itself, so a task column would always be empty: leave it out.
    tasks = cfg.clickup.mode != "create"
    out = ["| Time | Hours | Site | ClickUp task |", "|---|---|---|---|"] if tasks else ["| Time | Hours | Site |", "|---|---|---|"]
    for r in rows:
        cells = [f"{r.start:%H:%M}–{r.end:%H:%M}", hours(r.minutes)]
        if r.key == BREAK:
            cells += ["Break"] + ([""] if tasks else [])
        else:
            site, task = sites.split_key(r.key)
            cells += [site] + ([task or "—"] if tasks else [])
        out.append("| " + " | ".join(cells) + " |")
    billed = sum(r.minutes for r in rows if r.key != BREAK)
    took = sum(r.minutes for r in rows if r.key == BREAK)
    extra = f" (+{hours(took)}h break)" if took else ""
    out.append(f"| **Total** | **{hours(billed)}h**{extra} |" + (" | |" if tasks else " |"))

    per_key = tl.per_key()
    real = ", ".join(f"{sites.split_key(k)[0]} {hours(v[1])}h" for k, v in sorted(per_key.items(), key=lambda kv: kv[1][0]))
    bounds = ""
    if w.min_hours or w.max_hours:
        bounds = f"; reported day kept within {w.min_hours:g}-{w.max_hours:g}h by config" if w.max_hours else f"; reported day at least {w.min_hours:g}h by config"
    out.append("")
    out.append(f"(tracked active time {hours(tl.active_minutes())}h: {real}; {w.step_minutes}-min steps{bounds})")
    notes = []
    in_table = {r.key for r in rows}
    passing = [f"{sites.split_key(k)[0]} {int(round(v[1]))} min" for k, v in per_key.items() if k not in in_table and v[1] >= 1]
    if passing:
        notes.append(f"under {w.min_client_minutes} min, not billed as a row: " + ", ".join(passing))
    if any(is_unnamed(r.key) for r in rows):
        notes.append("rows starting with ? need a client name - see the listing above")
    if sites.using_example:
        notes.append("sites.txt not found: using sites.example.txt - copy it and list your clients")
    if notes:
        out.append("Note: " + "; ".join(notes))
    return "\n".join(out)


def as_json(day: date, rows: list[Row], sites: Sites, tl: Timeline, reports: list[Report]) -> str:
    entries = []
    for r in rows:
        if r.key == BREAK:
            continue
        site, task = sites.split_key(r.key)
        entries.append({"start": r.start.strftime("%Y-%m-%d %H:%M"), "end": r.end.strftime("%Y-%m-%d %H:%M"),
                        "minutes": r.minutes, "site": site, "task": task or None, "unnamed": is_unnamed(r.key)})
    return json.dumps({
        "day": day.isoformat(),
        "tracked_minutes": tl.active_minutes(),
        "sources": {r.name: {"available": r.available, "summary": r.summary} for r in reports},
        "rows": entries,
        # what was done per client - material for descriptive task titles (prompts are for the user's eyes only)
        "evidence": {site: [{"time": f"{t:%H:%M}", "kind": kind, "text": text} for t, kind, text in tl.evidence_for(site)]
                     for site in dict.fromkeys(e["site"] for e in entries)},
    }, ensure_ascii=False, indent=2)

"""Push a day's table into ClickUp as time entries, idempotently.

The plan is computed from the table and what the user already logged that day: for every task, only the
difference between the table and the logged minutes is posted. Pushing twice changes nothing. Rows without
a task block the push - the user has to assign one - so no time is ever logged somewhere vague.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .allocate import BREAK, Row
from .clickup import ClickUp, ClickUpError, Entry, Task
from .sites import Sites, is_unnamed

DESCRIPTION = "hours"  # entries this tool wrote carry it, so they can be told apart in ClickUp


@dataclass
class Action:
    task_id: str
    site: str
    start: datetime
    minutes: int  # minutes to post (after subtracting what is logged already)
    table_minutes: int
    logged_minutes: int
    task: Task | None = None
    entry_id: str = ""

    @property
    def skipped(self) -> bool:
        return self.minutes <= 0


@dataclass
class Plan:
    day: date
    actions: list[Action] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)  # rows that cannot be pushed, with the reason

    @property
    def ok(self) -> bool:
        return not self.blocked

    @property
    def to_post(self) -> list[Action]:
        return [a for a in self.actions if not a.skipped]


def plan(day: date, rows: list[Row], sites: Sites, logged: list[Entry], assign: dict[str, str]) -> Plan:
    """Decide what to post. `assign` maps a site name to a task id for this push only."""
    p = Plan(day)
    logged_by_task: dict[str, float] = {}
    for e in logged:
        logged_by_task[e.task_id] = logged_by_task.get(e.task_id, 0) + e.minutes
    table_by_task: dict[str, int] = {}
    first_start: dict[str, datetime] = {}
    site_of: dict[str, str] = {}
    for r in rows:
        if r.key == BREAK:
            continue
        site, task = sites.split_key(r.key)
        task = assign.get(site, task)
        if is_unnamed(r.key) or site == "Unknown":
            p.blocked.append(f"{r.start:%H:%M}-{r.end:%H:%M} {site}: no client - name it first")
            continue
        if not task:
            p.blocked.append(f"{r.start:%H:%M}-{r.end:%H:%M} {site}: no ClickUp task - add one to sites.txt or pass --assign \"{site}=<task id>\"")
            continue
        table_by_task[task] = table_by_task.get(task, 0) + r.minutes
        first_start.setdefault(task, r.start)
        site_of.setdefault(task, site)
    for task, minutes in table_by_task.items():
        already = int(round(logged_by_task.get(task, 0)))
        p.actions.append(Action(task, site_of[task], first_start[task], minutes - already, minutes, already))
    return p


def execute(p: Plan, api: ClickUp, billable: bool, dry_run: bool, log_dir: Path) -> None:
    """Validate every task, then post. Writes a JSON-lines log of what was done (also for dry runs)."""
    for a in p.actions:
        try:
            a.task = api.task(a.task_id)
        except ClickUpError as e:
            p.blocked.append(f"{a.site}: task {a.task_id} - {e}")
    if p.blocked:
        return
    log_dir.mkdir(exist_ok=True)
    log = log_dir / f"push-{p.day.isoformat()}.jsonl"
    with open(log, "a", encoding="utf-8") as f:
        for a in p.to_post:
            if not dry_run:
                a.entry_id = api.add_entry(a.task_id, a.start, a.minutes, f"{DESCRIPTION}: {a.site}", billable)
            f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "dry_run": dry_run,
                                "task": a.task_id, "task_name": a.task.name if a.task else "", "site": a.site,
                                "start": a.start.isoformat(timespec="minutes"), "minutes": a.minutes,
                                "entry_id": a.entry_id}, ensure_ascii=False) + "\n")


def render(p: Plan, dry_run: bool) -> str:
    out = []
    if p.blocked:
        out.append("Push blocked - resolve these rows first:")
        out += [f"  - {b}" for b in p.blocked]
        return "\n".join(out)
    verb = "Would post" if dry_run else "Posted"
    posted = p.to_post
    if posted:
        out.append(f"{verb} {len(posted)} entr{'y' if len(posted) == 1 else 'ies'}:")
        for a in posted:
            where = f" ({a.task.location})" if a.task and a.task.location else ""
            name = a.task.name if a.task else a.task_id
            already = f", {a.logged_minutes} min already logged" if a.logged_minutes else ""
            out.append(f"  - {a.start:%H:%M} {a.minutes} min -> {name}{where} [{a.task_id}]{already}"
                       + (f" entry {a.entry_id}" if a.entry_id else ""))
    skipped = [a for a in p.actions if a.skipped]
    if skipped:
        out.append("Already logged (nothing to add):")
        for a in skipped:
            name = a.task.name if a.task else a.task_id
            out.append(f"  - {name} [{a.task_id}]: table {a.table_minutes} min, logged {a.logged_minutes} min")
    if not posted and not skipped:
        out.append("Nothing to post.")
    return "\n".join(out)


def token_from(cfg_token: str) -> str:
    return cfg_token or os.environ.get("CLICKUP_TOKEN", "")

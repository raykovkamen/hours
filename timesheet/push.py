"""Push a day's table into ClickUp as time entries, idempotently.

Two modes (`[clickup] mode` in config.toml):

* existing - time goes on the task named by the branch, a commit, a prompt link or the client's default in
  sites.txt. Rows without a task block the push.
* create   - one task per client per day ("Acme – 2026-10-27") is created in the client's list with status
  "in progress" and assigned to you; the day's time goes on it. A second push the same day finds the task
  again instead of creating another. Rows whose client has no list block the push.

Either way only the difference between the table and what is already logged on the task is posted, so
pushing twice changes nothing, and nothing is written until every row can be placed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

from .allocate import BREAK, Row
from .clickup import ClickUp, ClickUpError, Entry, Task
from .sites import UNKNOWN, Sites, is_unnamed

DESCRIPTION = "hours"  # entries this tool wrote carry it, so they can be told apart in ClickUp


@dataclass
class Action:
    task_id: str  # empty in create mode until the daily task is found or created
    site: str
    start: datetime
    minutes: int  # minutes to post (after subtracting what is logged already)
    table_minutes: int
    logged_minutes: int
    task: Task | None = None
    entry_id: str = ""
    list_id: str = ""  # create mode
    task_name: str = ""  # create mode
    description: str = ""  # create mode: task description (work done + lookup marker)
    created: bool = False  # create mode: the task was created by this run (or would be, in a dry run)

    @property
    def skipped(self) -> bool:
        return self.minutes <= 0


@dataclass
class Plan:
    day: date
    mode: str = "existing"
    actions: list[Action] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)  # rows that cannot be pushed, with the reason
    logged_by_task: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.blocked

    @property
    def to_post(self) -> list[Action]:
        return [a for a in self.actions if not a.skipped]


def marker(site: str, day: date) -> str:
    """Hidden key in the daily task's description, so a repeated push finds it even after a rename."""
    return f"hours-key: {site} | {day.isoformat()}"


def auto_title(evidence: list[tuple[datetime, str, str]], limit: int = 70) -> str:
    """A title from the day's commits: the first real one, plus how many more. Empty when there were none."""
    commits = [text for _, kind, text in evidence if kind == "commit" and not text.lower().startswith("merge ")]
    if not commits:
        return ""
    title = commits[0] if len(commits[0]) <= limit else commits[0][:limit - 1].rstrip() + "…"
    return f"{title} (+{len(commits) - 1} more)" if len(commits) > 1 else title


def describe(site: str, day: date, evidence: list[tuple[datetime, str, str]], summary: str = "") -> str:
    """Task description: optional summary, the day's commits (never prompts - they can hold client material)."""
    commits = [(t, text) for t, kind, text in evidence if kind == "commit" and not text.lower().startswith("merge ")]
    out = [summary.strip(), ""] if summary.strip() else []
    if commits:
        out.append("**Commits**")
        out += [f"- {t:%H:%M} {text}" for t, text in commits[:20]]
        out.append("")
    out.append(f"_Logged by hours on {day.isoformat()}._")
    out.append(f"`{marker(site, day)}`")
    return "\n".join(out)


def plan(day: date, rows: list[Row], sites: Sites, logged: list[Entry], assign: dict[str, str],
         mode: str = "existing", default_list: str = "", task_name: str = "{site} – {title}",
         titles: dict[str, str] | None = None, evidence=None, summaries: dict[str, str] | None = None) -> Plan:
    """Decide what to post. `assign` maps a site to a task id (existing mode) or `list:ID` (create mode).

    Create mode only: `titles` (site -> title) override the automatic title from the day's commits,
    `evidence` (site -> [(time, kind, text)]) feeds that automatic title and the task description,
    `summaries` (site -> text) go at the top of the description.
    """
    titles, summaries = titles or {}, summaries or {}
    evidence = evidence or (lambda site: [])
    p = Plan(day, mode)
    for e in logged:
        p.logged_by_task[e.task_id] = p.logged_by_task.get(e.task_id, 0) + e.minutes
    totals: dict[str, int] = {}  # task id (existing) or site (create) -> minutes
    first: dict[str, tuple[datetime, str]] = {}
    for r in rows:
        if r.key == BREAK:
            continue
        site, task = sites.split_key(r.key)
        span = f"{r.start:%H:%M}-{r.end:%H:%M} {site}"
        if is_unnamed(r.key) or site == UNKNOWN:
            p.blocked.append(f"{span}: no client - name it first")
            continue
        if mode == "create":
            override = assign.get(site, "")
            target = override[5:] if override.lower().startswith("list:") else sites.lists.get(site, default_list)
            if not target:
                p.blocked.append(f"{span}: no ClickUp list to create the task in - add list:<id> to the client's line "
                                 f"in sites.txt, set [clickup] list_id, or pass --assign \"{site}=list:<id>\"")
                continue
            key = site
            first.setdefault(key, (r.start, target))
        else:
            task = assign.get(site, task)
            if not task:
                p.blocked.append(f"{span}: no ClickUp task - add one to sites.txt or pass --assign \"{site}=<task id>\"")
                continue
            key = task
            first.setdefault(key, (r.start, site))
        totals[key] = totals.get(key, 0) + r.minutes
    for key, minutes in totals.items():
        start, extra = first[key]
        if mode == "create":
            ev = evidence(key)
            title = titles.get(key) or auto_title(ev) or day.isoformat()
            name = task_name.format(site=key, date=day.isoformat(), title=title)
            p.actions.append(Action("", key, start, minutes, minutes, 0, list_id=extra, task_name=name,
                                    description=describe(key, day, ev, summaries.get(key, ""))))
        else:
            already = int(round(p.logged_by_task.get(key, 0)))
            p.actions.append(Action(key, extra, start, minutes - already, minutes, already))
    return p


def _resolve_daily_task(p: Plan, a: Action, api: ClickUp, status: str, me: int | None, dry_run: bool) -> None:
    day_start = datetime.combine(p.day, time.min).astimezone()
    found = api.find_task(a.list_id, a.task_name, day_start, marker(a.site, p.day))
    if found:
        a.task, a.task_id = found, found.id
        a.logged_minutes = int(round(p.logged_by_task.get(found.id, 0)))
        a.minutes = a.table_minutes - a.logged_minutes
    elif dry_run:
        a.created = True
    else:
        a.task = api.create_task(a.list_id, a.task_name, status, me, a.description)
        a.task_id, a.created = a.task.id, True


def execute(p: Plan, api: ClickUp, billable: bool, dry_run: bool, log_dir: Path, status: str = "in progress") -> None:
    """Validate (or find/create) every task, then post. Appends a JSON-lines log of the run, dry runs included."""
    if p.blocked:
        return
    if p.mode == "create":
        me = api.user().get("id")
        for a in p.actions:
            try:
                _resolve_daily_task(p, a, api, status, me, dry_run)
            except ClickUpError as e:
                p.blocked.append(f"{a.site}: list {a.list_id} - {e}")
    else:
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
            f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "dry_run": dry_run, "mode": p.mode,
                                "task": a.task_id, "task_name": a.task.name if a.task else a.task_name,
                                "created": a.created, "site": a.site, "start": a.start.isoformat(timespec="minutes"),
                                "minutes": a.minutes, "entry_id": a.entry_id}, ensure_ascii=False) + "\n")


def _name(a: Action) -> str:
    if a.task:
        where = f" ({a.task.location})" if a.task.location else ""
        return f"{a.task.name}{where} [{a.task_id}]"
    return f"{a.task_name} (new task in list {a.list_id})"


def render(p: Plan, dry_run: bool) -> str:
    out = []
    if p.blocked:
        out.append("Push blocked - resolve these rows first:")
        out += [f"  - {b}" for b in p.blocked]
        return "\n".join(out)
    verb = "Would post" if dry_run else "Posted"
    posted = p.to_post
    created = [a for a in p.actions if a.created]
    if created:
        out.append(f"{'Would create' if dry_run else 'Created'} {len(created)} task(s), status \"in progress\", assigned to you:")
        out += [f"  - {_name(a)}" for a in created]
    if posted:
        out.append(f"{verb} {len(posted)} entr{'y' if len(posted) == 1 else 'ies'}:")
        for a in posted:
            already = f", {a.logged_minutes} min already logged" if a.logged_minutes else ""
            out.append(f"  - {a.start:%H:%M} {a.minutes} min -> {_name(a)}{already}"
                       + (f" entry {a.entry_id}" if a.entry_id else ""))
    skipped = [a for a in p.actions if a.skipped]
    if skipped:
        out.append("Already logged (nothing to add):")
        out += [f"  - {_name(a)}: table {a.table_minutes} min, logged {a.logged_minutes} min" for a in skipped]
    if not posted and not skipped:
        out.append("Nothing to post.")
    return "\n".join(out)


def token_from(cfg_token: str) -> str:
    return cfg_token or os.environ.get("CLICKUP_TOKEN", "")

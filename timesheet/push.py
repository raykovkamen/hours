"""Push a day's table into ClickUp as time entries, so that ClickUp matches the table.

Two modes (`[clickup] mode` in config.toml):

* existing - time goes on the task named by the branch, a commit, a prompt link or the client's default in
  sites.txt. Rows without a task block the push.
* create   - one task per client per day, named after the work, is created in the client's list with status
  "in progress" and assigned to you; the day's time goes on it. A later push finds that task again - from the
  local push log, or, when the log is missing (other machine, deleted folder), from the day's entries this
  tool wrote. Rows whose client has no list block the push.
  The daily tasks are subtasks of one parent task per client (`[clickup] parent_name`, e.g. "Acme – hours"),
  so a board shows one line per client, not one per day. The parent is created once, remembered as
  `parent:<id>` on the client's line in sites.txt, and found again by its name and tag in the list when that
  line is missing. `parent_name = ""` keeps the daily tasks flat.

Either way the push reconciles each task with the table:

* more in the table than logged -> the difference is posted as a new entry;
* less in the table than logged -> entries this tool wrote (described "hours: <client>") are shortened or
  removed, newest first. Entries logged by hand are never changed; if they alone exceed the table, that is
  reported, not corrected;
* equal -> nothing happens. Pushing twice changes nothing.

Nothing is written until every row can be placed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .allocate import BREAK, Row
from .clickup import ClickUp, ClickUpError, Entry, Task
from .sites import UNKNOWN, Sites, is_unnamed

MAX_PROMPT = 5000  # characters kept for the "Initial prompt" field
MAX_DESCRIPTION = 4000  # characters kept for a daily task's description
DESCRIPTION = "hours"  # entries this tool wrote carry "hours: <client>", so they can be told apart in ClickUp


def ours(e: Entry) -> bool:
    return e.description.startswith(f"{DESCRIPTION}: ")


@dataclass
class Action:
    task_id: str  # empty in create mode until the daily task is found or created
    site: str
    start: datetime
    minutes: int  # minutes to post (after subtracting what is logged already); negative = too much logged
    table_minutes: int
    logged_minutes: int
    task: Task | None = None
    entry_id: str = ""
    list_id: str = ""  # create mode
    task_name: str = ""  # create mode
    created: bool = False  # create mode: the task was created by this run (or would be, in a dry run)
    parent: Task | None = None  # create mode: the client's parent task the daily task nests under
    parent_name: str = ""  # create mode: name of the parent task ("" = flat daily tasks)
    parent_created: bool = False  # create mode: the parent task was created by this run (or would be)
    prompt: str = ""  # create mode: value for the "Initial prompt" field of a new task
    prompt_set: bool = False  # the field existed on the list and was filled
    description: str = ""  # create mode: Markdown summary of the day's work for the task (--desc)
    described: bool = False  # create mode: the description was set on a task that already existed
    shrink: list[tuple[Entry, int]] = field(default_factory=list)  # (entry, new minutes; 0 = delete)
    by_hand_excess: int = 0  # minutes logged by hand beyond the table - reported, never changed

    @property
    def skipped(self) -> bool:
        return self.minutes <= 0

    @property
    def reduced(self) -> int:
        return sum(int(round(e.minutes)) - new for e, new in self.shrink)


@dataclass
class Plan:
    day: date
    mode: str = "existing"
    actions: list[Action] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)  # rows that cannot be pushed, with the reason
    logged: list[Entry] = field(default_factory=list)
    tag: str = ""  # create mode: tag on created tasks (for the report)

    @property
    def ok(self) -> bool:
        return not self.blocked

    @property
    def to_post(self) -> list[Action]:
        return [a for a in self.actions if not a.skipped]

    @property
    def to_shrink(self) -> list[Action]:
        return [a for a in self.actions if a.shrink]

    def logged_on(self, task_id: str) -> list[Entry]:
        return [e for e in self.logged if e.task_id == task_id]


def reconcile(p: Plan, a: Action) -> None:
    """Compare the table with what is logged on the action's task and decide what to post or shrink."""
    entries = p.logged_on(a.task_id) if a.task_id else []
    a.logged_minutes = int(round(sum(e.minutes for e in entries)))
    a.minutes = a.table_minutes - a.logged_minutes
    a.shrink, a.by_hand_excess = [], 0
    if a.minutes >= 0:
        return
    excess = -a.minutes
    for e in sorted((e for e in entries if ours(e)), key=lambda e: e.start, reverse=True):  # newest first
        if excess <= 0:
            break
        have = int(round(e.minutes))
        cut = min(have, excess)
        a.shrink.append((e, have - cut))
        excess -= cut
    a.by_hand_excess = excess


def pushed_minutes(log_dir: Path, day: date) -> int:
    """Minutes really posted for the day according to the local push log (weekly view fallback)."""
    log = log_dir / f"push-{day.isoformat()}.jsonl"
    total = 0
    for line in log.read_text(encoding="utf-8").splitlines() if log.exists() else []:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not rec.get("dry_run") and rec.get("entry_id"):
            total += int(rec.get("minutes", 0))
    return total


def known_task(log_dir: Path, site: str, day: date) -> str:
    """The daily task an earlier real push created for this client and day, from the local push log."""
    log = log_dir / f"push-{day.isoformat()}.jsonl"
    if not log.exists():
        return ""
    found = ""
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("mode") == "create" and not rec.get("dry_run") and rec.get("site") == site and rec.get("task"):
            found = rec["task"]
    return found


def auto_title(evidence: list[tuple[datetime, str, str]], limit: int = 70) -> str:
    """A title from the day's commits: the first real one. Empty when there were none."""
    commits = [text for _, kind, text in evidence if kind == "commit" and not text.lower().startswith("merge ")]
    if not commits:
        return ""
    return commits[0] if len(commits[0]) <= limit else commits[0][:limit - 1].rstrip() + "…"


def daily_task_name(day: date, site: str, task_name: str = "{title}", titles: dict[str, str] | None = None,
                    evidence=None) -> str:
    """Create mode: the name of the client's daily task - its --title, else from the day's commits, else the date."""
    ev = evidence(site) if evidence else []
    title = (titles or {}).get(site) or auto_title(ev) or day.isoformat()
    return task_name.format(site=site, date=day.isoformat(), title=title)


def plan(day: date, rows: list[Row], sites: Sites, logged: list[Entry], assign: dict[str, str],
         mode: str = "existing", default_list: str = "", task_name: str = "{title}",
         titles: dict[str, str] | None = None, evidence=None, prompts=None,
         descriptions: dict[str, str] | None = None) -> Plan:
    """Decide what to post. `assign` maps a site to a task id (existing mode) or `list:ID` (create mode).

    Create mode only: `titles` (site -> title) override the automatic title from the day's commits,
    `evidence` (site -> [(time, kind, text)]) feeds that automatic title. `descriptions` (site -> Markdown)
    is written as the daily task's description; without one the task has none.
    `prompts` (site -> text) is the work's initial prompt, stored in the task's "Initial prompt" field.
    """
    titles = titles or {}
    descriptions = descriptions or {}
    prompts = prompts or (lambda site: "")
    evidence = evidence or (lambda site: [])
    p = Plan(day, mode, logged=list(logged))
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
            name = daily_task_name(day, key, task_name, titles, evidence)
            p.actions.append(Action("", key, start, minutes, minutes, 0, list_id=extra, task_name=name,
                                    prompt=prompts(key)[:MAX_PROMPT],
                                    description=descriptions.get(key, "").strip()[:MAX_DESCRIPTION]))
        else:
            a = Action(key, extra, start, minutes, minutes, 0)
            reconcile(p, a)
            p.actions.append(a)
    return p


def _find_daily_task(p: Plan, a: Action, api: ClickUp, log_dir: Path) -> Task | None:
    """Today's task for the client: from the push log, else from today's entries this tool wrote for the
    client on a task in the same list that was created today (log missing, or pushed from another machine)."""
    tid = known_task(log_dir, a.site, p.day)
    candidates = [tid] if tid else []
    candidates += [e.task_id for e in p.logged if e.description == f"{DESCRIPTION}: {a.site}" and e.task_id not in candidates]
    for i, cand in enumerate(candidates):
        try:
            t = api.task(cand)
        except ClickUpError:
            continue  # deleted in ClickUp since
        from_log = bool(tid) and i == 0
        same_day = t.created is None or t.created.date() == p.day
        if from_log or (t.list_id == a.list_id and same_day):
            return t
    return None


def _find_parent(a: Action, api: ClickUp, sites: Sites | None, tag: str) -> Task | None:
    """The client's parent task: the one sites.txt names (if it still exists, top-level, in the client's list),
    else the oldest top-level task of that name tagged by this tool in the list (sites.txt line lost)."""
    pid = sites.parents.get(a.site, "") if sites else ""
    if pid:
        try:
            t = api.task(pid)
            if not t.parent and (t.list_id == a.list_id or not t.list_id):
                return t
        except ClickUpError:
            pass  # deleted in ClickUp since
    if tag:
        for t in api.tagged_tasks(a.list_id, tag):
            if t.name.strip().lower() == a.parent_name.strip().lower():
                return t
    return None


def _resolve_parent(a: Action, api: ClickUp, sites: Sites | None, status: str, me: int | None, dry_run: bool,
                    tag: str, parents: dict[str, Task]) -> None:
    """Find or create the client's parent task (once per push per client) and remember it in sites.txt."""
    if a.site in parents:
        a.parent = parents[a.site]
        return
    found = _find_parent(a, api, sites, tag)
    if found is None:
        a.parent_created = True
        if dry_run:
            return
        found = api.create_task(a.list_id, a.parent_name, status, me, None, tag)
    a.parent = parents[a.site] = found
    if not dry_run and sites and sites.parents.get(a.site) != found.id:
        sites.set_parent(a.site, found.id)


def _resolve_daily_task(p: Plan, a: Action, api: ClickUp, status: str, me: int | None, dry_run: bool,
                        log_dir: Path, prompt_field: str = "", tag: str = "", sites: Sites | None = None,
                        parents: dict[str, Task] | None = None) -> None:
    """Reuse the task an earlier push created today (if it still exists in ClickUp), else create one
    as a subtask of the client's parent task (when `a.parent_name` is set)."""
    found = _find_daily_task(p, a, api, log_dir)
    if found:
        a.task, a.task_id = found, found.id
        reconcile(p, a)
        if a.description:  # a later push with --desc updates the description of today's task
            a.described = True
            if not dry_run:
                api.set_description(a.task_id, a.description)
        return
    if a.parent_name:
        _resolve_parent(a, api, sites, status, me, dry_run, tag, parents if parents is not None else {})
    fid = api.field_id(a.list_id, prompt_field) if (prompt_field and a.prompt) else ""
    fields = [{"id": fid, "value": a.prompt}] if fid else None
    a.created, a.prompt_set = True, bool(fid)
    if not dry_run:
        a.task = api.create_task(a.list_id, a.task_name, status, me, fields, tag, a.parent.id if a.parent else "",
                                 a.description)
        a.task_id = a.task.id


def execute(p: Plan, api: ClickUp, billable: bool, dry_run: bool, log_dir: Path, status: str = "in progress",
            prompt_field: str = "", tag: str = "", sites: Sites | None = None, parent_name: str = "") -> None:
    """Validate (or find/create) every task, then post and shrink. Appends a JSON-lines log, dry runs included.

    Create mode: `parent_name` ("{site} – hours"; {site} and {date} are filled) is the per-client parent task
    every daily task is a subtask of; "" keeps them flat. `sites` remembers the parent (`parent:<id>`)."""
    if p.blocked:
        return
    p.tag = tag
    if p.mode == "create":
        me = api.user().get("id")
        parents: dict[str, Task] = {}
        for a in p.actions:
            a.parent_name = parent_name.format(site=a.site, date=p.day.isoformat()).strip()
            try:
                _resolve_daily_task(p, a, api, status, me, dry_run, log_dir, prompt_field, tag, sites, parents)
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
        def record(a: Action, **extra) -> None:
            f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "dry_run": dry_run, "mode": p.mode,
                                "task": a.task_id, "task_name": a.task.name if a.task else a.task_name,
                                "created": a.created, "site": a.site, "parent": a.parent.id if a.parent else "",
                                **extra}, ensure_ascii=False) + "\n")
            f.flush()  # a failure halfway leaves an accurate log of what was done

        for a in p.to_post:
            if not dry_run:
                a.entry_id = api.add_entry(a.task_id, a.start, a.minutes, f"{DESCRIPTION}: {a.site}", billable)
            record(a, start=a.start.isoformat(timespec="minutes"), minutes=a.minutes, entry_id=a.entry_id)
        for a in p.to_shrink:
            for e, new in a.shrink:
                if not dry_run:
                    if new:
                        api.shorten_entry(e.id, e.start, new)
                    else:
                        api.delete_entry(e.id)
                record(a, shrunk_entry=e.id, from_minutes=int(round(e.minutes)), to_minutes=new)


def _name(a: Action) -> str:
    if a.task:
        where = f" ({a.task.location})" if a.task.location else ""
        return f"{a.task.name}{where} [{a.task_id}]"
    if a.parent_name:
        return f"{a.task_name} (new subtask of \"{a.parent_name}\" in list {a.list_id})"
    return f"{a.task_name} (new task in list {a.list_id})"


def _short(text: str, limit: int = 100) -> str:
    one = " ".join(text.split())
    return one[:limit] + ("…" if len(one) > limit else "")


def _parent_line(a: Action, dry_run: bool) -> str:
    """How the daily task was filed under the client's parent task, for the report."""
    if not a.parent_name:
        return ""
    if a.parent:
        if a.parent_created:
            return f"parent task \"{a.parent.name}\" [{a.parent.id}] created, remembered as parent:{a.parent.id} in sites.txt"
        return f"subtask of \"{a.parent.name}\" [{a.parent.id}]"
    if a.parent_created and dry_run:
        return f"parent task \"{a.parent_name}\" would be created first (list {a.list_id})"
    return ""


def render(p: Plan, dry_run: bool) -> str:
    out = []
    if p.blocked:
        out.append("Push blocked - resolve these rows first:")
        out += [f"  - {b}" for b in p.blocked]
        return "\n".join(out)
    posted = p.to_post
    created = [a for a in p.actions if a.created]
    if created:
        tagged = f", tagged \"{p.tag}\"" if p.tag else ""
        out.append(f"{'Would create' if dry_run else 'Created'} {len(created)} task(s), status \"in progress\", assigned to you{tagged}:")
        for a in created:
            out.append(f"  - {_name(a)}")
            parent = _parent_line(a, dry_run)
            if parent:
                out.append(f"      {parent}")
            if a.description:
                out.append(f"      Description: {_short(a.description)}")
            if a.prompt_set:
                first = " ".join(a.prompt.split())
                out.append(f"      Initial prompt: {first[:100]}{'…' if len(first) > 100 else ''}")
    described = [a for a in p.actions if a.described]
    if described:
        out.append(f"{'Would set' if dry_run else 'Set'} the description of {len(described)} existing task(s):")
        out += [f"  - {_name(a)}: {_short(a.description)}" for a in described]
    if posted:
        out.append(f"{'Would post' if dry_run else 'Posted'} {len(posted)} entr{'y' if len(posted) == 1 else 'ies'}:")
        for a in posted:
            already = f", {a.logged_minutes} min already logged" if a.logged_minutes else ""
            out.append(f"  - {a.start:%H:%M} {a.minutes} min -> {_name(a)}{already}"
                       + (f" entry {a.entry_id}" if a.entry_id else ""))
    shrunk = p.to_shrink
    if shrunk:
        out.append(f"{'Would reduce' if dry_run else 'Reduced'} (the table now has less time than was pushed earlier):")
        for a in shrunk:
            steps = ", ".join(f"entry {e.id} {int(round(e.minutes))}->{new} min" + (" (removed)" if not new else "")
                              for e, new in a.shrink)
            out.append(f"  - {_name(a)}: table {a.table_minutes} min, logged {a.logged_minutes} min, -{a.reduced} min: {steps}")
    by_hand = [a for a in p.actions if a.by_hand_excess]
    if by_hand:
        out.append("Logged by hand beyond the table (left as is - adjust in ClickUp if it is wrong):")
        out += [f"  - {_name(a)}: {a.by_hand_excess} min" for a in by_hand]
    settled = [a for a in p.actions if a.minutes == 0 and not a.created]
    if settled:
        out.append("Already logged (nothing to change):")
        out += [f"  - {_name(a)}: {a.table_minutes} min" for a in settled]
    if not posted and not shrunk and not settled and not by_hand and not described:
        out.append("Nothing to post.")
    return "\n".join(out)


def adopt(sites: Sites, api: ClickUp, dry_run: bool, status: str = "in progress", tag: str = "",
          parent_name: str = "{site} – hours", only: set[str] | None = None) -> list[str]:
    """File the daily tasks this tool created earlier (top-level, tagged) as subtasks of each client's parent
    task, creating the parent where a client has none yet. Returns one report line per client."""
    out = []
    if not tag or not parent_name:
        return ["adopt needs [clickup] task_tag and parent_name - without them earlier tasks cannot be told apart"]
    me = api.user().get("id")
    for site, list_id in sites.lists.items():
        if only and site.lower() not in only:
            continue
        name = parent_name.format(site=site, date=date.today().isoformat()).strip()
        a = Action("", site, datetime.now(), 0, 0, 0, list_id=list_id)
        a.parent_name = name
        try:
            parent = _find_parent(a, api, sites, tag)
            orphans = [t for t in api.tagged_tasks(list_id, tag)
                       if (parent is None or t.id != parent.id) and t.name.strip().lower() != name.lower()]
            if not orphans:
                out.append(f"{site}: nothing to move")
                continue
            if parent is None and not dry_run:
                parent = api.create_task(list_id, name, status, me, None, tag)
            if parent and sites.parents.get(site) != parent.id and not dry_run:
                sites.set_parent(site, parent.id)
            where = f'"{parent.name}" [{parent.id}]' if parent else f'"{name}" (would be created in list {list_id})'
            for t in orphans:
                if not dry_run:
                    api.move_under(t.id, parent.id)
            moved = "; ".join(f"{t.name[:50]} [{t.id}]" for t in orphans)
            out.append(f"{site}: {'would move' if dry_run else 'moved'} {len(orphans)} task(s) under {where}: {moved}")
        except ClickUpError as e:
            out.append(f"{site}: list {list_id} - {e}")
    return out


def token_from(cfg_token: str) -> str:
    return cfg_token or os.environ.get("CLICKUP_TOKEN", "")

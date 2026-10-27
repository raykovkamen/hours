"""Daily timesheet per client from local evidence (ActivityWatch, Claude Code sessions, git commits).

    hours.py [DAY] [--all] [--json]                       print the day's table
    hours.py push [DAY] [--dry-run] [--assign SITE=TASK]  log the table into ClickUp as time entries
    hours.py add "Client call" 30m [--at 14:00] [DAY]     note time no source can see (a call, a meeting)
    hours.py week [DAY]                                   the week containing DAY: hours, gaps, what is unpushed
    hours.py check                                        verify the installation (config, sources, ClickUp)

DAY is YYYY-MM-DD and defaults to today. Run with `py -3 -I` (Windows) or `python3 -I` (macOS/Linux).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops it; the package lives next to this file

if sys.version_info < (3, 11):
    sys.exit("hours: Python 3.11 or newer is required")

from timesheet import __version__  # noqa: E402
from timesheet import allocate, config, push, report  # noqa: E402
from timesheet.clickup import ClickUp, ClickUpError  # noqa: E402
from timesheet.sites import Sites  # noqa: E402
from timesheet.sources import activitywatch, calendar_ics, claude_sessions, git_commits, manual  # noqa: E402
from timesheet.timeline import Timeline  # noqa: E402

COMMANDS = ("table", "push", "add", "week", "check")
SOURCES = (activitywatch, claude_sessions, git_commits, calendar_ics, manual)


def build(cfg: config.Config, sites: Sites, day: date):
    """Collect every source for the day and lay the result out as table rows."""
    tl = Timeline(day, datetime.now().astimezone().tzinfo)
    reports = [src.collect(cfg, sites, tl) for src in SOURCES]
    tl.resolve_unplaced(cfg.sources.carry_minutes)
    slots = allocate.day_slots(tl.active_minutes(), cfg.workday)
    allocation = allocate.allocate(tl.per_key(), slots, cfg.workday.min_client_minutes)
    pause = tl.longest_pause(cfg.workday.break_window, min_minutes=30)
    rows = allocate.schedule(day, allocation, cfg.workday, allocate.break_time(pause, cfg.workday))
    return tl, reports, rows, slots


def cmd_table(args, cfg, sites) -> int:
    tl, reports, rows, slots = build(cfg, sites, args.day)
    if args.json:
        print(report.as_json(args.day, rows, sites, tl, reports))
        return 0
    print(report.listing(args.day, reports, args.all))
    if not rows:
        print("No activity found for this day.")
        return 0
    print(report.table(rows, sites, tl, cfg, slots))
    return 0


def pairs(items: list[str] | None, flag: str) -> dict[str, str]:
    """["Site=text", ...] -> {"Site": "text"}."""
    out = {}
    for item in items or []:
        site, _, text = item.partition("=")
        if not site.strip() or not text.strip():
            sys.exit(f"{flag} expects SITE=TEXT, got {item!r}")
        out[site.strip()] = text.strip()
    return out


def cmd_push(args, cfg, sites) -> int:
    assign = {}
    for item in args.assign or []:
        site, _, task = item.partition("=")
        if not site.strip() or not task.strip():
            sys.exit(f"--assign expects SITE=TASK, got {item!r}")
        assign[site.strip()] = task.strip()
    tl, reports, rows, slots = build(cfg, sites, args.day)
    print(report.table(rows, sites, tl, cfg, slots))
    print()
    if args.only:
        wanted = {s.strip().lower() for s in args.only}
        rows = [r for r in rows if r.key == allocate.BREAK or sites.split_key(r.key)[0].lower() in wanted]
        if not any(r.key != allocate.BREAK for r in rows):
            print(f"--only matched no row: {', '.join(args.only)}")
            return 2
    try:
        api = ClickUp(push.token_from(cfg.clickup.token), cfg.clickup.team_id)
        tz = datetime.now().astimezone().tzinfo
        day_start = datetime.combine(args.day, datetime.min.time()).replace(tzinfo=tz)
        logged = api.entries(day_start, day_start + timedelta(days=1))
        mode = "create" if args.create else cfg.clickup.mode
        titles = pairs(args.title, "--title")
        prompt_over = pairs(args.prompt, "--prompt")
        p = push.plan(args.day, rows, sites, logged, assign, mode, cfg.clickup.list_id, cfg.clickup.task_name,
                      titles, tl.evidence_for, lambda site: prompt_over.get(site) or tl.initial_prompt(site))
        push.execute(p, api, args.billable or cfg.clickup.billable, args.dry_run, cfg.log_dir, cfg.clickup.task_status,
                     cfg.clickup.prompt_field, cfg.clickup.task_tag)
    except ClickUpError as e:
        print(f"Push failed: {e}")
        return 2
    print(push.render(p, args.dry_run))
    return 0 if p.ok else 2


def cmd_add(args, cfg, sites) -> int:
    try:
        minutes = manual.parse_duration(args.duration)
        tz = datetime.now().astimezone().tzinfo
        if args.at:
            hh, mm = (int(x) for x in args.at.split(":"))
            start = datetime.combine(args.day, datetime.min.time()).replace(hour=hh, minute=mm, tzinfo=tz)
        elif args.day == date.today():
            start = datetime.now().astimezone().replace(second=0, microsecond=0) - timedelta(minutes=minutes)
        else:
            sys.exit("add: --at HH:MM is required for another day")
        manual.add(cfg.log_dir, args.day, args.text, minutes, start)
    except ValueError as e:
        sys.exit(f"add: {e}")
    site = sites.match(args.text)
    print(f"Noted {minutes} min at {start:%H:%M} on {args.day}: {args.text}"
          + (f" -> {site}" if site else " -> no client matched (it will show as a ? row; add a sites.txt rule or name it)"))
    print(f"Edit or remove notes in {manual.notes_file(cfg.log_dir, args.day)}")
    return 0


def cmd_week(args, cfg, sites) -> int:
    import contextlib
    import io
    monday = args.day - timedelta(days=args.day.weekday())
    days = [monday + timedelta(days=i) for i in range(7) if monday + timedelta(days=i) <= date.today()]
    clients: dict[str, float] = {}
    logged_by_day: dict[date, float] | None = None  # what ClickUp really holds; None = no token, use the push log
    token = push.token_from(cfg.clickup.token)
    if token:
        try:
            tz = datetime.now().astimezone().tzinfo
            start = datetime.combine(monday, datetime.min.time()).replace(tzinfo=tz)
            logged_by_day = {}
            for e in ClickUp(token, cfg.clickup.team_id).entries(start, start + timedelta(days=7)):
                logged_by_day[e.start.date()] = logged_by_day.get(e.start.date(), 0) + e.minutes
        except ClickUpError as e:
            print(f"(ClickUp not reachable, 'Logged' comes from the local push log: {e})")
            logged_by_day = None
    print(f"Week of {monday.isoformat()}")
    print()
    print("| Day | Tracked | Reported | Unknown | Unnamed rows | In ClickUp |")
    print("|---|---|---|---|---|---|")
    for d in days:
        with contextlib.redirect_stdout(io.StringIO()):
            tl, reports, rows, slots = build(cfg, sites, d)
        per = tl.per_key()
        for k, (_, mins) in per.items():
            site = sites.split_key(k)[0]
            clients[site] = clients.get(site, 0) + mins
        reported = sum(r.minutes for r in rows if r.key != allocate.BREAK)
        unknown = per.get("Unknown", (0, 0.0))[1]
        unnamed = sum(1 for r in rows if r.key.startswith("?"))
        pushed = int(round(logged_by_day.get(d, 0))) if logged_by_day is not None else push.pushed_minutes(cfg.log_dir, d)
        state = ("-" if not reported else "complete" if pushed >= reported else f"{report.hours(pushed)}h of {report.hours(reported)}h"
                 if pushed else "nothing")
        print(f"| {d:%a %d.%m} | {report.hours(tl.active_minutes())}h | {report.hours(reported)}h | "
              f"{int(round(unknown))} min | {unnamed or ''} | {state} |")
    if clients:
        print()
        print("Tracked per client this week:")
        print()
        print("| Client | Hours |")
        print("|---|---|")
        for site, mins in sorted(clients.items(), key=lambda kv: -kv[1]):
            print(f"| {site[:50]} | {report.hours(mins)}h |")
    return 0


def cmd_check(args, cfg, sites) -> int:
    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok = ok and good
        print(("  ok   " if good else "  FAIL ") + text)

    print(f"hours {__version__}, Python {sys.version.split()[0]}")
    line(cfg.path is not None and cfg.path.name == "config.toml", f"config: {cfg.path}" + ("" if cfg.path and cfg.path.name == "config.toml" else " (copy config.example.toml to config.toml)"))
    line(not sites.using_example, f"sites: {sites.path if not sites.using_example else 'sites.txt missing - using sites.example.txt'} ({len(sites.rules)} rules)")
    line(bool(cfg.sources.git_roots), "git roots: " + (", ".join(str(r) for r in cfg.sources.git_roots) or "none configured"))
    author = cfg.sources.git_author or git_commits._git(["config", "--global", "user.email"]).strip()
    line(bool(author), f"git author: {author or 'not set (git config --global user.email)'}")
    line(cfg.sources.claude_projects.is_dir(), f"claude projects: {cfg.sources.claude_projects}")
    tl = Timeline(date.today(), datetime.now().astimezone().tzinfo)
    aw = activitywatch.collect(cfg, sites, tl)
    line(aw.available, aw.summary)
    if aw.available:
        web, code = aw.extensions.get("web"), aw.extensions.get("vscode")
        print("  " + ("ok   browser extension found" if web else
                      "tip  browser extension missing: install 'ActivityWatch Web Watcher' so tab URLs place browser work"))
        print("  " + ("ok   VS Code extension found" if code else
                      "tip  VS Code extension missing: code --install-extension ActivityWatch.aw-watcher-vscode"))
    if cfg.sources.calendar_ics:
        cal = calendar_ics.collect(cfg, sites, tl)
        line(cal.available, cal.summary)
    else:
        print("  tip  calendar: not configured - set [sources] calendar_ics to count meetings")
    token = push.token_from(cfg.clickup.token)
    if not token:
        line(False, "clickup: no token (set [clickup] token in config.toml or CLICKUP_TOKEN) - push is unavailable")
    else:
        try:
            api = ClickUp(token, cfg.clickup.team_id)
            user = api.user()
            team = api.resolve_team()
            line(True, f"clickup: token valid for {user.get('username')} ({user.get('email')}), workspace {team}")
        except ClickUpError as e:
            line(False, f"clickup: {e}")
    print("All checks passed." if ok else "Some checks failed - see above.")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv.pop(0) if argv and argv[0] in COMMANDS else "table"
    ap = argparse.ArgumentParser(prog=f"hours.py {command}" if command != "table" else "hours.py",
                                 description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=Path, help="config file (default: config.toml next to this script)")
    ap.add_argument("--version", action="version", version=f"hours {__version__}")
    if command == "add":
        ap.add_argument("text", help='what it was, naming the client: "Josera call about the ES store"')
        ap.add_argument("duration", help="30m, 1h, 1h30m")
        ap.add_argument("--at", metavar="HH:MM", help="start time (default: it just ended)")
    if command != "check":
        ap.add_argument("day", nargs="?", type=date.fromisoformat, default=date.today(), help="YYYY-MM-DD (default: today)")
    if command == "table":
        ap.add_argument("--all", action="store_true", help="list every session and commit, not only the unnamed ones")
        ap.add_argument("--json", action="store_true", help="emit the rows as JSON")
    if command == "push":
        ap.add_argument("--dry-run", action="store_true", help="show what would be posted, write nothing")
        ap.add_argument("--assign", action="append", metavar="SITE=TASK", help="task for a client, this push only")
        ap.add_argument("--billable", action="store_true", help="mark the entries billable")
        ap.add_argument("--only", action="append", metavar="SITE", help="push only this client's rows (repeatable)")
        ap.add_argument("--title", action="append", metavar="SITE=TITLE",
                        help="create mode: title of the client's daily task (default: from the day's commits)")
        ap.add_argument("--prompt", action="append", metavar="SITE=TEXT",
                        help="create mode: value for the task's Initial prompt field (default: the work's first prompt)")
        ap.add_argument("--create", action="store_true", help="create one 'in progress' task per client for the day "
                        "and log the time there (same as [clickup] mode = \"create\")")
    args = ap.parse_args(argv)

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # prompts are often Cyrillic; cp1252 would crash

    cfg = config.load(args.config)
    sites = Sites(cfg.sites_file, cfg.sites_example)
    commands = {"table": cmd_table, "push": cmd_push, "add": cmd_add, "week": cmd_week, "check": cmd_check}
    return commands[command](args, cfg, sites)


if __name__ == "__main__":
    sys.exit(main())

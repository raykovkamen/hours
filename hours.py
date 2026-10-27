"""Daily timesheet per client from local evidence (ActivityWatch, Claude Code sessions, git commits).

    hours.py [DAY] [--all] [--json]                       print the day's table
    hours.py push [DAY] [--dry-run] [--assign SITE=TASK]  log the table into ClickUp as time entries
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
from timesheet.sources import activitywatch, claude_sessions, git_commits  # noqa: E402
from timesheet.timeline import Timeline  # noqa: E402

COMMANDS = ("table", "push", "check")


def build(cfg: config.Config, sites: Sites, day: date):
    """Collect every source for the day and lay the result out as table rows."""
    tl = Timeline(day, datetime.now().astimezone().tzinfo)
    reports = [src.collect(cfg, sites, tl) for src in (activitywatch, claude_sessions, git_commits)]
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
        p = push.plan(args.day, rows, sites, logged, assign)
        push.execute(p, api, args.billable or cfg.clickup.billable, args.dry_run, cfg.log_dir)
    except ClickUpError as e:
        print(f"Push failed: {e}")
        return 2
    print(push.render(p, args.dry_run))
    return 0 if p.ok else 2


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
    args = ap.parse_args(argv)

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # prompts are often Cyrillic; cp1252 would crash

    cfg = config.load(args.config)
    sites = Sites(cfg.sites_file, cfg.sites_example)
    return {"table": cmd_table, "push": cmd_push, "check": cmd_check}[command](args, cfg, sites)


if __name__ == "__main__":
    sys.exit(main())

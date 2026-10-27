"""Configuration: config.toml next to the package (falls back to config.example.toml)."""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


@dataclass(frozen=True)
class Workday:
    start: time = time(9, 0)
    step_minutes: int = 15
    min_hours: float = 0.0  # 0 = report real time; >0 = never report less than this
    max_hours: float = 0.0  # 0 = no cap
    break_minutes: int = 60
    break_window: tuple[time, time] = (time(11, 0), time(15, 0))
    break_default: time = time(12, 0)
    min_client_minutes: int = 5  # a client with less real time than this is a passing question, not a row


@dataclass(frozen=True)
class Sources:
    claude_projects: Path = Path.home() / ".claude" / "projects"
    idle_minutes: int = 15  # a longer gap inside a Claude session is not active time
    git_roots: tuple[Path, ...] = ()
    git_author: str = ""  # empty = git config user.email
    commit_lead_minutes: int = 30  # how much work a commit stands for
    activitywatch_url: str = "http://localhost:5600"
    carry_minutes: int = 30  # unplaced active minutes inherit a site this close
    # ClickUp task ids in branch names and commit subjects: "86cabc123", "CU-86cabc123", "#86cabc123"
    git_task_pattern: str = r"(?:\bCU-|#|\b)([0-9]{2,3}[a-z][a-z0-9]{5,9})\b"
    calendar_ics: str = ""  # iCal feed URL or .ics path with your meetings ("" = off)
    my_emails: frozenset[str] = frozenset()  # your addresses, to skip calendar events you declined


@dataclass(frozen=True)
class ClickUpConfig:
    token: str = ""  # or CLICKUP_TOKEN in the environment
    team_id: str = ""  # optional when the token sees exactly one workspace
    billable: bool = False
    mode: str = "existing"  # "existing": log on the task from branch/prompt/sites.txt; "create": one new task per client per day
    list_id: str = ""  # create mode: list for clients without their own `list:ID` in sites.txt
    task_status: str = "in progress"  # create mode: status of the new task (falls back to the list default)
    task_tag: str = "created by hours"  # create mode: tag on every task the push creates ("" = none)
    prompt_field: str = "Initial prompt"  # create mode: custom field filled with the work's first prompt ("" = off)
    task_name: str = "{title}"  # create mode: {title} (from --title or commits), also {site} and {date}


@dataclass(frozen=True)
class Config:
    workday: Workday = field(default_factory=Workday)
    sources: Sources = field(default_factory=Sources)
    clickup: ClickUpConfig = field(default_factory=ClickUpConfig)
    sites_file: Path = BASE_DIR / "sites.txt"
    sites_example: Path = BASE_DIR / "sites.example.txt"
    log_dir: Path = BASE_DIR / "logs"
    path: Path | None = None


def load(path: Path | None = None) -> Config:
    """Load config.toml (or the example when the user has none yet)."""
    if path is None:
        path = BASE_DIR / "config.toml"
        if not path.exists():
            path = BASE_DIR / "config.example.toml"
    raw: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    w, s = raw.get("workday", {}), raw.get("sources", {})
    workday = Workday(
        start=_hhmm(w.get("start", "09:00")),
        step_minutes=int(w.get("step_minutes", 15)),
        min_hours=float(w.get("min_hours", 0)),
        max_hours=float(w.get("max_hours", 0)),
        break_minutes=int(w.get("break_minutes", 60)),
        break_window=tuple(_hhmm(x) for x in w.get("break_window", ["11:00", "15:00"])),
        break_default=_hhmm(w.get("break_default", "12:00")),
        min_client_minutes=int(w.get("min_client_minutes", 5)),
    )
    roots = tuple(Path(os.path.expanduser(r)) for r in s.get("git_roots", []))
    sources = Sources(
        claude_projects=Path(os.path.expanduser(s.get("claude_projects", "~/.claude/projects"))),
        idle_minutes=int(s.get("idle_minutes", 15)),
        git_roots=tuple(r for r in roots if r.is_dir()),
        git_author=s.get("git_author", ""),
        commit_lead_minutes=int(s.get("commit_lead_minutes", 30)),
        activitywatch_url=s.get("activitywatch_url", "http://localhost:5600").rstrip("/"),
        carry_minutes=int(s.get("carry_minutes", 30)),
        git_task_pattern=s.get("git_task_pattern", Sources.git_task_pattern),
        calendar_ics=str(s.get("calendar_ics", "")),
        my_emails=frozenset(e.strip().lower() for e in [*s.get("my_emails", []), s.get("git_author", "")] if e.strip()),
    )
    c = raw.get("clickup", {})
    clickup = ClickUpConfig(token=str(c.get("token", "")), team_id=str(c.get("team_id", "")),
                            billable=bool(c.get("billable", False)), mode=str(c.get("mode", "existing")),
                            list_id=str(c.get("list_id", "")), task_status=str(c.get("task_status", "in progress")),
                            task_name=str(c.get("task_name", ClickUpConfig.task_name)),
                            prompt_field=str(c.get("prompt_field", ClickUpConfig.prompt_field)),
                            task_tag=str(c.get("task_tag", ClickUpConfig.task_tag)))
    if clickup.mode not in ("existing", "create"):
        sys.exit(f'config: [clickup] mode must be "existing" or "create" (got {clickup.mode!r})')
    if workday.step_minutes <= 0 or 60 % workday.step_minutes:
        sys.exit(f"config: step_minutes must divide 60 (got {workday.step_minutes})")
    if workday.max_hours and workday.max_hours < workday.min_hours:
        sys.exit("config: max_hours must be >= min_hours")
    return Config(workday=workday, sources=sources, clickup=clickup, path=path)

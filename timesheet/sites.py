"""Client ("site") resolution from sites.txt.

Format, one rule per line, first match wins:

    pattern = Site Name | clickup_task_id   # comment

`pattern` is a case-insensitive substring matched against project folder names, repository
names, window titles and prompt text. The ClickUp task id is optional and is the default task
for `/hours push`. A ClickUp task link (or `task:ID`) inside a prompt overrides it for that
session, which produces a row keyed "Site @task" so one client can fill several task rows.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

TASK_RE = re.compile(r"clickup\.com/t/(?:\d+/)?([a-z0-9]+)|\btask:([a-z0-9]+)", re.I)

# Folders whose name only says what kind of project it is, not which client.
GENERIC_SUFFIXES = ("migration", "data", "admin", "app", "gadget", "export", "tmp", "theme", "shopify", "store")

# Row keys that are not real clients.
UNKNOWN = "Unknown"


def is_unnamed(key: str) -> bool:
    return key.startswith("?")


def row_key(site: str | None, task: str | None) -> str | None:
    if site and task and not is_unnamed(site):
        return f"{site} @{task}"
    return site


class Sites:
    def __init__(self, path: Path, example: Path | None = None):
        self.path = path
        self.rules: list[tuple[str, str]] = []
        self.tasks: dict[str, str] = {}
        self.lists: dict[str, str] = {}  # site -> ClickUp list id where daily tasks are created (create mode)
        source = path if path.exists() else example
        self.using_example = source is not None and source != path
        if source and source.exists():
            self._load(source)

    def _load(self, source: Path) -> None:
        for line in source.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            pattern, rest = line.split("=", 1)
            site, _, refs = rest.split("#", 1)[0].partition("|")
            pattern, site = pattern.strip().lower(), site.strip()
            if pattern and site:
                self.rules.append((pattern, site))
                for ref in refs.split():  # "86cabc123" (default task) and/or "list:901524027003"
                    if ref.lower().startswith("list:"):
                        self.lists.setdefault(site, ref[5:])
                    else:
                        self.tasks.setdefault(site, ref)

    def match(self, text: str) -> str | None:
        text = text.lower()
        return next((site for pattern, site in self.rules if pattern in text), None)

    def split_key(self, key: str) -> tuple[str, str]:
        """'Site @task' -> (site, task); plain 'Site' -> (site, default task or '')."""
        site, _, task = key.partition(" @")
        return site, task or self.tasks.get(site, "")

    def auto_add(self, folder: str) -> str:
        """Name a client after a project folder (what-the-pink -> What The Pink) and remember the rule.

        The line is appended to sites.txt (when the user has one) so it can be edited later.
        """
        key = folder.lower()
        words = [w for w in re.split(r"[-_.\s]+", key) if w]
        while len(words) > 1 and words[-1] in GENERIC_SUFFIXES:
            words.pop()
        site = " ".join(w.capitalize() for w in words)
        self.rules.append((key, site))
        if self.path.exists():
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(f"{key} = {site}  # auto-added {date.today()}, rename if needed\n")
        return site


def task_in(text: str) -> str | None:
    hit = TASK_RE.search(text)
    return (hit.group(1) or hit.group(2)) if hit else None

"""Client ("site") resolution from sites.txt.

Format, one rule per line, first match wins:

    pattern = Site Name | clickup_task_id   # comment

`pattern` is a case-insensitive substring matched against project folder names, repository
names, window titles and prompt text. The ClickUp task id is optional and is the default task
for `/hours push`. A ClickUp task link (or `task:ID`) inside a prompt overrides it for that
session, which produces a row keyed "Site @task" so one client can fill several task rows.

Create mode adds two references after the `|`: `list:ID`, the list where the client's daily tasks
are opened, and `parent:ID`, the client's parent task under which every daily task is filed as a
subtask. The push writes `parent:` itself once it has created that task (see `set_parent`).
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

TASK_RE = re.compile(r"clickup\.com/t/(?:\d+/)?([a-z0-9]+)|\btask:([a-z0-9]+)", re.I)
PARENT_RE = re.compile(r"\bparent:\S+", re.I)

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
        self.parents: dict[str, str] = {}  # site -> parent task whose subtasks the daily tasks are (create mode)
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
                for ref in refs.split():  # "86cabc123" (default task), "list:901524027003", "parent:86cabc999"
                    if ref.lower().startswith("list:"):
                        self.lists.setdefault(site, ref[5:])
                    elif ref.lower().startswith("parent:"):
                        self.parents.setdefault(site, ref[7:])
                    else:
                        self.tasks.setdefault(site, ref)

    def match(self, text: str) -> str | None:
        text = text.lower()
        return next((site for pattern, site in self.rules if pattern in text), None)

    def split_key(self, key: str) -> tuple[str, str]:
        """'Site @task' -> (site, task); plain 'Site' -> (site, default task or '')."""
        site, _, task = key.partition(" @")
        return site, task or self.tasks.get(site, "")

    def set_parent(self, site: str, task_id: str) -> bool:
        """Remember the parent task of the client's daily tasks as `parent:ID` on its line in sites.txt.

        The line with the client's `list:` is used (else its first line); an older `parent:` is replaced.
        Returns False when there is no sites.txt to write to (the example is never changed).
        """
        self.parents[site] = task_id
        if self.using_example or not self.path.exists():
            return False
        with open(self.path, encoding="utf-8", newline="") as f:  # keep the file's own line endings
            lines = f.read().splitlines(keepends=True)
        hits: list[tuple[int, bool]] = []  # (line index, has list:)
        for i, line in enumerate(lines):
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            name, _, refs = line.split("=", 1)[1].split("#", 1)[0].partition("|")
            if name.strip() == site:
                hits.append((i, "list:" in refs.lower()))
        if not hits:
            return False
        i = next((i for i, has_list in hits if has_list), hits[0][0])
        raw = lines[i].rstrip("\r\n")
        eol = lines[i][len(raw):]
        pattern, rest = raw.split("=", 1)
        body, hash_, comment = rest.partition("#")
        body = body.rstrip()
        if PARENT_RE.search(body):
            body = PARENT_RE.sub(f"parent:{task_id}", body)
        else:
            body += f" parent:{task_id}" if "|" in body else f" | parent:{task_id}"
        lines[i] = f"{pattern}={body}" + (f"  {hash_}{comment}" if hash_ else "") + eol
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            f.write("".join(lines))
        return True

    def auto_add(self, folder: str) -> str:
        """Name a client after a project folder (what-the-pink -> What The Pink) and remember the rule.

        The line is appended to sites.txt (when the user has one) so it can be edited later.
        """
        key = folder.lower()
        known = next((site for pattern, site in self.rules if pattern == key), None)
        if known:  # never write the same rule twice
            return known
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

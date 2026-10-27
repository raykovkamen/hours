"""Claude Code sessions: ~/.claude/projects/<project>/<session>.jsonl.

The project folder names the client. A prompt that names another client moves the session to it from
then on; a ClickUp task link in a prompt moves it to that task. Sessions opened to ask for the timesheet
are excluded, as are prompts that never got a reply.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import Report
from ..config import Config
from ..sites import Sites, is_unnamed, row_key, task_in
from ..timeline import Timeline

SOURCE = "claude"
TIMESHEET_PHRASES = ("today's hours", "hours for", "/hours")
# Folders that hold many clients' repos; a session started there is placed by its prompts, not its folder.
CONTAINER_DEPTH = 3  # drive + one folder (D:\ERP) is a container; D:\PROJECTS\shop is a project
MIN_REPLY = 80  # shorter assistant texts are acknowledgements, not descriptions of work
REPLY_CHARS = 600  # kept per reply for the evidence listing (local only, never sent anywhere)
PASTED_RE = re.compile(r"<pasted_content[^>]*>.*?</pasted_content>", re.S)


def label(text: str) -> str:
    """Prompt text for the listing: the user's own words first, pasted material only as a fallback."""
    own = PASTED_RE.sub(" ", text).strip()
    if own:
        return own
    pasted = re.sub(r"<[^>]+>", " ", text).strip()
    return f"(pasted) {pasted}"


@dataclass
class Session:
    start: datetime
    end: datetime
    folder: str
    cwd: str
    active: timedelta
    prompts: list[tuple[datetime, str]]
    switches: list[tuple[datetime, str]]  # (prompt time, row key from then on)

    @property
    def unnamed(self) -> bool:
        return any(is_unnamed(k) for _, k in self.switches)


def _branch_task(cfg: Config, cwd: str | None) -> str | None:
    """A ClickUp task id in the branch checked out in the session's repository (meaningful for today only)."""
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        branch = subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                                text=True, timeout=10, encoding="utf-8", errors="replace").stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return None
    hit = re.compile(cfg.sources.git_task_pattern, re.I).search(branch)
    return hit.group(1) if hit else None


def _project_site(sites: Sites, folder: str, cwd: str | None) -> str | None:
    """The client a session folder stands for, learning new project folders on the way."""
    site = sites.match(folder)
    if site:
        return site
    parts = [p for p in (cwd or "").replace("\\", "/").split("/") if p]
    if len(parts) >= CONTAINER_DEPTH:
        return sites.auto_add(parts[-1])
    return None


def is_real_prompt(c: str) -> bool:
    """Typed (or pasted) by the user - not a command, hook output, image placeholder or timesheet request."""
    text = c.strip()
    if not text or text.startswith("[Image") or any(k in text.lower() for k in TIMESHEET_PHRASES):
        return False
    return not text.startswith("<") or bool(PASTED_RE.search(text))


def clean_prompt(c: str) -> str:
    """The prompt as the user wrote it, pasted material included, without the paste markers."""
    return re.sub(r"</?pasted_content[^>]*>", "", c).strip()


def _read(path: str, day, idle: timedelta):
    stamps, prompts, cwd, first, initial, replies = [], [], None, None, None, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("timestamp")
            if not t:
                continue
            if initial is None and d.get("type") == "user":  # the session's first prompt, whatever the day
                c = d.get("message", {}).get("content")
                if isinstance(c, str) and is_real_prompt(c):
                    initial = clean_prompt(c)
            local = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone()
            if local.date() != day:
                continue
            stamps.append(local)
            cwd = cwd or d.get("cwd")
            if d.get("type") == "assistant":  # what was actually done - the best material for a task title
                content = d.get("message", {}).get("content")
                for c in content if isinstance(content, list) else []:
                    if isinstance(c, dict) and c.get("type") == "text" and len(c.get("text", "")) >= MIN_REPLY:
                        replies.append((local, c["text"]))
            if d.get("type") == "user":
                c = d.get("message", {}).get("content")
                if isinstance(c, str) and c.strip():
                    if first is None:
                        first = c.lower()
                    if not c.lstrip().startswith("<") and not any(k in c.lower() for k in TIMESHEET_PHRASES):
                        prompts.append((local, c.strip()))
                    elif PASTED_RE.search(c):  # a paste is a real prompt; other <...> blocks are system noise
                        prompts.append((local, c.strip()))
    stamps.sort()
    prompts.sort(key=lambda p: p[0])
    replies.sort(key=lambda r: r[0])
    return stamps, prompts, cwd, first, initial, replies


def collect(cfg: Config, sites: Sites, tl: Timeline) -> Report:
    idle = timedelta(minutes=cfg.sources.idle_minutes)
    sessions: list[Session] = []
    pattern = os.path.join(str(cfg.sources.claude_projects), "*", "*.jsonl")
    for path in glob.glob(pattern):
        if datetime.fromtimestamp(os.path.getmtime(path)).date() < tl.day:
            continue
        folder = os.path.basename(os.path.dirname(path))
        stamps, prompts, cwd, first, initial, replies = _read(path, tl.day, idle)
        if not prompts or (first and any(k in first for k in TIMESHEET_PHRASES)):
            continue
        active = sum(((b - a) for a, b in zip(stamps, stamps[1:]) if b - a <= idle), timedelta())
        if active < timedelta(minutes=1):
            continue
        one_off = len(prompts) == 1 and active < timedelta(minutes=5)

        base = None if one_off else (_project_site(sites, folder, cwd) or f"? {os.path.basename(cwd or folder)}")
        # For today, the branch checked out in the session's repository usually names the task.
        switches, current, task = [], base, (_branch_task(cfg, cwd) if tl.day == date.today() else None)
        for t, text in prompts:
            named = sites.match(text)
            if named and named != current:
                current, task = named, None
            task = task_in(text) or task
            key = row_key(current, task) if current else f"? one-off: {label(text)[:64]}".replace("\n", " ")
            switches.append((t, key))
        if base and is_unnamed(base):  # a container-folder session is about the first client it names
            named = next((k for _, k in switches if not is_unnamed(k)), None)
            if named:
                switches = [(t, named if k == base else k) for t, k in switches]

        for a, b in zip(stamps, stamps[1:]):
            if b - a <= idle:
                key = next((k for t, k in reversed(switches) if t <= a), switches[0][1])
                tl.mark(a, b, key, SOURCE)
        for (t, text), (_, key) in zip(prompts, switches):
            tl.note(key, t, "prompt", label(text)[:200])
        for t, text in replies:  # a reply belongs to the client of the prompt it answers
            key = next((k for pt, k in reversed(switches) if pt <= t), switches[0][1])
            tl.note(key, t, "reply", text[:REPLY_CHARS])
        if initial:  # the request that started this session's work, for the task's "Initial prompt" field
            tl.note_initial(switches[0][1], stamps[0], initial)
        sessions.append(Session(stamps[0], stamps[-1], folder, cwd or "", active, prompts, switches))

    sessions.sort(key=lambda s: s.start)
    details, needs = [], []
    for s in sessions:
        block = [f"  session {s.start:%H:%M}-{s.end:%H:%M}, active ~{int(s.active.total_seconds() // 60)} min, folder {s.folder} (cwd {s.cwd})"]
        shown = None
        for i, ((t, text), (_, key)) in enumerate(zip(s.prompts, s.switches)):
            if key != shown or i < 12:
                tag = f"[{key}] " if key != shown else ""
                block.append(f"    - {t:%H:%M} {tag}{label(text)[:200]}".replace("\n", " "))
                shown = key
        details.extend(block)
        if s.unnamed:
            needs.extend(block)
    return Report(SOURCE, f"Claude Code: {len(sessions)} session(s)", details, needs)

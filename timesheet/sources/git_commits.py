"""Git commits by the user in the configured repository roots.

A commit stands for the work that led to it: up to `commit_lead_minutes` before it, capped at the previous
commit in the same repository. The repository folder names the client. A ClickUp task id in the commit
subject or in the name of a branch containing the commit names the task (row "Site @task").
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta

from . import Report
from ..config import Config
from ..sites import Sites, is_unnamed, row_key
from ..timeline import Timeline

SOURCE = "git"


def _git(args: list[str], timeout: int = 20) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace").stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def task_in_text(pattern: re.Pattern, *texts: str) -> str | None:
    for text in texts:
        hit = pattern.search(text)
        if hit:
            return hit.group(1)
    return None


def _branches(repo: str, sha: str) -> list[str]:
    out = _git(["-C", repo, "branch", "-a", "--contains", sha, "--format=%(refname:short)"])
    return [b.strip() for b in out.splitlines() if b.strip()]


def collect(cfg: Config, sites: Sites, tl: Timeline) -> Report:
    author = cfg.sources.git_author or _git(["config", "--global", "user.email"]).strip()
    if not author or not cfg.sources.git_roots:
        why = "no git_roots configured" if author else "git user.email not set"
        return Report(SOURCE, f"Git: skipped ({why})", available=False)
    pattern = re.compile(cfg.sources.git_task_pattern, re.I)
    day = tl.day.isoformat()
    commits: list[tuple[datetime, str, str, str]] = []  # (time, repo, row key, subject)
    for root in cfg.sources.git_roots:
        for dot_git in sorted(glob.glob(os.path.join(str(root), "*", ".git"))):
            repo = os.path.dirname(dot_git)
            log = _git(["-C", repo, "log", "--all", f"--author={author}", f"--since={day} 00:00",
                        f"--until={day} 23:59:59", "--format=%cI\t%H\t%s"])
            rows = []
            for line in log.splitlines():
                when, sha, subject = (line.split("\t", 2) + ["", ""])[:3]
                try:
                    rows.append((datetime.fromisoformat(when), sha, subject))
                except ValueError:
                    continue
            rows.sort()
            name = os.path.basename(repo)
            site = sites.match(name)
            prev = None
            for when, sha, subject in rows:
                task = task_in_text(pattern, subject, *_branches(repo, sha)) if site else None
                key = row_key(site, task) if site else f"? {name}"
                start = when - timedelta(minutes=cfg.sources.commit_lead_minutes)
                if prev and prev > start:
                    start = prev
                tl.mark(start, when, key, SOURCE)
                commits.append((when, name, key, subject))
                tl.note(key, when, "commit", subject)
                prev = when
    commits.sort()
    per_repo = ", ".join(f"{r} {n}" for r, n in Counter(c[1] for c in commits).most_common())
    summary = f"Git: {len(commits)} commit(s)" + (f" - {per_repo}" if commits else "")
    details = [f"  commit {when:%H:%M} [{key}] {repo}: {subject[:90]}" for when, repo, key, subject in commits]
    needs = [d for d, c in zip(details, commits) if is_unnamed(c[2])]
    return Report(SOURCE, summary, details, needs)

"""Minimal ClickUp API v2 client (standard library only).

Authentication uses a personal API token (ClickUp > Settings > Apps > API Token), read from
`[clickup] token` in config.toml or the CLICKUP_TOKEN environment variable. The token is never logged.
Rate limits (HTTP 429), server errors (5xx) and dropped connections are retried with a growing pause.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime

API = "https://api.clickup.com/api/v2"
RETRIES = 4  # attempts after the first, for 429 / 5xx / network errors
RETRY_WAIT = 2.0  # seconds before the first retry; doubles each time (capped by RETRY_MAX)
RETRY_MAX = 30.0


class ClickUpError(RuntimeError):
    pass


@dataclass(frozen=True)
class Task:
    id: str
    name: str
    status: str
    list_name: str
    folder_name: str
    list_id: str = ""
    created: datetime | None = None

    @property
    def location(self) -> str:
        return " / ".join(p for p in (self.folder_name, self.list_name) if p)


@dataclass(frozen=True)
class Entry:
    id: str
    task_id: str
    task_name: str
    start: datetime
    minutes: float
    description: str


def to_ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


class ClickUp:
    def __init__(self, token: str, team_id: str = "", sleep=time.sleep):
        if not token:
            raise ClickUpError("no ClickUp token: set [clickup] token in config.toml or CLICKUP_TOKEN")
        self._token = token
        self.team_id = team_id
        self._sleep = sleep

    # -- transport -----------------------------------------------------------------
    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        url = f"{API}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        wait = RETRY_WAIT
        for attempt in range(RETRIES + 1):
            req = urllib.request.Request(url, data=data, method=method,
                                         headers={"Authorization": self._token, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    raw = r.read()
                    return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:300]
                retryable = e.code == 429 or e.code >= 500
                if not retryable or attempt == RETRIES:
                    raise ClickUpError(f"ClickUp {method} {path}: HTTP {e.code} {detail}") from None
                reset = (e.headers or {}).get("X-RateLimit-Reset") if e.code == 429 else None
                pause = wait
                if reset and str(reset).isdigit():  # epoch seconds when the limit resets
                    pause = max(1.0, min(RETRY_MAX, int(reset) - time.time()))
            except (urllib.error.URLError, OSError) as e:
                if attempt == RETRIES:
                    raise ClickUpError(f"ClickUp unreachable: {e}") from None
                pause = wait
            self._sleep(pause)
            wait = min(RETRY_MAX, wait * 2)
        raise ClickUpError("unreachable")  # pragma: no cover - the loop always returns or raises

    # -- identity ------------------------------------------------------------------
    def user(self) -> dict:
        return self._call("GET", "user")["user"]

    def resolve_team(self) -> str:
        if self.team_id:
            return self.team_id
        teams = self._call("GET", "team")["teams"]
        if len(teams) != 1:
            names = ", ".join(f"{t['name']} ({t['id']})" for t in teams)
            raise ClickUpError(f"token sees {len(teams)} workspaces, set [clickup] team_id: {names}")
        self.team_id = teams[0]["id"]
        return self.team_id

    # -- tasks ---------------------------------------------------------------------
    def task(self, task_id: str) -> Task:
        t = self._call("GET", f"task/{task_id}")
        status = t.get("status") or {}
        created = t.get("date_created")
        return Task(t["id"], t.get("name", ""), status.get("status", ""),
                    (t.get("list") or {}).get("name", ""), (t.get("folder") or {}).get("name", ""),
                    str((t.get("list") or {}).get("id", "")),
                    datetime.fromtimestamp(int(created) / 1000).astimezone() if created else None)

    def field_id(self, list_id: str, name: str) -> str:
        """Id of the custom field called `name` (case-insensitive) available on the list, or ""."""
        cache = self.__dict__.setdefault("_fields", {})
        if list_id not in cache:
            cache[list_id] = {f["name"].strip().lower(): f["id"] for f in self._call("GET", f"list/{list_id}/field").get("fields", [])}
        return cache[list_id].get(name.strip().lower(), "")

    def add_tag(self, task_id: str, tag: str) -> None:
        """Attach a tag (ClickUp creates it in the space if it does not exist yet)."""
        self._call("POST", f"task/{task_id}/tag/{urllib.parse.quote(tag)}")

    def create_task(self, list_id: str, name: str, status: str, assignee_id: int | None,
                    custom_fields: list[dict] | None = None, tag: str = "") -> Task:
        """Create a task; when the list has no such status, it is created with the list's default status."""
        body: dict = {"name": name}
        if custom_fields:
            body["custom_fields"] = custom_fields
        if assignee_id:
            body["assignees"] = [assignee_id]
        try:
            t = self._call("POST", f"list/{list_id}/task", body={**body, "status": status} if status else body)
        except ClickUpError as e:
            if not status or "status" not in str(e).lower():
                raise
            t = self._call("POST", f"list/{list_id}/task", body=body)
        if tag:
            self.add_tag(t["id"], tag)
        return self.task(t["id"])

    # -- time entries --------------------------------------------------------------
    def entries(self, day_start: datetime, day_end: datetime) -> list[Entry]:
        """The authenticated user's entries starting within [day_start, day_end)."""
        team = self.resolve_team()
        data = self._call("GET", f"team/{team}/time_entries",
                          {"start_date": to_ms(day_start), "end_date": to_ms(day_end)})["data"]
        out = []
        for e in data:
            task = e.get("task") or {}
            start = datetime.fromtimestamp(int(e["start"]) / 1000, tz=day_start.tzinfo)
            out.append(Entry(str(e["id"]), str(task.get("id", "")), task.get("name", ""), start,
                             int(e.get("duration", 0)) / 60000, e.get("description") or ""))
        return out

    def add_entry(self, task_id: str, start: datetime, minutes: int, description: str, billable: bool) -> str:
        team = self.resolve_team()
        body = {"tid": task_id, "start": to_ms(start), "duration": minutes * 60000,
                "description": description, "billable": billable}
        return str(self._call("POST", f"team/{team}/time_entries", body=body)["data"]["id"])

    def shorten_entry(self, entry_id: str, start: datetime, minutes: int) -> None:
        """Set an entry's duration (used only on entries this tool wrote)."""
        team = self.resolve_team()
        self._call("PUT", f"team/{team}/time_entries/{entry_id}",
                   body={"start": to_ms(start), "end": to_ms(start) + minutes * 60000, "duration": minutes * 60000})

    def delete_entry(self, entry_id: str) -> None:
        team = self.resolve_team()
        self._call("DELETE", f"team/{team}/time_entries/{entry_id}")

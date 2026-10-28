import json
import os
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from timesheet.config import Config, Sources
from timesheet.sites import Sites
from timesheet.sources import activitywatch, claude_sessions, git_commits
from timesheet.timeline import Timeline

DAY = date(2026, 10, 27)
TZ = timezone(timedelta(hours=2))


def at(h, m=0):
    return datetime(2026, 10, 27, h, m, tzinfo=TZ)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / "sites.txt").write_text("acme = Acme | 86c1\nzeta = Zeta\n", encoding="utf-8")
        self.sites = Sites(self.root / "sites.txt")
        self.tl = Timeline(DAY, TZ)

    def tearDown(self):
        self.dir.cleanup()

    def config(self, **sources):
        return Config(sources=Sources(**sources), sites_file=self.root / "sites.txt")


class ClaudeSessionsTest(Fixture):
    def session(self, folder, cwd, events):
        d = self.root / "projects" / folder
        d.mkdir(parents=True, exist_ok=True)
        lines = []
        for t, kind, text in events:
            rec = {"timestamp": iso(t), "cwd": cwd, "type": kind}
            if kind == "user":
                rec["message"] = {"role": "user", "content": text}
            else:
                rec["message"] = {"role": "assistant", "content": [{"type": "text", "text": text}]}
            lines.append(json.dumps(rec))
        (d / "s.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def collect(self):
        return claude_sessions.collect(self.config(claude_projects=self.root / "projects"), self.sites, self.tl)

    def test_folder_names_the_client_and_gaps_count_as_active(self):
        self.session("D--PROJECTS-acme", r"D:\PROJECTS\acme", [
            (at(9, 0), "user", "fix the header"),
            (at(9, 5), "assistant", "done"),
            (at(9, 40), "user", "and the footer"),  # 35-min gap: idle, not active
            (at(9, 45), "assistant", "done"),
        ])
        report = self.collect()
        self.assertEqual(report.summary, "Claude Code: 1 session(s)")
        self.assertEqual(self.tl.per_key()["Acme"][1], 10.0)

    def test_prompt_naming_a_client_moves_the_session(self):
        self.session("D--PROJECTS-acme", r"D:\PROJECTS\acme", [
            (at(9, 0), "user", "fix the header"),
            (at(9, 5), "assistant", "done"),
            (at(9, 6), "user", "now the same on zeta"),
            (at(9, 16), "assistant", "done"),
        ])
        self.collect()
        per = self.tl.per_key()
        self.assertEqual(per["Acme"][1], 6.0)
        self.assertEqual(per["Zeta"][1], 10.0)

    def test_container_folder_session_belongs_to_the_first_client_it_names(self):
        self.session("D--ERP", r"D:\ERP", [
            (at(9, 0), "user", "look at this connector log"),
            (at(9, 5), "assistant", "ok"),
            (at(9, 6), "user", "this is for acme"),
            (at(9, 10), "assistant", "ok"),
        ])
        report = self.collect()
        self.assertEqual(self.tl.per_key()["Acme"][1], 10.0)
        self.assertEqual(report.needs_naming, [])

    def test_container_folder_session_naming_nothing_stays_unnamed(self):
        self.session("D--ERP", r"D:\ERP", [
            (at(9, 0), "user", "what is this error"),
            (at(9, 5), "assistant", "ok"),
            (at(9, 6), "user", "and this one"),
            (at(9, 10), "assistant", "ok"),
        ])
        report = self.collect()
        self.assertIn("? ERP", self.tl.per_key())
        self.assertTrue(report.needs_naming)

    def test_new_project_folder_is_learned(self):
        self.session("D--PROJECTS-new-shop", r"D:\PROJECTS\new-shop", [
            (at(9, 0), "user", "hello"),
            (at(9, 5), "assistant", "hi"),
        ])
        self.collect()
        self.assertIn("New Shop", self.tl.per_key())
        self.assertIn("new-shop = New Shop", (self.root / "sites.txt").read_text(encoding="utf-8"))

    def test_project_under_a_container_folder_matches_without_relearning(self):
        for _ in range(2):  # every run: the Claude folder is the container, the cwd the project
            self.session("D--ERP", r"D:\ERP\acme-migration", [
                (at(9, 0), "user", "hello"),
                (at(9, 5), "assistant", "hi"),
            ])
            self.collect()
        self.assertIn("Acme", self.tl.per_key())
        self.assertNotIn("auto-added", (self.root / "sites.txt").read_text(encoding="utf-8"))

    def test_task_link_in_prompt_keys_the_row_to_that_task(self):
        self.session("D--PROJECTS-acme", r"D:\PROJECTS\acme", [
            (at(9, 0), "user", "work on https://app.clickup.com/t/86cxyz"),
            (at(9, 10), "assistant", "ok"),
        ])
        self.collect()
        self.assertIn("Acme @86cxyz", self.tl.per_key())

    def test_timesheet_sessions_and_unanswered_prompts_are_ignored(self):
        self.session("D--PROJECTS-acme", r"D:\PROJECTS\acme", [
            (at(9, 0), "user", "/hours"),
            (at(9, 5), "assistant", "table"),
        ])
        self.session("D--PROJECTS-zeta", r"D:\PROJECTS\zeta", [
            (at(9, 0), "user", "a screenshot"),
        ])
        report = self.collect()
        self.assertEqual(report.summary, "Claude Code: 0 session(s)")
        self.assertEqual(self.tl.active_minutes(), 0)


class GitCommitsTest(Fixture):
    def repo(self, name, commits):
        path = self.root / "repos" / name
        path.mkdir(parents=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_COMMITTER_NAME": "T",
               "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_EMAIL": "t@example.com"}
        subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
        for i, when in enumerate(commits):
            (path / f"f{i}").write_text("x")
            stamp = when.isoformat()
            subprocess.run(["git", "-C", str(path), "add", "."], check=True, env=env)
            subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", f"c{i}"], check=True,
                           env={**env, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})

    def collect(self):
        cfg = self.config(git_roots=(self.root / "repos",), git_author="t@example.com")
        return git_commits.collect(cfg, self.sites, self.tl)

    def test_commit_stands_for_the_lead_before_it_capped_at_previous_commit(self):
        self.repo("acme-theme", [at(10, 0), at(10, 10)])
        report = self.collect()
        self.assertIn("acme-theme 2", report.summary)
        self.assertEqual(self.tl.per_key()["Acme"][1], 40.0)  # 30 + 10

    def test_task_id_in_branch_name_or_subject_keys_the_row(self):
        self.repo("acme-theme", [at(10, 0)])
        path = self.root / "repos" / "acme-theme"
        subprocess.run(["git", "-C", str(path), "branch", "-m", "feature/86cabc123-header"], check=True)
        self.collect()
        self.assertIn("Acme @86cabc123", self.tl.per_key())

    def test_task_id_in_commit_subject(self):
        self.repo("acme-theme", [at(10, 0)])
        path = self.root / "repos" / "acme-theme"
        subprocess.run(["git", "-C", str(path), "commit", "-q", "--amend", "-m", "CU-123xf86w832 fix footer"], check=True,
                       env={**os.environ, "GIT_COMMITTER_DATE": at(10, 0).isoformat(), "GIT_AUTHOR_DATE": at(10, 0).isoformat(),
                            "GIT_AUTHOR_NAME": "T", "GIT_COMMITTER_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_EMAIL": "t@example.com"})
        self.collect()
        self.assertIn("Acme @123xf86w832", self.tl.per_key())

    def test_unknown_repo_is_flagged(self):
        self.repo("mystery", [at(10, 0)])
        report = self.collect()
        self.assertIn("? mystery", self.tl.per_key())
        self.assertEqual(len(report.needs_naming), 1)

    def test_other_days_are_ignored(self):
        self.repo("acme-theme", [at(10, 0) - timedelta(days=1)])
        self.collect()
        self.assertEqual(self.tl.active_minutes(), 0)


class ActivityWatchTest(Fixture):
    def fake_api(self, window_events, afk_events):
        def get(base, path, **params):
            if path == "buckets/":
                return {"aw-watcher-window_pc": {}, "aw-watcher-afk_pc": {}}
            if path.startswith("buckets/aw-watcher-window"):
                return window_events
            return afk_events
        return get

    def collect(self, window_events, afk_events=()):
        with mock.patch.object(activitywatch, "_get", self.fake_api(window_events, list(afk_events))):
            return activitywatch.collect(self.config(), self.sites, self.tl)

    def test_titles_place_minutes_and_afk_is_removed(self):
        report = self.collect(
            [{"timestamp": iso(at(9, 0)), "duration": 600, "data": {"app": "Code.exe", "title": "x.liquid - acme - Visual Studio Code"}},
             {"timestamp": iso(at(9, 10)), "duration": 600, "data": {"app": "chrome.exe", "title": "Inbox - Gmail"}}],
            [{"timestamp": iso(at(9, 15)), "duration": 300, "data": {"status": "afk"}}],
        )
        per = self.tl.per_key()
        self.assertEqual(per["Acme"][1], 10.0)
        self.assertEqual(self.tl.active_minutes(), 15)  # 5 afk minutes removed from the Gmail window
        self.assertIn("Inbox - Gmail", report.needs_naming[0])

    def test_not_running_is_reported_not_fatal(self):
        def boom(base, path, **params):
            raise OSError("refused")
        with mock.patch.object(activitywatch, "_get", boom):
            report = activitywatch.collect(self.config(), self.sites, self.tl)
        self.assertFalse(report.available)
        self.assertIn("not reachable", report.summary)

    def collect_buckets(self, buckets):
        def get(base, path, **params):
            if path == "buckets/":
                return {name: {} for name in buckets}
            name = path.split("/")[1]
            return buckets[name]
        with mock.patch.object(activitywatch, "_get", get):
            return activitywatch.collect(self.config(), self.sites, self.tl)

    def test_browser_url_places_minutes_the_title_cannot(self):
        report = self.collect_buckets({
            "aw-watcher-window_pc": [{"timestamp": iso(at(9, 0)), "duration": 600,
                                      "data": {"app": "chrome.exe", "title": "Shopify Plus - Google Chrome"}}],
            "aw-watcher-web-chrome": [{"timestamp": iso(at(9, 0)), "duration": 300,
                                       "data": {"url": "https://admin.shopify.com/store/acme-shop/products?x=1", "title": "x"}},
                                      {"timestamp": iso(at(9, 5)), "duration": 300,
                                       "data": {"url": "https://example.org/docs/page", "title": "y"}}],
        })
        self.assertEqual(self.tl.per_key()["Acme"][1], 5.0)
        self.assertEqual(report.extensions, {"web": True, "vscode": False})
        self.assertIn("browser URLs: 10 min recorded, 5 min placed that the tab title could not", report.summary)
        self.assertIn("example.org/docs/page", report.needs_naming[0])  # short URL, no query string

    def test_url_is_ignored_when_a_non_browser_window_is_in_front(self):
        self.collect_buckets({
            "aw-watcher-window_pc": [{"timestamp": iso(at(9, 0)), "duration": 300, "data": {"app": "Slack.exe", "title": "DM"}}],
            "aw-watcher-web-chrome": [{"timestamp": iso(at(9, 0)), "duration": 300,
                                       "data": {"url": "https://admin.shopify.com/store/acme-shop"}}],
        })
        self.assertNotIn("Acme", self.tl.per_key())

    def test_vscode_project_places_editing(self):
        report = self.collect_buckets({
            "aw-watcher-window_pc": [],
            "aw-watcher-vscode_pc": [{"timestamp": iso(at(14, 0)), "duration": 1200,
                                      "data": {"project": "D:\\PROJECTS\\zeta-theme", "file": "a.liquid"}}],
        })
        self.assertEqual(self.tl.per_key()["Zeta"][1], 20.0)
        self.assertIn("VS Code: 20 min placed", report.summary)


class ManualNotesTest(Fixture):
    def test_parse_duration(self):
        from timesheet.sources import manual
        self.assertEqual([manual.parse_duration(x) for x in ("30m", "1h", "1h30m", "1.5h", "90")], [30, 60, 90, 90, 90])
        with self.assertRaises(ValueError):
            manual.parse_duration("soon")

    def test_notes_mark_time_and_unnamed_notes_are_flagged(self):
        from timesheet.sources import manual
        logs = self.root / "logs"
        manual.add(logs, DAY, "Acme call about checkout", 30, at(15, 0))
        manual.add(logs, DAY, "dentist", 20, at(16, 0))
        cfg = Config(sources=Sources(), sites_file=self.root / "sites.txt", log_dir=logs)
        rep = manual.collect(cfg, self.sites, self.tl)
        per = self.tl.per_key()
        self.assertEqual(per["Acme"][1], 30.0)
        self.assertEqual(per["? note: dentist"][1], 20.0)
        self.assertEqual(rep.summary, "Notes: 2 note(s), 50 min")
        self.assertEqual(len(rep.needs_naming), 1)


class ClaudeRepliesTest(ClaudeSessionsTest):
    def test_replies_become_evidence_for_the_client_of_the_prompt(self):
        long = "Restored the out-of-stock recommendations block on both product templates and merged it to main."
        self.session("D--PROJECTS-acme", r"D:\PROJECTS\acme", [
            (at(9, 0), "user", "please add the block"),
            (at(9, 5), "assistant", long),
            (at(9, 6), "user", "now the same on zeta"),
            (at(9, 9), "assistant", "ok"),  # too short to describe anything
        ])
        self.collect()
        kinds = [(k, text[:9]) for _, k, text in self.tl.evidence_for("Acme")]
        self.assertIn(("reply", "Restored "), kinds)
        self.assertFalse([e for e in self.tl.evidence_for("Zeta") if e[1] == "reply"])

    def test_initial_prompt_is_the_sessions_first_prompt_with_paste_kept(self):
        self.session("D--PROJECTS-acme", r"D:\PROJECTS\acme", [
            (at(9, 0) - timedelta(days=1), "user", "do this: <pasted_content id=\"x\">Client brief here</pasted_content>"),
            (at(9, 0), "user", "is it done"),
            (at(9, 5), "assistant", "yes"),
        ])
        self.collect()
        self.assertEqual(self.tl.initial_prompt("Acme"), "do this: Client brief here")


if __name__ == "__main__":
    unittest.main()

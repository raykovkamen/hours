import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from timesheet import push
from timesheet.allocate import BREAK, Row
from timesheet.clickup import Entry, Task
from timesheet.sites import Sites

DAY = date(2026, 10, 27)
TZ = timezone(timedelta(hours=2))


def at(h, m=0):
    return datetime(2026, 10, 27, h, m, tzinfo=TZ)


def row(h1, m1, h2, m2, key):
    return Row(at(h1, m1), at(h2, m2), key)


class FakeApi:
    def __init__(self, tasks=None, fail=()):
        self.tasks = tasks or {}
        self.fail = set(fail)
        self.posted = []

    def task(self, task_id):
        if task_id in self.fail:
            from timesheet.clickup import ClickUpError
            raise ClickUpError("HTTP 404")
        return self.tasks.get(task_id, Task(task_id, f"Task {task_id}", "open", "DEV", "Client"))

    def add_entry(self, task_id, start, minutes, description, billable):
        self.posted.append((task_id, start, minutes, description, billable))
        return f"entry-{len(self.posted)}"

    def shorten_entry(self, entry_id, start, minutes):
        self.__dict__.setdefault("changed", []).append(("shorten", entry_id, minutes))

    def delete_entry(self, entry_id):
        self.__dict__.setdefault("changed", []).append(("delete", entry_id))


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        p = Path(self.dir.name) / "sites.txt"
        p.write_text("acme = Acme | T1\nzeta = Zeta\n", encoding="utf-8")
        self.sites = Sites(p)

    def tearDown(self):
        self.dir.cleanup()

    def test_rows_are_summed_per_task_and_break_is_ignored(self):
        rows = [row(9, 0, 10, 0, "Acme"), row(10, 0, 11, 0, "Zeta"), row(12, 0, 13, 0, BREAK), row(13, 0, 13, 30, "Acme")]
        p = push.plan(DAY, rows, self.sites, [], {"Zeta": "T2"})
        self.assertTrue(p.ok)
        by = {a.task_id: a for a in p.actions}
        self.assertEqual(by["T1"].minutes, 90)
        self.assertEqual(by["T1"].start, at(9, 0))
        self.assertEqual(by["T2"].minutes, 60)

    def test_only_the_difference_to_logged_time_is_posted(self):
        rows = [row(9, 0, 10, 0, "Acme")]
        logged = [Entry("e1", "T1", "Task", at(8, 0), 45, "")]
        p = push.plan(DAY, rows, self.sites, logged, {})
        self.assertEqual(p.actions[0].minutes, 15)
        p = push.plan(DAY, rows, self.sites, [Entry("e1", "T1", "Task", at(8, 0), 90, "")], {})
        self.assertTrue(p.actions[0].skipped)
        self.assertEqual(p.to_post, [])

    def test_task_override_in_row_key_wins_over_default(self):
        p = push.plan(DAY, [row(9, 0, 10, 0, "Acme @T9")], self.sites, [], {})
        self.assertEqual(p.actions[0].task_id, "T9")

    def test_rows_without_task_or_client_block_the_push(self):
        rows = [row(9, 0, 10, 0, "Zeta"), row(10, 0, 11, 0, "? one-off: what"), row(11, 0, 12, 0, "Unknown")]
        p = push.plan(DAY, rows, self.sites, [], {})
        self.assertFalse(p.ok)
        self.assertEqual(len(p.blocked), 3)
        self.assertIn("no ClickUp task", p.blocked[0])
        self.assertEqual(p.actions, [])


class ExecuteTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        p = Path(self.dir.name) / "sites.txt"
        p.write_text("acme = Acme | T1\n", encoding="utf-8")
        self.sites = Sites(p)
        self.logs = Path(self.dir.name) / "logs"

    def tearDown(self):
        self.dir.cleanup()

    def test_dry_run_validates_and_logs_but_posts_nothing(self):
        api = FakeApi()
        p = push.plan(DAY, [row(9, 0, 10, 0, "Acme")], self.sites, [], {})
        push.execute(p, api, billable=False, dry_run=True, log_dir=self.logs)
        self.assertEqual(api.posted, [])
        self.assertEqual(p.actions[0].task.name, "Task T1")
        self.assertIn('"dry_run": true', (self.logs / "push-2026-10-27.jsonl").read_text(encoding="utf-8"))
        self.assertIn("Would post 1 entry", push.render(p, dry_run=True))

    def test_real_run_posts_and_records_entry_ids(self):
        api = FakeApi()
        p = push.plan(DAY, [row(9, 0, 10, 0, "Acme")], self.sites, [], {})
        push.execute(p, api, billable=True, dry_run=False, log_dir=self.logs)
        self.assertEqual(api.posted, [("T1", at(9, 0), 60, "hours: Acme", True)])
        self.assertEqual(p.actions[0].entry_id, "entry-1")
        self.assertIn("entry entry-1", push.render(p, dry_run=False))

    def test_unknown_task_blocks_before_anything_is_posted(self):
        api = FakeApi(fail={"T1"})
        p = push.plan(DAY, [row(9, 0, 10, 0, "Acme")], self.sites, [], {})
        push.execute(p, api, billable=False, dry_run=False, log_dir=self.logs)
        self.assertFalse(p.ok)
        self.assertEqual(api.posted, [])
        self.assertIn("Push blocked", push.render(p, dry_run=False))


class CreateApi(FakeApi):
    """Fake API with lists: find_task / create_task / user."""

    def __init__(self, existing=None, bad_lists=()):
        super().__init__()
        self.existing = existing or {}  # (list_id, name) -> task id
        self.bad_lists = set(bad_lists)
        self.created = []

    def user(self):
        return {"id": 42}

    def field_id(self, list_id, name):
        return "F1" if name.lower() == "initial prompt" else ""

    def create_task(self, list_id, name, status, assignee_id, custom_fields=None, tag=""):
        self.tags = getattr(self, "tags", []) + [tag]
        self.fields = getattr(self, "fields", []) + [custom_fields]
        if list_id in self.bad_lists:
            from timesheet.clickup import ClickUpError
            raise ClickUpError("HTTP 404 list not found")
        tid = f"new{len(self.created) + 1}"
        self.created.append((list_id, name, status, assignee_id))
        self.existing[(list_id, name)] = tid
        return Task(tid, name, status, "DEV", "Client")


class CreateModeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        p = Path(self.dir.name) / "sites.txt"
        p.write_text("acme = Acme | T1 list:L1\nzeta = Zeta\n", encoding="utf-8")
        self.sites = Sites(p)
        self.logs = Path(self.dir.name) / "logs"

    def tearDown(self):
        self.dir.cleanup()

    def remember(self, site, task_id):
        """Simulate an earlier real push that created `task_id` for `site` today."""
        self.logs.mkdir(exist_ok=True)
        with open(self.logs / "push-2026-10-27.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"mode": "create", "dry_run": False, "site": site, "task": task_id}) + "\n")

    def plan(self, rows, logged=(), assign=None, default_list="", name="{site} – {date}", titles=None, evidence=None):
        return push.plan(DAY, rows, self.sites, list(logged), assign or {}, "create", default_list, name,
                         titles, evidence)

    def test_list_from_sites_txt_and_default_list(self):
        self.assertEqual(self.sites.lists, {"Acme": "L1"})
        self.assertEqual(self.sites.tasks, {"Acme": "T1"})
        p = self.plan([row(9, 0, 10, 0, "Acme"), row(10, 0, 10, 30, "Zeta")], default_list="L0")
        self.assertTrue(p.ok)
        by = {a.site: a for a in p.actions}
        self.assertEqual((by["Acme"].list_id, by["Acme"].task_name), ("L1", "Acme – 2026-10-27"))
        self.assertEqual(by["Zeta"].list_id, "L0")

    def test_client_without_list_blocks(self):
        p = self.plan([row(9, 0, 10, 0, "Zeta")])
        self.assertFalse(p.ok)
        self.assertIn("no ClickUp list", p.blocked[0])
        self.assertTrue(self.plan([row(9, 0, 10, 0, "Zeta")], assign={"Zeta": "list:L9"}).ok)

    def test_creates_in_progress_task_assigned_to_me_and_logs_time(self):
        api = CreateApi()
        p = self.plan([row(9, 0, 10, 0, "Acme"), row(13, 0, 13, 30, "Acme")])
        push.execute(p, api, billable=False, dry_run=False, log_dir=self.logs, status="in progress")
        self.assertEqual(api.created, [("L1", "Acme – 2026-10-27", "in progress", 42)])
        self.assertEqual(api.posted, [("new1", at(9, 0), 90, "hours: Acme", False)])
        self.assertIn("Created 1 task", push.render(p, dry_run=False))

    def test_second_push_finds_the_task_and_adds_only_the_difference(self):
        api = CreateApi()
        self.remember("Acme", "D1")
        logged = [Entry("e1", "D1", "Acme – 2026-10-27", at(9, 0), 60, "hours: Acme")]
        p = self.plan([row(9, 0, 10, 30, "Acme")], logged)
        push.execute(p, api, billable=False, dry_run=False, log_dir=self.logs)
        self.assertEqual(api.created, [])
        self.assertEqual(api.posted, [("D1", at(9, 0), 30, "hours: Acme", False)])

    def test_dry_run_creates_nothing(self):
        api = CreateApi()
        p = self.plan([row(9, 0, 10, 0, "Acme")])
        push.execute(p, api, billable=False, dry_run=True, log_dir=self.logs)
        self.assertEqual((api.created, api.posted), ([], []))
        out = push.render(p, dry_run=True)
        self.assertIn("Would create 1 task", out)
        self.assertIn("new task in list L1", out)

    def test_title_from_commits_or_override(self):
        ev = {"Acme": [(at(9, 10), "prompt", "secret client text"), (at(9, 20), "commit", "Merge branch main"),
                       (at(9, 30), "commit", "Fix VAT field in checkout"), (at(10, 0), "commit", "Add tests")]}
        p = self.plan([row(9, 0, 10, 0, "Acme")], name="{title}", evidence=lambda s: ev.get(s, []))
        self.assertEqual(p.actions[0].task_name, "Fix VAT field in checkout (+1 more)")
        p = self.plan([row(9, 0, 10, 0, "Acme")], name="{title}", titles={"Acme": "Checkout VAT fixes"},
                      evidence=lambda s: ev.get(s, []))
        self.assertEqual(p.actions[0].task_name, "Checkout VAT fixes")

    def test_title_falls_back_to_the_date_without_commits(self):
        p = self.plan([row(9, 0, 10, 0, "Acme")], name="{title}")
        self.assertEqual(p.actions[0].task_name, "2026-10-27")

    def test_renamed_daily_task_is_found_through_the_push_log(self):
        api = CreateApi()
        self.remember("Acme", "D1")
        p = self.plan([row(9, 0, 10, 0, "Acme")], name="{title}", titles={"Acme": "A new title"})
        push.execute(p, api, billable=False, dry_run=False, log_dir=self.logs)
        self.assertEqual(api.created, [])
        self.assertEqual(api.posted[0][0], "D1")

    def test_task_deleted_in_clickup_is_created_again(self):
        api = CreateApi()
        api.fail = {"GONE"}
        self.remember("Acme", "GONE")
        p = self.plan([row(9, 0, 10, 0, "Acme")])
        push.execute(p, api, billable=False, dry_run=False, log_dir=self.logs)
        self.assertEqual(len(api.created), 1)
        self.assertEqual(api.posted[0][0], "new1")

    def test_dry_runs_in_the_log_are_not_mistaken_for_created_tasks(self):
        self.logs.mkdir()
        (self.logs / "push-2026-10-27.jsonl").write_text(json.dumps({"mode": "create", "dry_run": True, "site": "Acme", "task": "X"}) + "\n")
        self.assertEqual(push.known_task(self.logs, "Acme", DAY), "")

    def test_initial_prompt_fills_the_custom_field(self):
        api = CreateApi()
        p = push.plan(DAY, [row(9, 0, 10, 0, "Acme")], self.sites, [], {}, "create", "", "{title}",
                      {"Acme": "Checkout VAT"}, None, lambda s: "fix the VAT field\non checkout" if s == "Acme" else "")
        push.execute(p, api, billable=False, dry_run=False, log_dir=self.logs, prompt_field="Initial prompt")
        self.assertEqual(api.fields, [[{"id": "F1", "value": "fix the VAT field\non checkout"}]])
        self.assertIn("Initial prompt: fix the VAT field on checkout", push.render(p, dry_run=False))

    def test_no_field_when_list_lacks_it_or_feature_is_off(self):
        api = CreateApi()
        p = push.plan(DAY, [row(9, 0, 10, 0, "Acme")], self.sites, [], {}, "create", "", "{title}",
                      None, None, lambda s: "a prompt")
        push.execute(p, api, billable=False, dry_run=False, log_dir=self.logs, prompt_field="")
        self.assertEqual(api.fields, [None])
        self.assertNotIn("Initial prompt", push.render(p, dry_run=False))

    def test_existing_daily_task_is_not_touched(self):
        api = CreateApi()
        self.remember("Acme", "D1")
        p = push.plan(DAY, [row(9, 0, 10, 0, "Acme")], self.sites, [], {}, "create", "", "{title}",
                      None, None, lambda s: "a prompt")
        push.execute(p, api, billable=False, dry_run=False, log_dir=self.logs, prompt_field="Initial prompt")
        self.assertFalse(hasattr(api, "fields"))

    def test_bad_list_blocks_before_anything_is_written(self):
        api = CreateApi(bad_lists={"L1"})
        p = self.plan([row(9, 0, 10, 0, "Acme")])
        push.execute(p, api, billable=False, dry_run=False, log_dir=self.logs)
        self.assertFalse(p.ok)
        self.assertEqual((api.created, api.posted), ([], []))



class TagTest(unittest.TestCase):
    def test_created_tasks_carry_the_tag_and_existing_ones_are_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sites.txt"
            p.write_text("acme = Acme | list:L1\nzeta = Zeta | list:L2\n", encoding="utf-8")
            sites, logs = Sites(p), Path(d) / "logs"
            logs.mkdir()
            (logs / "push-2026-10-27.jsonl").write_text(
                json.dumps({"mode": "create", "dry_run": False, "site": "Zeta", "task": "Z1"}) + "\n", encoding="utf-8")
            api = CreateApi()
            plan = push.plan(DAY, [row(9, 0, 10, 0, "Acme"), row(10, 0, 11, 0, "Zeta")], sites, [], {}, "create", "", "{title}")
            push.execute(plan, api, billable=False, dry_run=False, log_dir=logs, tag="created by hours")
            self.assertEqual(api.tags, ["created by hours"])  # only the new Acme task; Zeta's existed
            self.assertIn('tagged "created by hours"', push.render(plan, dry_run=False))


class ReconcileTest(unittest.TestCase):
    """The push makes ClickUp match the table: it adds, shrinks its own entries, never touches hand-made ones."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        p = Path(self.dir.name) / "sites.txt"
        p.write_text("acme = Acme | T1 list:L1\n", encoding="utf-8")
        self.sites = Sites(p)
        self.logs = Path(self.dir.name) / "logs"

    def tearDown(self):
        self.dir.cleanup()

    def entry(self, eid, h, minutes, mine=True, task="T1"):
        return Entry(eid, task, "Task", at(h), minutes, "hours: Acme" if mine else "")

    def test_less_time_shrinks_our_newest_entries_first(self):
        logged = [self.entry("e1", 9, 60), self.entry("e2", 13, 30)]
        p = push.plan(DAY, [row(9, 0, 9, 45, "Acme")], self.sites, logged, {})
        a = p.actions[0]
        self.assertEqual((a.table_minutes, a.logged_minutes, a.minutes), (45, 90, -45))
        self.assertEqual([(e.id, new) for e, new in a.shrink], [("e2", 0), ("e1", 45)])
        self.assertEqual(a.by_hand_excess, 0)
        api = FakeApi()
        push.execute(p, api, billable=False, dry_run=False, log_dir=self.logs)
        self.assertEqual(api.posted, [])
        self.assertEqual(api.changed, [("delete", "e2"), ("shorten", "e1", 45)])
        out = push.render(p, dry_run=False)
        self.assertIn("Reduced", out)
        self.assertIn("-45 min", out)

    def test_hand_logged_time_is_never_changed(self):
        logged = [self.entry("mine", 9, 30), self.entry("hand", 10, 60, mine=False)]
        p = push.plan(DAY, [row(9, 0, 9, 30, "Acme")], self.sites, logged, {})
        a = p.actions[0]
        self.assertEqual([(e.id, new) for e, new in a.shrink], [("mine", 0)])
        self.assertEqual(a.by_hand_excess, 30)
        self.assertIn("Logged by hand beyond the table", push.render(p, dry_run=True))

    def test_dry_run_changes_nothing(self):
        p = push.plan(DAY, [row(9, 0, 9, 30, "Acme")], self.sites, [self.entry("e1", 9, 60)], {})
        api = FakeApi()
        push.execute(p, api, billable=False, dry_run=True, log_dir=self.logs)
        self.assertFalse(hasattr(api, "changed"))
        self.assertIn("Would reduce", push.render(p, dry_run=True))

    def test_equal_time_changes_nothing(self):
        p = push.plan(DAY, [row(9, 0, 10, 0, "Acme")], self.sites, [self.entry("e1", 9, 60)], {})
        api = FakeApi()
        push.execute(p, api, billable=False, dry_run=False, log_dir=self.logs)
        self.assertEqual((api.posted, getattr(api, "changed", [])), ([], []))
        self.assertIn("Already logged", push.render(p, dry_run=False))

    def test_create_mode_without_log_finds_the_task_from_todays_entries(self):
        api = CreateApi()
        api.tasks = {"D1": Task("D1", "Yesterday's name", "in progress", "DEV", "Client", "L1", datetime(2026, 10, 27, 9, tzinfo=TZ)),
                     "OLD": Task("OLD", "Old", "open", "DEV", "Client", "L1", datetime(2026, 9, 1, tzinfo=TZ)),
                     "ELSE": Task("ELSE", "Other list", "open", "DEV", "Client", "L9", datetime(2026, 10, 27, tzinfo=TZ))}
        logged = [self.entry("x1", 8, 10, task="OLD"), self.entry("x2", 8, 10, task="ELSE"), self.entry("e1", 9, 30, task="D1")]
        p = push.plan(DAY, [row(9, 0, 10, 0, "Acme")], self.sites, logged, {}, "create", "", "{title}")
        push.execute(p, api, billable=False, dry_run=False, log_dir=self.logs)  # no push log exists
        self.assertEqual(api.created, [])
        self.assertEqual(api.posted, [("D1", at(9, 0), 30, "hours: Acme", False)])


class RetryTest(unittest.TestCase):
    def test_rate_limit_and_server_errors_are_retried_then_succeed(self):
        import io
        import urllib.error
        from unittest import mock
        from timesheet.clickup import ClickUp
        calls, pauses = [], []

        class Ok:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"user": {"id": 1}}'

        def urlopen(req, timeout):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO(b""))
            if len(calls) == 2:
                raise urllib.error.HTTPError(req.full_url, 503, "Unavailable", {}, io.BytesIO(b""))
            return Ok()

        api = ClickUp("pk", "1", sleep=pauses.append)
        with mock.patch("urllib.request.urlopen", side_effect=urlopen):
            self.assertEqual(api.user(), {"id": 1})
        self.assertEqual(len(calls), 3)
        self.assertEqual(pauses, [2.0, 4.0])

    def test_client_errors_are_not_retried(self):
        import io
        import urllib.error
        from unittest import mock
        from timesheet.clickup import ClickUp, ClickUpError
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b"{}"))
        api = ClickUp("pk", "1", sleep=lambda s: self.fail("should not wait"))
        with mock.patch("urllib.request.urlopen", side_effect=err) as m:
            with self.assertRaises(ClickUpError):
                api.user()
        self.assertEqual(m.call_count, 1)


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

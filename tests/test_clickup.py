import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from timesheet.clickup import ClickUp, ClickUpError, to_ms

TZ = timezone(timedelta(hours=2))


class ClickUpClientTest(unittest.TestCase):
    def client(self, responses):
        api = ClickUp("pk_test", "")
        calls = []

        def fake(method, path, params=None, body=None):
            calls.append((method, path, params, body))
            return responses[path]
        api._call = fake
        api.calls = calls
        return api

    def test_missing_token_is_an_error(self):
        with self.assertRaises(ClickUpError):
            ClickUp("")

    def test_single_workspace_is_resolved_automatically(self):
        api = self.client({"team": {"teams": [{"id": "77", "name": "W"}]}})
        self.assertEqual(api.resolve_team(), "77")

    def test_several_workspaces_need_team_id(self):
        api = self.client({"team": {"teams": [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]}})
        with self.assertRaises(ClickUpError):
            api.resolve_team()

    def test_entries_are_parsed_in_local_time(self):
        start = datetime(2026, 10, 27, 9, 0, tzinfo=TZ)
        api = self.client({
            "team": {"teams": [{"id": "77", "name": "W"}]},
            "team/77/time_entries": {"data": [{"id": 5, "task": {"id": "T1", "name": "Fix"}, "start": str(to_ms(start)),
                                               "duration": str(90 * 60000), "description": "hours: Acme"}]},
        })
        entries = api.entries(start, start + timedelta(days=1))
        self.assertEqual(entries[0].task_id, "T1")
        self.assertEqual(entries[0].minutes, 90)
        self.assertEqual(entries[0].start, start)
        method, path, params, _ = api.calls[-1]
        self.assertEqual(params["start_date"], to_ms(start))

    def test_add_entry_posts_milliseconds(self):
        start = datetime(2026, 10, 27, 9, 0, tzinfo=TZ)
        api = self.client({"team": {"teams": [{"id": "77", "name": "W"}]}, "team/77/time_entries": {"data": {"id": "e9"}}})
        self.assertEqual(api.add_entry("T1", start, 45, "hours: Acme", False), "e9")
        _, _, _, body = api.calls[-1]
        self.assertEqual(body, {"tid": "T1", "start": to_ms(start), "duration": 45 * 60000, "description": "hours: Acme", "billable": False})

    def test_tagged_tasks_are_top_level_only_oldest_first_and_filtered_by_tag(self):
        api = self.client({"list/L1/task": {"tasks": [
            {"id": "B", "name": "Newer", "date_created": "1761600000000", "list": {"id": "L1"}},
            {"id": "S", "name": "Sub", "date_created": "1761500000000", "list": {"id": "L1"}, "parent": "A"},
            {"id": "A", "name": "Acme – hours", "date_created": "1761550000000", "list": {"id": "L1"}, "parent": None},
        ]}})
        tasks = api.tagged_tasks("L1", "created by hours")
        self.assertEqual([t.id for t in tasks], ["A", "B"])
        self.assertEqual(tasks[0].parent, "")
        _, _, params, _ = api.calls[-1]
        self.assertEqual(params["tags[]"], "created by hours")
        self.assertEqual(params["include_closed"], "true")

    def test_create_task_sends_the_parent(self):
        api = self.client({"list/L1/task": {"id": "N1"},
                           "task/N1": {"id": "N1", "name": "Daily", "list": {"id": "L1"}, "parent": "P1"}})
        t = api.create_task("L1", "Daily", "in progress", 42, None, "", "P1", "- did things")
        self.assertEqual(t.parent, "P1")
        _, _, _, body = api.calls[0]
        self.assertEqual(body["parent"], "P1")
        self.assertEqual(body["status"], "in progress")
        self.assertEqual(body["markdown_description"], "- did things")

    def test_set_description_updates_the_task(self):
        api = self.client({"task/N1": {"id": "N1"}})
        api.set_description("N1", "- summary")
        self.assertEqual(api.calls[-1], ("PUT", "task/N1", None, {"markdown_content": "- summary"}))

    def test_http_errors_become_clickup_errors_without_the_token(self):
        import io
        import urllib.error
        api = ClickUp("pk_secret")
        err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b'{"err":"Token invalid"}'))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(ClickUpError) as ctx:
                api.user()
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn("pk_secret", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

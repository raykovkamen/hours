import tempfile
import unittest
from pathlib import Path

from timesheet.sites import Sites, row_key, task_in


class SitesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "sites.txt"
        self.path.write_text(
            "# comment\n"
            "acme-b2b = Acme B2B | 86c111\n"
            "acme = Acme | 86c222   # default task\n"
            "acme.com = Acme\n"
            "zeta = Zeta\n",
            encoding="utf-8",
        )
        self.sites = Sites(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_first_match_wins_case_insensitively(self):
        self.assertEqual(self.sites.match("D--PROJECTS-ACME-B2B"), "Acme B2B")
        self.assertEqual(self.sites.match("Products · Acme.com · Shopify"), "Acme")
        self.assertIsNone(self.sites.match("something else"))

    def test_default_task_is_the_first_one_listed_for_the_site(self):
        self.assertEqual(self.sites.split_key("Acme"), ("Acme", "86c222"))
        self.assertEqual(self.sites.split_key("Acme @86c999"), ("Acme", "86c999"))
        self.assertEqual(self.sites.split_key("Zeta"), ("Zeta", ""))

    def test_auto_add_names_the_folder_and_appends_a_rule(self):
        self.assertEqual(self.sites.auto_add("what-the-pink"), "What The Pink")
        self.assertEqual(self.sites.auto_add("blanca-migration"), "Blanca")
        self.assertEqual(self.sites.match("what-the-pink"), "What The Pink")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("what-the-pink = What The Pink", text)
        self.assertIn("auto-added", text)

    def test_falls_back_to_example_without_writing(self):
        example = Path(self.dir.name) / "sites.example.txt"
        example.write_text("demo = Demo\n", encoding="utf-8")
        missing = Path(self.dir.name) / "none.txt"
        sites = Sites(missing, example)
        self.assertTrue(sites.using_example)
        self.assertEqual(sites.match("demo-shop"), "Demo")
        sites.auto_add("new-client")
        self.assertFalse(missing.exists())


class TaskTest(unittest.TestCase):
    def test_task_from_link_or_prefix(self):
        self.assertEqual(task_in("see https://app.clickup.com/t/86cabc123 please"), "86cabc123")
        self.assertEqual(task_in("https://app.clickup.com/t/6649928/123xf86w832"), "123xf86w832")
        self.assertEqual(task_in("log it to task:86cxyz"), "86cxyz")
        self.assertIsNone(task_in("no task here"))

    def test_row_key(self):
        self.assertEqual(row_key("Acme", "86c1"), "Acme @86c1")
        self.assertEqual(row_key("Acme", None), "Acme")
        self.assertEqual(row_key("? ERP", "86c1"), "? ERP")
        self.assertIsNone(row_key(None, "86c1"))


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from timesheet import names
from timesheet.timeline import Timeline

DAY = date(2026, 10, 28)
TZ = timezone(timedelta(hours=2))


def at(h, m=0):
    return datetime(2026, 10, 28, h, m, tzinfo=TZ)


class NamesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.logs = Path(self.dir.name)
        self.tl = Timeline(DAY, TZ)

    def tearDown(self):
        self.dir.cleanup()

    def test_parse_span(self):
        self.assertEqual(names.parse_span("11:00"), (660, None))
        self.assertEqual(names.parse_span("9:30-10:15"), (570, 615))
        for bad in ("11", "25:00", "11:00-10:00", "11:00-"):
            with self.assertRaises(ValueError):
                names.parse_span(bad)

    def test_add_refuses_unnamed_client(self):
        with self.assertRaises(ValueError):
            names.add(self.logs, DAY, "11:00", "? meeting")

    def test_names_the_whole_meeting_from_one_minute(self):
        self.tl.mark(at(11), at(11, 30), "? meeting: Busy", "calendar")
        self.tl.note("? meeting: Busy", at(11), "meeting", "Busy")
        names.add(self.logs, DAY, "11:10", "Shelly")
        report = names.apply(self.logs, self.tl)
        per = self.tl.per_key()
        self.assertEqual(per["Shelly"], (660, 30.0))
        self.assertNotIn("? meeting: Busy", per)
        self.assertEqual([e[2] for e in self.tl.evidence_for("Shelly")], ["Busy"])
        self.assertIn("11:00-11:30", report.details[0])

    def test_same_title_meetings_are_named_separately(self):
        self.tl.mark(at(11), at(11, 30), "? meeting: Busy", "calendar")
        self.tl.mark(at(14), at(15), "? meeting: Busy", "calendar")
        names.add(self.logs, DAY, "14:00", "Josera")
        names.apply(self.logs, self.tl)
        per = self.tl.per_key()
        self.assertEqual(per["Josera"], (840, 60.0))
        self.assertEqual(per["? meeting: Busy"], (660, 30.0))

    def test_span_splits_back_to_back_meetings(self):
        self.tl.mark(at(11), at(12), "? meeting: Busy", "calendar")
        names.add(self.logs, DAY, "11:00-11:30", "Shelly")
        names.add(self.logs, DAY, "11:30-12:00", "Josera")
        names.apply(self.logs, self.tl)
        per = self.tl.per_key()
        self.assertEqual(per["Shelly"][1], 30.0)
        self.assertEqual(per["Josera"][1], 30.0)
        self.assertNotIn("? meeting: Busy", per)

    def test_shared_minutes_keep_the_named_client(self):
        self.tl.mark(at(11), at(11, 30), "? meeting: Busy", "calendar")
        self.tl.mark(at(11), at(11, 30), "Skinshop", "activitywatch")
        names.add(self.logs, DAY, "11:00", "Shelly")
        names.apply(self.logs, self.tl)
        per = self.tl.per_key()
        self.assertEqual(per["Shelly"][1], 15.0)
        self.assertEqual(per["Skinshop"][1], 15.0)

    def test_named_rows_lead_unplaced_minutes(self):
        self.tl.mark(at(10, 39), at(10, 47), "? MIGRATION", "claude")
        self.tl.mark(at(10, 47), at(10, 55), None, "activitywatch")
        names.add(self.logs, DAY, "10:40", "Blanca Sport")
        names.apply(self.logs, self.tl)
        self.tl.resolve_unplaced(30)
        self.assertEqual(self.tl.per_key()["Blanca Sport"][1], 16.0)

    def test_no_row_at_that_time_changes_nothing(self):
        self.tl.mark(at(11), at(11, 30), "Skinshop", "activitywatch")
        names.add(self.logs, DAY, "11:00", "Shelly")
        report = names.apply(self.logs, self.tl)
        self.assertNotIn("Shelly", self.tl.per_key())
        self.assertIn("nothing changed", report.details[0])

    def test_no_file_is_fine(self):
        self.assertEqual(names.apply(self.logs, self.tl).details, [])


if __name__ == "__main__":
    unittest.main()

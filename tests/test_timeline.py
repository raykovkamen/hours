import unittest
from datetime import date, datetime, time, timedelta, timezone

from timesheet.sites import UNKNOWN
from timesheet.timeline import Timeline

DAY = date(2026, 10, 27)
TZ = timezone(timedelta(hours=2))


def at(h, m=0):
    return datetime(2026, 10, 27, h, m, tzinfo=TZ)


class TimelineTest(unittest.TestCase):
    def test_mark_claims_minutes_and_counts_source_once(self):
        tl = Timeline(DAY, TZ)
        tl.mark(at(9, 0), at(9, 10), "A", "claude")
        tl.mark(at(9, 5), at(9, 15), "B", "git")
        self.assertEqual(tl.active_minutes(), 15)
        self.assertEqual(tl.by_source, {"claude": 10, "git": 5})
        self.assertEqual(tl.claims[9 * 60 + 7], {"A", "B"})

    def test_sub_minute_span_claims_one_minute(self):
        tl = Timeline(DAY, TZ)
        tl.mark(at(9, 0), at(9, 0) + timedelta(seconds=20), "A", "claude")
        self.assertEqual(tl.active_minutes(), 1)

    def test_shared_minutes_split_between_clients(self):
        tl = Timeline(DAY, TZ)
        tl.mark(at(9, 0), at(9, 10), "A", "claude")
        tl.mark(at(9, 0), at(9, 10), "B", "git")
        per = tl.per_key()
        self.assertAlmostEqual(per["A"][1], 5.0)
        self.assertAlmostEqual(per["B"][1], 5.0)
        self.assertEqual(per["A"][0], 9 * 60)

    def test_unplaced_minutes_inherit_nearest_client_within_carry(self):
        tl = Timeline(DAY, TZ)
        tl.mark(at(9, 0), at(9, 10), "A", "claude")
        tl.mark(at(9, 10), at(9, 30), None, "activitywatch")  # browser work, no title matched
        tl.resolve_unplaced(carry_minutes=30)
        self.assertEqual(tl.per_key()["A"][1], 30.0)

    def test_unplaced_minutes_far_from_any_client_are_unknown(self):
        tl = Timeline(DAY, TZ)
        tl.mark(at(9, 0), at(9, 10), "A", "claude")
        tl.mark(at(11, 0), at(11, 20), None, "activitywatch")
        tl.resolve_unplaced(carry_minutes=30)
        per = tl.per_key()
        self.assertEqual(per["A"][1], 10.0)
        self.assertEqual(per[UNKNOWN][1], 20.0)

    def test_utc_timestamps_are_converted_to_the_local_day(self):
        tl = Timeline(DAY, TZ)
        tl.mark(datetime(2026, 10, 27, 7, 0, tzinfo=timezone.utc), datetime(2026, 10, 27, 7, 5, tzinfo=timezone.utc), "A", "x")
        self.assertIn(9 * 60, tl.active)

    def test_longest_pause_inside_window(self):
        tl = Timeline(DAY, TZ)
        tl.mark(at(9, 0), at(12, 10), "A", "x")
        tl.mark(at(12, 50), at(15, 0), "A", "x")
        self.assertEqual(tl.longest_pause((time(11), time(15)), 30), (12 * 60 + 10, 40))
        self.assertIsNone(tl.longest_pause((time(11), time(15)), 60))

    def test_end_of_day_is_not_a_break(self):
        tl = Timeline(DAY, TZ)
        tl.mark(at(9, 0), at(14, 0), "A", "x")
        self.assertIsNone(tl.longest_pause((time(11), time(15)), 30))

    def test_late_start_is_not_a_break(self):
        tl = Timeline(DAY, TZ)
        tl.mark(at(13, 0), at(17, 0), "A", "x")
        self.assertIsNone(tl.longest_pause((time(11), time(15)), 30))

    def test_pause_running_past_window_end_counts_in_full(self):
        tl = Timeline(DAY, TZ)
        tl.mark(at(9, 0), at(14, 30), "A", "x")
        tl.mark(at(15, 45), at(18, 0), "A", "x")
        self.assertEqual(tl.longest_pause((time(11), time(15)), 30), (14 * 60 + 30, 75))


if __name__ == "__main__":
    unittest.main()

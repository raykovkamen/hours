import unittest
from datetime import date, time

from timesheet.allocate import BREAK, allocate, break_time, day_slots, schedule
from timesheet.config import Workday

DAY = date(2026, 10, 27)


class DaySlotsTest(unittest.TestCase):
    def test_real_time_rounded_to_step_when_unbounded(self):
        self.assertEqual(day_slots(254, Workday()), 17)  # 4h14 -> 4h15
        self.assertEqual(day_slots(0, Workday()), 0)

    def test_bounds_pad_and_cap(self):
        w = Workday(min_hours=6, max_hours=8)
        self.assertEqual(day_slots(254, w), 24)
        self.assertEqual(day_slots(9 * 60, w), 32)
        self.assertEqual(day_slots(7 * 60, w), 28)


class AllocateTest(unittest.TestCase):
    def test_every_client_gets_one_slot_and_the_rest_follows_time(self):
        per = {"A": (0, 90.0), "B": (10, 30.0), "C": (20, 1.0)}
        self.assertEqual(allocate(per, 8), [("A", 5), ("B", 2), ("C", 1)])

    def test_largest_remainder_breaks_ties_in_favour_of_bigger_fraction(self):
        per = {"A": (0, 10.0), "B": (1, 10.0), "C": (2, 5.0)}
        self.assertEqual(sum(u for _, u in allocate(per, 7)), 7)

    def test_clients_ordered_by_first_activity(self):
        per = {"Late": (300, 60.0), "Early": (10, 10.0)}
        self.assertEqual([k for k, _ in allocate(per, 4)], ["Early", "Late"])

    def test_clients_under_a_minute_are_dropped(self):
        per = {"A": (0, 60.0), "Noise": (5, 0.5)}
        self.assertEqual(allocate(per, 4), [("A", 4)])

    def test_more_clients_than_slots_keeps_the_earliest(self):
        per = {"A": (0, 5.0), "B": (1, 5.0), "C": (2, 5.0)}
        self.assertEqual(allocate(per, 2), [("A", 1), ("B", 1)])


class ScheduleTest(unittest.TestCase):
    def test_rows_are_contiguous_and_break_splits_a_client(self):
        rows = schedule(DAY, [("A", 2), ("B", 12), ("C", 2)], Workday(), time(12, 0))
        keys = [(r.start.strftime("%H:%M"), r.end.strftime("%H:%M"), r.key) for r in rows]
        self.assertEqual(keys, [
            ("09:00", "09:30", "A"),
            ("09:30", "12:00", "B"),
            ("12:00", "13:00", BREAK),
            ("13:00", "13:30", "B"),
            ("13:30", "14:00", "C"),
        ])
        self.assertEqual(sum(r.minutes for r in rows if r.key != BREAK), 16 * 15)

    def test_break_exactly_at_a_row_boundary(self):
        rows = schedule(DAY, [("A", 4), ("B", 4)], Workday(), time(10, 0))
        self.assertEqual([r.key for r in rows], ["A", BREAK, "B"])


class BreakTimeTest(unittest.TestCase):
    def test_default_when_no_pause(self):
        self.assertEqual(break_time(None, Workday()), time(12, 0))

    def test_pause_snaps_to_step_and_never_runs_past_window(self):
        self.assertEqual(break_time((12 * 60 + 38, 45), Workday()), time(12, 45))
        self.assertEqual(break_time((14 * 60 + 50, 30), Workday()), time(14, 0))


if __name__ == "__main__":
    unittest.main()

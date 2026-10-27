import unittest
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from timesheet.timeline import Timeline, local_midnight

SOFIA = ZoneInfo("Europe/Sofia")  # EEST (+3) until 25.10.2026 04:00, EET (+2) after


class ClockChangeTest(unittest.TestCase):
    def test_same_utc_hour_lands_on_the_right_local_minute_either_side_of_the_switch(self):
        summer = Timeline(date(2026, 10, 24), SOFIA)
        winter = Timeline(date(2026, 10, 27), SOFIA)
        self.assertEqual(summer.minute(datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)), 10 * 60)
        self.assertEqual(winter.minute(datetime(2026, 10, 27, 7, 0, tzinfo=timezone.utc)), 9 * 60)

    def test_day_boundaries_use_each_days_own_offset(self):
        self.assertEqual(local_midnight(date(2026, 10, 24), SOFIA).utcoffset().total_seconds(), 3 * 3600)
        self.assertEqual(local_midnight(date(2026, 10, 27), SOFIA).utcoffset().total_seconds(), 2 * 3600)
        switch = Timeline(date(2026, 10, 25), SOFIA)
        utc = timezone.utc  # same-tzinfo subtraction is wall-clock in Python; compare real instants
        self.assertEqual((switch.day_end.astimezone(utc) - switch.day_start.astimezone(utc)).total_seconds(), 25 * 3600)

    def test_naive_times_are_wall_clock(self):
        tl = Timeline(date(2026, 10, 24), SOFIA)
        self.assertEqual(tl.minute(datetime(2026, 10, 24, 14, 30)), 14 * 60 + 30)

    def test_minutes_on_the_switch_day_follow_the_wall_clock(self):
        tl = Timeline(date(2026, 10, 25), SOFIA)
        # 02:30 EEST (23:30Z the day before) and 09:00 EET (07:00Z)
        self.assertEqual(tl.minute(datetime(2026, 10, 24, 23, 30, tzinfo=timezone.utc)), 2 * 60 + 30)
        self.assertEqual(tl.minute(datetime(2026, 10, 25, 7, 0, tzinfo=timezone.utc)), 9 * 60)

    def test_system_local_mode(self):
        tl = Timeline(date(2026, 10, 24))
        local = datetime.combine(date(2026, 10, 24), time(11, 15)).astimezone()
        self.assertEqual(tl.minute(local.astimezone(timezone.utc)), 11 * 60 + 15)


if __name__ == "__main__":
    unittest.main()

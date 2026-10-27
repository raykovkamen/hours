import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from timesheet.config import Config, Sources
from timesheet.sites import Sites
from timesheet.sources import calendar_ics
from timesheet.timeline import Timeline

TZ = timezone(timedelta(hours=2))
ME = {"me@example.com"}


def ics(*events: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(events) + "END:VCALENDAR\r\n"


def vevent(*lines: str) -> str:
    return "BEGIN:VEVENT\r\n" + "".join(l + "\r\n" for l in lines) + "END:VEVENT\r\n"


def on(text: str, day: date):
    return [(s.strftime("%H:%M"), e.strftime("%H:%M"), ev.summary)
            for s, e, ev in calendar_ics.meetings(calendar_ics.parse(text, TZ, ME), day, TZ)]


class ParseTest(unittest.TestCase):
    def test_single_event_utc_and_folded_summary(self):
        text = ics(vevent("UID:1", "DTSTART:20261027T080000Z", "DTEND:20261027T083000Z",
                          "SUMMARY:Josera weekly", " sync"))
        self.assertEqual(on(text, date(2026, 10, 27)), [("10:00", "10:30", "Josera weeklysync")])
        self.assertEqual(on(text, date(2026, 10, 28)), [])

    def test_tzid_and_duration(self):
        text = ics(vevent("UID:2", "DTSTART;TZID=Europe/Sofia:20261027T140000", "DURATION:PT45M", "SUMMARY:Call"))
        self.assertEqual(on(text, date(2026, 10, 27)), [("14:00", "14:45", "Call")])

    def test_skipped_kinds(self):
        text = ics(
            vevent("UID:a", "DTSTART;VALUE=DATE:20261027", "DTEND;VALUE=DATE:20261028", "SUMMARY:Holiday"),
            vevent("UID:b", "DTSTART:20261027T080000Z", "DTEND:20261027T090000Z", "TRANSP:TRANSPARENT", "SUMMARY:Focus"),
            vevent("UID:c", "DTSTART:20261027T080000Z", "DTEND:20261027T090000Z", "STATUS:CANCELLED", "SUMMARY:Gone"),
            vevent("UID:d", "DTSTART:20261027T080000Z", "DTEND:20261027T090000Z", "SUMMARY:Declined",
                   "ATTENDEE;PARTSTAT=DECLINED:mailto:me@example.com"),
            vevent("UID:e", "DTSTART:20261027T080000Z", "DTEND:20261027T090000Z", "SUMMARY:Kept",
                   "ATTENDEE;PARTSTAT=DECLINED:mailto:someone@else.com"),
        )
        self.assertEqual([m[2] for m in on(text, date(2026, 10, 27))], ["Kept"])


class RecurrenceTest(unittest.TestCase):
    def test_weekly_byday_with_exdate_and_until(self):
        text = ics(vevent("UID:w", "DTSTART;TZID=Europe/Sofia:20261005T093000", "DTEND;TZID=Europe/Sofia:20261005T094500",
                          "RRULE:FREQ=WEEKLY;BYDAY=MO,TU;UNTIL=20261031T000000Z", "EXDATE;TZID=Europe/Sofia:20261020T093000",
                          "SUMMARY:Standup"))
        self.assertEqual(on(text, date(2026, 10, 27)), [("09:30", "09:45", "Standup")])  # a Tuesday
        self.assertEqual(on(text, date(2026, 10, 20)), [])  # excluded
        self.assertEqual(on(text, date(2026, 10, 28)), [])  # Wednesday
        self.assertEqual(on(text, date(2026, 11, 2)), [])  # after UNTIL

    def test_every_other_week_and_count(self):
        text = ics(vevent("UID:x", "DTSTART:20261005T100000Z", "DTEND:20261005T110000Z",
                          "RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=2", "SUMMARY:Review"))
        self.assertTrue(on(text, date(2026, 10, 19)))
        self.assertFalse(on(text, date(2026, 10, 12)))
        self.assertFalse(on(text, date(2026, 11, 2)))  # third occurrence, past COUNT

    def test_daily_and_monthly_nth_weekday(self):
        daily = ics(vevent("UID:d", "DTSTART:20261001T070000Z", "DTEND:20261001T071500Z", "RRULE:FREQ=DAILY", "SUMMARY:D"))
        self.assertTrue(on(daily, date(2026, 10, 27)))
        last_fri = ics(vevent("UID:m", "DTSTART:20260130T120000Z", "DTEND:20260130T130000Z",
                              "RRULE:FREQ=MONTHLY;BYDAY=-1FR", "SUMMARY:Retro"))
        self.assertTrue(on(last_fri, date(2026, 10, 30)))
        self.assertFalse(on(last_fri, date(2026, 10, 23)))

    def test_moved_single_occurrence_replaces_the_series(self):
        text = ics(
            vevent("UID:s", "DTSTART:20261005T080000Z", "DTEND:20261005T083000Z", "RRULE:FREQ=WEEKLY", "SUMMARY:Sync"),
            vevent("UID:s", "RECURRENCE-ID:20261026T080000Z", "DTSTART:20261026T120000Z", "DTEND:20261026T123000Z",
                   "SUMMARY:Sync (moved)"),
        )
        self.assertEqual(on(text, date(2026, 10, 26)), [("14:00", "14:30", "Sync (moved)")])
        self.assertEqual(on(text, date(2026, 10, 19)), [("10:00", "10:30", "Sync")])


class CollectTest(unittest.TestCase):
    def test_meetings_mark_time_and_unnamed_meetings_are_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sites.txt").write_text("josera = Josera\n", encoding="utf-8")
            cal = root / "cal.ics"
            cal.write_text(ics(
                vevent("UID:1", "DTSTART:20261027T080000Z", "DTEND:20261027T083000Z", "SUMMARY:Josera weekly"),
                vevent("UID:2", "DTSTART:20261027T120000Z", "DTEND:20261027T121500Z", "SUMMARY:Dentist"),
            ), encoding="utf-8")
            tl = Timeline(date(2026, 10, 27), TZ)
            cfg = Config(sources=Sources(calendar_ics=str(cal)), sites_file=root / "sites.txt")
            rep = calendar_ics.collect(cfg, Sites(root / "sites.txt"), tl)
            per = tl.per_key()
            self.assertEqual(per["Josera"][1], 30.0)
            self.assertEqual(per["? meeting: Dentist"][1], 15.0)
            self.assertEqual(rep.summary, "Calendar: 2 meeting(s), 45 min")
            self.assertEqual(len(rep.needs_naming), 1)

    def test_not_configured_or_unreadable(self):
        tl = Timeline(date(2026, 10, 27), TZ)
        self.assertFalse(calendar_ics.collect(Config(), Sites(Path("none")), tl).available)
        cfg = Config(sources=Sources(calendar_ics="/no/such/file.ics"))
        self.assertIn("could not be read", calendar_ics.collect(cfg, Sites(Path("none")), tl).summary)


if __name__ == "__main__":
    unittest.main()

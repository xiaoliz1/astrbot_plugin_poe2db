"""Beijing-time refresh schedule tests."""

import sys
import unittest
from datetime import datetime, time, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.price_schedule import (
    BEIJING_TIMEZONE,
    next_price_refresh_at,
    normalize_utc_offset_hours,
    parse_daily_refresh_times,
    timezone_for_utc_offset,
)


class PriceScheduleTests(unittest.TestCase):
    def test_refresh_slots_are_six_hours_apart_in_beijing_time(self):
        current = datetime(2026, 10, 13, 5, 59, tzinfo=BEIJING_TIMEZONE)
        self.assertEqual(
            next_price_refresh_at(current),
            datetime(2026, 10, 13, 6, 0, tzinfo=BEIJING_TIMEZONE),
        )
        self.assertEqual(
            next_price_refresh_at(
                datetime(2026, 10, 13, 6, 0, tzinfo=BEIJING_TIMEZONE)
            ),
            datetime(2026, 10, 13, 12, 0, tzinfo=BEIJING_TIMEZONE),
        )

    def test_midnight_is_the_next_day_after_18(self):
        self.assertEqual(
            next_price_refresh_at(
                datetime(2026, 10, 13, 18, 0, tzinfo=BEIJING_TIMEZONE)
            ),
            datetime(2026, 10, 14, 0, 0, tzinfo=BEIJING_TIMEZONE),
        )

    def test_naive_datetimes_are_treated_as_beijing_time(self):
        result = next_price_refresh_at(datetime(2026, 10, 13, 23, 59))
        self.assertEqual(result, datetime(2026, 10, 14, 0, 0, tzinfo=BEIJING_TIMEZONE))

    def test_utc_input_converts_to_beijing_slots(self):
        current = datetime(2026, 10, 12, 22, 30, tzinfo=timezone.utc)
        self.assertEqual(
            next_price_refresh_at(current),
            datetime(2026, 10, 13, 12, 0, tzinfo=BEIJING_TIMEZONE),
        )

    def test_custom_times_accept_comma_newline_and_24_hour_midnight(self):
        self.assertEqual(
            parse_daily_refresh_times("06:00，12:30\n24:00"),
            (time(0, 0), time(6, 0), time(12, 30)),
        )
        self.assertEqual(
            parse_daily_refresh_times((time(9, 15),)),
            (time(9, 15),),
        )

    def test_custom_timezone_and_schedule_are_applied(self):
        current = datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)
        result = next_price_refresh_at(
            current, parse_daily_refresh_times("12:00"), 5.5
        )
        expected = datetime(
            2026, 10, 13, 12, 0, tzinfo=timezone_for_utc_offset(5.5)
        )
        self.assertEqual(result, expected)

    def test_invalid_refresh_times_and_offsets_fall_back_safely(self):
        defaults = parse_daily_refresh_times("00:00,06:00,12:00,18:00")
        self.assertEqual(parse_daily_refresh_times("bad"), defaults)
        self.assertEqual(normalize_utc_offset_hours("nan"), 8.0)
        self.assertEqual(normalize_utc_offset_hours(99), 14.0)


if __name__ == "__main__":
    unittest.main()

"""Beijing-time refresh schedule tests."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.price_schedule import (
    BEIJING_TIMEZONE,
    next_price_refresh_at,
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


if __name__ == "__main__":
    unittest.main()

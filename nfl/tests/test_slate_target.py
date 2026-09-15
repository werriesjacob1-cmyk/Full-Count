#!/usr/bin/env python3
"""Contracts for live NFL slate-date targeting."""
from datetime import date, datetime, timezone
import unittest

from nfl.prospective import slate_target


class SlateTargetTests(unittest.TestCase):
    def test_manual_monday_is_preserved(self):
        self.assertEqual(
            slate_target.resolve_target_local_date("2026-09-14"),
            "2026-09-14",
        )

    def test_blank_monday_falls_forward_to_sunday(self):
        self.assertEqual(
            slate_target.resolve_target_local_date("", reference=date(2026, 9, 14)),
            "2026-09-20",
        )

    def test_blank_sunday_keeps_same_sunday(self):
        self.assertEqual(
            slate_target.resolve_target_local_date(None, reference=date(2026, 9, 20)),
            "2026-09-20",
        )

    def test_timezone_aware_reference_uses_chicago_calendar(self):
        # 2026-09-14 04:30 UTC is still Sunday night in Chicago.
        reference = datetime(2026, 9, 14, 4, 30, tzinfo=timezone.utc)
        self.assertEqual(
            slate_target.resolve_target_local_date(None, reference=reference),
            "2026-09-13",
        )

    def test_non_iso_dates_fail_closed(self):
        for value in ("09/14/2026", "2026-9-14", "2026-09-14T00:00:00", "garbage"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "strict YYYY-MM-DD"):
                    slate_target.validate_target_local_date(value)

    def test_naive_datetime_is_refused(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            slate_target.next_sunday_local(datetime(2026, 9, 14, 12, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)

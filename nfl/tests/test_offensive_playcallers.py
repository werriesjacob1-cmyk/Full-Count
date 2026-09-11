#!/usr/bin/env python3
"""Contract for curated actual offensive play-caller evidence.

Coordinator title is not play-calling duty. The first curated snapshot is a
research evidence artifact from ESPN/NFL Nation's all-32 2026 play-caller
survey. It must remain explicitly non-PIT-admissible until FULL COUNT has a
real observed-at capture and, where necessary, an effective-time source.
"""
import unittest

from nfl.research import offensive_playcallers as pc


NFLVERSE_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
    "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
    "LV", "LAC", "LAR", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS",
}


class OffensivePlayCallerSnapshot(unittest.TestCase):
    def test_2026_snapshot_covers_all_32_teams_once(self):
        rows = pc.snapshot_for_season(2026)
        self.assertEqual(len(rows), 32)
        self.assertEqual({row["team"] for row in rows}, NFLVERSE_TEAMS)
        self.assertEqual(len({row["team"] for row in rows}), 32)

    def test_snapshot_records_actual_duty_not_coordinator_title(self):
        rows = {row["team"]: row for row in pc.snapshot_for_season(2026)}
        self.assertEqual(rows["BUF"]["play_caller"], "Joe Brady")
        self.assertEqual(rows["BUF"]["caller_role"], "head_coach")
        self.assertEqual(rows["DEN"]["play_caller"], "Davis Webb")
        self.assertEqual(rows["DEN"]["caller_role"], "offensive_coordinator")
        self.assertEqual(rows["CIN"]["caller_role"], "head_coach")
        self.assertEqual(rows["PHI"]["caller_role"], "offensive_coordinator")

    def test_source_and_time_semantics_fail_closed_for_model_use(self):
        rows = pc.snapshot_for_season(2026)
        for row in rows:
            self.assertEqual(row["phase"], "offense")
            self.assertEqual(row["source_id"], "espn_nfl_nation_2026_playcallers")
            self.assertEqual(row["source_published_at"], "2026-09-03T10:00:00Z")
            self.assertIsNone(row["observed_at"])
            self.assertIsNone(row["effective_at"])
            self.assertFalse(row["pit_admissible"])
            self.assertIn("not preserved", row["pit_block_reason"].lower())

    def test_source_metadata_never_claims_publication_time_is_effective_time(self):
        source = pc.SOURCES["espn_nfl_nation_2026_playcallers"]
        self.assertEqual(source["publication_time_semantics"], "source_published_at")
        self.assertIn("not", source["effective_time_caveat"].lower())
        self.assertIn("appointment", source["effective_time_caveat"].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)

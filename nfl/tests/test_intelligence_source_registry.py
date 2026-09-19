import json
import unittest
from copy import deepcopy
from pathlib import Path

from nfl.intelligence.source_registry import NFLSourceRegistryError, validate_source_registry

REGISTRY=Path("data/nfl_intelligence/source_registry.json")


def load_sources():
    with REGISTRY.open("r",encoding="utf-8") as handle:
        return json.load(handle)


def by_id(payload):
    return {row["source_id"]:row for row in payload["sources"]}


class NFLSourceRegistryTests(unittest.TestCase):
    def test_registry_valid_and_contains_key_sources(self):
        payload=load_sources()
        summary=validate_source_registry(payload)
        self.assertGreaterEqual(summary["source_count"],26)
        required={
            "NFLVERSE_WEEKLY_STATS",
            "NFLFASTR_PLAY_BY_PLAY",
            "NFLVERSE_SNAP_COUNTS",
            "NFLVERSE_PARTICIPATION",
            "NFLVERSE_NEXTGEN_WEEKLY",
            "NFLVERSE_PFR_ADVSTATS",
            "NFLVERSE_WEEKLY_ROSTERS",
            "DEPTH_CHARTS",
            "NFLVERSE_INJURY_REPORTS",
            "OFFICIAL_NFL_INACTIVES",
            "OFFICIAL_ASSIGNMENTS_AND_PENALTIES",
            "COACH_PLAYCALLER_HISTORY",
            "FANDUEL_NFL",
        }
        self.assertTrue(required.issubset(set(summary["source_ids"])))

    def test_2023_plus_participation_is_not_live_source(self):
        row=by_id(load_sources())["NFLVERSE_PARTICIPATION"]
        self.assertIn("NOT suitable",row["current_season_policy"])
        self.assertIn("HISTORICAL_ONLY",row["point_in_time_class"])

    def test_injury_source_is_not_treated_as_live_2026(self):
        row=by_id(load_sources())["NFLVERSE_INJURY_REPORTS"]
        self.assertEqual(row["status"],"REAUDIT_REQUIRED")
        self.assertIn("DO NOT use as 2026 live injury source",row["current_season_policy"])

    def test_depth_chart_schema_break_is_preserved(self):
        row=by_id(load_sources())["DEPTH_CHARTS"]
        self.assertIn("2001",row["historical_coverage_claim"])
        self.assertIn("2025",row["historical_coverage_claim"])
        self.assertIn("timestamp",row["historical_coverage_claim"].lower())
        self.assertIn("2001_2024",row["point_in_time_class"])
        self.assertIn("2025_PLUS",row["point_in_time_class"])

    def test_snap_counts_are_prior_history_not_upcoming_starters(self):
        row=by_id(load_sources())["NFLVERSE_SNAP_COUNTS"]
        self.assertTrue(any("upcoming starters" in x for x in row["restrictions"]))

    def test_nextgen_missing_rows_are_not_zero(self):
        row=by_id(load_sources())["NFLVERSE_NEXTGEN_WEEKLY"]
        self.assertTrue(any("missing players" in x.lower() for x in row["restrictions"]))

    def test_duplicate_source_fails(self):
        payload=load_sources()
        payload["sources"].append(deepcopy(payload["sources"][0]))
        with self.assertRaisesRegex(NFLSourceRegistryError,"duplicate source_id"):
            validate_source_registry(payload)


if __name__=="__main__":
    unittest.main(verbosity=2)

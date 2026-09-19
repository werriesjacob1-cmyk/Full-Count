#!/usr/bin/env python3
"""Guards the honesty of this workstream's registry coverage claim.

NFL-GENIUS-NEWS-CLAIM-LEDGER-20260919 populated exactly two teams (BUF, DET)
from one real live official_nfl.capture() run. This test fails closed if a
future edit silently expands that claim beyond what was actually verified,
or silently regresses the two real entries back to UNPOPULATED.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from nfl.intelligence.team_intelligence_registry import (
    validate_team_intelligence_registry,
)

REGISTRY = Path("data/nfl_intelligence/team_intelligence_registry.json")
REALLY_COVERED_TEAMS = {"BUF", "DET"}


def load_registry():
    with REGISTRY.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class NewsBrainTeamCoverageHonestyTests(unittest.TestCase):
    def test_exactly_the_two_verified_teams_show_official_injury_practice_coverage(self):
        payload = load_registry()
        validate_team_intelligence_registry(payload)
        covered = {
            row["team"] for row in payload["teams"]
            if "OFFICIAL_INJURY_PRACTICE" not in row["missing_channels"]
        }
        self.assertEqual(covered, REALLY_COVERED_TEAMS)

    def test_covered_teams_carry_real_official_nfl_source_evidence(self):
        payload = load_registry()
        by_team = {row["team"]: row for row in payload["teams"]}
        for team in REALLY_COVERED_TEAMS:
            row = by_team[team]
            self.assertEqual(row["coverage_status"], "PARTIAL")
            self.assertTrue(row["official_sources"], f"{team} should have >=1 official source")
            self.assertTrue(
                any(s.get("source_id") == "official_nfl" for s in row["official_sources"])
            )
            self.assertIsNotNone(row["last_audited"])

    def test_remaining_thirty_teams_are_still_honestly_unpopulated(self):
        payload = load_registry()
        untouched = [
            row for row in payload["teams"] if row["team"] not in REALLY_COVERED_TEAMS
        ]
        self.assertEqual(len(untouched), 30)
        for row in untouched:
            self.assertEqual(row["coverage_status"], "UNPOPULATED")
            self.assertEqual(row["official_sources"], [])
            self.assertIsNone(row["last_audited"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

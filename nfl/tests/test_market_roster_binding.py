#!/usr/bin/env python3
"""Contracts for FanDuel passing candidate -> 2026 roster identity binding."""
import unittest

from nfl.normalize import market_roster_binding as binding


CANDIDATE = {
    "event_id": "999",
    "event_name": "Philadelphia Eagles @ Kansas City Chiefs",
    "market": "passing_yards",
    "player_name": "Jalen Hurts",
    "line": 247.5,
    "over_odds": -114,
    "under_odds": -114,
}

ROSTER = [
    {
        "season": "2026",
        "team": "PHI",
        "position": "QB",
        "depth_chart_position": "QB",
        "full_name": "Jalen Hurts",
        "gsis_id": "00-0036389",
        "esb_id": "HUR767022",
        "status": "ACT",
    },
    {
        "season": "2026",
        "team": "KC",
        "position": "QB",
        "depth_chart_position": "QB",
        "full_name": "Patrick Mahomes",
        "gsis_id": "00-0033873",
        "esb_id": "MAH401939",
        "status": "ACT",
    },
]


class MarketRosterBindingTests(unittest.TestCase):
    def test_exact_qb_in_event_binds(self):
        out = binding.bind_passing_candidate(
            CANDIDATE,
            ROSTER,
            season=2026,
        )
        self.assertEqual(out["binding_status"], "BOUND")
        self.assertEqual(out["team"], "PHI")
        self.assertEqual(out["gsis_id"], "00-0036389")
        self.assertEqual(out["event_away_team"], "PHI")
        self.assertEqual(out["event_home_team"], "KC")

    def test_player_on_team_outside_event_is_unresolved(self):
        roster = [{**ROSTER[0], "team": "DAL"}]
        out = binding.bind_passing_candidate(
            CANDIDATE,
            roster,
            season=2026,
        )
        self.assertEqual(out["binding_status"], "UNRESOLVED_PLAYER")
        self.assertIsNone(out["gsis_id"])

    def test_duplicate_exact_event_qb_is_ambiguous(self):
        roster = ROSTER + [{**ROSTER[0], "team": "KC"}]
        out = binding.bind_passing_candidate(
            CANDIDATE,
            roster,
            season=2026,
        )
        self.assertEqual(out["binding_status"], "AMBIGUOUS_PLAYER")
        self.assertEqual(out["candidate_count"], 2)

    def test_same_name_non_qb_fails_position(self):
        roster = [{
            **ROSTER[0],
            "position": "WR",
            "depth_chart_position": "WR",
        }]
        out = binding.bind_passing_candidate(
            CANDIDATE,
            roster,
            season=2026,
        )
        self.assertEqual(out["binding_status"], "POSITION_MISMATCH")
        self.assertIsNone(out["gsis_id"])

    def test_missing_gsis_id_fails_closed(self):
        roster = [{**ROSTER[0], "gsis_id": ""}]
        out = binding.bind_passing_candidate(
            CANDIDATE,
            roster,
            season=2026,
        )
        self.assertEqual(out["binding_status"], "MISSING_DURABLE_ID")

    def test_unknown_event_team_fails_closed(self):
        bad = {
            **CANDIDATE,
            "event_name": "Mystery Club @ Kansas City Chiefs",
        }
        out = binding.bind_passing_candidate(
            bad,
            ROSTER,
            season=2026,
        )
        self.assertEqual(
            out["binding_status"],
            "UNRESOLVED_GAME_TEAMS",
        )

    def test_non_passing_yards_market_is_rejected(self):
        bad = {**CANDIDATE, "market": "passing_tds"}
        with self.assertRaisesRegex(ValueError, "passing_yards"):
            binding.bind_passing_candidate(
                bad,
                ROSTER,
                season=2026,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

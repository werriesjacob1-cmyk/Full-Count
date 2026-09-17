#!/usr/bin/env python3
import unittest

from nfl.normalize.market_roster_binding import bind_player_prop_selection


BASE = {
    "event_id": "35599552",
    "event_name": "Detroit Lions @ Buffalo Bills",
    "canonical_market": "receiving_yards",
    "player_name": "Amon-Ra St. Brown",
    "selection_id": "1",
}

ROSTER = [
    {
        "season": 2026,
        "team": "DET",
        "position": "WR",
        "depth_chart_position": "WR",
        "full_name": "Amon-Ra St. Brown",
        "gsis_id": "00-0036963",
        "esb_id": "STB183376",
        "status": "ACT",
    },
    {
        "season": 2026,
        "team": "BUF",
        "position": "QB",
        "depth_chart_position": "QB",
        "full_name": "Josh Allen",
        "gsis_id": "00-0034857",
        "status": "ACT",
    },
]


class PlayerPropBindingTests(unittest.TestCase):
    def test_exact_event_player_binds_to_gsis(self):
        result = bind_player_prop_selection(BASE, ROSTER, season=2026)
        self.assertEqual(result["binding_status"], "BOUND")
        self.assertEqual(result["player_gsis_id"], "00-0036963")
        self.assertEqual(result["team"], "DET")

    def test_passing_market_requires_qb_compatible_identity(self):
        result = bind_player_prop_selection(
            {**BASE, "canonical_market": "passing_yards"},
            ROSTER,
            season=2026,
        )
        self.assertEqual(result["binding_status"], "POSITION_MISMATCH")
        self.assertIsNone(result["player_gsis_id"])

    def test_player_outside_event_and_missing_id_fail_closed(self):
        outside = [{**ROSTER[0], "team": "GB"}]
        self.assertEqual(
            bind_player_prop_selection(BASE, outside, season=2026)[
                "binding_status"
            ],
            "UNRESOLVED_PLAYER",
        )
        no_id = [{**ROSTER[0], "gsis_id": ""}]
        self.assertEqual(
            bind_player_prop_selection(BASE, no_id, season=2026)[
                "binding_status"
            ],
            "MISSING_DURABLE_ID",
        )

    def test_duplicate_exact_identity_is_ambiguous(self):
        duplicate = ROSTER + [{**ROSTER[0], "team": "BUF"}]
        result = bind_player_prop_selection(BASE, duplicate, season=2026)
        self.assertEqual(result["binding_status"], "AMBIGUOUS_PLAYER")

    def test_touchdown_market_does_not_guess_position_but_still_requires_identity(self):
        defender = {
            **BASE,
            "canonical_market": "anytime_touchdown",
            "player_name": "Example Returner",
        }
        roster = [
            {
                "season": 2026,
                "team": "BUF",
                "position": "DB",
                "full_name": "Example Returner",
                "gsis_id": "00-0099999",
            }
        ]
        result = bind_player_prop_selection(defender, roster, season=2026)
        self.assertEqual(result["binding_status"], "BOUND")


if __name__ == "__main__":
    unittest.main(verbosity=2)

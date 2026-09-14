#!/usr/bin/env python3
"""Identity and temporal contracts for nflverse weekly history."""
import unittest

from nfl.research.nflverse_history import NUMERIC_STATS, build_prior_only_rows


def row(player_id: str, *, passing_yards: float = 0) -> dict:
    result = {
        "player_id": player_id,
        "player_display_name": "" if player_id in {"", "0"} else "Quarter Back",
        "position": "" if player_id in {"", "0"} else "QB",
        "season": "1999",
        "week": "1",
        "season_type": "REG",
        "team": "ATL",
        "opponent_team": "MIN",
    }
    result.update({stat: "0" for stat in NUMERIC_STATS})
    result["passing_yards"] = str(passing_yards)
    return result


class MissingIdentityTests(unittest.TestCase):
    def test_literal_zero_structural_row_is_not_a_player(self):
        self.assertEqual(build_prior_only_rows([row("0")]), [])

    def test_blank_structural_row_is_not_a_player(self):
        self.assertEqual(build_prior_only_rows([row("")]), [])

    def test_literal_zero_with_offense_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "missing player_id row carries offense"):
            build_prior_only_rows([row("0", passing_yards=42)])


if __name__ == "__main__":
    unittest.main()

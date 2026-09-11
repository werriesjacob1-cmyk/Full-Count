#!/usr/bin/env python3
"""Regression contract for B1 point-in-time feature construction.

The builder must:
- project team volume from PRIOR team games only
- derive player share/efficiency from PRIOR player games only
- derive team target volume from all players in prior team games
- never use current-game team/player outcomes as features
- fail closed on missing/duplicate team rows
"""
import unittest

from nfl.research import b1_history


def prow(player_id, week, team, opponent, *,
         attempts=0, passing_yards=0, carries=0, rushing_yards=0,
         targets=0, receptions=0, receiving_yards=0, position="WR"):
    return {
        "player_id": player_id,
        "player_display_name": player_id,
        "position": position,
        "season": "2025",
        "week": str(week),
        "season_type": "REG",
        "team": team,
        "opponent_team": opponent,
        "attempts": str(attempts),
        "completions": "0",
        "passing_yards": str(passing_yards),
        "passing_tds": "0",
        "carries": str(carries),
        "rushing_yards": str(rushing_yards),
        "rushing_tds": "0",
        "targets": str(targets),
        "receptions": str(receptions),
        "receiving_yards": str(receiving_yards),
        "receiving_tds": "0",
    }


def trow(week, team, opponent, *, attempts, carries):
    return {
        "season": "2025",
        "week": str(week),
        "season_type": "REG",
        "team": team,
        "opponent_team": opponent,
        "attempts": str(attempts),
        "carries": str(carries),
    }


class PriorOnlyTeamVolume(unittest.TestCase):
    def setUp(self):
        self.team_rows = [
            trow(1, "AAA", "BBB", attempts=30, carries=25),
            trow(2, "AAA", "CCC", attempts=40, carries=20),
            trow(3, "AAA", "DDD", attempts=99, carries=99),
        ]
        self.player_rows = [
            # Team targets week 1 = 10.
            prow("WR1", 1, "AAA", "BBB", targets=6, receptions=4,
                 receiving_yards=60),
            prow("WR2", 1, "AAA", "BBB", targets=4, receptions=2,
                 receiving_yards=20),
            # Team targets week 2 = 20.
            prow("WR1", 2, "AAA", "CCC", targets=10, receptions=7,
                 receiving_yards=100),
            prow("WR2", 2, "AAA", "CCC", targets=10, receptions=5,
                 receiving_yards=50),
            # Week 3 is deliberately extreme and MUST NOT leak into week 3 features.
            prow("WR1", 3, "AAA", "DDD", targets=30, receptions=20,
                 receiving_yards=300),
            prow("WR2", 3, "AAA", "DDD", targets=30, receptions=20,
                 receiving_yards=300),
        ]

    def test_week_three_team_projection_uses_weeks_one_and_two_only(self):
        built = b1_history.build_b1_rows(
            self.player_rows, self.team_rows, rolling_window=2
        )
        w3 = next(r for r in built if r["player_id"] == "WR1" and r["week"] == 3)
        f = w3["features"]
        self.assertEqual(f["projected_team_pass_attempts"], 35.0)
        self.assertEqual(f["projected_team_carries"], 22.5)
        self.assertEqual(f["projected_team_targets"], 15.0)
        self.assertNotEqual(f["projected_team_pass_attempts"], 99.0)
        self.assertNotEqual(f["projected_team_targets"], 60.0)

    def test_player_target_share_is_ratio_of_prior_sums(self):
        built = b1_history.build_b1_rows(
            self.player_rows, self.team_rows, rolling_window=2
        )
        w3 = next(r for r in built if r["player_id"] == "WR1" and r["week"] == 3)
        # WR1 prior targets = 6+10=16; team prior targets = 10+20=30.
        self.assertAlmostEqual(
            w3["features"]["prior_player_target_share"], 16 / 30
        )
        # Catch rate = (4+7)/(6+10)
        self.assertAlmostEqual(
            w3["features"]["prior_player_catch_rate"], 11 / 16
        )
        # Yards per target = (60+100)/(6+10)
        self.assertAlmostEqual(
            w3["features"]["prior_player_yards_per_target"], 10.0
        )

    def test_current_target_remains_target_only(self):
        built = b1_history.build_b1_rows(
            self.player_rows, self.team_rows, rolling_window=2
        )
        w3 = next(r for r in built if r["player_id"] == "WR1" and r["week"] == 3)
        self.assertEqual(w3["target"]["receiving_yards"], 300.0)
        self.assertAlmostEqual(
            w3["features"]["prior_player_yards_per_target"], 10.0
        )


class QBAndRushShares(unittest.TestCase):
    def test_attempt_and_carry_shares_use_prior_team_denominators(self):
        team_rows = [
            trow(1, "AAA", "BBB", attempts=40, carries=20),
            trow(2, "AAA", "CCC", attempts=30, carries=30),
            trow(3, "AAA", "DDD", attempts=35, carries=25),
        ]
        player_rows = [
            prow("QB1", 1, "AAA", "BBB", attempts=36, passing_yards=288,
                 carries=4, rushing_yards=20, position="QB"),
            prow("QB1", 2, "AAA", "CCC", attempts=24, passing_yards=168,
                 carries=6, rushing_yards=18, position="QB"),
            prow("QB1", 3, "AAA", "DDD", attempts=35, passing_yards=350,
                 carries=10, rushing_yards=100, position="QB"),
        ]
        built = b1_history.build_b1_rows(
            player_rows, team_rows, rolling_window=2
        )
        w3 = next(r for r in built if r["week"] == 3)
        f = w3["features"]
        self.assertAlmostEqual(
            f["prior_player_pass_attempt_share"], (36 + 24) / (40 + 30)
        )
        self.assertAlmostEqual(
            f["prior_player_carry_share"], (4 + 6) / (20 + 30)
        )
        self.assertAlmostEqual(
            f["prior_player_pass_yards_per_attempt"], (288 + 168) / (36 + 24)
        )
        self.assertAlmostEqual(
            f["prior_player_rush_yards_per_carry"], (20 + 18) / (4 + 6)
        )


class FailClosed(unittest.TestCase):
    def test_missing_team_week_row_fails(self):
        players = [prow("WR1", 1, "AAA", "BBB", targets=2)]
        with self.assertRaisesRegex(ValueError, "missing team-week row"):
            b1_history.build_b1_rows(players, [])

    def test_duplicate_team_week_row_fails(self):
        teams = [
            trow(1, "AAA", "BBB", attempts=30, carries=20),
            trow(1, "AAA", "BBB", attempts=30, carries=20),
        ]
        players = [prow("WR1", 1, "AAA", "BBB", targets=2)]
        with self.assertRaisesRegex(ValueError, "duplicate team-week row"):
            b1_history.build_b1_rows(players, teams)


if __name__ == "__main__":
    unittest.main(verbosity=2)

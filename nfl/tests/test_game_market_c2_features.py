#!/usr/bin/env python3
import copy
import unittest

from nfl.research.game_market_c2_features import (
    MARGIN_FEATURES,
    TOTAL_FEATURES,
    build_c2_game_rows,
    filter_team_offense_rows_for_negative_value_bug,
)


def _game_id(week, home, away):
    return f"2020_{week:02d}_{away}_{home}"


def _make_dataset(n_weeks=5):
    """AAA hosts BBB in odd weeks, BBB hosts AAA in even weeks."""
    scoring_rows = []
    team_offense_rows = []
    pbp_rows = []
    play_counter = 0
    for week in range(1, n_weeks + 1):
        home, away = ("AAA", "BBB") if week % 2 == 1 else ("BBB", "AAA")
        game_id = _game_id(week, home, away)
        home_score, away_score = 24, 17
        scoring_rows.append({
            "game_id": game_id, "season": 2020, "week": week, "game_type": "REG",
            "home_team": home, "away_team": away, "final_status": "FINAL",
            "home_score": home_score, "away_score": away_score,
        })
        for team, opponent, attempts in ((home, away, 30), (away, home, 25)):
            team_offense_rows.append({
                "game_id": game_id, "season": 2020, "week": week, "season_type": "REG",
                "team": team, "opponent_team": opponent,
                "attempts": attempts, "passing_yards": 220, "sacks_suffered": 2,
                "passing_epa": 3.0, "carries": 22, "rushing_yards": 90,
            })
            for _ in range(6):
                play_counter += 1
                pbp_rows.append({
                    "game_id": game_id, "play_id": str(play_counter), "season": 2020,
                    "week": week, "season_type": "REG", "posteam": team, "defteam": opponent,
                    "down": 1, "half_seconds_remaining": 1500, "wp": 0.5,
                    "qb_dropback": 1, "rush_attempt": 0, "qb_kneel": 0, "qb_spike": 0,
                })
    return scoring_rows, team_offense_rows, pbp_rows


class GameMarketC2FeatureLeakageTests(unittest.TestCase):
    def test_no_leakage_current_game_stats_never_enter_its_own_features(self):
        scoring_rows, team_offense_rows, pbp_rows = _make_dataset(n_weeks=5)
        baseline = build_c2_game_rows(
            scoring_rows, team_offense_rows, pbp_rows, rolling_window=5, min_prior_games=3
        )
        week5_id = _game_id(5, "AAA", "BBB")
        week5_baseline = next(r for r in baseline if r["game_id"] == week5_id)
        self.assertEqual(week5_baseline["eligibility"], "ELIGIBLE")

        mutated_offense = copy.deepcopy(team_offense_rows)
        for row in mutated_offense:
            if row["game_id"] == week5_id:
                row["passing_epa"] = 999.0
                row["rushing_yards"] = 999.0
        mutated_pbp = copy.deepcopy(pbp_rows)
        for row in mutated_pbp:
            if row["game_id"] == week5_id:
                row["down"] = 3
                row["wp"] = 0.99

        mutated = build_c2_game_rows(
            scoring_rows, mutated_offense, mutated_pbp, rolling_window=5, min_prior_games=3
        )
        week5_mutated = next(r for r in mutated if r["game_id"] == week5_id)
        self.assertEqual(
            week5_baseline["margin_features"], week5_mutated["margin_features"]
        )
        self.assertEqual(
            week5_baseline["total_features"], week5_mutated["total_features"]
        )

    def test_no_leakage_future_game_never_enters_an_earlier_feature(self):
        scoring_rows, team_offense_rows, pbp_rows = _make_dataset(n_weeks=5)
        baseline = build_c2_game_rows(
            scoring_rows, team_offense_rows, pbp_rows, rolling_window=5, min_prior_games=3
        )
        week4_id = _game_id(4, "BBB", "AAA")
        week4_baseline = next(r for r in baseline if r["game_id"] == week4_id)

        truncated_scoring = [r for r in scoring_rows if r["week"] <= 4]
        truncated_offense = [r for r in team_offense_rows if r["week"] <= 4]
        truncated_pbp = [r for r in pbp_rows if r["week"] <= 4]
        truncated = build_c2_game_rows(
            truncated_scoring, truncated_offense, truncated_pbp,
            rolling_window=5, min_prior_games=3,
        )
        week4_truncated = next(r for r in truncated if r["game_id"] == week4_id)
        self.assertEqual(
            week4_baseline["margin_features"], week4_truncated["margin_features"]
        )
        self.assertEqual(
            week4_baseline["total_features"], week4_truncated["total_features"]
        )

    def test_insufficient_prior_games_is_marked_ineligible(self):
        scoring_rows, team_offense_rows, pbp_rows = _make_dataset(n_weeks=2)
        rows = build_c2_game_rows(
            scoring_rows, team_offense_rows, pbp_rows, rolling_window=5, min_prior_games=3
        )
        self.assertTrue(all(r["eligibility"] == "INSUFFICIENT_HISTORY" for r in rows))
        self.assertTrue(all(r["margin_features"] is None for r in rows))

    def test_eligible_row_has_every_declared_feature(self):
        scoring_rows, team_offense_rows, pbp_rows = _make_dataset(n_weeks=5)
        rows = build_c2_game_rows(
            scoring_rows, team_offense_rows, pbp_rows, rolling_window=5, min_prior_games=3
        )
        eligible = [r for r in rows if r["eligibility"] == "ELIGIBLE"]
        self.assertTrue(eligible)
        for row in eligible:
            self.assertEqual(set(row["margin_features"]), set(MARGIN_FEATURES))
            self.assertEqual(set(row["total_features"]), set(TOTAL_FEATURES))
            self.assertFalse(row["uses_market_line_as_feature"])
            self.assertFalse(row["uses_current_game_outcome_as_feature"])
            self.assertFalse(row["ol_continuity_prior_used"])


class NegativeValueFilterTests(unittest.TestCase):
    def test_negative_value_excludes_both_teams_of_the_affected_game(self):
        rows = [
            {"game_id": "g1", "team": "AAA", "opponent_team": "BBB",
             "attempts": 20, "passing_yards": -5, "sacks_suffered": 1, "carries": 10, "rushing_yards": 30},
            {"game_id": "g1", "team": "BBB", "opponent_team": "AAA",
             "attempts": 22, "passing_yards": 200, "sacks_suffered": 2, "carries": 15, "rushing_yards": 60},
            {"game_id": "g2", "team": "AAA", "opponent_team": "BBB",
             "attempts": 25, "passing_yards": 210, "sacks_suffered": 1, "carries": 12, "rushing_yards": 40},
            {"game_id": "g2", "team": "BBB", "opponent_team": "AAA",
             "attempts": 24, "passing_yards": 190, "sacks_suffered": 2, "carries": 14, "rushing_yards": 50},
        ]
        kept, excluded = filter_team_offense_rows_for_negative_value_bug(rows)
        self.assertEqual({row["game_id"] for row in kept}, {"g2"})
        self.assertEqual({row["game_id"] for row in excluded}, {"g1"})
        self.assertEqual(len(excluded), 2)


if __name__ == "__main__":
    unittest.main()

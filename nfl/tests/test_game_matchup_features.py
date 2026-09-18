#!/usr/bin/env python3
import unittest

from nfl.research.game_matchup_features import (
    GameMatchupFeatureError,
    build_game_matchup_features,
)


def offense(game, season, week, team, opponent, n, ypp, plays):
    base = {
        "game_id": game,
        "season": season,
        "week": week,
        "season_type": "REG",
        "team": team,
        "opponent_team": opponent,
        "prior_games_n": n,
        "feature_semantics": "STRICTLY_PRIOR_REG_TEAM_BOX_SCORE",
        "prior_mean_attempts": 30.0 if n else None,
        "prior_mean_passing_yards": 240.0 if n else None,
        "prior_mean_sacks_suffered": 2.0 if n else None,
        "prior_mean_passing_epa": 3.0 if n else None,
        "prior_mean_carries": 25.0 if n else None,
        "prior_mean_rushing_yards": 110.0 if n else None,
        "prior_mean_dropback_proxy": 32.0 if n else None,
        "prior_mean_offensive_play_proxy": plays if n else None,
        "prior_mean_dropback_share_proxy": 0.56 if n else None,
        "prior_mean_yards_per_play_proxy": ypp if n else None,
    }
    return base


def defense(game, season, week, team, opponent, n, ypp_allowed, plays_allowed):
    return {
        "game_id": game,
        "season": season,
        "week": week,
        "season_type": "REG",
        "team": team,
        "opponent_team": opponent,
        "prior_games_n": n,
        "feature_semantics": "STRICTLY_PRIOR_REG_DEFENSE_BOX_SCORE",
        "prior_mean_opp_attempts_allowed": 31.0 if n else None,
        "prior_mean_opp_passing_yards_allowed": 245.0 if n else None,
        "prior_mean_sacks_generated_proxy": 2.5 if n else None,
        "prior_mean_opp_passing_epa_allowed": 2.0 if n else None,
        "prior_mean_opp_carries_allowed": 24.0 if n else None,
        "prior_mean_opp_rushing_yards_allowed": 105.0 if n else None,
        "prior_mean_opp_dropback_proxy_allowed": 33.5 if n else None,
        "prior_mean_opp_play_proxy_allowed": plays_allowed if n else None,
        "prior_mean_opp_yards_per_play_proxy_allowed": ypp_allowed if n else None,
    }


class GameMatchupFeatureTests(unittest.TestCase):
    def test_exact_home_away_join_and_transparent_contrasts(self):
        schedule = [{
            "game_id": "g1", "season": 2026, "week": 2, "season_type": "REG",
            "home_team": "DEN", "away_team": "KC",
        }]
        offenses = [
            offense("g1", 2026, 2, "DEN", "KC", 5, 6.2, 64.0),
            offense("g1", 2026, 2, "KC", "DEN", 5, 5.7, 68.0),
        ]
        defenses = [
            defense("g1", 2026, 2, "DEN", "KC", 5, 5.4, 62.0),
            defense("g1", 2026, 2, "KC", "DEN", 5, 5.9, 66.0),
        ]
        built = build_game_matchup_features(schedule, offenses, defenses)
        self.assertEqual(len(built), 1)
        game = built[0]
        self.assertEqual(game["home_team"], "DEN")
        self.assertEqual(game["away_team"], "KC")
        self.assertAlmostEqual(game["home_ypp_matchup_delta"], 0.3)
        self.assertAlmostEqual(game["away_ypp_matchup_delta"], 0.3)
        self.assertEqual(game["home_play_volume_matchup_blend"], 65.0)
        self.assertEqual(game["away_play_volume_matchup_blend"], 65.0)
        self.assertFalse(game["contains_current_game_outcome"])
        self.assertFalse(game["contains_market_line_or_price"])

    def test_week_one_null_history_is_preserved_not_imputed(self):
        schedule = [{
            "game_id": "g1", "season": 2026, "week": 1, "season_type": "REG",
            "home_team": "DEN", "away_team": "KC",
        }]
        offenses = [
            offense("g1", 2026, 1, "DEN", "KC", 0, None, None),
            offense("g1", 2026, 1, "KC", "DEN", 0, None, None),
        ]
        defenses = [
            defense("g1", 2026, 1, "DEN", "KC", 0, None, None),
            defense("g1", 2026, 1, "KC", "DEN", 0, None, None),
        ]
        game = build_game_matchup_features(schedule, offenses, defenses)[0]
        self.assertIsNone(game["home_ypp_matchup_delta"])
        self.assertIsNone(game["away_play_volume_matchup_blend"])

    def test_missing_team_feature_fails_closed(self):
        schedule = [{
            "game_id": "g1", "season": 2026, "week": 2, "season_type": "REG",
            "home_team": "DEN", "away_team": "KC",
        }]
        offenses = [offense("g1", 2026, 2, "DEN", "KC", 5, 6.2, 64.0)]
        defenses = [
            defense("g1", 2026, 2, "DEN", "KC", 5, 5.4, 62.0),
            defense("g1", 2026, 2, "KC", "DEN", 5, 5.9, 66.0),
        ]
        with self.assertRaisesRegex(GameMatchupFeatureError, "missing offense/defense"):
            build_game_matchup_features(schedule, offenses, defenses)

    def test_wrong_opponent_identity_fails_closed(self):
        schedule = [{
            "game_id": "g1", "season": 2026, "week": 2, "season_type": "REG",
            "home_team": "DEN", "away_team": "KC",
        }]
        offenses = [
            offense("g1", 2026, 2, "DEN", "LV", 5, 6.2, 64.0),
            offense("g1", 2026, 2, "KC", "DEN", 5, 5.7, 68.0),
        ]
        defenses = [
            defense("g1", 2026, 2, "DEN", "KC", 5, 5.4, 62.0),
            defense("g1", 2026, 2, "KC", "DEN", 5, 5.9, 66.0),
        ]
        with self.assertRaisesRegex(GameMatchupFeatureError, "identity mismatch"):
            build_game_matchup_features(schedule, offenses, defenses)

    def test_duplicate_schedule_game_fails_closed(self):
        game = {
            "game_id": "g1", "season": 2026, "week": 2, "season_type": "REG",
            "home_team": "DEN", "away_team": "KC",
        }
        offenses = [
            offense("g1", 2026, 2, "DEN", "KC", 5, 6.2, 64.0),
            offense("g1", 2026, 2, "KC", "DEN", 5, 5.7, 68.0),
        ]
        defenses = [
            defense("g1", 2026, 2, "DEN", "KC", 5, 5.4, 62.0),
            defense("g1", 2026, 2, "KC", "DEN", 5, 5.9, 66.0),
        ]
        with self.assertRaisesRegex(GameMatchupFeatureError, "duplicate schedule"):
            build_game_matchup_features([game, dict(game)], offenses, defenses)


if __name__ == "__main__":
    unittest.main()

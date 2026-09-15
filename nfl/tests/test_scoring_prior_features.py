#!/usr/bin/env python3
import unittest

from nfl.research.scoring_prior_features import (
    ScoringPriorFeatureError,
    build_prior_scoring_features,
)


def game(game_id, season, week, home, away, *, status="FINAL", home_score=24, away_score=20):
    return {
        "game_id": game_id,
        "season": season,
        "week": week,
        "game_type": "REG",
        "home_team": home,
        "away_team": away,
        "final_status": status,
        "home_score": home_score,
        "away_score": away_score,
    }


class ScoringPriorFeaturesTests(unittest.TestCase):
    def test_target_game_score_never_enters_target_features(self):
        rows = [
            game("g1", 2025, 1, "DEN", "KC", home_score=21, away_score=17),
            game("g2", 2025, 2, "DEN", "LV", home_score=42, away_score=7),
        ]
        built = build_prior_scoring_features(rows)
        den_w1 = next(r for r in built if r["team"] == "DEN" and r["week"] == 1)
        den_w2 = next(r for r in built if r["team"] == "DEN" and r["week"] == 2)
        self.assertEqual(den_w1["prior_games_n"], 0)
        self.assertIsNone(den_w1["prior_mean_points_for"])
        self.assertEqual(den_w2["prior_games_n"], 1)
        self.assertEqual(den_w2["prior_mean_points_for"], 21.0)
        self.assertEqual(den_w2["prior_mean_points_against"], 17.0)
        self.assertEqual(den_w2["prior_mean_margin"], 4.0)
        self.assertEqual(den_w2["prior_mean_game_total"], 38.0)
        self.assertFalse(den_w2["contains_target_game_score"])

    def test_pregame_target_receives_history_but_never_advances_it(self):
        rows = [
            game("g1", 2025, 18, "DEN", "KC", home_score=27, away_score=24),
            game("g2", 2026, 1, "KC", "DEN", status="PREGAME", home_score=None, away_score=None),
        ]
        built = build_prior_scoring_features(rows)
        den_target = next(r for r in built if r["game_id"] == "g2" and r["team"] == "DEN")
        self.assertEqual(den_target["prior_games_n"], 1)
        self.assertEqual(den_target["prior_mean_points_for"], 27.0)
        self.assertEqual(den_target["prior_mean_points_against"], 24.0)
        self.assertEqual(den_target["target_final_status"], "PREGAME")
        self.assertFalse(den_target["finality_inferred_from_scores"])

    def test_pregame_row_with_scores_fails_closed(self):
        row = game("g1", 2026, 1, "DEN", "KC", status="PREGAME", home_score=7, away_score=0)
        with self.assertRaisesRegex(ScoringPriorFeatureError, "must not contain scores"):
            build_prior_scoring_features([row])

    def test_final_row_without_both_scores_fails_closed(self):
        row = game("g1", 2026, 1, "DEN", "KC", status="FINAL", home_score=24, away_score=None)
        with self.assertRaisesRegex(ScoringPriorFeatureError, "requires both scores"):
            build_prior_scoring_features([row])

    def test_later_game_after_unresolved_pregame_is_rejected(self):
        rows = [
            game("g1", 2026, 1, "DEN", "KC", status="PREGAME", home_score=None, away_score=None),
            game("g2", 2026, 2, "DEN", "LV", status="PREGAME", home_score=None, away_score=None),
        ]
        with self.assertRaisesRegex(ScoringPriorFeatureError, "after an unresolved PREGAME"):
            build_prior_scoring_features(rows)

    def test_history_crosses_season_boundary(self):
        rows = [
            game("g1", 2025, 18, "DEN", "KC", home_score=30, away_score=10),
            game("g2", 2026, 1, "DEN", "LV", home_score=20, away_score=17),
        ]
        built = build_prior_scoring_features(rows)
        den = next(r for r in built if r["game_id"] == "g2" and r["team"] == "DEN")
        self.assertEqual(den["prior_games_n"], 1)
        self.assertEqual(den["prior_mean_points_for"], 30.0)
        self.assertEqual(den["prior_mean_points_against"], 10.0)


if __name__ == "__main__":
    unittest.main()

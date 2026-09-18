#!/usr/bin/env python3
import unittest

from nfl.research.game_market_b0 import (
    GameMarketB0Error,
    build_b0_predictions,
    evaluate_against_closing_market,
)


def scoring_row(game_id, season, week, team, opponent, *, is_home, n, pf, pa, status="FINAL"):
    return {
        "game_id": game_id,
        "season": season,
        "week": week,
        "game_type": "REG",
        "team": team,
        "opponent_team": opponent,
        "is_home": is_home,
        "target_final_status": status,
        "prior_games_n": n,
        "prior_mean_points_for": pf,
        "prior_mean_points_against": pa,
        "feature_semantics": "STRICTLY_PRIOR_EXPLICIT_FINAL_SCORING",
        "contains_target_game_score": False,
    }


def outcome(game_id, season, week, home, away, home_score, away_score, spread, total):
    return {
        "game_id": game_id,
        "season": season,
        "week": week,
        "game_type": "REG",
        "home_team": home,
        "away_team": away,
        "home_score": home_score,
        "away_score": away_score,
        "spread_line": spread,
        "total_line": total,
    }


class GameMarketB0Tests(unittest.TestCase):
    def test_prediction_rule_is_prior_scoring_blend_only(self):
        rows = [
            scoring_row("g1", 2024, 5, "DEN", "KC", is_home=True, n=4, pf=24, pa=18),
            scoring_row("g1", 2024, 5, "KC", "DEN", is_home=False, n=4, pf=28, pa=22),
        ]
        pred = build_b0_predictions(rows)[0]
        self.assertEqual(pred["eligibility"], "ELIGIBLE")
        self.assertEqual(pred["predicted_home_points"], 23.0)  # mean(24, KC allowed 22)
        self.assertEqual(pred["predicted_away_points"], 23.0)  # mean(28, DEN allowed 18)
        self.assertEqual(pred["predicted_home_margin"], 0.0)
        self.assertEqual(pred["predicted_total"], 46.0)
        self.assertFalse(pred["uses_market_line_as_feature"])
        self.assertFalse(pred["uses_current_game_outcome_as_feature"])
        self.assertEqual(pred["home_field_adjustment"], 0.0)

    def test_insufficient_history_is_not_silently_imputed(self):
        rows = [
            scoring_row("g1", 2024, 2, "DEN", "KC", is_home=True, n=1, pf=24, pa=18),
            scoring_row("g1", 2024, 2, "KC", "DEN", is_home=False, n=1, pf=28, pa=22),
        ]
        pred = build_b0_predictions(rows)[0]
        self.assertEqual(pred["eligibility"], "INSUFFICIENT_HISTORY")
        self.assertIsNone(pred["predicted_home_margin"])
        self.assertIsNone(pred["predicted_total"])

    def test_target_score_contamination_is_refused(self):
        home = scoring_row("g1", 2024, 5, "DEN", "KC", is_home=True, n=4, pf=24, pa=18)
        away = scoring_row("g1", 2024, 5, "KC", "DEN", is_home=False, n=4, pf=28, pa=22)
        home["contains_target_game_score"] = True
        with self.assertRaisesRegex(GameMarketB0Error, "refuses scoring features containing"):
            build_b0_predictions([home, away])

    def test_closing_market_is_evaluation_only_on_same_game(self):
        rows = [
            scoring_row("g1", 2024, 5, "DEN", "KC", is_home=True, n=4, pf=24, pa=18),
            scoring_row("g1", 2024, 5, "KC", "DEN", is_home=False, n=4, pf=28, pa=22),
        ]
        predictions = build_b0_predictions(rows)
        result = evaluate_against_closing_market(
            predictions,
            [outcome("g1", 2024, 5, "DEN", "KC", 27, 20, 3.5, 44.5)],
            bootstrap_iterations=20,
            bootstrap_seed=7,
        )
        held = result["partitions"]["held_2023_2025"]
        self.assertEqual(held["games"], 1)
        # B0 margin=0, actual margin=7 -> abs error 7. Closing margin=3.5 -> abs error 3.5.
        self.assertEqual(held["margin"]["b0"]["mae"], 7.0)
        self.assertEqual(held["margin"]["closing_market"]["mae"], 3.5)
        self.assertEqual(held["margin"]["mae_delta_b0_minus_close"], 3.5)
        # B0 total=46, actual=47 -> 1. Closing total=44.5 -> 2.5.
        self.assertEqual(held["total"]["b0"]["mae"], 1.0)
        self.assertEqual(held["total"]["closing_market"]["mae"], 2.5)
        self.assertEqual(held["total"]["mae_delta_b0_minus_close"], -1.5)
        self.assertFalse(result["prediction_uses_market_line_as_feature"])
        self.assertEqual(result["closing_market_use"], "RETROSPECTIVE_BENCHMARK_CONTROL_ONLY")

    def test_duplicate_prediction_game_id_fails_closed(self):
        rows = [
            scoring_row("g1", 2024, 5, "DEN", "KC", is_home=True, n=4, pf=24, pa=18),
            scoring_row("g1", 2024, 5, "KC", "DEN", is_home=False, n=4, pf=28, pa=22),
        ]
        prediction = build_b0_predictions(rows)[0]
        with self.assertRaisesRegex(GameMarketB0Error, "duplicate prediction game_id"):
            evaluate_against_closing_market(
                [prediction, dict(prediction)],
                [outcome("g1", 2024, 5, "DEN", "KC", 27, 20, 3.5, 44.5)],
                bootstrap_iterations=10,
            )

    def test_prediction_outcome_identity_mismatch_fails_closed(self):
        rows = [
            scoring_row("g1", 2024, 5, "DEN", "KC", is_home=True, n=4, pf=24, pa=18),
            scoring_row("g1", 2024, 5, "KC", "DEN", is_home=False, n=4, pf=28, pa=22),
        ]
        predictions = build_b0_predictions(rows)
        bad = outcome("g1", 2024, 5, "KC", "DEN", 20, 27, -3.5, 44.5)
        with self.assertRaisesRegex(GameMarketB0Error, "home_team mismatch"):
            evaluate_against_closing_market(predictions, [bad], bootstrap_iterations=10)


if __name__ == "__main__":
    unittest.main()

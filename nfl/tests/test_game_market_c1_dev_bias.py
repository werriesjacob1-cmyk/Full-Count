#!/usr/bin/env python3
import unittest

from nfl.research.game_market_c1_dev_bias import (
    GameMarketC1Error,
    apply_c1,
    evaluate_c1,
    fit_development_corrections,
    prepare_rows,
)


def pred(game_id, season, margin, total):
    return {
        "game_id": game_id, "season": season, "week": 5, "game_type": "REG",
        "home_team": "DEN", "away_team": "KC", "eligibility": "ELIGIBLE",
        "target_final_status": "FINAL", "predicted_home_margin": margin,
        "predicted_total": total, "uses_market_line_as_feature": False,
        "uses_current_game_outcome_as_feature": False,
    }


def out(game_id, season, home_score, away_score):
    return {
        "game_id": game_id, "season": season, "week": 5, "game_type": "REG",
        "home_team": "DEN", "away_team": "KC", "home_score": home_score,
        "away_score": away_score, "final_status": "FINAL",
    }


class GameMarketC1Tests(unittest.TestCase):
    def test_fit_uses_development_only(self):
        rows = prepare_rows(
            [pred("d1", 2018, 0, 40), pred("d2", 2019, 1, 42), pred("v1", 2021, 100, 100)],
            [out("d1", 2018, 24, 20), out("d2", 2019, 27, 21), out("v1", 2021, 10, 7)],
        )
        fit = fit_development_corrections(rows)
        self.assertEqual(fit["development_games"], 2)
        # margin residuals: 4-0 and 6-1 => +4.5
        self.assertEqual(fit["margin_additive_correction"], 4.5)
        # total residuals: 44-40 and 48-42 => +5
        self.assertEqual(fit["total_additive_correction"], 5.0)

    def test_apply_is_additive_and_has_no_market_input(self):
        rows = [{"game_id":"x", "season":2024, "b0_margin":2.0, "b0_total":44.0,
                 "actual_margin":3.0, "actual_total":46.0}]
        scored = apply_c1(rows, {"margin_additive_correction":1.5, "total_additive_correction":-2.0})
        self.assertEqual(scored[0]["c1_margin"], 3.5)
        self.assertEqual(scored[0]["c1_total"], 42.0)

    def test_evaluation_reports_validation_and_held_without_refit(self):
        rows = [
            {"game_id":"v", "season":2021, "b0_margin":0.0, "b0_total":40.0, "actual_margin":4.0, "actual_total":44.0},
            {"game_id":"h", "season":2024, "b0_margin":1.0, "b0_total":41.0, "actual_margin":5.0, "actual_total":45.0},
        ]
        result = evaluate_c1(rows, {"development_games":10, "margin_additive_correction":4.0, "total_additive_correction":4.0})
        self.assertEqual(result["partitions"]["validation_2020_2022"]["margin"]["c1_mae"], 0.0)
        self.assertEqual(result["partitions"]["held_2023_2025"]["total"]["c1_mae"], 0.0)
        self.assertFalse(result["uses_closing_market_for_fit"])
        self.assertFalse(result["uses_validation_or_held_outcomes_for_fit"])

    def test_duplicate_prediction_identity_fails_closed(self):
        with self.assertRaisesRegex(GameMarketC1Error, "duplicate prediction"):
            prepare_rows([pred("x", 2018, 0, 40), pred("x", 2018, 1, 41)], [out("x", 2018, 24, 20)])

    def test_missing_outcome_for_eligible_final_fails_closed(self):
        with self.assertRaisesRegex(GameMarketC1Error, "missing outcome"):
            prepare_rows([pred("x", 2018, 0, 40)], [])

    def test_market_using_prediction_fails_closed(self):
        prediction = pred("x", 2018, 0, 40)
        prediction["uses_market_line_as_feature"] = True
        with self.assertRaisesRegex(GameMarketC1Error, "no market-line feature"):
            prepare_rows([prediction], [out("x", 2018, 24, 20)])

    def test_outcome_requires_explicit_finality(self):
        outcome = out("x", 2018, 24, 20)
        outcome["final_status"] = "PREGAME"
        with self.assertRaisesRegex(GameMarketC1Error, "explicit FINAL"):
            prepare_rows([pred("x", 2018, 0, 40)], [outcome])


if __name__ == "__main__":
    unittest.main()

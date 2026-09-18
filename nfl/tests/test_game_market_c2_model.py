#!/usr/bin/env python3
import unittest

from nfl.research.game_market_c2_model import (
    DEV_END,
    DEV_START,
    GameMarketC2ModelError,
    evaluate_c2,
    evaluate_promotion_gate,
    fit_c2_model,
    apply_c2,
    pair_with_b0,
)


def _c2_row(game_id, season, margin_x, total_x, actual_margin, actual_total, eligible=True):
    return {
        "game_id": game_id, "season": season, "week": 1, "game_type": "REG",
        "home_team": "DEN", "away_team": "KC",
        "eligibility": "ELIGIBLE" if eligible else "INSUFFICIENT_HISTORY",
        "margin_features": {"x": margin_x} if eligible else None,
        "total_features": {"x": total_x} if eligible else None,
        "actual_margin": actual_margin, "actual_total": actual_total,
    }


def _b0_row(game_id, season, margin, total):
    return {
        "game_id": game_id, "season": season, "week": 1, "game_type": "REG",
        "home_team": "DEN", "away_team": "KC", "eligibility": "ELIGIBLE",
        "target_final_status": "FINAL", "predicted_home_margin": margin,
        "predicted_total": total,
    }


class GameMarketC2ModelFitTests(unittest.TestCase):
    def test_fit_uses_only_development_partition(self):
        rows = [_c2_row(f"d{i}", 2010, i, i, 2 * i, 3 * i) for i in range(1, 30)]
        rows += [_c2_row("v1", 2021, 999, 999, -999, -999)]  # would badly skew fit if included
        rows += [_c2_row("h1", 2024, 999, 999, -999, -999)]
        model = fit_c2_model(rows, margin_features=["x"], total_features=["x"])
        self.assertEqual(model["development_games"], 29)
        self.assertTrue(DEV_START <= 2010 <= DEV_END)
        # Coefficients should reflect the clean dev-only linear relationship,
        # not be dragged toward the out-of-range validation/held rows.
        self.assertGreater(model["margin_model"]["coefficients"]["x"], 0)

    def test_fit_ignores_ineligible_rows(self):
        rows = [_c2_row(f"d{i}", 2010, i, i, 2 * i, 3 * i) for i in range(1, 10)]
        rows.append(_c2_row("bad", 2010, None, None, None, None, eligible=False))
        model = fit_c2_model(rows, margin_features=["x"], total_features=["x"])
        self.assertEqual(model["development_games"], 9)

    def test_apply_only_predicts_eligible_rows(self):
        rows = [_c2_row(f"d{i}", 2010, i, i, 2 * i, 3 * i) for i in range(1, 10)]
        model = fit_c2_model(rows, margin_features=["x"], total_features=["x"])
        rows_with_ineligible = rows + [_c2_row("bad", 2010, None, None, None, None, eligible=False)]
        scored = apply_c2(rows_with_ineligible, model)
        bad = next(r for r in scored if r["game_id"] == "bad")
        self.assertIsNone(bad["c2_margin"])
        self.assertIsNone(bad["c2_total"])
        good = next(r for r in scored if r["game_id"] == "d5")
        self.assertIsNotNone(good["c2_margin"])


class GameMarketC2PairingTests(unittest.TestCase):
    def test_pair_requires_matching_identity(self):
        c2_rows = [{**_c2_row("g1", 2021, 1, 1, 4.0, 44.0), "c2_margin": 4.0, "c2_total": 44.0}]
        b0_rows = [{**_b0_row("g1", 2021, 3.0, 40.0), "home_team": "MISMATCH"}]
        with self.assertRaisesRegex(GameMarketC2ModelError, "identity mismatch"):
            pair_with_b0(c2_rows, b0_rows)

    def test_pair_keeps_only_common_eligible_population(self):
        c2_rows = [
            {**_c2_row("g1", 2021, 1, 1, 4.0, 44.0), "c2_margin": 4.0, "c2_total": 44.0},
            {**_c2_row("g2", 2021, 1, 1, None, None, eligible=False), "c2_margin": None, "c2_total": None},
        ]
        b0_rows = [_b0_row("g1", 2021, 3.0, 40.0)]  # g2 has no B0 prediction at all
        paired = pair_with_b0(c2_rows, b0_rows)
        self.assertEqual([r["game_id"] for r in paired], ["g1"])


class GameMarketC2EvaluationAndGateTests(unittest.TestCase):
    def _paired(self):
        # C2 exactly matches actuals (better); B0 is off by a fixed amount.
        rows = []
        for season in (2021, 2024):
            for i in range(1, 40):
                actual_margin = float(i % 7 - 3)
                actual_total = float(40 + i % 5)
                rows.append({
                    "game_id": f"{season}_{i}", "season": season,
                    "actual_margin": actual_margin, "actual_total": actual_total,
                    "b0_margin": actual_margin + 3.0, "b0_total": actual_total + 3.0,
                    "c2_margin": actual_margin + 0.1, "c2_total": actual_total + 0.1,
                })
        return rows

    def test_evaluate_c2_reports_partitions_and_season_breakdown(self):
        evaluation = evaluate_c2(self._paired())
        held = evaluation["held_2023_2025"]
        self.assertEqual(held["games"], 39)
        self.assertLess(held["margin"]["c2_mae"], held["margin"]["b0_mae"])
        self.assertIn("game_bootstrap", held["margin"])
        self.assertIn("2024", evaluation["season_by_season_2020_2025"])

    def test_promotion_gate_passes_when_c2_clearly_better_everywhere(self):
        evaluation = evaluate_c2(self._paired())
        gate = evaluate_promotion_gate(evaluation)
        self.assertTrue(gate["promotion_eligible"])
        self.assertEqual(gate["failed_conditions"], [])

    def test_promotion_gate_fails_when_held_bootstrap_crosses_zero(self):
        rows = []
        for season in (2021, 2024):
            for i in range(1, 40):
                actual_margin = float(i % 7 - 3)
                actual_total = float(40 + i % 5)
                # C2 margin barely differs from B0 -- noise-level, should not pass.
                jitter = 0.05 if i % 2 == 0 else -0.05
                rows.append({
                    "game_id": f"{season}_{i}", "season": season,
                    "actual_margin": actual_margin, "actual_total": actual_total,
                    "b0_margin": actual_margin + 1.0, "b0_total": actual_total + 3.0,
                    "c2_margin": actual_margin + 1.0 + jitter, "c2_total": actual_total + 0.1,
                })
        evaluation = evaluate_c2(rows)
        gate = evaluate_promotion_gate(evaluation)
        self.assertFalse(gate["promotion_eligible"])
        self.assertTrue(gate["failed_conditions"])

    def test_promotion_gate_fails_when_total_regresses(self):
        rows = []
        for season in (2021, 2024):
            for i in range(1, 40):
                actual_margin = float(i % 7 - 3)
                actual_total = float(40 + i % 5)
                rows.append({
                    "game_id": f"{season}_{i}", "season": season,
                    "actual_margin": actual_margin, "actual_total": actual_total,
                    "b0_margin": actual_margin + 3.0, "b0_total": actual_total + 0.1,
                    "c2_margin": actual_margin + 0.1, "c2_total": actual_total + 5.0,
                })
        evaluation = evaluate_c2(rows)
        gate = evaluate_promotion_gate(evaluation)
        self.assertFalse(gate["promotion_eligible"])
        self.assertIn(
            "held total MAE is worse than B0", gate["failed_conditions"]
        )


if __name__ == "__main__":
    unittest.main()

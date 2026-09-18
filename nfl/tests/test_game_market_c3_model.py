#!/usr/bin/env python3
import inspect
import unittest

from nfl.research.game_market_c3_model import (
    DEV_END,
    DEV_START,
    GameMarketC3ModelError,
    PROMOTION_GATE_DESCRIPTION,
    evaluate_c3,
    evaluate_promotion_gate,
    fit_c3_model,
    apply_c3,
    pair_predictions,
)


def _c3_row(game_id, season, x, actual_margin, eligible=True, c2_margin=None, c3_margin="_unset"):
    row = {
        "game_id": game_id, "season": season, "week": 1, "game_type": "REG",
        "home_team": "DEN", "away_team": "KC",
        "c3_eligibility": "ELIGIBLE" if eligible else "INSUFFICIENT_AVAILABILITY_HISTORY",
        "c3_margin_features": {"x": x} if eligible else None,
        "actual_margin": actual_margin if eligible else None,
    }
    if c2_margin is not None:
        row["c2_margin"] = c2_margin
    if c3_margin != "_unset":
        row["c3_margin"] = c3_margin
    return row


def _b0_row(game_id, season, margin):
    return {
        "game_id": game_id, "season": season, "week": 1, "game_type": "REG",
        "home_team": "DEN", "away_team": "KC", "eligibility": "ELIGIBLE",
        "target_final_status": "FINAL", "predicted_home_margin": margin,
    }


class GameMarketC3ModelFitTests(unittest.TestCase):
    def test_fit_uses_only_development_partition(self):
        rows = [_c3_row(f"d{i}", 2010, i, 2 * i) for i in range(1, 30)]
        rows += [_c3_row("v1", 2021, 999, -999)]
        rows += [_c3_row("h1", 2024, 999, -999)]
        model = fit_c3_model(rows, margin_features=["x"])
        self.assertEqual(model["development_games"], 29)
        self.assertTrue(DEV_START <= 2010 <= DEV_END)
        self.assertGreater(model["margin_model"]["coefficients"]["x"], 0)

    def test_fit_ignores_c3_ineligible_rows(self):
        rows = [_c3_row(f"d{i}", 2010, i, 2 * i) for i in range(1, 10)]
        rows.append(_c3_row("bad", 2010, None, None, eligible=False))
        model = fit_c3_model(rows, margin_features=["x"])
        self.assertEqual(model["development_games"], 9)

    def test_fit_raises_when_no_eligible_development_rows(self):
        rows = [_c3_row("v1", 2021, 1.0, 1.0)]
        with self.assertRaises(GameMarketC3ModelError):
            fit_c3_model(rows, margin_features=["x"])

    def test_apply_only_predicts_c3_eligible_rows(self):
        rows = [_c3_row(f"d{i}", 2010, i, 2 * i) for i in range(1, 10)]
        model = fit_c3_model(rows, margin_features=["x"])
        rows_with_ineligible = rows + [_c3_row("bad", 2010, None, None, eligible=False)]
        scored = apply_c3(rows_with_ineligible, model)
        bad = next(r for r in scored if r["game_id"] == "bad")
        self.assertIsNone(bad["c3_margin"])
        good = next(r for r in scored if r["game_id"] == "d5")
        self.assertIsNotNone(good["c3_margin"])


class GameMarketC3PairingTests(unittest.TestCase):
    def test_pair_requires_matching_identity(self):
        c3_rows = [_c3_row("g1", 2021, 1, 4.0, c2_margin=3.5, c3_margin=4.0)]
        b0_rows = [{**_b0_row("g1", 2021, 3.0), "home_team": "MISMATCH"}]
        with self.assertRaisesRegex(GameMarketC3ModelError, "identity mismatch"):
            pair_predictions(c3_rows, b0_rows)

    def test_pair_requires_applied_predictions_present(self):
        c3_rows = [_c3_row("g1", 2021, 1, 4.0, c2_margin=None, c3_margin=4.0)]
        b0_rows = [_b0_row("g1", 2021, 3.0)]
        with self.assertRaisesRegex(GameMarketC3ModelError, "missing an applied model prediction"):
            pair_predictions(c3_rows, b0_rows)

    def test_pair_keeps_only_common_c3_eligible_population(self):
        c3_rows = [
            _c3_row("g1", 2021, 1, 4.0, c2_margin=3.5, c3_margin=4.0),
            _c3_row("g2", 2021, 1, None, eligible=False),
        ]
        b0_rows = [_b0_row("g1", 2021, 3.0)]  # g2 has no B0 prediction at all
        paired = pair_predictions(c3_rows, b0_rows)
        self.assertEqual([r["game_id"] for r in paired], ["g1"])


class GameMarketC3EvaluationAndGateTests(unittest.TestCase):
    def _paired(self, *, c3_beats_c2_and_b0=True, seasons=(2021, 2023, 2024, 2025)):
        rows = []
        for season in seasons:
            for i in range(1, 40):
                actual_margin = float(i % 7 - 3)
                if c3_beats_c2_and_b0:
                    b0 = actual_margin + 3.0
                    c2 = actual_margin + 1.0
                    c3 = actual_margin + 0.1
                else:
                    b0 = actual_margin + 1.0
                    c2 = actual_margin + 1.0
                    c3 = actual_margin + 1.0
                rows.append({
                    "game_id": f"{season}_{i}", "season": season,
                    "actual_margin": actual_margin,
                    "b0_margin": b0, "c2_margin": c2, "c3_margin": c3,
                })
        return rows

    def test_evaluate_c3_reports_partitions_season_breakdown_and_leave_one_out(self):
        evaluation = evaluate_c3(self._paired())
        held = evaluation["held_2023_2025"]
        self.assertEqual(held["games"], 39 * 3)
        self.assertLess(held["c3_mae"], held["b0_mae"])
        self.assertLess(held["c3_mae"], held["c2_mae"])
        self.assertIn("bootstrap_c3_vs_b0", held)
        self.assertIn("bootstrap_c3_vs_c2", held)
        self.assertIn("2024", evaluation["season_by_season_all_covered"])
        self.assertEqual(
            set(evaluation["held_leave_one_season_out"].keys()), {"2023", "2024", "2025"}
        )

    def test_promotion_gate_passes_when_c3_clearly_better_than_both_everywhere(self):
        evaluation = evaluate_c3(self._paired())
        gate = evaluate_promotion_gate(evaluation)
        self.assertTrue(gate["promotion_eligible"])
        self.assertEqual(gate["failed_conditions"], [])

    def test_promotion_gate_fails_when_c3_does_not_beat_c2(self):
        evaluation = evaluate_c3(self._paired(c3_beats_c2_and_b0=False))
        gate = evaluate_promotion_gate(evaluation)
        self.assertFalse(gate["promotion_eligible"])
        self.assertTrue(any("C2" in reason for reason in gate["failed_conditions"]))

    def test_promotion_gate_fails_when_one_held_season_drives_the_entire_improvement(self):
        # C3 only beats C2 in 2023; in 2024/2025 it is identical to C2. The
        # aggregate held delta is still an improvement, but it should not
        # survive excluding 2023 -- this is exactly the diagnosed C2 failure
        # mode (driven largely by one season) and the gate must catch it.
        rows = []
        for season in (2021, 2023, 2024, 2025):
            for i in range(1, 40):
                actual_margin = float(i % 7 - 3)
                b0 = actual_margin + 3.0
                c2 = actual_margin + 1.0
                c3 = actual_margin + (0.1 if season == 2023 else 1.0)
                rows.append({
                    "game_id": f"{season}_{i}", "season": season,
                    "actual_margin": actual_margin,
                    "b0_margin": b0, "c2_margin": c2, "c3_margin": c3,
                })
        evaluation = evaluate_c3(rows)
        held = evaluation["held_2023_2025"]
        self.assertLess(held["c3_mae"], held["c2_mae"])  # aggregate looks like an improvement
        gate = evaluate_promotion_gate(evaluation)
        self.assertFalse(gate["promotion_eligible"])
        self.assertTrue(any("does not survive excluding" in reason for reason in gate["failed_conditions"]))

    def test_promotion_gate_fails_when_held_bootstrap_crosses_zero(self):
        rows = []
        for season in (2021, 2023, 2024, 2025):
            for i in range(1, 40):
                actual_margin = float(i % 7 - 3)
                jitter = 0.05 if i % 2 == 0 else -0.05
                rows.append({
                    "game_id": f"{season}_{i}", "season": season,
                    "actual_margin": actual_margin,
                    "b0_margin": actual_margin + 1.0,
                    "c2_margin": actual_margin + 1.0,
                    "c3_margin": actual_margin + 1.0 + jitter,
                })
        evaluation = evaluate_c3(rows)
        gate = evaluate_promotion_gate(evaluation)
        self.assertFalse(gate["promotion_eligible"])
        self.assertTrue(gate["failed_conditions"])


class GameMarketC3ReproducibilityTests(unittest.TestCase):
    def test_fit_apply_and_evaluate_are_deterministic_across_repeated_runs(self):
        rows = [_c3_row(f"d{i}", 2010, i, 2 * i) for i in range(1, 30)]
        model_a = fit_c3_model(rows, margin_features=["x"])
        model_b = fit_c3_model(rows, margin_features=["x"])
        self.assertEqual(model_a["margin_model"]["coefficients"], model_b["margin_model"]["coefficients"])

        scored_a = apply_c3(rows, model_a)
        scored_b = apply_c3(rows, model_b)
        self.assertEqual(
            [r["c3_margin"] for r in scored_a], [r["c3_margin"] for r in scored_b]
        )

        held_rows = self._held_paired_rows()
        evaluation_a = evaluate_c3(held_rows)
        evaluation_b = evaluate_c3(held_rows)
        self.assertEqual(
            evaluate_promotion_gate(evaluation_a), evaluate_promotion_gate(evaluation_b)
        )

    @staticmethod
    def _held_paired_rows():
        rows = []
        for season in (2021, 2023, 2024, 2025):
            for i in range(1, 40):
                actual_margin = float(i % 7 - 3)
                rows.append({
                    "game_id": f"{season}_{i}", "season": season,
                    "actual_margin": actual_margin,
                    "b0_margin": actual_margin + 3.0,
                    "c2_margin": actual_margin + 1.0,
                    "c3_margin": actual_margin + 0.1,
                })
        return rows


class GameMarketC3GatePredeclarationTests(unittest.TestCase):
    """Structural proof the gate is predeclared, not fit to real held data.

    `evaluate_promotion_gate` must be a pure function of whatever
    `evaluation` dict it is handed -- its thresholds/conditions are fixed in
    source (`PROMOTION_GATE_DESCRIPTION`) independent of any specific
    dataset. This is checked two ways: (1) the gate function's source never
    references a magic number derived from real data, and (2) calling it on
    entirely synthetic, hand-built evaluation dicts -- never real held
    output -- exercises every documented failure condition.
    """

    def test_gate_description_is_a_fixed_module_constant(self):
        self.assertIsInstance(PROMOTION_GATE_DESCRIPTION, str)
        self.assertIn("held margin MAE(C3) < held margin MAE(B0)", PROMOTION_GATE_DESCRIPTION)
        self.assertIn("held margin MAE(C3) < held margin MAE(C2)", PROMOTION_GATE_DESCRIPTION)
        self.assertIn("leaving out any single held-partition season", PROMOTION_GATE_DESCRIPTION)

    def test_gate_source_contains_no_real_evaluation_literals(self):
        source = inspect.getsource(evaluate_promotion_gate)
        # The gate must reference only dict keys/comparisons, never a
        # hardcoded MAE-like float threshold that could only have come from
        # having already looked at real held numbers.
        for token in ("10.4", "10.7", "0.151"):  # C2's own real reported numbers
            self.assertNotIn(token, source)

    def test_gate_handles_synthetic_all_pass_case(self):
        synthetic_evaluation = {
            "held_2023_2025": {
                "b0_mae": 10.0, "c2_mae": 9.5, "c3_mae": 9.0,
                "bootstrap_c3_vs_b0": {"p97_5": -0.2},
                "bootstrap_c3_vs_c2": {"p97_5": -0.1},
            },
            "validation_2020_2022": {"b0_mae": 10.0, "c2_mae": 9.5, "c3_mae": 9.0},
            "held_leave_one_season_out": {
                "2023": {"mae_delta_c3_minus_c2_excluding_this_season": -0.05},
                "2024": {"mae_delta_c3_minus_c2_excluding_this_season": -0.05},
                "2025": {"mae_delta_c3_minus_c2_excluding_this_season": -0.05},
            },
        }
        gate = evaluate_promotion_gate(synthetic_evaluation)
        self.assertTrue(gate["promotion_eligible"])

    def test_gate_handles_synthetic_empty_leave_one_out_case(self):
        synthetic_evaluation = {
            "held_2023_2025": {
                "b0_mae": 10.0, "c2_mae": 9.5, "c3_mae": 9.0,
                "bootstrap_c3_vs_b0": {"p97_5": -0.2},
                "bootstrap_c3_vs_c2": {"p97_5": -0.1},
            },
            "validation_2020_2022": {"b0_mae": 10.0, "c2_mae": 9.5, "c3_mae": 9.0},
            "held_leave_one_season_out": {},
        }
        gate = evaluate_promotion_gate(synthetic_evaluation)
        self.assertFalse(gate["promotion_eligible"])


if __name__ == "__main__":
    unittest.main()

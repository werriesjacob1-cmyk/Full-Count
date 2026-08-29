#!/usr/bin/env python3
"""Synthetic tests for the decisive PA/opportunity replication scaffold."""
from __future__ import annotations

import unittest

from backtest.pa_opportunity_decisive import (
    PAExperimentIntegrityError,
    build_prediction_stage,
    decisive_verdict,
    evaluate_frozen_predictions,
    fit_training_state,
)


def lineup_signal(order):
    return (9.0 - order) * 100.0 / 8.0


def training_row(player, pa, outcome, date="2025-06-01", order=1):
    return {
        "date": date,
        "game_pk": 1000 + player,
        "player_id": player,
        "prop_type": "hits",
        "line": 1,
        "actual_pa": pa,
        "outcome": outcome,
        "signals": {
            "lineup_slot": lineup_signal(order),
            "days_rest": 0,
            "getaway_day": 0,
        },
    }


def holdout_row(date, game, player, prob, order=1):
    return {
        "date": date,
        "game_pk": game,
        "player_id": player,
        "prop_type": "hits",
        "line": 1,
        "predicted_prob": prob,
        "signals": {
            "lineup_slot": lineup_signal(order),
            "days_rest": 0,
            "getaway_day": 0,
        },
    }


def fitted_state():
    rows = []
    # Enough variety to create order and P(hit|PA) tables, intentionally
    # fewer than MIN_CELL_N=200 so prediction exercises order fallback.
    for i in range(60):
        pa = 3 + (i % 3)
        outcome = 1 if pa >= 4 else 0
        rows.append(training_row(i + 1, pa, outcome))
    return fit_training_state(rows)


class TrainingTests(unittest.TestCase):
    def test_2026_row_cannot_enter_training(self):
        rows = [training_row(1, 4, 1, date="2026-01-01")]
        with self.assertRaises(PAExperimentIntegrityError):
            fit_training_state(rows)

    def test_training_locks_existing_200_cell_threshold(self):
        state = fitted_state()
        self.assertEqual(state["metadata"]["min_cell_n"], 200)
        self.assertEqual(state["metadata"]["min_line_prob"], 0.60)
        self.assertEqual(state["metadata"]["n_joint_cells"], 0)


class PredictionTests(unittest.TestCase):
    def test_prediction_stage_rejects_even_masked_outcome_key(self):
        rows = [holdout_row("2026-04-01", 1, 1, 0.70)]
        rows[0]["outcome"] = None
        with self.assertRaises(PAExperimentIntegrityError):
            build_prediction_stage(rows, fitted_state())

    def test_missing_opportunity_state_falls_back_exactly_to_champion(self):
        item = holdout_row("2026-04-01", 1, 1, 0.67)
        item["signals"] = {}
        freeze = build_prediction_stage([item], fitted_state())
        frozen = freeze["payload"]["population"][0]
        self.assertEqual(frozen["prediction_path"], "champion_fallback")
        self.assertEqual(frozen["challenger_prob"], frozen["current_prob"])

    def test_volume_cannot_migrate_to_another_date(self):
        rows = [
            holdout_row("2026-04-01", 1, 1, 0.70),
            holdout_row("2026-04-01", 1, 2, 0.61),
            holdout_row("2026-04-01", 1, 3, 0.59),
            holdout_row("2026-04-02", 2, 4, 0.59),
            holdout_row("2026-04-02", 2, 5, 0.58),
        ]
        freeze = build_prediction_stage(rows, fitted_state())
        dates = freeze["payload"]["selection"]["dates"]
        self.assertEqual(dates["2026-04-01"]["selected_n"], 2)
        self.assertEqual(dates["2026-04-02"]["selected_n"], 0)
        self.assertEqual(
            len(freeze["payload"]["selection"]["challenger_ids"]),
            2,
        )

    def test_wrong_market_is_not_silently_filtered(self):
        item = holdout_row("2026-04-01", 1, 1, 0.70)
        item["prop_type"] = "total_bases"
        with self.assertRaises(PAExperimentIntegrityError):
            build_prediction_stage([item], fitted_state())


class EvaluationTests(unittest.TestCase):
    def test_evaluation_requires_exact_frozen_population(self):
        rows = [
            holdout_row("2026-04-01", 1, 1, 0.70),
            holdout_row("2026-04-01", 1, 2, 0.61),
        ]
        freeze = build_prediction_stage(rows, fitted_state())
        eval_rows = [dict(rows[0], outcome=1)]
        with self.assertRaises(PAExperimentIntegrityError):
            evaluate_frozen_predictions(eval_rows, freeze)

    def test_evaluation_uses_frozen_selection_not_new_scores(self):
        rows = [
            holdout_row("2026-04-01", 1, 1, 0.70),
            holdout_row("2026-04-01", 1, 2, 0.61),
            holdout_row("2026-04-01", 1, 3, 0.59),
        ]
        freeze = build_prediction_stage(rows, fitted_state())

        # Outcomes are joined later; radically mutate prediction fields to prove
        # evaluation does not rerank from them.
        eval_rows = []
        for i, source in enumerate(rows):
            item = dict(source)
            item["predicted_prob"] = 0.01 if i == 0 else 0.99
            item["outcome"] = 1 if i == 2 else 0
            eval_rows.append(item)

        report = evaluate_frozen_predictions(eval_rows, freeze)
        self.assertEqual(
            report["prediction_freeze_sha256"],
            freeze["sha256"],
        )
        self.assertEqual(
            report["selection_anatomy"]["n_selected"],
            freeze["payload"]["selection"]["dates"]["2026-04-01"]["selected_n"],
        )

    def test_verdict_defaults_to_kill_when_uncertainty_is_not_strictly_positive(self):
        report = {
            "selection_anatomy": {
                "realized_winner_delta": 1,
                "added_minus_removed_hit_rate": 0.10,
            },
            "cluster_bootstrap": {
                "overall_delta_ci95": [-0.01, 0.05],
                "changed_estimable": True,
                "added_minus_removed_ci95": [0.01, 0.20],
            },
        }
        verdict = decisive_verdict(report)
        self.assertEqual(verdict["verdict"], "KILL_CLOSE")

    def test_verdict_requires_changed_set_to_be_estimable(self):
        report = {
            "selection_anatomy": {
                "realized_winner_delta": 2,
                "added_minus_removed_hit_rate": 0.20,
            },
            "cluster_bootstrap": {
                "overall_delta_ci95": [0.01, 0.08],
                "changed_estimable": False,
                "added_minus_removed_ci95": None,
            },
        }
        verdict = decisive_verdict(report)
        self.assertEqual(verdict["verdict"], "KILL_CLOSE")


if __name__ == "__main__":
    unittest.main(verbosity=2)

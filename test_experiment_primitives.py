#!/usr/bin/env python3
"""Regression tests for backtest/experiment_primitives.py."""
from __future__ import annotations

import copy
import unittest

from backtest.experiment_primitives import (
    ExperimentIntegrityError,
    build_prediction_freeze,
    candidate_identity,
    deterministic_sha256,
    paired_game_cluster_bootstrap,
    require_unique_population,
    select_floor_matched_per_date,
    selection_anatomy,
    select_top_k_per_date,
)


def row(date, game, player, current, challenger, outcome=None):
    item = {
        "date": date,
        "game_pk": game,
        "player_id": player,
        "prop_type": "hits",
        "line": 1,
        "current_prob": current,
        "challenger_prob": challenger,
    }
    if outcome is not None:
        item["outcome"] = outcome
    return item


class IdentityTests(unittest.TestCase):
    def test_incomplete_identity_fails_closed(self):
        item = row("2026-04-01", 1, 10, 0.7, 0.7)
        del item["line"]
        with self.assertRaises(ExperimentIntegrityError):
            candidate_identity(item)

    def test_duplicate_identity_fails_closed(self):
        a = row("2026-04-01", 1, 10, 0.7, 0.7)
        b = copy.deepcopy(a)
        b["challenger_prob"] = 0.9
        with self.assertRaises(ExperimentIntegrityError):
            require_unique_population([a, b])


class PerDateSelectionTests(unittest.TestCase):
    def test_top_k_cannot_move_volume_between_dates(self):
        rows = [
            row("2026-04-01", 1, 1, 0.90, 0.10),
            row("2026-04-01", 1, 2, 0.80, 0.20),
            row("2026-04-01", 1, 3, 0.70, 0.99),
            row("2026-04-02", 2, 4, 0.60, 0.95),
        ]
        result = select_top_k_per_date(rows, 2)
        self.assertEqual(result["dates"]["2026-04-01"]["selected_n"], 2)
        self.assertEqual(result["dates"]["2026-04-02"]["selected_n"], 1)
        self.assertEqual(len(result["champion_ids"]), 3)
        self.assertEqual(len(result["challenger_ids"]), 3)

    def test_floor_volume_is_recomputed_on_each_date(self):
        rows = [
            row("2026-04-01", 1, 1, 0.70, 0.10),
            row("2026-04-01", 1, 2, 0.61, 0.20),
            row("2026-04-01", 1, 3, 0.59, 0.99),
            row("2026-04-02", 2, 4, 0.59, 0.99),
            row("2026-04-02", 2, 5, 0.20, 0.98),
        ]
        result = select_floor_matched_per_date(rows, 0.60)
        self.assertEqual(result["dates"]["2026-04-01"]["selected_n"], 2)
        self.assertEqual(result["dates"]["2026-04-02"]["selected_n"], 0)
        self.assertEqual(len(result["champion_ids"]), 2)
        self.assertEqual(len(result["challenger_ids"]), 2)

    def test_tie_break_is_deterministic_across_input_order(self):
        rows = [
            row("2026-04-01", 1, 12, 0.7, 0.7),
            row("2026-04-01", 1, 11, 0.7, 0.7),
            row("2026-04-01", 1, 13, 0.7, 0.7),
        ]
        a = select_top_k_per_date(rows, 2)
        b = select_top_k_per_date(list(reversed(rows)), 2)
        self.assertEqual(a, b)

    def test_nonfinite_score_fails_closed(self):
        rows = [row("2026-04-01", 1, 1, float("nan"), 0.7)]
        with self.assertRaises(ExperimentIntegrityError):
            select_top_k_per_date(rows, 1)


class FreezeTests(unittest.TestCase):
    def test_prediction_freeze_rejects_outcome(self):
        rows = [row("2026-04-01", 1, 1, 0.7, 0.8, outcome=1)]
        selection_rows = [dict(rows[0])]
        selection_rows[0].pop("outcome")
        selection = select_top_k_per_date(selection_rows, 1)
        with self.assertRaises(ExperimentIntegrityError):
            build_prediction_freeze(rows, selection)

    def test_freeze_hash_is_input_order_invariant(self):
        rows = [
            row("2026-04-01", 1, 2, 0.7, 0.8),
            row("2026-04-01", 1, 1, 0.8, 0.7),
        ]
        selection_a = select_top_k_per_date(rows, 1)
        selection_b = select_top_k_per_date(list(reversed(rows)), 1)
        freeze_a = build_prediction_freeze(rows, selection_a, {"arm": "B"})
        freeze_b = build_prediction_freeze(list(reversed(rows)), selection_b, {"arm": "B"})
        self.assertEqual(freeze_a["sha256"], freeze_b["sha256"])

    def test_deterministic_hash_changes_when_value_changes(self):
        a = deterministic_sha256({"x": 1, "y": 2})
        b = deterministic_sha256({"y": 2, "x": 1})
        c = deterministic_sha256({"x": 1, "y": 3})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class EvaluationTests(unittest.TestCase):
    def test_selection_anatomy_counts_added_removed_and_winners(self):
        prediction_rows = [
            row("2026-04-01", 1, 1, 0.90, 0.10),
            row("2026-04-01", 1, 2, 0.80, 0.99),
        ]
        selection = select_top_k_per_date(prediction_rows, 1)
        eval_rows = [
            row("2026-04-01", 1, 1, 0.90, 0.10, outcome=0),
            row("2026-04-01", 1, 2, 0.80, 0.99, outcome=1),
        ]
        anatomy = selection_anatomy(eval_rows, selection)
        self.assertEqual(anatomy["n_selected"], 1)
        self.assertEqual(anatomy["realized_winner_delta"], 1)
        self.assertEqual(anatomy["added_n"], 1)
        self.assertEqual(anatomy["removed_n"], 1)
        self.assertEqual(anatomy["added_minus_removed_hit_rate"], 1.0)

    def test_identical_selection_bootstrap_is_exact_zero(self):
        prediction_rows = [
            row("2026-04-01", 1, 1, 0.9, 0.9),
            row("2026-04-02", 2, 2, 0.8, 0.8),
        ]
        selection = select_top_k_per_date(prediction_rows, 1)
        eval_rows = [
            row("2026-04-01", 1, 1, 0.9, 0.9, outcome=1),
            row("2026-04-02", 2, 2, 0.8, 0.8, outcome=0),
        ]
        boot = paired_game_cluster_bootstrap(
            eval_rows,
            selection,
            n_replicates=200,
            seed=7,
        )
        self.assertEqual(boot["overall_delta_ci95"], [0.0, 0.0])
        self.assertEqual(boot["p_overall_delta_le_zero"], 1.0)
        self.assertFalse(boot["changed_estimable"])
        self.assertEqual(boot["changed_valid_replicates"], 0)

    def test_changed_set_requires_cluster_support_not_pseudocounts(self):
        prediction_rows = [
            row("2026-04-01", 1, 1, 0.90, 0.10),
            row("2026-04-01", 1, 2, 0.80, 0.99),
            row("2026-04-02", 2, 3, 0.90, 0.90),
        ]
        selection = select_top_k_per_date(prediction_rows, 1)
        eval_rows = [
            row("2026-04-01", 1, 1, 0.90, 0.10, outcome=0),
            row("2026-04-01", 1, 2, 0.80, 0.99, outcome=1),
            row("2026-04-02", 2, 3, 0.90, 0.90, outcome=1),
        ]
        a = paired_game_cluster_bootstrap(
            eval_rows,
            selection,
            n_replicates=400,
            seed=9,
            min_changed_valid_fraction=0.95,
        )
        b = paired_game_cluster_bootstrap(
            eval_rows,
            selection,
            n_replicates=400,
            seed=9,
            min_changed_valid_fraction=0.95,
        )
        self.assertEqual(a, b)
        self.assertGreater(a["changed_invalid_replicates"], 0)
        self.assertFalse(a["changed_estimable"])
        self.assertIsNone(a["added_minus_removed_ci95"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

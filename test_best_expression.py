#!/usr/bin/env python3
"""Tests for backtest/best_expression.py.

The whole risk with a correlation-aware selector is that it improves its
apparent hit rate by betting less. These tests exist to make that
impossible: exact N is asserted everywhere, refill is asserted to come
only from the declared population, and the not-enough-independents case
is asserted to keep volume rather than shrink it.
"""
from __future__ import annotations

import os
import sys
import unittest
import json
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from backtest import best_expression as be
from backtest import equal_volume as ev


def row(date, game_pk, player_id, prop_type, outcome, score):
    return {"date": date, "game_pk": game_pk, "player_id": player_id,
            "prop_type": prop_type, "line": 0.5, "outcome": outcome,
            "predicted_prob": 0.6, "score": float(score)}


def pop_from(rows):
    return ev.EligiblePopulation(rows, definition="t", definition_version="v1",
                                 evidence_regime="test",
                                 dataset_identity={"artifact_sha256": "a" * 64,
                                                   "artifact_row_count": len(rows)})


def verified_pop_from(rows):
    import accuracy_lab as al
    tmp = tempfile.mkdtemp(prefix="fc_best_expression_verified_")
    rows_path = os.path.join(tmp, "rows.jsonl")
    manifest_path = os.path.join(tmp, "holdout_manifest.json")
    with open(rows_path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    al.lock_holdout(
        rows_path, holdout_frac=0.2, manifest_path=manifest_path,
        require_strong_dataset_identity=True)
    with open(manifest_path, encoding="utf-8") as fh:
        identity = json.load(fh)
    identity["manifest_path"] = manifest_path
    return ev.EligiblePopulation(
        rows, definition="t", definition_version="v1",
        evidence_regime="canonical_historical_model_data",
        dataset_identity=identity)


SCORE = lambda r: r["score"]


class SuppressionTests(unittest.TestCase):
    def test_redundant_expressions_are_demoted_not_deleted(self):
        # Player 1 in game 10 has THREE expressions occupying the top of
        # the base ranking -- exactly the Jared Young shape.
        rows = [
            row("2024-05-01", 10, 1, "hits", 0, 100),
            row("2024-05-01", 10, 1, "total_bases", 0, 99),
            row("2024-05-01", 10, 1, "hits_runs_rbis", 0, 98),
            row("2024-05-01", 11, 2, "hits", 1, 50),
            row("2024-05-01", 12, 3, "hits", 1, 40),
        ]
        population = pop_from(rows)
        order = be.best_expression_rank_fn(SCORE)(population)
        self.assertEqual(len(order), len(rows),
                         "ranking must still cover the whole population")
        self.assertEqual(len(set(order)), len(rows), "no duplicates")
        top3 = order[:3]
        theses = {be.thesis_identity(population.row(i)) for i in top3}
        self.assertEqual(len(theses), 3, "top 3 must be three distinct theses")

    def test_exact_volume_is_preserved_when_refill_is_available(self):
        rows = [row("2024-05-01", 10, 1, p, 0, 100 - i)
                for i, p in enumerate(["hits", "total_bases", "hits_runs_rbis"])]
        rows += [row("2024-05-01", 20 + i, 10 + i, "hits", 1, 50 - i) for i in range(5)]
        population = pop_from(rows)
        d = be.describe_suppression(population, SCORE, volume=3)
        self.assertTrue(d["exact_volume_preserved"])
        self.assertEqual(d["n_suppressed"], d["n_refilled"],
                         "every suppressed slot must be refilled, not dropped")
        self.assertEqual(d["redundant_expressions_in_base_top_n"], 2)
        self.assertTrue(d["fully_refillable"])

    def test_refill_never_comes_from_outside_the_population(self):
        rows = [row("2024-05-01", 10, 1, p, 0, 100 - i)
                for i, p in enumerate(["hits", "total_bases"])]
        rows += [row("2024-05-01", 30, 9, "hits", 1, 10)]
        population = pop_from(rows)
        order = be.best_expression_rank_fn(SCORE)(population)
        for ident in order:
            self.assertIn(ident, population)

    def test_when_population_lacks_independents_volume_is_KEPT_not_shrunk(self):
        # Every candidate is the same player/game: nothing to refill with.
        rows = [row("2024-05-01", 10, 1, p, 0, 100 - i)
                for i, p in enumerate(["hits", "total_bases", "hits_runs_rbis", "home_run"])]
        population = pop_from(rows)
        d = be.describe_suppression(population, SCORE, volume=3)
        self.assertTrue(d["exact_volume_preserved"],
                        "volume must never be reduced just because refill was impossible")
        self.assertFalse(d["fully_refillable"])
        self.assertEqual(d["independent_candidates_available_below_cut"], 0)
        order = be.best_expression_rank_fn(SCORE)(population)
        self.assertEqual(len(order[:3]), 3)

    def test_strict_promotion_mode_fails_when_same_slate_cannot_refill(self):
        rows = [row("2024-05-01", 10, 1, p, 0, 100 - i)
                for i, p in enumerate(["hits", "total_bases", "hits_runs_rbis", "home_run"])]
        population = pop_from(rows)
        strict = be.strict_best_expression_rank_fn(
            SCORE, volume_by_date={"2024-05-01": 3})
        with self.assertRaises(be.StrictRefillError):
            strict(population)

    def test_strict_promotion_mode_accepts_fully_refillable_same_slate(self):
        rows = [row("2024-05-01", 10, 1, p, 0, 100 - i)
                for i, p in enumerate(["hits", "total_bases", "hits_runs_rbis"])]
        rows += [row("2024-05-01", 20 + i, 10 + i, "hits", 1, 50 - i)
                 for i in range(4)]
        population = pop_from(rows)
        strict = be.strict_best_expression_rank_fn(
            SCORE, volume_by_date={"2024-05-01": 3})
        self.assertEqual(len(strict(population)), len(rows))

    def test_max_per_thesis_greater_than_one_is_honoured(self):
        rows = [row("2024-05-01", 10, 1, p, 0, 100 - i)
                for i, p in enumerate(["hits", "total_bases", "hits_runs_rbis"])]
        rows += [row("2024-05-01", 20, 5, "hits", 1, 10)]
        population = pop_from(rows)
        order = be.best_expression_rank_fn(SCORE, max_per_thesis=2)(population)
        top2 = order[:2]
        self.assertEqual(len({be.thesis_identity(population.row(i)) for i in top2}), 1,
                         "with max_per_thesis=2 the top two may share a thesis")

    def test_invalid_max_per_thesis_rejected(self):
        with self.assertRaises(ValueError):
            be.best_expression_rank_fn(SCORE, max_per_thesis=0)

    def test_unknown_thesis_mode_rejected(self):
        with self.assertRaises(ValueError):
            be.thesis_identity({"game_pk": 1}, mode="nonsense")


class DeterminismTests(unittest.TestCase):
    def test_ranking_is_deterministic_and_input_order_independent(self):
        rows = [row("2024-05-01", 10 + (i // 2), i, "hits", i % 2, 100 - i)
                for i in range(10)]
        a, b = pop_from(rows), pop_from(list(reversed(rows)))
        fn = be.best_expression_rank_fn(SCORE)
        self.assertEqual(fn(a), fn(a))
        self.assertEqual(fn(a), fn(b))


class EqualVolumeIntegrationTests(unittest.TestCase):
    """Best Expression must be usable as a drop-in challenger, and the
    equal-volume framework must accept it without special-casing."""

    def _rows(self):
        rows = [row("2024-05-01", 10, 1, p, 0, 100 - i)
                for i, p in enumerate(["hits", "total_bases", "hits_runs_rbis"])]
        # Independent refill candidates belong to the SAME operational slate.
        # Cross-date refill would manufacture a selector win by borrowing easy
        # candidates from a different day's opportunity set.
        rows += [row("2024-05-01", 20 + i, 10 + i, "hits", 1, 60 - i)
                 for i in range(5)]
        return rows

    def _population(self):
        return pop_from(self._rows())

    def test_best_expression_passes_the_promotion_grade_equal_volume_contract(self):
        population = verified_pop_from(self._rows())
        quota = {"2024-05-01": 3}
        champ = ev.SelectionPolicy(
            "champion_score", "1.0", ev.rank_by(SCORE),
            ranking_input_fields=("score",))
        chal = ev.SelectionPolicy(
            "best_expression", "1.0",
            be.strict_best_expression_rank_fn(SCORE, volume_by_date=quota),
            ranking_input_fields=("score", "date", "game_pk", "player_id"))
        rep = ev.EqualVolumeExperiment(
            population=population, champion=champ, challenger=chal, volume=3,
            promotion_grade=True, volume_by_date=quota).run()
        self.assertEqual(rep["champion"]["selected_n"], 3)
        self.assertEqual(rep["challenger"]["selected_n"], 3)
        self.assertTrue(rep["integrity"]["same_operational_volume_by_date"])
        a = rep["selection_anatomy"]
        self.assertEqual(a["overlap_n"] + a["added"]["n"], 3)
        self.assertEqual(a["overlap_n"] + a["removed"]["n"], 3)

    def test_challenger_is_more_diversified_than_champion_here(self):
        population = self._population()
        champ = ev.SelectionPolicy(
            "champion_score", "1.0", ev.rank_by(SCORE),
            ranking_input_fields=("score",))
        chal = ev.SelectionPolicy(
            "best_expression", "1.0", be.best_expression_rank_fn(SCORE),
            ranking_input_fields=("score", "game_pk", "player_id"))
        rep = ev.EqualVolumeExperiment(population=population, champion=champ,
                                       challenger=chal, volume=3).run()
        self.assertLess(rep["dependence"]["champion"]["unique_games"],
                        rep["dependence"]["challenger"]["unique_games"])
        # Diversification is a portfolio property, NOT evidence of accuracy.
        # This fixture is constructed, so no hit-rate claim is asserted.


if __name__ == "__main__":
    unittest.main(verbosity=2)

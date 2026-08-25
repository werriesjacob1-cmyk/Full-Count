#!/usr/bin/env python3
"""test_candidate_dataset.py -- coverage for backtest/candidate_dataset.py,
the reusable candidate-level decision-record builder prepared 2026-08-25 so
research can start the moment rows_canonical.jsonl exists. Synthetic
fixtures only -- no real historical data exists yet (main backfill still
running at the time this was written).

    /tmp/mlbvenv/bin/python3 test_candidate_dataset.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import candidate_dataset as cd


def backtest_row(**overrides):
    row = {
        "date": "2026-06-14", "game_pk": 812345, "player_id": 670541,
        "player_name": "Yordan Alvarez", "prop_type": "hits", "line": 0.5,
        "needs": 1, "signals": {"platoon": 80.0}, "score": 74.2,
        "cat_matchup": 71.0, "cat_recent_form": 68.5, "cat_environment": 55.0,
        "cat_baseline_skill": 62.0, "cat_context": 80.0,
        "predicted_prob": 0.71, "outcome": 1, "actual": 2, "fair_test": True,
        "actual_pa": 4, "code_git_sha": "6d01e83",
        "backtest_generated_at": "2026-08-16T14:02:11+00:00",
    }
    row.update(overrides)
    return row


def registry_snapshot(**overrides):
    snap = {
        "market_odds": -130, "market_implied": 0.565, "market_edge": 0.021,
        "market_hold": 0.045, "price_clears": True, "hit_probability": 0.71,
        "prob_ci": [0.62, 0.79], "reliability": "A", "reliability_note": "n=412",
        "sample_n": 412, "stable_lift": 0.05, "lift": 0.08, "base_rate": 0.63,
        "lineup_assumed": False, "recommendation_status": "top_pick",
        "status_reasons": [], "publication_source_commit": "1ead2fb1",
        "publication_run_id": "32807093880", "publication_deployment_id": "d1",
        "published_top_pick_at": "2026-06-14T17:05:00+00:00",
    }
    snap.update(overrides)
    return snap


class FromBacktestRowTests(unittest.TestCase):
    def test_identity_prediction_evidence_outcome_provenance_mapped_directly(self):
        record = cd.from_backtest_row(backtest_row())
        self.assertEqual(record["identity"]["player_name"], "Yordan Alvarez")
        self.assertEqual(record["identity"]["stat"], "hits")
        self.assertEqual(record["prediction"]["predicted_prob"], 0.71)
        self.assertEqual(record["evidence"]["cat_matchup"], 71.0)
        self.assertEqual(record["outcome"]["outcome"], 1)
        self.assertEqual(record["outcome"]["actual"], 2)
        self.assertEqual(record["provenance"]["code_git_sha"], "6d01e83")

    def test_market_is_explicitly_unavailable_not_silently_missing(self):
        record = cd.from_backtest_row(backtest_row())
        self.assertIsNone(record["market"]["market_odds"])
        self.assertIn("no historical market data", record["market"]["market_unavailable_reason"])

    def test_decision_is_explicitly_unavailable_without_a_policy_replay_row(self):
        record = cd.from_backtest_row(backtest_row())
        self.assertIsNone(record["decision"]["recommendation_status"])
        self.assertIsNotNone(record["decision"]["decision_unavailable_reason"])

    def test_apply_policy_row_populates_decision_status(self):
        row = backtest_row(recommendation_status="lean", status_reasons=["real read"])
        record = cd.from_backtest_row(row)
        self.assertEqual(record["decision"]["recommendation_status"], "lean")

    def test_shrinkage_inputs_are_explicitly_flagged_unavailable(self):
        record = cd.from_backtest_row(backtest_row())
        self.assertIsNone(record["prediction"]["shrinkage_inputs"])
        self.assertIsNotNone(record["prediction"]["shrinkage_inputs_unavailable_reason"])


class OverlayRegistrySnapshotTests(unittest.TestCase):
    def test_none_snapshot_is_a_no_op(self):
        record = cd.from_backtest_row(backtest_row())
        before = dict(record["market"])
        cd.overlay_registry_snapshot(record, None)
        self.assertEqual(record["market"], before)

    def test_real_snapshot_fills_market_and_clears_the_unavailable_reason(self):
        record = cd.from_backtest_row(backtest_row())
        cd.overlay_registry_snapshot(record, registry_snapshot())
        self.assertEqual(record["market"]["market_odds"], -130)
        self.assertTrue(record["market"]["price_clears"])
        self.assertIsNone(record["market"]["market_unavailable_reason"])

    def test_real_snapshot_fills_decision_and_provenance(self):
        record = cd.from_backtest_row(backtest_row())
        cd.overlay_registry_snapshot(record, registry_snapshot())
        self.assertEqual(record["decision"]["recommendation_status"], "top_pick")
        self.assertEqual(record["provenance"]["publication_source_commit"], "1ead2fb1")

    def test_never_fabricates_data_for_a_candidate_absent_from_the_registry(self):
        # A candidate genuinely not in the registry (rejected, never
        # published) must stay explicitly unavailable, not get zero-filled.
        record = cd.from_backtest_row(backtest_row())
        cd.overlay_registry_snapshot(record, None)
        self.assertIsNotNone(record["market"]["market_unavailable_reason"])
        self.assertIsNotNone(record["decision"]["decision_unavailable_reason"])


class OverlayGateTraceTests(unittest.TestCase):
    def test_none_trace_is_a_no_op(self):
        record = cd.from_backtest_row(backtest_row())
        before = dict(record["decision"])
        cd.overlay_gate_trace(record, None)
        self.assertEqual(record["decision"], before)

    def test_real_trace_fills_gates_and_blocking_gate(self):
        record = cd.from_backtest_row(backtest_row())
        trace = {"status": "neutral", "gates": {"has_prob": True, "meets_prob_floor": False},
                 "blocking_gate": "meets_prob_floor"}
        cd.overlay_gate_trace(record, trace)
        self.assertEqual(record["decision"]["blocking_gate"], "meets_prob_floor")
        self.assertEqual(record["decision"]["gates"]["meets_prob_floor"], False)
        self.assertIsNone(record["decision"]["decision_unavailable_reason"])

    def test_does_not_override_a_real_recommendation_status_already_present(self):
        row = backtest_row(recommendation_status="lean")
        record = cd.from_backtest_row(row)
        trace = {"status": "neutral", "gates": {}, "blocking_gate": None}
        cd.overlay_gate_trace(record, trace)
        self.assertEqual(record["decision"]["recommendation_status"], "lean")


class OverlaySettlementTests(unittest.TestCase):
    def test_none_settlement_is_a_no_op(self):
        record = cd.from_backtest_row(backtest_row())
        before = dict(record["outcome"])
        cd.overlay_settlement(record, None)
        self.assertEqual(record["outcome"], before)

    def test_settlement_vocabulary_kept_separate_from_backtest_outcome(self):
        record = cd.from_backtest_row(backtest_row())  # outcome=1
        cd.overlay_settlement(record, {"settlement_state": "miss", "result_actual": 0,
                                       "result_reason": "official final"})
        # Both vocabularies preserved even if they'd disagree -- a real,
        # investigable finding, never silently collapsed into one.
        self.assertEqual(record["outcome"]["outcome"], 1)
        self.assertEqual(record["outcome"]["settlement_state"], "miss")


if __name__ == "__main__":
    unittest.main(verbosity=2)

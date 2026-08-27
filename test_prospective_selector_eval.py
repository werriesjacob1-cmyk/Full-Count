#!/usr/bin/env python3
"""Tests for pre-outcome locked prospective selector evaluation."""
from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))

import candidate_funnel_logger as cfl
import prospective_durability as pdur
import prospective_selector_eval as pse


DATE = "2026-08-25"
OBS = "2026-08-25T17:00:00Z"


def candidate(player_id, *, status=None, edge=0.05, prob=0.65):
    return {
        "game_pk": 99,
        "game_start": "2026-08-25T23:00:00Z",
        "player_id": player_id,
        "name": f"Player {player_id}",
        "team": "A",
        "matchup": "A@B",
        "bet_side": "over",
        "projection": {"stat": "hits", "value": 0.5, "needs": 1},
        "hit_probability": prob,
        "market_odds": -120,
        "market_implied": 0.545,
        "market_fair": 0.54,
        "market_fair_method": "assumed_hold",
        "edge_vs_fair": edge,
        "reliability": "A",
        "score": 70.0,
        "status": status,
        "status_reasons": [],
    }


def build_snapshot():
    candidates = [
        candidate(1, status="top_pick", edge=0.02, prob=0.61),
        candidate(2, status="top_pick", edge=0.03, prob=0.62),
        candidate(3, edge=0.20, prob=0.70),
        candidate(4, edge=0.10, prob=0.69),
    ]
    qc = {
        cfl.candidate_identity(c, date=DATE): ("confirmed_lineup", None)
        for c in candidates
    }
    records = cfl.build_funnel_records(
        candidates,
        date=DATE,
        generated_at=OBS,
        code_git_sha="a" * 40,
        quality_control_index=qc,
        market_context={
            "book": "fanduel",
            "observed_at": OBS,
            "family_states": {"batter_props": "AVAILABLE"},
        },
        run_metadata={
            "model_version": "m1",
            "selection_policy_version": "s1",
            "calibration_version": "c1",
            "feature_version": "f1",
            "prediction_timestamp": OBS,
            "odds_fetched_at": OBS,
            "board_generated_at": OBS,
        },
    )
    manifest = cfl.build_snapshot_manifest(
        records,
        date=DATE,
        observed_at=OBS,
        code_git_sha="a" * 40,
        market_context={
            "book": "fanduel",
            "observed_at": OBS,
            "family_states": {"batter_props": "AVAILABLE"},
        },
        run_metadata={
            "model_version": "m1",
            "selection_policy_version": "s1",
            "calibration_version": "c1",
            "feature_version": "f1",
            "prediction_timestamp": OBS,
            "odds_fetched_at": OBS,
            "board_generated_at": OBS,
        },
    )
    return records, manifest


def outcome(record, grade):
    return {
        "candidate_id": record["identity"]["candidate_id"],
        "date": DATE,
        "grade": grade,
        "actual": 1 if grade == "hit" else 0,
        "actual_stat": "hits",
        "reason": None,
        "graded_at": "2026-08-26T05:00:00Z",
    }


class LockPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.records, self.manifest = build_snapshot()
        pdur.materialize_snapshot(
            self.records, self.manifest, self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_lock_binds_exact_snapshot_before_outcomes(self):
        plan = pse.lock_plan(
            self.root,
            observations=[{
                "slate_date": DATE,
                "snapshot_id": self.manifest["snapshot_id"],
            }],
            challenger_ranking="edge_vs_fair",
            locked_at="2026-08-25T18:00:00Z",
        )
        self.assertEqual(plan["challenger_ranking"], "edge_vs_fair")
        self.assertEqual(
            plan["observations"][0]["candidate_universe_fingerprint"],
            self.manifest["candidate_universe_fingerprint"])
        self.assertTrue(plan["plan_fingerprint"])
        self.assertTrue(pse.validate_plan(self.root, plan))

    def test_lock_after_any_outcome_exists_is_rejected_as_post_outcome(self):
        pdur.materialize_outcomes(
            [outcome(self.records[0], "hit")], self.root)
        with self.assertRaises(pse.ProspectivePlanError):
            pse.lock_plan(
                self.root,
                observations=[{
                    "slate_date": DATE,
                    "snapshot_id": self.manifest["snapshot_id"],
                }],
                challenger_ranking="edge_vs_fair",
            )

    def test_duplicate_slate_date_is_rejected(self):
        with self.assertRaises(pse.ProspectivePlanError):
            pse.lock_plan(
                self.root,
                observations=[
                    {"slate_date": DATE, "snapshot_id": self.manifest["snapshot_id"]},
                    {"slate_date": DATE, "snapshot_id": "other"},
                ],
                challenger_ranking="edge_vs_fair",
            )

    def test_editing_locked_challenger_breaks_plan_fingerprint(self):
        plan = pse.lock_plan(
            self.root,
            observations=[{
                "slate_date": DATE,
                "snapshot_id": self.manifest["snapshot_id"],
            }],
            challenger_ranking="edge_vs_fair",
        )
        tampered = copy.deepcopy(plan)
        tampered["challenger_ranking"] = "hit_probability"
        with self.assertRaises(pse.ProspectivePlanError):
            pse.validate_plan(self.root, tampered)

    def test_editing_locked_snapshot_evidence_breaks_validation(self):
        plan = pse.lock_plan(
            self.root,
            observations=[{
                "slate_date": DATE,
                "snapshot_id": self.manifest["snapshot_id"],
            }],
            challenger_ranking="edge_vs_fair",
        )
        tampered = copy.deepcopy(plan)
        tampered["observations"][0]["n_candidates"] += 1
        core = {
            k: v for k, v in tampered.items()
            if k != "plan_fingerprint"
        }
        tampered["plan_fingerprint"] = pse._fingerprint(core)
        with self.assertRaises(pse.ProspectivePlanError):
            pse.validate_plan(self.root, tampered)


class EvaluatePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.records, self.manifest = build_snapshot()
        pdur.materialize_snapshot(
            self.records, self.manifest, self.root)
        self.plan = pse.lock_plan(
            self.root,
            observations=[{
                "slate_date": DATE,
                "snapshot_id": self.manifest["snapshot_id"],
            }],
            challenger_ranking="edge_vs_fair",
            locked_at="2026-08-25T18:00:00Z",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_evaluation_uses_locked_equal_volume_challenger(self):
        grades = ["miss", "hit", "hit", "hit"]
        pdur.materialize_outcomes(
            [outcome(r, g) for r, g in zip(self.records, grades)],
            self.root)
        result = pse.evaluate_plan(
            self.root, self.plan, bootstrap_samples=100, seed=3)
        per = result["per_slate"][0]
        self.assertEqual(per["selection_volume"], 2)
        self.assertEqual(per["champion"]["hit_rate"], 0.5)
        self.assertEqual(per["challenger"]["hit_rate"], 1.0)
        self.assertEqual(per["realized_hit_rate_delta"], 0.5)
        self.assertEqual(result["aggregate"]["selection_volume"], 2)
        self.assertIsNone(result["promotion_verdict"])

    def test_incomplete_outcomes_fail_closed_in_aggregate(self):
        pdur.materialize_outcomes(
            [outcome(self.records[0], "hit")], self.root)
        with self.assertRaises(Exception):
            pse.evaluate_plan(
                self.root, self.plan, bootstrap_samples=10)

    def test_plan_file_round_trip_preserves_fingerprint(self):
        path = os.path.join(self.root, "plan.json")
        pse.write_plan(self.plan, path)
        loaded = pse.read_plan(path)
        self.assertEqual(
            loaded["plan_fingerprint"],
            self.plan["plan_fingerprint"])
        self.assertTrue(pse.validate_plan(self.root, loaded))


if __name__ == "__main__":
    unittest.main(verbosity=2)

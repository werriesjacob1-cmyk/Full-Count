#!/usr/bin/env python3
"""Adversarial tests for immutable HR contact-state Stage 2."""
from __future__ import annotations

import copy
import os
import tempfile
import unittest

from backtest.experiment_primitives import (
    build_prediction_freeze,
    deterministic_sha256,
    select_top_k_per_date,
)
from backtest.hr_contact_state_stage2 import (
    BOOTSTRAP_SEED,
    HRStage2IntegrityError,
    _arm_survival,
    evaluate_hr_stage2,
    robustness_removal_audit,
    stability_tables,
    verify_stage1_bundle,
    write_immutable_evaluation_report,
)


def identity(day, game, player):
    return (day, game, player, "home_run", 0.5)


def population_row(day, game, player, current, challenger, *, team=None, park=None, supported=True):
    return {
        "date": day,
        "game_pk": game,
        "player_id": player,
        "prop_type": "home_run",
        "line": 0.5,
        "team": team or f"T{player % 3}",
        "venue_id": park if park is not None else 100 + game,
        "current_prob": current,
        "challenger_prob": challenger,
        "supported": supported,
        "prediction_path": "contact_state_model" if supported else "champion_fallback",
        "raw_features": {},
        "standardized_features": {} if supported else None,
    }


def stage1_bundle():
    # Six candidates -> top-five capacity. Challenger swaps player 1 out and
    # player 6 in, creating one pre-outcome added and one removed identity.
    rows = [
        population_row("2026-05-01", 10, 1, 0.30, 0.01, team="A", park=1),
        population_row("2026-05-01", 10, 2, 0.29, 0.29, team="A", park=1),
        population_row("2026-05-01", 11, 3, 0.28, 0.28, team="B", park=2),
        population_row("2026-05-01", 11, 4, 0.27, 0.27, team="B", park=2),
        population_row("2026-05-01", 12, 5, 0.26, 0.26, team="C", park=3),
        population_row("2026-05-01", 12, 6, 0.10, 0.99, team="C", park=3),
    ]
    venue_records = [
        {"game_pk": 10, "venue_id": 1, "venue_name": "P1"},
        {"game_pk": 11, "venue_id": 2, "venue_name": "P2"},
        {"game_pk": 12, "venue_id": 3, "venue_name": "P3"},
    ]
    venue_attestation = {
        "row_count": len(venue_records),
        "sha256": deterministic_sha256(venue_records),
        "records": venue_records,
    }
    venue_compact = {
        "row_count": venue_attestation["row_count"],
        "sha256": venue_attestation["sha256"],
    }

    arms = {}
    for arm in ("B", "C", "D"):
        selection = select_top_k_per_date(
            rows,
            5,
            champion_score_key="current_prob",
            challenger_score_key="challenger_prob",
        )
        arms[arm] = build_prediction_freeze(
            rows,
            selection,
            metadata={
                "arm": arm,
                "k_primary": 5,
                "runner_code_sha": "runner-sha",
                "canonical_artifact_identity": {"sha256": "canonical-sha"},
                "source_artifact_identity": {"sha256": "source-sha"},
                "venue_map_attestation": venue_compact,
                "coverage": {"supported_n": 6},
            },
        )
    hashes = {arm: arms[arm]["sha256"] for arm in ("B", "C", "D")}
    bundle = {
        "arms": arms,
        "coverage": {
            "per_arm_supported_n": {"B": 6, "C": 6, "D": 6},
        },
        "venue_map_attestation": venue_attestation,
        "freeze_set_sha256": deterministic_sha256(hashes),
    }
    bundle["bundle_sha256"] = deterministic_sha256(bundle)
    return bundle


def evaluation_rows():
    # Player 6 (added) hits; player 1 (removed) misses.
    outcomes = {1: 0, 2: 1, 3: 0, 4: 1, 5: 0, 6: 1}
    current = {1: .30, 2: .29, 3: .28, 4: .27, 5: .26, 6: .10}
    teams = {1: "A", 2: "A", 3: "B", 4: "B", 5: "C", 6: "C"}
    games = {1: 10, 2: 10, 3: 11, 4: 11, 5: 12, 6: 12}
    return [
        {
            "date": "2026-05-01",
            "game_pk": games[player],
            "player_id": player,
            "prop_type": "home_run",
            "line": 0.5,
            "team": teams[player],
            "predicted_prob": current[player],
            "outcome": outcomes[player],
        }
        for player in range(1, 7)
    ]


class FreezeVerificationTests(unittest.TestCase):
    def test_clean_stage1_bundle_verifies(self):
        verified = verify_stage1_bundle(stage1_bundle())
        self.assertEqual(verified["b_support_count"], 6)
        self.assertEqual(set(verified["freeze_hashes"]), {"B", "C", "D"})

    def test_tampered_prediction_payload_aborts(self):
        bundle = stage1_bundle()
        bundle["arms"]["B"]["payload"]["population"][0]["challenger_prob"] = 0.777
        with self.assertRaises(HRStage2IntegrityError):
            verify_stage1_bundle(bundle)

    def test_tampered_preoutcome_added_set_aborts_even_if_hash_rewritten(self):
        bundle = stage1_bundle()
        freeze = bundle["arms"]["B"]
        freeze["payload"]["selection"]["added_ids"] = []
        freeze["sha256"] = deterministic_sha256(freeze["payload"])
        bundle["freeze_set_sha256"] = deterministic_sha256({
            arm: bundle["arms"][arm]["sha256"]
            for arm in ("B", "C", "D")
        })
        logical = dict(bundle)
        logical.pop("bundle_sha256", None)
        bundle["bundle_sha256"] = deterministic_sha256(logical)
        with self.assertRaises(HRStage2IntegrityError):
            verify_stage1_bundle(bundle)


class OutcomeJoinTests(unittest.TestCase):
    def test_probability_change_after_freeze_aborts(self):
        rows = evaluation_rows()
        rows[0]["predicted_prob"] = 0.3000001
        with self.assertRaises(HRStage2IntegrityError):
            evaluate_hr_stage2(rows, stage1_bundle())

    def test_population_change_after_freeze_aborts(self):
        rows = evaluation_rows()[:-1]
        with self.assertRaises(HRStage2IntegrityError):
            evaluate_hr_stage2(rows, stage1_bundle())

    def test_stage2_uses_frozen_selection_and_locked_seed(self):
        report = evaluate_hr_stage2(evaluation_rows(), stage1_bundle())
        self.assertEqual(report["bootstrap"]["seed"], BOOTSTRAP_SEED)
        self.assertEqual(report["bootstrap"]["replicates"], 5000)
        self.assertEqual(report["arm_b_supported_holdout_n"], 6)
        self.assertFalse(report["production_promotion_authorized"])
        self.assertTrue(report["historical_evidence_only"])
        self.assertEqual(report["initial_survivors"], [])
        self.assertFalse(report["arm_e_permitted"])
        self.assertTrue(report["thread_closes_without_e"])
        for arm in ("B", "C", "D"):
            anatomy = report["arms"][arm]["selection_anatomy"]
            self.assertEqual(anatomy["added_n"], 1)
            self.assertEqual(anatomy["removed_n"], 1)
            self.assertEqual(anatomy["realized_winner_delta"], 1)
            self.assertFalse(report["arms"][arm]["survival"]["earns_continuation"])


class RobustnessTests(unittest.TestCase):
    def test_largest_contributor_sign_flip_and_unresolvable_axis(self):
        added = [
            identity("2026-05-01", 1, 1),
            identity("2026-05-01", 2, 2),
        ]
        removed = [
            identity("2026-05-01", 3, 3),
            identity("2026-06-01", 4, 4),
        ]
        selection = {"added_ids": added, "removed_ids": removed}
        frozen = {
            added[0]: {"player_id": 1, "team": "A", "venue_id": 10, "date": "2026-05-01"},
            added[1]: {"player_id": 2, "team": "B", "venue_id": 20, "date": "2026-05-01"},
            removed[0]: {"player_id": 3, "team": "A", "venue_id": 10, "date": "2026-05-01"},
            removed[1]: {"player_id": 4, "team": "B", "venue_id": 20, "date": "2026-06-01"},
        }
        eval_by_id = {
            added[0]: {"outcome": 1},
            added[1]: {"outcome": 1},
            removed[0]: {"outcome": 0},
            removed[1]: {"outcome": 1},
        }
        audit = robustness_removal_audit(selection, eval_by_id, frozen)
        self.assertAlmostEqual(audit["full_delta"], 0.5)
        self.assertTrue(audit["axes"]["player"]["sign_flip_to_nonpositive"])
        self.assertTrue(audit["axes"]["team"]["sign_flip_to_nonpositive"])
        self.assertTrue(audit["axes"]["park"]["sign_flip_to_nonpositive"])
        self.assertEqual(
            audit["axes"]["month"]["status"],
            "dependency_unresolvable",
        )
        self.assertTrue(audit["axes"]["month"]["stop_triggered"])

    def test_empty_changed_side_fails_every_axis_closed(self):
        audit = robustness_removal_audit(
            {"added_ids": [], "removed_ids": [identity("2026-05-01", 1, 1)]},
            {identity("2026-05-01", 1, 1): {"outcome": 0}},
            {identity("2026-05-01", 1, 1): {
                "player_id": 1, "team": "A", "venue_id": 1, "date": "2026-05-01"
            }},
        )
        for axis in ("player", "team", "park", "month"):
            self.assertEqual(audit["axes"][axis]["status"], "dependency_unresolvable")
            self.assertTrue(audit["axes"][axis]["stop_triggered"])


class StabilityTests(unittest.TestCase):
    def test_probability_bands_use_frozen_champion_probability(self):
        c1 = identity("2026-05-01", 1, 1)
        c2 = identity("2026-05-01", 2, 2)
        selection = {
            "champion_ids": [c1],
            "challenger_ids": [c2],
        }
        frozen = {
            c1: {"date": "2026-05-01", "current_prob": 0.049, "challenger_prob": 0.99},
            c2: {"date": "2026-05-01", "current_prob": 0.31, "challenger_prob": 0.01},
        }
        eval_by_id = {c1: {"outcome": 0}, c2: {"outcome": 1}}
        tables = stability_tables(selection, eval_by_id, frozen)
        bands = {row["band"]: row for row in tables["champion_probability_band"]}
        self.assertEqual(bands["[0.00, 0.05)"]["champion"]["n"], 1)
        self.assertEqual(bands["[0.30, 1.00]"]["challenger"]["n"], 1)
        self.assertEqual(bands["[0.00, 0.05)"]["challenger"]["n"], 0)


class SurvivalRuleTests(unittest.TestCase):
    def resolved_robustness(self):
        return {
            "axes": {
                axis: {
                    "status": "resolved",
                    "sign_flip_to_nonpositive": False,
                }
                for axis in ("player", "team", "park", "month")
            }
        }

    def test_strictly_positive_changed_ci_and_500_support_survives(self):
        result = _arm_survival(
            {"added_n": 20, "removed_n": 20},
            {
                "changed_estimable": True,
                "changed_valid_replicates": 5000,
                "added_minus_removed_ci95": [0.001, 0.15],
            },
            self.resolved_robustness(),
            500,
        )
        self.assertTrue(result["earns_continuation"])
        self.assertEqual(result["failures"], [])

    def test_ci_touching_zero_fails(self):
        result = _arm_survival(
            {"added_n": 20, "removed_n": 20},
            {
                "changed_estimable": True,
                "changed_valid_replicates": 5000,
                "added_minus_removed_ci95": [0.0, 0.15],
            },
            self.resolved_robustness(),
            500,
        )
        self.assertFalse(result["earns_continuation"])
        self.assertTrue(any("not strictly positive" in x for x in result["failures"]))

    def test_499_base_support_fails_even_with_strong_effect(self):
        result = _arm_survival(
            {"added_n": 20, "removed_n": 20},
            {
                "changed_estimable": True,
                "changed_valid_replicates": 5000,
                "added_minus_removed_ci95": [0.05, 0.20],
            },
            self.resolved_robustness(),
            499,
        )
        self.assertFalse(result["earns_continuation"])
        self.assertTrue(any("< 500" in x for x in result["failures"]))


class ImmutableWriterTests(unittest.TestCase):
    def test_report_can_be_written_once_only(self):
        report = {"x": 1}
        report["evaluation_report_sha256"] = deterministic_sha256(report)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "evaluation.json")
            first = write_immutable_evaluation_report(path, report)
            self.assertTrue(os.path.exists(path))
            self.assertGreater(first["bytes"], 0)
            with self.assertRaises(FileExistsError):
                write_immutable_evaluation_report(path, report)


if __name__ == "__main__":
    unittest.main(verbosity=2)

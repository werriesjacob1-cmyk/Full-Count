#!/usr/bin/env python3
"""Synthetic integrity tests for conditional Arm E."""
from __future__ import annotations

import unittest

from backtest.experiment_primitives import (
    build_prediction_freeze,
    deterministic_sha256,
    select_top_k_per_date,
)
from backtest.hr_contact_state_arm_e import (
    HRESequentialIntegrityError,
    build_hr_e_prediction_bundle,
    frozen_holdout_from_stage1,
    verify_stage2_trigger,
)
from backtest.hr_contact_state_stage2 import verify_stage1_bundle


def pop(day, game, player, current, challenger):
    return {
        "date": day,
        "game_pk": game,
        "player_id": player,
        "prop_type": "home_run",
        "line": 0.5,
        "team": f"T{player % 2}",
        "venue_id": 100 + game,
        "eligibility_score": 70.0,
        "current_prob": current,
        "challenger_prob": challenger,
        "supported": True,
        "prediction_path": "contact_state_model",
        "raw_features": {},
        "standardized_features": {},
    }


def initial_stage1_bundle():
    rows = [
        pop("2026-05-01", 10, 1, .30, .01),
        pop("2026-05-01", 10, 2, .29, .29),
        pop("2026-05-01", 11, 3, .28, .28),
        pop("2026-05-01", 11, 4, .27, .27),
        pop("2026-05-01", 12, 5, .26, .26),
        pop("2026-05-01", 12, 6, .10, .99),
    ]
    venue_records = [
        {"game_pk": 10, "venue_id": 110, "venue_name": "P10"},
        {"game_pk": 11, "venue_id": 111, "venue_name": "P11"},
        {"game_pk": 12, "venue_id": 112, "venue_name": "P12"},
    ]
    venue = {
        "row_count": len(venue_records),
        "sha256": deterministic_sha256(venue_records),
        "records": venue_records,
    }
    compact = {"row_count": venue["row_count"], "sha256": venue["sha256"]}

    arms = {}
    for arm in ("B", "C", "D"):
        selection = select_top_k_per_date(rows, 5)
        arms[arm] = build_prediction_freeze(
            rows,
            selection,
            metadata={
                "arm": arm,
                "k_primary": 5,
                "runner_code_sha": "a" * 40,
                "canonical_artifact_identity": {"artifact_sha256": "c" * 64},
                "source_artifact_identity": {"content_sha256": "s" * 64},
                "venue_map_attestation": compact,
                "coverage": {"supported_n": 6},
            },
        )

    hashes = {arm: arms[arm]["sha256"] for arm in ("B", "C", "D")}
    bundle = {
        "arms": arms,
        "coverage": {"per_arm_supported_n": {"B": 6, "C": 6, "D": 6}},
        "venue_map_attestation": venue,
        "freeze_set_sha256": deterministic_sha256(hashes),
    }
    bundle["bundle_sha256"] = deterministic_sha256(bundle)
    return bundle


def stage2_report(bundle, d_survives):
    report = {
        "experiment": "hr_contact_state",
        "stage": 2,
        "stage1_bundle_sha256": bundle["bundle_sha256"],
        "stage1_freeze_set_sha256": bundle["freeze_set_sha256"],
        "arms": {
            "B": {"survival": {"earns_continuation": False}},
            "C": {"survival": {"earns_continuation": False}},
            "D": {"survival": {"earns_continuation": d_survives}},
        },
        "initial_survivors": ["D"] if d_survives else [],
        "arm_e_permitted": bool(d_survives),
    }
    report["evaluation_report_sha256"] = deterministic_sha256(report)
    return report


class TriggerTests(unittest.TestCase):
    def test_d_failure_forbids_e(self):
        bundle = initial_stage1_bundle()
        verified = verify_stage1_bundle(bundle)
        report = stage2_report(bundle, False)
        with self.assertRaises(HRESequentialIntegrityError):
            verify_stage2_trigger(report, verified)

    def test_d_survival_permits_e_trigger_only(self):
        bundle = initial_stage1_bundle()
        verified = verify_stage1_bundle(bundle)
        report = stage2_report(bundle, True)
        self.assertEqual(
            verify_stage2_trigger(report, verified),
            report["evaluation_report_sha256"],
        )

    def test_tampered_stage2_report_cannot_trigger_e(self):
        bundle = initial_stage1_bundle()
        verified = verify_stage1_bundle(bundle)
        report = stage2_report(bundle, True)
        report["initial_survivors"].append("B")
        with self.assertRaises(HRESequentialIntegrityError):
            verify_stage2_trigger(report, verified)


class FrozenPopulationTests(unittest.TestCase):
    def test_e_population_reconstructs_only_from_initial_stage1(self):
        bundle = initial_stage1_bundle()
        verified = verify_stage1_bundle(bundle)
        rows = frozen_holdout_from_stage1(verified)
        self.assertEqual(len(rows), 6)
        for row in rows:
            self.assertNotIn("outcome", row)
            self.assertNotIn("actual", row)
            self.assertEqual(row["score"], 70.0)

    def test_runner_sha_change_aborts_before_e_fit(self):
        bundle = initial_stage1_bundle()
        report = stage2_report(bundle, True)
        with self.assertRaises(HRESequentialIntegrityError) as ctx:
            build_hr_e_prediction_bundle(
                training_rows=None,
                source_frame=None,
                initial_stage1_bundle=bundle,
                stage2_report=report,
                runner_code_sha="b" * 40,
            )
        self.assertIn("runner code SHA changed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)

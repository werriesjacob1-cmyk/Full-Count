#!/usr/bin/env python3
"""Synthetic Stage-1 tests for the integrated HR contact-state runner."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from backtest.hr_contact_state_stage1 import (
    ARM_FEATURES,
    DEFAULT_ARMS,
    HRStageIntegrityError,
    build_hr_prediction_freezes,
    fit_hr_arms,
    venue_map_attestation,
    write_immutable_stage1_bundle,
)


def statcast_for(players, *, n=35, date="2026-05-01"):
    rows = []
    order = 0
    for player in players:
        for i in range(n):
            order += 1
            rows.append({
                "game_date": date,
                "game_pk": 9000 + player,
                "batter": player,
                "at_bat_number": i + 1,
                "bat_speed": 68.0 + player * 0.01 + i * 0.02,
                "swing_length": 6.8 + player * 0.001 + i * 0.005,
                "attack_angle": 8.0 + player * 0.002 + i * 0.03,
                "swing_path_tilt": 18.0 + player * 0.002 + i * 0.02,
                "attack_direction": -2.0 + player * 0.001 + i * 0.01,
                "hit_distance_sc": 320.0 + player * 0.02 + i,
            })
    return pd.DataFrame(rows)


def holdout_row(day, game, player, prob, team="TST"):
    return {
        "date": day,
        "game_pk": game,
        "player_id": player,
        "player_name": f"Player {player}",
        "team": team,
        "prop_type": "home_run",
        "line": 0.5,
        "predicted_prob": prob,
    }


def manual_fitted_arms():
    fitted = {}
    for arm in DEFAULT_ARMS:
        width = len(ARM_FEATURES[arm])
        fitted[arm] = {
            "feature_names": ARM_FEATURES[arm],
            "supported_training_ids": [
                ("2025-07-01", 1, 1, "home_run", 0.5)
            ],
            "supported_training_n": 1,
            "training_population_n": 1,
            "fit": {
                "beta": np.zeros(width),
                "standardizer": {
                    "mean": np.zeros(width),
                    "std": np.ones(width),
                },
                "optimizer": {
                    "method": "L-BFGS-B",
                    "success": True,
                    "intercept": None,
                },
            },
        }
    return fitted


def venue_map(rows):
    return {
        int(row["game_pk"]): {
            "venue_id": 100 + int(row["game_pk"]),
            "venue_name": f"Park {row['game_pk']}",
        }
        for row in rows
    }


class ImmutableStageOneWriterTests(unittest.TestCase):
    def test_bundle_can_be_written_once_only(self):
        rows = [holdout_row("2026-05-02", 1, 1, 0.20)]
        bundle = build_hr_prediction_freezes(
            rows,
            statcast_for([1]),
            manual_fitted_arms(),
            venue_map(rows),
            runner_code_sha="runner",
            canonical_artifact_identity={"sha256": "canonical"},
            source_artifact_identity={"sha256": "source"},
        )
        logical = dict(bundle)
        embedded = logical.pop("bundle_sha256")
        self.assertEqual(embedded, __import__(
            "backtest.experiment_primitives",
            fromlist=["deterministic_sha256"],
        ).deterministic_sha256(logical))

        with __import__("tempfile").TemporaryDirectory() as tmp:
            path = __import__("os").path.join(tmp, "stage1.json")
            first = write_immutable_stage1_bundle(path, bundle)
            self.assertEqual(first["bundle_sha256"], bundle["bundle_sha256"])
            with self.assertRaises(FileExistsError):
                write_immutable_stage1_bundle(path, bundle)


class VenueTests(unittest.TestCase):
    def test_venue_attestation_is_order_independent(self):
        a = {
            2: {"venue_id": 20, "venue_name": "B"},
            1: {"venue_id": 10, "venue_name": "A"},
        }
        b = {
            1: {"venue_id": 10, "venue_name": "A"},
            2: {"venue_id": 20, "venue_name": "B"},
        }
        self.assertEqual(
            venue_map_attestation(a)["sha256"],
            venue_map_attestation(b)["sha256"],
        )


class StageOneTests(unittest.TestCase):
    def test_outcome_field_is_forbidden_even_when_none(self):
        rows = [holdout_row("2026-05-02", 1, 1, 0.20)]
        rows[0]["outcome"] = None
        with self.assertRaises(HRStageIntegrityError):
            build_hr_prediction_freezes(
                rows,
                statcast_for([1]),
                manual_fitted_arms(),
                venue_map(rows),
                runner_code_sha="runner",
                canonical_artifact_identity={"sha256": "canonical"},
                source_artifact_identity={"sha256": "source"},
            )

    def test_missing_venue_aborts_before_freeze(self):
        rows = [holdout_row("2026-05-02", 1, 1, 0.20)]
        with self.assertRaises(HRStageIntegrityError):
            build_hr_prediction_freezes(
                rows,
                statcast_for([1]),
                manual_fitted_arms(),
                {},
                runner_code_sha="runner",
                canonical_artifact_identity={"sha256": "canonical"},
                source_artifact_identity={"sha256": "source"},
            )

    def test_b_c_d_freeze_same_population_and_per_date_top_five(self):
        rows = []
        players = []
        for player in range(1, 8):
            players.append(player)
            rows.append(
                holdout_row(
                    "2026-05-02",
                    10,
                    player,
                    0.10 + player * 0.01,
                )
            )
        for player in range(8, 10):
            players.append(player)
            rows.append(
                holdout_row(
                    "2026-05-03",
                    11,
                    player,
                    0.10 + player * 0.01,
                )
            )

        result = build_hr_prediction_freezes(
            rows,
            statcast_for(players),
            manual_fitted_arms(),
            venue_map(rows),
            runner_code_sha="runner-sha",
            canonical_artifact_identity={"sha256": "canonical-sha"},
            source_artifact_identity={"sha256": "source-sha"},
        )

        self.assertEqual(set(result["arms"]), {"B", "C", "D"})
        champion_reference = None
        for arm, freeze in result["arms"].items():
            selection = freeze["payload"]["selection"]
            self.assertEqual(selection["dates"]["2026-05-02"]["selected_n"], 5)
            self.assertEqual(selection["dates"]["2026-05-03"]["selected_n"], 2)
            self.assertEqual(selection["population_n"], 9)
            self.assertEqual(len(selection["champion_ids"]), 7)
            self.assertEqual(len(selection["challenger_ids"]), 7)
            # beta=0 in this fixture => challenger equals champion and all
            # selected identities overlap before any outcome exists.
            self.assertEqual(selection["removed_ids"], [])
            self.assertEqual(selection["added_ids"], [])
            self.assertEqual(
                set(selection["overlap_ids"]),
                set(selection["champion_ids"]),
            )
            if champion_reference is None:
                champion_reference = selection["champion_ids"]
            else:
                self.assertEqual(
                    selection["champion_ids"],
                    champion_reference,
                    f"champion selection drifted in arm {arm}",
                )
            self.assertNotIn(
                "outcome",
                freeze["payload"]["population"][0],
            )

        self.assertEqual(result["coverage"]["population_n"], 9)
        self.assertEqual(result["coverage"]["per_arm_supported_n"], {
            "B": 9,
            "C": 9,
            "D": 9,
        })
        self.assertTrue(result["freeze_set_sha256"])

    def test_unsupported_candidate_remains_in_population_at_exact_champion_prob(self):
        rows = [
            holdout_row("2026-05-02", 10, 1, 0.21),
            holdout_row("2026-05-02", 10, 2, 0.19),
        ]
        source = pd.concat([
            statcast_for([1], n=35),
            statcast_for([2], n=20),
        ], ignore_index=True)

        result = build_hr_prediction_freezes(
            rows,
            source,
            manual_fitted_arms(),
            venue_map(rows),
            runner_code_sha="runner",
            canonical_artifact_identity={"sha256": "canonical"},
            source_artifact_identity={"sha256": "source"},
        )

        for arm in DEFAULT_ARMS:
            population = result["arms"][arm]["payload"]["population"]
            by_player = {row["player_id"]: row for row in population}
            self.assertEqual(len(population), 2)
            self.assertFalse(by_player[2]["supported"])
            self.assertEqual(
                by_player[2]["challenger_prob"],
                by_player[2]["current_prob"],
            )
            self.assertIsNone(by_player[2]["standardized_features"])

    def test_initial_stage_refuses_arm_e(self):
        training = [{
            "date": "2025-06-01",
            "game_pk": 1,
            "player_id": 1,
            "prop_type": "home_run",
            "line": 0.5,
            "predicted_prob": 0.20,
            "outcome": 1,
        }]
        with self.assertRaises(HRStageIntegrityError):
            fit_hr_arms(training, None, arms=("B", "C", "D", "E"))

    def test_2026_training_row_is_rejected_before_feature_access(self):
        training = [{
            "date": "2026-01-01",
            "game_pk": 1,
            "player_id": 1,
            "prop_type": "home_run",
            "line": 0.5,
            "predicted_prob": 0.20,
            "outcome": 1,
        }]
        with self.assertRaises(HRStageIntegrityError):
            fit_hr_arms(training, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)

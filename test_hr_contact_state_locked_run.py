#!/usr/bin/env python3
"""Synthetic gate tests for the locked HR execution wrapper."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest

from backtest.experiment_primitives import deterministic_sha256
from backtest.hr_contact_state_locked_run import (
    LockedRunGateError,
    _load_venue_map_artifact,
    build_venue_map_from_schedule_payloads,
    load_stage1_populations,
    load_stage2_holdout_truth,
    require_runner_sha,
    validate_authorization_record,
    validate_certification_report,
    validate_execution_gate,
)


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        h.update(handle.read())
    return h.hexdigest()


def write_json(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def good_cert(canonical_sha, source_sha):
    return {
        "verdict": "CANONICAL CERTIFIED",
        "failures": [],
        "blockers": [],
        "run_id": "canonical-test",
        "virtual_assembled_byte_sha256": canonical_sha,
        "observed_code_shas": ["a" * 40],
        "dataset_identity": {
            "strength": {"promotion_grade": True},
            "derived_manifest": {
                "manifest_schema_version": 2,
                "artifact_sha256": canonical_sha,
                "artifact_row_count": 2,
                "artifact_date_range": ["2025-01-01", "2026-05-01"],
                "code_git_sha_at_lock": "a" * 40,
            },
        },
        "source_schema_attestation": {
            "available": True,
            "content_sha256": source_sha,
            "row_count": 2,
            "schema_fingerprint": "schema",
            "schema_columns": ["game_date", "batter", "bat_speed"],
            "date_coverage": "2024-01-01..2026-05-01",
        },
    }


def good_auth(canonical_sha, stages=None):
    return {
        "authorized": True,
        "authorization_type": "explicit_user_authorization",
        "scope": "hr_contact_state_2026_holdout",
        "allowed_stages": stages or ["venue-map", "stage1", "stage2"],
        "canonical_artifact_sha256": canonical_sha,
        "authorization_reference": "explicit approval in FULL COUNT project conversation",
    }


class RunnerShaGateTests(unittest.TestCase):
    def test_exact_same_runner_sha_passes(self):
        sha_value = "a" * 40
        self.assertTrue(
            require_runner_sha(
                sha_value,
                sha_value,
                "Stage 1 -> Stage 2",
            )
        )

    def test_changed_or_missing_runner_sha_fails_closed(self):
        with self.assertRaises(LockedRunGateError):
            require_runner_sha(
                "b" * 40,
                "a" * 40,
                "Stage 1 -> Stage 2",
            )
        with self.assertRaises(LockedRunGateError):
            require_runner_sha(
                "a" * 40,
                None,
                "Stage 1 -> Stage 2",
            )


class CertificationGateTests(unittest.TestCase):
    def test_certified_promotion_grade_single_sha_report_passes(self):
        report = good_cert("c" * 64, "s" * 64)
        identity = validate_certification_report(report)
        self.assertEqual(identity["canonical_sha256"], "c" * 64)
        self.assertEqual(identity["source_sha256"], "s" * 64)

    def test_blocked_report_fails(self):
        report = good_cert("c" * 64, "s" * 64)
        report["verdict"] = "CERTIFICATION BLOCKED"
        with self.assertRaises(LockedRunGateError):
            validate_certification_report(report)

    def test_assembled_sha_disagreement_fails(self):
        report = good_cert("c" * 64, "s" * 64)
        report["virtual_assembled_byte_sha256"] = "d" * 64
        with self.assertRaises(LockedRunGateError):
            validate_certification_report(report)


class AuthorizationGateTests(unittest.TestCase):
    def test_explicit_matching_record_passes(self):
        self.assertTrue(
            validate_authorization_record(
                good_auth("c" * 64),
                stage="stage1",
                canonical_sha256="c" * 64,
            )
        )

    def test_unauthorized_template_cannot_run(self):
        record = good_auth("c" * 64)
        record["authorized"] = False
        with self.assertRaises(LockedRunGateError):
            validate_authorization_record(
                record,
                stage="stage1",
                canonical_sha256="c" * 64,
            )

    def test_wrong_artifact_or_stage_cannot_run(self):
        with self.assertRaises(LockedRunGateError):
            validate_authorization_record(
                good_auth("c" * 64),
                stage="stage1",
                canonical_sha256="d" * 64,
            )
        with self.assertRaises(LockedRunGateError):
            validate_authorization_record(
                good_auth("c" * 64, stages=["venue-map"]),
                stage="stage1",
                canonical_sha256="c" * 64,
            )


class FileBindingTests(unittest.TestCase):
    def test_execution_gate_rehashes_both_real_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = os.path.join(tmp, "rows.jsonl")
            source = os.path.join(tmp, "source.parquet")
            cert_path = os.path.join(tmp, "cert.json")
            auth_path = os.path.join(tmp, "auth.json")

            open(canonical, "wb").write(b"canonical-bytes")
            open(source, "wb").write(b"source-bytes")
            csha = file_sha(canonical)
            ssha = file_sha(source)
            write_json(cert_path, good_cert(csha, ssha))
            write_json(auth_path, good_auth(csha))

            gate = validate_execution_gate(
                cert_path,
                auth_path,
                canonical,
                source,
                stage="stage1",
            )
            self.assertEqual(gate["canonical_rows_sha256"], csha)
            self.assertEqual(gate["source_parquet_sha256"], ssha)

            open(canonical, "ab").write(b"-tampered")
            with self.assertRaises(LockedRunGateError):
                validate_execution_gate(
                    cert_path,
                    auth_path,
                    canonical,
                    source,
                    stage="stage1",
                )


class PopulationMaskTests(unittest.TestCase):
    def test_stage1_immediately_masks_2026_truth_and_reports_predeclared_exclusions(self):
        rows = [
            {
                "date": "2025-06-01",
                "game_pk": 1,
                "player_id": 1,
                "player_name": "Train",
                "team": "A",
                "prop_type": "home_run",
                "line": 0.5,
                "predicted_prob": 0.20,
                "score": 70.0,
                "outcome": 1,
                "actual": 1,
            },
            {
                "date": "2026-05-01",
                "game_pk": 2,
                "player_id": 2,
                "player_name": "Holdout",
                "team": "B",
                "prop_type": "home_run",
                "line": 0.5,
                "predicted_prob": 0.19,
                "score": 68.0,
                "outcome": 0,
                "actual": 0,
                "actual_pa": 4,
                "fair_test": True,
            },
            {
                "date": "2026-05-01",
                "game_pk": 2,
                "player_id": 3,
                "player_name": "MissingScore",
                "team": "B",
                "prop_type": "home_run",
                "line": 0.5,
                "predicted_prob": 0.18,
                "score": None,
                "outcome": 1,
            },
            {
                "date": "2026-05-01",
                "game_pk": 2,
                "player_id": 4,
                "team": "B",
                "prop_type": "hits",
                "line": 0.5,
                "predicted_prob": 0.70,
                "score": 70.0,
                "outcome": 1,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rows.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")

            loaded = load_stage1_populations(path)
            self.assertEqual(len(loaded["training"]), 1)
            self.assertEqual(len(loaded["masked_holdout"]), 1)
            masked = loaded["masked_holdout"][0]
            for forbidden in ("outcome", "actual", "actual_pa", "fair_test"):
                self.assertNotIn(forbidden, masked)
            self.assertEqual(masked["score"], 68.0)
            self.assertEqual(
                loaded["counts"]["excluded_home_run_rows"]["missing_score"],
                1,
            )

            truth = load_stage2_holdout_truth(path)
            self.assertEqual(len(truth["holdout"]), 1)
            self.assertEqual(truth["holdout"][0]["outcome"], 0)


class VenueMapTests(unittest.TestCase):
    def test_only_required_game_identity_and_venue_are_materialized(self):
        payloads = [{
            "dates": [{
                "games": [
                    {
                        "gamePk": 100,
                        "venue": {"id": 10, "name": "Park A"},
                        "status": {"detailedState": "Final"},
                        "linescore": {"teams": {"home": {"runs": 12}}},
                    },
                    {
                        "gamePk": 101,
                        "venue": {"id": 11, "name": "Park B"},
                    },
                ]
            }]
        }]
        result = build_venue_map_from_schedule_payloads(payloads, {100})
        self.assertEqual(
            result,
            {100: {"venue_id": 10, "venue_name": "Park A"}},
        )

    def test_missing_or_conflicting_required_venue_fails_closed(self):
        with self.assertRaises(LockedRunGateError):
            build_venue_map_from_schedule_payloads(
                [{"dates": [{"games": [{"gamePk": 100, "venue": {}}]}]}],
                {100},
            )

        with self.assertRaises(LockedRunGateError):
            build_venue_map_from_schedule_payloads(
                [
                    {"dates": [{"games": [{"gamePk": 100, "venue": {"id": 10, "name": "A"}}]}]},
                    {"dates": [{"games": [{"gamePk": 100, "venue": {"id": 11, "name": "B"}}]}]},
                ],
                {100},
            )


class VenueArtifactTests(unittest.TestCase):
    def test_venue_artifact_is_hash_bound_to_canonical_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "venue.json")
            payload = {
                "scope": "hr_contact_state_2026_holdout",
                "canonical_rows_sha256": "c" * 64,
                "authorization_file_sha256": "a" * 64,
                "venue_map": {"100": {"venue_id": 10, "venue_name": "A"}},
            }
            payload["logical_sha256"] = deterministic_sha256(payload)
            write_json(path, payload)
            venue, artifact = _load_venue_map_artifact(
                path,
                canonical_sha256="c" * 64,
            )
            self.assertEqual(venue[100]["venue_id"], 10)
            self.assertEqual(artifact["logical_sha256"], payload["logical_sha256"])

            with self.assertRaises(LockedRunGateError):
                _load_venue_map_artifact(
                    path,
                    canonical_sha256="d" * 64,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

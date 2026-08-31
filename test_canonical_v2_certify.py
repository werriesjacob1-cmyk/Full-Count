#!/usr/bin/env python3
"""Adversarial tests for canonical-v2 independent certification."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from backtest import canonical_v2_certify as cert


GEN_SHA = "a" * 40
PARENT_SHA = "b" * 40
SOURCE_COLS = sorted([
    "attack_angle",
    "attack_direction",
    "bat_speed",
    "game_date",
    "hit_distance_sc",
    "swing_length",
    "swing_path_tilt",
])


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def write_gzip(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            gz.write(body)


class PackageFactory:
    def __init__(self, root):
        self.root = root
        self.day = "2025-08-20"

    def build(
        self,
        *,
        statsapi_failure=False,
        mlbcom_bad_date=False,
        current_team_request=False,
    ):
        source_dir = os.path.join(self.root, "source")
        meta_dir = os.path.join(self.root, "date_metadata")
        blob_dir = os.path.join(self.root, "http_blobs")
        os.makedirs(source_dir, exist_ok=True)
        os.makedirs(meta_dir, exist_ok=True)
        os.makedirs(blob_dir, exist_ok=True)

        source_path = os.path.join(
            source_dir,
            "statcast_2024_through_2026-08-24.parquet",
        )
        frame = pd.DataFrame({
            "game_date": [self.day],
            "bat_speed": [72.0],
            "swing_length": [7.1],
            "attack_angle": [12.0],
            "swing_path_tilt": [4.0],
            "attack_direction": [1.0],
            "hit_distance_sc": [410.0],
        })
        frame.to_parquet(source_path, index=False)
        source_sha = cert.sha256_file(source_path)
        schema_fp = cert.sha256_bytes(",".join(SOURCE_COLS).encode())

        outcome_cols = sorted(cert.OUTCOME_ONLY_SOURCE_COLUMNS)
        outcome_path = os.path.join(
            source_dir,
            f"statcast_outcome_{self.day}.parquet",
        )
        outcome_frame = pd.DataFrame({
            "game_date": [self.day],
            "game_pk": [123],
            "batter": [10],
            "events": ["home_run"],
            "launch_speed": [108.0],
            "hit_distance_sc": [425.0],
        })[outcome_cols]
        outcome_frame.to_parquet(outcome_path, index=False)
        outcome_sha = cert.sha256_file(outcome_path)
        outcome_schema_fp = cert.sha256_bytes(
            ",".join(outcome_cols).encode()
        )

        row = {
            "date": self.day,
            "game_pk": 123,
            "player_id": 10,
            "prop_type": "home_run",
            "line": 0.5,
            "predicted_prob": 0.18,
            "outcome": 1,
            "code_git_sha": GEN_SHA,
        }
        rows_raw = (
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        rows_path = os.path.join(self.root, "rows.jsonl")
        with open(rows_path, "wb") as handle:
            handle.write(rows_raw)

        stats_payload = {
            "dates": [{
                "date": self.day,
                "games": [{
                    "gamePk": 123,
                    "gameType": "R",
                    "officialDate": self.day,
                    "gameDate": f"{self.day}T23:05:00Z",
                    "teams": {
                        "away": {"team": {"id": 101}},
                        "home": {"team": {"id": 102}},
                    },
                    "status": {
                        "codedGameState": "S",
                        "statusCode": "S",
                        "detailedState": "Scheduled",
                    },
                }],
            }],
        }
        stats_body = json.dumps(
            stats_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        stats_body_sha = sha(stats_body)
        write_gzip(
            os.path.join(blob_dir, f"{stats_body_sha}.gz"),
            stats_body,
        )

        team_payload = {
            "teams": [
                {
                    "id": 100 + index,
                    "name": f"Historical Team {index}",
                    "abbreviation": f"T{index:02d}",
                }
                for index in range(1, 31)
            ]
        }
        team_body = json.dumps(
            team_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        team_body_sha = sha(team_body)
        write_gzip(
            os.path.join(blob_dir, f"{team_body_sha}.gz"),
            team_body,
        )

        stats_rows = [{
            "observed_date": self.day,
            "scientific_phase": "predictive_input",
            "method": "GET",
            "url": (
                "https://statsapi.mlb.com/api/v1/teams"
                "?sportId=1&season=2025"
            ),
            "request_body_sha256": None,
            "status_code": 200,
            "response_sha256": team_body_sha,
            "response_bytes": len(team_body),
            "exception_type": None,
        }]
        if statsapi_failure:
            stats_rows.append({
                "observed_date": self.day,
                "scientific_phase": "predictive_input",
                "method": "GET",
                "url": (
                    "https://statsapi.mlb.com/api/v1/schedule"
                    f"?date={self.day}&sportId=1"
                ),
                "request_body_sha256": None,
                "status_code": 503,
                "response_sha256": None,
                "response_bytes": None,
                "exception_type": None,
            })
        else:
            stats_rows.append({
                "observed_date": self.day,
                "scientific_phase": "predictive_input",
                "method": "GET",
                "url": (
                    "https://statsapi.mlb.com/api/v1/schedule"
                    f"?date={self.day}&sportId=1"
                ),
                "request_body_sha256": None,
                "status_code": 200,
                "response_sha256": stats_body_sha,
                "response_bytes": len(stats_body),
                "exception_type": None,
            })

        if current_team_request:
            stats_rows.append({
                "observed_date": self.day,
                "scientific_phase": "predictive_input",
                "method": "GET",
                "url": (
                    "https://statsapi.mlb.com/api/v1/teams"
                    "?activeStatus=Y&sportIds=1&season=2026"
                ),
                "request_body_sha256": None,
                "status_code": 200,
                "response_sha256": stats_body_sha,
                "response_bytes": len(stats_body),
                "exception_type": None,
            })

        mlb_body = b"<html>dated lineup</html>"
        mlb_body_sha = sha(mlb_body)
        write_gzip(
            os.path.join(blob_dir, f"{mlb_body_sha}.gz"),
            mlb_body,
        )
        fallback_day = "2025-08-19" if mlbcom_bad_date else self.day
        mlb_rows = [{
            "observed_date": self.day,
            "scientific_phase": "predictive_input",
            "method": "GET",
            "url": f"https://www.mlb.com/starting-lineups/{fallback_day}",
            "request_body_sha256": None,
            "status_code": 200,
            "response_sha256": mlb_body_sha,
            "response_bytes": len(mlb_body),
            "exception_type": None,
        }]

        def ledger(path, rows):
            raw = b"".join(
                (
                    json.dumps(r, sort_keys=True, separators=(",", ":"))
                    + "\n"
                ).encode()
                for r in rows
            )
            with open(path, "wb") as handle:
                handle.write(raw)
            return raw

        stats_name = "mlb_statsapi_request_ledger.jsonl"
        mlb_name = "mlbcom_dated_lineup_request_ledger.jsonl"
        stats_raw = ledger(os.path.join(self.root, stats_name), stats_rows)
        mlb_raw = ledger(os.path.join(self.root, mlb_name), mlb_rows)

        lineage = [
            {
                "source": "statcast_leaguewide",
                "request_identity": "statcast:2024:through=2026-08-24",
                "content_sha256": source_sha,
                "row_count": 1,
                "schema_columns": SOURCE_COLS,
                "schema_fingerprint": schema_fp,
                "date_coverage": f"{self.day}..{self.day}",
                "cache_mode": "frozen_exact_artifact",
            },
            {
                "source": "statcast_outcome_only",
                "request_identity": f"statcast:outcome-only:{self.day}",
                "content_sha256": outcome_sha,
                "row_count": 1,
                "schema_columns": outcome_cols,
                "schema_fingerprint": outcome_schema_fp,
                "date_coverage": f"{self.day}..{self.day}",
                "cache_mode": "frozen_exact_artifact_grader_only",
            },
            {
                "source": "mlb_statsapi_request_ledger",
                "request_identity": "mlb_statsapi_request_ledger:v2-test",
                "content_sha256": sha(stats_raw),
                "row_count": len(stats_rows),
                "schema_fingerprint": "s" * 64,
                "date_coverage": f"{self.day}..{self.day}",
                "cache_mode": "generation_time_content_hash_ledger",
                "notes": f"path={stats_name} bodies=http_blobs/",
            },
            {
                "source": "mlbcom_dated_lineup_request_ledger",
                "request_identity": "mlbcom_dated_lineup_request_ledger:v2-test",
                "content_sha256": sha(mlb_raw),
                "row_count": len(mlb_rows),
                "schema_fingerprint": "m" * 64,
                "date_coverage": f"{self.day}..{self.day}",
                "cache_mode": "generation_time_content_hash_ledger",
                "notes": f"path={mlb_name} bodies=http_blobs/",
            },
        ]

        environment = {
            "python_version": cert.EXPECTED_PYTHON,
            "python_implementation": "CPython",
            "critical_packages": dict(cert.EXPECTED_CRITICAL_PACKAGES),
            "pip_freeze_sha256": "f" * 64,
            "environment_fingerprint": "e" * 64,
        }

        identity = {
            "run_id": "v2-test",
            "requested_start_date": self.day,
            "requested_end_date": self.day,
            "scientific_parent_sha": PARENT_SHA,
            "generation_code_sha": GEN_SHA,
            "weather_mode": "no_weather",
            "bullpen_mode": "enabled",
            "policy_replay": False,
            "strict_historical_lineups": True,
            "historical_allowed_game_types": sorted(
                cert.CANONICAL_ALLOWED_GAME_TYPES
            ),
            "historical_excluded_game_types": sorted(
                cert.CANONICAL_EXCLUDED_GAME_TYPES
            ),
            "historical_unknown_game_types_fail_closed": True,
            "http_strict_host_firewall": True,
            "http_response_content_bound": True,
            "http_scientific_phase_bound": True,
            "http_identical_get_cache": True,
            "historical_team_identity": (
                "schedule_team_ids_plus_season_directory"
            ),
            "historical_bullpen_temporal_gate": (
                "official_date_before_D_current_terminal_plus_team_pregame_timecode_v2"
            ),
            "historical_bullpen_boxscore_cutoff": (
                "earliest_simulated_D_team_first_pitch_minus_1_second_utc"
            ),
            "outcome_only_date": self.day,
            "outcome_source_isolation": "grader_only_external_parquet_v1",
            "outcome_statcast_source": {
                "content_sha256": outcome_sha,
                "row_count": 1,
                "schema_columns": outcome_cols,
                "schema_fingerprint": outcome_schema_fp,
                "date_coverage": f"{self.day}..{self.day}",
            },
            "statsapi_source_shape_policy": cert.STATSAPI_SOURCE_SHAPE_POLICY,
            "statcast_source": {
                "content_sha256": source_sha,
                "row_count": 1,
                "schema_columns": SOURCE_COLS,
                "schema_fingerprint": schema_fp,
                "date_coverage": f"{self.day}..{self.day}",
            },
        }

        meta = {
            "date": self.day,
            "status": "ok",
            "row_count": 1,
            "generation_code_sha": GEN_SHA,
            "source_content_sha256": source_sha,
            "outcome_source_content_sha256": outcome_sha,
            "outcome_source_schema_fingerprint": outcome_schema_fp,
            "ungraded_reasons": {},
            "http_provenance": {
                "strict_host_firewall": True,
                "firewall_block_count": 0,
            },
            "point_in_time_access": {
                "violations": [],
            },
        }
        write_json(os.path.join(meta_dir, f"{self.day}.json"), meta)

        report = {
            "verdict": "CANONICAL_V2_CONSOLIDATED",
            "run_id": "v2-test",
            "requested_date_range": [self.day, self.day],
            "requested_dates": 1,
            "status_counts": {"ok": 1},
            "total_rows": 1,
            "unique_candidate_identities": 1,
            "assembled_rows_sha256": sha(rows_raw),
            "identity": identity,
            "generation_code_sha": GEN_SHA,
            "scientific_parent_sha": PARENT_SHA,
            "scientific_environment": environment,
            "statcast_source_sha256": source_sha,
            "statcast_source_path": (
                "source/statcast_2024_through_2026-08-24.parquet"
            ),
            "outcome_statcast_source_sha256": outcome_sha,
            "outcome_statcast_source_path": f"source/statcast_outcome_{self.day}.parquet",
            "date_metadata_path": "date_metadata",
            "source_lineage": lineage,
            "source_lineage_fingerprint": cert.source_lineage_fingerprint(
                lineage
            ),
            "http_totals": {
                "response_body_directory": "http_blobs",
            },
        }
        report["report_sha256"] = cert.sha256_bytes(
            json.dumps(
                report,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        )
        write_json(
            os.path.join(self.root, "consolidation_report.json"),
            report,
        )
        return {
            "source_sha": source_sha,
            "outcome_sha": outcome_sha,
            "stats_body_sha": stats_body_sha,
            "mlb_body_sha": mlb_body_sha,
            "team_body_sha": team_body_sha,
        }


def clean_code_audit(*args, **kwargs):
    return {
        "failures": [],
        "blockers": [],
        "changed_files": [
            "backtest/canonical_v2_shard.py",
        ],
        "protected_files": list(cert.PROTECTED_SCIENTIFIC_FILES),
    }


class CertificationTests(unittest.TestCase):
    def certify(self, root):
        with patch.object(cert, "code_audit", side_effect=clean_code_audit):
            return cert.certify(
                root,
                repo_root=root,
                expected_parent_sha=PARENT_SHA,
            )

    def test_clean_self_contained_package_certifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"],
                "CANONICAL CERTIFIED",
                msg=json.dumps(result, indent=2),
            )
            self.assertEqual(result["failures"], [])
            self.assertEqual(result["blockers"], [])

    def test_missing_historical_team_identity_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            path = os.path.join(tmp, "consolidation_report.json")
            report = json.load(open(path, encoding="utf-8"))
            report["identity"].pop("historical_team_identity")
            report.pop("report_sha256", None)
            report["report_sha256"] = cert.sha256_bytes(
                json.dumps(
                    report,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            )
            write_json(path, report)
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any("historical team identity" in f for f in result["failures"])
            )

    def test_corrupted_archived_response_body_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            ids = PackageFactory(tmp).build()
            path = os.path.join(
                tmp,
                "http_blobs",
                f"{ids['stats_body_sha']}.gz",
            )
            write_gzip(path, b"corrupted")
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any("response body SHA mismatch" in f for f in result["failures"])
            )

    def test_point_in_time_violation_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            factory = PackageFactory(tmp)
            factory.build()
            path = os.path.join(
                tmp,
                "date_metadata",
                f"{factory.day}.json",
            )
            meta = json.load(open(path, encoding="utf-8"))
            meta["point_in_time_access"]["violations"] = [{
                "reason": "read touched simulated date"
            }]
            write_json(path, meta)
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any("point-in-time" in f for f in result["failures"])
            )

    def test_non_date_bound_mlbcom_fallback_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build(mlbcom_bad_date=True)
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any(
                    "MLB.com fallback URL is not date-bound" in f
                    for f in result["failures"]
                )
            )

    def test_current_team_directory_in_historical_replay_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build(current_team_request=True)
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any(
                    "current/active team directory" in failure
                    for failure in result["failures"]
                )
            )


    def test_unrelated_statsapi_body_is_verified_without_semantic_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()

            # Player metadata is an allowed historical request shape, but its
            # response content is not one of the bodies certification needs to
            # interpret semantically.  Make it intentionally large enough that
            # eager corpus-wide JSON materialization would be the wrong design.
            unrelated_body = json.dumps({
                "people": [{"id": 10, "fullName": "Test Player"}],
                "padding": "x" * (2 * 1024 * 1024),
            }, sort_keys=True, separators=(",", ":")).encode()
            unrelated_sha = sha(unrelated_body)
            write_gzip(
                os.path.join(tmp, "http_blobs", f"{unrelated_sha}.gz"),
                unrelated_body,
            )

            def mutate(rows):
                rows.append({
                    "observed_date": "2025-08-20",
                    "scientific_phase": "predictive_input",
                    "method": "GET",
                    "url": "https://statsapi.mlb.com/api/v1/people?personIds=10",
                    "request_body_sha256": None,
                    "status_code": 200,
                    "response_sha256": unrelated_sha,
                    "response_bytes": len(unrelated_body),
                    "exception_type": None,
                })

            self._rewrite_ledger(
                tmp,
                "mlb_statsapi_request_ledger",
                mutate,
                bind=True,
            )

            original = cert.load_archived_json
            semantic_decodes = []

            def audited_decode(blob_dir, response_sha):
                semantic_decodes.append(response_sha)
                if response_sha == unrelated_sha:
                    raise AssertionError(
                        "unrelated player-metadata body was semantically decoded"
                    )
                return original(blob_dir, response_sha)

            with patch.object(
                cert,
                "load_archived_json",
                side_effect=audited_decode,
            ):
                result = self.certify(tmp)

            self.assertEqual(
                result["verdict"],
                "CANONICAL CERTIFIED",
                msg=json.dumps(result, indent=2),
            )
            self.assertNotIn(unrelated_sha, semantic_decodes)
            self.assertIn(unrelated_sha, {
                row["response_sha256"]
                for row in self._rewrite_ledger(
                    tmp,
                    "mlb_statsapi_request_ledger",
                    lambda rows: None,
                    bind=False,
                )
                if row.get("response_sha256")
            })

    def test_unseen_statsapi_shape_blocks_certification(self):
        with tempfile.TemporaryDirectory() as tmp:
            factory = PackageFactory(tmp)
            factory.build()
            ledger_path = os.path.join(
                tmp,
                "mlb_statsapi_request_ledger.jsonl",
            )
            rows = [
                json.loads(line)
                for line in open(ledger_path, encoding="utf-8")
                if line.strip()
            ]
            extra = dict(rows[0])
            extra["url"] = (
                "https://statsapi.mlb.com/api/v1/unknown-scientific-endpoint"
            )
            rows.append(extra)
            raw = b"".join(
                (
                    json.dumps(row, sort_keys=True, separators=(",", ":"))
                    + "\n"
                ).encode()
                for row in rows
            )
            with open(ledger_path, "wb") as handle:
                handle.write(raw)

            report_path = os.path.join(tmp, "consolidation_report.json")
            report = json.load(open(report_path, encoding="utf-8"))
            record = next(
                item for item in report["source_lineage"]
                if item["source"] == "mlb_statsapi_request_ledger"
            )
            record["content_sha256"] = sha(raw)
            record["row_count"] = len(rows)
            report["source_lineage_fingerprint"] = (
                cert.source_lineage_fingerprint(report["source_lineage"])
            )
            report.pop("report_sha256", None)
            report["report_sha256"] = cert.sha256_bytes(
                json.dumps(
                    report,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            )
            write_json(report_path, report)

            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "CERTIFICATION BLOCKED")
            self.assertTrue(
                any(
                    "previously unseen StatsAPI request shape" in blocker
                    for blocker in result["blockers"]
                )
            )

    def test_same_request_identity_with_two_successful_bodies_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            factory = PackageFactory(tmp)
            ids = factory.build()

            ledger_path = os.path.join(
                tmp,
                "mlb_statsapi_request_ledger.jsonl",
            )
            rows = [
                json.loads(line)
                for line in open(ledger_path, encoding="utf-8")
                if line.strip()
            ]
            schedule = next(
                row for row in rows
                if "/api/v1/schedule" in row["url"]
            )

            divergent_body = b'{"dates":[{"date":"2025-08-20","games":[{"gamePk":999}]}]}'
            divergent_sha = sha(divergent_body)
            write_gzip(
                os.path.join(tmp, "http_blobs", f"{divergent_sha}.gz"),
                divergent_body,
            )
            duplicate = dict(schedule)
            duplicate["response_sha256"] = divergent_sha
            duplicate["response_bytes"] = len(divergent_body)
            rows.append(duplicate)

            raw = b"".join(
                (
                    json.dumps(row, sort_keys=True, separators=(",", ":"))
                    + "\n"
                ).encode()
                for row in rows
            )
            with open(ledger_path, "wb") as handle:
                handle.write(raw)

            report_path = os.path.join(tmp, "consolidation_report.json")
            report = json.load(open(report_path, encoding="utf-8"))
            record = next(
                item for item in report["source_lineage"]
                if item["source"] == "mlb_statsapi_request_ledger"
            )
            record["content_sha256"] = sha(raw)
            record["row_count"] = len(rows)
            report["source_lineage_fingerprint"] = (
                cert.source_lineage_fingerprint(report["source_lineage"])
            )
            report.pop("report_sha256", None)
            report["report_sha256"] = cert.sha256_bytes(
                json.dumps(
                    report,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode()
            )
            write_json(report_path, report)

            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any(
                    "different successful content SHAs" in failure
                    for failure in result["failures"]
                )
            )


    def test_same_url_can_differ_between_prediction_and_grading(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            blob_dir = os.path.join(tmp, "http_blobs")

            grading_body = (
                b'{"dates":[{"date":"2025-08-20","games":[]}],'
                b'"snapshot":"grading"}'
            )
            grading_sha = sha(grading_body)
            write_gzip(
                os.path.join(blob_dir, f"{grading_sha}.gz"),
                grading_body,
            )

            def mutate(rows):
                schedule = next(
                    row for row in rows
                    if "/api/v1/schedule" in row["url"]
                )
                outcome = dict(schedule)
                outcome["scientific_phase"] = "outcome_grading"
                outcome["response_sha256"] = grading_sha
                outcome["response_bytes"] = len(grading_body)
                rows.append(outcome)

            self._rewrite_ledger(
                tmp,
                "mlb_statsapi_request_ledger",
                mutate,
                bind=True,
            )
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"],
                "CANONICAL CERTIFIED",
                msg=json.dumps(result, indent=2),
            )
            self.assertEqual(
                result["cross_shard_request_consistency"][
                    "divergent_request_identities"
                ],
                0,
            )

    def test_unrecovered_statsapi_failure_blocks_certification(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build(statsapi_failure=True)
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "CERTIFICATION BLOCKED")
            self.assertTrue(
                any(
                    "unrecovered StatsAPI" in b
                    for b in result["blockers"]
                )
            )


    def _refresh_report(self, root, mutate=None):
        path = os.path.join(root, "consolidation_report.json")
        report = json.load(open(path, encoding="utf-8"))
        if mutate:
            mutate(report)
        report.pop("report_sha256", None)
        report["report_sha256"] = cert.sha256_bytes(
            json.dumps(report, sort_keys=True, separators=(",", ":"), default=str).encode()
        )
        write_json(path, report)
        return report

    def _rewrite_ledger(self, root, source_name, mutate_rows, bind=True):
        report_path = os.path.join(root, "consolidation_report.json")
        report = json.load(open(report_path, encoding="utf-8"))
        record = next(item for item in report["source_lineage"] if item["source"] == source_name)
        ledger_rel = next(
            token[5:] for token in str(record.get("notes") or "").split()
            if token.startswith("path=")
        )
        ledger_path = os.path.join(root, ledger_rel)
        rows = [
            json.loads(line)
            for line in open(ledger_path, encoding="utf-8")
            if line.strip()
        ]
        mutate_rows(rows)
        raw = b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for row in rows
        )
        with open(ledger_path, "wb") as handle:
            handle.write(raw)
        if bind:
            record["content_sha256"] = sha(raw)
            record["row_count"] = len(rows)
            report["source_lineage_fingerprint"] = cert.source_lineage_fingerprint(
                report["source_lineage"]
            )
            report.pop("report_sha256", None)
            report["report_sha256"] = cert.sha256_bytes(
                json.dumps(report, sort_keys=True, separators=(",", ":"), default=str).encode()
            )
            write_json(report_path, report)
        return rows



    def _replace_predictive_d_schedule(self, root, games):
        body = json.dumps(
            {
                "dates": [{
                    "date": "2025-08-20",
                    "games": games,
                }],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        body_sha = sha(body)
        write_gzip(
            os.path.join(root, "http_blobs", f"{body_sha}.gz"),
            body,
        )

        def mutate(rows):
            schedule = next(
                row for row in rows
                if "/api/v1/schedule" in row["url"]
                and "date=2025-08-20" in row["url"]
                and row.get("scientific_phase") == "predictive_input"
            )
            schedule["response_sha256"] = body_sha
            schedule["response_bytes"] = len(body)

        self._rewrite_ledger(
            root,
            "mlb_statsapi_request_ledger",
            mutate,
            bind=True,
        )

    @staticmethod
    def _d_schedule_game(game_type="R", game_pk=123):
        return {
            "gamePk": game_pk,
            "gameType": game_type,
            "officialDate": "2025-08-20",
            "gameDate": "2025-08-20T23:05:00Z",
            "teams": {
                "away": {"team": {"id": 101}},
                "home": {"team": {"id": 102}},
            },
            "status": {
                "codedGameState": "S",
                "statusCode": "S",
                "detailedState": "Scheduled",
            },
        }

    def _install_timebounded_predictive_feed(
        self,
        root,
        *,
        timecode="20250820_230459",
    ):
        blob_dir = os.path.join(root, "http_blobs")

        current_schedule = {
            "dates": [{
                "date": "2025-08-20",
                "games": [{
                    "gamePk": 123,
                    "gameType": "R",
                    "officialDate": "2025-08-20",
                    "gameDate": "2025-08-20T23:05:00Z",
                    "teams": {
                        "away": {"team": {"id": 101}},
                        "home": {"team": {"id": 102}},
                    },
                    "status": {
                        "codedGameState": "S",
                        "statusCode": "S",
                        "detailedState": "Scheduled",
                    },
                }],
            }],
        }
        current_body = json.dumps(
            current_schedule,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        current_sha = sha(current_body)
        write_gzip(
            os.path.join(blob_dir, f"{current_sha}.gz"),
            current_body,
        )

        prior_schedule = {
            "dates": [{
                "date": "2025-08-19",
                "games": [{
                    "gamePk": 7001,
                    "officialDate": "2025-08-19",
                    "gameDate": "2025-08-19T18:10:00Z",
                    "teams": {
                        "away": {"team": {"id": 101}},
                        "home": {"team": {"id": 130}},
                    },
                    "status": {
                        "codedGameState": "F",
                        "statusCode": "F",
                        "detailedState": "Final",
                    },
                }],
            }],
        }
        prior_body = json.dumps(
            prior_schedule,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        prior_sha = sha(prior_body)
        write_gzip(
            os.path.join(blob_dir, f"{prior_sha}.gz"),
            prior_body,
        )

        feed_payload = {
            "gameData": {
                "datetime": {"officialDate": "2025-08-19"},
                "status": {
                    "codedGameState": "F",
                    "detailedState": "Final",
                },
            },
            "liveData": {"boxscore": {"teams": {}}},
        }
        feed_body = json.dumps(
            feed_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        feed_sha = sha(feed_body)
        write_gzip(
            os.path.join(blob_dir, f"{feed_sha}.gz"),
            feed_body,
        )

        def mutate(rows):
            current = next(
                row for row in rows
                if "/api/v1/schedule" in row["url"]
                and "date=2025-08-20" in row["url"]
            )
            current["response_sha256"] = current_sha
            current["response_bytes"] = len(current_body)

            rows.append({
                "observed_date": "2025-08-20",
                "scientific_phase": "predictive_input",
                "method": "GET",
                "url": (
                    "https://statsapi.mlb.com/api/v1/schedule"
                    "?startDate=2025-08-12&endDate=2025-08-19"
                    "&teamId=101&sportId=1"
                ),
                "request_body_sha256": None,
                "status_code": 200,
                "response_sha256": prior_sha,
                "response_bytes": len(prior_body),
                "exception_type": None,
            })
            feed_url = (
                "https://statsapi.mlb.com/api/v1.1/game/7001/feed/live"
            )
            if timecode is not None:
                feed_url += f"?timecode={timecode}"
            rows.append({
                "observed_date": "2025-08-20",
                "scientific_phase": "predictive_input",
                "method": "GET",
                "url": feed_url,
                "request_body_sha256": None,
                "status_code": 200,
                "response_sha256": feed_sha,
                "response_bytes": len(feed_body),
                "exception_type": None,
            })

        self._rewrite_ledger(
            root,
            "mlb_statsapi_request_ledger",
            mutate,
            bind=True,
        )

    def test_wrong_rows_sha_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            with open(os.path.join(tmp, "rows.jsonl"), "ab") as handle:
                handle.write(b" ")
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(any("rows.jsonl SHA" in f for f in result["failures"]))

    def test_duplicate_candidate_identity_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            rows_path = os.path.join(tmp, "rows.jsonl")
            raw = open(rows_path, "rb").read()
            with open(rows_path, "wb") as handle:
                handle.write(raw + raw)
            self._refresh_report(
                tmp,
                lambda report: report.update({
                    "assembled_rows_sha256": cert.sha256_file(rows_path),
                    "total_rows": 2,
                }),
            )
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(any("duplicate candidate identity" in f for f in result["failures"]))

    def test_missing_date_metadata_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            factory = PackageFactory(tmp)
            factory.build()
            os.remove(os.path.join(tmp, "date_metadata", f"{factory.day}.json"))
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any("date_metadata does not cover requested dates exactly" in f for f in result["failures"])
            )

    def test_declared_statcast_source_sha_mismatch_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            self._refresh_report(
                tmp,
                lambda report: report.update({"statcast_source_sha256": "0" * 64}),
            )
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any("Statcast source SHA mismatch" in f or "Statcast parquet SHA mismatch" in f
                    for f in result["failures"])
            )

    def test_missing_external_request_ledger_blocks_certification(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            os.remove(os.path.join(tmp, "mlb_statsapi_request_ledger.jsonl"))
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"], "CERTIFICATION BLOCKED", msg=json.dumps(result, indent=2)
            )
            self.assertTrue(
                any("durable ledger artifact missing" in b for b in result["blockers"])
            )

    def test_ledger_sha_tamper_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            self._rewrite_ledger(
                tmp,
                "mlb_statsapi_request_ledger",
                lambda rows: rows[0].update({
                    "response_bytes": int(rows[0]["response_bytes"]) + 1
                }),
                bind=False,
            )
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(any("content SHA mismatch" in f for f in result["failures"]))


    def test_missing_archived_response_body_blocks_certification(self):
        with tempfile.TemporaryDirectory() as tmp:
            ids = PackageFactory(tmp).build()
            os.remove(os.path.join(tmp, "http_blobs", f"{ids['team_body_sha']}.gz"))
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"],
                "CERTIFICATION BLOCKED",
                msg=json.dumps(result, indent=2),
            )
            self.assertTrue(
                any("archived external response body missing" in b for b in result["blockers"])
            )

    def test_unapproved_external_host_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            factory = PackageFactory(tmp)
            factory.build()

            def mutate(rows):
                rows[0]["url"] = (
                    f"https://evidence-spoof.invalid/starting-lineups/{factory.day}"
                )

            self._rewrite_ledger(
                tmp,
                "mlbcom_dated_lineup_request_ledger",
                mutate,
                bind=True,
            )
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any("MLB.com ledger contains unexpected host" in f for f in result["failures"])
            )

    def test_recovered_transient_is_warning_not_false_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()

            def mutate(rows):
                schedule = next(row for row in rows if "/api/v1/schedule" in row["url"])
                transient = dict(schedule)
                transient["status_code"] = 503
                transient["response_sha256"] = None
                transient["response_bytes"] = None
                rows.insert(0, transient)

            self._rewrite_ledger(
                tmp,
                "mlb_statsapi_request_ledger",
                mutate,
                bind=True,
            )
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"],
                "CANONICAL CERTIFIED",
                msg=json.dumps(result, indent=2),
            )
            self.assertEqual(
                result["external_response_archive"][
                    "recovered_statsapi_transient_identities"
                ],
                1,
            )
            self.assertTrue(
                any("recovered transient failures" in w for w in result["warnings"])
            )


    def test_matching_short_row_code_sha_certifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            rows_path = os.path.join(tmp, "rows.jsonl")
            row = json.loads(open(rows_path, encoding="utf-8").read())
            row["code_git_sha"] = GEN_SHA[:7]
            raw = (
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            with open(rows_path, "wb") as handle:
                handle.write(raw)
            self._refresh_report(
                tmp,
                lambda report: report.update(
                    {"assembled_rows_sha256": sha(raw)}
                ),
            )
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"],
                "CANONICAL CERTIFIED",
                msg=json.dumps(result, indent=2),
            )

    def test_wrong_short_row_code_sha_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            rows_path = os.path.join(tmp, "rows.jsonl")
            row = json.loads(open(rows_path, encoding="utf-8").read())
            row["code_git_sha"] = "c" * 7
            raw = (
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            with open(rows_path, "wb") as handle:
                handle.write(raw)
            self._refresh_report(
                tmp,
                lambda report: report.update(
                    {"assembled_rows_sha256": sha(raw)}
                ),
            )
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any(
                    "row code SHA regime contains values" in failure
                    for failure in result["failures"]
                ),
                msg=json.dumps(result, indent=2),
            )

    def test_too_short_row_code_sha_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            rows_path = os.path.join(tmp, "rows.jsonl")
            row = json.loads(open(rows_path, encoding="utf-8").read())
            row["code_git_sha"] = GEN_SHA[:6]
            raw = (
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            with open(rows_path, "wb") as handle:
                handle.write(raw)
            self._refresh_report(
                tmp,
                lambda report: report.update(
                    {"assembled_rows_sha256": sha(raw)}
                ),
            )
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any(
                    "row code SHA regime contains values" in failure
                    for failure in result["failures"]
                ),
                msg=json.dumps(result, indent=2),
            )

    def test_mixed_row_code_shas_are_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            rows_path = os.path.join(tmp, "rows.jsonl")
            row = json.loads(open(rows_path, encoding="utf-8").read())
            row["code_git_sha"] = "c" * 40
            raw = (
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            with open(rows_path, "wb") as handle:
                handle.write(raw)
            self._refresh_report(
                tmp,
                lambda report: report.update({"assembled_rows_sha256": sha(raw)}),
            )
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(any("row code SHA regime" in f for f in result["failures"]))

    def test_scientific_environment_mismatch_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            self._refresh_report(
                tmp,
                lambda report: report["scientific_environment"].update(
                    {"python_version": "3.12.0"}
                ),
            )
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any("Python" in f and "expected" in f for f in result["failures"])
            )

    def test_firewall_block_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            factory = PackageFactory(tmp)
            factory.build()
            path = os.path.join(tmp, "date_metadata", f"{factory.day}.json")
            meta = json.load(open(path, encoding="utf-8"))
            meta["http_provenance"]["firewall_block_count"] = 1
            write_json(path, meta)
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any("source firewall blocked" in f for f in result["failures"])
            )


    def test_missing_preregistered_hr_source_column_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            factory = PackageFactory(tmp)
            factory.build()
            source_path = os.path.join(
                tmp,
                "source",
                "statcast_2024_through_2026-08-24.parquet",
            )
            frame = pd.read_parquet(source_path).drop(columns=["attack_angle"])
            frame.to_parquet(source_path, index=False)
            new_sha = cert.sha256_file(source_path)
            new_cols = sorted(str(column) for column in frame.columns)
            new_schema_fp = cert.sha256_bytes(",".join(new_cols).encode())

            meta_path = os.path.join(tmp, "date_metadata", f"{factory.day}.json")
            meta = json.load(open(meta_path, encoding="utf-8"))
            meta["source_content_sha256"] = new_sha
            write_json(meta_path, meta)

            def mutate(report):
                report["statcast_source_sha256"] = new_sha
                bound = report["identity"]["statcast_source"]
                bound["content_sha256"] = new_sha
                bound["row_count"] = len(frame)
                bound["schema_columns"] = new_cols
                bound["schema_fingerprint"] = new_schema_fp
                record = next(
                    item for item in report["source_lineage"]
                    if item["source"] == "statcast_leaguewide"
                )
                record["content_sha256"] = new_sha
                record["row_count"] = len(frame)
                record["schema_columns"] = new_cols
                record["schema_fingerprint"] = new_schema_fp
                report["source_lineage_fingerprint"] = (
                    cert.source_lineage_fingerprint(report["source_lineage"])
                )

            self._refresh_report(tmp, mutate)
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any("lacks preregistered HR columns" in f for f in result["failures"])
            )

    def _init_code_repo(self, root):
        cert.git("init", cwd=root)
        cert.git("config", "user.email", "canonical-v2-test@invalid.example", cwd=root)
        cert.git("config", "user.name", "Canonical V2 Test", cwd=root)
        for rel in cert.PROTECTED_SCIENTIFIC_FILES:
            target = os.path.join(root, rel)
            os.makedirs(os.path.dirname(target) or root, exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(f"frozen {rel}\n")
        allowed = os.path.join(root, "backtest", "canonical_v2_shard.py")
        os.makedirs(os.path.dirname(allowed), exist_ok=True)
        with open(allowed, "w", encoding="utf-8") as handle:
            handle.write("v1\n")
        cert.git("add", ".", cwd=root)
        cert.git("commit", "-m", "scientific parent", cwd=root)
        return cert.git("rev-parse", "HEAD", cwd=root)

    def test_generation_checkout_divergence_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as code_tmp, \
             tempfile.TemporaryDirectory() as package_tmp:
            parent = self._init_code_repo(code_tmp)
            allowed = os.path.join(code_tmp, "backtest", "canonical_v2_shard.py")
            with open(allowed, "a", encoding="utf-8") as handle:
                handle.write("v2\n")
            cert.git("add", ".", cwd=code_tmp)
            cert.git("commit", "-m", "generation", cwd=code_tmp)
            generation = cert.git("rev-parse", "HEAD", cwd=code_tmp)
            cert.git("checkout", parent, cwd=code_tmp)
            audit = cert.code_audit(code_tmp, parent, generation)
            self.assertTrue(
                any("certification checkout HEAD" in f for f in audit["failures"])
            )
            PackageFactory(package_tmp).build()
            with patch.object(cert, "code_audit", return_value=audit):
                result = cert.certify(
                    package_tmp,
                    repo_root=code_tmp,
                    expected_parent_sha=PARENT_SHA,
                )
            self.assertEqual(result["verdict"], "NOT CANONICAL")

    def test_protected_scientific_file_drift_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as code_tmp, \
             tempfile.TemporaryDirectory() as package_tmp:
            parent = self._init_code_repo(code_tmp)
            target = os.path.join(code_tmp, "recommendation.py")
            with open(target, "a", encoding="utf-8") as handle:
                handle.write("scientific drift\n")
            cert.git("add", ".", cwd=code_tmp)
            cert.git("commit", "-m", "drift", cwd=code_tmp)
            generation = cert.git("rev-parse", "HEAD", cwd=code_tmp)
            audit = cert.code_audit(code_tmp, parent, generation)
            self.assertTrue(
                any("protected scientific file changed" in f for f in audit["failures"])
            )
            PackageFactory(package_tmp).build()
            with patch.object(cert, "code_audit", return_value=audit):
                result = cert.certify(
                    package_tmp,
                    repo_root=code_tmp,
                    expected_parent_sha=PARENT_SHA,
                )
            self.assertEqual(result["verdict"], "NOT CANONICAL")


    def _rebind_outcome_source(self, root, frame):
        outcome_path = os.path.join(
            root,
            "source",
            "statcast_outcome_2025-08-20.parquet",
        )
        frame.to_parquet(outcome_path, index=False)
        new_sha = cert.sha256_file(outcome_path)
        columns = sorted(str(column) for column in frame.columns)
        schema_fp = cert.sha256_bytes(",".join(columns).encode())
        parsed = pd.to_datetime(frame["game_date"], errors="coerce").dropna()
        coverage = (
            f"{parsed.min().date()}..{parsed.max().date()}"
            if len(parsed) else None
        )

        meta_path = os.path.join(root, "date_metadata", "2025-08-20.json")
        meta = json.load(open(meta_path, encoding="utf-8"))
        meta["outcome_source_content_sha256"] = new_sha
        meta["outcome_source_schema_fingerprint"] = schema_fp
        write_json(meta_path, meta)

        def mutate(report):
            report["outcome_statcast_source_sha256"] = new_sha
            bound = report["identity"]["outcome_statcast_source"]
            bound["content_sha256"] = new_sha
            bound["row_count"] = len(frame)
            bound["schema_columns"] = columns
            bound["schema_fingerprint"] = schema_fp
            bound["date_coverage"] = coverage
            record = next(
                item for item in report["source_lineage"]
                if item["source"] == "statcast_outcome_only"
            )
            record["content_sha256"] = new_sha
            record["row_count"] = len(frame)
            record["schema_columns"] = columns
            record["schema_fingerprint"] = schema_fp
            record["date_coverage"] = coverage
            report["source_lineage_fingerprint"] = (
                cert.source_lineage_fingerprint(report["source_lineage"])
            )

        self._refresh_report(root, mutate)

    def test_outcome_source_with_predictive_feature_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            path = os.path.join(
                tmp, "source", "statcast_outcome_2025-08-20.parquet"
            )
            frame = pd.read_parquet(path)
            frame["bat_speed"] = 72.0
            self._rebind_outcome_source(tmp, frame)
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"], "NOT CANONICAL", msg=json.dumps(result, indent=2)
            )
            self.assertTrue(
                any(
                    "exact grading-only contract" in failure
                    for failure in result["failures"]
                )
            )

    def test_missing_outcome_source_blocks_certification(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            os.remove(
                os.path.join(
                    tmp, "source", "statcast_outcome_2025-08-20.parquet"
                )
            )
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"],
                "CERTIFICATION BLOCKED",
                msg=json.dumps(result, indent=2),
            )
            self.assertTrue(
                any(
                    "outcome-only Statcast parquet missing" in blocker
                    for blocker in result["blockers"]
                )
            )

    def test_postponed_same_day_predictive_game_feed_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            blob_dir = os.path.join(tmp, "http_blobs")

            schedule_payload = {
                "dates": [{
                    "date": "2025-08-19",
                    "games": [{
                        "gamePk": 776691,
                        "officialDate": "2025-08-20",
                        "gameDate": "2025-08-20T00:05:00Z",
                        "status": {
                            "codedGameState": "D",
                            "statusCode": "DR",
                            "detailedState": "Postponed",
                        },
                    }],
                }],
            }
            schedule_body = json.dumps(
                schedule_payload, sort_keys=True, separators=(",", ":")
            ).encode()
            schedule_sha = sha(schedule_body)
            write_gzip(
                os.path.join(blob_dir, f"{schedule_sha}.gz"),
                schedule_body,
            )

            feed_payload = {
                "gameData": {
                    "datetime": {"officialDate": "2025-08-20"},
                    "status": {"codedGameState": "F", "detailedState": "Final"},
                }
            }
            feed_body = json.dumps(
                feed_payload, sort_keys=True, separators=(",", ":")
            ).encode()
            feed_sha = sha(feed_body)
            write_gzip(
                os.path.join(blob_dir, f"{feed_sha}.gz"),
                feed_body,
            )

            def mutate(rows):
                rows.extend([
                    {
                        "observed_date": "2025-08-20",
                        "scientific_phase": "predictive_input",
                        "method": "GET",
                        "url": (
                            "https://statsapi.mlb.com/api/v1/schedule"
                            "?startDate=2025-08-12&endDate=2025-08-19"
                            "&teamId=133&sportId=1"
                        ),
                        "request_body_sha256": None,
                        "status_code": 200,
                        "response_sha256": schedule_sha,
                        "response_bytes": len(schedule_body),
                        "exception_type": None,
                    },
                    {
                        "observed_date": "2025-08-20",
                        "scientific_phase": "predictive_input",
                        "method": "GET",
                        "url": (
                            "https://statsapi.mlb.com/api/v1.1/game/776691/feed/live"
                        ),
                        "request_body_sha256": None,
                        "status_code": 200,
                        "response_sha256": feed_sha,
                        "response_bytes": len(feed_body),
                        "exception_type": None,
                    },
                ])

            self._rewrite_ledger(
                tmp,
                "mlb_statsapi_request_ledger",
                mutate,
                bind=True,
            )
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"], "NOT CANONICAL", msg=json.dumps(result, indent=2)
            )
            self.assertTrue(
                any(
                    "predictive game feed 776691" in failure
                    for failure in result["failures"]
                )
            )


    def test_cross_phase_success_does_not_recover_predictive_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()

            def mutate(rows):
                schedule = next(
                    row for row in rows
                    if "/api/v1/schedule" in row["url"]
                )
                failed_predictive = dict(schedule)
                failed_predictive["scientific_phase"] = "predictive_input"
                failed_predictive["status_code"] = 503
                failed_predictive["response_sha256"] = None
                failed_predictive["response_bytes"] = None

                successful_outcome = dict(schedule)
                successful_outcome["scientific_phase"] = "outcome_grading"

                rows[:] = [
                    row for row in rows
                    if row is not schedule
                ]
                rows.extend([failed_predictive, successful_outcome])

            self._rewrite_ledger(
                tmp,
                "mlb_statsapi_request_ledger",
                mutate,
                bind=True,
            )
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"],
                "CERTIFICATION BLOCKED",
                msg=json.dumps(result, indent=2),
            )
            self.assertEqual(
                result["external_response_archive"][
                    "recovered_statsapi_transient_identities"
                ],
                0,
            )
            self.assertEqual(
                result["external_response_archive"][
                    "unrecovered_statsapi_request_identities"
                ],
                1,
            )


    def test_valid_pregame_timebounded_predictive_feed_certifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            self._install_timebounded_predictive_feed(tmp)
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"],
                "CANONICAL CERTIFIED",
                msg=json.dumps(result, indent=2),
            )
            self.assertEqual(
                result["statsapi_source_shape_audit"]["classes"].get(
                    "historical_predictive_game_feed"
                ),
                1,
            )

    def test_predictive_feed_at_first_pitch_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            self._install_timebounded_predictive_feed(
                tmp,
                timecode="20250820_230500",
            )
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"],
                "NOT CANONICAL",
                msg=json.dumps(result, indent=2),
            )
            self.assertTrue(
                any(
                    "not an allowed team pregame cutoff" in failure
                    for failure in result["failures"]
                )
            )

    def test_predictive_feed_without_timecode_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            self._install_timebounded_predictive_feed(
                tmp,
                timecode=None,
            )
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"],
                "NOT CANONICAL",
                msg=json.dumps(result, indent=2),
            )
            self.assertTrue(
                any(
                    "predictive game feed lacks historical timecode" in failure
                    for failure in result["failures"]
                )
            )




    def test_excluded_only_schedule_requires_no_games_before_candidate_assembly(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            self._replace_predictive_d_schedule(
                tmp,
                [self._d_schedule_game("S")],
            )

            rows_path = os.path.join(tmp, "rows.jsonl")
            with open(rows_path, "wb") as handle:
                handle.write(b"")
            empty_sha = sha(b"")

            meta_path = os.path.join(
                tmp, "date_metadata", "2025-08-20.json"
            )
            meta = json.load(open(meta_path, encoding="utf-8"))
            meta["status"] = "ok"
            meta["n_games"] = 1
            meta["n_candidates"] = 1
            meta["row_count"] = 0
            write_json(meta_path, meta)

            def mutate(report):
                report["total_rows"] = 0
                report["unique_candidate_identities"] = 0
                report["assembled_rows_sha256"] = empty_sha

            self._refresh_report(tmp, mutate)
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any(
                    "zero-eligible D-schedule did not fail closed before "
                    "candidate assembly" in failure
                    for failure in result["failures"]
                ),
                msg=json.dumps(result, indent=2),
            )

    def test_candidate_from_spring_training_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            self._replace_predictive_d_schedule(
                tmp,
                [self._d_schedule_game("S")],
            )
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"],
                "NOT CANONICAL",
                msg=json.dumps(result, indent=2),
            )
            self.assertTrue(
                any(
                    "excluded/noncompetitive gameType" in failure
                    for failure in result["failures"]
                )
            )

    def test_candidate_from_exhibition_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            self._replace_predictive_d_schedule(
                tmp,
                [self._d_schedule_game("E")],
            )
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any(
                    "excluded/noncompetitive gameType" in failure
                    for failure in result["failures"]
                )
            )

    def test_unknown_d_schedule_game_type_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            self._replace_predictive_d_schedule(
                tmp,
                [self._d_schedule_game("X")],
            )
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any(
                    "unknown gameType" in failure
                    for failure in result["failures"]
                )
            )

    def test_candidate_missing_from_predictive_d_schedule_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            self._replace_predictive_d_schedule(tmp, [])
            result = self.certify(tmp)
            self.assertEqual(result["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any(
                    "absent from archived predictive D-schedule" in failure
                    for failure in result["failures"]
                )
            )

    def test_postseason_candidate_remains_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            PackageFactory(tmp).build()
            self._replace_predictive_d_schedule(
                tmp,
                [self._d_schedule_game("W")],
            )
            result = self.certify(tmp)
            self.assertEqual(
                result["verdict"],
                "CANONICAL CERTIFIED",
                msg=json.dumps(result, indent=2),
            )



if __name__ == "__main__":
    unittest.main(verbosity=2)

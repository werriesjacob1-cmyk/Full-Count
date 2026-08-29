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

        stats_body = b'{"dates":[{"date":"2025-08-20","games":[]}]}'
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
            "http_strict_host_firewall": True,
            "http_response_content_bound": True,
            "http_identical_get_cache": True,
            "historical_team_identity": (
                "schedule_team_ids_plus_season_directory"
            ),
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

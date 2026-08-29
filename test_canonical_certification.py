#!/usr/bin/env python3
"""Synthetic tests for the read-only canonical certifier."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import unittest

import pandas as pd

from backtest.canonical_certification import certify_run


PINNED_SHA = "a" * 40


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, obj):
    raw = (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()
    with open(path, "wb") as handle:
        handle.write(raw)
    return raw


def environment_record():
    env = {
        "python_version": "3.11.15",
        "python_implementation": "CPython",
        "platform": "Linux-test",
        "machine": "x86_64",
        "critical_packages": {"pandas": "3.0.5", "pyarrow": "25.0.1"},
        "pip_freeze_sha256": "freeze-sha",
        "pip_freeze_package_count": 2,
        "recorded_at": "2026-08-29T00:00:00+00:00",
    }
    payload = {
        key: env[key]
        for key in (
            "python_version",
            "python_implementation",
            "platform",
            "machine",
            "critical_packages",
            "pip_freeze_sha256",
        )
    }
    env["environment_fingerprint"] = sha(
        json.dumps(payload, sort_keys=True).encode()
    )
    return env


def lineage_fingerprint(records):
    keyed = sorted(
        json.dumps(
            {
                key: record.get(key)
                for key in (
                    "source",
                    "request_identity",
                    "content_sha256",
                    "schema_fingerprint",
                    "row_count",
                )
            },
            sort_keys=True,
        )
        for record in records
    )
    return sha("\n".join(keyed).encode())


def identity_fingerprint(identity):
    return sha(json.dumps(identity, sort_keys=True).encode())


def candidate(day, game, player, prob=0.65, outcome=1):
    return {
        "date": day,
        "game_pk": game,
        "player_id": player,
        "player_name": f"Player {player}",
        "prop_type": "hits",
        "line": 0.5,
        "needs": 1,
        "signals": {},
        "score": 70.0,
        "predicted_prob": prob,
        "outcome": outcome,
        "actual": 1,
        "fair_test": True,
        "actual_pa": 4,
        "code_git_sha": PINNED_SHA,
        "backtest_generated_at": "2026-08-29T00:00:00+00:00",
    }


def make_run(root, *, incomplete=False, duplicate=False, include_api_ledger=True):
    run_dir = os.path.join(root, "canonical-test")
    rows_dir = os.path.join(run_dir, "rows")
    source_dir = os.path.join(run_dir, "source")
    os.makedirs(rows_dir)
    os.makedirs(source_dir)

    manifest = {
        "run_id": "canonical-test",
        "schema_version": 1,
        "sport": "mlb",
        "evidence_regime": "canonical_historical_model_data",
        "requested_start_date": "2026-04-01",
        "requested_end_date": "2026-04-02",
        "command": "synthetic",
        "weather_mode": "no_weather",
        "config": {},
        "code_git_sha": PINNED_SHA,
        "repository_identity": "werriesjacob1-cmyk/Full-Count",
        "model_artifact_versions": {
            "model_version": "test-model",
            "selection_policy_version": "test-policy",
            "calibration_version": "test-cal",
            "feature_version": "test-feature",
        },
        "source_provider": "mlb_statsapi",
        "output_target": "synthetic",
        "created_at": "2026-08-29T00:00:00+00:00",
        "candidate_identity_fields": [
            "date", "game_pk", "player_id", "prop_type", "line"
        ],
    }
    write_json(os.path.join(run_dir, "manifest.json"), manifest)

    source_path = os.path.join(source_dir, "statcast.parquet")
    source_frame = pd.DataFrame({
        "game_date": ["2026-03-31", "2026-04-01"],
        "batter": [1, 2],
        "bat_speed": [70.0, 71.0],
    })
    source_frame.to_parquet(source_path, index=False)
    source_sha = sha(open(source_path, "rb").read())
    lineage = [{
        "source": "statcast_leaguewide",
        "request_identity": "statcast:test",
        "retrieval_timestamp": "2026-08-29T00:00:00+00:00",
        "library": "pybaseball",
        "library_version": "2.2.7",
        "row_count": 2,
        # Deliberately absent in the STORED record; the certifier must close
        # this additively from the exact parquet rather than rewrite lineage.
        "schema_columns": None,
        "schema_fingerprint": None,
        "content_sha256": source_sha,
        "date_coverage": "2026-03-31..2026-04-01",
        "cache_mode": "fresh_source",
        "notes": "synthetic",
    }]

    if include_api_ledger:
        ledger_path = os.path.join(source_dir, "mlb_statsapi_request_ledger.jsonl")
        ledger_rows = [
            {
                "date": "2026-04-01",
                "request_identity": "GET /api/v1/schedule?sportId=1&date=2026-04-01",
                "retrieved_at": "2026-08-29T00:00:01+00:00",
                "response_sha256": "1" * 64,
                "response_bytes": 1234,
            },
            {
                "date": "2026-04-01",
                "request_identity": "GET /api/v1/stats?stats=byDateRange",
                "retrieved_at": "2026-08-29T00:00:02+00:00",
                "response_sha256": "2" * 64,
                "response_bytes": 5678,
            },
        ]
        with open(ledger_path, "w", encoding="utf-8") as handle:
            for row in ledger_rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        ledger_sha = sha(open(ledger_path, "rb").read())
        ledger_columns = [
            "date",
            "request_identity",
            "retrieved_at",
            "response_sha256",
            "response_bytes",
        ]
        lineage.append({
            "source": "mlb_statsapi_request_ledger",
            "request_identity": "mlb_statsapi_request_ledger:canonical-test",
            "retrieval_timestamp": "2026-08-29T00:00:02+00:00",
            "library": "requests",
            "library_version": "2.34.2",
            "row_count": len(ledger_rows),
            "schema_columns": ledger_columns,
            "schema_fingerprint": sha(",".join(sorted(ledger_columns)).encode()),
            "content_sha256": ledger_sha,
            "date_coverage": "2026-04-01..2026-04-02",
            "cache_mode": "fresh_source",
            "notes": "path=source/mlb_statsapi_request_ledger.jsonl synthetic",
        })

    identity = {
        "run_id": manifest["run_id"],
        "code_git_sha": manifest["code_git_sha"],
        "schema_version": manifest["schema_version"],
        "requested_start_date": manifest["requested_start_date"],
        "requested_end_date": manifest["requested_end_date"],
        "weather_mode": manifest["weather_mode"],
        "repository_identity": manifest["repository_identity"],
        "model_artifact_versions": manifest["model_artifact_versions"],
        "evidence_regime": manifest["evidence_regime"],
        "candidate_identity_fields": manifest["candidate_identity_fields"],
    }

    dates = {}

    rows = [candidate("2026-04-01", 1, 10)]
    if duplicate:
        rows.append(dict(rows[0]))
    raw = b"".join(
        (json.dumps(row, sort_keys=True) + "\n").encode()
        for row in rows
    )
    data_sha = sha(raw)
    data_path = os.path.join(rows_dir, "2026-04-01.jsonl.gz")
    with gzip.open(data_path, "wb") as handle:
        handle.write(raw)
    meta = {
        "code_git_sha": PINNED_SHA,
        "date": "2026-04-01",
        "elapsed_seconds": 1.0,
        "extra": {"candidates": len(rows), "games": 1},
        "row_count": len(rows),
        "sha256": data_sha,
        "status": "ok",
        "written_at": "2026-08-29T00:00:00+00:00",
    }
    meta_raw = write_json(
        os.path.join(rows_dir, "2026-04-01.meta.json"),
        meta,
    )
    dates["2026-04-01"] = {
        "data_bytes": len(raw),
        "data_sha256": data_sha,
        "meta_sha256": sha(meta_raw),
        "rows": len(rows),
        "status": "ok",
    }

    if incomplete:
        dates["2026-04-02"] = {
            "data_bytes": 0,
            "data_sha256": None,
            "meta_sha256": None,
            "rows": 0,
            "status": "never_run",
        }
        summary = {"ok": 1, "no_games": 0, "never_run": 1}
    else:
        raw2 = b""
        data_sha2 = sha(raw2)
        data_path2 = os.path.join(rows_dir, "2026-04-02.jsonl.gz")
        with gzip.open(data_path2, "wb") as handle:
            handle.write(raw2)
        meta2 = {
            "code_git_sha": PINNED_SHA,
            "date": "2026-04-02",
            "elapsed_seconds": 0.1,
            "extra": {"candidates": 0, "games": 0},
            "row_count": 0,
            "sha256": data_sha2,
            "status": "no_games",
            "written_at": "2026-08-29T00:00:00+00:00",
        }
        meta_raw2 = write_json(
            os.path.join(rows_dir, "2026-04-02.meta.json"),
            meta2,
        )
        dates["2026-04-02"] = {
            "data_bytes": 0,
            "data_sha256": data_sha2,
            "meta_sha256": sha(meta_raw2),
            "rows": 0,
            "status": "no_games",
        }
        summary = {"ok": 1, "no_games": 1, "never_run": 0}

    index = {
        "durability_schema_version": 1,
        "run_id": manifest["run_id"],
        "updated_at": "2026-08-29T00:00:00+00:00",
        "identity": identity,
        "identity_fingerprint": identity_fingerprint(identity),
        "environment": environment_record(),
        "source_lineage": lineage,
        "source_lineage_fingerprint": lineage_fingerprint(lineage),
        "cache_mode": "fresh_source",
        "dates": dates,
        "summary": summary,
    }
    write_json(os.path.join(run_dir, "index.json"), index)
    return run_dir


class CertificationTests(unittest.TestCase):
    def test_complete_clean_run_certifies_with_additive_schema_attestation(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run(tmp)
            report = certify_run(run_dir)
            self.assertEqual(report["verdict"], "CANONICAL CERTIFIED")
            self.assertEqual(report["summary"]["ok"], 1)
            self.assertEqual(report["summary"]["no_games"], 1)
            self.assertEqual(report["summary"]["never_run"], 0)
            self.assertEqual(report["total_rows"], 1)
            self.assertTrue(
                report["source_schema_attestation"]["schema_fingerprint"]
            )
            self.assertTrue(
                report["dataset_identity"]["strength"]["promotion_grade"]
            )
            self.assertIn(
                "stored source lineage omitted schema_fingerprint; "
                "independent source-schema attestation supplies it additively",
                report["warnings"],
            )

    def test_missing_generation_time_api_response_ledger_blocks_certification(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run(tmp, include_api_ledger=False)
            report = certify_run(run_dir)
            self.assertEqual(report["verdict"], "CERTIFICATION BLOCKED")
            self.assertTrue(
                any(
                    "unbound external source lineage" in item
                    for item in report["blockers"]
                )
            )
            self.assertEqual(report["failures"], [])

    def test_incomplete_run_is_blocked_not_condemned(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run(tmp, incomplete=True)
            report = certify_run(run_dir)
            self.assertEqual(report["verdict"], "CERTIFICATION BLOCKED")
            self.assertEqual(report["summary"]["never_run"], 1)
            self.assertTrue(
                any("run incomplete" in item for item in report["blockers"])
            )
            self.assertEqual(report["failures"], [])

    def test_checkpoint_corruption_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run(tmp)
            path = os.path.join(run_dir, "rows", "2026-04-01.jsonl.gz")
            with gzip.open(path, "wb") as handle:
                handle.write(b'{"corrupt":true}\n')
            report = certify_run(run_dir)
            self.assertEqual(report["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any("data SHA" in item for item in report["failures"])
            )

    def test_duplicate_candidate_identity_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run(tmp, duplicate=True)
            report = certify_run(run_dir)
            self.assertEqual(report["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any("duplicate candidate identity" in item for item in report["failures"])
            )

    def test_source_content_mismatch_is_not_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run(tmp)
            index_path = os.path.join(run_dir, "index.json")
            index = json.load(open(index_path, encoding="utf-8"))
            index["source_lineage"][0]["content_sha256"] = "0" * 64
            index["source_lineage_fingerprint"] = lineage_fingerprint(
                index["source_lineage"]
            )
            write_json(index_path, index)
            report = certify_run(run_dir)
            self.assertEqual(report["verdict"], "NOT CANONICAL")
            self.assertTrue(
                any("no content SHA matches" in item for item in report["failures"])
            )

    def test_certifier_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run(tmp)

            def file_hashes():
                out = {}
                for root, _, files in os.walk(run_dir):
                    for name in files:
                        path = os.path.join(root, name)
                        rel = os.path.relpath(path, run_dir)
                        out[rel] = sha(open(path, "rb").read())
                return out

            before = file_hashes()
            report = certify_run(run_dir)
            after = file_hashes()
            self.assertEqual(report["verdict"], "CANONICAL CERTIFIED")
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Synthetic integration tests for canonical-v2 consolidation."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from backtest.canonical_v2_consolidate import (
    ConsolidationError,
    http_logical_fingerprint,
    main as consolidate_main,
    sha256_bytes,
)


CODE_SHA = "a" * 40
SOURCE_SCHEMA = "c" * 64


def atomic_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def gzip_bytes(path, raw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as raw_handle:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw_handle,
            mtime=0,
        ) as gz:
            gz.write(raw)


def environment():
    return {
        "python_version": "3.11.15",
        "python_implementation": "CPython",
        "critical_packages": {
            "pandas": "3.0.5",
            "pyarrow": "25.0.1",
            "pybaseball": "2.2.7",
            "requests": "2.34.2",
        },
        "pip_freeze_sha256": "d" * 64,
        "environment_fingerprint": "e" * 64,
    }


def identity(source_sha):
    return {
        "run_id": "v2-test",
        "requested_start_date": "2025-01-01",
        "requested_end_date": "2025-01-02",
        "scientific_parent_sha": "f" * 40,
        "generation_code_sha": CODE_SHA,
        "statcast_source": {
            "content_sha256": source_sha,
            "row_count": 10,
            "schema_columns": ["game_date", "batter"],
            "schema_fingerprint": SOURCE_SCHEMA,
            "date_coverage": "2024-01-01..2026-01-01",
        },
        "identity_fingerprint": "identity-fixed",
    }


def row(day, game, player):
    return {
        "date": day,
        "game_pk": game,
        "player_id": player,
        "prop_type": "hits",
        "line": 0.5,
        "predicted_prob": 0.7,
        "outcome": 1,
        "code_git_sha": CODE_SHA,
    }


def create_shard(root, index, day, player, source_sha):
    shard = os.path.join(root, f"shard-{index}")
    rows_dir = os.path.join(shard, "rows")
    http_dir = os.path.join(shard, "http")
    blobs = os.path.join(http_dir, "blobs")
    os.makedirs(blobs, exist_ok=True)

    raw_rows = (
        json.dumps(
            row(day, 100 + index, player),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    gzip_bytes(os.path.join(rows_dir, f"{day}.jsonl.gz"), raw_rows)

    response_body = json.dumps(
        {"date": day, "ok": True},
        sort_keys=True,
    ).encode()
    response_sha = sha256_bytes(response_body)
    gzip_bytes(
        os.path.join(blobs, f"{response_sha}.gz"),
        response_body,
    )
    entry = {
        "sequence": 1,
        "observed_date": day,
        "retrieved_at": "2026-08-29T00:00:00+00:00",
        "thread_id": 1,
        "method": "GET",
        "url": (
            "https://statsapi.mlb.com/api/v1/schedule"
            f"?date={day}&sportId=1"
        ),
        "request_headers": {"user-agent": f"ua-{index}"},
        "request_body_sha256": None,
        "status_code": 200,
        "response_sha256": response_sha,
        "response_bytes": len(response_body),
        "response_content_type": "application/json",
        "exception_type": None,
        "transport": "network",
        "archived_body": f"blobs/{response_sha}.gz",
    }
    ledger_raw = (
        json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    ledger_name = f"{day}.ledger.jsonl"
    with open(os.path.join(http_dir, ledger_name), "wb") as handle:
        handle.write(ledger_raw)

    http_summary = {
        "date": day,
        "request_count": 1,
        "success_2xx": 1,
        "http_non_2xx": 0,
        "exceptions": 0,
        "response_bytes_total": len(response_body),
        "ledger_file": ledger_name,
        "ledger_file_sha256": sha256_bytes(ledger_raw),
        "logical_fingerprint": http_logical_fingerprint([entry]),
        "allowed_hosts": [
            "mlb.com",
            "statsapi.mlb.com",
            "www.mlb.com",
        ],
        "archive_bodies": True,
        "strict_host_firewall": True,
        "firewall_block_count": 0,
        "firewall_blocks": [],
    }

    meta = {
        "date": day,
        "status": "ok",
        "row_count": 1,
        "decompressed_rows_sha256": sha256_bytes(raw_rows),
        "generation_code_sha": CODE_SHA,
        "source_content_sha256": source_sha,
        "source_schema_fingerprint": SOURCE_SCHEMA,
        "http_provenance": http_summary,
        "point_in_time_access": {
            "reads": [],
            "violations": [],
            "logical_fingerprint": sha256_bytes(b""),
        },
    }
    atomic_json(os.path.join(rows_dir, f"{day}.meta.json"), meta)

    ident = identity(source_sha)
    manifest = {
        "identity": ident,
        "environment": environment(),
        "shard_index": index,
        "shard_count": 2,
        "shard_first_date": day,
        "shard_last_date": day,
        "shard_dates": [day],
    }
    atomic_json(os.path.join(shard, "shard_manifest.json"), manifest)

    summary = {
        "identity_fingerprint": ident["identity_fingerprint"],
        "generation_code_sha": CODE_SHA,
        "scientific_parent_sha": ident["scientific_parent_sha"],
        "source_content_sha256": source_sha,
        "source_schema_fingerprint": SOURCE_SCHEMA,
        "shard_index": index,
        "shard_count": 2,
        "requested_dates": [day],
        "completed_dates": [day],
        "date_summaries": {
            day: {
                "status": "ok",
                "rows": 1,
                "rows_sha256": sha256_bytes(raw_rows),
                "http_logical_fingerprint": http_summary["logical_fingerprint"],
                "http_request_count": 1,
                "access_logical_fingerprint": sha256_bytes(b""),
            }
        },
        "errors": {},
    }
    atomic_json(os.path.join(shard, "shard_summary.json"), summary)


class ConsolidationTests(unittest.TestCase):
    def make_source(self, tmp):
        path = os.path.join(
            tmp,
            "statcast_2024_through_2026-08-24.parquet",
        )
        with open(path, "wb") as handle:
            handle.write(b"synthetic exact statcast source bytes")
        return path, hashlib.sha256(open(path, "rb").read()).hexdigest()

    def run_consolidator(self, shards_root, out_dir, source_path):
        argv = [
            "canonical_v2_consolidate.py",
            "--shards-root", shards_root,
            "--out-dir", out_dir,
            "--run-id", "v2-test",
            "--start", "2025-01-01",
            "--end", "2025-01-02",
            "--shard-count", "2",
            "--source-parquet", source_path,
            "--require-body-archive",
        ]
        with patch.object(sys, "argv", argv):
            return consolidate_main()

    def test_two_clean_shards_consolidate_and_preserve_response_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            shards = os.path.join(tmp, "shards")
            out = os.path.join(tmp, "out")
            source_path, source_sha = self.make_source(tmp)
            create_shard(shards, 0, "2025-01-01", 1, source_sha)
            create_shard(shards, 1, "2025-01-02", 2, source_sha)

            self.assertEqual(
                self.run_consolidator(shards, out, source_path),
                0,
            )
            report = json.load(
                open(
                    os.path.join(out, "consolidation_report.json"),
                    encoding="utf-8",
                )
            )
            self.assertEqual(report["verdict"], "CANONICAL_V2_CONSOLIDATED")
            self.assertEqual(report["requested_dates"], 2)
            self.assertEqual(report["total_rows"], 2)
            self.assertEqual(report["status_counts"], {"ok": 2})
            self.assertEqual(
                report["http_totals"]["archived_unique_response_bodies"],
                2,
            )
            self.assertEqual(
                len(os.listdir(os.path.join(out, "http_blobs"))),
                2,
            )
            rows = [
                json.loads(line)
                for line in open(os.path.join(out, "rows.jsonl"), encoding="utf-8")
            ]
            self.assertEqual(
                [row["date"] for row in rows],
                ["2025-01-01", "2025-01-02"],
            )
            self.assertEqual(
                report["source_lineage"][0]["schema_columns"],
                ["game_date", "batter"],
            )

    def test_missing_expected_date_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            shards = os.path.join(tmp, "shards")
            out = os.path.join(tmp, "out")
            source_path, source_sha = self.make_source(tmp)
            create_shard(shards, 0, "2025-01-01", 1, source_sha)
            create_shard(shards, 1, "2025-01-02", 2, source_sha)
            summary_path = os.path.join(
                shards,
                "shard-1",
                "shard_summary.json",
            )
            summary = json.load(open(summary_path, encoding="utf-8"))
            summary["completed_dates"] = []
            atomic_json(summary_path, summary)
            with self.assertRaises(ConsolidationError):
                self.run_consolidator(shards, out, source_path)

    def test_corrupted_archived_response_body_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            shards = os.path.join(tmp, "shards")
            out = os.path.join(tmp, "out")
            source_path, source_sha = self.make_source(tmp)
            create_shard(shards, 0, "2025-01-01", 1, source_sha)
            create_shard(shards, 1, "2025-01-02", 2, source_sha)
            blob_dir = os.path.join(shards, "shard-0", "http", "blobs")
            blob = os.path.join(blob_dir, os.listdir(blob_dir)[0])
            with open(blob, "wb") as handle:
                handle.write(b"not-gzip")
            with self.assertRaises(Exception):
                self.run_consolidator(shards, out)

    def test_mixed_code_sha_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            shards = os.path.join(tmp, "shards")
            out = os.path.join(tmp, "out")
            source_path, source_sha = self.make_source(tmp)
            create_shard(shards, 0, "2025-01-01", 1, source_sha)
            create_shard(shards, 1, "2025-01-02", 2, source_sha)
            path = os.path.join(shards, "shard-1", "shard_manifest.json")
            manifest = json.load(open(path, encoding="utf-8"))
            manifest["identity"]["generation_code_sha"] = "9" * 40
            atomic_json(path, manifest)
            with self.assertRaises(ConsolidationError):
                self.run_consolidator(shards, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

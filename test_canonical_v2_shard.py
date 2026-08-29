#!/usr/bin/env python3
"""Unit tests for canonical-v2 deterministic sharding primitives."""
from __future__ import annotations

import os
import tempfile
import unittest

import pandas as pd

from backtest.canonical_v2_shard import (
    CanonicalV2IntegrityError,
    contiguous_shard,
    row_blob,
    sha256_bytes,
    source_identity,
)


class ShardPartitionTests(unittest.TestCase):
    def test_contiguous_shards_cover_every_item_once(self):
        items = [f"d{i:03d}" for i in range(877)]
        shards = [contiguous_shard(items, i, 6) for i in range(6)]
        flattened = [item for shard in shards for item in shard]
        self.assertEqual(flattened, items)
        self.assertEqual(len(set(flattened)), len(items))
        sizes = [len(shard) for shard in shards]
        self.assertLessEqual(max(sizes) - min(sizes), 1)

    def test_bad_shard_arguments_fail(self):
        with self.assertRaises(ValueError):
            contiguous_shard([1, 2], 0, 0)
        with self.assertRaises(ValueError):
            contiguous_shard([1, 2], 2, 2)


class SourceIdentityTests(unittest.TestCase):
    def test_exact_parquet_sha_schema_rows_and_coverage_are_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(
                tmp,
                "statcast_2024_through_2026-08-24.parquet",
            )
            frame = pd.DataFrame({
                "game_date": ["2025-01-01", "2025-01-02"],
                "batter": [1, 2],
                "bat_speed": [70.0, 71.0],
            })
            frame.to_parquet(path, index=False)
            expected = __import__("hashlib").sha256(
                open(path, "rb").read()
            ).hexdigest()
            identity = source_identity(path, expected)
            self.assertEqual(identity["content_sha256"], expected)
            self.assertEqual(identity["row_count"], 2)
            self.assertEqual(
                identity["date_coverage"],
                "2025-01-01..2025-01-02",
            )
            self.assertEqual(
                identity["schema_columns"],
                ["bat_speed", "batter", "game_date"],
            )
            self.assertTrue(identity["schema_fingerprint"])

    def test_source_sha_mismatch_fails_before_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.parquet")
            pd.DataFrame({
                "game_date": ["2025-01-01"],
                "batter": [1],
            }).to_parquet(path, index=False)
            with self.assertRaises(CanonicalV2IntegrityError):
                source_identity(path, "0" * 64)


class RowSerializationTests(unittest.TestCase):
    def test_row_blob_is_deterministic_for_dict_key_order(self):
        a = {"date": "2025-01-01", "player_id": 1, "x": 2}
        b = {"x": 2, "player_id": 1, "date": "2025-01-01"}
        self.assertEqual(row_blob([a]), row_blob([b]))
        self.assertEqual(
            sha256_bytes(row_blob([a])),
            sha256_bytes(row_blob([b])),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

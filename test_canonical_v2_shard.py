#!/usr/bin/env python3
"""Unit tests for canonical-v2 deterministic sharding primitives."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import mlb_daily as m
import mlb_sources as msrc

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



class FullSeasonGameLogPointInTimeTests(unittest.TestCase):
    """Future splits in a mutable full-season gameLog must be inert at D-1."""

    @staticmethod
    def batter_split(day, hits=1):
        return {
            "date": day,
            "stat": {
                "plateAppearances": 4,
                "hits": hits,
                "totalBases": hits,
                "doubles": 0,
                "triples": 0,
                "homeRuns": 0,
                "runs": 0,
                "rbi": 0,
                "stolenBases": 0,
                "baseOnBalls": 0,
            },
        }

    @staticmethod
    def pitcher_split(day, strikeouts=6, innings="6.0"):
        return {
            "date": day,
            "stat": {
                "gamesStarted": 1,
                "strikeOuts": strikeouts,
                "inningsPitched": innings,
                "battersFaced": 24,
                "hits": 5,
                "baseOnBalls": 2,
            },
        }

    def test_empirical_batter_ignores_appended_future_split(self):
        asof = "2025-08-19"
        base = [
            self.batter_split("2025-08-17", 1),
            self.batter_split("2025-08-19", 0),
        ]
        future = self.batter_split("2025-08-20", 4)
        with patch.object(msrc, "_game_log", return_value=base):
            expected = msrc._empirical_batter_one((10, 1, asof))
        with patch.object(msrc, "_game_log", return_value=base + [future]):
            actual = msrc._empirical_batter_one((10, 1, asof))
        self.assertEqual(actual, expected)

    def test_empirical_pitcher_k_ignores_appended_future_split(self):
        asof = "2025-08-19"
        base = [
            self.pitcher_split("2025-08-14", 5),
            self.pitcher_split("2025-08-19", 7),
        ]
        future = self.pitcher_split("2025-08-20", 18)
        with patch.object(msrc, "_game_log", return_value=base):
            expected = msrc._empirical_pitcher_one((20, 1, asof))
        with patch.object(msrc, "_game_log", return_value=base + [future]):
            actual = msrc._empirical_pitcher_one((20, 1, asof))
        self.assertEqual(actual, expected)

    def test_empirical_pitcher_outs_ignores_appended_future_split(self):
        asof = "2025-08-19"
        base = [
            self.pitcher_split("2025-08-14", innings="5.2"),
            self.pitcher_split("2025-08-19", innings="6.1"),
        ]
        future = self.pitcher_split("2025-08-20", innings="9.0")
        with patch.object(msrc, "_game_log", return_value=base):
            expected = msrc._empirical_pitcher_outs_one((20, 1, asof))
        with patch.object(msrc, "_game_log", return_value=base + [future]):
            actual = msrc._empirical_pitcher_outs_one((20, 1, asof))
        self.assertEqual(actual, expected)

    def _rest_response(self, splits):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"stats": [{"splits": splits}]}

        return Response()

    def test_batter_rest_ignores_appended_future_split(self):
        asof = "2025-08-19"
        base = [
            {"date": "2025-08-17", "stat": {}},
            {"date": "2025-08-19", "stat": {}},
        ]
        future = {"date": "2025-08-20", "stat": {}}
        with patch.object(m, "TODAY", "2025-08-20"), \
             patch.object(m, "retry_get", return_value=self._rest_response(base)):
            expected = msrc._rest_batter_one((10, "Batter", asof))
        with patch.object(m, "TODAY", "2025-08-20"), \
             patch.object(
                 m, "retry_get", return_value=self._rest_response(base + [future])
             ):
            actual = msrc._rest_batter_one((10, "Batter", asof))
        self.assertEqual(actual, expected)

    def test_pitcher_rest_ignores_appended_future_start(self):
        asof = "2025-08-19"
        base = [
            self.pitcher_split("2025-08-12"),
            self.pitcher_split("2025-08-19"),
        ]
        future = self.pitcher_split("2025-08-20")
        with patch.object(m, "TODAY", "2025-08-20"), \
             patch.object(m, "retry_get", return_value=self._rest_response(base)):
            expected = msrc._rest_pitcher_one((20, "Pitcher", asof))
        with patch.object(m, "TODAY", "2025-08-20"), \
             patch.object(
                 m, "retry_get", return_value=self._rest_response(base + [future])
             ):
            actual = msrc._rest_pitcher_one((20, "Pitcher", asof))
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)

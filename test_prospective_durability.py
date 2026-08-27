#!/usr/bin/env python3
"""Adversarial tests for content-addressed prospective durability."""
from __future__ import annotations

import gzip
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))

import candidate_funnel_logger as cfl
import prospective_durability as pdur


def candidate(player_id=1, *, prob=0.65, odds=-120):
    return {
        "game_pk": 99,
        "game_start": "2026-08-25T23:00:00Z",
        "player_id": player_id,
        "name": f"Player {player_id}",
        "team": "A",
        "matchup": "A@B",
        "bet_side": "over",
        "projection": {"stat": "hits", "value": 0.5, "needs": 1},
        "hit_probability": prob,
        "market_odds": odds,
        "market_implied": 0.545,
        "reliability": "A",
        "score": 70.0,
        "status": "top_pick",
        "status_reasons": [],
    }


def records(*players, observed_at="2026-08-25T17:00:00Z"):
    market_context = {
        "book": "fanduel",
        "observed_at": observed_at,
        "family_states": {"batter_props": "AVAILABLE"},
    }
    run_meta = {
        "model_version": "m1",
        "selection_policy_version": "s1",
        "calibration_version": "c1",
        "feature_version": "f1",
        "prediction_timestamp": observed_at,
        "odds_fetched_at": observed_at,
        "board_generated_at": observed_at,
    }
    return cfl.build_funnel_records(
        list(players),
        date="2026-08-25",
        generated_at=observed_at,
        code_git_sha="a" * 40,
        market_context=market_context,
        run_metadata=run_meta,
        quality_control_index={
            cfl.candidate_identity(p, date="2026-08-25"):
                ("confirmed_lineup", None)
            for p in players
        },
    )


def manifest(rows, observed_at="2026-08-25T17:00:00Z"):
    return cfl.build_snapshot_manifest(
        rows,
        date="2026-08-25",
        observed_at=observed_at,
        code_git_sha="a" * 40,
        market_context={
            "book": "fanduel",
            "observed_at": observed_at,
            "family_states": {"batter_props": "AVAILABLE"},
        },
        run_metadata={
            "model_version": "m1",
            "selection_policy_version": "s1",
            "calibration_version": "c1",
            "feature_version": "f1",
            "prediction_timestamp": observed_at,
            "odds_fetched_at": observed_at,
            "board_generated_at": observed_at,
        },
    )


class CandidateBlobTests(unittest.TestCase):
    def test_candidate_bytes_hash_matches_content_hash(self):
        row = records(candidate())[0]
        raw = pdur.candidate_content_bytes(row)
        self.assertEqual(pdur._sha256_bytes(raw), cfl.content_hash(row))

    def test_observation_timestamp_does_not_change_candidate_blob_identity(self):
        r1 = records(candidate(), observed_at="2026-08-25T17:00:00Z")[0]
        r2 = records(candidate(), observed_at="2026-08-25T18:00:00Z")[0]
        self.assertEqual(cfl.content_hash(r1), cfl.content_hash(r2))
        self.assertEqual(
            pdur.candidate_content_bytes(r1),
            pdur.candidate_content_bytes(r2),
        )


class MaterializeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_materializes_exact_snapshot_and_reuses_candidate_blobs(self):
        rows = records(candidate(1), candidate(2))
        snap = manifest(rows)
        first = pdur.materialize_snapshot(rows, snap, self.root)
        self.assertEqual(first["candidate_blobs_written"], 2)
        self.assertEqual(first["candidate_blobs_reused"], 0)
        self.assertTrue(first["snapshot_written"])

        second = pdur.materialize_snapshot(rows, snap, self.root)
        self.assertEqual(second["candidate_blobs_written"], 0)
        self.assertEqual(second["candidate_blobs_reused"], 2)
        self.assertFalse(second["snapshot_written"])

        for row in rows:
            h = cfl.content_hash(row)
            path = os.path.join(
                self.root, pdur.candidate_blob_relpath(h))
            self.assertTrue(os.path.exists(path))
            with open(path, "rb") as fh:
                raw = gzip.decompress(fh.read())
            self.assertEqual(pdur._sha256_bytes(raw), h)

    def test_two_observations_share_unchanged_candidate_blob_but_keep_two_snapshots(self):
        rows1 = records(candidate(1), observed_at="2026-08-25T17:00:00Z")
        rows2 = records(candidate(1), observed_at="2026-08-25T18:00:00Z")
        s1 = manifest(rows1, observed_at="2026-08-25T17:00:00Z")
        s2 = manifest(rows2, observed_at="2026-08-25T18:00:00Z")

        r1 = pdur.materialize_snapshot(rows1, s1, self.root)
        r2 = pdur.materialize_snapshot(rows2, s2, self.root)
        self.assertEqual(r1["candidate_blobs_written"], 1)
        self.assertEqual(r2["candidate_blobs_written"], 0)
        self.assertEqual(r2["candidate_blobs_reused"], 1)
        self.assertNotEqual(s1["snapshot_id"], s2["snapshot_id"])
        self.assertTrue(r1["snapshot_written"])
        self.assertTrue(r2["snapshot_written"])

    def test_missing_candidate_state_fails_before_snapshot_write(self):
        rows = records(candidate(1))
        snap = manifest(rows)
        with self.assertRaises(Exception):
            pdur.materialize_snapshot([], snap, self.root)
        snapshot_path = os.path.join(
            self.root,
            pdur.snapshot_relpath(snap["date"], snap["snapshot_id"]))
        self.assertFalse(os.path.exists(snapshot_path))

    def test_corrupt_existing_candidate_blob_fails_closed(self):
        rows = records(candidate(1))
        snap = manifest(rows)
        pdur.materialize_snapshot(rows, snap, self.root)
        h = cfl.content_hash(rows[0])
        path = os.path.join(self.root, pdur.candidate_blob_relpath(h))
        with open(path, "wb") as fh:
            fh.write(b"not gzip")
        with self.assertRaises(pdur.ProspectiveDurabilityError):
            pdur.materialize_snapshot(rows, snap, self.root)

    def test_conflicting_existing_snapshot_bytes_fail_closed(self):
        rows = records(candidate(1))
        snap = manifest(rows)
        result = pdur.materialize_snapshot(rows, snap, self.root)
        with open(result["snapshot_path"], "wb") as fh:
            fh.write(b"{}")
        with self.assertRaises(pdur.ProspectiveDurabilityError):
            pdur.materialize_snapshot(rows, snap, self.root)


class SpoolTests(unittest.TestCase):
    def test_latest_snapshot_from_jsonl_spool_materializes(self):
        with tempfile.TemporaryDirectory() as tmp:
            spool = os.path.join(tmp, "spool")
            dest = os.path.join(tmp, "dest")
            os.makedirs(spool)
            rows = records(candidate(1))
            snap = manifest(rows)
            cpath = os.path.join(spool, "candidate_funnel_2026-08-25.jsonl")
            spath = os.path.join(
                spool, "candidate_funnel_snapshots_2026-08-25.jsonl")
            with open(cpath, "w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")
            with open(spath, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(snap) + "\n")

            result = pdur.materialize_from_spool(
                candidate_path=cpath,
                snapshot_path=spath,
                destination_root=dest)
            self.assertEqual(result["snapshot_id"], snap["snapshot_id"])
            self.assertTrue(result["snapshot_written"])

    def test_malformed_spool_json_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, "bad.jsonl")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("not json\n")
            with self.assertRaises(pdur.ProspectiveDurabilityError):
                pdur.load_jsonl(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)

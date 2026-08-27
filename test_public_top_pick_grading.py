#!/usr/bin/env python3
"""Durable public Top Pick grading/history population regressions."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import grade_results as gr
from dashboard.live_state import canonical_prop_id, default_live_state
from dashboard.publication_registry import (
    build_publication_manifest, confirm_publication, default_registry, write_registry,
)


DATE = "2026-08-17"
FINAL = {"abstractGameState": "Final", "detailedState": "Final", "codedGameState": "F"}
# Real-pitch commencement evidence (playEvents[].isPitch == True) -- these
# tests represent genuinely completed real games, so their mocked feed must
# satisfy has_authoritative_game_commencement() the same way a real Final
# game's feed would, per the 2026-08-27 stronger settlement-boundary
# invariant (dashboard/settlement_rules.has_authoritative_game_commencement).
COMMENCED_FEED = {"liveData": {"plays": {"allPlays": [{"playEvents": [{"isPitch": True}]}]}}}


def pick(status="top_pick"):
    value = {
        "identity_version": 2, "type": "batter", "name": "Published X", "team": "A",
        "matchup": "A @ B", "game_pk": 1, "game_start": "2026-08-17T22:00:00Z",
        "player_id": 101, "combo_player_ids": None,
        "projection": {"stat": "hits", "needs": 1, "value": 1}, "stat": "hits",
        "market_side": "over", "prop": "Over 0.5 Hits",
        "recommendation_status": status, "status_reasons": [], "hit_probability": .7,
        "market_odds": -120, "market_implied": .545, "market_edge": .155,
    }
    value["id"] = canonical_prop_id(value)
    return value


def board(rows):
    return {
        "schema_version": 3, "identity_schema_version": 2, "date": DATE,
        "generated_at": "2026-08-17T18:00:00Z", "odds_fetched_at": "2026-08-17T18:00:00Z",
        "recommendation_metadata": {}, "props": rows,
    }


def registry_with(value):
    registry = default_registry()
    manifest = build_publication_manifest(
        board([value]), default_live_state(), registry, "sha", "2026-08-17T18:00:00Z",
    )
    confirm_publication(registry, manifest, "2026-08-17T18:01:00Z", {})
    return registry


class PublicGradingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.output = os.path.join(self.tmp.name, "output")
        self.results = os.path.join(self.tmp.name, "results")
        os.makedirs(self.output)
        os.makedirs(self.results)
        self.registry_path = os.path.join(self.tmp.name, "registry.json")
        self.patches = (
            mock.patch.object(gr, "OUTPUT_DIR", self.output),
            mock.patch.object(gr, "RESULTS_DIR", self.results),
            mock.patch.object(gr, "HISTORY_FILE", os.path.join(self.results, "history.json")),
            mock.patch.object(gr, "PUBLIC_REGISTRY_FILE", self.registry_path),
        )
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmp.cleanup()

    def write_canonical(self, rows):
        with open(os.path.join(self.output, f"picks_{DATE}.json"), "w", encoding="utf-8") as handle:
            json.dump({"picks": rows, "shadow_tracking": []}, handle)

    def grade(self, result):
        with mock.patch.object(gr, "fetch_game_contexts",
                                return_value={1: {"status": FINAL, "feed": COMMENCED_FEED}}), \
             mock.patch.object(gr, "grade_public_pick", return_value=result), \
             mock.patch.object(gr, "fetch_game_statuses", return_value={1: FINAL}), \
             mock.patch.object(gr, "grade_pick", return_value={**pick(), "grade": result.get("grade", "ungraded")}):
            self.assertTrue(gr.grade_day(DATE))
        with open(os.path.join(self.results, f"grades_{DATE}.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def test_public_pick_absent_from_later_canonical_board_is_still_graded_once(self):
        value = pick()
        write_registry(self.registry_path, registry_with(value))
        self.write_canonical([])
        first = self.grade({"grade": "hit", "settlement_state": "hit", "actual": 1})
        second = self.grade({"grade": "hit", "settlement_state": "hit", "actual": 1})
        self.assertEqual(len(first["public_top_picks"]), 1)
        self.assertEqual(len(second["public_top_picks"]), 1)
        self.assertEqual(second["public_top_pick_counts"]["hits"], 1)
        self.assertEqual(second["public_top_picks"], first["public_top_picks"])

    def test_canonical_same_wager_does_not_double_count_public_top_pick(self):
        value = pick()
        write_registry(self.registry_path, registry_with(value))
        self.write_canonical([value])
        result = self.grade({"grade": "hit", "settlement_state": "hit", "actual": 1})
        self.assertEqual(result["by_recommendation_status"]["top_pick"]["hits"], 1)
        self.assertEqual(result["public_top_pick_counts"]["hits"], 1)
        self.assertEqual(len(result["public_top_picks"]), 1)

    def test_void_excluded_and_later_unknown_cannot_erase_it(self):
        value = pick()
        write_registry(self.registry_path, registry_with(value))
        self.write_canonical([])
        voided = self.grade({"grade": "void", "settlement_state": "void", "reason": "no action"})
        counts = voided["public_top_pick_counts"]
        self.assertEqual((counts["hits"], counts["misses"], counts["voids"]), (0, 0, 1))
        retried = self.grade({"grade": "ungraded", "settlement_state": "ungraded", "reason": "retry"})
        self.assertEqual(retried["public_top_pick_counts"]["voids"], 1)

    def test_ungraded_remains_retryable_and_can_resolve(self):
        value = pick()
        write_registry(self.registry_path, registry_with(value))
        self.write_canonical([])
        ungraded = self.grade({
            "grade": "ungraded", "settlement_state": "ungraded", "reason": "retry",
        })
        self.assertEqual(ungraded["public_top_pick_counts"]["ungraded"], 1)
        self.assertIn(DATE, gr.dates_needing_grading())
        resolved = self.grade({"grade": "hit", "settlement_state": "hit", "actual": 1})
        self.assertEqual(resolved["public_top_pick_counts"]["hits"], 1)

    def test_authoritative_correction_replaces_record_without_duplicate(self):
        value = pick()
        write_registry(self.registry_path, registry_with(value))
        self.write_canonical([])
        self.grade({"grade": "hit", "settlement_state": "hit", "actual": 1})
        with mock.patch.object(gr, "PUBLIC_CORRECTION_RECHECK_DAYS", 100000):
            self.assertIn(DATE, gr.dates_needing_grading())
        corrected = self.grade({"grade": "miss", "settlement_state": "miss", "actual": 0})
        self.assertEqual(len(corrected["public_top_picks"]), 1)
        self.assertEqual(corrected["public_top_pick_counts"]["misses"], 1)
        with open(os.path.join(self.results, "history.json"), encoding="utf-8") as handle:
            history = json.load(handle)
        self.assertEqual(len(history["days"]), 1)
        self.assertEqual(history["public_top_pick_totals"]["misses"], 1)
        self.assertEqual(history["top_pick_hit_rate"], 0.0)

    def test_pre_registry_date_is_not_guessed_into_public_top_picks(self):
        write_registry(self.registry_path, default_registry())
        self.write_canonical([pick()])
        result = self.grade({"grade": "hit", "settlement_state": "hit", "actual": 1})
        self.assertEqual(result["public_top_picks"], [])
        self.assertEqual(result["by_recommendation_status"]["top_pick"]["hits"], 1)
        self.assertEqual(result["public_top_pick_counts"]["hits"], 0)
        with open(os.path.join(self.results, "history.json"), encoding="utf-8") as handle:
            history = json.load(handle)
        self.assertIsNone(history["top_pick_hit_rate"])
        self.assertEqual(history["modeled_top_pick_hit_rate"], 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

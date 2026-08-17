#!/usr/bin/env python3
"""End-to-end regressions for public recommendation lifecycle ownership."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import grade_results as gr
from dashboard import build_dashboard as bd
from dashboard import refresh_grades as rg
from dashboard.live_state import (
    atomic_write_json, canonical_prop_id, default_live_state, merge_prop_fields,
)
from dashboard.publication_registry import (
    build_publication_manifest, confirm_publication, default_registry, write_registry,
)


PREVIEW = {"abstractGameState": "Preview", "detailedState": "Scheduled", "codedGameState": "S"}
LIVE = {"abstractGameState": "Live", "detailedState": "In Progress", "codedGameState": "I"}
FINAL = {"abstractGameState": "Final", "detailedState": "Final", "codedGameState": "F"}
T0 = "2026-08-17T17:00:00Z"
T1 = "2026-08-17T18:00:00Z"
T2 = "2026-08-17T18:05:00Z"


def prop(stat="hits", needs=1, game_pk=1, player_id=101, side="over", status="top_pick"):
    row = {
        "identity_version": 2, "type": "pitcher" if stat in ("strikeouts", "pitcher_outs") else "batter",
        "name": "Fixture Player", "team": "A", "matchup": "A @ B", "side": "away",
        "game_pk": game_pk, "game_start": T1, "player_id": player_id,
        "combo_player_ids": None, "projection": {"stat": stat, "needs": needs, "value": float(needs)},
        "stat": stat, "market_side": side,
        "prop": ("Under" if side == "under" else "Over") + f" {needs - .5} {stat}",
        "recommendation_status": status, "status_reasons": [], "hit_probability": .7,
        "market_odds": -120, "market_implied": .545, "market_edge": .155,
        "price_clears": True, "market_hold": None,
    }
    row["id"] = canonical_prop_id(row)
    return row


def payload(rows, date="2026-08-17"):
    return {
        "schema_version": 3, "identity_schema_version": 2, "date": date,
        "generated_at": T0, "odds_fetched_at": T0,
        "recommendation_metadata": {"model_version": "m", "selection_policy_version": "p"},
        "props": rows, "summary": {}, "families": [], "schedule": [],
    }


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def published_registry(row):
    registry = default_registry()
    manifest = build_publication_manifest(payload([row]), default_live_state(), registry, "sha", T0)
    confirm_publication(registry, manifest, "2026-08-17T17:05:00Z", {"source_commit": "sha"})
    return registry


class TempLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data.json")
        self.live = os.path.join(self.tmp.name, "live.json")
        self.registry = os.path.join(self.tmp.name, "registry.json")

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, row, live=None):
        atomic_write_json(self.data, payload([row]))
        atomic_write_json(self.live, live or default_live_state())
        write_registry(self.registry, published_registry(row))


class LiveSettlementTests(TempLifecycle):
    def test_monotonic_live_hits_are_explicitly_provisional(self):
        cases = (("hits", 1), ("total_bases", 2), ("home_runs", 1), ("strikeouts", 5))
        for stat, needs in cases:
            with self.subTest(stat=stat):
                row = prop(stat, needs)
                self.seed(row)
                with mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": LIVE, "feed": {}}}), \
                     mock.patch.object(gr, "grade_pick", return_value={"grade": "hit", "actual": needs}):
                    rg.refresh(self.data, self.live, self.registry)
                delta = load_json(self.live)["props"][row["id"]]
                self.assertEqual(delta["game_state"], "live")
                self.assertEqual(delta["settlement_state"], "provisional_hit")
                self.assertEqual(delta["settlement_authority"], "live_observation")

    def test_first_inning_market_hits_are_provisional_only_when_proven(self):
        def first_inning(lean):
            value = prop()
            value.update({
                "type": "game", "player_id": "nrfi_1", "stat": "nrfi_combined",
                "projection": {"stat": "nrfi_combined"}, "lean": lean,
                "market_side": lean.lower(), "prop": f"{lean} — both teams",
            })
            value["id"] = canonical_prop_id(value)
            return value

        cases = (
            ("YRFI", 1, 1, "provisional_hit"),
            ("NRFI", 0, 2, "provisional_hit"),
            ("YRFI", 0, 1, "open"),
        )
        for lean, runs, inning, expected in cases:
            with self.subTest(lean=lean, runs=runs, inning=inning):
                row = first_inning(lean)
                self.seed(row)
                context = {
                    "status": LIVE,
                    "feed": {"liveData": {"linescore": {
                        "currentInning": inning,
                        "innings": [{"away": {"runs": runs}, "home": {"runs": 0}}],
                    }}},
                }
                with mock.patch.object(gr, "fetch_game_contexts", return_value={1: context}), \
                     mock.patch.object(gr, "grade_pick") as legacy_grader:
                    rg.refresh(self.data, self.live, self.registry)
                with open(self.live, encoding="utf-8") as handle:
                    delta = json.load(handle)["props"][row["id"]]
                self.assertEqual(delta["settlement_state"], expected)
                self.assertFalse(legacy_grader.called)

    def test_under_and_unresolved_over_remain_open(self):
        for row, observed in ((prop("strikeouts", 5, side="under"), None),
                              (prop("hits", 1), {"grade": "miss", "actual": 0})):
            with self.subTest(side=row["market_side"]):
                self.seed(row)
                with mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": LIVE, "feed": {}}}), \
                     mock.patch.object(gr, "grade_pick", return_value=observed) as grader:
                    rg.refresh(self.data, self.live, self.registry)
                delta = load_json(self.live)["props"][row["id"]]
                self.assertEqual(delta["game_state"], "live")
                self.assertEqual(delta["settlement_state"], "open")
                if row["market_side"] == "under":
                    self.assertFalse(grader.called)

    def test_final_confirms_or_corrects_provisional_and_is_idempotent(self):
        row = prop()
        live = default_live_state()
        merge_prop_fields(live, row["id"], {
            "settlement_state": "provisional_hit", "settlement_authority": "live_observation",
            "settlement_observed_at": T1, "settlement_source": "mlb_live_box_score",
            "result_actual": 1, "result_reason": "initial scoring",
        }, T1, channel="grades")
        self.seed(row, live)
        with mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": FINAL, "feed": {}}}), \
             mock.patch.object(gr, "grade_public_pick", return_value={
                 "grade": "miss", "settlement_state": "miss", "actual": 0,
                 "reason": "official scoring correction",
             }):
            rg.refresh(self.data, self.live, self.registry)
            first = load_json(self.live)
            rg.refresh(self.data, self.live, self.registry)
            second = load_json(self.live)
        delta = second["props"][row["id"]]
        self.assertEqual(delta["settlement_state"], "miss")
        self.assertEqual(delta["settlement_authority"], "official_final")
        self.assertEqual(delta["result_actual"], 0)
        self.assertEqual(delta["result_reason"], "official scoring correction")
        self.assertEqual(first["props"][row["id"]]["settlement_state"],
                         second["props"][row["id"]]["settlement_state"])
        self.assertEqual(first, second)

    def test_final_hit_and_source_failure_preservation(self):
        row = prop()
        self.seed(row)
        with mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": FINAL, "feed": {}}}), \
             mock.patch.object(gr, "grade_public_pick", return_value={
                 "grade": "hit", "settlement_state": "hit", "actual": 1,
             }):
            rg.refresh(self.data, self.live, self.registry)
        prior = read_bytes(self.live)
        with mock.patch.object(gr, "fetch_game_contexts", return_value={}):
            rg.refresh(self.data, self.live, self.registry)
        self.assertEqual(read_bytes(self.live), prior)


class BuildPersistenceTests(unittest.TestCase):
    def test_registered_pick_survives_start_and_full_rebuild(self):
        row = prop()
        registry = published_registry(row)
        live = default_live_state()
        merge_prop_fields(live, row["id"], {
            "market_odds": -150, "recommendation_status": "lean",
            "price_basis_board_generated_at": T0,
        }, "2026-08-17T17:30:00Z", channel="prices")
        merge_prop_fields(live, row["id"], {
            "settlement_state": "provisional_hit",
            "settlement_authority": "live_observation",
            "settlement_observed_at": T2,
            "settlement_source": "live", "result_actual": 1,
            "result_reason": "threshold reached",
        }, T2, channel="grades")
        out = bd.reconcile_public_lifecycle(
            payload([]), live=live, registry=registry,
            schedule={1: {"status": LIVE}}, now=T2,
        )
        self.assertEqual([value["id"] for value in out["props"]], [row["id"]])
        self.assertEqual(out["props"][0]["game_state"], "live")
        self.assertEqual(out["props"][0]["recommendation_status"], "top_pick")
        self.assertEqual(out["props"][0]["market_odds"], -120)
        self.assertEqual(out["props"][0]["settlement_state"], "provisional_hit")

    def test_started_or_unknown_unpublished_candidate_never_appears(self):
        for status, now in ((LIVE, T2), ({}, T0)):
            with self.subTest(status=status):
                row = prop()
                out = bd.reconcile_public_lifecycle(
                    payload([row]), live=default_live_state(), registry=default_registry(),
                    schedule={1: {"status": status}}, now=now,
                )
                self.assertEqual(out["props"], [])

    def test_preview_clock_crossing_start_blocks_full_build_publication(self):
        row = prop()
        out = bd.reconcile_public_lifecycle(
            payload([row]), live=default_live_state(), registry=default_registry(),
            schedule={1: {"status": PREVIEW}}, now=T1,
        )
        self.assertEqual(out["props"], [])

    def test_prior_slate_registered_pick_survives_utc_rollover(self):
        row = prop()
        registry = published_registry(row)
        out = bd.reconcile_public_lifecycle(
            payload([], date="2026-08-18"), live=default_live_state(), registry=registry,
            schedule={1: {"status": LIVE}}, now="2026-08-18T01:00:00Z",
        )
        self.assertEqual([value["id"] for value in out["props"]], [row["id"]])


class DeliveryAndImportTests(unittest.TestCase):
    def test_live_grader_imports_without_pybaseball(self):
        script = r'''
import builtins
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split(".")[0] in {"pybaseball", "pandas", "numpy", "bs4", "sklearn"}:
        raise ImportError("simulated reduced live-workflow environment")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
import grade_results
import dashboard.refresh_grades
import dashboard.refresh_prices
print("ok")
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_frontend_has_separate_states_and_reapplies_overlay(self):
        with open(os.path.join(ROOT, "dashboard/static/app.js"), encoding="utf-8") as handle:
            app = handle.read()
        with open(os.path.join(ROOT, "dashboard/static/app.css"), encoding="utf-8") as handle:
            css = handle.read()
        for token in ("settlement_state", "game_state", "provisional_hit",
                      "applyCachedLive();", "publication_candidate_token"):
            self.assertIn(token, app)
        for token in ("lifecycle-live", "lifecycle-hit", "lifecycle-miss"):
            self.assertIn(token, css)


if __name__ == "__main__":
    unittest.main(verbosity=2)

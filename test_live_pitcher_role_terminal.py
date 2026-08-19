#!/usr/bin/env python3
"""Live Integrity PR 2: role-terminal pitcher settlement (provisional_miss).

Real incident (Keider Montero, 2026-08-19): a strikeouts/pitcher_outs Over
pick needed more outs than its pitcher could still record once he left the
mound (MLB StatsAPI's own gameStatus.isCurrentPitcher going false), but the
board kept showing "open" until the official final -- a live viewer had no
way to tell the pick was already effectively dead. This locks in the fix:
a pitcher-stat pick whose listed pitcher has been removed, and who is not
currently trending toward a live hit, is explicitly marked
"provisional_miss" (live_observation authority, reversible, re-derived
every cycle) rather than silently staying "open".
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import grade_results as gr
from dashboard import refresh_grades as rg
from dashboard.live_state import (
    _validate_settlement_fact, atomic_write_json, canonical_prop_id,
    default_live_state,
)
from dashboard.publication_registry import (
    build_publication_manifest, confirm_publication, default_registry, write_registry,
)
from dashboard.settlement_rules import player_game_status
from dashboard.verify_pages_artifact import _validate_row

import tempfile
import json


LIVE = {"abstractGameState": "Live", "detailedState": "In Progress", "codedGameState": "I"}
PREP = "2026-08-19T19:00:00Z"
CONFIRM = "2026-08-19T19:05:00Z"
T0 = "2026-08-19T20:00:00Z"
T1 = "2026-08-19T20:10:00Z"


def prop(stat="strikeouts", needs=6, player_id=701, side="over"):
    row = {
        "identity_version": 2, "type": "pitcher",
        "name": "Fixture Pitcher", "team": "A", "matchup": "A @ B", "side": "away",
        "game_pk": 1, "game_start": T0, "player_id": player_id,
        "combo_player_ids": [player_id, 702] if stat == "combined_strikeouts" else None,
        "projection": {"stat": stat, "needs": needs, "value": float(needs)},
        "stat": stat, "market_side": side,
        "prop": ("Under" if side == "under" else "Over") + f" {needs - .5} {stat}",
        "recommendation_status": "top_pick", "status_reasons": [], "hit_probability": .7,
        "market_odds": -120, "market_implied": .545, "market_edge": .155,
        "price_clears": True, "market_hold": None,
    }
    row["id"] = canonical_prop_id(row)
    return row


def payload(rows, date="2026-08-19"):
    return {
        "schema_version": 3, "identity_schema_version": 2, "date": date,
        "generated_at": T0, "odds_fetched_at": T0,
        "recommendation_metadata": {"model_version": "m", "selection_policy_version": "p"},
        "props": rows, "summary": {}, "families": [], "schedule": [],
    }


def feed_with_pitcher_status(player_id, is_current_pitcher):
    return {
        "liveData": {"boxscore": {"teams": {"away": {"players": {
            f"ID{player_id}": {
                "person": {"id": player_id},
                "gameStatus": {"isCurrentPitcher": is_current_pitcher, "isSubstitute": False},
            },
        }}, "home": {"players": {}}}}},
    }


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def published_registry(row):
    registry = default_registry()
    manifest = build_publication_manifest(payload([row]), default_live_state(), registry, "sha", PREP)
    confirm_publication(registry, manifest, CONFIRM, {"source_commit": "sha"})
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


class PlayerGameStatusTests(unittest.TestCase):
    def test_returns_real_feed_gameStatus_for_known_player(self):
        feed = feed_with_pitcher_status(701, False)
        self.assertEqual(player_game_status(feed, 701), {"isCurrentPitcher": False, "isSubstitute": False})

    def test_unknown_player_returns_none(self):
        feed = feed_with_pitcher_status(701, False)
        self.assertIsNone(player_game_status(feed, 999))

    def test_missing_feed_returns_none(self):
        self.assertIsNone(player_game_status(None, 701))
        self.assertIsNone(player_game_status({}, 701))

    def test_non_numeric_player_id_returns_none(self):
        feed = feed_with_pitcher_status(701, False)
        self.assertIsNone(player_game_status(feed, "not-a-number"))
        self.assertIsNone(player_game_status(feed, None))


class SettlementFactValidationTests(unittest.TestCase):
    def test_provisional_miss_accepted_under_live_observation_with_actual(self):
        _validate_settlement_fact({
            "settlement_state": "provisional_miss", "settlement_authority": "live_observation",
            "settlement_observed_at": T1, "settlement_source": "mlb_live_role_terminal_pitching_change",
            "result_actual": 4, "result_reason": "pitcher removed from the mound",
        })

    def test_provisional_miss_rejected_without_result_actual(self):
        with self.assertRaises(ValueError):
            _validate_settlement_fact({
                "settlement_state": "provisional_miss", "settlement_authority": "live_observation",
                "settlement_observed_at": T1, "settlement_source": "mlb_live_role_terminal_pitching_change",
                "result_actual": None, "result_reason": "pitcher removed from the mound",
            })

    def test_provisional_miss_rejected_under_official_final_authority(self):
        with self.assertRaises(ValueError):
            _validate_settlement_fact({
                "settlement_state": "provisional_miss", "settlement_authority": "official_final",
                "settlement_observed_at": T1, "settlement_source": "mlb_live_role_terminal_pitching_change",
                "result_actual": 4, "result_reason": "pitcher removed from the mound",
            })


class RoleTerminalSettlementTests(TempLifecycle):
    def _run(self, row, feed, grade):
        self.seed(row)
        context = {"status": LIVE, "feed": feed}
        with mock.patch.object(gr, "fetch_game_contexts", return_value={1: context}), \
             mock.patch.object(gr, "grade_pick", return_value=grade):
            rg.refresh(self.data, self.live, self.registry)
        return load_json(self.live)["props"][row["id"]]

    def test_strikeouts_pick_becomes_provisional_miss_when_pitcher_removed(self):
        row = prop("strikeouts", needs=6, player_id=701)
        feed = feed_with_pitcher_status(701, False)
        delta = self._run(row, feed, {"grade": "miss", "actual": 4})
        self.assertEqual(delta["settlement_state"], "provisional_miss")
        self.assertEqual(delta["settlement_authority"], "live_observation")
        self.assertEqual(delta["result_actual"], 4)
        self.assertEqual(delta["settlement_source"], "mlb_live_role_terminal_pitching_change")

    def test_pitcher_outs_pick_becomes_provisional_miss_when_pitcher_removed(self):
        row = prop("pitcher_outs", needs=17, player_id=701)
        feed = feed_with_pitcher_status(701, False)
        delta = self._run(row, feed, {"grade": "miss", "actual": 15})
        self.assertEqual(delta["settlement_state"], "provisional_miss")

    def test_active_pitcher_stays_open_even_when_trailing(self):
        row = prop("strikeouts", needs=6, player_id=701)
        feed = feed_with_pitcher_status(701, True)
        delta = self._run(row, feed, {"grade": "miss", "actual": 4})
        self.assertEqual(delta["settlement_state"], "open")

    def test_combined_strikeouts_is_not_role_terminal(self):
        row = prop("combined_strikeouts", needs=10, player_id=701)
        feed = feed_with_pitcher_status(701, False)
        delta = self._run(row, feed, {"grade": "miss", "actual": 4})
        self.assertEqual(delta["settlement_state"], "open")

    def test_batter_market_is_not_role_terminal(self):
        row = prop("hits", needs=1, player_id=701)
        row["type"] = "batter"
        feed = feed_with_pitcher_status(701, False)
        delta = self._run(row, feed, {"grade": "miss", "actual": 0})
        self.assertEqual(delta["settlement_state"], "open")

    def test_ungraded_missing_box_line_never_becomes_provisional_miss(self):
        row = prop("strikeouts", needs=6, player_id=701)
        feed = feed_with_pitcher_status(701, False)
        delta = self._run(row, feed, {"grade": "ungraded", "reason": "no box line yet"})
        self.assertEqual(delta["settlement_state"], "open")

    def test_no_feed_gameStatus_for_player_never_becomes_provisional_miss(self):
        row = prop("strikeouts", needs=6, player_id=701)
        feed = {"liveData": {"boxscore": {"teams": {"away": {"players": {}}, "home": {"players": {}}}}}}
        delta = self._run(row, feed, {"grade": "miss", "actual": 4})
        self.assertEqual(delta["settlement_state"], "open")

    def test_provisional_miss_is_reversible_on_a_later_cycle(self):
        row = prop("strikeouts", needs=6, player_id=701)
        removed_feed = feed_with_pitcher_status(701, False)
        first = self._run(row, removed_feed, {"grade": "miss", "actual": 4})
        self.assertEqual(first["settlement_state"], "provisional_miss")

        active_feed = feed_with_pitcher_status(701, True)
        with mock.patch.object(gr, "fetch_game_contexts",
                                return_value={1: {"status": LIVE, "feed": active_feed}}), \
             mock.patch.object(gr, "grade_pick", return_value={"grade": "miss", "actual": 4}):
            rg.refresh(self.data, self.live, self.registry)
        second = load_json(self.live)["props"][row["id"]]
        self.assertEqual(second["settlement_state"], "open")

    def test_provisional_hit_is_never_reopened_by_role_terminal_logic(self):
        row = prop("strikeouts", needs=6, player_id=701)
        feed = feed_with_pitcher_status(701, False)
        first = self._run(row, feed, {"grade": "hit", "actual": 6})
        self.assertEqual(first["settlement_state"], "provisional_hit")
        with mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": LIVE, "feed": feed}}), \
             mock.patch.object(gr, "grade_pick", return_value={"grade": "miss", "actual": 4}):
            rg.refresh(self.data, self.live, self.registry)
        second = load_json(self.live)["props"][row["id"]]
        self.assertEqual(second["settlement_state"], "provisional_hit")

    def test_observed_counts_tracks_provisional_miss(self):
        row = prop("strikeouts", needs=6, player_id=701)
        feed = feed_with_pitcher_status(701, False)
        self.seed(row)
        with mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": LIVE, "feed": feed}}), \
             mock.patch.object(gr, "grade_pick", return_value={"grade": "miss", "actual": 4}), \
             mock.patch("builtins.print") as printer:
            rg.refresh(self.data, self.live, self.registry)
        summary = "\n".join(str(call.args[0]) for call in printer.call_args_list if call.args)
        self.assertIn("provisional-miss=1", summary)


class DeployGuardTests(unittest.TestCase):
    def test_final_with_stuck_provisional_miss_is_rejected(self):
        row = prop("strikeouts", needs=6, player_id=701)
        row["game_state"] = "final"
        row["game_state_observed_at"] = T1
        row["settlement_state"] = "provisional_miss"
        row["settlement_authority"] = "live_observation"
        row["settlement_observed_at"] = T1
        row["settlement_source"] = "mlb_live_role_terminal_pitching_change"
        row["result_actual"] = 4
        row["result_reason"] = "pitcher removed from the mound"
        row["publication_candidate_token"] = None
        row["published_top_pick_at"] = None
        row["publication_artifact_id"] = None
        with self.assertRaises(ValueError):
            _validate_row(row, {}, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)

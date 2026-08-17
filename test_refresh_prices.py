#!/usr/bin/env python3
"""Structured sportsbook observation and first-pitch race regressions."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

import grade_results as gr
import odds_fanduel as fd
import recommendation
from dashboard import refresh_prices as rp
from dashboard.live_state import atomic_write_json, canonical_prop_id, default_live_state, merge_prop_fields
from dashboard.publication_registry import (
    build_publication_manifest, confirm_publication, default_registry, write_registry,
)


PREVIEW = {"abstractGameState": "Preview", "detailedState": "Scheduled", "codedGameState": "S"}
LIVE = {"abstractGameState": "Live", "detailedState": "In Progress", "codedGameState": "I"}
T0 = "2026-08-17T17:00:00Z"
T1 = "2026-08-17T18:00:00Z"
T2 = "2026-08-17T18:00:01Z"


def row(stat="hits", game_pk=1, player_id=101):
    value = {
        "identity_version": 2, "type": "pitcher" if stat in ("strikeouts", "pitcher_outs") else "batter",
        "name": "Fixture", "team": "A", "matchup": "A @ B", "game_pk": game_pk,
        "game_start": T1, "player_id": player_id,
        "combo_player_ids": [101, 202] if stat == "combined_strikeouts" else None,
        "projection": {"stat": stat, "needs": 5 if stat in ("strikeouts", "pitcher_outs") else 1,
                       "value": 5 if stat in ("strikeouts", "pitcher_outs") else 1},
        "stat": stat, "market_side": "over", "lean": "NRFI" if stat == "nrfi_combined" else None,
        "prop": "Fixture prop", "recommendation_status": "top_pick",
        "status_reasons": ["prior"], "hit_probability": .7,
        "market_odds": -120, "market_implied": .545, "market_edge": .155,
        "price_clears": True, "market_hold": None, "stale": False,
    }
    if stat == "nrfi_combined":
        value["player_id"] = None
        value["type"] = "game"
    value["id"] = canonical_prop_id(value)
    return value


def payload(rows):
    return {
        "schema_version": 3, "identity_schema_version": 2, "date": "2026-08-17",
        "generated_at": T0, "odds_fetched_at": T0, "props": rows, "summary": {},
    }


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class TempPrice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data.json")
        self.live = os.path.join(self.tmp.name, "live.json")
        self.registry = os.path.join(self.tmp.name, "registry.json")
        atomic_write_json(self.live, default_live_state())
        write_registry(self.registry, default_registry())

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, rows, live=None):
        atomic_write_json(self.data, payload(rows))
        if live is not None:
            atomic_write_json(self.live, live)

    def feed_patches(self, failures=()):
        funcs = {
            "general_batter": "fetch_prop_prices", "strikeouts": "fetch_pitcher_strikeouts",
            "pitcher_outs": "fetch_pitcher_outs", "first_inning": "fetch_first_inning_totals",
            "combined_strikeouts": "fetch_combined_pitcher_strikeouts",
        }
        stack = ExitStack()
        for family, name in funcs.items():
            if family in failures:
                stack.enter_context(mock.patch.object(fd, name, side_effect=RuntimeError(f"{family} down")))
            else:
                stack.enter_context(mock.patch.object(fd, name, return_value={}))
        return stack


class ObservationTests(TempPrice):
    def test_matched_and_successful_not_posted_are_distinct(self):
        matched_row, absent_row = row("hits", 1, 101), row("hits", 2, 202)
        self.seed([matched_row, absent_row])

        def attach(rows, **_feeds):
            if rows[0]["game_pk"] == 1:
                rows[0].update({"market_odds": -150, "market_implied": .6,
                                "market_edge": .1, "price_clears": True})
                return rows, 1
            return rows, 0

        def classify(rows, **_kwargs):
            for value in rows:
                value["status"] = "top_pick" if value.get("market_odds") is not None else "lean"
                value["status_reasons"] = []

        contexts = {1: {"status": PREVIEW, "feed": {}}, 2: {"status": PREVIEW, "feed": {}}}
        with self.feed_patches(), \
             mock.patch.object(gr, "fetch_game_contexts", return_value=contexts), \
             mock.patch.object(fd, "attach_market_prices", side_effect=attach), \
             mock.patch.object(recommendation, "attach_recommendations", side_effect=classify), \
             mock.patch.object(rp, "utc_now", side_effect=[T0, T0, T0, T0]):
            rp.refresh(self.data, self.live, self.registry)
        live = load_json(self.live)["props"]
        self.assertEqual(live[matched_row["id"]]["market_observation_state"], "MATCHED")
        self.assertEqual(live[matched_row["id"]]["market_odds"], -150)
        self.assertEqual(live[absent_row["id"]]["market_observation_state"], "NOT_POSTED")
        self.assertIsNone(live[absent_row["id"]]["market_odds"])
        self.assertEqual(live[absent_row["id"]]["recommendation_status"], "lean")

    def test_total_primary_failure_preserves_quote_and_success_timestamp(self):
        value = row("hits")
        live = default_live_state()
        merge_prop_fields(live, value["id"], {
            "market_odds": -125, "market_observed_at": "2026-08-17T16:00:00Z",
            "market_observation_state": "MATCHED", "market_family": "general_batter",
            "price_basis_board_generated_at": T0,
        }, "2026-08-17T16:00:00Z", channel="prices")
        self.seed([value], live)
        with self.feed_patches(failures={"general_batter"}), \
             mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": PREVIEW, "feed": {}}}), \
             mock.patch.object(rp, "utc_now", side_effect=[T0, T0]):
            rp.refresh(self.data, self.live, self.registry)
        delta = load_json(self.live)["props"][value["id"]]
        self.assertEqual(delta["market_odds"], -125)
        self.assertEqual(delta["market_observed_at"], "2026-08-17T16:00:00Z")
        self.assertEqual(delta["market_fetch_state"], "FETCH_FAILED")
        self.assertEqual(delta["market_fetch_failed_at"], T0)

    def test_each_failed_special_family_preserves_its_quote(self):
        cases = (("strikeouts", "strikeouts"), ("pitcher_outs", "pitcher_outs"),
                 ("nrfi_combined", "first_inning"),
                 ("combined_strikeouts", "combined_strikeouts"))
        for index, (stat, family) in enumerate(cases, 1):
            with self.subTest(family=family):
                value = row(stat, game_pk=index, player_id=100 + index)
                self.seed([value])
                with self.feed_patches(failures={family}), \
                     mock.patch.object(gr, "fetch_game_contexts", return_value={index: {"status": PREVIEW, "feed": {}}}), \
                     mock.patch.object(rp, "utc_now", side_effect=[T0, T0]):
                    rp.refresh(self.data, self.live, self.registry)
                delta = load_json(self.live)["props"][value["id"]]
                self.assertNotIn("market_observed_at", delta)
                self.assertEqual(delta["market_fetch_state"], "FETCH_FAILED")
                self.assertNotIn("recommendation_status", delta)

    def test_started_market_freezes_without_fetch_or_reclassification(self):
        value = row("hits")
        self.seed([value])
        with mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": LIVE, "feed": {}}}), \
             mock.patch.object(fd, "fetch_prop_prices") as fetch, \
             mock.patch.object(recommendation, "attach_recommendations") as classify, \
             mock.patch.object(rp, "utc_now", return_value=T2):
            result = rp.refresh(self.data, self.live, self.registry)
        self.assertFalse(fetch.called)
        self.assertFalse(classify.called)
        self.assertEqual(result["props"][0]["market_odds"], -120)
        delta = load_json(self.live)["props"][value["id"]]
        self.assertEqual(delta["market_fetch_state"], "IN_PLAY")

    def test_preview_to_clock_crossing_during_fetch_cannot_publish_top_pick(self):
        value = row("hits")
        value["recommendation_status"] = "lean"
        self.seed([value])

        def attach(rows, **_kwargs):
            rows[0]["market_odds"] = -110
            return rows, 1

        def classify(rows, **_kwargs):
            rows[0]["status"] = "top_pick"

        with self.feed_patches(), \
             mock.patch.object(gr, "fetch_game_contexts", side_effect=[
                 {1: {"status": PREVIEW, "feed": {}}},
                 {1: {"status": PREVIEW, "feed": {}}},
             ]), \
             mock.patch.object(fd, "attach_market_prices", side_effect=attach), \
             mock.patch.object(recommendation, "attach_recommendations", side_effect=classify), \
             mock.patch.object(rp, "utc_now", side_effect=[T0, "2026-08-17T17:59:59Z", T2]):
            result = rp.refresh(self.data, self.live, self.registry)
        self.assertEqual(result["props"][0]["recommendation_status"], "lean")
        self.assertNotIn(value["id"], load_json(self.live)["props"])

    def test_legacy_rollout_state_is_canonicalized_before_live_write(self):
        value = row("hits")
        legacy_id = "1-101-hits-1"
        legacy_row = dict(value)
        legacy_row["id"] = legacy_id
        legacy_row.pop("identity_version")
        legacy_payload = payload([legacy_row])
        legacy_payload.pop("schema_version")
        legacy_payload.pop("identity_schema_version")
        atomic_write_json(self.data, legacy_payload)
        atomic_write_json(self.live, {
            "prices_updated_at": "2026-08-17T16:00:00Z",
            "grades_updated_at": None,
            "props": {legacy_id: {"market_odds": -125}},
        })
        with self.feed_patches(failures={"general_batter"}), \
             mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": PREVIEW, "feed": {}}}), \
             mock.patch.object(rp, "utc_now", side_effect=[T0, T0]):
            rp.refresh(self.data, self.live, self.registry)
        written = load_json(self.live)
        self.assertEqual(written["schema_version"], 3)
        self.assertIn(value["id"], written["props"])
        self.assertNotIn(legacy_id, written["props"])
        self.assertEqual(written["props"][value["id"]]["market_odds"], -125)

    def test_published_snapshot_cannot_reprice_or_demote_across_start_race(self):
        value = row("hits")
        self.seed([value])
        registry = default_registry()
        manifest = build_publication_manifest(
            payload([value]), default_live_state(), registry, "sha", T0,
        )
        confirm_publication(registry, manifest, T0, {})
        write_registry(self.registry, registry)

        def attach(rows, **_kwargs):
            rows[0]["market_odds"] = -150
            return rows, 1

        def classify(rows, **_kwargs):
            rows[0]["status"] = "lean"

        with self.feed_patches(), \
             mock.patch.object(gr, "fetch_game_contexts", side_effect=[
                 {1: {"status": PREVIEW, "feed": {}}},
                 {1: {"status": LIVE, "feed": {}}},
             ]), \
             mock.patch.object(fd, "attach_market_prices", side_effect=attach), \
             mock.patch.object(recommendation, "attach_recommendations", side_effect=classify), \
             mock.patch.object(rp, "utc_now", side_effect=[T0, T0, T2]):
            result = rp.refresh(self.data, self.live, self.registry)
        self.assertEqual(result["props"][0]["market_odds"], -120)
        self.assertEqual(result["props"][0]["recommendation_status"], "top_pick")
        with open(self.live, encoding="utf-8") as handle:
            self.assertNotIn(value["id"], json.load(handle)["props"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

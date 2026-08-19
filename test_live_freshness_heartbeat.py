#!/usr/bin/env python3
"""test_live_freshness_heartbeat.py — 2026-08-19 Live Integrity PR 1.

Backend half of the freshness contract: dashboard/live_state.py's new
touch_heartbeat()/grades_checked_at/prices_checked_at, and their wiring
through refresh_grades.py/refresh_prices.py/merge_live_states/normalize_live.
The frontend half (deterministic staleness evaluation, per-state
applicability, clock-advance behavior) is test_live_freshness.py.

Deliberately distinct from *_updated_at (which must keep meaning "a fact
actually changed" -- test_lifecycle_contract_v3.py/test_state_races.py
depend on that): a heartbeat must advance on a genuine no-op check cycle
(the point of this feature) and must NOT advance when the channel could
not actually check anything (a real upstream fetch failure), so both
directions are exercised here through the real refresh() entry points,
not just touch_heartbeat() in isolation.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from unittest import mock

import grade_results as gr
from dashboard import refresh_grades as rg
from dashboard import refresh_prices as rp
from dashboard.live_state import (
    atomic_write_json, canonical_prop_id, default_live_state, merge_live_states,
    touch_heartbeat, validate_live_state,
)
from dashboard.prepare_pages_artifact import normalize_live
from dashboard.publication_registry import (
    build_publication_manifest, confirm_publication, default_registry, write_registry,
)


LIVE = {"abstractGameState": "Live", "detailedState": "In Progress", "codedGameState": "I"}
T0 = "2026-08-19T17:00:00Z"
T1 = "2026-08-19T18:00:00Z"


def prop(stat="hits", needs=1, game_pk=1, player_id=101, side="under"):
    row = {
        "identity_version": 2, "type": "batter", "name": "Fixture Player",
        "team": "A", "matchup": "A @ B", "side": "away",
        "game_pk": game_pk, "game_start": T1, "player_id": player_id,
        "combo_player_ids": None, "projection": {"stat": stat, "needs": needs, "value": float(needs)},
        "stat": stat, "market_side": side,
        "prop": ("Under" if side == "under" else "Over") + f" {needs - .5} {stat}",
        "recommendation_status": "top_pick", "status_reasons": [], "hit_probability": .3,
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


def published_registry(row):
    registry = default_registry()
    manifest = build_publication_manifest(payload([row]), default_live_state(), registry, "sha", T0)
    confirm_publication(registry, manifest, "2026-08-19T17:05:00Z", {"source_commit": "sha"})
    return registry


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class DefaultAndValidateTests(unittest.TestCase):
    def test_default_live_state_includes_heartbeat_fields_as_none(self):
        state = default_live_state()
        self.assertIsNone(state["grades_checked_at"])
        self.assertIsNone(state["prices_checked_at"])

    def test_validate_rejects_invalid_heartbeat_timestamp(self):
        for key in ("grades_checked_at", "prices_checked_at"):
            with self.subTest(key=key):
                state = default_live_state()
                state[key] = "not a real timestamp"
                with self.assertRaises(ValueError):
                    validate_live_state(state)

    def test_validate_accepts_valid_heartbeat_timestamp(self):
        state = default_live_state()
        state["grades_checked_at"] = T0
        state["prices_checked_at"] = T0
        self.assertTrue(validate_live_state(state))


class TouchHeartbeatTests(unittest.TestCase):
    def test_advances_on_newer_and_keeps_newer_on_tie_or_regression(self):
        state = default_live_state()
        touch_heartbeat(state, "grades", "2026-08-19T17:00:00Z")
        self.assertEqual(state["grades_checked_at"], "2026-08-19T17:00:00Z")
        touch_heartbeat(state, "grades", "2026-08-19T16:00:00Z")  # older: must not regress
        self.assertEqual(state["grades_checked_at"], "2026-08-19T17:00:00Z")
        touch_heartbeat(state, "grades", "2026-08-19T18:00:00Z")  # newer: must advance
        self.assertEqual(state["grades_checked_at"], "2026-08-19T18:00:00Z")

    def test_prices_channel_is_independent_of_grades_channel(self):
        state = default_live_state()
        touch_heartbeat(state, "grades", T0)
        self.assertIsNone(state["prices_checked_at"])
        touch_heartbeat(state, "prices", T1)
        self.assertEqual(state["grades_checked_at"], T0)
        self.assertEqual(state["prices_checked_at"], T1)

    def test_rejects_unknown_channel_and_invalid_timestamp(self):
        state = default_live_state()
        with self.assertRaises(ValueError):
            touch_heartbeat(state, "odds", T0)
        with self.assertRaises(ValueError):
            touch_heartbeat(state, "grades", "not a timestamp")

    def test_does_not_touch_updated_at_triplet(self):
        # The whole point of a separate heartbeat: it must never be
        # confusable with "a fact changed."
        state = default_live_state()
        touch_heartbeat(state, "grades", T0)
        self.assertIsNone(state["updated_at"])
        self.assertIsNone(state["grades_updated_at"])


class MergeAndStagingTests(unittest.TestCase):
    def test_merge_live_states_keeps_newer_heartbeat_never_regresses(self):
        # Covers "out-of-order artifact arrival": an older incoming document
        # (e.g. a delayed/retried push) must not roll back a newer base
        # heartbeat already recorded on current main.
        base = default_live_state()
        touch_heartbeat(base, "grades", "2026-08-19T18:00:00Z")
        incoming = default_live_state()
        touch_heartbeat(incoming, "grades", "2026-08-19T17:00:00Z")
        merged = merge_live_states(base, incoming)
        self.assertEqual(merged["grades_checked_at"], "2026-08-19T18:00:00Z")

        # And the reverse: a genuinely newer incoming heartbeat must win.
        incoming2 = default_live_state()
        touch_heartbeat(incoming2, "grades", "2026-08-19T19:00:00Z")
        merged2 = merge_live_states(base, incoming2)
        self.assertEqual(merged2["grades_checked_at"], "2026-08-19T19:00:00Z")

    def test_normalize_live_preserves_heartbeat_for_pages_staging(self):
        # Covers "artifact-generation failure"/"stale Pages artifact" risk:
        # if this silently dropped the heartbeat, the deployed Pages copy
        # would forever show "never_checked" even with a perfectly healthy
        # backend, which is a worse failure than showing nothing at all --
        # a permanent false alarm baked into every future deploy.
        live = default_live_state()
        touch_heartbeat(live, "grades", T0)
        touch_heartbeat(live, "prices", T1)
        remapped = normalize_live(live, id_map={})
        self.assertEqual(remapped["grades_checked_at"], T0)
        self.assertEqual(remapped["prices_checked_at"], T1)


class GradingHeartbeatIntegrationTests(unittest.TestCase):
    """Exercises the real refresh_grades.refresh() entry point, matching
    test_live_lifecycle.py's own TempLifecycle pattern, rather than
    approximating it."""

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
        return load_json(self.registry)

    def test_noop_grading_cycle_still_advances_heartbeat_not_updated_at(self):
        # An "under" market never settles early (see refresh_grades.py's own
        # comment), so a SECOND cycle against the exact same live game
        # context the first cycle already recorded is a genuine no-op --
        # comparing the pipeline's own prior output against itself, rather
        # than guessing the exact internal delta shape a hand-built fixture
        # would need to match byte-for-byte.
        row = prop(side="under")
        registry_before = self.seed(row)  # starts from a truly empty live.json
        with mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": LIVE, "feed": {}}}):
            rg.refresh(self.data, self.live, self.registry)  # first real cycle: establishes state
            first = load_json(self.live)
            self.assertIsNotNone(first["grades_updated_at"])
            self.assertIsNotNone(first["grades_checked_at"])

            rg.refresh(self.data, self.live, self.registry)  # second cycle: identical context
        second = load_json(self.live)
        # The point of this test: grades_updated_at (a real-change marker)
        # must NOT move on a genuine no-op cycle, while grades_checked_at
        # (the heartbeat) still advances to prove the channel really looked.
        self.assertEqual(second["grades_updated_at"], first["grades_updated_at"])
        self.assertGreaterEqual(second["grades_checked_at"], first["grades_checked_at"])
        self.assertEqual(load_json(self.registry), registry_before)

    def test_total_feed_failure_does_not_advance_heartbeat(self):
        # Covers "source fetch failure"/"grading failure": the channel
        # never actually checked anything this cycle, so the heartbeat
        # must honestly stay exactly where it was.
        row = prop(side="under")
        seeded = default_live_state()
        seeded["grades_checked_at"] = "2026-08-19T17:00:00Z"
        self.seed(row, seeded)
        with mock.patch.object(gr, "fetch_game_contexts", return_value={}):
            rg.refresh(self.data, self.live, self.registry)
        result = load_json(self.live)
        self.assertEqual(result["grades_checked_at"], "2026-08-19T17:00:00Z")

    def test_grading_delta_never_carries_recommendation_truth(self):
        # No recommendation-policy field is ever part of what this channel
        # writes -- the north star ("real hits, not manufactured
        # confidence") requires the grading/freshness channel stay strictly
        # separate from selection truth.
        row = prop(side="under")
        self.seed(row)
        with mock.patch.object(gr, "fetch_game_contexts",
                               return_value={1: {"status": LIVE, "feed": {}}}):
            rg.refresh(self.data, self.live, self.registry)
        delta = load_json(self.live)["props"][row["id"]]
        self.assertNotIn("recommendation_status", delta)
        self.assertNotIn("market_odds", delta)


class PricesHeartbeatIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data.json")
        self.live = os.path.join(self.tmp.name, "live.json")
        self.registry = os.path.join(self.tmp.name, "registry.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_total_context_failure_does_not_advance_prices_heartbeat(self):
        row = prop(side="over")
        atomic_write_json(self.data, payload([row]))
        seeded = default_live_state()
        seeded["prices_checked_at"] = "2026-08-19T17:00:00Z"
        atomic_write_json(self.live, seeded)
        write_registry(self.registry, default_registry())
        with mock.patch.object(gr, "fetch_game_contexts", return_value={}):
            rp.refresh(self.data, self.live, self.registry)
        result = load_json(self.live)
        self.assertEqual(result["prices_checked_at"], "2026-08-19T17:00:00Z")

    def test_no_pregame_props_remaining_still_advances_heartbeat(self):
        # A game that already crossed the wagering boundary: refresh_prices
        # still performed a genuine check (real contexts were fetched),
        # even though there is nothing left to reprice.
        row = prop(side="over", game_pk=1)
        row["game_start"] = "2020-01-01T00:00:00Z"  # long past cutoff
        atomic_write_json(self.data, payload([row]))
        atomic_write_json(self.live, default_live_state())
        write_registry(self.registry, default_registry())
        with mock.patch.object(gr, "fetch_game_contexts",
                               return_value={1: {"status": LIVE, "feed": {}}}):
            rp.refresh(self.data, self.live, self.registry)
        result = load_json(self.live)
        self.assertIsNotNone(result["prices_checked_at"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

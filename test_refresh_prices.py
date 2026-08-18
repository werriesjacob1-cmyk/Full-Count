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


def observed_family(family, values=None, *, complete=True, errors=()):
    """One relevant FanDuel event with explicit family-observation evidence."""
    values = values or {}
    event = fd.MarketEventObservation(
        event_id="fd-1", name="A @ B", start=T1,
        complete=complete, values=values, errors=tuple(errors),
    )
    return fd.MarketFeedObservation(
        family=family, root_state=fd.EVENTS_DISCOVERED,
        values=values, events=(event,), errors=tuple(errors),
    )


def indeterminate_family(family, root_state=fd.ROOT_MALFORMED, error="fixture malformed"):
    return fd.MarketFeedObservation(
        family=family, root_state=root_state, values={}, events=(), errors=(error,),
    )


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
                stack.enter_context(mock.patch.object(
                    fd, name, return_value=observed_family(family),
                ))
        return stack


class FanDuelFeedEvidenceTests(unittest.TestCase):
    def test_root_transport_failure_is_explicit_and_never_an_empty_success(self):
        with mock.patch.object(fd, "_get", side_effect=RuntimeError("root down")):
            observation = fd.fetch_prop_prices(strict=True, with_evidence=True)
        self.assertEqual(observation.root_state, fd.ROOT_FETCH_FAILED)
        self.assertFalse(fd.market_evidence_for_row(observation, row("hits"))["absence_proven"])
        with mock.patch.object(fd, "_get", side_effect=RuntimeError("root down")):
            with self.assertRaises(RuntimeError):
                fd.fetch_prop_prices()

    def test_http_success_empty_root_is_indeterminate_even_in_strict_mode(self):
        with mock.patch.object(fd, "_get", return_value={}):
            observation = fd.fetch_prop_prices(strict=True, with_evidence=True)
        self.assertEqual(observation.root_state, fd.ROOT_MALFORMED)
        evidence = fd.market_evidence_for_row(observation, row("hits"))
        self.assertFalse(evidence["absence_proven"])
        self.assertEqual(evidence["values"], {})
        with mock.patch.object(fd, "_get", return_value={}):
            with self.assertRaises(RuntimeError):
                fd.fetch_prop_prices(strict=True)

    def test_http_success_empty_events_is_not_positive_market_absence(self):
        root = {"attachments": {"events": {}}}
        with mock.patch.object(fd, "_get", return_value=root):
            observation = fd.fetch_prop_prices(strict=True, with_evidence=True)
        self.assertEqual(observation.root_state, fd.ROOT_EMPTY)
        self.assertFalse(fd.market_evidence_for_row(observation, row("hits"))["absence_proven"])

    def test_usable_game_with_malformed_family_pages_is_indeterminate(self):
        root = {"attachments": {"events": {"one": {
            "eventId": "fd-1", "name": "A @ B", "openDate": T1,
        }}}}
        responses = [root, {}, {}, {}, {}]
        with mock.patch.object(fd, "_get", side_effect=responses):
            observation = fd.fetch_prop_prices(strict=True, with_evidence=True, max_workers=1)
        self.assertEqual(observation.root_state, fd.EVENTS_DISCOVERED)
        self.assertFalse(observation.events[0].complete)
        self.assertFalse(fd.market_evidence_for_row(observation, row("hits"))["absence_proven"])

    def test_every_event_request_failure_is_indeterminate_not_not_posted(self):
        root = {"attachments": {"events": {"one": {
            "eventId": "fd-1", "name": "A @ B", "openDate": T1,
        }}}}

        def get(path, **_kwargs):
            if path.startswith("content-managed-page"):
                return root
            raise RuntimeError("event endpoint down")

        with mock.patch.object(fd, "_get", side_effect=get):
            observation = fd.fetch_prop_prices(strict=True, with_evidence=True, max_workers=1)
        evidence = fd.market_evidence_for_row(observation, row("hits"))
        self.assertFalse(evidence["absence_proven"])
        self.assertIn("event endpoint down", evidence["reason"])

    def test_every_market_family_requires_structurally_valid_relevant_event_pages(self):
        root = {"attachments": {"events": {"one": {
            "eventId": "fd-1", "name": "A @ B", "openDate": T1,
        }}}}
        cases = (
            ("general_batter", fd.fetch_prop_prices, row("hits")),
            ("strikeouts", fd.fetch_pitcher_strikeouts, row("strikeouts")),
            ("pitcher_outs", fd.fetch_pitcher_outs, row("pitcher_outs")),
            ("first_inning", fd.fetch_first_inning_totals, row("nrfi_combined")),
            ("combined_strikeouts", fd.fetch_combined_pitcher_strikeouts,
             row("combined_strikeouts")),
        )
        for family, fetcher, candidate in cases:
            with self.subTest(family=family):
                def get(path, **_kwargs):
                    if path.startswith("content-managed-page"):
                        return root
                    return {}  # HTTP success, but no attachments.markets

                with mock.patch.object(fd, "_get", side_effect=get):
                    observation = fetcher(strict=True, with_evidence=True)
                self.assertEqual(observation.family, family)
                self.assertFalse(observation.events[0].complete)
                self.assertFalse(
                    fd.market_evidence_for_row(observation, candidate)["absence_proven"],
                )


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

    def test_structurally_empty_primary_feed_preserves_quote_and_recommendation(self):
        value = row("hits")
        live = default_live_state()
        merge_prop_fields(live, value["id"], {
            "market_odds": -125, "market_implied": .555,
            "market_edge": .145, "price_clears": True,
            "recommendation_status": "top_pick",
            "market_observed_at": "2026-08-17T16:00:00Z",
            "market_observation_state": "MATCHED",
            "market_family": "general_batter",
            "price_basis_board_generated_at": T0,
        }, "2026-08-17T16:00:00Z", channel="prices")
        self.seed([value], live)
        with self.feed_patches() as stack:
            stack.enter_context(mock.patch.object(
                fd, "fetch_prop_prices",
                return_value=indeterminate_family("general_batter", fd.ROOT_EMPTY),
            ))
            stack.enter_context(mock.patch.object(
                gr, "fetch_game_contexts",
                return_value={1: {"status": PREVIEW, "feed": {}}},
            ))
            stack.enter_context(mock.patch.object(rp, "utc_now", side_effect=[T0, T0]))
            classify = stack.enter_context(mock.patch.object(
                recommendation, "attach_recommendations",
            ))
            rp.refresh(self.data, self.live, self.registry)
        delta = load_json(self.live)["props"][value["id"]]
        self.assertEqual(delta["market_odds"], -125)
        self.assertEqual(delta["market_implied"], .555)
        self.assertEqual(delta["market_edge"], .145)
        self.assertTrue(delta["price_clears"])
        self.assertEqual(delta["recommendation_status"], "top_pick")
        self.assertEqual(delta["market_observed_at"], "2026-08-17T16:00:00Z")
        self.assertEqual(delta["market_fetch_state"], "FETCH_FAILED")
        classify.assert_not_called()

    def test_valid_relevant_event_with_exact_market_absent_clears_old_quote(self):
        value = row("hits")
        self.seed([value])

        def classify(rows, **_kwargs):
            rows[0]["status"] = "lean"

        with self.feed_patches(), \
             mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": PREVIEW, "feed": {}}}), \
             mock.patch.object(fd, "attach_market_prices", return_value=([value], 0)), \
             mock.patch.object(recommendation, "attach_recommendations", side_effect=classify), \
             mock.patch.object(rp, "utc_now", side_effect=[T0, T0, T0]):
            rp.refresh(self.data, self.live, self.registry)
        delta = load_json(self.live)["props"][value["id"]]
        self.assertEqual(delta["market_observation_state"], "NOT_POSTED")
        self.assertIsNone(delta["market_odds"])
        self.assertEqual(delta["market_observed_at"], T0)
        self.assertEqual(delta["recommendation_status"], "lean")

    def test_exact_match_is_positive_evidence_even_if_another_family_tab_failed(self):
        value = row("hits")
        self.seed([value])
        partial = observed_family(
            "general_batter", {"fixture": {("hits", 1): -150}},
            complete=False, errors=("moonshots tab malformed",),
        )

        def classify(rows, **_kwargs):
            rows[0]["status"] = "top_pick"

        with self.feed_patches() as stack:
            stack.enter_context(mock.patch.object(fd, "fetch_prop_prices", return_value=partial))
            stack.enter_context(mock.patch.object(
                gr, "fetch_game_contexts", return_value={1: {"status": PREVIEW, "feed": {}}},
            ))
            stack.enter_context(mock.patch.object(
                recommendation, "attach_recommendations", side_effect=classify,
            ))
            stack.enter_context(mock.patch.object(rp, "utc_now", side_effect=[T0, T0, T0]))
            rp.refresh(self.data, self.live, self.registry)
        delta = load_json(self.live)["props"][value["id"]]
        self.assertEqual(delta["market_observation_state"], "MATCHED")
        self.assertEqual(delta["market_odds"], -150)
        self.assertEqual(delta["market_observed_at"], T0)

    def test_malformed_one_family_does_not_block_successful_other_family(self):
        batter = row("hits", game_pk=1, player_id=101)
        pitcher = row("strikeouts", game_pk=1, player_id=202)
        self.seed([batter, pitcher])

        def attach(rows, **_feeds):
            if rows[0]["projection"]["stat"] == "strikeouts":
                rows[0].update({"market_odds": -140, "market_implied": .58,
                                "market_edge": .12, "price_clears": True})
                return rows, 1
            return rows, 0

        with self.feed_patches() as stack:
            stack.enter_context(mock.patch.object(
                fd, "fetch_prop_prices", return_value=indeterminate_family("general_batter"),
            ))
            stack.enter_context(mock.patch.object(
                gr, "fetch_game_contexts",
                return_value={1: {"status": PREVIEW, "feed": {}}},
            ))
            stack.enter_context(mock.patch.object(fd, "attach_market_prices", side_effect=attach))
            stack.enter_context(mock.patch.object(
                recommendation, "attach_recommendations",
                side_effect=lambda rows, **_kwargs: [r.update(status="top_pick") for r in rows],
            ))
            stack.enter_context(mock.patch.object(rp, "utc_now", side_effect=[T0, T0, T0]))
            rp.refresh(self.data, self.live, self.registry)
        live = load_json(self.live)["props"]
        self.assertEqual(live[batter["id"]]["market_fetch_state"], "FETCH_FAILED")
        self.assertEqual(live[batter["id"]].get("market_odds"), None)
        self.assertEqual(live[pitcher["id"]]["market_observation_state"], "MATCHED")
        self.assertEqual(live[pitcher["id"]]["market_odds"], -140)

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

    def test_no_relevant_pregame_rows_does_not_manufacture_feed_failure(self):
        value = row("hits")
        self.seed([value])
        with mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": LIVE, "feed": {}}}), \
             mock.patch.object(fd, "fetch_prop_prices") as general, \
             mock.patch.object(fd, "fetch_pitcher_strikeouts") as strikeouts, \
             mock.patch.object(rp, "utc_now", return_value=T2):
            rp.refresh(self.data, self.live, self.registry)
        general.assert_not_called()
        strikeouts.assert_not_called()
        delta = load_json(self.live)["props"][value["id"]]
        self.assertEqual(delta["market_fetch_state"], "IN_PLAY")
        self.assertNotEqual(delta["market_fetch_state"], "FETCH_FAILED")

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

    def test_stale_unmappable_legacy_orphan_no_longer_bricks_the_price_channel(self):
        # 2026-08-18 Pre-Phase-V production incident: this exact real-workflow
        # path (dashboard-live.yml's price channel, refresh_prices.refresh())
        # failed on every single scheduled run once docs/live.json accumulated
        # a legacy id for a game/prop no longer on any current board -- see
        # engineering/ENGINEERING_HANDOFF.md's incident entry. This id/content
        # shape is the literal one from the incident.
        value = row("hits")
        self.seed([value])
        incident_id = "824077-686930-strikeouts-4"
        atomic_write_json(self.live, {
            "prices_updated_at": "2026-08-17T16:00:00Z", "grades_updated_at": None,
            "props": {incident_id: {
                "market_odds": 112, "market_implied": 0.4456, "market_edge": 0.1654,
                "price_clears": True, "market_hold": 0.0585,
                "recommendation_status": "lean", "status_reasons": [], "stale": False,
            }},
        })
        with self.feed_patches(), \
             mock.patch.object(gr, "fetch_game_contexts", return_value={1: {"status": PREVIEW, "feed": {}}}), \
             mock.patch.object(rp, "utc_now", return_value=T1):
            rp.refresh(self.data, self.live, self.registry)  # must not raise
        written = load_json(self.live)
        self.assertNotIn(incident_id, written["props"])
        self.assertIn(value["id"], written["props"])

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

#!/usr/bin/env python3
"""Settlement eligibility and game-identity grading regressions."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import grade_results as gr
from dashboard import refresh_grades as rg
from dashboard.live_state import (
    atomic_write_json, canonical_prop_id, default_live_state, merge_prop_fields,
)
from dashboard.publication_registry import (
    build_publication_manifest, confirm_publication, default_registry, write_registry,
)
from dashboard.settlement_rules import settlement_eligibility


FINAL = {"abstractGameState": "Final", "detailedState": "Final", "codedGameState": "F"}
LIVE = {"abstractGameState": "Live", "detailedState": "In Progress", "codedGameState": "I"}


def pick(stat="strikeouts", player_id=10, combo=None, type_="pitcher"):
    row = {
        "identity_version": 2, "type": type_, "name": "Fixture", "team": "A",
        "matchup": "A @ B", "game_pk": 77, "game_start": "2026-08-17T23:30:00Z",
        "player_id": player_id, "combo_player_ids": combo,
        "projection": {"stat": stat, "needs": 5, "value": 5}, "stat": stat,
        "market_side": "over", "prop": "Over 4.5", "recommendation_status": "top_pick",
        "status_reasons": [], "hit_probability": .7, "market_odds": -110,
        "market_implied": .524, "market_edge": .176,
    }
    row["id"] = canonical_prop_id(row)
    return row


def feed(players=None, innings=None, scheduled=9):
    players = players or {}
    if innings is None:
        innings = [{"away": {"runs": 0}, "home": {"runs": 0}}
                   for _ in range(scheduled)]
    return {
        "gameData": {"status": FINAL, "game": {"scheduledInnings": scheduled}},
        "liveData": {
            "boxscore": {"teams": {"away": {"players": players}, "home": {"players": {}}}},
            "linescore": {"innings": innings},
        },
    }


def raw_player(player_id, *, started=0, pa=0, substitute=False, batting_order=""):
    return {
        "person": {"id": player_id}, "battingOrder": batting_order,
        "gameStatus": {"isSubstitute": substitute},
        "stats": {"pitching": {"gamesStarted": started},
                  "batting": {"plateAppearances": pa}},
    }


class EligibilityTests(unittest.TestCase):
    def test_listed_pitcher_must_start_even_if_he_pitches_in_relief(self):
        p = pick()
        relief = feed({"ID10": raw_player(10, started=0)})
        self.assertEqual(settlement_eligibility(p, relief, "final")["eligibility"], "void")
        started = feed({"ID10": raw_player(10, started=1)})
        self.assertEqual(settlement_eligibility(p, started, "final")["eligibility"], "eligible")
        absent = feed()
        self.assertEqual(settlement_eligibility(p, absent, "final")["eligibility"], "void")

    def test_combined_k_requires_both_actual_starters(self):
        p = pick("combined_strikeouts", player_id=10, combo=[10, 20])
        both = feed({"ID10": raw_player(10, started=1), "ID20": raw_player(20, started=1)})
        one = feed({"ID10": raw_player(10, started=1), "ID20": raw_player(20, started=0)})
        self.assertEqual(settlement_eligibility(p, both, "final")["eligibility"], "eligible")
        self.assertEqual(settlement_eligibility(p, one, "final")["eligibility"], "void")

    def test_batter_action_rules_are_market_specific(self):
        p = pick("total_bases", player_id=30, type_="batter")
        no_action = feed({"ID30": raw_player(30, pa=0, substitute=True)})
        starter_no_pa = feed({"ID30": raw_player(30, pa=0, substitute=False, batting_order="100")})
        substitute_pa = feed({"ID30": raw_player(30, pa=1, substitute=True)})
        self.assertEqual(settlement_eligibility(p, no_action, "final")["eligibility"], "void")
        self.assertEqual(settlement_eligibility(p, starter_no_pa, "final")["eligibility"], "eligible")
        self.assertEqual(settlement_eligibility(p, substitute_pa, "final")["eligibility"], "ungraded")

        hit = pick("hits", player_id=30, type_="batter")
        starter_pa = feed({"ID30": raw_player(30, pa=1, substitute=False, batting_order="100")})
        self.assertEqual(settlement_eligibility(hit, no_action, "final")["eligibility"], "void")
        self.assertEqual(settlement_eligibility(hit, starter_pa, "final")["eligibility"], "eligible")
        self.assertEqual(settlement_eligibility(hit, substitute_pa, "final")["eligibility"], "ungraded")
        self.assertEqual(settlement_eligibility(hit, starter_no_pa, "final")["eligibility"], "ungraded")

        homer = pick("home_runs", player_id=30, type_="batter")
        self.assertEqual(settlement_eligibility(homer, no_action, "final")["eligibility"], "void")
        self.assertEqual(settlement_eligibility(homer, substitute_pa, "final")["eligibility"], "void")
        self.assertEqual(settlement_eligibility(homer, starter_no_pa, "final")["eligibility"], "ungraded")
        self.assertEqual(settlement_eligibility(homer, starter_pa, "final")["eligibility"], "eligible")

        hrr = pick("hits_runs_rbis", player_id=30, type_="batter")
        self.assertEqual(settlement_eligibility(hrr, no_action, "final")["eligibility"], "void")
        self.assertEqual(settlement_eligibility(hrr, substitute_pa, "final")["eligibility"], "ungraded")
        self.assertEqual(settlement_eligibility(hrr, starter_no_pa, "final")["eligibility"], "ungraded")
        self.assertEqual(settlement_eligibility(hrr, starter_pa, "final")["eligibility"], "eligible")

    def test_unverified_hit_type_action_rules_fail_ungraded(self):
        for stat in ("singles", "doubles", "triples"):
            with self.subTest(stat=stat):
                p = pick(stat, player_id=30, type_="batter")
                official = feed({
                    "ID30": raw_player(30, pa=4, substitute=False, batting_order="100"),
                })
                result = settlement_eligibility(p, official, "final")
                self.assertEqual(result["eligibility"], "ungraded")
                self.assertEqual(result["reason_code"], "unsupported_market_rule")

    def test_game_state_is_not_itself_void_proof(self):
        p = pick()
        for state in ("postponed", "cancelled", "suspended"):
            with self.subTest(state=state):
                self.assertEqual(settlement_eligibility(p, feed(), state)["eligibility"], "ungraded")

    def test_unknown_special_market_rule_fails_ungraded(self):
        p = pick("first_inning_run")
        self.assertEqual(settlement_eligibility(p, feed(), "final")["eligibility"], "ungraded")

    def test_shortened_game_requires_an_unequivocally_determined_result(self):
        p = pick("hits", player_id=30, type_="batter")
        shortened = feed(
            {"ID30": raw_player(30, pa=3, substitute=False, batting_order="100")},
            innings=[{"away": {"runs": 0}, "home": {"runs": 0}}] * 5,
        )
        eligibility = settlement_eligibility(p, shortened, "final")
        self.assertEqual(eligibility["eligibility"], "conditional")
        with mock.patch.object(gr, "grade_pick", return_value={"grade": "hit", "actual": 1}):
            self.assertEqual(gr.grade_public_pick(p, {"status": FINAL, "feed": shortened})["grade"], "hit")
        with mock.patch.object(gr, "grade_pick", return_value={"grade": "miss", "actual": 0}):
            self.assertEqual(gr.grade_public_pick(p, {"status": FINAL, "feed": shortened})["grade"], "ungraded")

    def test_scheduled_seven_inning_game_can_complete_normally(self):
        p = pick("strikeouts")
        seven = feed({"ID10": raw_player(10, started=1)}, scheduled=7)
        eligibility = settlement_eligibility(p, seven, "final")
        self.assertEqual(eligibility["eligibility"], "eligible")
        self.assertEqual(eligibility["scheduled_innings"], 7)


class GameIdentityLookupTests(unittest.TestCase):
    def test_prior_slate_after_utc_midnight_is_looked_up_by_game_pk(self):
        row = pick("hits", player_id=30, type_="batter")
        board = {
            "schema_version": 3, "identity_schema_version": 2,
            "date": "2026-08-18", "generated_at": "2026-08-18T00:05:00Z",
            "odds_fetched_at": "2026-08-18T00:05:00Z", "props": [row], "summary": {},
        }
        registry = default_registry()
        exposure_board = dict(board)
        exposure_board["date"] = "2026-08-17"
        manifest = build_publication_manifest(
            exposure_board, default_live_state(), registry, "sha", "2026-08-17T22:00:00Z",
        )
        confirm_publication(registry, manifest, "2026-08-17T22:01:00Z", {})
        with tempfile.TemporaryDirectory() as root:
            data_path = os.path.join(root, "data.json")
            live_path = os.path.join(root, "live.json")
            registry_path = os.path.join(root, "registry.json")
            atomic_write_json(data_path, board)
            atomic_write_json(live_path, default_live_state())
            write_registry(registry_path, registry)
            with mock.patch.object(gr, "fetch_game_contexts", return_value={77: {"status": LIVE, "feed": {}}}) as lookup, \
                 mock.patch.object(gr, "grade_pick", return_value={"grade": "miss", "actual": 0}):
                rg.refresh(data_path, live_path, registry_path)
            self.assertIn(77, lookup.call_args.args[0])
            with open(live_path, encoding="utf-8") as handle:
                delta = json.load(handle)["props"][row["id"]]
            self.assertEqual(delta["game_state"], "live")
            self.assertEqual(delta["settlement_state"], "open")

    def test_five_minute_grader_bounds_old_terminal_history_only(self):
        current = pick("hits", player_id=30, type_="batter")
        old_terminal = pick("hits", player_id=31, type_="batter")
        old_terminal["game_pk"] = 78
        old_terminal["game_start"] = "2026-07-01T20:00:00Z"
        old_terminal["player_id"] = 31
        old_terminal["id"] = canonical_prop_id(old_terminal)
        old_open = pick("hits", player_id=32, type_="batter")
        old_open["game_pk"] = 79
        old_open["game_start"] = "2026-07-01T20:00:00Z"
        old_open["player_id"] = 32
        old_open["id"] = canonical_prop_id(old_open)
        live = default_live_state()
        merge_prop_fields(live, old_terminal["id"], {
            "settlement_state": "hit", "settlement_authority": "official_final",
            "settlement_observed_at": "2026-07-01T23:00:00Z",
            "settlement_source": "official", "result_actual": 1,
            "result_reason": "official hit",
        }, "2026-07-01T23:00:00Z", channel="grades")
        merge_prop_fields(live, old_open["id"], {
            "settlement_state": "open", "settlement_authority": "live_observation",
            "settlement_observed_at": "2026-07-01T23:00:00Z",
            "settlement_source": "suspended", "result_actual": None,
            "result_reason": "awaiting completion",
        }, "2026-07-01T23:00:00Z", channel="grades")
        selected = rg._active_public_snapshots(
            [current, old_terminal, old_open], {"props": [current]}, live,
            "2026-08-17T20:00:00Z",
        )
        self.assertEqual({row["id"] for row in selected}, {current["id"], old_open["id"]})


class LiveGraderChannelTests(unittest.TestCase):
    """2026-08-18 Pre-Phase-V production incident: dashboard-live.yml's
    grading channel (this module's own refresh()) shares the exact same
    normalize_live() call as the price channel and full rebuild -- see
    engineering/ENGINEERING_HANDOFF.md's incident entry. Exercises the real
    refresh() entry point, not just normalize_live() in isolation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data.json")
        self.live = os.path.join(self.tmp.name, "live.json")
        self.registry = os.path.join(self.tmp.name, "registry.json")
        board_row = pick("hits", player_id=30, type_="batter")
        atomic_write_json(self.data, {
            "schema_version": 3, "identity_schema_version": 2,
            "date": "2026-08-17", "generated_at": "2026-08-17T17:00:00Z",
            "odds_fetched_at": "2026-08-17T17:00:00Z", "props": [board_row], "summary": {},
        })
        registry = default_registry()
        registry["migration"] = {
            "version": registry["rollout_version"], "completed_at": "2026-08-17T16:00:00Z",
            "source_artifact_id": "legacy-proof",
        }
        registry["updated_at"] = "2026-08-17T16:00:00Z"
        write_registry(self.registry, registry)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stale_unmappable_legacy_orphan_no_longer_bricks_the_grading_channel(self):
        # The literal id/content shape from the real incident: no registry
        # entries exist yet (nothing has ever been published), so refresh()
        # has nothing to grade -- but it must still get past normalize_live()
        # without raising, since that call happens before the early return.
        incident_id = "824077-686930-strikeouts-4"
        atomic_write_json(self.live, {
            "prices_updated_at": "2026-08-17T16:00:00Z", "grades_updated_at": None,
            "props": {incident_id: {
                "market_odds": 112, "market_implied": 0.4456, "market_edge": 0.1654,
                "price_clears": True, "market_hold": 0.0585,
                "recommendation_status": "lean", "status_reasons": [], "stale": False,
            }},
        })
        result = rg.refresh(self.data, self.live, self.registry)  # must not raise
        self.assertTrue(result["props"])

    def test_orphan_with_durable_settlement_state_still_fails_closed(self):
        # The grading channel must not silently launder away a real wager
        # outcome either -- same fail-closed guarantee proven for the price
        # channel and prepare(), exercised here through refresh() directly.
        incident_id = "824077-686930-strikeouts-4"
        atomic_write_json(self.live, {
            "prices_updated_at": "2026-08-17T16:00:00Z", "grades_updated_at": None,
            "props": {incident_id: {
                "settlement_state": "hit", "settlement_authority": "live_observation",
                "settlement_observed_at": "2026-08-17T20:00:00Z",
                "settlement_source": "legacy_schema_v2", "result_actual": 5,
            }},
        })
        with self.assertRaises(RuntimeError) as ctx:
            rg.refresh(self.data, self.live, self.registry)
        self.assertIn("durable settlement/publication state", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)

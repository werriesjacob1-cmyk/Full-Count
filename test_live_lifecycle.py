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


# A real pitch already thrown -- see settlement_rules.has_authoritative_
# game_commencement's own docstring. "LIVE" status alone is not enough
# proof any more (2026-08-26 Dustin May incident); any fixture below that
# means to represent a genuinely in-progress game needs this too.
PITCH_THROWN_PLAYS = {"allPlays": [{"playEvents": [{"isPitch": True}]}]}


class LiveSettlementTests(TempLifecycle):
    def test_monotonic_live_hits_are_explicitly_provisional(self):
        cases = (("hits", 1), ("total_bases", 2), ("home_runs", 1), ("strikeouts", 5))
        for stat, needs in cases:
            with self.subTest(stat=stat):
                row = prop(stat, needs)
                self.seed(row)
                commenced_feed = {"liveData": {"plays": PITCH_THROWN_PLAYS}}
                with mock.patch.object(gr, "fetch_game_contexts",
                                        return_value={1: {"status": LIVE, "feed": commenced_feed}}), \
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
                    "feed": {"liveData": {
                        "linescore": {
                            "currentInning": inning,
                            "innings": [{"away": {"runs": runs}, "home": {"runs": 0}}],
                        },
                        "plays": PITCH_THROWN_PLAYS,
                    }},
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
        with mock.patch.object(gr, "fetch_game_contexts",
                                return_value={1: {"status": FINAL,
                                                   "feed": {"liveData": {"plays": PITCH_THROWN_PLAYS}}}}), \
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
        # Idempotent means the SETTLEMENT fact does not re-transform on a
        # second call against an already-final state -- already proven
        # field-by-field above. It does not mean the whole document is
        # byte-identical: 2026-08-19 Live Integrity PR 1 added
        # grades_checked_at, a heartbeat that correctly advances on EVERY
        # real check attempt (including a stable-final no-op one) so a
        # viewer can tell the grading channel is still actually running.
        # Excluding it here, not disabling it, is the correct fix -- a
        # frozen heartbeat would silently defeat the whole freshness
        # contract this same PR built.
        first_stable = {k: v for k, v in first.items() if k not in ("grades_checked_at", "prices_checked_at")}
        second_stable = {k: v for k, v in second.items() if k not in ("grades_checked_at", "prices_checked_at")}
        self.assertEqual(first_stable, second_stable)
        self.assertGreaterEqual(second["grades_checked_at"], first["grades_checked_at"])

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

    def test_settled_prior_slate_pick_does_not_stick_once_baked_into_props(self):
        # Regression for a real production bug found 2026-08-20: once a
        # registry-backed pick was carried into payload["props"] on some
        # earlier build (e.g. while its game was still live, before
        # settlement was known), the *next* build's first loop -- which
        # walks payload["props"], not the registry -- had no age/staleness
        # check of its own. It kept re-freezing the same row forever, so a
        # Top Pick from days ago that had long since graded hit/miss stayed
        # pinned as if it were part of tonight's board. The carry-forward
        # loop already excluded a stale settled registry entry that was NOT
        # in payload["props"]; this proves the first loop now does too, for
        # a registry entry that already IS baked into the disk payload.
        row = prop()
        registry = published_registry(row)
        live = default_live_state()
        merge_prop_fields(live, row["id"], {
            "settlement_state": "miss", "settlement_authority": "official_final",
            "settlement_observed_at": "2026-08-19T01:18:09Z",
            "settlement_source": "mlb_official_final_with_fanduel_eligibility",
            "result_actual": 0, "result_reason": "official final statistic compared with the displayed threshold",
        }, "2026-08-19T01:18:09Z", channel="grades")
        merge_prop_fields(live, row["id"], {
            "game_state": "final", "game_state_source": "mlb_game_feed_by_game_pk",
            "game_state_observed_at": "2026-08-20T20:06:22Z",
        }, "2026-08-20T20:06:22Z", channel="grades")

        # row itself (published for 2026-08-17) is already present as a
        # source_row in a payload dated two days later -- exactly what a
        # stale docs/data.json committed by an earlier build looks like.
        out = bd.reconcile_public_lifecycle(
            payload([row], date="2026-08-19"), live=live, registry=registry,
            schedule={}, now="2026-08-19T20:11:00Z",
        )
        self.assertEqual(out["props"], [])

    def test_settled_prior_slate_pick_excluded_even_after_live_overlay_is_pruned(self):
        # Regression for the REAL failure mode found deploying the fix
        # above, live, 2026-08-20: the settlement-state check worked while
        # live.json still carried the settlement fact, but the very next
        # live-update cycle's compact_live_state() legitimately pruned that
        # fact -- it is no longer in current_ids once the pick correctly
        # dropped off the board, its settlement is officially final, and it
        # is durably recorded in results/grades_*.json, which is exactly
        # what compaction is FOR. The settlement-based exclusion check then
        # found nothing, defaulted to "open", and readmitted the pick to
        # the public board a second time. Fixed by keying exclusion on game
        # state (observable from live.json's game_state, which only gets
        # pruned once the game itself is no longer "live"/"suspended"/
        # "postponed" -- i.e. never while that would matter) instead of the
        # settlement fact, which has no such guarantee.
        row = prop()
        registry = published_registry(row)
        live = default_live_state()  # no overlay entry for row at all
        out = bd.reconcile_public_lifecycle(
            payload([row], date="2026-08-19"), live=live, registry=registry,
            schedule={}, now="2026-08-19T20:11:00Z",
        )
        self.assertEqual(out["props"], [])
        # Same story via the carry-forward path (row NOT already in props).
        out2 = bd.reconcile_public_lifecycle(
            payload([], date="2026-08-19"), live=live, registry=registry,
            schedule={}, now="2026-08-19T20:11:00Z",
        )
        self.assertEqual(out2["props"], [])

    def test_why_watchouts_reflect_current_generator_not_stale_first_publication(self):
        # 2026-08-25 Weston Wilson investigation: the real production bug.
        # reconcile_public_lifecycle() used to do `row = frozen` wholesale
        # (and the final freeze pass did `{**frozen, ...}` wholesale) once a
        # game started -- correct for audit/settlement-critical facts, wrong
        # for why/watchouts, which are pure presentation. A pick first
        # published while generate_picks.py had a real directionality bug
        # (routing negative context like an elite opposing SP ERA into
        # "why" instead of "watchouts") kept showing that stale, wrong
        # explanation forever after the game started, even after the
        # generator was fixed and every later rebuild computed the correct
        # why/watchouts for the SAME candidate. This proves the fix: the
        # registry's immutable snapshot (real, deliberate audit history --
        # "what did we say at the time") still has the bad why/watchouts,
        # but the live row must show the CURRENT rebuild's corrected ones.
        stale_row = prop()
        stale_row["why"] = ["Opposing SP ERA 2.92 -- elite pitcher, tough matchup"]
        stale_row["watchouts"] = []
        registry = published_registry(stale_row)

        current_row = prop()
        current_row["why"] = ["wOBA vs xwOBA underperforming -- positive regression candidate"]
        current_row["watchouts"] = ["Opposing SP ERA 2.92 -- elite pitcher, tough matchup"]
        # Changed on purpose to prove these do NOT leak through even though
        # why/watchouts correctly does -- audit/settlement facts must stay
        # pinned to first publication, never regress to a later rebuild.
        current_row["market_odds"] = -999
        current_row["hit_probability"] = 0.01

        out = bd.reconcile_public_lifecycle(
            payload([current_row]), live=default_live_state(), registry=registry,
            schedule={1: {"status": LIVE}}, now=T2,
        )
        self.assertEqual(len(out["props"]), 1)
        published = out["props"][0]

        # PRESENTATION reflects the CURRENT (fixed) generator -- the fix.
        self.assertEqual(published["why"], current_row["why"])
        self.assertEqual(published["watchouts"], current_row["watchouts"])
        self.assertNotIn("Opposing SP ERA 2.92 -- elite pitcher, tough matchup",
                         published["why"])

        # AUDIT/SETTLEMENT-CRITICAL fields stay frozen to first publication.
        self.assertEqual(published["market_odds"], -120)
        self.assertEqual(published["hit_probability"], 0.7)
        self.assertEqual(published["recommendation_status"], "top_pick")

        # The registry's own immutable record is untouched -- historical
        # audit value ("what did we say at the time") is preserved, not
        # rewritten, exactly as instructed.
        entry = next(iter(registry["entries"].values()))
        self.assertEqual(entry["snapshot"]["why"],
                         ["Opposing SP ERA 2.92 -- elite pitcher, tough matchup"])

    def test_same_slate_settled_pick_still_shows_after_first_loop_check(self):
        # The new stale-prior-slate check must not exclude a pick that
        # settled EARLIER TODAY on the board's own slate date -- that is a
        # legitimate, still-relevant graded Top Pick from tonight's board.
        row = prop()
        registry = published_registry(row)
        live = default_live_state()
        merge_prop_fields(live, row["id"], {
            "settlement_state": "hit", "settlement_authority": "official_final",
            "settlement_observed_at": T2,
            "settlement_source": "mlb_official_final_with_fanduel_eligibility",
            "result_actual": 1, "result_reason": "official final statistic compared with the displayed threshold",
        }, T2, channel="grades")
        merge_prop_fields(live, row["id"], {
            "game_state": "final", "game_state_source": "mlb_game_feed_by_game_pk",
            "game_state_observed_at": T2,
        }, T2, channel="grades")
        out = bd.reconcile_public_lifecycle(
            payload([row], date="2026-08-17"), live=live, registry=registry,
            schedule={}, now=T2,
        )
        self.assertEqual([value["id"] for value in out["props"]], [row["id"]])
        self.assertEqual(out["props"][0]["settlement_state"], "hit")


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

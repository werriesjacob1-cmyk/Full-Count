#!/usr/bin/env python3
"""Stronger lifecycle invariant: no LIVE-derived settlement without proof
a real pitch has actually been thrown.

Real incident, 2026-08-26 (Dustin May, game_pk 823584, "Over 15.5 Outs
Recorded" Top Pick): the site showed TRENDING MISS / AWAITING FINAL 19
minutes before his game's own scheduled first pitch. The emergency
containment fix (dashboard/live_state.py's game_state() clock guard,
claude/dustin-may-lifecycle-fix-01) closed the exact incident, but an
independent audit found it insufficient on its own: it only checks the
scheduled clock, and this session's own inspection of real MLB StatsAPI
payloads (a genuinely live game, a genuinely pregame game, a completed
game -- fetched directly from statsapi.mlb.com, not assumed) proved that
every OTHER field a naive check might reach for -- abstractGameState,
gameStatus.isCurrentPitcher, linescore.currentInning,
linescore.offense/defense, even liveData.plays.allPlays itself -- can
already be populated with real-looking data before a single pitch is
thrown. MLB's pregame feed carries a "Game Advisory / Status Change -
Pre-Game" entry that is *typed* as an atBat result, complete with a real
batter/pitcher matchup.

The one field that cannot appear before real play begins is
playEvents[].isPitch == True (see settlement_rules.
has_authoritative_game_commencement's own docstring for the full
reasoning). This file locks in the stronger invariant built on that
signal, applied at the settlement boundary (dashboard/refresh_grades.py)
rather than the display layer -- game_state() and this predicate are
independent checks that cooperate, not one subsuming the other.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import grade_results as gr
from dashboard import refresh_grades as rg
from dashboard.live_state import (
    atomic_write_json, canonical_prop_id, default_live_state,
)
from dashboard.publication_registry import (
    build_publication_manifest, confirm_publication, default_registry, write_registry,
)
from dashboard.settlement_rules import has_authoritative_game_commencement


LIVE = {"abstractGameState": "Live", "detailedState": "In Progress", "codedGameState": "I"}
POSTPONED = {"abstractGameState": "Preview", "detailedState": "Postponed", "codedGameState": "P"}
CANCELLED = {"abstractGameState": "Final", "detailedState": "Cancelled", "codedGameState": "C"}
DELAYED = {"abstractGameState": "Preview", "detailedState": "Delayed Start", "codedGameState": "D"}
SUSPENDED = {"abstractGameState": "Live", "detailedState": "Suspended", "codedGameState": "U"}
T_START = "2026-08-26T23:10:00Z"      # the real Dustin May scheduled first pitch
T_BEFORE_START = "2026-08-26T22:51:19Z"  # the real observed false-live timestamp
T_AFTER_START = "2026-08-26T23:18:00Z"   # requirement-2 example: 19:18 when start was 19:10


# Real shape, trimmed to the fields that matter, of what MLB's own feed
# actually returns pregame: liveData.plays.allPlays[0] is a "Game
# Advisory / Status Change - Pre-Game" entry -- present, non-empty,
# result.type == "atBat", but its one playEvents entry is an
# administrative action, not a pitch.
PREGAME_ADVISORY_FEED = {
    "liveData": {
        "plays": {
            "allPlays": [{
                "result": {
                    "type": "atBat", "event": "Game Advisory",
                    "eventType": "game_advisory",
                    "description": "Status Change - Pre-Game",
                },
                "about": {"inning": 1, "isComplete": False},
                "playEvents": [{
                    "details": {"description": "Status Change - Pre-Game",
                                "event": "Game Advisory", "eventType": "game_advisory"},
                    "isPitch": False, "type": "action",
                }],
            }],
        },
        "linescore": {
            "currentInning": 1, "offense": {"batter": {"id": 1}}, "defense": {"pitcher": {"id": 2}},
            "innings": [{"num": 1, "home": {"hits": 0, "errors": 0}, "away": {"hits": 0, "errors": 0}}],
        },
        "boxscore": {"teams": {"away": {"players": {
            "ID669160": {"person": {"id": 669160},
                         "gameStatus": {"isCurrentPitcher": False, "isSubstitute": False}},
        }}, "home": {"players": {}}}},
    },
}

# Real shape of what the feed returns once a pitch has genuinely been
# thrown: a play with a real result (not a game_advisory), and at least
# one playEvents entry with isPitch=True and real pitchData.
REAL_PITCH_FEED = {
    "liveData": {
        "plays": {
            "allPlays": [{
                "result": {"type": "atBat", "event": "Single", "eventType": "single"},
                "about": {"inning": 1, "isComplete": True},
                "playEvents": [
                    {"details": {"call": {"code": "B", "description": "Ball"}}, "isPitch": True,
                     "pitchData": {"startSpeed": 90.0}},
                ],
            }],
        },
        "linescore": {
            "currentInning": 2,
            "innings": [{"num": 1, "home": {"runs": 0, "hits": 0}, "away": {"runs": 0, "hits": 1}}],
        },
        "boxscore": {"teams": {"away": {"players": {
            "ID669160": {"person": {"id": 669160},
                         "gameStatus": {"isCurrentPitcher": False, "isSubstitute": False}},
        }}, "home": {"players": {}}}},
    },
}


def prop(stat="pitcher_outs", needs=16, player_id=669160, game_pk=823584,
         game_start=T_START, side="over"):
    row = {
        "identity_version": 2, "type": "pitcher",
        "name": "Fixture Pitcher", "team": "A", "matchup": "A @ B", "side": "away",
        "game_pk": game_pk, "game_start": game_start, "player_id": player_id,
        "combo_player_ids": None,
        "projection": {"stat": stat, "needs": needs, "value": float(needs)},
        "stat": stat, "market_side": side,
        "prop": ("Under" if side == "under" else "Over") + f" {needs - .5} {stat}",
        "recommendation_status": "top_pick", "status_reasons": [], "hit_probability": .7,
        "market_odds": -120, "market_implied": .545, "market_edge": .155,
        "price_clears": True, "market_hold": None,
    }
    row["id"] = canonical_prop_id(row)
    return row


def payload(rows, date="2026-08-26"):
    return {
        "schema_version": 3, "identity_schema_version": 2, "date": date,
        "generated_at": T_BEFORE_START, "odds_fetched_at": T_BEFORE_START,
        "recommendation_metadata": {"model_version": "m", "selection_policy_version": "p"},
        "props": rows, "summary": {}, "families": [], "schedule": [],
    }


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class CommencementPredicateTests(unittest.TestCase):
    """Direct unit tests for the predicate itself, isolated from the
    settlement pipeline."""

    def test_pregame_advisory_play_is_not_commencement(self):
        self.assertFalse(has_authoritative_game_commencement(PREGAME_ADVISORY_FEED))

    def test_real_pitch_is_commencement(self):
        self.assertTrue(has_authoritative_game_commencement(REAL_PITCH_FEED))

    def test_currentPlay_is_also_checked_not_only_allPlays(self):
        feed = {"liveData": {"plays": {
            "allPlays": [],
            "currentPlay": {"playEvents": [{"isPitch": True}]},
        }}}
        self.assertTrue(has_authoritative_game_commencement(feed))

    def test_missing_feed_fails_closed(self):
        self.assertFalse(has_authoritative_game_commencement(None))
        self.assertFalse(has_authoritative_game_commencement({}))

    def test_malformed_plays_fails_closed_not_raises(self):
        self.assertFalse(has_authoritative_game_commencement({"liveData": {"plays": "not-a-dict"}}))
        self.assertFalse(has_authoritative_game_commencement({"liveData": {"plays": {"allPlays": "nope"}}}))
        self.assertFalse(has_authoritative_game_commencement(
            {"liveData": {"plays": {"allPlays": [{"playEvents": "nope"}]}}}))

    def test_once_thrown_a_later_pitch_missing_from_this_snapshot_still_counts(self):
        # Any single proven pitch anywhere in the play history is enough --
        # this predicate does not require the pitch to be the most recent
        # event, matching its own documented state-history-agnostic design.
        feed = {"liveData": {"plays": {"allPlays": [
            {"playEvents": [{"isPitch": True}]},
            {"playEvents": [{"isPitch": False}]},
        ]}}}
        self.assertTrue(has_authoritative_game_commencement(feed))


class TempLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data.json")
        self.live = os.path.join(self.tmp.name, "live.json")
        self.registry = os.path.join(self.tmp.name, "registry.json")

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, row):
        atomic_write_json(self.data, payload([row]))
        atomic_write_json(self.live, default_live_state())
        registry = default_registry()
        manifest = build_publication_manifest(
            payload([row]), default_live_state(), registry, "sha", T_BEFORE_START,
        )
        confirm_publication(registry, manifest, T_BEFORE_START, {"source_commit": "sha"})
        write_registry(self.registry, registry)

    def run_refresh(self, row, status, feed, grade=None):
        self.seed(row)
        context = {"status": status, "feed": feed}
        with mock.patch.object(gr, "fetch_game_contexts", return_value={row["game_pk"]: context}), \
             mock.patch.object(gr, "grade_pick", return_value=grade):
            rg.refresh(self.data, self.live, self.registry)
        return load_json(self.live)["props"][row["id"]]


class InvariantTests(TempLifecycle):
    """Requirements A-H from the governing hardening request, each
    labelled with its letter."""

    def test_A_exact_dustin_may_condition_produces_no_provisional_settlement(self):
        row = prop(game_start=T_START)
        delta = self.run_refresh(
            row, LIVE, PREGAME_ADVISORY_FEED,
            grade={"grade": "miss", "actual": 0},
        )
        self.assertNotIn(delta["settlement_state"], ("provisional_hit", "provisional_miss"))
        self.assertEqual(delta["settlement_state"], "open")

    def test_B_delayed_start_after_scheduled_time_still_produces_no_provisional_settlement(self):
        # Scheduled 19:10-equivalent, "now" is past it (T_AFTER_START), feed
        # incorrectly claims live, but still zero pitch evidence -- the
        # clock alone (already past game_start) must not be read as proof.
        row = prop(game_start=T_START)
        with mock.patch("dashboard.refresh_grades.utc_now", return_value=T_AFTER_START):
            delta = self.run_refresh(
                row, LIVE, PREGAME_ADVISORY_FEED,
                grade={"grade": "miss", "actual": 0},
            )
        self.assertNotIn(delta["settlement_state"], ("provisional_hit", "provisional_miss"))

    def test_C_genuine_live_condition_still_supports_early_hit(self):
        row = prop(stat="strikeouts", needs=5, game_start=T_START)
        delta = self.run_refresh(
            row, LIVE, REAL_PITCH_FEED,
            grade={"grade": "hit", "actual": 5},
        )
        self.assertEqual(delta["settlement_state"], "provisional_hit")

    def test_D_genuine_role_terminal_pitcher_miss_still_supported(self):
        # Real pitch proven thrown AND the listed pitcher has left the
        # mound (isCurrentPitcher False in REAL_PITCH_FEED) -- the
        # Keider Montero-class case this codebase already protects.
        row = prop(stat="pitcher_outs", needs=17, game_start=T_START)
        delta = self.run_refresh(
            row, LIVE, REAL_PITCH_FEED,
            grade={"grade": "miss", "actual": 15},
        )
        self.assertEqual(delta["settlement_state"], "provisional_miss")
        self.assertEqual(delta["settlement_source"], "mlb_live_role_terminal_pitching_change")

    def test_E_pregame_isCurrentPitcher_false_is_never_read_as_removal(self):
        # PREGAME_ADVISORY_FEED's own boxscore already carries
        # isCurrentPitcher=False for this player (he simply hasn't
        # started yet) -- must not be interpreted as "removed."
        row = prop(stat="pitcher_outs", needs=16, game_start=T_START)
        delta = self.run_refresh(
            row, LIVE, PREGAME_ADVISORY_FEED,
            grade={"grade": "miss", "actual": 0},
        )
        self.assertNotEqual(delta["settlement_state"], "provisional_miss")

    def test_F_non_settlement_statuses_unaffected(self):
        for status, expected_state in (
            (POSTPONED, "postponed"), (CANCELLED, "cancelled"), (DELAYED, "delayed"),
        ):
            with self.subTest(status=status["detailedState"]):
                row = prop(game_start=T_START)
                delta = self.run_refresh(row, status, PREGAME_ADVISORY_FEED)
                self.assertEqual(delta["game_state"], expected_state)
                self.assertNotIn(delta.get("settlement_state"),
                                  ("provisional_hit", "provisional_miss"))

    def test_F_suspended_with_prior_real_play_keeps_its_earlier_provisional_miss(self):
        # A suspended game that had already thrown real pitches before
        # suspension must not have its earlier role-terminal fact erased
        # just because it's now suspended -- suspension only stops
        # further game_state advancement, per the existing "delayed/
        # suspended/postponed/cancelled update only game state" comment
        # in refresh_grades.py (unchanged by this patch).
        row = prop(stat="pitcher_outs", needs=17, game_start=T_START)
        first = self.run_refresh(row, LIVE, REAL_PITCH_FEED, grade={"grade": "miss", "actual": 15})
        self.assertEqual(first["settlement_state"], "provisional_miss")
        with mock.patch.object(gr, "fetch_game_contexts",
                                return_value={row["game_pk"]: {"status": SUSPENDED, "feed": REAL_PITCH_FEED}}), \
             mock.patch.object(gr, "grade_pick", return_value={"grade": "miss", "actual": 15}):
            rg.refresh(self.data, self.live, self.registry)
        second = load_json(self.live)["props"][row["id"]]
        self.assertEqual(second["game_state"], "suspended")
        self.assertEqual(second["settlement_state"], "provisional_miss")

    def test_G_missing_ambiguous_commencement_evidence_fails_closed(self):
        row = prop(game_start=T_START)
        delta = self.run_refresh(row, LIVE, {}, grade={"grade": "miss", "actual": 0})
        self.assertNotIn(delta["settlement_state"], ("provisional_hit", "provisional_miss"))

    def test_H_stale_scheduled_clock_does_not_suppress_real_commencement_evidence(self):
        # game_start deliberately stored WRONG -- later than "now" -- so
        # game_state()'s own clock guard alone would force "pregame."
        # Real pitch evidence must still be trusted at the settlement
        # boundary regardless.
        stale_future_start = "2099-01-01T00:00:00Z"
        row = prop(stat="strikeouts", needs=5, game_start=stale_future_start)
        delta = self.run_refresh(
            row, LIVE, REAL_PITCH_FEED,
            grade={"grade": "hit", "actual": 5},
        )
        self.assertEqual(delta["game_state"], "live")
        self.assertEqual(delta["settlement_state"], "provisional_hit")

    def test_H_final_settlement_also_not_suppressed_by_a_stale_clock(self):
        stale_future_start = "2099-01-01T00:00:00Z"
        row = prop(stat="strikeouts", needs=5, game_start=stale_future_start)
        final_status = {"abstractGameState": "Final", "detailedState": "Final", "codedGameState": "F"}
        with mock.patch.object(gr, "grade_public_pick",
                                return_value={"settlement_state": "hit", "actual": 5}):
            delta = self.run_refresh(row, final_status, REAL_PITCH_FEED)
        self.assertEqual(delta["game_state"], "final")
        self.assertEqual(delta["settlement_state"], "hit")


if __name__ == "__main__":
    unittest.main(verbosity=2)

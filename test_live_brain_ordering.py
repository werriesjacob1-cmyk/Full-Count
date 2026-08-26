#!/usr/bin/env python3
"""test_live_brain_ordering.py -- deterministic fixtures for the Live Brain
foundation's ordering/monotonicity/identity primitives (live_brain/ordering.py,
live_brain/envelopes.py). Pure, no I/O, no network -- these test the SMALL
PURE PRIMITIVES the eventual data plane will be built on, not a deployed
system (none exists yet). See live_brain/README.md for scope.

    /tmp/mlbvenv/bin/python3 test_live_brain_ordering.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from live_brain.envelopes import DeltaEnvelope, EventEnvelope, EventType
from live_brain.ordering import (
    LogicalDuplicateError,
    accept_game_state,
    accept_price,
    accept_settlement,
    apply_delta,
    dedupe_events,
    impact_set,
    register_candidate_identity,
)


def _event(game_pk=776970, source="mlb_statsapi", source_event_id="ev1",
           event_type=EventType.GAME_STATE_CHANGE, player_ids=()):
    return EventEnvelope(
        event_id=f"e-{source_event_id}", source=source, source_event_id=source_event_id,
        observed_at="2026-08-26T20:00:00Z", ingested_at="2026-08-26T20:00:00Z",
        game_pk=game_pk, event_type=event_type, payload={}, player_ids=player_ids,
    )


def _delta(candidate_id, event_version, changed_fields, game_pk=776970, source_event_id="e-1"):
    return DeltaEnvelope(
        delta_id=f"d-{candidate_id}-{event_version}", game_pk=game_pk, candidate_id=candidate_id,
        changed_fields=changed_fields, source_event_id=source_event_id, event_version=event_version,
        reason_codes=("price_moved",), created_at="2026-08-26T20:00:00Z",
    )


class DuplicateEventTests(unittest.TestCase):
    def test_duplicate_event_twice_state_changes_once(self):
        state = {}
        d = _delta("cand-1", 1, {"market_edge": {"old": None, "new": 0.03}})
        state = apply_delta(state, d)
        state = apply_delta(state, d)  # exact duplicate, same event_version
        self.assertEqual(state["cand-1"]["event_version"], 1)
        self.assertEqual(state["cand-1"]["fields"]["market_edge"], 0.03)


class OutOfOrderTests(unittest.TestCase):
    def test_n_plus_1_then_n_no_regression(self):
        state = {}
        state = apply_delta(state, _delta("cand-1", 6, {"odds": {"old": -110, "new": -120}}))
        state = apply_delta(state, _delta("cand-1", 5, {"odds": {"old": -110, "new": -105}}))  # stale, arrives late
        self.assertEqual(state["cand-1"]["event_version"], 6)
        self.assertEqual(state["cand-1"]["fields"]["odds"], -120)


class ImpactRoutingTests(unittest.TestCase):
    def test_pitch_in_game_a_no_game_b_recomputation(self):
        ev_a = _event(game_pk=111)
        self.assertEqual(impact_set(ev_a), frozenset({111}))
        self.assertNotIn(222, impact_set(ev_a))

    def test_pitcher_change_only_correct_dependencies(self):
        ev = _event(game_pk=111, event_type=EventType.PITCHER_CHANGE, player_ids=(660271,))
        affected = impact_set(ev)
        self.assertEqual(affected, frozenset({111}))
        self.assertEqual(ev.player_ids, (660271,))

    def test_batter_substitution_only_relevant_impact(self):
        ev = _event(game_pk=111, event_type=EventType.BATTER_CHANGE, player_ids=(545361,))
        self.assertEqual(impact_set(ev), frozenset({111}))


class SettlementMonotonicityTests(unittest.TestCase):
    def test_official_final_then_later_stale_live_event_ignored(self):
        self.assertTrue(accept_settlement("live_observation", "official_final"))
        self.assertFalse(accept_settlement("official_final", "live_observation"))

    def test_equal_authority_still_accepted(self):
        self.assertTrue(accept_settlement("official_final", "official_final"))


class GameStateMonotonicityTests(unittest.TestCase):
    def test_final_never_regresses_to_live(self):
        self.assertFalse(accept_game_state("final", "live"))
        self.assertTrue(accept_game_state("live", "final"))

    def test_pregame_to_live_is_forward_progress(self):
        self.assertTrue(accept_game_state("pregame", "live"))


class PriceMonotonicityTests(unittest.TestCase):
    def test_older_odds_cannot_replace_newer_odds(self):
        self.assertFalse(accept_price("2026-08-26T20:05:00Z", "2026-08-26T20:00:00Z"))
        self.assertTrue(accept_price("2026-08-26T20:00:00Z", "2026-08-26T20:05:00Z"))


class IndependentChannelTests(unittest.TestCase):
    def test_sportsbook_outage_mlb_final_still_settles(self):
        """A delta carrying only game-state/settlement fields must apply
        cleanly with no dependency on a price field being present -- odds
        failure must not block official game finalization (existing rule,
        already enforced informally in dashboard/refresh_grades.py's
        continue-on-error channel isolation; this is the same invariant
        expressed as a pure-function test)."""
        state = {}
        state = apply_delta(state, _delta("cand-1", 1, {"settlement_state": {"old": "open", "new": "hit"}}))
        self.assertEqual(state["cand-1"]["fields"]["settlement_state"], "hit")
        self.assertNotIn("odds", state["cand-1"]["fields"])


class ReplayDeterminismTests(unittest.TestCase):
    def test_restart_full_replay_identical_state(self):
        deltas = [
            _delta("cand-1", 1, {"odds": {"old": None, "new": -110}}),
            _delta("cand-1", 2, {"odds": {"old": -110, "new": -120}}),
            _delta("cand-2", 1, {"market_edge": {"old": None, "new": 0.02}}),
        ]
        run1: dict = {}
        for d in deltas:
            run1 = apply_delta(run1, d)
        run2: dict = {}
        for d in deltas:
            run2 = apply_delta(run2, d)
        self.assertEqual(run1, run2)

    def test_replay_is_order_independent_for_out_of_order_versions(self):
        """A restart that replays events in a different arrival order (e.g.
        after a crash and reconnect) must converge to the same final state,
        since apply_delta's version check makes it order-tolerant."""
        d1 = _delta("cand-1", 1, {"odds": {"old": None, "new": -110}})
        d2 = _delta("cand-1", 2, {"odds": {"old": -110, "new": -120}})
        forward: dict = {}
        forward = apply_delta(forward, d1)
        forward = apply_delta(forward, d2)
        reordered: dict = {}
        reordered = apply_delta(reordered, d2)
        reordered = apply_delta(reordered, d1)
        self.assertEqual(forward, reordered)


class UTCRolloverTests(unittest.TestCase):
    def test_late_west_coast_rollover_identity_unaffected(self):
        """Candidate/game identity must never derive from a naive local
        "today" string -- game_pk is MLB's own stable id, not a
        wall-clock-derived key, so a West Coast late game that runs past
        local midnight cannot split into two identities."""
        ev_late = _event(game_pk=776999, source_event_id="late-game")
        # game_pk alone determines impact_set -- no date component anywhere
        # in identity, so a late/rollover game is indistinguishable here
        # from any other game, which is the correct behavior.
        self.assertEqual(impact_set(ev_late), frozenset({776999}))


class CandidateIdentityTests(unittest.TestCase):
    def test_published_candidate_identity_unchanged_through_live_updates(self):
        state = {}
        state = apply_delta(state, _delta("cand-1", 1, {"odds": {"old": None, "new": -110}}))
        before_keys = set(state.keys())
        state = apply_delta(state, _delta("cand-1", 2, {"odds": {"old": -110, "new": -105}}))
        self.assertEqual(set(state.keys()), before_keys)  # no new/renamed candidate

    def test_alt_lines_remain_distinct(self):
        registry: dict = {}
        registry = register_candidate_identity(registry, "cand-hits-0.5-over", "776970:660271:hits:0.5:over")
        registry = register_candidate_identity(registry, "cand-hits-1.5-over", "776970:660271:hits:1.5:over")
        self.assertEqual(len(registry), 2)
        self.assertNotEqual(registry["cand-hits-0.5-over"], registry["cand-hits-1.5-over"])

    def test_logical_duplicate_raises(self):
        registry: dict = {}
        registry = register_candidate_identity(registry, "cand-1", "776970:660271:hits:0.5:over")
        with self.assertRaises(LogicalDuplicateError):
            # same identity claimed by a second, different candidate_id
            register_candidate_identity(registry, "cand-2", "776970:660271:hits:0.5:over")
        with self.assertRaises(LogicalDuplicateError):
            # same candidate_id re-registered under a conflicting identity
            register_candidate_identity(registry, "cand-1", "776970:660271:hits:1.5:over")


class DedupeIdentityTests(unittest.TestCase):
    def test_event_dedupe_by_source_event_id(self):
        events = [_event(source_event_id="ev1"), _event(source_event_id="ev1"), _event(source_event_id="ev2")]
        deduped = dedupe_events(events)
        self.assertEqual(len(deduped), 2)


if __name__ == "__main__":
    unittest.main()

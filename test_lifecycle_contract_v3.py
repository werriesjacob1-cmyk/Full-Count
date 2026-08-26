#!/usr/bin/env python3
"""Adversarial contract tests for the v3 public lifecycle state."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from unittest import mock

from dashboard.live_state import (
    GAME_STATES,
    SETTLEMENT_STATES,
    atomic_write_json,
    before_betting_cutoff,
    canonical_prop_id,
    compact_live_state,
    default_live_state,
    game_state,
    merge_live_states,
    merge_prop_fields,
    parse_utc,
    prop_identity_key,
    stable_prop_id,
    validate_payload_identities,
)


T0 = "2026-08-17T17:59:00Z"
T1 = "2026-08-17T18:00:00+00:00"
T2 = "2026-08-17T18:05:00Z"
T3 = "2026-08-17T18:10:00+00:00"


def row(**overrides):
    base = {
        "identity_version": 2,
        "type": "batter",
        "game_pk": 1,
        "game_start": T1,
        "player_id": 101,
        "combo_player_ids": None,
        "projection": {"stat": "hits", "needs": 1},
        "stat": "hits",
        "market_side": "over",
        "recommendation_status": "top_pick",
    }
    base.update(overrides)
    base["id"] = canonical_prop_id(base)
    return base


def settlement(state, authority, observed_at, actual=None, reason=None):
    return {
        "settlement_state": state,
        "settlement_authority": authority,
        "settlement_observed_at": observed_at,
        "settlement_source": "mlb_live_feed" if authority == "live_observation" else "mlb_official_final",
        "result_actual": actual,
        "result_reason": reason,
    }


class TimestampTests(unittest.TestCase):
    def test_new_timestamp_contract(self):
        self.assertIsNone(parse_utc("2026-08-17T18:00:00"))
        self.assertIsNotNone(parse_utc("2026-08-17T18:00:00Z"))
        self.assertIsNotNone(parse_utc("2026-08-17T18:00:00+00:00"))
        self.assertIsNone(parse_utc("2026-08-17T13:00:00-05:00"))
        self.assertIsNone(parse_utc("not-a-timestamp"))
        live = default_live_state()
        with self.assertRaises(ValueError):
            merge_prop_fields(live, "x", {"market_odds": -110}, "2026-08-17T18:00:00")


class GameStateTests(unittest.TestCase):
    def test_supported_mappings(self):
        cases = {
            "pregame": {"abstractGameState": "Preview", "detailedState": "Scheduled", "codedGameState": "S"},
            "live": {"abstractGameState": "Live", "detailedState": "In Progress", "codedGameState": "I"},
            "delayed": {"abstractGameState": "Preview", "detailedState": "Delayed Start", "codedGameState": "D"},
            "suspended": {"abstractGameState": "Live", "detailedState": "Suspended", "codedGameState": "U"},
            "postponed": {"abstractGameState": "Preview", "detailedState": "Postponed", "codedGameState": "P"},
            "cancelled": {"abstractGameState": "Final", "detailedState": "Cancelled", "codedGameState": "C"},
            "final": {"abstractGameState": "Final", "detailedState": "Final", "codedGameState": "F"},
            "unknown": {"abstractGameState": "Mystery", "detailedState": "Administrative Review", "codedGameState": "?"},
        }
        for expected, status in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(game_state(status), expected)
        self.assertEqual(set(cases), set(GAME_STATES))

    def test_empty_status_does_not_guess_from_clock(self):
        self.assertEqual(game_state({}, row=row(), now=T3), "unknown")

    def test_live_feed_before_scheduled_start_never_settles(self):
        """Real 2026-08-26 incident: MLB's feed reported abstractGameState
        =="live" 19 minutes before game_pk 823584's own scheduled first
        pitch, and refresh_grades.py wrote a role-terminal provisional_miss
        for Dustin May off that claim -- a customer saw a settled prop
        before the game had thrown a pitch. game_state() must refuse "live"
        (and "final") before the clock says the game could have started,
        regardless of what the feed claims."""
        live_status = {"abstractGameState": "Live", "detailedState": "In Progress", "codedGameState": "I"}
        final_status = {"abstractGameState": "Final", "detailedState": "Final", "codedGameState": "F"}
        before_start = row(game_start=T1)
        self.assertEqual(game_state(live_status, row=before_start, now=T0), "pregame")
        self.assertEqual(game_state(final_status, row=before_start, now=T0), "pregame")
        # Same feed claim, same row, only the clock changed -- must resolve
        # normally once actually at/after the scheduled start.
        self.assertEqual(game_state(live_status, row=before_start, now=T2), "live")
        self.assertEqual(game_state(final_status, row=before_start, now=T2), "final")

    def test_pregame_guard_never_suppresses_non_settlement_states(self):
        """cancelled/postponed/delayed/suspended are legitimate
        pregame-announceable facts, not settlement claims -- the clock
        guard added for the Dustin May incident must never touch them."""
        before_start = row(game_start=T1)
        cases = {
            "cancelled": {"abstractGameState": "Final", "detailedState": "Cancelled", "codedGameState": "C"},
            "postponed": {"abstractGameState": "Preview", "detailedState": "Postponed", "codedGameState": "P"},
            "delayed": {"abstractGameState": "Preview", "detailedState": "Delayed Start", "codedGameState": "D"},
            "suspended": {"abstractGameState": "Live", "detailedState": "Suspended", "codedGameState": "U"},
        }
        for expected, status in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(game_state(status, row=before_start, now=T0), expected)

    def test_missing_row_or_clock_falls_back_to_unguarded_mapping(self):
        """Callers that don't pass row/now (the plain-mapping test above)
        must be completely unaffected by the clock guard."""
        live_status = {"abstractGameState": "Live", "detailedState": "In Progress", "codedGameState": "I"}
        self.assertEqual(game_state(live_status), "live")
        self.assertEqual(game_state(live_status, row=row(game_start=T1)), "live")
        self.assertEqual(game_state(live_status, now=T0), "live")

    def test_scheduled_start_is_absolute_publication_cutoff(self):
        self.assertTrue(before_betting_cutoff(row(), T0))
        self.assertFalse(before_betting_cutoff(row(), T1))
        self.assertFalse(before_betting_cutoff(row(), T2))


class IdentityTests(unittest.TestCase):
    def test_combo_is_commutative_and_identity_bearing_fields_change_id(self):
        a = row(type="pitcher", player_id=11, combo_player_ids=[11, 22],
                projection={"stat": "combined_strikeouts", "needs": 12},
                stat="combined_strikeouts")
        b = row(type="pitcher", player_id=22, combo_player_ids=[22, 11],
                projection={"stat": "combined_strikeouts", "needs": 12},
                stat="combined_strikeouts")
        self.assertEqual(prop_identity_key(a), prop_identity_key(b))
        self.assertEqual(a["id"], b["id"])
        with self.assertRaises(ValueError):
            canonical_prop_id({**a, "combo_player_ids": [11, 11]})
        for changed in (
            {"combo_player_ids": [11, 33]},
            {"projection": {"stat": "combined_strikeouts", "needs": 13}},
            {"market_side": "under"},
            {"projection": {"stat": "pitcher_outs", "needs": 12}, "stat": "pitcher_outs", "combo_player_ids": None},
            {"game_pk": 2},
        ):
            candidate = copy.deepcopy(a)
            candidate.update(changed)
            self.assertNotEqual(canonical_prop_id(candidate), a["id"])

    def test_normal_and_game_level_identity_never_use_name(self):
        normal = row(name="Name A")
        renamed = copy.deepcopy(normal)
        renamed["name"] = "Name B"
        self.assertEqual(canonical_prop_id(normal), canonical_prop_id(renamed))
        game = row(player_id=None, type="game", stat="nrfi_combined",
                   projection={"stat": "nrfi_combined"}, lean="NRFI")
        self.assertIn(":game:nrfi_combined:0.5:nrfi", game["id"])
        synthetic = copy.deepcopy(game)
        synthetic["player_id"] = "nrfi_1"
        self.assertEqual(canonical_prop_id(synthetic), game["id"])
        yrfi = copy.deepcopy(game)
        yrfi["lean"] = "YRFI"
        self.assertNotEqual(canonical_prop_id(yrfi), game["id"])

    def test_inconsistent_id_and_duplicates_fail_closed(self):
        valid = row()
        bad = copy.deepcopy(valid)
        bad["id"] = "claimed-wrong-id"
        with self.assertRaises(ValueError):
            stable_prop_id(bad)
        with self.assertRaises(ValueError):
            validate_payload_identities({"props": [valid, copy.deepcopy(valid)]})


class ResultAuthorityTests(unittest.TestCase):
    def test_live_provisional_can_be_confirmed_or_corrected(self):
        pid = row()["id"]
        live = default_live_state()
        merge_prop_fields(live, pid, settlement("provisional_hit", "live_observation", T1, 1, "threshold reached"), T1, channel="grades")
        merge_prop_fields(live, pid, settlement("hit", "official_final", T2, 1, "official final"), T2, channel="grades")
        self.assertEqual(live["props"][pid]["settlement_state"], "hit")

        corrected = default_live_state()
        merge_prop_fields(corrected, pid, settlement("provisional_hit", "live_observation", T1, 1, "initial hit"), T1, channel="grades")
        merge_prop_fields(corrected, pid, settlement("miss", "official_final", T2, 0, "official scoring correction"), T2, channel="grades")
        fact = corrected["props"][pid]
        self.assertEqual(fact["settlement_state"], "miss")
        self.assertEqual(fact["result_actual"], 0)
        self.assertEqual(fact["result_reason"], "official scoring correction")

    def test_stale_or_lower_authority_cannot_replace_final(self):
        pid = row()["id"]
        base = default_live_state()
        merge_prop_fields(base, pid, settlement("miss", "official_final", T2, 0, "official miss"), T2, channel="grades")
        stale = default_live_state()
        merge_prop_fields(stale, pid, settlement("provisional_hit", "live_observation", T3, 1, "late stale poll"), T3, channel="grades")
        merged = merge_live_states(base, stale)
        self.assertEqual(merged["props"][pid]["settlement_state"], "miss")
        self.assertEqual(merged["props"][pid]["result_actual"], 0)

    def test_final_fact_is_atomic_and_idempotent(self):
        pid = row()["id"]
        live = default_live_state()
        fact = settlement("hit", "official_final", T2, 1, "official hit")
        merge_prop_fields(live, pid, fact, T2, channel="grades")
        first = copy.deepcopy(live)
        merge_prop_fields(live, pid, fact, T2, channel="grades")
        self.assertEqual(live, first)
        with self.assertRaises(ValueError):
            merge_prop_fields(live, pid, {"settlement_state": "miss"}, T3, channel="grades")

    def test_unknown_game_state_preserves_last_known_good(self):
        pid = row()["id"]
        live = default_live_state()
        merge_prop_fields(live, pid, {
            "game_state": "live", "game_state_observed_at": T1,
            "game_state_source": "mlb_live_feed",
        }, T1, channel="grades")
        merge_prop_fields(live, pid, {
            "game_state": "unknown", "game_state_observed_at": T2,
            "game_state_source": "mlb_live_feed_failure",
        }, T2, channel="grades")
        self.assertEqual(live["props"][pid]["game_state"], "live")


class FileAndRetentionTests(unittest.TestCase):
    def test_corruption_and_failed_replace_preserve_prior_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "live.json")
            atomic_write_json(path, {"valid": True})
            with open(path, "rb") as handle:
                before = handle.read()
            with mock.patch("dashboard.live_state.os.replace", side_effect=OSError("simulated")):
                with self.assertRaises(OSError):
                    atomic_write_json(path, {"valid": False})
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), before)
            self.assertFalse(any(name.endswith(".tmp") for name in os.listdir(root)))

    def test_compaction_requires_all_durability_proofs(self):
        rows = {
            "open": settlement("open", "none", T1),
            "provisional": settlement("provisional_hit", "live_observation", T1, 1),
            "terminal-not-durable": settlement("hit", "official_final", T1, 1),
            "terminal-durable": settlement("miss", "official_final", T1, 0),
        }
        live = default_live_state()
        for pid, fact in rows.items():
            merge_prop_fields(live, pid, fact, T1, channel="grades")
        compacted = compact_live_state(
            live, current_ids=set(), published_ids=set(rows),
            durable_settlements={"terminal-durable": ("miss", T1)},
            protected_game_states={"open": "suspended"},
        )
        self.assertEqual(set(compacted["props"]), {"open", "provisional", "terminal-not-durable"})
        self.assertEqual(compacted, compact_live_state(
            compacted, current_ids=set(), published_ids=set(rows),
            durable_settlements={"terminal-durable": ("miss", T1)},
            protected_game_states={"open": "suspended"},
        ))


if __name__ == "__main__":
    unittest.main(verbosity=2)

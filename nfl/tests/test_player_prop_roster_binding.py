#!/usr/bin/env python3
"""Contracts for non-passing player-prop candidate -> roster identity binding."""
import unittest

from nfl.normalize import player_prop_roster_binding as binding


RUSHING_CANDIDATE = {
    "event_id": "999",
    "event_name": "Detroit Lions @ Buffalo Bills",
    "market": "rushing_yards",
    "player_name": "Jahmyr Gibbs",
    "line": 64.5,
    "over_odds": -115,
    "under_odds": -115,
}

ROSTER = [
    {
        "season": "2026",
        "team": "DET",
        "position": "RB",
        "depth_chart_position": "RB",
        "full_name": "Jahmyr Gibbs",
        "gsis_id": "00-0039164",
        "esb_id": "GIB123456",
        "status": "ACT",
    },
    {
        "season": "2026",
        "team": "BUF",
        "position": "QB",
        "depth_chart_position": "QB",
        "full_name": "Josh Allen",
        "gsis_id": "00-0034857",
        "esb_id": "ALL401886",
        "status": "ACT",
    },
]


class PlayerPropRosterBindingTests(unittest.TestCase):
    def test_exact_rb_rushing_candidate_in_event_binds(self):
        out = binding.bind_player_prop_candidate(
            RUSHING_CANDIDATE, ROSTER, season=2026
        )
        self.assertEqual(out["binding_status"], "BOUND")
        self.assertEqual(out["team"], "DET")
        self.assertEqual(out["gsis_id"], "00-0039164")
        self.assertEqual(out["event_away_team"], "DET")
        self.assertEqual(out["event_home_team"], "BUF")

    def test_qb_rushing_candidate_binds(self):
        candidate = {**RUSHING_CANDIDATE, "player_name": "Josh Allen"}
        out = binding.bind_player_prop_candidate(candidate, ROSTER, season=2026)
        self.assertEqual(out["binding_status"], "BOUND")
        self.assertEqual(out["team"], "BUF")

    def test_kicker_rushing_candidate_fails_position(self):
        roster = [{
            **ROSTER[0], "position": "K", "depth_chart_position": "K",
        }]
        out = binding.bind_player_prop_candidate(
            RUSHING_CANDIDATE, roster, season=2026
        )
        self.assertEqual(out["binding_status"], "POSITION_MISMATCH")
        self.assertIsNone(out["gsis_id"])

    def test_record_a_sack_requires_defensive_position(self):
        candidate = {
            **RUSHING_CANDIDATE, "market": "record_a_sack",
            "player_name": "Aidan Hutchinson",
        }
        roster = [{
            "season": "2026", "team": "DET", "position": "DE",
            "depth_chart_position": "DE", "full_name": "Aidan Hutchinson",
            "gsis_id": "00-0038543", "esb_id": None, "status": "ACT",
        }]
        out = binding.bind_player_prop_candidate(candidate, roster, season=2026)
        self.assertEqual(out["binding_status"], "BOUND")

    def test_record_a_sack_rejects_offensive_position(self):
        candidate = {
            **RUSHING_CANDIDATE, "market": "record_a_sack",
        }
        out = binding.bind_player_prop_candidate(
            candidate, ROSTER, season=2026
        )
        self.assertEqual(out["binding_status"], "POSITION_MISMATCH")

    def test_anytime_touchdown_allows_te(self):
        candidate = {
            **RUSHING_CANDIDATE, "market": "anytime_touchdown",
            "player_name": "Sam LaPorta",
        }
        roster = [{
            "season": "2026", "team": "DET", "position": "TE",
            "depth_chart_position": "TE", "full_name": "Sam LaPorta",
            "gsis_id": "00-0039341", "esb_id": None, "status": "ACT",
        }]
        out = binding.bind_player_prop_candidate(candidate, roster, season=2026)
        self.assertEqual(out["binding_status"], "BOUND")

    def test_player_on_team_outside_event_is_unresolved(self):
        roster = [{**ROSTER[0], "team": "DAL"}]
        out = binding.bind_player_prop_candidate(
            RUSHING_CANDIDATE, roster, season=2026
        )
        self.assertEqual(out["binding_status"], "UNRESOLVED_PLAYER")
        self.assertIsNone(out["gsis_id"])

    def test_duplicate_exact_event_position_match_is_ambiguous(self):
        roster = ROSTER + [{**ROSTER[0], "team": "DET"}]
        out = binding.bind_player_prop_candidate(
            RUSHING_CANDIDATE, roster, season=2026
        )
        self.assertEqual(out["binding_status"], "AMBIGUOUS_PLAYER")
        self.assertEqual(out["candidate_count"], 2)

    def test_missing_gsis_id_fails_closed(self):
        roster = [{**ROSTER[0], "gsis_id": ""}]
        out = binding.bind_player_prop_candidate(
            RUSHING_CANDIDATE, roster, season=2026
        )
        self.assertEqual(out["binding_status"], "MISSING_DURABLE_ID")

    def test_unknown_event_team_fails_closed(self):
        bad = {**RUSHING_CANDIDATE, "event_name": "Mystery Club @ Buffalo Bills"}
        out = binding.bind_player_prop_candidate(bad, ROSTER, season=2026)
        self.assertEqual(out["binding_status"], "UNRESOLVED_GAME_TEAMS")

    def test_unsupported_market_is_rejected(self):
        bad = {**RUSHING_CANDIDATE, "market": "passing_yards"}
        with self.assertRaisesRegex(ValueError, "passing_yards"):
            binding.bind_player_prop_candidate(bad, ROSTER, season=2026)

    def test_every_normalizer_canonical_market_has_a_position_group(self):
        from nfl.normalize.player_prop_markets import (
            PRIMARY_STAT_MARKETS, ALT_LADDER_MARKETS, SINGLE_THRESHOLD_MARKETS,
        )
        canonical_markets = set()
        for _mtype, entry in PRIMARY_STAT_MARKETS.items():
            canonical_markets.add(entry[0])
        for _mtype, entry in ALT_LADDER_MARKETS.items():
            canonical_markets.add(entry[0])
        for _mtype, entry in SINGLE_THRESHOLD_MARKETS.items():
            canonical_markets.add(entry[0])
        self.assertEqual(
            canonical_markets, set(binding.MARKET_POSITION_GROUPS)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

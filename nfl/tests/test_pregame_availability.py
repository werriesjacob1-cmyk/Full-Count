#!/usr/bin/env python3
"""Contracts for fail-closed official-inactives availability gating."""
import unittest

from nfl.normalize import pregame_availability as availability


CANDIDATE = {
    "binding_status": "BOUND",
    "player_name": "Jalen Hurts",
    "gsis_id": "00-0036389",
    "team": "PHI",
    "event_away_team": "PHI",
    "event_home_team": "KC",
}


def report(*, include_candidate=False, unresolved_candidate=False):
    phi_players = [
        {
            "binding_status": "BOUND",
            "player_name": "Other Eagle",
            "gsis_id": "00-0000001",
            "team": "PHI",
        }
    ]
    if include_candidate:
        phi_players.append({
            "binding_status": "BOUND",
            "player_name": "Jalen Hurts",
            "gsis_id": "00-0036389",
            "team": "PHI",
        })
    if unresolved_candidate:
        phi_players.append({
            "binding_status": "UNRESOLVED_PLAYER",
            "player_name": "Jalen Hurts",
            "gsis_id": None,
            "team": "PHI",
        })
    return {
        "teams": [
            {"team": "PHI", "players": phi_players},
            {
                "team": "KC",
                "players": [{
                    "binding_status": "BOUND",
                    "player_name": "Other Chief",
                    "gsis_id": "00-0000002",
                    "team": "KC",
                }],
            },
        ]
    }


class PregameAvailabilityTests(unittest.TestCase):
    def test_absence_is_usable_only_with_both_event_teams_covered(self):
        out = availability.evaluate_candidate(CANDIDATE, [report()])
        self.assertEqual(out["availability_status"], "NOT_LISTED_INACTIVE")
        self.assertTrue(out["availability_gate_pass"])

    def test_listed_candidate_is_inactive(self):
        out = availability.evaluate_candidate(
            CANDIDATE,
            [report(include_candidate=True)],
        )
        self.assertEqual(out["availability_status"], "LISTED_INACTIVE")
        self.assertFalse(out["availability_gate_pass"])

    def test_one_team_only_is_not_negative_evidence(self):
        r = report()
        r["teams"] = r["teams"][:1]
        out = availability.evaluate_candidate(CANDIDATE, [r])
        self.assertEqual(out["availability_status"], "UNKNOWN_GAME_COVERAGE")
        self.assertFalse(out["availability_gate_pass"])

    def test_unresolved_same_name_blocks_clearance(self):
        out = availability.evaluate_candidate(
            CANDIDATE,
            [report(unresolved_candidate=True)],
        )
        self.assertEqual(
            out["availability_status"],
            "UNKNOWN_PLAYER_BINDING",
        )
        self.assertFalse(out["availability_gate_pass"])

    def test_inactive_evidence_dominates_other_clean_report(self):
        out = availability.evaluate_candidate(
            CANDIDATE,
            [report(), report(include_candidate=True)],
        )
        self.assertEqual(out["availability_status"], "LISTED_INACTIVE")
        self.assertFalse(out["availability_gate_pass"])

    def test_unbound_market_candidate_fails_closed(self):
        bad = {**CANDIDATE, "binding_status": "UNRESOLVED_PLAYER"}
        out = availability.evaluate_candidate(bad, [report()])
        self.assertEqual(
            out["availability_status"],
            "UNKNOWN_CANDIDATE_IDENTITY",
        )
        self.assertFalse(out["availability_gate_pass"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

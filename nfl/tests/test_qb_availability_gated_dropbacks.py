#!/usr/bin/env python3
"""Unit and adversarial tests for `qb_availability_gated_dropbacks` -- the
real current-week-safe QB-availability gate (Mission 9 Workstream B).
"""
from __future__ import annotations

import unittest

from nfl.research.injury_availability_features import InjuryAvailabilityError
from nfl.research.qb_availability_gated_dropbacks import (
    CONFIRMED_AVAILABLE,
    DISPUTED,
    EXPECTED_UNAVAILABLE,
    UNKNOWN,
    classify_current_week_qb_availability,
    predict_team_pass_dropbacks_availability_gated,
)


def _injury_row(team, season, week, gsis_id, report_status, *, game_type="REG", position="QB"):
    return {
        "season": season, "game_type": game_type, "team": team, "week": week,
        "gsis_id": gsis_id, "position": position, "report_status": report_status,
    }


def _starter(team, season, week, player_id, attempts=25.0, opponent="OPP"):
    return {
        "season": season, "week": week, "team": team,
        "opponent_team": opponent, "starter_player_id": player_id,
        "starter_attempts": attempts,
    }


def _box_row(team, season, week, attempts=30.0, sacks=2.0):
    return {"team": team, "season": season, "week": week, "attempts": attempts, "sacks_suffered": sacks}


class ClassifyCurrentWeekQBAvailabilityTests(unittest.TestCase):
    def test_not_listed_at_all_is_confirmed_available(self):
        result = classify_current_week_qb_availability(
            team="KC", opponent_team="SEA", target_season=2025, target_week=6,
            incumbent_player_id="QB_A", injury_rows=[],
        )
        self.assertEqual(result["incumbent_availability_bucket"], CONFIRMED_AVAILABLE)
        self.assertIsNone(result["report_status_raw"])

    def test_listed_out_is_expected_unavailable(self):
        rows = [_injury_row("KC", 2025, 6, "QB_A", "Out")]
        result = classify_current_week_qb_availability(
            team="KC", opponent_team="SEA", target_season=2025, target_week=6,
            incumbent_player_id="QB_A", injury_rows=rows,
        )
        self.assertEqual(result["incumbent_availability_bucket"], EXPECTED_UNAVAILABLE)
        self.assertEqual(result["availability_status"], "LISTED_OUT")

    def test_listed_doubtful_is_expected_unavailable(self):
        rows = [_injury_row("KC", 2025, 6, "QB_A", "Doubtful")]
        result = classify_current_week_qb_availability(
            team="KC", opponent_team="SEA", target_season=2025, target_week=6,
            incumbent_player_id="QB_A", injury_rows=rows,
        )
        self.assertEqual(result["incumbent_availability_bucket"], EXPECTED_UNAVAILABLE)

    def test_listed_questionable_is_disputed_not_confirmed(self):
        rows = [_injury_row("KC", 2025, 6, "QB_A", "Questionable")]
        result = classify_current_week_qb_availability(
            team="KC", opponent_team="SEA", target_season=2025, target_week=6,
            incumbent_player_id="QB_A", injury_rows=rows,
        )
        self.assertEqual(result["incumbent_availability_bucket"], DISPUTED)
        self.assertEqual(result["report_status_raw"], "QUESTIONABLE")

    def test_listed_with_blank_report_status_is_confirmed_available(self):
        # Real 2024 data finding: a player can carry a real filed row (e.g.
        # practice-report-only participation) with a blank report_status --
        # distinct from "no row at all" but carrying no real game-affecting
        # designation, so it must classify the same as not-listed, not raise.
        rows = [_injury_row("KC", 2025, 6, "QB_A", "")]
        result = classify_current_week_qb_availability(
            team="KC", opponent_team="SEA", target_season=2025, target_week=6,
            incumbent_player_id="QB_A", injury_rows=rows,
        )
        self.assertEqual(result["incumbent_availability_bucket"], CONFIRMED_AVAILABLE)
        self.assertEqual(result["report_status_raw"], "")

    def test_no_real_incumbent_identity_is_unknown_not_healthy(self):
        result = classify_current_week_qb_availability(
            team="KC", opponent_team="SEA", target_season=2025, target_week=6,
            incumbent_player_id=None, injury_rows=[],
        )
        self.assertEqual(result["incumbent_availability_bucket"], UNKNOWN)

    def test_season_not_covered_by_source_is_unknown_not_healthy(self):
        result = classify_current_week_qb_availability(
            team="KC", opponent_team="SEA", target_season=2005, target_week=6,
            incumbent_player_id="QB_A", injury_rows=[],
        )
        self.assertEqual(result["incumbent_availability_bucket"], UNKNOWN)

    def test_never_reads_a_different_teams_or_weeks_injury_row(self):
        # A real row for a different team, and a real row for the same
        # player at a different week, must not leak into this week's
        # classification for KC.
        rows = [
            _injury_row("SEA", 2025, 6, "QB_A", "Out"),
            _injury_row("KC", 2025, 5, "QB_A", "Out"),
        ]
        result = classify_current_week_qb_availability(
            team="KC", opponent_team="SEA", target_season=2025, target_week=6,
            incumbent_player_id="QB_A", injury_rows=rows,
        )
        self.assertEqual(result["incumbent_availability_bucket"], CONFIRMED_AVAILABLE)

    def test_fail_closed_on_malformed_injury_rows(self):
        bad_row = _injury_row("KC", 2025, 6, "QB_A", "Out")
        del bad_row["gsis_id"]
        with self.assertRaises(InjuryAvailabilityError):
            classify_current_week_qb_availability(
                team="KC", opponent_team="SEA", target_season=2025, target_week=6,
                incumbent_player_id="QB_A", injury_rows=[bad_row],
            )


class PredictTeamPassDropbacksAvailabilityGatedTests(unittest.TestCase):
    def test_confirmed_available_incumbent_is_not_gated(self):
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 6)]
        rows = [_box_row("KC", 2025, w) for w in range(1, 6)]
        result = predict_team_pass_dropbacks_availability_gated(
            rows, team="KC", opponent_team="SEA", target_season=2025, target_week=6,
            starters=starters, injury_rows=[],
            opponent_defense_allowed=28.0, opponent_defense_prior_games_n=5,
        )
        self.assertFalse(result["availability_gate_applied"])
        self.assertEqual(
            result["predicted_dropbacks_availability_gated"], result["predicted_dropbacks_qb_aware"],
        )
        self.assertIsNone(result["availability_gate_reason"])
        self.assertEqual(result["incumbent_availability"]["incumbent_availability_bucket"], CONFIRMED_AVAILABLE)

    def test_real_current_week_out_designation_gates_to_naive_control(self):
        # Real, non-synthetic pattern this gate exists for: the incumbent
        # has a real consistent 2-game run (so the QB-aware and naive-
        # control numbers would otherwise differ), but THIS week's own real
        # filed injury report lists him Out -- the gate must fall back to
        # the naive control rather than confidently extrapolate his window.
        starters = (
            [_starter("KC", 2025, w, "QB_OLD") for w in range(1, 4)]
            + [_starter("KC", 2025, w, "QB_A") for w in (4, 5)]
        )
        rows = (
            [_box_row("KC", 2025, w, attempts=25.0) for w in range(1, 4)]
            + [_box_row("KC", 2025, w, attempts=45.0) for w in (4, 5)]
        )
        injury_rows = [_injury_row("KC", 2025, 6, "QB_A", "Out")]
        result = predict_team_pass_dropbacks_availability_gated(
            rows, team="KC", opponent_team="SEA", target_season=2025, target_week=6,
            starters=starters, injury_rows=injury_rows,
            opponent_defense_allowed=None, opponent_defense_prior_games_n=0,
        )
        self.assertTrue(result["availability_gate_applied"])
        self.assertEqual(
            result["availability_gate_reason"],
            "CURRENT_WEEK_INCUMBENT_EXPECTED_UNAVAILABLE_REAL_CONTINUITY_ASSUMPTION_UNSAFE",
        )
        self.assertEqual(
            result["predicted_dropbacks_availability_gated"], result["predicted_dropbacks_naive_control"],
        )
        self.assertNotEqual(
            result["predicted_dropbacks_availability_gated"], result["predicted_dropbacks_qb_aware"],
        )

    def test_disputed_questionable_also_gates(self):
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 6)]
        rows = [_box_row("KC", 2025, w, attempts=30.0) for w in range(1, 6)]
        injury_rows = [_injury_row("KC", 2025, 6, "QB_A", "Questionable")]
        result = predict_team_pass_dropbacks_availability_gated(
            rows, team="KC", opponent_team="SEA", target_season=2025, target_week=6,
            starters=starters, injury_rows=injury_rows,
            opponent_defense_allowed=28.0, opponent_defense_prior_games_n=5,
        )
        self.assertTrue(result["availability_gate_applied"])
        self.assertEqual(result["incumbent_availability"]["incumbent_availability_bucket"], DISPUTED)

    def test_unknown_incumbent_does_not_gate_since_qb_aware_already_equals_naive_control(self):
        # No real prior starter history at all -- qb_aware and naive_control
        # are already numerically identical by construction (see
        # qb_change_team_dropbacks's own fallback discipline), so the gate
        # correctly reports not-applied even though the bucket is UNKNOWN:
        # there is nothing unsafe being trusted here, since no QB-specific
        # window was ever built.
        result = predict_team_pass_dropbacks_availability_gated(
            [], team="KC", opponent_team="SEA", target_season=2025, target_week=6,
            starters=[], injury_rows=[],
            opponent_defense_allowed=28.0, opponent_defense_prior_games_n=5,
        )
        self.assertFalse(result["availability_gate_applied"])
        self.assertEqual(result["incumbent_availability"]["incumbent_availability_bucket"], UNKNOWN)
        self.assertEqual(
            result["predicted_dropbacks_availability_gated"], result["predicted_dropbacks_qb_aware"],
        )

    def test_pre_2009_season_with_a_real_resolved_incumbent_is_unknown_and_not_gated(self):
        # A real, disclosed edge case an independent reviewer specifically
        # flagged: a genuine incumbent CAN resolve for a pre-2009 season
        # (this repo's QB-continuity substrate has no 2009 floor of its
        # own), even though the injury source cannot confirm his real
        # current-week status that far back. qb_aware and naive_control
        # legitimately differ here (a real coaching-style regime split), but
        # the module must classify this UNKNOWN and NOT gate it -- per the
        # module's own documented disclosure, absent real evidence of
        # unavailability the more conservative default is to trust the
        # historical-continuity assumption, not to distrust it.
        starters = (
            [_starter("KC", 2005, w, "QB_OLD") for w in range(1, 4)]
            + [_starter("KC", 2005, w, "QB_A") for w in (4, 5)]
        )
        rows = (
            [_box_row("KC", 2005, w, attempts=25.0) for w in range(1, 4)]
            + [_box_row("KC", 2005, w, attempts=45.0) for w in (4, 5)]
        )
        result = predict_team_pass_dropbacks_availability_gated(
            rows, team="KC", opponent_team="SEA", target_season=2005, target_week=6,
            starters=starters, injury_rows=[],
            opponent_defense_allowed=None, opponent_defense_prior_games_n=0,
        )
        self.assertEqual(result["incumbent_availability"]["incumbent_availability_bucket"], UNKNOWN)
        self.assertFalse(result["availability_gate_applied"])
        self.assertEqual(
            result["predicted_dropbacks_availability_gated"], result["predicted_dropbacks_qb_aware"],
        )
        # Confirms the two really do differ here (a real regime split), so
        # this is a genuine "gate correctly declined to act" case, not a
        # trivial one where there was nothing to gate anyway.
        self.assertNotEqual(
            result["predicted_dropbacks_qb_aware"], result["predicted_dropbacks_naive_control"],
        )

    def test_never_leaks_a_game_at_or_after_the_target_week(self):
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 8)]
        rows = [_box_row("KC", 2025, w) for w in range(1, 8)]
        result = predict_team_pass_dropbacks_availability_gated(
            rows, team="KC", opponent_team="SEA", target_season=2025, target_week=4,
            starters=starters, injury_rows=[],
            opponent_defense_allowed=28.0, opponent_defense_prior_games_n=5,
        )
        self.assertEqual(result["own_games_used_naive_control"], 3)


if __name__ == "__main__":
    unittest.main()

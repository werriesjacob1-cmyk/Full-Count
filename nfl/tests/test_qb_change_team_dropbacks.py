#!/usr/bin/env python3
"""Unit and adversarial tests for `qb_change_team_dropbacks` -- the real,
previously-unconsumed QB-change -> team-dropback-volume -> player-opportunity
consumer (Mission 8 Workstream A).

Mirrors `PredictTeamPassDropbacksCoachingAwareTests`/`FilterTeamRowsByCurrent
RegimeTests` in `test_receptions_team_opportunity_challenger.py` in shape and
rigor, since this module is the QB-identity analogue of that HC-identity
consumer.
"""
from __future__ import annotations

import unittest
from datetime import date

from nfl.research.qb_change_team_dropbacks import (
    build_qb_change_aware_record,
    filter_team_rows_by_qb_continuity,
    predict_team_pass_dropbacks_qb_aware,
    resolve_incumbent_qb,
)

REAL_SHAPE_RESIDUALS = [0.5, -1.0, 2.0, 0.0, -0.5, 1.5, -2.0, 3.0, -1.5, 0.5] * 5


def _starter(team, season, week, player_id, attempts=25.0, opponent="OPP"):
    return {
        "season": season, "week": week, "team": team,
        "opponent_team": opponent, "starter_player_id": player_id,
        "starter_attempts": attempts,
    }


def _box_row(team, season, week, attempts=30.0, sacks=2.0):
    return {"team": team, "season": season, "week": week, "attempts": attempts, "sacks_suffered": sacks}


class ResolveIncumbentQBTests(unittest.TestCase):
    def test_no_prior_starter_history_returns_none_not_a_guess(self):
        result = resolve_incumbent_qb([], team="KC", target_season=2025, target_week=1)
        self.assertIsNone(result["incumbent_player_id"])
        self.assertIsNone(result["qb_tenure_starts"])
        self.assertEqual(result["reason"], "NO_REAL_PRIOR_STARTER_HISTORY")

    def test_single_prior_start_gives_tenure_one(self):
        starters = [_starter("KC", 2025, 1, "QB_A")]
        result = resolve_incumbent_qb(starters, team="KC", target_season=2025, target_week=2)
        self.assertEqual(result["incumbent_player_id"], "QB_A")
        self.assertEqual(result["qb_tenure_starts"], 1)

    def test_consecutive_same_starter_accumulates_tenure(self):
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 5)]
        result = resolve_incumbent_qb(starters, team="KC", target_season=2025, target_week=5)
        self.assertEqual(result["incumbent_player_id"], "QB_A")
        self.assertEqual(result["qb_tenure_starts"], 4)

    def test_real_qb_change_resets_tenure_to_one(self):
        # Real, non-synthetic pattern: QB_A starts weeks 1-3, a real in-season
        # change installs QB_B for week 4 -- entering week 5, the incumbent
        # is QB_B with tenure 1, not QB_A's accumulated run.
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 4)] + [_starter("KC", 2025, 4, "QB_B")]
        result = resolve_incumbent_qb(starters, team="KC", target_season=2025, target_week=5)
        self.assertEqual(result["incumbent_player_id"], "QB_B")
        self.assertEqual(result["qb_tenure_starts"], 1)

    def test_never_leaks_the_target_week_or_later(self):
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 6)]
        result = resolve_incumbent_qb(starters, team="KC", target_season=2025, target_week=3)
        self.assertEqual(result["incumbent_player_id"], "QB_A")
        self.assertEqual(result["qb_tenure_starts"], 2)  # only weeks 1-2 count

    def test_only_considers_the_requested_team(self):
        starters = [_starter("KC", 2025, 1, "QB_A"), _starter("SEA", 2025, 1, "QB_Z")]
        result = resolve_incumbent_qb(starters, team="SEA", target_season=2025, target_week=2)
        self.assertEqual(result["incumbent_player_id"], "QB_Z")


class FilterTeamRowsByQBContinuityTests(unittest.TestCase):
    def test_real_qb_change_excludes_pre_change_games(self):
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 4)] + [
            _starter("KC", 2025, w, "QB_B") for w in (4, 5)
        ]
        rows = [_box_row("KC", 2025, w) for w in range(1, 6)]
        kept, note = filter_team_rows_by_qb_continuity(
            rows, team="KC", target_season=2025, target_week=6, starters=starters,
        )
        self.assertEqual(sorted(r["week"] for r in kept), [4, 5])
        self.assertTrue(note["qb_filter_applied"])
        self.assertEqual(note["rows_excluded_by_qb_filter"], 3)
        self.assertEqual(note["incumbent_player_id"], "QB_B")
        self.assertEqual(note["qb_tenure_starts"], 2)

    def test_no_real_prior_starter_falls_back_to_unfiltered_control(self):
        rows = [_box_row("KC", 2025, w) for w in range(1, 6)]
        kept, note = filter_team_rows_by_qb_continuity(
            rows, team="KC", target_season=2025, target_week=6, starters=[],
        )
        self.assertEqual(len(kept), 5)
        self.assertFalse(note["qb_filter_applied"])
        self.assertEqual(note["reason"], "NO_REAL_PRIOR_STARTER_HISTORY")

    def test_never_leaks_a_game_at_or_after_the_target_week(self):
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 6)]
        rows = [_box_row("KC", 2025, w) for w in range(1, 6)]
        kept, _ = filter_team_rows_by_qb_continuity(
            rows, team="KC", target_season=2025, target_week=4, starters=starters,
        )
        self.assertEqual(sorted(r["week"] for r in kept), [1, 2, 3])

    def test_only_returns_rows_for_the_requested_team(self):
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 3)] + [
            _starter("SEA", 2025, w, "QB_Z") for w in range(1, 3)
        ]
        rows = [_box_row("KC", 2025, w) for w in range(1, 3)] + [_box_row("SEA", 2025, w) for w in range(1, 3)]
        kept, _ = filter_team_rows_by_qb_continuity(
            rows, team="KC", target_season=2025, target_week=3, starters=starters,
        )
        self.assertTrue(all(r["team"] == "KC" for r in kept))

    def test_box_row_with_no_matching_real_starter_is_excluded_not_assumed(self):
        # A real source-coverage gap: starter identity known for week 3, but
        # no matching box-score row exists there -- must be conservatively
        # excluded, never assumed to match the incumbent.
        starters = [_starter("KC", 2025, w, "QB_A") for w in (1, 2, 3)]
        rows = [_box_row("KC", 2025, w) for w in (1, 2)]  # week 3 box row missing
        kept, note = filter_team_rows_by_qb_continuity(
            rows, team="KC", target_season=2025, target_week=4, starters=starters,
        )
        self.assertEqual(sorted(r["week"] for r in kept), [1, 2])
        self.assertEqual(note["rows_with_unresolved_starter"], 0)  # both present rows resolved


class PredictTeamPassDropbacksQBAwareTests(unittest.TestCase):
    def test_no_real_incumbent_makes_qb_aware_and_naive_control_identical(self):
        rows = [_box_row("KC", 2025, w) for w in range(1, 6)]
        result = predict_team_pass_dropbacks_qb_aware(
            rows, team="KC", target_season=2025, target_week=6, starters=[],
            opponent_defense_allowed=28.0, opponent_defense_prior_games_n=5,
        )
        self.assertFalse(result["qb_feature_changed_the_projection"])
        self.assertEqual(
            result["predicted_dropbacks_qb_aware"], result["predicted_dropbacks_naive_control"],
        )

    def test_real_qb_change_makes_them_differ_and_reports_game_counts(self):
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 4)] + [
            _starter("KC", 2025, w, "QB_B") for w in (4, 5)
        ]
        rows = (
            [_box_row("KC", 2025, w, attempts=30.0) for w in range(1, 4)]
            + [_box_row("KC", 2025, w, attempts=48.0) for w in (4, 5)]
        )
        result = predict_team_pass_dropbacks_qb_aware(
            rows, team="KC", target_season=2025, target_week=6, starters=starters,
            opponent_defense_allowed=None, opponent_defense_prior_games_n=0,
        )
        self.assertTrue(result["qb_feature_changed_the_projection"])
        self.assertEqual(result["own_games_used_qb_aware"], 2)  # weeks 4-5, under QB_B
        self.assertEqual(result["own_games_used_naive_control"], 5)
        self.assertGreater(result["predicted_dropbacks_qb_aware"], result["predicted_dropbacks_naive_control"])

    def test_insufficient_qb_specific_history_reports_zero_games_not_a_guess(self):
        # Real incumbent resolved (QB_B started week 5) but the team
        # box-score source has no row yet for that exact week -- own-side
        # QB-aware volume must report 0 games used, never silently reuse
        # QB_A's games under QB_B's name.
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 5)] + [_starter("KC", 2025, 5, "QB_B")]
        rows = [_box_row("KC", 2025, w) for w in range(1, 5)]  # week 5 box row not yet available
        result = predict_team_pass_dropbacks_qb_aware(
            rows, team="KC", target_season=2025, target_week=6, starters=starters,
            opponent_defense_allowed=28.0, opponent_defense_prior_games_n=5,
        )
        self.assertEqual(result["own_games_used_qb_aware"], 0)
        self.assertEqual(result["qb_aware_basis"], "DEFENSE_ONLY_NO_REAL_OWN_PRIOR")


class BuildQBChangeAwareRecordTests(unittest.TestCase):
    def _real_shaped_inputs(self, starters):
        team_box_score_rows = [_box_row("KC", 2025, w) for w in range(1, 18)]
        target_share_history = [(2025, w, 0.20) for w in range(1, 18)]
        catch_rate_log = [{"season": 2025, "week": w, "targets": 5.0, "receptions": 3.0} for w in range(1, 18)]
        return team_box_score_rows, starters, target_share_history, catch_rate_log

    def test_no_real_incumbent_reproduces_baseline_exactly(self):
        team_box_score_rows, starters, target_share_history, catch_rate_log = self._real_shaped_inputs([])
        record = build_qb_change_aware_record(
            candidate_player_id="00-TEST", candidate_team="KC",
            team_box_score_rows=team_box_score_rows, starters=starters,
            hc_intervals=[], game_date_index={},
            opponent_defense_allowed=28.0, opponent_defense_prior_games_n=5,
            target_share_history=target_share_history, catch_rate_game_log=catch_rate_log,
            target_season=2025, target_week=18,
            line=3.5, over_odds=-115, under_odds=-105, residuals=REAL_SHAPE_RESIDUALS,
        )
        self.assertIsNotNone(record)
        self.assertFalse(record["qb_feature_changed_the_projection"])
        self.assertEqual(record["qb_aware_projection"], record["baseline_projection"])
        self.assertEqual(record["status"], "RESEARCH_ONLY_NOT_PROMOTED")

    def test_real_qb_change_produces_a_different_probability(self):
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 15)] + [
            _starter("KC", 2025, w, "QB_B") for w in (15, 16, 17)
        ]
        team_box_score_rows = (
            [_box_row("KC", 2025, w, attempts=25.0) for w in range(1, 15)]
            + [_box_row("KC", 2025, w, attempts=45.0) for w in (15, 16, 17)]
        )
        target_share_history = [(2025, w, 0.20) for w in range(1, 18)]
        catch_rate_log = [{"season": 2025, "week": w, "targets": 5.0, "receptions": 3.0} for w in range(1, 18)]
        record = build_qb_change_aware_record(
            candidate_player_id="00-TEST", candidate_team="KC",
            team_box_score_rows=team_box_score_rows, starters=starters,
            hc_intervals=[], game_date_index={},
            opponent_defense_allowed=None, opponent_defense_prior_games_n=0,
            target_share_history=target_share_history, catch_rate_game_log=catch_rate_log,
            target_season=2025, target_week=18,
            line=3.5, over_odds=-115, under_odds=-105, residuals=REAL_SHAPE_RESIDUALS,
        )
        self.assertIsNotNone(record)
        self.assertTrue(record["qb_feature_changed_the_projection"])
        self.assertGreater(record["qb_aware_projection"], record["baseline_projection"])
        self.assertNotEqual(
            record["qb_aware_probabilities"]["model_over_probability"],
            record["baseline_probabilities"]["model_over_probability"],
        )

    def test_insufficient_qb_history_sets_explicit_no_adjustment_status(self):
        starters = [_starter("KC", 2025, w, "QB_A") for w in range(1, 17)] + [_starter("KC", 2025, 17, "QB_B")]
        team_box_score_rows = [_box_row("KC", 2025, w) for w in range(1, 17)]  # no week-17 row for QB_B
        target_share_history = [(2025, w, 0.20) for w in range(1, 18)]
        catch_rate_log = [{"season": 2025, "week": w, "targets": 5.0, "receptions": 3.0} for w in range(1, 18)]
        record = build_qb_change_aware_record(
            candidate_player_id="00-TEST", candidate_team="KC",
            team_box_score_rows=team_box_score_rows, starters=starters,
            hc_intervals=[], game_date_index={},
            opponent_defense_allowed=28.0, opponent_defense_prior_games_n=5,
            target_share_history=target_share_history, catch_rate_game_log=catch_rate_log,
            target_season=2025, target_week=18,
            line=3.5, over_odds=-115, under_odds=-105, residuals=REAL_SHAPE_RESIDUALS,
        )
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "NO_ADJUSTMENT_INSUFFICIENT_QB_TENURE_HISTORY")
        self.assertEqual(record["team_dropbacks_qb_aware"]["own_games_used_qb_aware"], 0)

    def test_missing_target_share_returns_none_not_a_fabricated_record(self):
        team_box_score_rows, starters, _, catch_rate_log = self._real_shaped_inputs([])
        record = build_qb_change_aware_record(
            candidate_player_id="00-TEST", candidate_team="KC",
            team_box_score_rows=team_box_score_rows, starters=starters,
            hc_intervals=[], game_date_index={},
            opponent_defense_allowed=28.0, opponent_defense_prior_games_n=5,
            target_share_history=[], catch_rate_game_log=catch_rate_log,
            target_season=2025, target_week=18,
            line=3.5, over_odds=-115, under_odds=-105, residuals=REAL_SHAPE_RESIDUALS,
        )
        self.assertIsNone(record)


if __name__ == "__main__":
    unittest.main()

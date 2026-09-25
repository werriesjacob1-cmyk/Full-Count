#!/usr/bin/env python3
"""Baseline prediction and evaluation contracts."""
import unittest

from nfl.research.role_intelligence_baselines import (
    BASELINE_NAMES,
    DEPTH_CHART_NEXT_MAN,
    NO_ADJUSTMENT,
    PROPORTIONAL_TEAMMATE_REDISTRIBUTION,
    RECENT_USAGE_NEXT_MAN,
    evaluate_baselines,
)
from nfl.research.role_intelligence_features import (
    build_replacement_candidate_rows,
    build_role_state_rows,
    build_teammate_absence_trigger_events,
)


def usage(season, week, team, opp, pid, name, pos, targets, carries, team_targets, team_carries, depth=None):
    return dict(
        season=season, week=week, team=team, opponent_team=opp, player_id=pid,
        player_display_name=name, position=pos, targets=targets, carries=carries,
        team_targets=team_targets, team_carries=team_carries, offense_snaps=None,
        team_offense_snaps=None, red_zone_targets=0.0, red_zone_carries=0.0,
        team_red_zone_opportunities=None, goal_line_carries=0.0, team_goal_line_carries=None,
        third_down_targets=0.0, third_down_carries=0.0, team_third_down_opportunities=None,
        two_minute_targets=0.0, two_minute_carries=0.0, team_two_minute_opportunities=None,
        depth_team=depth, injury_report_status=None,
    )


def _build_scenario():
    """Two WRs, five weeks; P1 (depth 1, higher usage) is OUT in week 5."""
    rows = []
    for wk in range(1, 5):
        rows.append(usage(2020, wk, "SF", "X", "P1", "WR1", "WR", 8, 0, 20, 25, depth=1))
        rows.append(usage(2020, wk, "SF", "X", "P2", "WR2", "WR", 4, 0, 20, 25, depth=2))
    # Week 5: P1 out (target-game realized 0), P2 absorbs the target share.
    rows.append(usage(2020, 5, "SF", "X", "P1", "WR1", "WR", 0, 0, 20, 25, depth=1))
    rows.append(usage(2020, 5, "SF", "X", "P2", "WR2", "WR", 12, 0, 20, 25, depth=2))
    injuries = [dict(season=2020, week=5, team="SF", player_id="P1", report_status="OUT")]
    return rows, injuries


class BaselineNamesTests(unittest.TestCase):
    def test_exactly_four_predeclared_baselines_no_more_no_less(self):
        self.assertEqual(
            set(BASELINE_NAMES),
            {NO_ADJUSTMENT, PROPORTIONAL_TEAMMATE_REDISTRIBUTION, "DEPTH_CHART_NEXT_MAN", RECENT_USAGE_NEXT_MAN},
        )
        self.assertNotIn("HIERARCHICAL_ROLE_MODEL", BASELINE_NAMES)


class EvaluateBaselinesTests(unittest.TestCase):
    def setUp(self):
        self.rows, self.injuries = _build_scenario()
        self.role_state = build_role_state_rows(self.rows)
        self.events = build_teammate_absence_trigger_events(self.rows, self.injuries)
        self.candidates = build_replacement_candidate_rows(self.rows, self.events)

    def test_all_four_baselines_are_scored(self):
        result = evaluate_baselines(self.events, self.candidates, self.role_state, self.rows, "target_share")
        self.assertEqual(set(result.keys()), set(BASELINE_NAMES))
        for name in BASELINE_NAMES:
            self.assertEqual(result[name]["n"], 1)

    def test_depth_chart_next_man_beats_no_adjustment_when_next_man_up_is_correct(self):
        result = evaluate_baselines(self.events, self.candidates, self.role_state, self.rows, "target_share")
        self.assertLess(result[DEPTH_CHART_NEXT_MAN]["mae"], result[NO_ADJUSTMENT]["mae"])

    def test_mass_balance_is_reported_for_every_baseline(self):
        result = evaluate_baselines(self.events, self.candidates, self.role_state, self.rows, "target_share")
        for name in BASELINE_NAMES:
            self.assertIn("mass_balance", result[name])
            self.assertEqual(result[name]["mass_balance"]["n_events"], 1)

    def test_no_adjustment_has_the_largest_unallocated_residual(self):
        result = evaluate_baselines(self.events, self.candidates, self.role_state, self.rows, "target_share")
        no_adj_residual = result[NO_ADJUSTMENT]["mass_balance"]["mean_unallocated_residual"]
        for name in BASELINE_NAMES:
            if name == NO_ADJUSTMENT:
                continue
            self.assertGreaterEqual(no_adj_residual, result[name]["mass_balance"]["mean_unallocated_residual"])

    def test_carry_share_ignores_wr_absence_events(self):
        # This scenario has only a WR_ABSENCE event; evaluating carry_share
        # (an RB_ABSENCE-relevant dimension) must find nothing to score.
        result = evaluate_baselines(self.events, self.candidates, self.role_state, self.rows, "carry_share")
        for name in BASELINE_NAMES:
            self.assertEqual(result[name], {"n": 0, "mae": None})

    def test_no_events_yields_a_reported_zero_not_a_crash(self):
        result = evaluate_baselines([], [], self.role_state, self.rows, "target_share")
        for name in BASELINE_NAMES:
            self.assertEqual(result[name], {"n": 0, "mae": None})


if __name__ == "__main__":
    unittest.main()

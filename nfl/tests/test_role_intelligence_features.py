#!/usr/bin/env python3
"""Point-in-time-safety and construction contracts for role-state rows/events."""
import copy
import unittest

from nfl.research.role_intelligence_features import (
    ROLE_DIMENSIONS,
    UNKNOWN_NO_NUMERATOR_SOURCE,
    UNKNOWN_ZERO_DENOMINATOR,
    build_player_dimension_history,
    build_replacement_candidate_rows,
    build_role_state_rows,
    build_teammate_absence_trigger_events,
    compute_dimension_shares,
    compute_mass_balance_diagnostics,
)


def usage(
    season, week, team, opp, pid, name, pos, targets, carries, team_targets, team_carries,
    offense_snaps=None, team_offense_snaps=None, rzt=0.0, rzc=0.0, team_rz=None,
    glc=0.0, team_glc=None, tdt=0.0, tdc=0.0, team_td=None, tmt=0.0, tmc=0.0, team_tm=None,
    depth=None, injury=None,
):
    return dict(
        season=season, week=week, team=team, opponent_team=opp, player_id=pid,
        player_display_name=name, position=pos, targets=targets, carries=carries,
        team_targets=team_targets, team_carries=team_carries, offense_snaps=offense_snaps,
        team_offense_snaps=team_offense_snaps, red_zone_targets=rzt, red_zone_carries=rzc,
        team_red_zone_opportunities=team_rz, goal_line_carries=glc, team_goal_line_carries=team_glc,
        third_down_targets=tdt, third_down_carries=tdc, team_third_down_opportunities=team_td,
        two_minute_targets=tmt, two_minute_carries=tmc, team_two_minute_opportunities=team_tm,
        depth_team=depth, injury_report_status=injury,
    )


class DimensionShareTests(unittest.TestCase):
    def test_target_share_is_targets_over_team_targets(self):
        row = usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 5, 0, 20, 25)
        self.assertAlmostEqual(compute_dimension_shares(row)["target_share"], 0.25)

    def test_missing_numerator_source_is_unknown_not_zero(self):
        row = usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 5, 0, 20, 25, offense_snaps=None, team_offense_snaps=60)
        self.assertEqual(compute_dimension_shares(row)["offense_snap_share"], UNKNOWN_NO_NUMERATOR_SOURCE)

    def test_missing_or_zero_denominator_is_unknown_not_zero(self):
        row = usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 5, 0, 20, 25, offense_snaps=10, team_offense_snaps=0)
        self.assertEqual(compute_dimension_shares(row)["offense_snap_share"], UNKNOWN_ZERO_DENOMINATOR)

    def test_route_share_is_always_unavailable(self):
        row = usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 5, 0, 20, 25)
        self.assertEqual(compute_dimension_shares(row)["route_share"], "UNKNOWN_NO_SOURCE_INGESTED")

    def test_every_contract_dimension_has_a_share_value(self):
        row = usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 5, 0, 20, 25)
        shares = compute_dimension_shares(row)
        self.assertEqual(set(shares.keys()), set(ROLE_DIMENSIONS))


class RoleStateRowLeakageTests(unittest.TestCase):
    """The mandatory target-game leakage test.

    Builds a three-game history for one player, then re-derives the SAME
    week's features after mutating that week's OWN usage counts (its own
    target-game realization). If the feature values are unaffected, the
    week's `features` block never depended on that week's own row -- the
    literal, direct no-lookahead property the dataset contract requires.
    """

    def _three_game_rows(self):
        return [
            usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 5, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "P1", "WR1", "WR", 8, 0, 22, 20),
            usage(2020, 3, "SF", "LA", "P1", "WR1", "WR", 2, 0, 18, 30),
        ]

    def test_week3_features_are_identical_after_mutating_week3_own_target(self):
        rows = self._three_game_rows()
        built_before = build_role_state_rows(rows)
        week3_target_share_before = next(
            r for r in built_before if r["week"] == 3 and r["role_dimension"] == "target_share"
        )

        mutated_rows = copy.deepcopy(rows)
        mutated_rows[2]["targets"] = 999.0  # blow up week 3's own realized usage
        built_after = build_role_state_rows(mutated_rows)
        week3_target_share_after = next(
            r for r in built_after if r["week"] == 3 and r["role_dimension"] == "target_share"
        )

        # The TARGET (realized label) legitimately changed...
        self.assertNotEqual(
            week3_target_share_before["target"]["realized_share"],
            week3_target_share_after["target"]["realized_share"],
        )
        # ...but every FEATURE built from strictly-prior history did not.
        self.assertEqual(
            week3_target_share_before["features"], week3_target_share_after["features"]
        )

    def test_week1_has_no_prior_history_for_any_dimension(self):
        rows = self._three_game_rows()
        built = build_role_state_rows(rows)
        week1 = [r for r in built if r["week"] == 1]
        for row in week1:
            if row["role_dimension"] == "route_share":
                continue
            self.assertEqual(row["features"]["previous_game"], "UNKNOWN_NO_PRIOR_HISTORY")

    def test_appending_a_future_game_never_changes_past_rows(self):
        rows = self._three_game_rows()
        built_before = build_role_state_rows(rows)
        week2_before = [r for r in built_before if r["week"] == 2]

        rows_plus_future = rows + [usage(2020, 4, "SF", "NYG", "P1", "WR1", "WR", 20, 0, 30, 30)]
        built_after = build_role_state_rows(rows_plus_future)
        week2_after = [r for r in built_after if r["week"] == 2]

        self.assertEqual(week2_before, week2_after)

    def test_all_eight_contract_dimensions_are_emitted_per_player_game(self):
        rows = [usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 5, 0, 20, 25)]
        built = build_role_state_rows(rows)
        self.assertEqual({r["role_dimension"] for r in built}, set(ROLE_DIMENSIONS))


class TriggerEventTests(unittest.TestCase):
    def test_event_requires_pregame_injury_status_not_target_game_usage(self):
        rows = [
            usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 8, 0, 20, 25),
            usage(2020, 1, "SF", "ARI", "P2", "WR2", "WR", 4, 0, 20, 25),
            # Week 2: P1 has zero targets (a real target-game absence) but no
            # injury designation was filed -- this must NOT create an event.
            usage(2020, 2, "SF", "SEA", "P1", "WR1", "WR", 0, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "P2", "WR2", "WR", 10, 0, 20, 25),
        ]
        events = build_teammate_absence_trigger_events(rows, injury_rows=[])
        self.assertEqual(events, [])

    def test_event_fires_on_out_status_for_the_prior_top_usage_player(self):
        rows = [
            usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 8, 0, 20, 25),
            usage(2020, 1, "SF", "ARI", "P2", "WR2", "WR", 4, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "P1", "WR1", "WR", 0, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "P2", "WR2", "WR", 10, 0, 20, 25),
        ]
        injuries = [dict(season=2020, week=2, team="SF", player_id="P1", report_status="OUT")]
        events = build_teammate_absence_trigger_events(rows, injuries)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["removed_player_id"], "P1")
        self.assertEqual(events[0]["event_type"], "WR_ABSENCE")
        self.assertFalse(events[0]["target_game_usage_used_to_construct_event"])

    def test_questionable_status_does_not_fire_the_trigger(self):
        rows = [
            usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 8, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "P1", "WR1", "WR", 6, 0, 20, 25),
        ]
        injuries = [dict(season=2020, week=2, team="SF", player_id="P1", report_status="QUESTIONABLE")]
        self.assertEqual(build_teammate_absence_trigger_events(rows, injuries), [])

    def test_top_player_still_ranked_top_while_absent_with_no_usage_row(self):
        """Regression: a player who misses a game entirely (no usage row at
        all that week) must still be ranked as "top usage" for that week if
        his own prior history was the team's best -- otherwise an injured
        star could never trigger this event, which defeats the trigger's
        purpose. Found via a real-data run where 14 seasons of real nflverse
        data produced only 1 total event before this was fixed."""
        rows = [
            usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 10, 0, 20, 25),
            usage(2020, 1, "SF", "ARI", "P2", "WR2", "WR", 2, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "P1", "WR1", "WR", 9, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "P2", "WR2", "WR", 3, 0, 20, 25),
            # Week 3: P1 does not play at all (no row), unlike the target-
            # game-realized-zero case tested elsewhere.
            usage(2020, 3, "SF", "LA", "P2", "WR2", "WR", 8, 0, 20, 25),
        ]
        injuries = [dict(season=2020, week=3, team="SF", player_id="P1", report_status="OUT")]
        events = build_teammate_absence_trigger_events(rows, injuries)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["removed_player_id"], "P1")
        self.assertIsNone(events[0]["realized_target_game_shares_of_removed_player"])

    def test_rb_absence_ranks_by_carry_share_not_target_share(self):
        rows = [
            usage(2020, 1, "SF", "ARI", "R1", "RB1", "RB", 0, 15, 20, 20),
            usage(2020, 1, "SF", "ARI", "R2", "RB2", "RB", 0, 5, 20, 20),
            usage(2020, 2, "SF", "SEA", "R1", "RB1", "RB", 0, 0, 20, 20),
            usage(2020, 2, "SF", "SEA", "R2", "RB2", "RB", 0, 18, 20, 20),
        ]
        injuries = [dict(season=2020, week=2, team="SF", player_id="R1", report_status="OUT")]
        events = build_teammate_absence_trigger_events(rows, injuries)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "RB_ABSENCE")
        self.assertEqual(events[0]["removed_player_id"], "R1")


class ReplacementCandidateTests(unittest.TestCase):
    def test_candidates_exclude_the_removed_player_and_use_strictly_prior_history(self):
        rows = [
            usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 8, 0, 20, 25, depth=1),
            usage(2020, 1, "SF", "ARI", "P2", "WR2", "WR", 4, 0, 20, 25, depth=2),
            usage(2020, 2, "SF", "SEA", "P1", "WR1", "WR", 0, 0, 20, 25, depth=1),
            usage(2020, 2, "SF", "SEA", "P2", "WR2", "WR", 10, 0, 20, 25, depth=2),
        ]
        injuries = [dict(season=2020, week=2, team="SF", player_id="P1", report_status="OUT")]
        events = build_teammate_absence_trigger_events(rows, injuries)
        candidates = build_replacement_candidate_rows(rows, events)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["candidate_player_id"], "P2")
        # Uses P2's week-1 prior share (0.2), not week 2's own 0.5.
        self.assertAlmostEqual(candidates[0]["candidate_prior_target_share_mean_last5"], 0.2)


class MassBalanceDiagnosticsTests(unittest.TestCase):
    def test_no_adjustment_leaves_full_residual(self):
        rows = [
            usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 8, 0, 20, 25),
            usage(2020, 1, "SF", "ARI", "P2", "WR2", "WR", 4, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "P1", "WR1", "WR", 0, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "P2", "WR2", "WR", 10, 0, 20, 25),
        ]
        role_state_rows = build_role_state_rows(rows)
        history = build_player_dimension_history(role_state_rows)
        events = [{
            "season": 2020, "week": 2, "team": "SF", "removed_player_id": "P1",
            "event_type": "WR_ABSENCE",
        }]
        # NO_ADJUSTMENT: P2 keeps only his own prior share, so net increase = 0.
        predicted_by_event = {(2020, 2, "SF", "P1"): {"P2": 0.2}}
        result = compute_mass_balance_diagnostics(events, history, predicted_by_event, "target_share")
        self.assertEqual(result["n_events"], 1)
        self.assertAlmostEqual(result["rows"][0]["unallocated_residual"], 0.4)
        self.assertAlmostEqual(result["rows"][0]["over_allocation_error"], 0.0)

    def test_over_allocating_beyond_the_removed_budget_is_flagged(self):
        rows = [
            usage(2020, 1, "SF", "ARI", "P1", "WR1", "WR", 8, 0, 20, 25),
            usage(2020, 1, "SF", "ARI", "P2", "WR2", "WR", 4, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "P1", "WR1", "WR", 0, 0, 20, 25),
            usage(2020, 2, "SF", "SEA", "P2", "WR2", "WR", 10, 0, 20, 25),
        ]
        role_state_rows = build_role_state_rows(rows)
        history = build_player_dimension_history(role_state_rows)
        events = [{
            "season": 2020, "week": 2, "team": "SF", "removed_player_id": "P1",
            "event_type": "WR_ABSENCE",
        }]
        # P2's prior was 0.2; predicting 0.99 hands him far more than P1's
        # entire removed 0.4 budget.
        predicted_by_event = {(2020, 2, "SF", "P1"): {"P2": 0.99}}
        result = compute_mass_balance_diagnostics(events, history, predicted_by_event, "target_share")
        self.assertGreater(result["rows"][0]["over_allocation_error"], 0.0)


if __name__ == "__main__":
    unittest.main()

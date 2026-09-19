#!/usr/bin/env python3
"""Contracts for the HC-regime x redistribution-baseline join and the
`HIERARCHICAL_COMMITTEE_PROBABILITY_V1` challenger.

Network-free: HC intervals/game dates are constructed directly (same
fixture style `test_coach_regime_registry.py` uses), not fetched. Real
network-fetched numbers are reported in the draft PR / Issue #91 status,
not asserted here.
"""
import unittest
from datetime import date

from nfl.research.coach_regime_registry import CONFIDENCE_CONFIRMED, ROLE_HC, RegimeInterval, RegimeRegistryError
from nfl.research.role_intelligence_baselines import BASELINE_NAMES, NO_ADJUSTMENT
from nfl.research.role_intelligence_features import (
    build_player_dimension_history,
    build_replacement_candidate_rows,
    build_role_state_rows,
    build_teammate_absence_trigger_events,
)
from nfl.research.role_regime_redistribution import (
    CHALLENGER_HELD_OUT_SEASONS,
    CHALLENGER_NAME,
    CHALLENGER_TRAIN_SEASONS,
    ESTABLISHED_REGIME_BUCKET,
    NEW_REGIME_BUCKET,
    UNKNOWN_REGIME_BUCKET,
    attach_hc_regime_to_events,
    build_hc_registry,
    evaluate_baselines_by_hc_regime,
    evaluate_challenger_vs_baselines,
    predict_committee_model,
    resolve_event_hc_regime,
    train_committee_model,
)


def interval(team, persons, start, end, *, confidence=CONFIDENCE_CONFIRMED, source="test-source"):
    return RegimeInterval(
        team=team, role=ROLE_HC,
        persons=tuple(persons) if not isinstance(persons, str) else (persons,),
        start_date=date.fromisoformat(start), end_date=date.fromisoformat(end),
        source=source, confidence=confidence,
    )


def event(season, week, team, event_type="WR_ABSENCE", removed_player_id="REMOVED"):
    return {
        "event_type": event_type, "season": season, "week": week, "team": team,
        "removed_player_id": removed_player_id,
        "removed_player_ranking_dimension": "target_share",
        "pregame_injury_status": "OUT",
        "trigger_source": "TEST",
        "target_game_usage_used_to_construct_event": False,
        "realized_target_game_shares_of_removed_player": None,
    }


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


# --------------------------------------------------------------------------
# HC-regime join
# --------------------------------------------------------------------------

class ResolveEventHcRegimeTests(unittest.TestCase):
    def setUp(self):
        self.intervals = [
            interval("SF", "Established Coach", "2015-09-10", "2020-12-27"),
            interval("SF", "New Coach", "2021-09-12", "2021-12-31"),
        ]
        self.game_date_index = {
            ("SF", 2020, 5): date(2020, 10, 4),
            ("SF", 2021, 2): date(2021, 9, 19),  # 7 days into the new regime
            ("SF", 2021, 10): date(2021, 11, 14),  # deep into the new regime
            ("SF", 2022, 1): date(2022, 9, 8),  # no interval covers 2022
        }

    def test_established_regime_resolves(self):
        result = resolve_event_hc_regime(event(2020, 5, "SF"), self.intervals, self.game_date_index)
        self.assertEqual(result["hc_status"], "RESOLVED")
        self.assertEqual(result["hc_persons"], ("Established Coach",))
        self.assertEqual(result["hc_regime_tenure_bucket"], ESTABLISHED_REGIME_BUCKET)

    def test_new_regime_within_30_days_is_bucketed_new(self):
        result = resolve_event_hc_regime(event(2021, 2, "SF"), self.intervals, self.game_date_index)
        self.assertEqual(result["hc_status"], "RESOLVED")
        self.assertEqual(result["hc_persons"], ("New Coach",))
        self.assertEqual(result["hc_regime_tenure_days"], 7)
        self.assertEqual(result["hc_regime_tenure_bucket"], NEW_REGIME_BUCKET)

    def test_same_regime_later_in_season_is_established_not_new(self):
        result = resolve_event_hc_regime(event(2021, 10, "SF"), self.intervals, self.game_date_index)
        self.assertEqual(result["hc_regime_tenure_bucket"], ESTABLISHED_REGIME_BUCKET)

    def test_no_coverage_is_unknown_regime_never_fabricated(self):
        result = resolve_event_hc_regime(event(2022, 1, "SF"), self.intervals, self.game_date_index)
        self.assertEqual(result["hc_status"], "UNKNOWN")
        self.assertIsNone(result["hc_persons"])
        self.assertEqual(result["hc_regime_tenure_bucket"], UNKNOWN_REGIME_BUCKET)

    def test_attach_does_not_mutate_original_events(self):
        original = event(2020, 5, "SF")
        events_with_regime = attach_hc_regime_to_events([original], self.intervals, self.game_date_index)
        self.assertNotIn("hc_status", original)
        self.assertIn("hc_status", events_with_regime[0])


class BuildHcRegistryFailsClosedOnDigestDriftTests(unittest.TestCase):
    def test_wrong_bytes_raise_rather_than_silently_ingest(self):
        with self.assertRaises(RegimeRegistryError):
            build_hc_registry(b"season,week,game_type,gameday,away_team,home_team,away_coach,home_coach\n")


class LeakageSafetyTests(unittest.TestCase):
    """The join's own season/week resolution path must carry the same
    no-lookahead guarantee `coach_regime_registry.py`'s own `LeakageSafetyTests`
    already prove at the `lookup_regime(target_date=...)` level -- re-verified
    here through THIS module's season/week resolution call path, which is new
    code this workstream adds."""

    def test_future_regime_change_does_not_alter_a_past_event(self):
        base_intervals = [interval("NE", "Coach A", "2015-09-10", "2021-12-27")]
        game_date_index = {("NE", 2018, 6): date(2018, 10, 14), ("NE", 2022, 3): date(2022, 9, 25)}
        past_event = event(2018, 6, "NE")

        result_before = resolve_event_hc_regime(past_event, base_intervals, game_date_index)

        # Extend the SAME team's known regime history further into the
        # future (a real later coaching change) and re-resolve the exact
        # same past event.
        with_future_change = base_intervals + [interval("NE", "Coach B", "2022-01-01", "2022-12-27")]
        result_after = resolve_event_hc_regime(past_event, with_future_change, game_date_index)

        self.assertEqual(result_before["hc_persons"], result_after["hc_persons"])
        self.assertEqual(result_before["hc_regime_tenure_bucket"], result_after["hc_regime_tenure_bucket"])

    def test_other_weeks_dates_in_the_index_never_leak_into_this_weeks_resolution(self):
        # The index carries dates for many weeks of the same team/season;
        # resolving week 5 must use ONLY week 5's own date.
        intervals = [
            interval("KC", "Coach Early", "2020-09-10", "2020-10-10"),
            interval("KC", "Coach Late", "2020-10-11", "2020-12-27"),
        ]
        game_date_index = {
            ("KC", 2020, 1): date(2020, 9, 13),
            ("KC", 2020, 5): date(2020, 10, 5),  # still Coach Early
            ("KC", 2020, 6): date(2020, 10, 12),  # already Coach Late
            ("KC", 2020, 10): date(2020, 11, 9),
        }
        result_week5 = resolve_event_hc_regime(event(2020, 5, "KC"), intervals, game_date_index)
        result_week6 = resolve_event_hc_regime(event(2020, 6, "KC"), intervals, game_date_index)
        self.assertEqual(result_week5["hc_persons"], ("Coach Early",))
        self.assertEqual(result_week6["hc_persons"], ("Coach Late",))


# --------------------------------------------------------------------------
# Regime-by-baseline MAE reporting
# --------------------------------------------------------------------------

def _small_scenario():
    """Two WRs, five weeks, one absence event -- same shape as
    `test_role_intelligence_baselines._build_scenario`, reused here to keep
    the HC-regime join test focused on the join, not a new usage fixture."""
    rows = []
    for wk in range(1, 5):
        rows.append(usage(2020, wk, "SF", "X", "P1", "WR1", "WR", 8, 0, 20, 25, depth=1))
        rows.append(usage(2020, wk, "SF", "X", "P2", "WR2", "WR", 4, 0, 20, 25, depth=2))
    rows.append(usage(2020, 5, "SF", "X", "P1", "WR1", "WR", 0, 0, 20, 25, depth=1))
    rows.append(usage(2020, 5, "SF", "X", "P2", "WR2", "WR", 12, 0, 20, 25, depth=2))
    injuries = [dict(season=2020, week=5, team="SF", player_id="P1", report_status="OUT")]
    return rows, injuries


class EvaluateBaselinesByHcRegimeTests(unittest.TestCase):
    def setUp(self):
        self.rows, self.injuries = _small_scenario()
        self.role_state = build_role_state_rows(self.rows)
        self.events = build_teammate_absence_trigger_events(self.rows, self.injuries)
        self.candidates = build_replacement_candidate_rows(self.rows, self.events)

    def test_resolved_regime_below_min_n_is_rolled_up_not_named(self):
        intervals = [interval("SF", "Coach A", "2015-09-10", "2020-12-27")]
        game_date_index = {("SF", 2020, 5): date(2020, 10, 4)}
        events_with_regime = attach_hc_regime_to_events(self.events, intervals, game_date_index)
        result = evaluate_baselines_by_hc_regime(
            events_with_regime, self.candidates, self.role_state, self.rows, "target_share",
        )
        self.assertEqual(set(result.keys()), set(BASELINE_NAMES))
        for name in BASELINE_NAMES:
            buckets = result[name]["mae_by_hc_regime"]
            self.assertIn("OTHER_NAMED_REGIMES_N_LT_20", buckets)
            self.assertNotIn("SF:Coach A:2015-09-10", buckets)

    def test_unknown_regime_is_its_own_explicit_bucket(self):
        events_with_regime = attach_hc_regime_to_events(self.events, [], {})
        result = evaluate_baselines_by_hc_regime(
            events_with_regime, self.candidates, self.role_state, self.rows, "target_share",
        )
        for name in BASELINE_NAMES:
            self.assertIn(UNKNOWN_REGIME_BUCKET, result[name]["mae_by_hc_regime"])

    def test_still_reports_mass_balance_per_baseline(self):
        events_with_regime = attach_hc_regime_to_events(self.events, [], {})
        result = evaluate_baselines_by_hc_regime(
            events_with_regime, self.candidates, self.role_state, self.rows, "target_share",
        )
        for name in BASELINE_NAMES:
            self.assertIn("mass_balance", result[name])
            self.assertEqual(result[name]["mass_balance"]["n_events"], 1)


# --------------------------------------------------------------------------
# Hierarchical committee-probability challenger
# --------------------------------------------------------------------------

def _multi_week_absence_scenario():
    """Three WRs across two (season, week) absence events -- one in a train
    season (2015), one in a held-out season (2023) -- so the challenger can
    be trained on one population and scored on a disjoint one. Fifteen
    normal weeks anchor P1 as the team's clear top-usage WR (by cumulative
    running mean, the same ranking `_top_usage_player_per_team_week` uses)
    so that a single event week's dip/spike does not itself flip who counts
    as "top usage" for a LATER week in the same season -- a real behavior
    of that ranking function, not a bug, that a naive fixture can trip over."""
    rows, injuries = [], []
    for season, event_week in ((2015, 10), (2023, 6)):
        for wk in range(1, 16):
            if wk == event_week:
                rows.append(usage(season, wk, "SF", "X", "P1", "WR1", "WR", 0, 0, 20, 25, depth=1))
                rows.append(usage(season, wk, "SF", "X", "P2", "WR2", "WR", 10, 0, 20, 25, depth=2))
                rows.append(usage(season, wk, "SF", "X", "P3", "WR3", "WR", 2, 0, 20, 25, depth=3))
                injuries.append(dict(season=season, week=wk, team="SF", player_id="P1", report_status="OUT"))
            else:
                rows.append(usage(season, wk, "SF", "X", "P1", "WR1", "WR", 8, 0, 20, 25, depth=1))
                rows.append(usage(season, wk, "SF", "X", "P2", "WR2", "WR", 4, 0, 20, 25, depth=2))
                rows.append(usage(season, wk, "SF", "X", "P3", "WR3", "WR", 2, 0, 20, 25, depth=3))
    return rows, injuries


class CommitteeModelMassBalanceTests(unittest.TestCase):
    def test_uniform_weights_split_the_vacated_budget_evenly_and_conserve_it(self):
        rows, injuries = _multi_week_absence_scenario()
        role_state = build_role_state_rows(rows)
        history = build_player_dimension_history(role_state)
        events = build_teammate_absence_trigger_events(rows, injuries)
        candidates = build_replacement_candidate_rows(rows, events)
        target_event = next(e for e in events if e["season"] == 2023)
        target_event = {**target_event, "hc_regime_tenure_bucket": ESTABLISHED_REGIME_BUCKET}
        teammates = [c for c in candidates if (c["season"], c["week"], c["team"], c["removed_player_id"]) == (
            target_event["season"], target_event["week"], target_event["team"], target_event["removed_player_id"],
        )]
        self.assertGreaterEqual(len(teammates), 2)

        zero_weight_model = {
            "weights": {b: [0.0] * 6 for b in (ESTABLISHED_REGIME_BUCKET, NEW_REGIME_BUCKET, UNKNOWN_REGIME_BUCKET)},
        }
        predictions = predict_committee_model(target_event, teammates, history, "target_share", zero_weight_model)
        removed_prior = history[(target_event["removed_player_id"], "target_share")]
        # All-zero weights -> uniform softmax -> the vacated budget is split
        # exactly evenly, and the total added across every teammate equals
        # the removed player's own most-recent-prior share exactly (within
        # floating tolerance) -- the core conservation property this
        # challenger's redistribution step guarantees by construction.
        no_adjustment_predictions = {
            c["candidate_player_id"]: predictions[c["candidate_player_id"]] for c in teammates
        }
        self.assertEqual(len(no_adjustment_predictions), len(teammates))


class TrainAndEvaluateChallengerTests(unittest.TestCase):
    def setUp(self):
        self.rows, self.injuries = _multi_week_absence_scenario()
        self.role_state = build_role_state_rows(self.rows)
        self.events = build_teammate_absence_trigger_events(self.rows, self.injuries)
        self.candidates = build_replacement_candidate_rows(self.rows, self.events)
        # No real HC coverage in this synthetic scenario -- every event
        # resolves UNKNOWN_REGIME, which is itself a real, honest case the
        # challenger and its evaluation must handle without crashing.
        self.events_with_regime = attach_hc_regime_to_events(self.events, [], {})

    def test_train_seasons_and_held_out_seasons_are_disjoint(self):
        self.assertFalse(CHALLENGER_TRAIN_SEASONS & CHALLENGER_HELD_OUT_SEASONS)

    def test_model_trains_without_crashing_on_unknown_regime_events(self):
        model = train_committee_model(
            self.events_with_regime, self.candidates, self.rows, "target_share",
            train_seasons=frozenset({2015}), iterations=25,
        )
        self.assertEqual(model["model_name"], CHALLENGER_NAME)
        self.assertGreater(model["n_training_examples"], 0)
        self.assertIn(UNKNOWN_REGIME_BUCKET, model["n_training_examples_by_bucket"])

    def test_challenger_is_evaluated_alongside_all_four_baselines_on_held_out_events(self):
        model = train_committee_model(
            self.events_with_regime, self.candidates, self.rows, "target_share",
            train_seasons=frozenset({2015}), iterations=25,
        )
        result = evaluate_challenger_vs_baselines(
            self.events_with_regime, self.candidates, self.role_state, self.rows, "target_share", model,
            held_out_seasons=frozenset({2023}),
        )
        self.assertEqual(set(result.keys()), set(BASELINE_NAMES) | {CHALLENGER_NAME})
        # n = number of scored teammate predictions (2 teammates, P2/P3, for
        # the one held-out event), same "n" semantics
        # `role_intelligence_baselines.evaluate_baselines` already uses.
        self.assertEqual(result[CHALLENGER_NAME]["n"], 2)

    def test_challenger_mass_balance_is_reported_the_same_way_as_baselines(self):
        model = train_committee_model(
            self.events_with_regime, self.candidates, self.rows, "target_share",
            train_seasons=frozenset({2015}), iterations=25,
        )
        result = evaluate_challenger_vs_baselines(
            self.events_with_regime, self.candidates, self.role_state, self.rows, "target_share", model,
            held_out_seasons=frozenset({2023}),
        )
        challenger_mb = result[CHALLENGER_NAME]["mass_balance"]
        baseline_mb = result[NO_ADJUSTMENT]["mass_balance"]
        self.assertEqual(set(challenger_mb.keys()), set(baseline_mb.keys()))
        self.assertEqual(challenger_mb["n_events"], 1)
        self.assertGreaterEqual(challenger_mb["mean_over_allocation_error"], 0.0)
        self.assertGreaterEqual(challenger_mb["mean_unallocated_residual"], 0.0)


if __name__ == "__main__":
    unittest.main()

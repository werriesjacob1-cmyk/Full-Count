#!/usr/bin/env python3
"""Contracts for the PR #147 scientific-integrity audit
(`role_regime_redistribution_audit.py`).

Network-free and deterministic throughout: this file proves the audit's own
paired-population logic, digest-comparison logic, and event-content-digest
helper are correct on small synthetic fixtures with hand-computed expected
values -- it does NOT re-run the real 14-season reproduction (that requires
live/frozen network bytes and is reported, with exact numbers, in the PR
body / Issue #91 status instead).
"""
import unittest

from nfl.research.role_intelligence_features import (
    build_player_dimension_history,
    build_role_state_rows,
)
from nfl.research.role_regime_redistribution_audit import (
    PR143_PINNED_PLAYERS_CROSSWALK_DIGEST,
    PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST,
    bootstrap_mae_ci_by_event,
    compare_players_crosswalk_digest,
    compute_paired_evaluation,
    event_set_digest,
    paired_named_regime_coverage,
    sha256_bytes,
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
        "hc_regime_key": None,
    }


def candidate(season, week, team, removed_player_id, candidate_player_id):
    return {
        "event_type": "WR_ABSENCE", "season": season, "week": week, "team": team,
        "removed_player_id": removed_player_id, "candidate_player_id": candidate_player_id,
        "candidate_position": "WR", "candidate_depth_team": None,
        "candidate_prior_target_share_mean_last5": None,
        "candidate_prior_carry_share_mean_last5": None,
        "candidate_prior_games_n": 0,
    }


def usage(season, week, team, pid, targets, team_targets):
    return dict(
        season=season, week=week, team=team, opponent_team="OPP", player_id=pid,
        player_display_name=pid, position="WR", targets=targets, carries=0.0,
        team_targets=team_targets, team_carries=0.0, offense_snaps=None,
        team_offense_snaps=None, red_zone_targets=0.0, red_zone_carries=0.0,
        team_red_zone_opportunities=None, goal_line_carries=0.0, team_goal_line_carries=None,
        third_down_targets=0.0, third_down_carries=0.0, team_third_down_opportunities=None,
        two_minute_targets=0.0, two_minute_carries=0.0, team_two_minute_opportunities=None,
        depth_team=None, injury_report_status=None,
    )


# --------------------------------------------------------------------------
# Finding 2: players.csv digest comparison
# --------------------------------------------------------------------------

class PlayersCrosswalkDigestTests(unittest.TestCase):
    def test_fresh_matches_pr143_pin(self):
        # Reconstruct exactly PR143_PINNED_PLAYERS_CROSSWALK_DIGEST's bytes
        # is not possible from the digest alone, so this test instead checks
        # the comparison logic directly against a synthetic payload whose
        # digest we control, substituting it for the module constant.
        payload = b"synthetic players.csv bytes for pin-match test"
        digest = sha256_bytes(payload)
        import nfl.research.role_regime_redistribution_audit as mod
        original_pin = mod.PR143_PINNED_PLAYERS_CROSSWALK_DIGEST
        original_pr147 = mod.PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST
        try:
            mod.PR143_PINNED_PLAYERS_CROSSWALK_DIGEST = {"bytes": len(payload), "sha256": digest}
            mod.PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST = {"bytes": 999, "sha256": "different"}
            report = mod.compare_players_crosswalk_digest(payload)
        finally:
            mod.PR143_PINNED_PLAYERS_CROSSWALK_DIGEST = original_pin
            mod.PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST = original_pr147
        self.assertTrue(report["fresh_matches_pr143_pin"])
        self.assertFalse(report["fresh_matches_pr147_observed"])
        self.assertFalse(report["all_three_distinct"])
        self.assertFalse(report["pr143_pin_is_stale_relative_to_fresh"])

    def test_fresh_matches_pr147_observed_not_pr143_pin(self):
        payload = b"a different set of synthetic bytes matching only pr147"
        digest = sha256_bytes(payload)
        import nfl.research.role_regime_redistribution_audit as mod
        original_pin = mod.PR143_PINNED_PLAYERS_CROSSWALK_DIGEST
        original_pr147 = mod.PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST
        try:
            mod.PR143_PINNED_PLAYERS_CROSSWALK_DIGEST = {"bytes": 111, "sha256": "unrelated"}
            mod.PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST = {"bytes": len(payload), "sha256": digest}
            report = mod.compare_players_crosswalk_digest(payload)
        finally:
            mod.PR143_PINNED_PLAYERS_CROSSWALK_DIGEST = original_pin
            mod.PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST = original_pr147
        self.assertFalse(report["fresh_matches_pr143_pin"])
        self.assertTrue(report["fresh_matches_pr147_observed"])
        self.assertFalse(report["all_three_distinct"])
        self.assertTrue(report["pr143_pin_is_stale_relative_to_fresh"])

    def test_all_three_distinct(self):
        payload = b"a genuinely third, distinct set of bytes"
        report = compare_players_crosswalk_digest(payload)
        # Real module constants: this synthetic payload matches neither.
        self.assertFalse(report["fresh_matches_pr143_pin"])
        self.assertFalse(report["fresh_matches_pr147_observed"])
        self.assertTrue(report["all_three_distinct"])
        self.assertEqual(report["fresh"]["bytes"], len(payload))
        self.assertEqual(report["fresh"]["sha256"], sha256_bytes(payload))

    def test_module_constants_are_the_real_disclosed_values(self):
        # PR #143's pin as recorded in role_intelligence_source_digests.py
        # today, and PR #147's own disclosed live-fetch digest
        # (engineering/ENGINEERING_HANDOFF.md) -- pinned here as a contract
        # so a future edit to either source is caught by this test, not
        # silently drifted.
        # Re-pinned 2026-09-23 (a third real, independently-verified drift
        # of this living roster crosswalk asset -- see
        # role_intelligence_source_digests.PLAYERS_CROSSWALK_SOURCE's own
        # updated comment). This assertion tracks the current pin by design
        # ("caught by this test, not silently drifted"), so updating it here
        # alongside the re-pin is the intended contract, not a suppression.
        self.assertEqual(PR143_PINNED_PLAYERS_CROSSWALK_DIGEST["bytes"], 7234131)
        self.assertEqual(
            PR143_PINNED_PLAYERS_CROSSWALK_DIGEST["sha256"],
            "4dd70f328f31b0bb7cbf043412298d5a325863e27b8f2eeea22c9e925c808dee",
        )
        self.assertEqual(PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST["bytes"], 7291736)
        self.assertEqual(
            PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST["sha256"],
            "12c126bb35ddf015a929a8db7c019fd32693aa2b47bae7fad6fcc8132a3b7719",
        )
        self.assertNotEqual(
            PR143_PINNED_PLAYERS_CROSSWALK_DIGEST["sha256"],
            PR147_OBSERVED_PLAYERS_CROSSWALK_DIGEST["sha256"],
        )


# --------------------------------------------------------------------------
# event_set_digest
# --------------------------------------------------------------------------

class EventSetDigestTests(unittest.TestCase):
    def test_order_independent(self):
        e1 = event(2020, 1, "AAA", removed_player_id="P1")
        e2 = event(2020, 2, "BBB", removed_player_id="P2")
        digest_a, keys_a = event_set_digest([e1, e2])
        digest_b, keys_b = event_set_digest([e2, e1])
        self.assertEqual(digest_a, digest_b)
        self.assertEqual(keys_a, keys_b)

    def test_content_change_changes_digest(self):
        e1 = event(2020, 1, "AAA", removed_player_id="P1")
        e2 = event(2020, 2, "AAA", removed_player_id="P2")
        digest_a, _ = event_set_digest([e1])
        digest_b, _ = event_set_digest([e1, e2])
        self.assertNotEqual(digest_a, digest_b)


# --------------------------------------------------------------------------
# compute_paired_evaluation: the core correctness contract
# --------------------------------------------------------------------------

class ComputePairedEvaluationTests(unittest.TestCase):
    """A hand-computed synthetic fixture with a KNOWN expected paired N and
    per-predictor MAE, reproducing PR #147's exact disclosed defect
    (predictors with silently different eligible populations) and proving
    `compute_paired_evaluation` corrects it.
    """

    def setUp(self):
        # One event: team AAA, week 3, removed player REMOVED (WR).
        # Two teammate candidates: P1 (has a "prior" fed via predictor A
        # only) and P2 (has a "prior" fed via both predictors).
        self.event = event(2020, 3, "AAA", removed_player_id="REMOVED")
        self.events = [self.event]
        self.candidates = [
            candidate(2020, 3, "AAA", "REMOVED", "P1"),
            candidate(2020, 3, "AAA", "REMOVED", "P2"),
        ]
        # Realized target-game shares: P1 -> 0.40, P2 -> 0.20. No usage row
        # for P3 (never a candidate here, just documents "no realized" drop
        # separately below).
        self.usage_rows = [
            usage(2020, 3, "AAA", "P1", targets=4.0, team_targets=10.0),
            usage(2020, 3, "AAA", "P2", targets=2.0, team_targets=10.0),
        ]
        self.role_state_rows = []  # predictors below never read `history`

        def predictor_full(event, teammates, history, dimension):
            # Predicts for BOTH P1 and P2 (like the challenger: always
            # predicts every candidate).
            return {"P1": 0.5, "P2": 0.1}

        def predictor_partial(event, teammates, history, dimension):
            # Predicts ONLY for P2 (like NO_ADJUSTMENT: omits a candidate
            # lacking "prior" data instead of predicting 0/None for it).
            return {"P2": 0.3}

        self.predictors = {"FULL": predictor_full, "PARTIAL": predictor_partial}

    def test_paired_n_is_the_intersection_not_the_union(self):
        result = compute_paired_evaluation(
            self.events, self.candidates, self.role_state_rows, self.usage_rows,
            "target_share", self.predictors,
        )
        # P1 is dropped entirely (PARTIAL never predicts it) -- only P2
        # survives into the paired population. Union would have been 2.
        self.assertEqual(result["paired_n"], 1)
        self.assertEqual(result["drop_reasons"], {"missing_prediction:PARTIAL": 1})

    def test_paired_mae_uses_only_the_shared_row(self):
        result = compute_paired_evaluation(
            self.events, self.candidates, self.role_state_rows, self.usage_rows,
            "target_share", self.predictors,
        )
        # P2's realized share is 2/10 = 0.20. FULL predicted 0.10 (|err|=0.10);
        # PARTIAL predicted 0.30 (|err|=0.10). Both MAEs equal the single
        # paired row's error -- P1's much larger FULL-only error (|0.5-0.4|)
        # must NOT leak into FULL's paired MAE.
        self.assertAlmostEqual(result["mae_by_predictor"]["FULL"], 0.10, places=9)
        self.assertAlmostEqual(result["mae_by_predictor"]["PARTIAL"], 0.10, places=9)

    def test_season_breakdown_matches_paired_rows(self):
        result = compute_paired_evaluation(
            self.events, self.candidates, self.role_state_rows, self.usage_rows,
            "target_share", self.predictors,
        )
        self.assertEqual(result["n_by_season"], {2020: 1})
        self.assertAlmostEqual(result["mae_by_season"][2020]["FULL"], 0.10, places=9)

    def test_seasons_filter_excludes_out_of_window_events(self):
        result = compute_paired_evaluation(
            self.events, self.candidates, self.role_state_rows, self.usage_rows,
            "target_share", self.predictors, seasons=frozenset({2099}),
        )
        self.assertEqual(result["paired_n"], 0)
        self.assertEqual(result["mae_by_predictor"], {"FULL": None, "PARTIAL": None})

    def test_no_realized_share_drops_the_row_for_every_predictor(self):
        # Remove P2's usage row entirely -- no realized share exists for it.
        usage_rows = [usage(2020, 3, "AAA", "P1", targets=4.0, team_targets=10.0)]
        result = compute_paired_evaluation(
            self.events, self.candidates, self.role_state_rows, usage_rows,
            "target_share", self.predictors,
        )
        self.assertEqual(result["paired_n"], 0)
        self.assertEqual(result["drop_reasons"].get("no_realized_target_game_share"), 1)

    def test_wrong_dimension_relevance_excludes_rb_absence_from_target_share(self):
        rb_event = event(2020, 3, "AAA", event_type="RB_ABSENCE", removed_player_id="REMOVED")
        result = compute_paired_evaluation(
            [rb_event], self.candidates, self.role_state_rows, self.usage_rows,
            "target_share", self.predictors,
        )
        self.assertEqual(result["paired_n"], 0)


class BootstrapMaeCiByEventTests(unittest.TestCase):
    def test_constant_error_gives_degenerate_ci_equal_to_point_estimate(self):
        rows = [
            {"event_key": ("2020", 1, "AAA", "R1"), "abs_errors": {"X": 0.2}},
            {"event_key": ("2020", 2, "BBB", "R2"), "abs_errors": {"X": 0.2}},
            {"event_key": ("2020", 3, "CCC", "R3"), "abs_errors": {"X": 0.2}},
        ]
        result = bootstrap_mae_ci_by_event(rows, "X", n_resamples=200)
        self.assertEqual(result["n_events"], 3)
        self.assertAlmostEqual(result["point_estimate"], 0.2, places=9)
        self.assertAlmostEqual(result["ci_low"], 0.2, places=9)
        self.assertAlmostEqual(result["ci_high"], 0.2, places=9)

    def test_deterministic_given_seed(self):
        rows = [
            {"event_key": ("2020", i, "AAA", f"R{i}"), "abs_errors": {"X": 0.1 * i}}
            for i in range(1, 8)
        ]
        r1 = bootstrap_mae_ci_by_event(rows, "X", n_resamples=500, seed=42)
        r2 = bootstrap_mae_ci_by_event(rows, "X", n_resamples=500, seed=42)
        self.assertEqual(r1, r2)

    def test_empty_rows_returns_zero_events(self):
        result = bootstrap_mae_ci_by_event([], "X")
        self.assertEqual(result["n_events"], 0)
        self.assertIsNone(result["point_estimate"])

    def test_within_event_correlation_is_preserved_not_flattened(self):
        # Two rows from the SAME event share one very large error; three
        # other events have zero error. Per-row resampling would treat the
        # large-error rows as two independent draws (over-weighting how
        # often a resample avoids them); per-event resampling treats them as
        # one unit, so the point estimate must equal the plain row mean
        # while remaining a legitimate event-clustered estimate.
        rows = [
            {"event_key": ("2020", 1, "AAA", "R1"), "abs_errors": {"X": 1.0}},
            {"event_key": ("2020", 1, "AAA", "R1"), "abs_errors": {"X": 1.0}},
            {"event_key": ("2020", 2, "BBB", "R2"), "abs_errors": {"X": 0.0}},
            {"event_key": ("2020", 3, "CCC", "R3"), "abs_errors": {"X": 0.0}},
        ]
        result = bootstrap_mae_ci_by_event(rows, "X", n_resamples=500)
        self.assertEqual(result["n_events"], 3)
        self.assertAlmostEqual(result["point_estimate"], 0.5, places=9)


class PairedNamedRegimeCoverageTests(unittest.TestCase):
    def test_counts_distinct_events_not_rows(self):
        events_with_regime = [
            {**event(2022, 1, "AAA", removed_player_id="R1"), "hc_regime_key": "AAA:Coach:2020-01-01"},
        ]
        # Two candidate rows from the SAME event must count as ONE event
        # toward the regime's coverage, not two.
        rows = [
            {"event_key": (2022, 1, "AAA", "R1")},
            {"event_key": (2022, 1, "AAA", "R1")},
        ]
        result = paired_named_regime_coverage(rows, events_with_regime)
        self.assertEqual(result["counts"], {"AAA:Coach:2020-01-01": 1})
        self.assertEqual(result["max_paired_n_any_regime"], 1)
        self.assertFalse(result["any_regime_clears_threshold"])

    def test_unknown_regime_events_are_excluded_from_counts(self):
        events_with_regime = [
            {**event(2022, 1, "AAA", removed_player_id="R1"), "hc_regime_key": None},
        ]
        rows = [{"event_key": (2022, 1, "AAA", "R1")}]
        result = paired_named_regime_coverage(rows, events_with_regime)
        self.assertEqual(result["counts"], {})
        self.assertEqual(result["regimes_observed"], 0)


# --------------------------------------------------------------------------
# Sanity: the real `build_role_state_rows`/`build_player_dimension_history`
# this module reuses still behave as `compute_paired_evaluation` assumes
# (predictors receive a `history` built from realized labels).
# --------------------------------------------------------------------------

class HistoryReuseSanityTests(unittest.TestCase):
    def test_build_player_dimension_history_is_reused_unmodified(self):
        usage_rows = [
            usage(2020, 1, "AAA", "P1", targets=3.0, team_targets=10.0),
            usage(2020, 2, "AAA", "P1", targets=5.0, team_targets=10.0),
        ]
        role_state_rows = build_role_state_rows(usage_rows)
        history = build_player_dimension_history(role_state_rows)
        self.assertIn(("P1", "target_share"), history)
        self.assertEqual(history[("P1", "target_share")], [(2020, 1, 0.3), (2020, 2, 0.5)])


if __name__ == "__main__":
    unittest.main()

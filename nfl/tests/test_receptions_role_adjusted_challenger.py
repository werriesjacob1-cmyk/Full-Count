#!/usr/bin/env python3
"""Tests for nfl.research.receptions_role_adjusted_challenger.

Fixtures use the exact real (season, week, team, player_id, dimension)
shapes `role_regime_redistribution.predict_committee_model` and
`role_intelligence_baselines.predict_no_adjustment` actually consume
(verified by reading both modules directly), not invented shapes -- this
module is a thin adapter over that real, already-tested machinery, so its
own tests exercise the adapter's arithmetic and None-handling, not
role_regime_redistribution's internals a second time.
"""
from __future__ import annotations

import unittest

from nfl.research.receptions_role_adjusted_challenger import (
    FROZEN_COMMITTEE_MODEL,
    RoleAdjustedChallengerError,
    build_role_adjusted_challenger_record,
    compute_role_adjusted_projection,
    predicted_post_redistribution_target_share,
    role_adjusted_side_probabilities_for_lines,
)

REAL_SHAPE_RESIDUALS = [0.5, -1.0, 2.0, 0.0, -0.5, 1.5, -2.0, 3.0, -1.5, 0.5] * 5

EVENT = {
    "event_type": "WR_ABSENCE",
    "season": 2026,
    "week": 3,
    "team": "SEA",
    "removed_player_id": "00-0011111",
    "hc_regime_tenure_bucket": "ESTABLISHED_REGIME",
}

TEAMMATE_A = {
    "candidate_player_id": "00-0022222",
    "candidate_prior_target_share_mean_last5": 0.15,
    "candidate_depth_team": 2,
    "candidate_prior_games_n": 10,
}
TEAMMATE_B = {
    "candidate_player_id": "00-0033333",
    "candidate_prior_target_share_mean_last5": 0.10,
    "candidate_depth_team": 3,
    "candidate_prior_games_n": 8,
}

HISTORY = {
    ("00-0011111", "target_share"): [(2026, 1, 0.30), (2026, 2, 0.28)],
    ("00-0022222", "target_share"): [(2026, 1, 0.14), (2026, 2, 0.16)],
    ("00-0033333", "target_share"): [(2026, 1, 0.09), (2026, 2, 0.11)],
}


class PredictedPostRedistributionShareTests(unittest.TestCase):
    def test_real_absence_event_produces_a_real_predicted_share(self):
        share = predicted_post_redistribution_target_share(
            event=EVENT, teammates=[TEAMMATE_A, TEAMMATE_B], history=HISTORY,
            candidate_player_id="00-0022222",
        )
        self.assertIsInstance(share, float)
        self.assertGreater(share, 0.0)
        # The committee model can only ever add absorption on top of the
        # teammate's own strictly-prior baseline share -- never subtract.
        self.assertGreaterEqual(share, TEAMMATE_A["candidate_prior_target_share_mean_last5"])

    def test_absence_of_valid_opportunities_returns_none(self):
        # No teammates at all -- nothing to predict, never a fabricated share.
        self.assertIsNone(
            predicted_post_redistribution_target_share(
                event=EVENT, teammates=[], history=HISTORY, candidate_player_id="00-0022222",
            )
        )

    def test_wrong_candidate_identity_returns_none(self):
        # A player who is not actually among the real teammates for this
        # event must never receive an invented prediction.
        self.assertIsNone(
            predicted_post_redistribution_target_share(
                event=EVENT, teammates=[TEAMMATE_A, TEAMMATE_B], history=HISTORY,
                candidate_player_id="00-0099999-NOT-A-REAL-TEAMMATE",
            )
        )


class ComputeRoleAdjustedProjectionTests(unittest.TestCase):
    def test_real_ratio_scales_b0_projection(self):
        adjusted = compute_role_adjusted_projection(
            b0_projection=4.0,
            candidate_own_prior_target_share=0.15,
            predicted_post_redistribution_target_share=0.225,
        )
        self.assertAlmostEqual(adjusted, 4.0 * (0.225 / 0.15))

    def test_missing_candidate_prior_share_returns_none(self):
        self.assertIsNone(
            compute_role_adjusted_projection(
                b0_projection=4.0, candidate_own_prior_target_share=None,
                predicted_post_redistribution_target_share=0.2,
            )
        )

    def test_missing_predicted_share_returns_none(self):
        self.assertIsNone(
            compute_role_adjusted_projection(
                b0_projection=4.0, candidate_own_prior_target_share=0.15,
                predicted_post_redistribution_target_share=None,
            )
        )

    def test_zero_prior_share_returns_none_not_a_division_artifact(self):
        # Dividing by zero would either crash or, if guarded naively, could
        # silently produce an absurd/undefined ratio -- must be None.
        self.assertIsNone(
            compute_role_adjusted_projection(
                b0_projection=4.0, candidate_own_prior_target_share=0.0,
                predicted_post_redistribution_target_share=0.2,
            )
        )

    def test_negative_prior_share_returns_none(self):
        self.assertIsNone(
            compute_role_adjusted_projection(
                b0_projection=4.0, candidate_own_prior_target_share=-0.05,
                predicted_post_redistribution_target_share=0.2,
            )
        )

    def test_non_positive_b0_projection_returns_none(self):
        self.assertIsNone(
            compute_role_adjusted_projection(
                b0_projection=0.0, candidate_own_prior_target_share=0.15,
                predicted_post_redistribution_target_share=0.2,
            )
        )


class RoleAdjustedSideProbabilitiesForLinesTests(unittest.TestCase):
    def test_coherent_standard_and_alt_lines_share_one_distribution(self):
        result = role_adjusted_side_probabilities_for_lines(
            adjusted_projection=4.5,
            lines=[3.5, 5.5],
            over_odds_by_line={3.5: -115, 5.5: -110},
            under_odds_by_line={3.5: -105, 5.5: -110},
            residuals=REAL_SHAPE_RESIDUALS,
        )
        self.assertEqual(set(result.keys()), {3.5, 5.5})
        # A higher threshold on the SAME distribution must never show a
        # higher over-probability -- this is what "one shared distribution"
        # actually guarantees, not merely two separately-run calls.
        self.assertLess(result[5.5]["model_over_probability"], result[3.5]["model_over_probability"])
        self.assertGreater(result[5.5]["model_under_probability"], result[3.5]["model_under_probability"])

    def test_missing_price_for_one_line_fails_closed(self):
        with self.assertRaises(RoleAdjustedChallengerError):
            role_adjusted_side_probabilities_for_lines(
                adjusted_projection=4.5,
                lines=[3.5, 5.5],
                over_odds_by_line={3.5: -115},  # 5.5 missing -- no fabricated price
                under_odds_by_line={3.5: -105, 5.5: -110},
                residuals=REAL_SHAPE_RESIDUALS,
            )

    def test_no_lines_fails_closed(self):
        with self.assertRaises(RoleAdjustedChallengerError):
            role_adjusted_side_probabilities_for_lines(
                adjusted_projection=4.5, lines=[], over_odds_by_line={}, under_odds_by_line={},
                residuals=REAL_SHAPE_RESIDUALS,
            )


class BuildRoleAdjustedChallengerRecordTests(unittest.TestCase):
    def test_real_end_to_end_record_source_to_frozen_prediction(self):
        record = build_role_adjusted_challenger_record(
            b0_projection=4.0, line=3.5, over_odds=-115, under_odds=-105,
            residuals=REAL_SHAPE_RESIDUALS, event=EVENT,
            teammates=[TEAMMATE_A, TEAMMATE_B], history=HISTORY,
            candidate_player_id="00-0022222",
            candidate_own_prior_target_share=0.15,
        )
        self.assertIsNotNone(record)
        self.assertEqual(record["candidate_player_id"], "00-0022222")
        self.assertEqual(record["removed_player_id"], "00-0011111")
        self.assertEqual(record["event_type"], "WR_ABSENCE")
        self.assertGreater(record["adjusted_projection"], record["b0_projection"])
        self.assertIn("model_over_probability", record)
        self.assertIn("model_held_out_finding", record)
        self.assertEqual(record["status"], "RESEARCH_ONLY_NOT_PROMOTED")
        self.assertEqual(record["prediction_source"], "B0_VS_ROLE_ADJUSTED_HIERARCHICAL_COMMITTEE_V1")

    def test_missing_own_prior_share_yields_no_record_not_a_fabricated_one(self):
        record = build_role_adjusted_challenger_record(
            b0_projection=4.0, line=3.5, over_odds=-115, under_odds=-105,
            residuals=REAL_SHAPE_RESIDUALS, event=EVENT,
            teammates=[TEAMMATE_A, TEAMMATE_B], history=HISTORY,
            candidate_player_id="00-0022222",
            candidate_own_prior_target_share=None,
        )
        self.assertIsNone(record)

    def test_wrong_candidate_identity_yields_no_record(self):
        record = build_role_adjusted_challenger_record(
            b0_projection=4.0, line=3.5, over_odds=-115, under_odds=-105,
            residuals=REAL_SHAPE_RESIDUALS, event=EVENT,
            teammates=[TEAMMATE_A, TEAMMATE_B], history=HISTORY,
            candidate_player_id="00-0099999-NOT-A-REAL-TEAMMATE",
            candidate_own_prior_target_share=0.15,
        )
        self.assertIsNone(record)

    def test_no_teammates_yields_no_record(self):
        record = build_role_adjusted_challenger_record(
            b0_projection=4.0, line=3.5, over_odds=-115, under_odds=-105,
            residuals=REAL_SHAPE_RESIDUALS, event=EVENT,
            teammates=[], history=HISTORY,
            candidate_player_id="00-0022222",
            candidate_own_prior_target_share=0.15,
        )
        self.assertIsNone(record)

    def test_contradictory_zero_prior_share_never_produces_a_record(self):
        # A data-quality contradiction (candidate has a real teammate row
        # but a reported-zero prior share) must fail closed, not silently
        # divide into an arbitrary adjusted projection.
        record = build_role_adjusted_challenger_record(
            b0_projection=4.0, line=3.5, over_odds=-115, under_odds=-105,
            residuals=REAL_SHAPE_RESIDUALS, event=EVENT,
            teammates=[TEAMMATE_A, TEAMMATE_B], history=HISTORY,
            candidate_player_id="00-0022222",
            candidate_own_prior_target_share=0.0,
        )
        self.assertIsNone(record)


class FrozenModelIsNeverRetrainedTests(unittest.TestCase):
    def test_frozen_model_is_a_fixed_constant_with_full_provenance(self):
        self.assertEqual(FROZEN_COMMITTEE_MODEL["model_name"], "HIERARCHICAL_COMMITTEE_PROBABILITY_V1")
        self.assertEqual(FROZEN_COMMITTEE_MODEL["status"], "RESEARCH_ONLY_NOT_PROMOTED")
        self.assertIn("held_out_report", FROZEN_COMMITTEE_MODEL)
        self.assertIn("NO_ADJUSTMENT", FROZEN_COMMITTEE_MODEL["held_out_report"])
        # The disclosed negative finding must survive verbatim -- this test
        # fails if a future edit quietly removes or inverts the disclosure.
        self.assertIn("did NOT beat", FROZEN_COMMITTEE_MODEL["held_out_finding"])

    def test_calling_the_module_twice_never_mutates_the_frozen_constant(self):
        import copy

        before = copy.deepcopy(FROZEN_COMMITTEE_MODEL)
        build_role_adjusted_challenger_record(
            b0_projection=4.0, line=3.5, over_odds=-115, under_odds=-105,
            residuals=REAL_SHAPE_RESIDUALS, event=EVENT,
            teammates=[TEAMMATE_A, TEAMMATE_B], history=HISTORY,
            candidate_player_id="00-0022222", candidate_own_prior_target_share=0.15,
        )
        self.assertEqual(FROZEN_COMMITTEE_MODEL, before)


if __name__ == "__main__":
    unittest.main()

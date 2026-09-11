#!/usr/bin/env python3
"""Contracts for the prior-only NFL team pass-attempt EB baseline."""
import unittest

from nfl.research import team_attempt_eb as eb


class TeamAttemptEmpiricalBayesTests(unittest.TestCase):
    def test_zero_between_team_variance_shrinks_fully_to_league_mean(self):
        histories = {
            "A": [30, 32, 28, 31],
            "B": [29, 31, 30, 31],
            "C": [30, 29, 31, 30],
        }
        hyper = eb.estimate_hyperprior(histories, min_teams=3)
        self.assertEqual(hyper["tau2"], 0.0)

        pred = eb.predict_from_history(
            histories["A"],
            grand_mean=hyper["grand_mean"],
            tau2=hyper["tau2"],
        )
        self.assertEqual(pred["weight"], 0.0)
        self.assertAlmostEqual(
            pred["prediction"],
            hyper["grand_mean"],
        )

    def test_zero_sampling_noise_with_positive_tau_gets_full_team_weight(self):
        pred = eb.predict_from_history(
            [40.0] * 10,
            grand_mean=30.0,
            tau2=4.0,
        )
        self.assertEqual(pred["weight"], 1.0)
        self.assertAlmostEqual(pred["prediction"], 40.0)

    def test_known_shrinkage_weight_matches_closed_form(self):
        history = [30.0, 34.0, 32.0, 36.0]
        pred = eb.predict_from_history(
            history,
            grand_mean=28.0,
            tau2=2.0,
        )
        # sample variance = 20/3; sampling variance of mean = 5/3
        expected_weight = 2.0 / (2.0 + 5.0/3.0)
        expected = 28.0 + expected_weight * (33.0 - 28.0)
        self.assertAlmostEqual(pred["weight"], expected_weight)
        self.assertAlmostEqual(pred["prediction"], expected)

    def test_hyperprior_uses_equal_team_means_not_game_weighting(self):
        histories = {
            "A": [20.0, 20.0],
            "B": [30.0, 30.0, 30.0, 30.0],
            "C": [40.0, 40.0],
        }
        hyper = eb.estimate_hyperprior(histories, min_teams=3)
        self.assertAlmostEqual(hyper["grand_mean"], 30.0)

    def test_hyperprior_fails_closed_on_too_few_teams(self):
        with self.assertRaisesRegex(ValueError, "teams"):
            eb.estimate_hyperprior(
                {"A": [30.0, 31.0], "B": [29.0, 30.0]},
                min_teams=3,
            )

    def test_negative_attempt_value_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "negative"):
            eb.predict_from_history(
                [30.0, -1.0, 32.0],
                grand_mean=30.0,
                tau2=2.0,
            )

    def test_negative_tau_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "tau2"):
            eb.predict_from_history(
                [30.0, 31.0, 32.0],
                grand_mean=30.0,
                tau2=-0.1,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

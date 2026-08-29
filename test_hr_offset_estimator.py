#!/usr/bin/env python3
"""Numerical integrity tests for the locked HR offset estimator."""
from __future__ import annotations

import unittest

import numpy as np

from backtest.hr_offset_estimator import (
    HREstimatorIntegrityError,
    apply_standardizer,
    fit_offset_logistic,
    fit_standardizer,
    logit,
    objective_and_gradient,
    predict_supported,
    predict_with_champion_fallback,
)


class ObjectiveTests(unittest.TestCase):
    def test_analytic_gradient_matches_finite_difference(self):
        champion = np.array([0.20, 0.35, 0.55, 0.72, 0.81])
        offsets = logit(champion)
        x = np.array([
            [-1.2, 0.3],
            [-0.5, -0.7],
            [0.0, 0.2],
            [0.8, 1.1],
            [1.4, -0.4],
        ])
        y = np.array([0, 0, 1, 1, 0], dtype=float)
        beta = np.array([0.23, -0.17], dtype=float)

        _, analytic = objective_and_gradient(beta, offsets, x, y)
        eps = 1e-6
        numeric = np.zeros_like(beta)
        for j in range(beta.size):
            plus = beta.copy()
            minus = beta.copy()
            plus[j] += eps
            minus[j] -= eps
            lp, _ = objective_and_gradient(plus, offsets, x, y)
            lm, _ = objective_and_gradient(minus, offsets, x, y)
            numeric[j] = (lp - lm) / (2 * eps)

        np.testing.assert_allclose(analytic, numeric, rtol=1e-6, atol=1e-6)

    def test_objective_is_summed_bce_plus_half_l2(self):
        champion = np.array([0.25, 0.75])
        offsets = logit(champion)
        x = np.array([[-1.0], [1.0]])
        y = np.array([0.0, 1.0])
        beta = np.array([0.2])
        loss, _ = objective_and_gradient(beta, offsets, x, y)
        eta = offsets + x[:, 0] * beta[0]
        expected = float(
            np.sum(np.logaddexp(0.0, eta) - y * eta)
            + 0.5 * np.dot(beta, beta)
        )
        self.assertAlmostEqual(loss, expected, places=12)


class StandardizationTests(unittest.TestCase):
    def test_training_standardizer_uses_population_ddof_zero(self):
        x = np.array([[1.0, 2.0], [3.0, 6.0], [5.0, 10.0]])
        s = fit_standardizer(x)
        np.testing.assert_allclose(s["mean"], np.mean(x, axis=0))
        np.testing.assert_allclose(s["std"], np.std(x, axis=0, ddof=0))
        standardized = apply_standardizer(x, s)
        np.testing.assert_allclose(np.mean(standardized, axis=0), [0.0, 0.0], atol=1e-12)
        np.testing.assert_allclose(np.std(standardized, axis=0, ddof=0), [1.0, 1.0], atol=1e-12)

    def test_zero_variance_feature_aborts(self):
        x = np.array([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0]])
        with self.assertRaises(HREstimatorIntegrityError):
            fit_standardizer(x)


class PredictionTests(unittest.TestCase):
    def test_beta_zero_reproduces_champion_exactly_up_to_fp(self):
        champion = np.array([0.12, 0.33, 0.51, 0.79])
        x = np.array([
            [-1.0, 0.0],
            [0.0, 1.0],
            [1.0, -1.0],
            [0.5, 0.5],
        ])
        fitted = {
            "beta": np.zeros(2),
            "standardizer": {"mean": np.zeros(2), "std": np.ones(2)},
        }
        got = predict_supported(champion, x, fitted)
        np.testing.assert_allclose(got, champion, rtol=0, atol=1e-15)

    def test_unsupported_rows_preserve_original_champion_even_with_nan_features(self):
        champion = np.array([0.0, 0.60, 1.0])
        x = np.array([
            [np.nan, np.nan],
            [1.0, 2.0],
            [np.nan, np.nan],
        ])
        mask = np.array([False, True, False])
        fitted = {
            "beta": np.zeros(2),
            "standardizer": {"mean": np.zeros(2), "std": np.ones(2)},
        }
        got = predict_with_champion_fallback(champion, x, mask, fitted)
        self.assertEqual(got[0], 0.0)
        self.assertEqual(got[2], 1.0)
        self.assertAlmostEqual(got[1], 0.60, places=15)

    def test_nan_on_supported_row_fails_closed(self):
        champion = np.array([0.60, 0.70])
        x = np.array([[1.0, np.nan], [2.0, 3.0]])
        fitted = {
            "beta": np.zeros(2),
            "standardizer": {"mean": np.zeros(2), "std": np.ones(2)},
        }
        with self.assertRaises(HREstimatorIntegrityError):
            predict_with_champion_fallback(
                champion,
                x,
                np.array([True, False]),
                fitted,
            )


class FitTests(unittest.TestCase):
    @staticmethod
    def training_data():
        n = 120
        x1 = np.linspace(-2.0, 2.0, n)
        x2 = np.cos(np.linspace(0.0, 5.0, n))
        x = np.column_stack([x1, x2])
        champion = np.linspace(0.18, 0.42, n)
        # Deterministic, non-separable pattern with a real feature relationship.
        score = logit(champion) + 0.7 * x1 - 0.35 * x2
        latent = 1.0 / (1.0 + np.exp(-score))
        thresholds = np.array([
            0.22, 0.48, 0.68, 0.35, 0.57, 0.76, 0.41, 0.63
        ])
        y = (latent > np.resize(thresholds, n)).astype(float)
        return champion, x, y

    def test_fit_is_deterministic_and_has_no_intercept(self):
        champion, x, y = self.training_data()
        a = fit_offset_logistic(champion, x, y)
        b = fit_offset_logistic(champion, x, y)
        np.testing.assert_allclose(a["beta"], b["beta"], rtol=0, atol=1e-12)
        self.assertIsNone(a["optimizer"]["intercept"])
        self.assertTrue(a["optimizer"]["success"])
        self.assertLessEqual(a["optimizer"]["max_abs_gradient"], 1e-5)

    def test_fit_changes_supported_probabilities_when_signal_exists(self):
        champion, x, y = self.training_data()
        fitted = fit_offset_logistic(champion, x, y)
        got = predict_supported(champion, x, fitted)
        self.assertGreater(float(np.max(np.abs(got - champion))), 1e-4)

    def test_non_binary_training_outcome_aborts(self):
        champion, x, y = self.training_data()
        y = y.copy()
        y[0] = 2.0
        with self.assertRaises(HREstimatorIntegrityError):
            fit_offset_logistic(champion, x, y)


if __name__ == "__main__":
    unittest.main(verbosity=2)

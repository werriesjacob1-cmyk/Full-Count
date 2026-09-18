#!/usr/bin/env python3
import random
import unittest

from nfl.research.game_market_c2_ridge import RidgeError, fit_ridge, predict_ridge


def _synthetic_rows(n=200, seed=7):
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        x1 = rng.uniform(-5, 5)
        x2 = rng.uniform(-5, 5)
        noise = rng.uniform(-0.01, 0.01)
        y = 3.0 * x1 - 2.0 * x2 + 10.0 + noise
        rows.append({"x1": x1, "x2": x2, "y": y})
    return rows


class GameMarketC2RidgeTests(unittest.TestCase):
    def test_fit_recovers_near_exact_linear_relationship_with_low_penalty(self):
        rows = _synthetic_rows()
        model = fit_ridge(rows, feature_names=["x1", "x2"], target_key="y", ridge_lambda=0.001)
        self.assertAlmostEqual(model["target_mean"], 10.0, delta=1.0)
        prediction = predict_ridge({"x1": 2.0, "x2": -1.0}, model)
        expected = 3.0 * 2.0 - 2.0 * (-1.0) + 10.0
        self.assertAlmostEqual(prediction, expected, delta=0.5)
        self.assertLess(model["training_mae"], 0.5)

    def test_higher_ridge_lambda_shrinks_coefficients_toward_zero(self):
        rows = _synthetic_rows()
        weak = fit_ridge(rows, feature_names=["x1", "x2"], target_key="y", ridge_lambda=0.001)
        strong = fit_ridge(rows, feature_names=["x1", "x2"], target_key="y", ridge_lambda=5000.0)
        self.assertLess(
            abs(strong["coefficients"]["x1"]), abs(weak["coefficients"]["x1"])
        )
        self.assertLess(
            abs(strong["coefficients"]["x2"]), abs(weak["coefficients"]["x2"])
        )

    def test_fit_rejects_more_features_than_rows(self):
        rows = [{"x1": 1.0, "x2": 2.0, "y": 3.0}, {"x1": 2.0, "x2": 1.0, "y": 4.0}]
        with self.assertRaisesRegex(RidgeError, "more rows than features"):
            fit_ridge(rows, feature_names=["x1", "x2"], target_key="y", ridge_lambda=1.0)

    def test_fit_rejects_negative_ridge_lambda(self):
        rows = _synthetic_rows(n=20)
        with self.assertRaisesRegex(RidgeError, "non-negative"):
            fit_ridge(rows, feature_names=["x1", "x2"], target_key="y", ridge_lambda=-1.0)

    def test_constant_feature_does_not_divide_by_zero(self):
        rows = [{"x1": 5.0, "x2": float(i), "y": float(i)} for i in range(10)]
        model = fit_ridge(rows, feature_names=["x1", "x2"], target_key="y", ridge_lambda=1.0)
        self.assertEqual(model["feature_stats"]["x1"]["std"], 1.0)
        prediction = predict_ridge({"x1": 5.0, "x2": 3.0}, model)
        self.assertTrue(isinstance(prediction, float))


if __name__ == "__main__":
    unittest.main()

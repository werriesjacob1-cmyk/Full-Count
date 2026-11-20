import unittest

from nfl.research.fc_nfl_003.market_residual_probe import fit_slope, score


class ProbeTests(unittest.TestCase):
    def test_fit_slope_and_equal_direction_population(self):
        rows = [
            {"line_margin": 0.0, "b0_margin": 2.0, "actual_margin": 1.0},
            {"line_margin": 0.0, "b0_margin": -2.0, "actual_margin": -1.0},
        ]
        self.assertEqual(fit_slope(rows, "margin"), 0.5)
        positive = score(rows, "margin", 0.5)
        negative = score(rows, "margin", -0.5)
        self.assertEqual(positive["direction"]["b0"]["n"], negative["direction"]["residual_blend"]["n"])
        self.assertEqual(positive["direction"]["residual_blend"]["correct"], 2)
        self.assertEqual(negative["direction"]["residual_blend"]["correct"], 0)

    def test_push_and_zero_disagreement_never_generate_side(self):
        rows = [
            {"line_total": 45.0, "b0_total": 46.0, "actual_total": 45.0},
            {"line_total": 45.0, "b0_total": 45.0, "actual_total": 47.0},
        ]
        outcome = score(rows, "total", 0.2)
        self.assertEqual(outcome["pushes"], 1)
        self.assertEqual(outcome["direction"]["b0"]["n"], 0)
        self.assertEqual(outcome["direction"]["residual_blend"]["n"], 0)


if __name__ == "__main__":
    unittest.main()


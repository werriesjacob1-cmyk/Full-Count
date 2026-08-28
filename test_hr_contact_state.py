#!/usr/bin/env python3
"""Leakage is the whole risk here: a batter's bat-tracking on the game date
includes the swing that produced the home run being predicted."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import hr_contact_state as hc


def sw(date, bid=1, **kw):
    r = {"game_date": date, "batter": bid, "bat_speed": 72.0,
         "swing_length": 7.1, "attack_angle": 11.0, "swing_path_tilt": 31.0,
         "attack_direction": 2.0, "hit_distance_sc": 340.0}
    r.update(kw); return r


class TestLeakage(unittest.TestCase):
    def setUp(self):
        self.p = ([sw("2024-05-%02d" % d) for d in range(1, 20)] +
                  [sw("2024-05-20", bat_speed=999.0)])  # the game being predicted

    def test_same_day_swings_are_excluded(self):
        got = hc.prior_swings(self.p, 1, "2024-05-20")
        self.assertTrue(all(x["game_date"] < "2024-05-20" for x in got))
        self.assertNotIn(999.0, [x["bat_speed"] for x in got])

    def test_future_swings_are_excluded(self):
        got = hc.prior_swings(self.p + [sw("2024-06-01")], 1, "2024-05-20")
        self.assertTrue(all(x["game_date"] < "2024-05-20" for x in got))

    def test_leakage_assertion_passes_on_clean_data(self):
        self.assertTrue(hc.assert_no_same_game_leakage(self.p, 1, "2024-05-20"))

    def test_other_batters_never_enter(self):
        got = hc.prior_swings(self.p + [sw("2024-05-10", bid=2)], 1, "2024-05-20")
        self.assertTrue(all(x["batter"] == 1 for x in got))


class TestSupportAndMissingness(unittest.TestCase):
    def test_below_minimum_returns_none_not_a_mean(self):
        p = [sw("2024-05-%02d" % d) for d in range(1, 5)]
        b = hc.bat_speed_state(p, 1, "2024-05-20")
        self.assertFalse(b["supported"])
        self.assertIsNone(b["bat_speed_mean"])   # absent is not neutral

    def test_supported_when_enough_prior_swings(self):
        p = [sw("2024-05-%02d" % ((d % 28) + 1)) for d in range(hc.MIN_SWINGS + 5)]
        self.assertTrue(hc.bat_speed_state(p, 1, "2024-06-01")["supported"])

    def test_untracked_pitches_are_not_counted_as_swings(self):
        p = [sw("2024-05-01", bat_speed=None) for _ in range(50)]
        self.assertEqual(hc.bat_speed_state(p, 1, "2024-06-01")["n_swings"], 0)


class TestScope(unittest.TestCase):
    def test_arm_angle_is_excluded_by_design(self):
        """Pitcher release geometry would confound a batter contact test."""
        self.assertNotIn("arm_angle", hc.BAT_TRACKING_FIELDS)
        self.assertIn("arm_angle", hc.EXCLUDED_BY_DESIGN)

    def test_geometry_arm_excludes_bat_speed(self):
        """Arms B and C must be separable or the ladder proves nothing."""
        g = hc.swing_geometry_state(
            [sw("2024-05-%02d" % ((d % 28) + 1)) for d in range(50)], 1, "2024-06-01")
        self.assertNotIn("bat_speed_mean", g)

    def test_locked_window_constants(self):
        self.assertEqual((hc.TRAILING_SWINGS, hc.MIN_SWINGS), (100, 30))


if __name__ == "__main__":
    unittest.main(verbosity=2)

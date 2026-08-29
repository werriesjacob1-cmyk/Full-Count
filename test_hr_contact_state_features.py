#!/usr/bin/env python3
"""Synthetic leakage/identity tests for HR contact-state feature extraction."""
from __future__ import annotations

import unittest

import pandas as pd

from backtest.hr_contact_state_features import (
    ContactStateIntegrityError,
    extract_contact_state,
)


def frame(rows):
    return pd.DataFrame(rows)


def swing(
    date,
    batter,
    bat_speed,
    attack_angle=10.0,
    swing_length=7.0,
    swing_path_tilt=20.0,
    attack_direction=0.0,
    hit_distance_sc=350.0,
    game_pk=1,
    at_bat_number=1,
    player_name="Pitcher, Wrong Name",
):
    return {
        "game_date": date,
        "game_pk": game_pk,
        "batter": batter,
        "player_name": player_name,
        "at_bat_number": at_bat_number,
        "bat_speed": bat_speed,
        "attack_angle": attack_angle,
        "swing_length": swing_length,
        "swing_path_tilt": swing_path_tilt,
        "attack_direction": attack_direction,
        "hit_distance_sc": hit_distance_sc,
    }


class TimingTests(unittest.TestCase):
    def test_same_day_and_future_swings_are_structurally_excluded(self):
        rows = []
        for i in range(30):
            rows.append(
                swing(
                    "2026-05-01",
                    100,
                    70 + i / 100,
                    game_pk=10,
                    at_bat_number=i + 1,
                )
            )
        rows.append(
            swing(
                "2026-05-02",
                100,
                999.0,
                attack_angle=999.0,
                swing_length=999.0,
                swing_path_tilt=999.0,
                attack_direction=999.0,
                hit_distance_sc=999.0,
                game_pk=11,
            )
        )
        rows.append(
            swing(
                "2026-05-03",
                100,
                888.0,
                game_pk=12,
            )
        )
        got = extract_contact_state(frame(rows), 100, "2026-05-02")
        self.assertEqual(got["tracked_window_n"], 30)
        self.assertEqual(got["window_last_game_date"], "2026-05-01")
        self.assertLess(got["features"]["bat_speed_mean"], 71.0)
        self.assertTrue(got["support"]["D"])

    def test_last_100_tracked_swings_not_full_history(self):
        rows = []
        for i in range(130):
            # Oldest 30 are deliberately extreme and must fall out.
            speed = 10.0 if i < 30 else 70.0
            rows.append(
                swing(
                    "2026-04-%02d" % (1 + i // 6),
                    100,
                    speed,
                    game_pk=100 + i // 6,
                    at_bat_number=i + 1,
                )
            )
        got = extract_contact_state(frame(rows), 100, "2026-06-01")
        self.assertEqual(got["tracked_window_n"], 100)
        self.assertAlmostEqual(got["features"]["bat_speed_mean"], 70.0)
        self.assertAlmostEqual(got["features"]["bat_speed_p90"], 70.0)


class IdentityTests(unittest.TestCase):
    def test_batter_id_not_pitch_level_player_name_controls_identity(self):
        rows = []
        for i in range(30):
            rows.append(
                swing(
                    "2026-05-01",
                    100,
                    70.0,
                    player_name="Same Pitcher Name",
                    at_bat_number=i + 1,
                )
            )
            rows.append(
                swing(
                    "2026-05-01",
                    200,
                    90.0,
                    player_name="Same Pitcher Name",
                    at_bat_number=100 + i,
                )
            )
        got_100 = extract_contact_state(frame(rows), 100, "2026-05-02")
        got_200 = extract_contact_state(frame(rows), 200, "2026-05-02")
        self.assertAlmostEqual(got_100["features"]["bat_speed_mean"], 70.0)
        self.assertAlmostEqual(got_200["features"]["bat_speed_mean"], 90.0)

    def test_missing_retained_source_column_aborts(self):
        rows = [swing("2026-05-01", 100, 70.0)]
        bad = frame(rows).drop(columns=["swing_path_tilt"])
        with self.assertRaises(ContactStateIntegrityError):
            extract_contact_state(bad, 100, "2026-05-02")


class SupportTests(unittest.TestCase):
    def test_b_supported_while_geometry_unsupported(self):
        rows = []
        for i in range(35):
            rows.append(
                swing(
                    "2026-05-01",
                    100,
                    70.0 + i / 10,
                    attack_angle=10.0 if i < 29 else None,
                    swing_length=7.0,
                    swing_path_tilt=20.0,
                    attack_direction=0.0,
                    at_bat_number=i + 1,
                )
            )
        got = extract_contact_state(frame(rows), 100, "2026-05-02")
        self.assertTrue(got["support"]["B"])
        self.assertFalse(got["support"]["C"])
        self.assertFalse(got["support"]["D"])
        self.assertFalse(got["support"]["E"])
        self.assertIsNotNone(got["features"]["bat_speed_mean"])
        self.assertIsNone(got["features"]["attack_angle_mean"])

    def test_e_requires_thirty_hit_distance_values_in_same_window(self):
        rows = []
        for i in range(40):
            rows.append(
                swing(
                    "2026-05-01",
                    100,
                    70.0,
                    hit_distance_sc=300.0 + i if i < 29 else None,
                    at_bat_number=i + 1,
                )
            )
        got = extract_contact_state(frame(rows), 100, "2026-05-02")
        self.assertTrue(got["support"]["D"])
        self.assertFalse(got["support"]["E"])
        self.assertIsNone(got["features"]["hit_distance_sc_mean"])
        self.assertEqual(got["feature_counts"]["hit_distance_sc"], 29)

    def test_e_uses_locked_arithmetic_mean(self):
        rows = []
        distances = [300.0 + i for i in range(30)]
        for i, distance in enumerate(distances):
            rows.append(
                swing(
                    "2026-05-01",
                    100,
                    70.0,
                    hit_distance_sc=distance,
                    at_bat_number=i + 1,
                )
            )
        got = extract_contact_state(frame(rows), 100, "2026-05-02")
        self.assertTrue(got["support"]["E"])
        self.assertAlmostEqual(
            got["features"]["hit_distance_sc_mean"],
            sum(distances) / len(distances),
        )

    def test_bat_speed_p90_uses_locked_linear_interpolation(self):
        rows = []
        values = [float(i) for i in range(1, 31)]
        for i, value in enumerate(values):
            rows.append(
                swing(
                    "2026-05-01",
                    100,
                    value,
                    at_bat_number=i + 1,
                )
            )
        got = extract_contact_state(frame(rows), 100, "2026-05-02")
        # linear percentile: position .9*(30-1)=26.1 -> 27 + .1*(28-27)=27.1
        self.assertAlmostEqual(got["features"]["bat_speed_p90"], 27.1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

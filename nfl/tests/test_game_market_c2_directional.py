#!/usr/bin/env python3
import unittest

from nfl.research.game_market_c2_directional import (
    GameMarketC2DirectionalError,
    build_market_spread_index,
    compute_equal_volume_directional_accuracy,
)
from nfl.research.game_market_b0 import REQUIRED_CLOSING_CONTROL


def _market_row(game_id, spread_line):
    return {"game_id": game_id, "spread_line": spread_line, **REQUIRED_CLOSING_CONTROL}


def _paired(game_id, season, actual_margin, b0_margin, c2_margin):
    return {
        "game_id": game_id, "season": season, "actual_margin": actual_margin,
        "b0_margin": b0_margin, "b0_total": 0.0, "c2_margin": c2_margin, "c2_total": 0.0,
    }


class GameMarketC2DirectionalIndexTests(unittest.TestCase):
    def test_build_index_rejects_wrong_control_contract(self):
        bad_row = {"game_id": "g1", "spread_line": 3.0, "allowed_use": "WRONG"}
        with self.assertRaisesRegex(GameMarketC2DirectionalError, "closing control"):
            build_market_spread_index([bad_row])

    def test_build_index_rejects_duplicate_game_id(self):
        rows = [_market_row("g1", 3.0), _market_row("g1", 3.5)]
        with self.assertRaisesRegex(GameMarketC2DirectionalError, "duplicate"):
            build_market_spread_index(rows)


class GameMarketC2DirectionalAccuracyTests(unittest.TestCase):
    def test_agreement_games_are_excluded_from_disagreement_set(self):
        # Both models pick HOME (predicted margin > spread_line); no disagreement.
        paired = [_paired("g1", 2024, actual_margin=10.0, b0_margin=5.0, c2_margin=6.0)]
        market_index = build_market_spread_index([_market_row("g1", 2.0)])
        result = compute_equal_volume_directional_accuracy(paired, market_index)
        self.assertEqual(result["disagreement_games"], 0)
        self.assertEqual(result["market_matched_games"], 1)

    def test_push_actual_is_excluded(self):
        paired = [_paired("g1", 2024, actual_margin=2.0, b0_margin=5.0, c2_margin=-1.0)]
        market_index = build_market_spread_index([_market_row("g1", 2.0)])
        result = compute_equal_volume_directional_accuracy(paired, market_index)
        self.assertEqual(result["market_matched_games"], 0)

    def test_equal_volume_ranking_uses_each_models_own_edge(self):
        # Two disagreement games. B0 picks HOME both times (right once, wrong once);
        # C2 picks AWAY both times (wrong once, right once). At 50% volume each
        # model's own top-edge pick determines the reported hit rate.
        paired = [
            _paired("g1", 2024, actual_margin=10.0, b0_margin=5.0, c2_margin=-1.0),  # HOME covers; B0 right, C2 wrong
            _paired("g2", 2024, actual_margin=-10.0, b0_margin=1.0, c2_margin=-5.0),  # AWAY covers; B0 wrong, C2 right
        ]
        market_index = build_market_spread_index([_market_row("g1", 0.0), _market_row("g2", 0.0)])
        result = compute_equal_volume_directional_accuracy(
            paired, market_index, volume_fractions=(0.5, 1.0)
        )
        self.assertEqual(result["disagreement_games"], 2)
        by_fraction = {row["volume_fraction"]: row for row in result["by_volume_fraction"]}
        # g1 has the larger |margin - spread| for both b0 (5 vs 1) and c2 (5 vs 5 tie broken by ordering)
        self.assertEqual(by_fraction[1.0]["games"], 2)
        self.assertEqual(by_fraction[1.0]["b0_hit_rate"], 0.5)
        self.assertEqual(by_fraction[1.0]["c2_hit_rate"], 0.5)

    def test_no_disagreement_games_reports_none_hit_rates(self):
        paired = [_paired("g1", 2024, actual_margin=10.0, b0_margin=5.0, c2_margin=6.0)]
        market_index = build_market_spread_index([_market_row("g1", 2.0)])
        result = compute_equal_volume_directional_accuracy(paired, market_index, volume_fractions=(1.0,))
        self.assertIsNone(result["by_volume_fraction"][0]["b0_hit_rate"])
        self.assertIsNone(result["by_volume_fraction"][0]["c2_hit_rate"])


if __name__ == "__main__":
    unittest.main()

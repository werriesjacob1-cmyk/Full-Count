#!/usr/bin/env python3
import unittest
from unittest.mock import patch

from nfl.research import game_market_c1_research as research


class GameMarketC1ResearchTests(unittest.TestCase):
    def test_runner_uses_scoring_outcomes_not_market_subset(self):
        scoring = [
            {
                "game_id": "g1", "season": 2019, "week": 5, "game_type": "REG",
                "home_team": "DEN", "away_team": "KC", "final_status": "FINAL",
                "home_score": 24, "away_score": 20,
            }
        ]
        predictions = [
            {
                "game_id": "g1", "season": 2019, "week": 5, "game_type": "REG",
                "home_team": "DEN", "away_team": "KC", "eligibility": "ELIGIBLE",
                "target_final_status": "FINAL", "predicted_home_margin": 0.0,
                "predicted_total": 40.0, "uses_market_line_as_feature": False,
                "uses_current_game_outcome_as_feature": False,
            }
        ]
        with (
            patch.object(
                research,
                "load_pinned_historical_rows",
                return_value=(scoring, [], {"historical_reg_final_rows": 1}),
            ),
            patch.object(research, "build_prior_scoring_features", return_value=[]),
            patch.object(research, "build_b0_predictions", return_value=predictions),
            patch.object(
                research,
                "evaluate_c1",
                return_value={
                    "corrections": {
                        "development_games": 1,
                        "margin_additive_correction": 4.0,
                        "total_additive_correction": 4.0,
                    },
                    "partitions": {"held_2023_2025": {}},
                },
            ),
        ):
            report = research.run_research("unused.csv")

        self.assertTrue(report["outcome_population_is_market_independent"])
        self.assertEqual(report["status"], "RESEARCH_CHALLENGER_REJECTED")
        self.assertFalse(report["decision"]["promotion_eligible"])


if __name__ == "__main__":
    unittest.main()

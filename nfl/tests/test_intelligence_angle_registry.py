import json
import unittest
from copy import deepcopy
from pathlib import Path

from nfl.intelligence.angle_registry import (
    NFLIntelligenceRegistryError,
    validate_angle_registry,
)

REGISTRY = Path("data/nfl_intelligence/angle_registry.json")

REQUIRED_ANGLE_IDS = {
    "QB-OPPORTUNITY",
    "QB-PRESSURE-RESPONSE",
    "QB-COVERAGE-RESPONSE",
    "QB-CONTINUITY",
    "WR-ROUTE-OPPORTUNITY",
    "WR-COVERAGE",
    "RB-RUN-SCHEME",
    "OL-CONTINUITY",
    "OL-AVAILABILITY",
    "DEF-PRESSURE",
    "DEF-COVERAGE",
    "COACH-REGIME",
    "COACH-PROE",
    "COACH-PACE",
    "PERSONNEL-GROUP",
    "FORMATION",
    "MOTION",
    "PRE-SNAP-DISGUISE",
    "GAME-STATE",
    "GARBAGE-TIME",
    "INJURY-PROGRESSION",
    "OFFICIAL-INACTIVES",
    "REPLACEMENT-GRAPH",
    "WEATHER",
    "TRAVEL-CIRCADIAN",
    "OFFICIALS",
    "SPECIAL-TEAMS",
    "TURNOVER-PROCESS",
    "EXPLOSIVE-PROCESS",
    "DRIVE-EFFICIENCY",
    "MARKET-MOVEMENT",
    "CROSS-BOOK",
    "CROSS-MARKET",
    "CORRELATION",
    "ROOKIE-PRIORS",
    "AGE-WORKLOAD",
    "TEAM-CONTINUITY",
    "SCHEME-FAMILIARITY",
    "ADAPTATION-SELF-SCOUT",
    "WITHIN-GAME-FATIGUE",
    "FIELD-POSITION",
    "EXPECTED-TARGET-CATCH-YAC",
    "EXPECTED-PRESSURE-SACK",
    "EXPECTED-RUSHING",
    "FILM-STRUCTURED",
    "FILM-VISION",
    "NARRATIVE",
}


def load_registry():
    with REGISTRY.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class NFLIntelligenceRegistryTests(unittest.TestCase):
    def test_seed_registry_is_complete_and_valid(self):
        payload = load_registry()
        summary = validate_angle_registry(payload)
        self.assertGreaterEqual(summary["angle_count"], 67)
        self.assertTrue(REQUIRED_ANGLE_IDS.issubset(set(summary["angle_ids"])))
        self.assertGreaterEqual(summary["category_count"], 20)

    def test_multi_year_doctrine_cannot_be_removed(self):
        payload = load_registry()
        payload["multi_year_doctrine"]["required"] = False
        with self.assertRaisesRegex(NFLIntelligenceRegistryError, "multi_year"):
            validate_angle_registry(payload)

    def test_duplicate_angle_id_fails_closed(self):
        payload = load_registry()
        payload["angles"].append(deepcopy(payload["angles"][0]))
        with self.assertRaisesRegex(NFLIntelligenceRegistryError, "duplicate angle_id"):
            validate_angle_registry(payload)

    def test_pit_requirement_cannot_be_relaxed(self):
        payload = load_registry()
        payload["angles"][0]["point_in_time_required"] = False
        with self.assertRaisesRegex(NFLIntelligenceRegistryError, "PIT safety"):
            validate_angle_registry(payload)

    def test_historical_depth_target_cannot_silently_shrink(self):
        payload = load_registry()
        payload["angles"][0]["historical_depth_target"] = "LAST_5_ONLY"
        with self.assertRaisesRegex(NFLIntelligenceRegistryError, "historical depth target drift"):
            validate_angle_registry(payload)

    def test_post_selection_holdout_reuse_control_is_required(self):
        payload = load_registry()
        payload["angles"][0]["leakage_risks"].remove("post_selection_holdout_reuse")
        with self.assertRaisesRegex(NFLIntelligenceRegistryError, "leakage controls incomplete"):
            validate_angle_registry(payload)

    def test_prospective_confirmation_gate_is_required(self):
        payload = load_registry()
        payload["angles"][0]["validation_requirements"].remove(
            "prospective_confirmation_before_promotion"
        )
        with self.assertRaisesRegex(NFLIntelligenceRegistryError, "validation gates incomplete"):
            validate_angle_registry(payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)

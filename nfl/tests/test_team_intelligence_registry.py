import json
import unittest
from pathlib import Path

from nfl.intelligence.team_intelligence_registry import (
    EXPECTED_TEAMS,
    REQUIRED_CHANNELS,
    TeamIntelligenceRegistryError,
    validate_team_intelligence_registry,
)

REGISTRY = Path("data/nfl_intelligence/team_intelligence_registry.json")


def load_registry():
    with REGISTRY.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class TeamIntelligenceRegistryTests(unittest.TestCase):
    def test_all_32_teams_are_explicit(self):
        payload = load_registry()
        summary = validate_team_intelligence_registry(payload)
        self.assertEqual(summary["team_count"], 32)
        self.assertEqual({r["team"] for r in payload["teams"]}, EXPECTED_TEAMS)

    def test_all_required_channels_are_explicit(self):
        payload = load_registry()
        self.assertEqual(set(payload["required_channels"]), REQUIRED_CHANNELS)

    def test_missing_team_fails_closed(self):
        payload = load_registry()
        payload["teams"] = payload["teams"][:-1]
        with self.assertRaisesRegex(TeamIntelligenceRegistryError, "exactly 32 teams"):
            validate_team_intelligence_registry(payload)

    def test_unknown_channel_fails_closed(self):
        payload = load_registry()
        payload["teams"][0]["missing_channels"].append("RUMOR_MILL")
        with self.assertRaisesRegex(TeamIntelligenceRegistryError, "unknown missing channel"):
            validate_team_intelligence_registry(payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)

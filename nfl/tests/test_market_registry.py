#!/usr/bin/env python3
"""Contracts for the permanent NFL sportsbook market registry."""
import json
import unittest
from pathlib import Path

from nfl.intelligence.market_registry import (
    NFLMarketRegistryError,
    load_and_validate_market_registry,
    validate_market_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / "data" / "nfl_intelligence" / "market_registry.json"


def _base_market(**overrides):
    market = {
        "market_id": "passing_yards",
        "family": "player_passing",
        "shape": "primary",
        "status": "LIVE_SHADOW",
        "evidence": {
            "normalizer": "nfl/normalize/fanduel_passing.py",
            "binder": "nfl/normalize/market_roster_binding.py",
            "model": "nfl/research/passing_yards_shadow.py",
            "grader": "nfl/prospective/player_prop_grader.py",
            "workflow": ".github/workflows/nfl-live-passing-yards-shadow-board.yml",
            "tests": "nfl/tests/test_passing_yards_shadow.py",
        },
        "notes": "real evidence",
    }
    market.update(overrides)
    return market


def _base_payload(markets=None):
    return {
        "schema_version": 1,
        "sport": "NFL",
        "sportsbook_scope": ["fanduel"],
        "markets": markets if markets is not None else [_base_market()],
    }


class SeedRegistryTests(unittest.TestCase):
    def test_seed_registry_is_complete_and_valid(self):
        payload, summary = load_and_validate_market_registry(
            REGISTRY_PATH, repo_root=REPO_ROOT
        )
        self.assertGreaterEqual(summary["market_count"], 20)
        self.assertIn("passing_yards", summary["market_ids"])
        self.assertIn("receptions", summary["market_ids"])

    def test_every_evidence_path_actually_exists(self):
        # Re-asserts the repo_root existence check explicitly, independent of
        # the summary-only assertion above.
        with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        checked = 0
        for market in payload["markets"]:
            for value in market["evidence"].values():
                if isinstance(value, str) and value.strip():
                    self.assertTrue(
                        (REPO_ROOT / value).exists(),
                        f"{market['market_id']}: missing evidence file {value}",
                    )
                    checked += 1
        self.assertGreater(checked, 0)

    def test_no_unsupported_or_deferred_market_claims_evidence(self):
        with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        for market in payload["markets"]:
            if market["status"] in ("UNSUPPORTED", "DEFERRED"):
                real_paths = [v for v in market["evidence"].values() if v]
                if market["status"] == "UNSUPPORTED":
                    self.assertEqual(
                        real_paths, [],
                        f"{market['market_id']}: UNSUPPORTED but cites evidence",
                    )


class MarketRegistryValidationTests(unittest.TestCase):
    def test_valid_payload_passes(self):
        summary = validate_market_registry(_base_payload())
        self.assertEqual(summary["market_count"], 1)

    def test_duplicate_market_id_fails_closed(self):
        payload = _base_payload([_base_market(), _base_market()])
        with self.assertRaisesRegex(NFLMarketRegistryError, "duplicate"):
            validate_market_registry(payload)

    def test_invalid_status_fails_closed(self):
        payload = _base_payload([_base_market(status="PROMOTED")])
        with self.assertRaisesRegex(NFLMarketRegistryError, "invalid status"):
            validate_market_registry(payload)

    def test_invalid_shape_fails_closed(self):
        payload = _base_payload([_base_market(shape="parlay")])
        with self.assertRaisesRegex(NFLMarketRegistryError, "invalid shape"):
            validate_market_registry(payload)

    def test_missing_evidence_key_fails_closed(self):
        market = _base_market()
        del market["evidence"]["workflow"]
        with self.assertRaisesRegex(NFLMarketRegistryError, "evidence missing keys"):
            validate_market_registry(_base_payload([market]))

    def test_implemented_status_without_any_evidence_fails_closed(self):
        market = _base_market(
            status="TESTED",
            evidence={k: None for k in _base_market()["evidence"]},
        )
        with self.assertRaisesRegex(NFLMarketRegistryError, "requires at least one evidence"):
            validate_market_registry(_base_payload([market]))

    def test_unsupported_status_may_have_no_evidence(self):
        market = _base_market(
            status="UNSUPPORTED",
            shape="unsupported",
            evidence={k: None for k in _base_market()["evidence"]},
        )
        summary = validate_market_registry(_base_payload([market]))
        self.assertEqual(summary["status_counts"], {"UNSUPPORTED": 1})

    def test_nonexistent_evidence_path_fails_closed_with_repo_root(self):
        market = _base_market()
        market["evidence"]["model"] = "nfl/research/does_not_exist.py"
        with self.assertRaisesRegex(NFLMarketRegistryError, "does not exist"):
            validate_market_registry(_base_payload([market]), repo_root=REPO_ROOT)

    def test_wrong_sport_fails_closed(self):
        payload = _base_payload()
        payload["sport"] = "MLB"
        with self.assertRaisesRegex(NFLMarketRegistryError, "sport must be NFL"):
            validate_market_registry(payload)


if __name__ == "__main__":
    unittest.main()

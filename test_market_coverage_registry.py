#!/usr/bin/env python3
"""Contracts for the cross-sport market coverage registry."""
import copy
import unittest

from market_coverage.registry import (
    build_coverage_report,
    coverage_id,
    extract_fanduel_observations,
    new_registry,
    update_registry,
)


NOW = "2026-09-12T23:33:59Z"


def payload(*markets):
    return {"attachments": {"markets": {
        str(i): market for i, market in enumerate(markets, 1)
    }}}


def market(market_type, name, *, market_id="m1", event_id="e1", runners=2):
    return {
        "marketType": market_type,
        "marketName": name,
        "marketId": market_id,
        "eventId": event_id,
        "runners": [{} for _ in range(runners)],
    }


class ExtractionTests(unittest.TestCase):
    def test_unknown_market_is_retained_instead_of_filtered(self):
        rows = extract_fanduel_observations(
            payload(market("FUTURE_NEW_MARKET", "Something New")),
            sport="NFL", observed_at=NOW, source_artifact="event/one", tab="new-tab",
        )
        self.assertEqual([r["source_market_type"] for r in rows], ["FUTURE_NEW_MARKET"])
        self.assertEqual(rows[0]["market_instances"], 1)
        self.assertEqual(rows[0]["runner_instances"], 2)
        self.assertEqual(rows[0]["tabs"], ["new-tab"])

    def test_family_instances_are_aggregated_without_losing_names(self):
        rows = extract_fanduel_observations(
            payload(
                market("PLAYER_X_ALT_PASSING_YARDS_HIGH", "A - Alt Passing Yds", market_id="a"),
                market("PLAYER_X_ALT_PASSING_YARDS_HIGH", "B - Alt Passing Yds", market_id="b"),
            ),
            sport="NFL", observed_at=NOW, source_artifact="event/one",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["market_instances"], 2)
        self.assertTrue(rows[0]["alternate"])
        self.assertEqual(len(rows[0]["source_market_names"]), 2)

    def test_missing_market_type_becomes_visible_identity_blocker(self):
        rows = extract_fanduel_observations(
            payload(market("", "Unidentified", market_id="bad")),
            sport="MLB", observed_at=NOW, source_artifact="root",
        )
        registry = update_registry(
            new_registry(generated_at=NOW), rows, generated_at=NOW
        )
        entry = registry["markets"][0]
        self.assertEqual(entry["source_market_type"], "__MISSING_MARKET_TYPE__")
        self.assertEqual(entry["lifecycle_status"], "BLOCKED_IDENTITY")
        self.assertIn("SOURCE_MARKET_TYPE_MISSING", entry["blockers"])

    def test_raw_payload_digest_is_preserved_and_validated(self):
        digest = "a" * 64
        rows = extract_fanduel_observations(
            payload(market("MONEY_LINE", "Moneyline")),
            sport="NFL", observed_at=NOW, source_artifact="popular.json",
            source_payload_sha256=digest,
        )
        registry = update_registry(
            new_registry(generated_at=NOW), rows, generated_at=NOW
        )
        self.assertEqual(registry["markets"][0]["source_payload_sha256s"], [digest])
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            extract_fanduel_observations(
                payload(market("MONEY_LINE", "Moneyline")),
                sport="NFL", observed_at=NOW, source_artifact="popular.json",
                source_payload_sha256="not-a-digest",
            )


class RegistryTests(unittest.TestCase):
    def test_new_market_defaults_to_discovered_without_model_claims(self):
        observations = extract_fanduel_observations(
            payload(market("MONEY_LINE", "Moneyline")),
            sport="NFL", observed_at=NOW, source_artifact="event/one",
        )
        registry = update_registry(
            new_registry(generated_at=NOW), observations, generated_at=NOW
        )
        entry = registry["markets"][0]
        self.assertEqual(entry["lifecycle_status"], "DISCOVERED")
        self.assertTrue(entry["capabilities"]["ingested"])
        self.assertFalse(entry["capabilities"]["normalized"])
        self.assertFalse(entry["capabilities"]["public_eligible"])

    def test_reobservation_preserves_explicit_status_and_first_seen(self):
        observations = extract_fanduel_observations(
            payload(market("PLAYER_X_PASSING_YARDS_HIGH", "A - Passing Yds")),
            sport="NFL", observed_at=NOW, source_artifact="one",
        )
        classification = {
            "PLAYER_X_PASSING_YARDS_HIGH": {
                "lifecycle_status": "PROSPECTIVE_SHADOW",
                "canonical_market": "passing_yards",
                "capabilities": {
                    "ingested": True, "normalized": True,
                    "historical_data_available": True, "model_research": True,
                    "historical_validated": True, "prospective_capture": True,
                },
                "blockers": ["GRADER_NOT_ACTIVE", "SELECTOR_NOT_VALIDATED"],
                "status_reason": "B0 prospective control is active",
            }
        }
        registry = update_registry(
            new_registry(generated_at=NOW), observations, generated_at=NOW,
            classifications=classification,
        )
        later = copy.deepcopy(observations)
        later[0]["observed_at"] = "2026-09-13T00:00:00Z"
        registry = update_registry(
            registry, later, generated_at="2026-09-13T00:00:01Z"
        )
        entry = registry["markets"][0]
        self.assertEqual(entry["first_seen_at"], NOW)
        self.assertEqual(entry["last_seen_at"], "2026-09-13T00:00:00Z")
        self.assertEqual(entry["observation_count"], 2)
        self.assertEqual(entry["lifecycle_status"], "PROSPECTIVE_SHADOW")
        self.assertEqual(entry["canonical_market"], "passing_yards")

    def test_invalid_classification_fails_closed(self):
        observations = extract_fanduel_observations(
            payload(market("MONEY_LINE", "Moneyline")),
            sport="NFL", observed_at=NOW, source_artifact="one",
        )
        with self.assertRaisesRegex(ValueError, "unknown lifecycle status"):
            update_registry(
                new_registry(generated_at=NOW), observations, generated_at=NOW,
                classifications={"MONEY_LINE": {
                    "lifecycle_status": "MAGICALLY_READY",
                    "status_reason": "bad",
                }},
            )

    def test_coverage_loss_requires_a_complete_capture(self):
        observations = extract_fanduel_observations(
            payload(market("MONEY_LINE", "Moneyline")),
            sport="NFL", observed_at=NOW, source_artifact="one",
        )
        registry = update_registry(
            new_registry(generated_at=NOW), observations, generated_at=NOW
        )
        cid = coverage_id("NFL", "FANDUEL", "MONEY_LINE")
        incomplete = build_coverage_report(
            registry, observed_coverage_ids=[], previous_observed_coverage_ids=[cid],
            capture_complete=False,
        )
        self.assertEqual(incomplete["unexplained_coverage_loss"], [])
        self.assertEqual(
            incomplete["coverage_loss_suppressed_due_to_incomplete_capture"], [cid]
        )
        complete = build_coverage_report(
            registry, observed_coverage_ids=[], previous_observed_coverage_ids=[cid],
            capture_complete=True,
        )
        self.assertEqual(complete["unexplained_coverage_loss"], [cid])


if __name__ == "__main__":
    unittest.main()

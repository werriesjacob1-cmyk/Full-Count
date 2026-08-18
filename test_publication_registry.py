#!/usr/bin/env python3
"""Publication/deployment and durable public-population regression tests."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest

from dashboard.live_state import canonical_prop_id
from dashboard.publication_registry import (
    build_publication_manifest,
    confirm_publication,
    default_registry,
    load_registry,
    publication_candidate,
    published_snapshots_for_date,
    validate_manifest,
    validate_registry,
)


T1 = "2026-08-17T17:00:00Z"
T2 = "2026-08-17T17:05:00+00:00"


def prop(status="top_pick", odds=-120, stat="hits"):
    row = {
        "identity_version": 2, "type": "batter", "game_pk": 1,
        "game_start": "2026-08-17T18:00:00Z", "player_id": 101,
        "combo_player_ids": None, "name": "Fixture", "team": "A",
        "matchup": "A @ B", "prop": f"1+ {stat}",
        "projection": {"stat": stat, "needs": 1}, "stat": stat,
        "market_side": "over", "recommendation_status": status,
        "status_reasons": ["fixture"], "hit_probability": .7,
        "market_odds": odds, "market_implied": .545, "market_edge": .155,
    }
    row["id"] = canonical_prop_id(row)
    return row


def payload(rows):
    return {
        "schema_version": 3, "identity_schema_version": 2,
        "date": "2026-08-17", "generated_at": T1, "odds_fetched_at": T1,
        "recommendation_metadata": {
            "model_version": "model-x", "recommendation_policy_version": "policy-x",
            "calibration_version": "cal-x", "feature_version": "features-x",
        },
        "props": rows,
    }


class PublicationRegistryTests(unittest.TestCase):
    def test_failed_deployment_does_not_create_exposure(self):
        registry = default_registry()
        manifest = build_publication_manifest(payload([prop()]), {"schema_version": 3, "props": {}},
                                              registry, "sha", T1)
        self.assertEqual(registry["entries"], {})
        self.assertEqual(len(manifest["candidates"]), 1)

    def test_deployment_at_or_after_first_pitch_cannot_confirm_exposure(self):
        registry = default_registry()
        manifest = build_publication_manifest(
            payload([prop()]), {"schema_version": 3, "props": {}},
            registry, "sha", T1,
        )
        with self.assertRaises(ValueError):
            confirm_publication(
                registry, manifest, "2026-08-17T18:00:00Z",
                {"source_commit": "sha"},
            )
        self.assertEqual(registry["entries"], {})

    def test_success_is_exactly_once_and_first_snapshot_is_immutable(self):
        registry = default_registry()
        first = build_publication_manifest(payload([prop(odds=-120)]), {"schema_version": 3, "props": {}},
                                           registry, "sha1", T1)
        confirm_publication(registry, first, T2, {"source_commit": "sha1", "run_id": "10"})
        pid = prop()["id"]
        saved = copy.deepcopy(registry["entries"][pid])
        second = build_publication_manifest(payload([prop(odds=-150)]), {"schema_version": 3, "props": {}},
                                            registry, "sha2", T2)
        confirm_publication(registry, second, "2026-08-17T17:10:00Z",
                            {"source_commit": "sha2", "run_id": "11"})
        self.assertEqual(registry["entries"][pid], saved)
        self.assertEqual(saved["snapshot"]["market_odds"], -120)

    def test_demoted_or_later_missing_pick_remains_public_population(self):
        registry = default_registry()
        manifest = build_publication_manifest(payload([prop()]), {"schema_version": 3, "props": {}},
                                              registry, "sha", T1)
        confirm_publication(registry, manifest, T2, {"source_commit": "sha"})
        self.assertEqual(len(published_snapshots_for_date(registry, "2026-08-17")), 1)
        self.assertEqual(build_publication_manifest(payload([]), {"schema_version": 3, "props": {}},
                                                   registry, "sha2", T2)["candidates"], [])
        self.assertEqual(len(published_snapshots_for_date(registry, "2026-08-17")), 1)

    def test_registry_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "registry.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{")
            with self.assertRaises(RuntimeError):
                load_registry(path)
        bad = default_registry()
        bad["entries"] = {"wrong": {"canonical_id": "other"}}
        with self.assertRaises(ValueError):
            validate_registry(bad)

    def test_new_public_top_pick_requires_structured_settlement_support(self):
        registry = default_registry()
        supported = build_publication_manifest(
            payload([prop(stat="hits")]), {"schema_version": 3, "props": {}},
            registry, "sha", T1,
        )
        self.assertEqual(len(supported["candidates"]), 1)
        for stat in ("singles", "doubles", "triples", "first_inning_run"):
            with self.subTest(stat=stat):
                unsupported = build_publication_manifest(
                    payload([prop(stat=stat)]), {"schema_version": 3, "props": {}},
                    registry, "sha", T1,
                )
                self.assertEqual(unsupported["candidates"], [])

        injected = build_publication_manifest(
            payload([prop(stat="hits")]), {"schema_version": 3, "props": {}},
            registry, "sha", T1,
        )
        injected["candidates"] = [
            publication_candidate(prop(stat="doubles"), payload([prop(stat="doubles")]))
        ]
        with self.assertRaises(ValueError):
            validate_manifest(injected)


if __name__ == "__main__":
    unittest.main(verbosity=2)

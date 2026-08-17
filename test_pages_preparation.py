#!/usr/bin/env python3
"""Prospective artifact staging and post-deploy publication confirmation."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from dashboard.confirm_publication import confirm
from dashboard.live_state import atomic_write_json
from dashboard.prepare_pages_artifact import normalize_payload, prepare
from dashboard.publication_registry import default_registry, load_registry, write_registry
from dashboard.verify_pages_artifact import verify


PREVIEW = {"abstractGameState": "Preview", "detailedState": "Scheduled", "codedGameState": "S"}
LIVE = {"abstractGameState": "Live", "detailedState": "In Progress", "codedGameState": "I"}


class PagesPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = os.path.join(self.tmp.name, "docs")
        self.stage = os.path.join(self.tmp.name, "stage")
        self.registry_path = os.path.join(self.tmp.name, "registry.json")
        os.makedirs(self.source)
        for name, content in {
            "index.html": '<script src="app.js"></script>', "app.css": "body{}",
            "app.js": 'async function x(){return fetchJSON("live.json");}\nfunction y(p){return [p.settlement_state,p.game_state];}',
        }.items():
            with open(os.path.join(self.source, name), "w", encoding="utf-8") as handle:
                handle.write(content)
        row = {
            "id": "1-101-hits-1", "type": "batter", "name": "Fixture", "team": "A",
            "matchup": "A @ B", "game_pk": 1, "game_start": "2026-08-17T18:00:00Z",
            "player_id": 101, "combo_player_ids": None,
            "projection": {"stat": "hits", "needs": 1, "value": 1}, "stat": "hits",
            "prop": "Over 0.5 Hits", "recommendation_status": "top_pick",
            "status_reasons": [], "hit_probability": .7, "market_odds": -120,
            "market_implied": .545, "market_edge": .155,
        }
        atomic_write_json(os.path.join(self.source, "data.json"), {
            "date": "2026-08-17", "generated_at": "2026-08-17T17:00:00Z",
            "odds_fetched_at": "2026-08-17T17:00:00Z", "recommendation_metadata": {},
            "props": [row], "summary": {},
        })
        atomic_write_json(os.path.join(self.source, "live.json"), {
            "prices_updated_at": "2026-08-17T17:01:00Z", "grades_updated_at": None,
            "props": {row["id"]: {"market_odds": -125}},
        })
        registry = default_registry()
        registry["migration"] = {
            "version": registry["rollout_version"], "completed_at": "2026-08-17T16:00:00Z",
            "source_artifact_id": "legacy-proof",
        }
        registry["updated_at"] = "2026-08-17T16:00:00Z"
        write_registry(self.registry_path, registry)

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, destination=None, **kwargs):
        with mock.patch(
            "dashboard.prepare_pages_artifact.fetch_prior_deployment_recovery",
            return_value=None,
        ):
            return prepare(
                self.source, destination or self.stage,
                registry_path=self.registry_path,
                source_commit="abc", prepared_at="2026-08-17T17:05:00Z",
                contexts={1: {"status": PREVIEW, "feed": {}}}, **kwargs,
            )

    def test_failed_deploy_is_not_exposure_then_success_is_once(self):
        manifest = self.prepare()
        result = verify(self.stage)
        self.assertEqual(result["publication_candidates"], 1)
        with open(os.path.join(self.stage, "data.json"), encoding="utf-8") as handle:
            staged = json.load(handle)
        self.assertTrue(staged["props"][0]["publication_candidate_token"])
        self.assertEqual(load_registry(self.registry_path)["entries"], {})

        manifest_path = os.path.join(self.stage, "publication_manifest.json")
        changed, registry = confirm(
            manifest_path, self.registry_path, "2026-08-17T17:06:00Z",
            {"source_commit": "abc", "run_id": "10", "deployment_id": "20"},
        )
        self.assertTrue(changed)
        self.assertEqual(len(registry["entries"]), 1)
        saved = next(iter(registry["entries"].values()))
        self.assertEqual(saved["snapshot"]["market_odds"], -125)
        changed_again, registry_again = confirm(
            manifest_path, self.registry_path, "2026-08-17T17:10:00Z", {"run_id": "11"},
        )
        self.assertFalse(changed_again)
        self.assertEqual(next(iter(registry_again["entries"].values()))["first_published_at"],
                         "2026-08-17T17:06:00Z")

    def test_corrupt_source_fails_without_replacing_it(self):
        data_path = os.path.join(self.source, "data.json")
        with open(data_path, "w", encoding="utf-8") as handle:
            handle.write("{")
        with self.assertRaises(Exception):
            prepare(
                self.source, self.stage, registry_path=self.registry_path,
                source_commit="abc", prepared_at="2026-08-17T17:05:00Z", contexts={},
            )
        with open(data_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "{")

    def test_naive_timestamp_is_accepted_only_by_bounded_legacy_migration(self):
        with open(os.path.join(self.source, "data.json"), encoding="utf-8") as handle:
            legacy = json.load(handle)
        legacy["generated_at"] = "2026-08-17T17:00:00"
        migrated, _ = normalize_payload(legacy)
        self.assertEqual(migrated["generated_at"], "2026-08-17T17:00:00+00:00")
        current = dict(legacy)
        current["schema_version"] = 3
        with self.assertRaises(ValueError):
            normalize_payload(current)
        valid_current = migrated
        valid_current["props"][0]["id"] = "fc2:claimed-wrong"
        with self.assertRaises(ValueError):
            normalize_payload(valid_current)

    def test_near_start_unproven_top_pick_is_not_staged_for_public_exposure(self):
        near_stage = os.path.join(self.tmp.name, "near-stage")
        with mock.patch(
            "dashboard.prepare_pages_artifact.fetch_prior_deployment_recovery",
            return_value=None,
        ):
            manifest = prepare(
                self.source, near_stage, registry_path=self.registry_path,
                source_commit="abc", prepared_at="2026-08-17T17:50:00Z",
                contexts={1: {"status": PREVIEW, "feed": {}}},
            )
        self.assertEqual(manifest["candidates"], [])
        with open(os.path.join(near_stage, "data.json"), encoding="utf-8") as handle:
            staged = json.load(handle)
        self.assertEqual(staged["props"], [])
        self.assertEqual(staged["summary"]["n_top_pick"], 0)
        with open(os.path.join(near_stage, "live.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["props"], {})
        self.assertEqual(verify(near_stage)["props"], 0)

    def test_deployed_manifest_recovers_failed_registry_persistence(self):
        first_manifest = self.prepare()
        # Pages deploy succeeded, but confirm_publication/push did not. The
        # registry is therefore still empty while this manifest is public.
        self.assertEqual(load_registry(self.registry_path)["entries"], {})
        second_stage = os.path.join(self.tmp.name, "stage-two")
        deployed_modified = "Mon, 17 Aug 2026 17:06:00 GMT"
        with mock.patch(
            "dashboard.prepare_pages_artifact._http_optional_json",
            return_value=(first_manifest, b"manifest", deployed_modified),
        ):
            recovered_manifest = prepare(
                self.source, second_stage, registry_path=self.registry_path,
                source_commit="def", prepared_at="2026-08-17T18:10:00Z",
                contexts={1: {"status": LIVE, "feed": {}}},
            )
        batches = recovered_manifest["prior_deployment_recovery"]["batches"]
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]["candidates"]), 1)
        with open(os.path.join(second_stage, "data.json"), encoding="utf-8") as handle:
            recovered_data = json.load(handle)
        self.assertEqual(len(recovered_data["props"]), 1)
        self.assertEqual(recovered_data["props"][0]["game_state"], "live")
        self.assertEqual(recovered_data["props"][0]["publication_artifact_id"],
                         first_manifest["artifact_id"])

        changed, registry = confirm(
            os.path.join(second_stage, "publication_manifest.json"),
            self.registry_path, "2026-08-17T18:11:00Z",
            {"source_commit": "def", "run_id": "12", "deployment_id": "22"},
        )
        self.assertTrue(changed)
        entry = next(iter(registry["entries"].values()))
        self.assertEqual(entry["first_published_at"], "2026-08-17T17:06:00+00:00")
        self.assertEqual(entry["publication_provenance"]["artifact_id"],
                         first_manifest["artifact_id"])
        changed_again, registry_again = confirm(
            os.path.join(second_stage, "publication_manifest.json"),
            self.registry_path, "2026-08-17T18:12:00Z", {"run_id": "13"},
        )
        self.assertFalse(changed_again)
        self.assertEqual(len(registry_again["entries"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

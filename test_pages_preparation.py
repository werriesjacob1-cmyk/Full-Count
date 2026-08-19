#!/usr/bin/env python3
"""Prospective artifact staging and post-deploy publication confirmation."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from dashboard.confirm_publication import confirm
from dashboard.live_state import atomic_write_json, canonical_prop_id
from dashboard.prepare_pages_artifact import normalize_live, normalize_payload, prepare
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
        self.ledger_path = os.path.join(self.tmp.name, "ledger.jsonl")
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
            ledger_path=self.ledger_path,
        )
        self.assertTrue(changed)
        self.assertEqual(len(registry["entries"]), 1)
        saved = next(iter(registry["entries"].values()))
        self.assertEqual(saved["snapshot"]["market_odds"], -125)
        changed_again, registry_again = confirm(
            manifest_path, self.registry_path, "2026-08-17T17:10:00Z", {"run_id": "11"},
            ledger_path=self.ledger_path,
        )
        self.assertFalse(changed_again)
        self.assertEqual(next(iter(registry_again["entries"].values()))["first_published_at"],
                         "2026-08-17T17:06:00Z")
        # Regression guard (2026-08-19): confirm()'s ledger append landed in the
        # very same PR that also merged data/prediction_ledger/events.jsonl as a
        # real, git-tracked production file. Every confirm() call in this suite
        # MUST isolate ledger_path -- a call that silently fell through to
        # DEFAULT_LEDGER_PATH corrupted the real production ledger with a test
        # fixture event exactly once, caught only by manual post-merge
        # inspection, not by any test. This proves it can't happen again.
        self.assertGreater(os.path.getsize(self.ledger_path), 0)
        from dashboard.prediction_ledger import DEFAULT_LEDGER_PATH
        if os.path.exists(DEFAULT_LEDGER_PATH):
            with open(DEFAULT_LEDGER_PATH, encoding="utf-8") as handle:
                for line in handle:
                    self.assertNotIn('"fc2:1:player-101:hits:1:over"', line)

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

    def test_unsupported_settlement_market_cannot_first_publish_as_top_pick(self):
        with open(os.path.join(self.source, "data.json"), encoding="utf-8") as handle:
            source = json.load(handle)
        source["props"][0]["projection"] = {"stat": "doubles", "needs": 1, "value": 1}
        source["props"][0]["stat"] = "doubles"
        source["props"][0]["prop"] = "1+ Double"
        atomic_write_json(os.path.join(self.source, "data.json"), source)

        unsupported_stage = os.path.join(self.tmp.name, "unsupported-stage")
        manifest = self.prepare(destination=unsupported_stage)
        self.assertEqual(manifest["candidates"], [])
        with open(os.path.join(unsupported_stage, "data.json"), encoding="utf-8") as handle:
            staged = json.load(handle)
        self.assertEqual(staged["props"], [])
        self.assertEqual(verify(unsupported_stage)["publication_candidates"], 0)

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
            ledger_path=self.ledger_path,
        )
        self.assertTrue(changed)
        entry = next(iter(registry["entries"].values()))
        self.assertEqual(entry["first_published_at"], "2026-08-17T17:06:00+00:00")
        self.assertEqual(entry["publication_provenance"]["artifact_id"],
                         first_manifest["artifact_id"])
        changed_again, registry_again = confirm(
            os.path.join(second_stage, "publication_manifest.json"),
            self.registry_path, "2026-08-17T18:12:00Z", {"run_id": "13"},
            ledger_path=self.ledger_path,
        )
        self.assertFalse(changed_again)
        self.assertEqual(len(registry_again["entries"]), 1)


    # ── 2026-08-18 Pre-Phase-V production incident regression coverage ──
    # normalize_live() raised unconditionally on ANY legacy live-state id it
    # could not map onto today's board, bricking dashboard-live.yml (100% of
    # scheduled runs), dashboard-refresh.yml's commit step, and
    # dashboard-deploy.yml's staging step -- see the append-only
    # engineering/ENGINEERING_HANDOFF.md entry documenting the incident and
    # engineering/AUDIT/live-lifecycle-2026-08-17.md's updated disposition.
    # Every case below exercises the real prepare() staging path
    # (dashboard-deploy.yml's own entry point), not just normalize_live() in
    # isolation, since that is the exact function call chain that crashed.

    def test_stale_orphan_legacy_delta_no_longer_bricks_preparation(self):
        # The literal id/content shape from the real incident: a legacy
        # (pre-identity-schema-v2) id for a game/prop no longer on any
        # current board, carrying only PRICE_FIELDS -- fully reproducible,
        # non-durable content.
        incident_id = "824077-686930-strikeouts-4"
        with open(os.path.join(self.source, "live.json"), encoding="utf-8") as handle:
            live = json.load(handle)
        live["props"][incident_id] = {
            "market_odds": 112, "market_implied": 0.4456, "market_edge": 0.1654,
            "price_clears": True, "market_hold": 0.0585,
            "recommendation_status": "lean",
            "status_reasons": ["reliability grade D is too thin a sample"],
            "stale": False,
        }
        atomic_write_json(os.path.join(self.source, "live.json"), live)

        manifest = self.prepare()  # must not raise
        self.assertEqual(verify(self.stage)["props"], 1)
        with open(os.path.join(self.stage, "live.json"), encoding="utf-8") as handle:
            staged_live = json.load(handle)
        self.assertNotIn(incident_id, staged_live["props"],
                         "the stale non-durable orphan must be pruned, not carried forward")

    def test_mappable_legacy_id_still_migrates_and_preserves_its_fields(self):
        # The default fixture's live.json delta (market_odds=-125) keys off
        # the SAME legacy id as the one row on today's board -- this must
        # still migrate onto the row's real canonical id with its price
        # field intact, not be treated as an orphan.
        with open(os.path.join(self.source, "data.json"), encoding="utf-8") as handle:
            data = json.load(handle)
        canonical_id = canonical_prop_id(data["props"][0])
        self.prepare()
        with open(os.path.join(self.stage, "live.json"), encoding="utf-8") as handle:
            staged_live = json.load(handle)
        self.assertIn(canonical_id, staged_live["props"])
        self.assertEqual(staged_live["props"][canonical_id]["market_odds"], -125)

    def test_orphan_carrying_settlement_state_fails_closed_not_pruned(self):
        # An unmappable legacy id whose delta records a real settlement fact
        # (a wager outcome) must never be silently discarded -- this bounded
        # migration cannot prove that fact is durably recorded anywhere else.
        with open(os.path.join(self.source, "live.json"), encoding="utf-8") as handle:
            live = json.load(handle)
        live["props"]["824077-686930-strikeouts-4"] = {
            "settlement_state": "hit", "settlement_authority": "live_observation",
            "settlement_observed_at": "2026-08-17T20:00:00Z",
            "settlement_source": "legacy_schema_v2", "result_actual": 5,
        }
        atomic_write_json(os.path.join(self.source, "live.json"), live)
        with self.assertRaises(ValueError) as ctx:
            self.prepare()
        self.assertIn("durable settlement/publication state", str(ctx.exception))

    def test_orphan_carrying_publication_marker_fails_closed_not_pruned(self):
        # Same guarantee for a real public-exposure fact rather than a
        # settlement fact -- proof this repository ever showed this wager
        # to a real visitor as an official Top Pick.
        with open(os.path.join(self.source, "live.json"), encoding="utf-8") as handle:
            live = json.load(handle)
        live["props"]["824077-686930-strikeouts-4"] = {
            "published_top_pick_at": "2026-08-17T15:00:00Z",
            "publication_artifact_id": "a" * 64,
        }
        atomic_write_json(os.path.join(self.source, "live.json"), live)
        with self.assertRaises(ValueError) as ctx:
            self.prepare()
        self.assertIn("durable settlement/publication state", str(ctx.exception))

    def test_current_schema_orphan_fails_closed_even_without_durable_content(self):
        # Preserve the OTHER fail-closed invariant this correction must not
        # weaken: a document that already CLAIMS the current schema (not a
        # legacy migration input at all) containing a non-canonical id is
        # corruption, not a stale legacy artifact -- it must still fail
        # closed even though its content alone looks prunable.
        current_schema_live = {
            "schema_version": 3, "identity_schema_version": 2,
            "updated_at": "2026-08-17T17:00:00Z", "prices_updated_at": None,
            "grades_updated_at": None,
            "props": {"824077-686930-strikeouts-4": {"market_odds": 112, "stale": False}},
        }
        with self.assertRaises(ValueError) as ctx:
            normalize_live(current_schema_live, {})
        self.assertIn("corruption", str(ctx.exception))

    def test_normalize_live_directly_reproduces_and_resolves_the_incident(self):
        # Fast, isolated confirmation at the exact function boundary that
        # crashed, using the literal id string from the real incident,
        # independent of the prepare()-level integration coverage above.
        incident_id = "824077-686930-strikeouts-4"
        legacy_live = {
            "props": {incident_id: {
                "market_odds": 112, "market_implied": 0.4456, "market_edge": 0.1654,
                "price_clears": True, "market_hold": 0.0585,
                "recommendation_status": "lean", "status_reasons": [], "stale": False,
            }},
        }
        result = normalize_live(legacy_live, id_map={})
        self.assertNotIn(incident_id, result["props"])
        self.assertEqual(result["schema_version"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Strict Pages artifact and workflow ownership contract tests."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest

import yaml

from dashboard.live_state import canonical_prop_id, state_digest
from dashboard.verify_pages_artifact import verify


ROOT = os.path.dirname(os.path.abspath(__file__))


def row():
    value = {
        "identity_version": 2, "type": "batter", "game_pk": 1,
        "game_start": "2026-08-17T18:00:00Z", "player_id": 101,
        "combo_player_ids": None, "projection": {"stat": "hits", "needs": 1},
        "stat": "hits", "market_side": "over", "recommendation_status": "neutral",
        "game_state": "pregame", "game_state_observed_at": "2026-08-17T17:00:00Z",
        "game_state_source": "fixture", "settlement_state": "open",
        "settlement_authority": "none", "settlement_observed_at": "2026-08-17T17:00:00Z",
        "settlement_source": "fixture",
    }
    value["id"] = canonical_prop_id(value)
    return value


def artifact(root, rows=None, live=None):
    rows = [row()] if rows is None else rows
    live = live or {
        "schema_version": 3, "identity_schema_version": 2,
        "updated_at": "2026-08-17T17:00:00Z", "prices_updated_at": None,
        "grades_updated_at": None, "props": {},
    }
    files = {
        "index.html": '<script src="app.js"></script>',
        "app.css": "body{}",
        "app.js": 'async function pollLive(){return fetchJSON("live.json");}\nfunction x(p){return [p.settlement_state,p.game_state];}',
    }
    for name, content in files.items():
        with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
            handle.write(content)
    data = {
            "schema_version": 3, "identity_schema_version": 2,
            "generated_at": "2026-08-17T17:00:00Z",
            "lifecycle_prepared_at": "2026-08-17T17:00:00Z", "props": rows,
        }
    with open(os.path.join(root, "data.json"), "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    with open(os.path.join(root, "live.json"), "w", encoding="utf-8") as handle:
        json.dump(live, handle)
    with open(os.path.join(root, "publication_manifest.json"), "w", encoding="utf-8") as handle:
        json.dump({
            "schema_version": 1, "artifact_id": "a" * 64,
            "prepared_at": "2026-08-17T17:00:00Z", "source_commit": "sha",
            "publication_cutoff_at": "2026-08-17T17:15:00Z",
            "files": {"data.json": state_digest(data), "live.json": state_digest(live)},
            "candidates": [], "known_public_ids": [], "known_publications": {},
        }, handle)


class PagesContractTests(unittest.TestCase):
    def test_valid_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            artifact(root)
            self.assertEqual(verify(root)["props"], 1)

    def test_verifier_runs_from_the_workflow_cli_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            artifact(root)
            result = subprocess.run(
                [sys.executable, os.path.join(ROOT, "dashboard", "verify_pages_artifact.py"), root],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Pages artifact verified", result.stdout)

    def test_duplicate_and_impossible_state_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            one = row()
            artifact(root, [one, copy.deepcopy(one)])
            with self.assertRaises(ValueError):
                verify(root)
        with tempfile.TemporaryDirectory() as root:
            one = row()
            one.update({
                "settlement_state": "provisional_hit",
                "settlement_authority": "live_observation",
                "settlement_observed_at": "2026-08-17T17:01:00Z",
                "settlement_source": "impossible", "result_actual": 1,
            })
            artifact(root, [one])
            with self.assertRaises(ValueError):
                verify(root)
        with tempfile.TemporaryDirectory() as root:
            one = row()
            one.update({
                "game_state": "final", "settlement_state": "provisional_hit",
                "settlement_authority": "live_observation",
                "settlement_observed_at": "2026-08-17T20:00:00Z",
                "settlement_source": "mlb_live_feed", "result_actual": 1,
            })
            artifact(root, [one])
            with self.assertRaises(ValueError):
                verify(root)

    def test_invalid_timestamp_and_orphan_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            live = {
                "schema_version": 3, "identity_schema_version": 2,
                "updated_at": "2026-08-17T17:00:00", "props": {"orphan": {}},
            }
            artifact(root, live=live)
            with self.assertRaises(ValueError):
                verify(root)

    def test_publication_provenance_and_candidate_tokens_are_verified(self):
        with tempfile.TemporaryDirectory() as root:
            one = row()
            one["published_top_pick_at"] = "2026-08-17T16:00:00Z"
            one["publication_artifact_id"] = "b" * 64
            artifact(root, [one])
            with self.assertRaises(ValueError):
                verify(root)
        with tempfile.TemporaryDirectory() as root:
            one = row()
            one["publication_candidate_token"] = "not-a-digest"
            artifact(root, [one])
            with self.assertRaises(ValueError):
                verify(root)

    def test_unproven_or_unsupported_top_pick_cannot_enter_pages_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            one = row()
            one["recommendation_status"] = "top_pick"
            artifact(root, [one])
            with self.assertRaises(ValueError):
                verify(root)
        with tempfile.TemporaryDirectory() as root:
            one = row()
            one["projection"] = {"stat": "doubles", "needs": 1}
            one["stat"] = "doubles"
            one["recommendation_status"] = "top_pick"
            one["id"] = canonical_prop_id(one)
            artifact(root, [one])
            with self.assertRaises(ValueError):
                verify(root)

    def test_lower_authority_live_result_over_final_board_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            one = row()
            one.update({
                "game_state": "final", "settlement_state": "miss",
                "settlement_authority": "official_final",
                "settlement_observed_at": "2026-08-17T20:00:00Z",
                "settlement_source": "official", "result_actual": 0,
            })
            delta = {
                "settlement_state": "provisional_hit",
                "settlement_authority": "live_observation",
                "settlement_observed_at": "2026-08-17T19:00:00Z",
                "settlement_source": "live", "result_actual": 1,
                "_field_updated_at": {
                    field: "2026-08-17T19:00:00Z" for field in (
                        "settlement_state", "settlement_authority", "settlement_observed_at",
                        "settlement_source", "result_actual", "result_reason",
                    )
                },
            }
            live = {
                "schema_version": 3, "identity_schema_version": 2,
                "updated_at": "2026-08-17T19:00:00Z", "prices_updated_at": None,
                "grades_updated_at": "2026-08-17T19:00:00Z", "props": {one["id"]: delta},
            }
            artifact(root, [one], live)
            with self.assertRaises(ValueError):
                verify(root)

    def test_workflow_queue_and_coalescing_contract(self):
        def load(name):
            with open(os.path.join(ROOT, ".github/workflows", name), encoding="utf-8") as handle:
                return yaml.safe_load(handle)
        full = load("dashboard-refresh.yml")
        live = load("dashboard-live.yml")
        deploy = load("dashboard-deploy.yml")
        lineup = load("lineup-watch.yml")
        self.assertEqual(full["concurrency"]["queue"], "max")
        self.assertFalse(full["concurrency"]["cancel-in-progress"])
        self.assertNotIn("queue", live["concurrency"])
        self.assertFalse(live["concurrency"]["cancel-in-progress"])
        self.assertFalse(deploy["concurrency"]["cancel-in-progress"])
        self.assertEqual(deploy["jobs"]["deploy"]["timeout-minutes"], 10)
        self.assertFalse(lineup["concurrency"]["cancel-in-progress"])
        self.assertEqual(full["jobs"]["build-and-publish"]["steps"][0]["with"]["ref"], "main")
        self.assertEqual(live["jobs"]["update-live-state"]["steps"][0]["with"]["ref"], "main")
        self.assertEqual(deploy["jobs"]["deploy"]["steps"][0]["with"]["ref"], "main")
        workflow_dir = os.path.join(ROOT, ".github", "workflows")
        self.assertFalse(os.path.exists(os.path.join(workflow_dir, "dashboard-grades.yml")))
        self.assertFalse(os.path.exists(os.path.join(workflow_dir, "dashboard-prices.yml")))
        with open(os.path.join(workflow_dir, "dashboard-live.yml"), encoding="utf-8") as handle:
            live_source = handle.read()
        with open(os.path.join(workflow_dir, "dashboard-refresh.yml"), encoding="utf-8") as handle:
            full_source = handle.read()
        self.assertIn("merge_live_files.py", live_source)
        self.assertIn("git checkout --detach origin/main", live_source)
        self.assertIn("finalize_dashboard_state.py", full_source)
        self.assertIn("git checkout --detach origin/main", full_source)

        lineup_steps = lineup["jobs"]["check-and-trigger"]["steps"]
        dispatch_index = next(
            index for index, step in enumerate(lineup_steps)
            if step.get("name") == "Trigger a queued full dashboard rebuild"
        )
        commit_index = next(
            index for index, step in enumerate(lineup_steps)
            if step.get("name") == "Acknowledge lineup state after accepted rebuild"
        )
        dispatch = lineup_steps[dispatch_index]
        acknowledge = lineup_steps[commit_index]
        self.assertLess(dispatch_index, commit_index)
        self.assertEqual(dispatch.get("id"), "dispatch")
        self.assertNotIn("continue-on-error", dispatch)
        self.assertIn("steps.dispatch.outcome == 'success'", acknowledge.get("if", ""))
        self.assertIn("gh workflow run dashboard-refresh.yml --ref main", dispatch["run"])
        self.assertEqual(full["concurrency"]["queue"], "max")


if __name__ == "__main__":
    unittest.main(verbosity=2)

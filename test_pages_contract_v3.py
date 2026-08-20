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

    def test_picks_generation_triggers_dashboard_refresh(self):
        """Website-staleness fix, verified 2026-08-20: a fresh official picks
        generation (mlb-daily.yml) is a materially new board state, but
        nothing dispatched a rebuild for it before this fix -- reproduced
        live, "Picks 2026-08-20" landed 7 minutes after the prior Dashboard
        Refresh and then sat unpublished for over an hour with no other
        trigger able to catch it before the next fixed 2-hour cron window.
        mlb-daily.yml now dispatches dashboard-refresh.yml the same way
        lineup-watch.yml already does, but ONLY when a real commit happened
        (never on a "no picks to commit" no-op, which would otherwise fire
        this on every dry_run/skip_picks/no-op invocation)."""
        with open(os.path.join(ROOT, ".github", "workflows", "mlb-daily.yml"),
                 encoding="utf-8") as handle:
            daily = yaml.safe_load(handle)
        self.assertEqual(daily["permissions"].get("actions"), "write")
        steps = daily["jobs"]["run-pipeline"]["steps"]
        commit_index = next(
            index for index, step in enumerate(steps)
            if step.get("name") == "Commit picks immediately"
        )
        dispatch_index = next(
            index for index, step in enumerate(steps)
            if step.get("name") == "Trigger a queued full dashboard rebuild"
        )
        commit_step = steps[commit_index]
        dispatch_step = steps[dispatch_index]
        self.assertLess(commit_index, dispatch_index)
        self.assertEqual(commit_step.get("id"), "commit_picks")
        self.assertIn("committed=false", commit_step["run"])
        self.assertIn("committed=true", commit_step["run"])
        self.assertIn("steps.commit_picks.outputs.committed == 'true'", dispatch_step.get("if", ""))
        self.assertIn("gh workflow run dashboard-refresh.yml --ref main", dispatch_step["run"])

        with open(os.path.join(ROOT, ".github", "workflows", "odds-snapshot.yml"),
                 encoding="utf-8") as handle:
            odds = yaml.safe_load(handle)
        odds_steps = odds["jobs"]["snapshot"]["steps"]
        self.assertFalse(
            any("dashboard-refresh.yml" in (step.get("run") or "") for step in odds_steps),
            "odds-snapshot.yml runs hourly; build_dashboard.py fetches its own live "
            "FanDuel prices directly and never reads data/odds/*.json, so a committed "
            "odds snapshot is not an unpublished board input -- dispatching a rebuild "
            "here would risk the exact backlog dashboard-refresh.yml's queue:max "
            "concurrency exists to bound, for no freshness benefit.",
        )

    def test_finalize_dashboard_state_rejects_stale_candidate(self):
        """A queued/delayed full rebuild must never overwrite a newer board
        already published while it was running -- dashboard/
        finalize_dashboard_state.py's own stale-candidate guard, verified
        directly rather than assumed during the website-staleness fix."""
        from dashboard.finalize_dashboard_state import finalize
        with tempfile.TemporaryDirectory() as tmp:
            candidate_path = os.path.join(tmp, "candidate.json")
            current_path = os.path.join(tmp, "current.json")
            live_path = os.path.join(tmp, "live.json")
            out_path = os.path.join(tmp, "out.json")
            with open(candidate_path, "w", encoding="utf-8") as handle:
                json.dump({"date": "2026-08-20", "generated_at": "2026-08-20T10:00:00Z",
                          "props": []}, handle)
            with open(current_path, "w", encoding="utf-8") as handle:
                json.dump({"date": "2026-08-20", "generated_at": "2026-08-20T12:00:00Z",
                          "props": []}, handle)
            changed = finalize(candidate_path, current_path, live_path, out_path,
                              registry_path=os.path.join(tmp, "registry.json"), contexts={})
            self.assertFalse(changed)
            self.assertFalse(os.path.exists(out_path))

    def test_deploy_verifies_public_site_after_publish(self):
        """Real incident, 2026-08-20: main/docs/data.json was current, deploy-
        pages reported success, but nothing in the workflow had ever actually
        fetched the deployed PUBLIC URL to prove it served that content --
        "GitHub Actions is green" and "the public site is current" were
        silently treated as the same claim. dashboard-deploy.yml now polls
        the public publication_manifest.json + data.json after deploy-pages
        and fails the job if they never converge to what this run deployed.

        Also locks in a real bug caught by testing this against production
        BEFORE shipping it: the first draft compared sha256(raw response
        bytes) against publication_manifest.json's own files["data.json"]
        field -- which is dashboard/live_state.py's state_digest() (a hash
        of the NORMALIZED, canonically-reserialized dict), not a raw-bytes
        hash, so it would have mismatched on every real deploy. The shipped
        version compares source_commit and data.json's own generated_at
        field instead -- verified convergent against the real production
        site before merge."""
        with open(os.path.join(ROOT, ".github", "workflows", "dashboard-deploy.yml"),
                 encoding="utf-8") as handle:
            deploy = yaml.safe_load(handle)
        steps = deploy["jobs"]["deploy"]["steps"]
        deploy_index = next(
            index for index, step in enumerate(steps) if step.get("id") == "deployment"
        )
        verify_index = next(
            index for index, step in enumerate(steps)
            if step.get("name") == "Verify public site converges to deployed artifact"
        )
        confirm_index = next(
            index for index, step in enumerate(steps)
            if step.get("name") == "Confirm durable public exposure"
        )
        self.assertLess(deploy_index, verify_index)
        self.assertLess(verify_index, confirm_index)
        verify_step = steps[verify_index]
        run = verify_step["run"]
        # Must never repeat the raw-bytes-hash mistake, and must actually
        # compare the two real provenance fields.
        self.assertNotIn('hashlib.sha256(public_data_raw)', run)
        self.assertIn("source_commit", run)
        self.assertIn("generated_at", run)
        self.assertIn("publication_manifest.json", run)
        self.assertIn("sys.exit(1)", run)  # must fail loudly, not just warn
        self.assertIn("PAGES_DEPLOYMENT_URL", verify_step.get("env", {}))


if __name__ == "__main__":
    unittest.main(verbosity=2)

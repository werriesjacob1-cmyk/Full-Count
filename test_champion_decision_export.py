"""Adversarial contract tests for the inactive dashboard champion export."""
import copy
import json
import os
import tempfile
import unittest
from unittest import mock

from dashboard.champion_decision_export import (
    build_export, coverage_status, verify_export, write_once,
)
from dashboard.live_state import canonical_prop_id
from dashboard.prepare_pages_artifact import prepare
from dashboard.publication_registry import build_publication_manifest, default_registry

T = "2026-08-17T17:05:00Z"
START = "2026-08-17T18:00:00Z"
PREVIEW = {"abstractGameState": "Preview", "detailedState": "Scheduled", "codedGameState": "S"}


def row(player=101, status="top_pick", odds=-120, game=1):
    value = {"identity_version": 2, "type": "batter", "game_pk": game,
             "game_start": START, "player_id": player, "name": f"Player {player}",
             "team": "A", "matchup": "A @ B", "stat": "hits",
             "prop": "Over 0.5 Hits", "market_side": "over",
             "projection": {"stat": "hits", "needs": 1, "value": 0.5},
             "recommendation_status": status, "hit_probability": .7,
             "market_odds": odds, "status_reasons": ["test"]}
    value["id"] = canonical_prop_id(value)
    return value


def payload(rows):
    return {"schema_version": 3, "identity_schema_version": 2,
            "date": "2026-08-17", "generated_at": "2026-08-17T17:00:00Z",
            "odds_fetched_at": "2026-08-17T17:01:00Z",
            "recommendation_metadata": {"model_version": "test-model",
                                        "selection_policy_version": "test-policy"},
            "props": rows, "summary": {"n_props": len(rows)}}


def live():
    return {"schema_version": 3, "identity_schema_version": 2, "props": {}}


def specimen(rows=None):
    data = payload(rows if rows is not None else [row(), row(102, "lean")])
    overlay, registry = live(), default_registry()
    cutoff = "2026-08-17T17:20:00+00:00"
    pre = build_publication_manifest(data, overlay, registry, "test-sha", T,
                                     publication_cutoff_at=cutoff)
    final = copy.deepcopy(data)
    for value in final["props"]:
        if value["id"] in {c["canonical_id"] for c in pre["candidates"]}:
            value["publication_candidate_token"] = "test-token"
    manifest = build_publication_manifest(final, overlay, registry, "test-sha", T,
                                          publication_cutoff_at=cutoff)
    return data, overlay, registry, pre, final, manifest


class ExportTests(unittest.TestCase):
    def test_replay_exact_uncapped_dashboard_population_and_manifest(self):
        data, overlay, registry, pre, final, manifest = specimen([row(i) for i in range(101, 113)])
        record = build_export(data, overlay, registry, pre, final, overlay, manifest, run_id="42")
        self.assertEqual(record["counts"]["dashboard_top_picks"], 12)
        self.assertEqual(record["counts"]["new_publication_policy_N"], 12)
        self.assertEqual(record["decision_ids"]["new_publication_candidates"],
                         sorted(c["canonical_id"] for c in manifest["candidates"]))
        self.assertEqual(record["decision_ids"]["staged_manifest_candidates"],
                         record["decision_ids"]["new_publication_candidates"])
        self.assertEqual(record["decision_ids"]["withheld_after_preparation"], [])
        self.assertEqual(record["publication_status"], "INTENDED_NOT_EXPOSURE_PROVEN")
        self.assertTrue(verify_export(record, manifest, final, overlay))

    def test_withheld_intended_pick_is_not_claimed_as_staged_or_exposed(self):
        data, overlay, registry, pre, final, _ = specimen([row(101), row(102)])
        final["props"][1]["recommendation_status"] = "lean"
        final["props"][1].pop("publication_candidate_token")
        manifest = build_publication_manifest(
            final, overlay, registry, "test-sha", T,
            publication_cutoff_at="2026-08-17T17:20:00+00:00",
        )
        record = build_export(data, overlay, registry, pre, final, overlay, manifest)
        self.assertEqual(record["counts"]["new_publication_policy_N"], 2)
        self.assertEqual(record["counts"]["staged_publication_candidates"], 1)
        self.assertEqual(record["decision_ids"]["staged_manifest_candidates"],
                         [final["props"][0]["id"]])
        self.assertEqual(record["decision_ids"]["withheld_after_preparation"],
                         [final["props"][1]["id"]])
        self.assertEqual(record["publication_status"], "INTENDED_NOT_EXPOSURE_PROVEN")
        self.assertTrue(verify_export(record, manifest, final, overlay))

    def test_duplicate_identity_and_changed_line_fail(self):
        data, overlay, registry, pre, final, manifest = specimen()
        duplicate = copy.deepcopy(data)
        duplicate["props"].append(copy.deepcopy(duplicate["props"][0]))
        with self.assertRaises(ValueError):
            build_export(duplicate, overlay, registry, pre, final, overlay, manifest)
        changed = copy.deepcopy(data)
        changed["props"][0]["projection"]["needs"] = 2
        with self.assertRaises(ValueError):
            build_export(changed, overlay, registry, pre, final, overlay, manifest)

    def test_changed_price_and_manifest_fail_replay(self):
        data, overlay, registry, pre, final, manifest = specimen()
        record = build_export(data, overlay, registry, pre, final, overlay, manifest)
        changed = copy.deepcopy(record)
        changed["pre_data"]["props"][0]["market_odds"] = -190
        with self.assertRaises(ValueError):
            verify_export(changed, manifest, final, overlay)
        other = copy.deepcopy(manifest)
        other["artifact_id"] = "f" * 64
        with self.assertRaises(ValueError):
            verify_export(record, other, final, overlay)

    def test_missing_and_partial_games_cannot_be_zero_pick(self):
        self.assertEqual(coverage_status(None, [], 0, discovery_complete=True), "UNVERIFIED")
        self.assertEqual(coverage_status([1, 2], [], 0, discovery_complete=True), "MISSING")
        self.assertEqual(coverage_status([1, 2], [1], 0, discovery_complete=True), "PARTIAL")
        self.assertEqual(coverage_status([1], [1], 0, discovery_complete=False), "PARTIAL")
        self.assertEqual(coverage_status([1], [1], 0, discovery_complete=True), "ZERO_PICK")
        self.assertEqual(coverage_status([1], [1], 1, discovery_complete=True), "COMPLETE")

    def test_late_run_is_not_relabelled_as_eligible(self):
        data, overlay, registry, pre, final, manifest = specimen()
        late = copy.deepcopy(data)
        late["generated_at"] = "2026-08-17T18:01:00Z"
        with self.assertRaises(ValueError):
            build_export(late, overlay, registry, pre, final, overlay, manifest)

    def test_write_once_and_partial_write(self):
        data, overlay, registry, pre, final, manifest = specimen()
        record = build_export(data, overlay, registry, pre, final, overlay, manifest)
        with tempfile.TemporaryDirectory() as root:
            path = write_once(record, root)
            with open(path, "rb") as handle:
                first = handle.read()
            self.assertEqual(write_once(record, root), path)
            with open(path, "rb") as handle:
                self.assertEqual(first, handle.read())
            with open(path, "wb") as handle:
                handle.write(b"partial")
            with self.assertRaises(FileExistsError):
                write_once(record, root)

    def test_failed_atomic_publish_leaves_no_partial_final(self):
        data, overlay, registry, pre, final, manifest = specimen()
        record = build_export(data, overlay, registry, pre, final, overlay, manifest)
        with tempfile.TemporaryDirectory() as root:
            publish = "dashboard.champion_decision_export.os.rename" if os.name == "nt" else "dashboard.champion_decision_export.os.link"
            with mock.patch(publish, side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    write_once(record, root)
            self.assertEqual(os.listdir(root), [])

    def test_genuine_zero_decision_remains_distinct_from_missing_source(self):
        data, overlay, registry, pre, final, manifest = specimen([])
        record = build_export(data, overlay, registry, pre, final, overlay, manifest)
        self.assertEqual(record["counts"]["new_publication_policy_N"], 0)
        self.assertEqual(record["coverage_status"], "UNVERIFIED")
        self.assertTrue(verify_export(record, manifest, final, overlay))


class WiringTests(unittest.TestCase):
    def test_enabled_disabled_failed_export_leave_customer_bytes_identical(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source")
            os.mkdir(source)
            with open(os.path.join(source, "data.json"), "w", encoding="utf-8") as handle:
                json.dump(payload([row()]), handle)
            with open(os.path.join(source, "live.json"), "w", encoding="utf-8") as handle:
                json.dump(live(), handle)
            registry = default_registry()
            registry["migration"]["completed_at"] = "2026-08-17T16:00:00Z"
            registry_path = os.path.join(root, "registry.json")
            with open(registry_path, "w", encoding="utf-8") as handle:
                json.dump(registry, handle)
            outputs = []
            for index, mode in enumerate(("off", "on", "fail")):
                stage = os.path.join(root, f"stage-{index}")
                env = {} if mode == "off" else {"FC_ACC_009_EXPORT_DIR": os.path.join(root, "research", mode)}
                fail = mock.patch("dashboard.champion_decision_export.write_once", side_effect=OSError("disk full"))
                with mock.patch.dict(os.environ, env, clear=False), mock.patch(
                    "dashboard.prepare_pages_artifact.fetch_prior_deployment_recovery", return_value=None
                ):
                    if mode == "off":
                        os.environ.pop("FC_ACC_009_EXPORT_DIR", None)
                    if mode == "fail":
                        fail.start()
                    try:
                        prepare(source, stage, registry_path=registry_path, source_commit="test-sha",
                                prepared_at=T, contexts={1: {"status": PREVIEW, "feed": {}}})
                    finally:
                        if mode == "fail":
                            fail.stop()
                stage_bytes = {}
                for name in ("data.json", "live.json", "publication_manifest.json"):
                    with open(os.path.join(stage, name), "rb") as handle:
                        stage_bytes[name] = handle.read()
                outputs.append(stage_bytes)
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[0], outputs[2])
            export_path = os.path.join(root, "research", "on")
            self.assertEqual(len(os.listdir(export_path)), 1)
            with open(os.path.join(export_path, os.listdir(export_path)[0]), encoding="utf-8") as handle:
                record = json.load(handle)
            self.assertEqual(record["counts"]["new_publication_policy_N"], 1)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Immutable Prediction Ledger regression tests (publication events only --
see dashboard/prediction_ledger.py's module docstring for scope)."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest

from dashboard.live_state import canonical_prop_id
from dashboard.publication_registry import (
    build_publication_manifest, confirm_publication, default_registry,
)
from dashboard.prediction_ledger import (
    append_publication_event, backfill_from_registry, reconstruct_wager,
    verify_ledger_integrity,
)
from dashboard.confirm_publication import confirm as confirm_deploy


T1 = "2026-08-17T17:00:00Z"
T2 = "2026-08-17T17:05:00+00:00"


def prop(player_id=101, stat="hits", odds=-120):
    row = {
        "identity_version": 2, "type": "batter", "game_pk": 1,
        "game_start": "2026-08-17T18:00:00Z", "player_id": player_id,
        "combo_player_ids": None, "name": "Fixture", "team": "A",
        "matchup": "A @ B", "prop": f"1+ {stat}",
        "projection": {"stat": stat, "needs": 1}, "stat": stat,
        "market_side": "over", "recommendation_status": "top_pick",
        "status_reasons": ["fixture"], "hit_probability": .7,
        "market_odds": odds, "market_implied": .545, "market_edge": .155,
    }
    row["id"] = canonical_prop_id(row)
    return row


def payload(rows, date="2026-08-17"):
    return {
        "schema_version": 3, "identity_schema_version": 2,
        "date": date, "generated_at": T1, "odds_fetched_at": T1,
        "recommendation_metadata": {"model_version": "model-x"},
        "props": rows,
    }


def published_registry_entry(row=None, deployed_at=T2, odds=-120):
    row = row or prop(odds=odds)
    registry = default_registry()
    manifest = build_publication_manifest(
        payload([row]), {"schema_version": 3, "props": {}}, registry, "sha1", T1,
    )
    confirm_publication(registry, manifest, deployed_at, {"source_commit": "sha1", "run_id": "10"})
    return registry, registry["entries"][row["id"]]


class LedgerPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "events.jsonl")

    def tearDown(self):
        self.tmp.cleanup()


class AppendAndChainTests(LedgerPathTests):
    def test_first_event_has_no_prev_hash(self):
        _, entry = published_registry_entry()
        added = append_publication_event(entry, path=self.path)
        self.assertTrue(added)
        with open(self.path, encoding="utf-8") as handle:
            event = json.loads(handle.readline())
        self.assertIsNone(event["prev_hash"])
        self.assertEqual(event["event_seq"], 0)
        self.assertEqual(event["event_type"], "publication")
        self.assertEqual(event["prop_id"], entry["canonical_id"])

    def test_second_event_chains_to_first(self):
        _, entry1 = published_registry_entry(prop(player_id=101), deployed_at=T2)
        _, entry2 = published_registry_entry(prop(player_id=102), deployed_at=T2)
        append_publication_event(entry1, path=self.path)
        append_publication_event(entry2, path=self.path)
        with open(self.path, encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle]
        self.assertEqual(events[1]["prev_hash"], events[0]["event_hash"])
        self.assertEqual(events[1]["event_seq"], 1)

    def test_duplicate_publication_for_same_prop_is_a_no_op(self):
        _, entry = published_registry_entry()
        first = append_publication_event(entry, path=self.path)
        # Even with a different snapshot payload, publication cannot be re-asserted.
        mutated = copy.deepcopy(entry)
        mutated["snapshot"]["market_odds"] = -999
        second = append_publication_event(mutated, path=self.path)
        self.assertTrue(first)
        self.assertFalse(second)
        with open(self.path, encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["snapshot"]["market_odds"], -120)

    def test_rejects_non_utc_recorded_at(self):
        _, entry = published_registry_entry()
        with self.assertRaises(ValueError):
            append_publication_event(entry, recorded_at="not-a-timestamp", path=self.path)

    def test_missing_file_reads_as_empty_and_first_append_creates_it(self):
        self.assertFalse(os.path.exists(self.path))
        _, entry = published_registry_entry()
        append_publication_event(entry, path=self.path)
        self.assertTrue(os.path.exists(self.path))


class IntegrityVerificationTests(LedgerPathTests):
    def _seeded(self, n=3):
        for i in range(n):
            _, entry = published_registry_entry(prop(player_id=100 + i), deployed_at=T2)
            append_publication_event(entry, path=self.path)

    def test_clean_chain_verifies(self):
        self._seeded(3)
        summary = verify_ledger_integrity(self.path)
        self.assertEqual(summary, {"event_count": 3, "publication_count": 3})

    def test_empty_ledger_verifies_trivially(self):
        summary = verify_ledger_integrity(self.path)
        self.assertEqual(summary, {"event_count": 0, "publication_count": 0})

    def test_tampered_payload_is_detected(self):
        self._seeded(2)
        with open(self.path, encoding="utf-8") as handle:
            lines = handle.readlines()
        event = json.loads(lines[0])
        event["payload"]["snapshot"]["hit_probability"] = 0.999
        lines[0] = json.dumps(event) + "\n"
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
        with self.assertRaises(ValueError):
            verify_ledger_integrity(self.path)

    def test_broken_chain_is_detected(self):
        self._seeded(2)
        with open(self.path, encoding="utf-8") as handle:
            lines = handle.readlines()
        event = json.loads(lines[1])
        event["prev_hash"] = "0" * 64
        lines[1] = json.dumps(event) + "\n"
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
        with self.assertRaises(ValueError):
            verify_ledger_integrity(self.path)

    def test_out_of_order_event_seq_is_detected(self):
        self._seeded(2)
        with open(self.path, encoding="utf-8") as handle:
            lines = handle.readlines()
        event = json.loads(lines[1])
        event["event_seq"] = 5
        lines[1] = json.dumps(event) + "\n"
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
        with self.assertRaises(ValueError):
            verify_ledger_integrity(self.path)

    def test_duplicate_publication_event_hand_crafted_is_detected(self):
        # append_publication_event's own no-op guard prevents this in normal
        # use; verify_ledger_integrity must independently catch it too, in
        # case the file is ever produced by another path.
        _, entry = published_registry_entry()
        append_publication_event(entry, path=self.path)
        with open(self.path, encoding="utf-8") as handle:
            lines = handle.readlines()
        first = json.loads(lines[0])
        forged = dict(first)
        forged["event_seq"] = 1
        forged["prev_hash"] = first["event_hash"]
        from dashboard.prediction_ledger import _event_hash
        forged["event_hash"] = _event_hash(
            forged["prev_hash"], forged["prop_id"], forged["event_type"],
            forged["payload"], forged["recorded_at"],
        )
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(forged) + "\n")
        with self.assertRaises(ValueError):
            verify_ledger_integrity(self.path)


class BackfillTests(LedgerPathTests):
    def test_backfill_seeds_one_event_per_registry_entry(self):
        registry = default_registry()
        for i in range(3):
            row = prop(player_id=200 + i)
            manifest = build_publication_manifest(
                payload([row]), {"schema_version": 3, "props": {}}, registry, "sha", T1,
            )
            confirm_publication(registry, manifest, T2, {"source_commit": "sha"})
        added = backfill_from_registry(registry, path=self.path)
        self.assertEqual(added, 3)
        summary = verify_ledger_integrity(self.path)
        self.assertEqual(summary, {"event_count": 3, "publication_count": 3})

    def test_backfill_is_idempotent(self):
        registry = default_registry()
        row = prop()
        manifest = build_publication_manifest(
            payload([row]), {"schema_version": 3, "props": {}}, registry, "sha", T1,
        )
        confirm_publication(registry, manifest, T2, {"source_commit": "sha"})
        first_added = backfill_from_registry(registry, path=self.path)
        second_added = backfill_from_registry(registry, path=self.path)
        self.assertEqual(first_added, 1)
        self.assertEqual(second_added, 0)
        summary = verify_ledger_integrity(self.path)
        self.assertEqual(summary["event_count"], 1)

    def test_backfill_uses_registrys_own_first_published_at(self):
        registry = default_registry()
        row = prop()
        manifest = build_publication_manifest(
            payload([row]), {"schema_version": 3, "props": {}}, registry, "sha", T1,
        )
        confirm_publication(registry, manifest, T2, {"source_commit": "sha"})
        backfill_from_registry(registry, path=self.path)
        with open(self.path, encoding="utf-8") as handle:
            event = json.loads(handle.readline())
        self.assertEqual(event["recorded_at"], registry["entries"][row["id"]]["first_published_at"])


class ReconstructWagerTests(LedgerPathTests):
    def setUp(self):
        super().setUp()
        self.results_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.results_dir.cleanup()
        super().tearDown()

    def test_no_publication_event_is_reported_honestly(self):
        result = reconstruct_wager("fc2:nonexistent", ledger_path=self.path,
                                    results_dir=self.results_dir.name)
        self.assertFalse(result["found"])
        self.assertIn("reason", result)

    def test_published_but_not_yet_graded(self):
        row = prop()
        _, entry = published_registry_entry(row)
        append_publication_event(entry, path=self.path)
        result = reconstruct_wager(row["id"], ledger_path=self.path,
                                    results_dir=self.results_dir.name)
        self.assertTrue(result["found"])
        self.assertEqual(result["outcome_status"], "not_yet_graded")
        self.assertIsNone(result["outcome"])
        self.assertEqual(result["prediction"]["snapshot"]["market_odds"], -120)

    def test_slate_graded_but_pick_missing_from_grades_file(self):
        row = prop()
        _, entry = published_registry_entry(row)
        append_publication_event(entry, path=self.path)
        slate_date = entry["slate_date"]
        grades_path = os.path.join(self.results_dir.name, f"grades_{slate_date}.json")
        with open(grades_path, "w", encoding="utf-8") as handle:
            json.dump({"public_top_picks": []}, handle)
        result = reconstruct_wager(row["id"], ledger_path=self.path,
                                    results_dir=self.results_dir.name)
        self.assertEqual(result["outcome_status"], "slate_graded_but_pick_not_found")

    def test_graded_outcome_is_attached(self):
        row = prop()
        _, entry = published_registry_entry(row)
        append_publication_event(entry, path=self.path)
        slate_date = entry["slate_date"]
        grades_path = os.path.join(self.results_dir.name, f"grades_{slate_date}.json")
        graded_row = dict(entry["snapshot"])
        graded_row.update({
            "grade": "hit", "settlement_state": "hit",
            "settlement_authority": "official_final",
            "settlement_observed_at": "2026-08-17T22:00:00Z",
            "settlement_source": "mlb_official_final",
            "reason": "official final statistic compared with the displayed threshold",
        })
        with open(grades_path, "w", encoding="utf-8") as handle:
            json.dump({"public_top_picks": [graded_row]}, handle)
        result = reconstruct_wager(row["id"], ledger_path=self.path,
                                    results_dir=self.results_dir.name)
        self.assertEqual(result["outcome_status"], "graded")
        self.assertEqual(result["outcome"]["grade"], "hit")
        self.assertEqual(result["outcome"]["settlement_authority"], "official_final")

    def test_prediction_mismatch_is_flagged_not_silently_accepted(self):
        row = prop()
        _, entry = published_registry_entry(row)
        append_publication_event(entry, path=self.path)
        slate_date = entry["slate_date"]
        grades_path = os.path.join(self.results_dir.name, f"grades_{slate_date}.json")
        graded_row = dict(entry["snapshot"])
        graded_row["market_odds"] = -9999  # diverges from the immutable ledger snapshot
        graded_row.update({
            "grade": "hit", "settlement_state": "hit",
            "settlement_authority": "official_final",
            "settlement_observed_at": "2026-08-17T22:00:00Z",
            "settlement_source": "mlb_official_final",
        })
        with open(grades_path, "w", encoding="utf-8") as handle:
            json.dump({"public_top_picks": [graded_row]}, handle)
        result = reconstruct_wager(row["id"], ledger_path=self.path,
                                    results_dir=self.results_dir.name)
        self.assertEqual(result["outcome_status"], "graded_with_prediction_mismatch")
        self.assertIn("market_odds", result["outcome"]["prediction_mismatch_fields"])


class ConfirmPublicationWiringTests(LedgerPathTests):
    def setUp(self):
        super().setUp()
        self.tmp2 = tempfile.TemporaryDirectory()
        self.registry_path = os.path.join(self.tmp2.name, "registry.json")
        self.manifest_path = os.path.join(self.tmp2.name, "manifest.json")

    def tearDown(self):
        self.tmp2.cleanup()
        super().tearDown()

    def _write_manifest(self, row):
        registry = default_registry()
        manifest = build_publication_manifest(
            payload([row]), {"schema_version": 3, "props": {}}, registry, "sha", T1,
        )
        with open(self.manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        return manifest

    def test_confirm_writes_both_registry_and_ledger_together(self):
        row = prop()
        self._write_manifest(row)
        changed, registry = confirm_deploy(
            self.manifest_path, registry_path=self.registry_path,
            deployed_at=T2, provenance={"source_commit": "sha"}, ledger_path=self.path,
        )
        self.assertTrue(changed)
        self.assertIn(row["id"], registry["entries"])
        summary = verify_ledger_integrity(self.path)
        self.assertEqual(summary["publication_count"], 1)
        with open(self.path, encoding="utf-8") as handle:
            event = json.loads(handle.readline())
        self.assertEqual(event["prop_id"], row["id"])
        self.assertEqual(event["payload"]["snapshot"], registry["entries"][row["id"]]["snapshot"])

    def test_second_deploy_of_same_pick_does_not_duplicate_ledger_event(self):
        row = prop()
        self._write_manifest(row)
        confirm_deploy(self.manifest_path, registry_path=self.registry_path,
                        deployed_at=T2, provenance={"source_commit": "sha"}, ledger_path=self.path)
        # Re-running confirm with the same manifest must not re-append.
        changed, _ = confirm_deploy(
            self.manifest_path, registry_path=self.registry_path,
            deployed_at="2026-08-17T17:10:00Z", provenance={"source_commit": "sha"},
            ledger_path=self.path,
        )
        self.assertFalse(changed)
        summary = verify_ledger_integrity(self.path)
        self.assertEqual(summary["event_count"], 1)


class SnapshotPreservesLiftAndBaseRateTests(LedgerPathTests):
    """2026-08-25 registry-integrity reconciliation: real gap found while
    tracing why registry.json's published Top Picks and results/grades_*
    .json's "picks" list are different populations (they're SUPPOSED to be
    -- two independently-selected candidate pools from two different
    pipeline runs, not a defect; see the reconciliation report for the full
    trace). The one real, fixable gap found along the way: docs/data.json's
    own rows already carry `lift`/`base_rate`, but SNAPSHOT_FIELDS never
    listed them, so the one immutable, first-exposure record of a published
    Top Pick could never answer "was this a positive- or negative-lift
    pick" after the fact -- exactly what a lift-vs-outcome accuracy
    comparison needs the canonical published history to carry."""

    def test_lift_and_base_rate_flow_into_the_immutable_snapshot(self):
        row = prop()
        row["lift"] = -0.0517
        row["base_rate"] = 0.6691
        registry, entry = published_registry_entry(row=row)
        self.assertEqual(entry["snapshot"].get("lift"), -0.0517,
                         "a real negative lift on the source row survives into the "
                         "durable snapshot -- this is the exact field the negative-lift "
                         "Top Pick investigation needs the canonical history to carry")
        self.assertEqual(entry["snapshot"].get("base_rate"), 0.6691)

    def test_absence_degrades_gracefully_not_a_fabricated_zero(self):
        row = prop()  # no lift/base_rate set -- some callers may never have them
        self.assertNotIn("lift", row)
        registry, entry = published_registry_entry(row=row)
        self.assertNotIn("lift", entry["snapshot"],
                         "a row with no real lift computed leaves the field absent in the "
                         "snapshot, never a fabricated 0.0 -- same 'absent is not zero' "
                         "discipline this project holds everywhere else")
        self.assertNotIn("base_rate", entry["snapshot"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

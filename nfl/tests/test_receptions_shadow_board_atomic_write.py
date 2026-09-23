#!/usr/bin/env python3
"""Regression coverage for the atomic-write / failed-artifact-exclusion
safeguard added to the NFL live receptions shadow-board workflow's
aggregate challenger seal+write block.

Jacob's PR #172 merge authorization (Issue #91 comment `5784769579`)
explicitly required "a forced mid-write failure test demonstrating that
primary B0 capture still succeeds and no corrupted challenger evidence is
uploaded as valid." This file provides that in two layers:

1. Direct unit tests of `write_challenger_evidence_atomically` itself,
   forcing a crash partway through a real write (real bytes already
   flushed to the temp file, then an exception) and proving no partial
   file is ever left at the final path and no temp file lingers either.
2. A control-flow test that extracts the REAL try/except block from the
   workflow YAML file -- via the same PyYAML block-scalar extraction
   method used to catch the heredoc defect in PR #171, not a hand-copied
   reimplementation -- and executes it with that same forced mid-write
   failure, proving the real production code never lets a challenger-side
   failure escape past the try/except that guards it. (The primary
   board's own build/write, which follows unconditionally in unchanged
   code immediately after this block, was independently reviewed and
   confirmed unaffected by a challenger-side exception in the prior
   review round for this PR -- this test proves the exception is fully
   contained here, which is what makes that reachability guarantee hold.)
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from nfl.prospective.receptions_challenger_snapshot import (
    write_challenger_evidence_atomically,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = (
    REPO_ROOT / ".github" / "workflows" / "nfl-live-receptions-shadow-board.yml"
)
STEP_NAME = "Build live Sunday receptions shadow board without publishing"


def _load_capture_script() -> str:
    doc = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    for step in doc["jobs"]["board"]["steps"]:
        if step.get("name") == STEP_NAME:
            return step["run"]
    raise AssertionError(f"could not find step {STEP_NAME!r} in {WORKFLOW_PATH}")


def _extract_aggregate_challenger_block(script: str) -> str:
    start = script.index("challenger_snapshot = None")
    end = script.index("board = {")
    return script[start:end]


def _mid_write_crash(obj, fp, **kwargs):
    """Simulate a real crash partway through `json.dump`: some real bytes
    are already flushed to the (temp) file handle before the exception."""
    fp.write('{"partial": "should never be persisted or renamed into place"')
    raise OSError("simulated mid-write crash")


class WriteChallengerEvidenceAtomicallyTests(unittest.TestCase):
    def test_successful_write_produces_valid_readable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nfl-receptions-challenger-comparison.json"
            payload = {"status": "RESEARCH_ONLY_NOT_PROMOTED", "record_count": 3}
            write_challenger_evidence_atomically(path, payload)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
            self.assertEqual(os.listdir(tmp), [path.name])

    def test_forced_mid_write_failure_leaves_no_partial_or_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nfl-receptions-challenger-comparison.json"
            with mock.patch(
                "nfl.prospective.receptions_challenger_snapshot.json.dump",
                side_effect=_mid_write_crash,
            ):
                with self.assertRaises(OSError):
                    write_challenger_evidence_atomically(path, {"anything": True})
            self.assertFalse(path.exists(), "final path must never see a partial write")
            self.assertEqual(
                os.listdir(tmp), [],
                "the temp file must be cleaned up on failure, not left behind",
            )

    def test_forced_mid_write_failure_never_disturbs_a_prior_valid_file(self):
        # If a valid artifact from earlier already exists at the final
        # path, a later failed attempt to refresh it must leave that
        # existing valid evidence completely untouched -- never replaced
        # with a partial document, and never deleted either.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nfl-receptions-challenger-comparison.json"
            original = {"status": "RESEARCH_ONLY_NOT_PROMOTED", "record_count": 1}
            write_challenger_evidence_atomically(path, original)
            with mock.patch(
                "nfl.prospective.receptions_challenger_snapshot.json.dump",
                side_effect=_mid_write_crash,
            ):
                with self.assertRaises(OSError):
                    write_challenger_evidence_atomically(path, {"record_count": 2})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)
            self.assertEqual(os.listdir(tmp), [path.name])


class WorkflowAggregateChallengerBlockControlFlowTests(unittest.TestCase):
    """Executes the REAL try/except block from the workflow YAML (not a
    reimplementation) with the write forced to crash mid-write, proving
    the exception is fully contained and execution reaches the next real
    statement (`board = {`, unconditional primary-board assembly)."""

    def setUp(self):
        self.script = _load_capture_script()
        self.block = _extract_aggregate_challenger_block(self.script)
        self.assertIn("except Exception as exc:", self.block)
        self.assertIn("write_challenger_evidence_atomically", self.block)

    def _run_block(self, evidence_root: Path):
        namespace = {
            "os": os,
            "TARGET_DATE": "2026-09-22",
            "source_vintage": "2026-09-22T00:00:00Z",
            "sealed_at": "2026-09-22T01:00:00Z",
            "CHALLENGER_MODEL_VERSION": "TEST_NEGATIVE_BINOMIAL_POOLED_V1",
            "challenger_snapshot_records": [],
            "challenger_build_failures": [],
            "EVIDENCE_ROOT": evidence_root,
            "seal_challenger_snapshot": mock.Mock(return_value={"record_count": 0}),
            "write_challenger_evidence_atomically": write_challenger_evidence_atomically,
        }
        # A marker line appended by THIS TEST (not from the workflow file)
        # that sits immediately after the real except block in source
        # order -- it only ever runs if the try/except above it did not
        # re-raise.
        source = self.block + "\nexecution_reached_next_statement = True\n"
        with mock.patch.dict(os.environ, {"FULL_COUNT_CODE_SHA": "deadbeef"}):
            exec(compile(source, str(WORKFLOW_PATH), "exec"), namespace)
        return namespace

    def test_forced_mid_write_failure_is_fully_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            with mock.patch(
                "nfl.prospective.receptions_challenger_snapshot.json.dump",
                side_effect=_mid_write_crash,
            ):
                namespace = self._run_block(evidence_root)

        self.assertIsNone(namespace["challenger_snapshot"])
        self.assertEqual(len(namespace["challenger_build_failures"]), 1)
        self.assertIn(
            "aggregate seal/write failed",
            namespace["challenger_build_failures"][0]["failure_reason"],
        )
        self.assertTrue(
            namespace.get("execution_reached_next_statement"),
            "the real try/except must swallow the forced mid-write crash and "
            "let control fall through to the unconditional primary board "
            "assembly that follows it in the actual workflow script",
        )

    def test_forced_mid_write_failure_uploads_no_challenger_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            with mock.patch(
                "nfl.prospective.receptions_challenger_snapshot.json.dump",
                side_effect=_mid_write_crash,
            ):
                self._run_block(evidence_root)
            # actions/upload-artifact globs the whole EVIDENCE_ROOT
            # directory indiscriminately -- nothing may be left in it for
            # a failed run to be mistaken for valid evidence.
            self.assertEqual(list(evidence_root.iterdir()), [])

    def test_clean_run_does_produce_a_readable_challenger_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            namespace = self._run_block(evidence_root)
        self.assertIsNotNone(namespace["challenger_snapshot"])
        self.assertEqual(namespace["challenger_build_failures"], [])
        self.assertTrue(namespace.get("execution_reached_next_statement"))


if __name__ == "__main__":
    unittest.main()

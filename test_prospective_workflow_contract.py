#!/usr/bin/env python3
"""Static safety contract for the manual prospective-shadow workflow."""
from __future__ import annotations

import os
import unittest


ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, ".github", "workflows", "prospective-shadow.yml")


class ProspectiveWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PATH, encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_manual_only_until_separately_authorized(self):
        self.assertIn("workflow_dispatch:", self.text)
        self.assertNotIn("schedule:", self.text)

    def test_persistence_is_explicit_opt_in(self):
        self.assertIn("persist:", self.text)
        self.assertIn("default: false", self.text)
        self.assertIn(
            "if: inputs.persist == true", self.text)

    def test_capture_job_is_read_only(self):
        capture = self.text.split("  capture:", 1)[1].split("  persist:", 1)[0]
        self.assertIn("contents: read", capture)
        self.assertNotIn("contents: write", capture)

    def test_only_dedicated_research_ledger_is_push_target(self):
        self.assertIn("prospective-candidate-ledger", self.text)
        self.assertIn(
            "HEAD:refs/heads/prospective-candidate-ledger", self.text)
        for forbidden in (
            "HEAD:refs/heads/main",
            "HEAD:main",
            "git push origin main",
            "git push origin HEAD:main",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_writer_is_serialized_not_cancelled_mid_commit(self):
        self.assertIn("group: prospective-shadow-capture", self.text)
        self.assertIn("cancel-in-progress: false", self.text)

    def test_append_only_diff_is_enforced_before_push(self):
        self.assertIn(
            "prospective ledger is append-only", self.text)
        self.assertIn(
            "diff --cached --name-status", self.text)
        self.assertIn(
            'awk \'$1 != "A" {print}\'', self.text)

    def test_existing_ledger_is_reverified_not_blindly_copied(self):
        self.assertIn(
            "--destination-root \"$LEDGER_DIR\"", self.text)
        # The pre-materialized artifact is uploaded for inspection, while the
        # persistence job reruns the durability verifier against the actual
        # ledger checkout before committing.
        self.assertIn(
            "Re-verify against existing ledger and materialize", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

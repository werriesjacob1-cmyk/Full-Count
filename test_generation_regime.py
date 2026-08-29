#!/usr/bin/env python3
"""Tests for backtest/generation_regime.py -- the canonical dataset's
code-identity and repository-provenance layer.

Covers the two integrity failures an independent audit found in the
canonical run: a manifest recording the wrong repository, and a
mixed-code-SHA dataset with no formal basis for treating its two segments
as one population.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from backtest import generation_regime as gr

LEGACY = "2ce95fe903526c62640d23659d84d37bbaf1d6d2"
PINNED = "022c88299281c5265204fcff548313b9973e9ec7"


def _sha_reachable(sha):
    """Is this historical commit actually present in the local object store?

    These tests read real blobs at real SHAs, which is the point -- a code
    identity closure is a claim about history, so proving it needs history.
    But a shallow clone (actions/checkout@v4 defaults to fetch-depth: 1) has
    no such objects, and the failure surfaced as a bare RegimeError about a
    "stale closure definition" -- which is a genuinely alarming message for
    what is only a missing checkout depth. CI is now configured with
    fetch-depth: 0 so these run for real; this guard exists so that any OTHER
    shallow environment reports a precise skip instead of a misleading error.
    """
    import subprocess
    try:
        r = subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                           cwd=ROOT, capture_output=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_HISTORY_AVAILABLE = _sha_reachable(PINNED) and _sha_reachable(LEGACY)
_NEEDS_HISTORY = unittest.skipUnless(
    _HISTORY_AVAILABLE,
    f"requires full git history: {PINNED[:12]} and/or {LEGACY[:12]} are not in this "
    "clone (shallow checkout?). Run with fetch-depth: 0.")


class RepositoryIdentityTests(unittest.TestCase):
    def test_canonical_identity_is_the_post_rename_name(self):
        # Verified against GitHub's API reporting full_name for this repo.
        self.assertEqual(gr.CANONICAL_REPOSITORY_IDENTITY, "werriesjacob1-cmyk/Full-Count")

    def test_correct_identity_validates(self):
        m = {"run_id": "r", "repository_identity": gr.CANONICAL_REPOSITORY_IDENTITY}
        self.assertEqual(gr.validate_repository_identity(m),
                         gr.CANONICAL_REPOSITORY_IDENTITY)

    def test_wrong_identity_fails_closed_without_a_correction(self):
        m = {"run_id": "r", "repository_identity": "werriesjacob1-cmyk/PROJECT-GRIDIRON"}
        with self.assertRaises(gr.RepositoryIdentityError):
            gr.validate_repository_identity(m)

    def test_wrong_identity_passes_with_a_matching_correction_record(self):
        m = {"run_id": "r", "repository_identity": "werriesjacob1-cmyk/PROJECT-GRIDIRON"}
        corr = gr.build_repository_identity_correction(
            m, reason="pre-rename name", fix_commit="abc123")
        self.assertEqual(gr.validate_repository_identity(m, [corr]),
                         gr.CANONICAL_REPOSITORY_IDENTITY)
        self.assertTrue(corr["original_value_is_known_alias"])
        self.assertTrue(corr["manifest_left_unmodified"])

    def test_correction_is_additive_and_never_edits_the_manifest(self):
        m = {"run_id": "r", "repository_identity": "werriesjacob1-cmyk/PROJECT-GRIDIRON"}
        snapshot = dict(m)
        gr.build_repository_identity_correction(m, reason="x", fix_commit="y")
        self.assertEqual(m, snapshot, "building a correction must not mutate the manifest")

    def test_correction_for_a_DIFFERENT_original_value_does_not_apply(self):
        m = {"run_id": "r", "repository_identity": "someone-else/OtherRepo"}
        other = {"run_id": "r", "repository_identity": "werriesjacob1-cmyk/PROJECT-GRIDIRON"}
        corr = gr.build_repository_identity_correction(other, reason="x", fix_commit="y")
        with self.assertRaises(gr.RepositoryIdentityError):
            gr.validate_repository_identity(m, [corr])

    def test_correction_for_a_different_run_does_not_apply(self):
        m = {"run_id": "run-A", "repository_identity": "werriesjacob1-cmyk/PROJECT-GRIDIRON"}
        corr = gr.build_repository_identity_correction(
            {"run_id": "run-B", "repository_identity": "werriesjacob1-cmyk/PROJECT-GRIDIRON"},
            reason="x", fix_commit="y")
        with self.assertRaises(gr.RepositoryIdentityError):
            gr.validate_repository_identity(m, [corr])

    def test_correcting_an_already_correct_manifest_is_refused_as_meaningless(self):
        m = {"run_id": "r", "repository_identity": gr.CANONICAL_REPOSITORY_IDENTITY}
        with self.assertRaises(ValueError):
            gr.build_repository_identity_correction(m, reason="x", fix_commit="y")


@_NEEDS_HISTORY
class ClosureAndFingerprintTests(unittest.TestCase):
    def test_closure_is_computed_and_includes_the_undiscoverable_extras(self):
        closure = gr.discover_generation_closure(PINNED)
        self.assertIn("backtest/engine.py", closure)
        self.assertIn("generate_picks.py", closure)
        # sys.path-injected, invisible to a naive resolver -- declared by hand
        self.assertIn("backtest/calibration.py", closure)
        # lazily imported from inside a function, so a top-level-only walk misses it
        self.assertIn("dashboard/live_state.py", closure)

    def test_fingerprint_is_stable_and_covers_model_artifacts(self):
        a = gr.generation_regime_fingerprint(PINNED)
        b = gr.generation_regime_fingerprint(PINNED)
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        self.assertIn("backtest/calibrators_by_market.json", a["artifacts"])
        self.assertGreater(a["n_files"], 10)

    def test_the_two_real_shas_differ_structurally_in_exactly_three_files(self):
        cmp_ = gr.compare_generation_regimes(LEGACY, PINNED)
        self.assertFalse(cmp_["structurally_equivalent"])
        self.assertEqual(sorted(cmp_["differing_files"]), [
            "dashboard/live_state.py", "dashboard/settlement_rules.py",
            "grade_results.py"])
        # The audit that preceded this module named only grade_results.py;
        # a computed closure finds all three.
        self.assertEqual(
            cmp_["differing_files"]["grade_results.py"]["functions"]["changed"], ["grade_day"])
        self.assertEqual(
            cmp_["differing_files"]["dashboard/settlement_rules.py"]["functions"]["changed"], [])


@_NEEDS_HISTORY
class EquivalenceRecordTests(unittest.TestCase):
    def _rec(self, **kw):
        return gr.build_equivalence_record(LEGACY, PINNED, **kw)

    def test_structural_difference_without_replay_is_UNPROVEN_and_not_eligible(self):
        rec = self._rec()
        self.assertEqual(rec["status"], gr.MIXED_UNPROVEN)
        self.assertFalse(rec["canonical_eligible"])

    def test_structural_difference_plus_replay_equivalent_is_eligible(self):
        rec = self._rec(replay_verdict="equivalent")
        self.assertEqual(rec["status"], gr.MIXED_EQUIVALENT)
        self.assertTrue(rec["canonical_eligible"])

    def test_replay_not_equivalent_always_wins_over_structure(self):
        rec = self._rec(replay_verdict="not_equivalent")
        self.assertEqual(rec["status"], gr.MIXED_NON_EQUIVALENT)
        self.assertFalse(rec["canonical_eligible"])

    def test_record_states_that_row_level_provenance_is_preserved(self):
        self.assertIn("PRESERVED", self._rec(replay_verdict="equivalent")["note"])

    def test_invalid_replay_verdict_is_rejected(self):
        with self.assertRaises(ValueError):
            self._rec(replay_verdict="probably fine")


@_NEEDS_HISTORY
class DatasetClassificationTests(unittest.TestCase):
    def test_single_sha_is_eligible(self):
        c = gr.classify_dataset_regime([PINNED])
        self.assertEqual(c["status"], gr.SINGLE_SHA)
        self.assertTrue(c["canonical_eligible"])

    def test_mixed_with_covering_equivalent_record_is_eligible(self):
        rec = gr.build_equivalence_record(LEGACY, PINNED, replay_verdict="equivalent")
        c = gr.classify_dataset_regime([LEGACY, PINNED], [rec])
        self.assertEqual(c["status"], gr.MIXED_EQUIVALENT)
        self.assertTrue(c["canonical_eligible"])

    def test_mixed_with_no_record_is_unproven_and_not_eligible(self):
        c = gr.classify_dataset_regime([LEGACY, PINNED], [])
        self.assertEqual(c["status"], gr.MIXED_UNPROVEN)
        self.assertFalse(c["canonical_eligible"])
        self.assertEqual(c["uncovered_pairs"], [sorted([LEGACY, PINNED])])

    def test_a_single_uncovered_pair_drops_the_whole_dataset(self):
        rec = gr.build_equivalence_record(LEGACY, PINNED, replay_verdict="equivalent")
        c = gr.classify_dataset_regime([LEGACY, PINNED, "cafebabe" * 5], [rec])
        self.assertEqual(c["status"], gr.MIXED_UNPROVEN)
        self.assertFalse(c["canonical_eligible"])

    def test_non_equivalent_pair_poisons_the_dataset(self):
        rec = gr.build_equivalence_record(LEGACY, PINNED, replay_verdict="not_equivalent")
        c = gr.classify_dataset_regime([LEGACY, PINNED], [rec])
        self.assertEqual(c["status"], gr.MIXED_NON_EQUIVALENT)
        self.assertFalse(c["canonical_eligible"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

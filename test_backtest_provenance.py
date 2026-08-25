#!/usr/bin/env python3
"""test_backtest_provenance.py -- coverage for backtest/provenance.py, the
fail-fast regime-detection guardrail added 2026-08-25 after the repair-vs-
main backfill investigation found a real, silently-mixed-code-regime
dataset that nothing had caught automatically.

    /tmp/mlbvenv/bin/python3 test_backtest_provenance.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import provenance as prov


def write_rows(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class RegimeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name="rows.jsonl"):
        return os.path.join(self.tmp.name, name)

    def test_single_regime_reports_one_bucket_with_correct_counts(self):
        p = self.path()
        write_rows(p, [
            {"date": "2024-04-01", "code_git_sha": "aaa111"},
            {"date": "2024-04-02", "code_git_sha": "aaa111"},
            {"date": "2024-04-03", "code_git_sha": "aaa111"},
        ])
        report = prov.inspect_regimes(p)
        self.assertEqual(len(report), 1)
        info = next(iter(report.values()))
        self.assertEqual(info["n_rows"], 3)
        self.assertEqual(info["n_dates"], 3)
        self.assertEqual(info["date_range"], ["2024-04-01", "2024-04-03"])

    def test_mixed_regime_reports_every_distinct_regime_separately(self):
        p = self.path()
        write_rows(p, [
            {"date": "2024-04-01", "code_git_sha": "old_sha"},
            {"date": "2024-04-02", "code_git_sha": "old_sha"},
            {"date": "2024-04-03", "code_git_sha": "new_sha"},
        ])
        report = prov.inspect_regimes(p)
        self.assertEqual(len(report), 2)
        n_rows_by_regime = sorted(info["n_rows"] for info in report.values())
        self.assertEqual(n_rows_by_regime, [1, 2])

    def test_require_single_regime_passes_silently_when_clean(self):
        p = self.path()
        write_rows(p, [{"date": "2024-04-01", "code_git_sha": "aaa111"}])
        report = prov.require_single_regime(p)  # must not raise
        self.assertEqual(len(report), 1)

    def test_require_single_regime_fails_closed_on_a_real_mix(self):
        # Reproduces the exact real shape found 2026-08-25: an early
        # portion on a pre-fix commit, a later portion on a post-fix one.
        p = self.path()
        write_rows(p, [
            {"date": "2024-04-01", "code_git_sha": "c182b186"},
            {"date": "2024-04-20", "code_git_sha": "c182b186"},
            {"date": "2025-02-27", "code_git_sha": "6b748538"},
        ])
        with self.assertRaises(prov.MixedRegimeError) as ctx:
            prov.require_single_regime(p)
        # The error message must actually name both regimes and their row
        # counts -- not just "mixed", so a human reading it can act on it
        # without re-running the inspection by hand.
        self.assertIn("c182b186", str(ctx.exception))
        self.assertIn("6b748538", str(ctx.exception))
        self.assertIn("2", str(ctx.exception))  # 2 distinct regimes found

    def test_require_single_regime_allow_multi_opts_in_explicitly(self):
        p = self.path()
        write_rows(p, [
            {"date": "2024-04-01", "code_git_sha": "old"},
            {"date": "2025-02-27", "code_git_sha": "new"},
        ])
        # Must NOT raise -- explicit opt-in for a deliberate regime
        # comparison (e.g. measuring whether the code change itself moved
        # outcomes), a real, different, legitimate use case.
        report = prov.require_single_regime(p, allow_multi=True)
        self.assertEqual(len(report), 2)

    def test_row_with_no_regime_fields_at_all_is_honestly_unknown_not_assumed(self):
        p = self.path()
        write_rows(p, [
            {"date": "2024-04-01"},  # no code_git_sha, no version fields
            {"date": "2024-04-02"},
        ])
        report = prov.inspect_regimes(p)
        self.assertEqual(len(report), 1)
        key = next(iter(report))
        self.assertEqual(key, ("unknown",))

    def test_unknown_and_known_regimes_are_never_silently_merged(self):
        p = self.path()
        write_rows(p, [
            {"date": "2024-04-01"},  # unknown
            {"date": "2024-04-02", "code_git_sha": "aaa111"},  # known
        ])
        report = prov.inspect_regimes(p)
        self.assertEqual(len(report), 2)
        with self.assertRaises(prov.MixedRegimeError):
            prov.require_single_regime(p)

    def test_future_version_fields_are_inspected_too_not_just_code_git_sha(self):
        # Backtest rows don't carry model_version/feature_version today,
        # but this must keep working without a code change if they start
        # being recorded -- proven directly rather than assumed.
        p = self.path()
        write_rows(p, [
            {"date": "2024-04-01", "code_git_sha": "same", "model_version": "v1"},
            {"date": "2024-04-02", "code_git_sha": "same", "model_version": "v2"},
        ])
        report = prov.inspect_regimes(p)
        self.assertEqual(len(report), 2)  # same code, different model_version -> still a real regime split


if __name__ == "__main__":
    unittest.main(verbosity=2)

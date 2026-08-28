#!/usr/bin/env python3
"""Tests for backtest/canonical_population.py -- the bridge from canonical
durable rows to a measurable EligiblePopulation.

Runs against the REAL durable checkpoint branch when it is present (so the
bridge is proven on real canonical bytes, not only on fixtures), and falls
back to synthetic fixtures everywhere that would otherwise make the suite
depend on a network fetch or on a run being in a particular state.
"""
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import canonical_population as cp
from backtest.equal_volume import (
    EqualVolumeExperiment, SelectionPolicy, OutcomePolicy, rank_by,
    OutcomeLeakage, PopulationIntegrityError,
)

RUN_ID = "canonical-20260827T232203Z-cfb15819"



def _make_fixture_repo(tmpdir, *, n_dates=3, tamper_date=None,
                       status_override=None):
    """Build a real git repo carrying a durable-checkpoint-shaped branch.

    Exists because gating these tests on the real branch means they SKIP in
    CI -- actions/checkout never fetches origin/canonical-durable-
    checkpoints, so the tampered-sha and bad-status tests, the two that
    actually guard canonical integrity, would silently not run. A test that
    does not run is a test that does not exist. This exercises the same
    load_canonical_rows() code path with no network and no branch state.
    """
    repo = os.path.join(tmpdir, "repo")
    os.makedirs(repo)
    env = dict(os.environ,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    def git(*args):
        subprocess.run(["git", "-C", repo] + list(args), check=True,
                       env=env, capture_output=True)
    git("init", "-q", "-b", "main")

    run_dir = os.path.join(repo, "canonical", RUN_ID, "rows")
    os.makedirs(run_dir)
    index = {"run_id": RUN_ID, "dates": {},
             "identity": {"code_git_sha": "abc123"},
             "source_lineage": [{"source": "statcast_leaguewide",
                                 "content_sha256": "c" * 64,
                                 "row_count": 100, "date_coverage": "x..y"}]}
    for i in range(n_dates):
        date = f"2024-04-{i + 1:02d}"
        rows = [_row(date=date, game_pk=500 + i, player_id=900 + j)
                for j in range(4)]
        raw = ("\n".join(json.dumps(r) for r in rows) + "\n").encode()
        with open(os.path.join(run_dir, f"{date}.jsonl.gz"), "wb") as fh:
            fh.write(gzip.compress(raw))
        index["dates"][date] = {
            "status": status_override if date == tamper_date and status_override
                      else "ok",
            "rows": len(rows), "data_bytes": len(raw),
            "data_sha256": ("0" * 64 if date == tamper_date and not status_override
                            else hashlib.sha256(raw).hexdigest()),
        }
    with open(os.path.join(repo, "canonical", RUN_ID, "index.json"), "w") as fh:
        json.dump(index, fh)
    git("add", "-A")
    git("commit", "-q", "-m", "durable fixture")
    return repo, "main"


def _have_durable_run():
    try:
        cp.read_run_index(RUN_ID)
        return True
    except Exception:
        return False


HAVE_REAL = _have_durable_run()


def _row(date="2024-04-01", game_pk=1, player_id=1, prop_type="hits",
         line=0.5, **kw):
    r = {"date": date, "game_pk": game_pk, "player_id": player_id,
         "prop_type": prop_type, "line": line, "outcome": 1, "score": 60.0,
         "predicted_prob": 0.6, "fair_test": True}
    r.update(kw)
    return r


def _synthetic(n=40):
    rows = []
    for i in range(n):
        rows.append(_row(game_pk=100 + i // 4, player_id=1000 + i,
                         outcome=i % 3 == 0, score=float(i),
                         predicted_prob=i / (n * 1.0),
                         fair_test=(i % 10 != 0)))
    artifact = {"artifact_sha256": "a" * 64, "artifact_row_count": len(rows),
                "run_id": "synthetic", "n_dates": 1,
                "date_range": ["2024-04-01", "2024-04-01"],
                "source_artifact_sha256": "b" * 64, "code_git_sha": "deadbeef"}
    return rows, artifact


class TestFairTestIsNeverSilent(unittest.TestCase):
    def test_fair_test_only_is_required(self):
        rows, art = _synthetic()
        with self.assertRaises(TypeError):
            cp.build_eligible_population(rows, art)

    def test_fair_test_only_must_be_a_bool(self):
        rows, art = _synthetic()
        for bad in (None, "yes", 1, 0):
            with self.assertRaises(cp.CanonicalPopulationError):
                cp.build_eligible_population(rows, art, fair_test_only=bad)

    def test_choice_is_recorded_in_the_definition(self):
        rows, art = _synthetic()
        inc = cp.build_eligible_population(rows, art, fair_test_only=False)
        exc = cp.build_eligible_population(rows, art, fair_test_only=True)
        self.assertIn("INCLUDING rows that got no real opportunity", inc.definition)
        self.assertIn("fair_test only", exc.definition)
        self.assertNotEqual(inc.fingerprint, exc.fingerprint)

    def test_exclusion_counts_are_reported(self):
        rows, art = _synthetic()
        exc = cp.build_eligible_population(rows, art, fair_test_only=True)
        reasons = {e["reason"]: e["n_removed"] for e in exc.exclusions}
        fair = [r for r in reasons if r.startswith("fair_test is False")]
        self.assertEqual(len(fair), 1)
        self.assertEqual(reasons[fair[0]],
                         sum(1 for r in rows if r["fair_test"] is False))


class TestEligibilityFilters(unittest.TestCase):
    def test_required_fields_drop_rows_and_say_so(self):
        rows, art = _synthetic()
        rows[0]["predicted_prob"] = None
        pop = cp.build_eligible_population(rows, art, fair_test_only=False)
        self.assertEqual(len(pop), len(rows) - 1)
        self.assertTrue(any("predicted_prob" in e["reason"] for e in pop.exclusions))

    def test_market_and_date_filters(self):
        rows, art = _synthetic()
        rows[0]["prop_type"] = "home_run"
        pop = cp.build_eligible_population(rows, art, fair_test_only=False,
                                           markets=["hits"])
        self.assertEqual(len(pop), len(rows) - 1)
        rows[1]["date"] = "2024-05-01"
        pop2 = cp.build_eligible_population(
            rows, art, fair_test_only=False,
            date_range=("2024-04-01", "2024-04-30"))
        self.assertEqual(len(pop2), len(rows) - 1)

    def test_empty_population_raises_rather_than_returning_nothing(self):
        rows, art = _synthetic()
        with self.assertRaises(cp.CanonicalPopulationError):
            cp.build_eligible_population(rows, art, fair_test_only=False,
                                         markets=["no_such_market"])

    def test_duplicate_identities_are_refused(self):
        rows, art = _synthetic(4)
        rows.append(dict(rows[0]))
        with self.assertRaises(PopulationIntegrityError):
            cp.build_eligible_population(rows, art, fair_test_only=False)


class TestDatasetIdentity(unittest.TestCase):
    def test_identity_is_carried_through_for_promotion_grade(self):
        rows, art = _synthetic()
        pop = cp.build_eligible_population(rows, art, fair_test_only=False)
        self.assertEqual(pop.dataset_identity["artifact_sha256"], "a" * 64)
        self.assertEqual(pop.dataset_identity["artifact_row_count"], len(rows))

    def test_source_identity_is_kept_separate_from_row_identity(self):
        rows, art = _synthetic()
        pop = cp.build_eligible_population(rows, art, fair_test_only=False)
        self.assertNotEqual(pop.dataset_identity["artifact_sha256"],
                            pop.dataset_identity["source_artifact_sha256"])

    def test_promotion_grade_experiment_accepts_this_population(self):
        rows, art = _synthetic()
        pop = cp.build_eligible_population(rows, art, fair_test_only=False)
        exp = EqualVolumeExperiment(
            population=pop,
            champion=SelectionPolicy("champ", "1", rank_by(lambda r: r["score"])),
            challenger=SelectionPolicy(
                "chal", "1", rank_by(lambda r: r["predicted_prob"])),
            volume=5, promotion_grade=True)
        self.assertTrue(exp.promotion_grade)

    def test_missing_identity_blocks_a_promotion_claim(self):
        rows, art = _synthetic()
        art = dict(art, artifact_sha256=None)
        pop = cp.build_eligible_population(rows, art, fair_test_only=False)
        with self.assertRaises(Exception):
            EqualVolumeExperiment(
                population=pop,
                champion=SelectionPolicy("c", "1", rank_by(lambda r: r["score"])),
                challenger=SelectionPolicy("d", "1", rank_by(lambda r: r["score"])),
                volume=5, promotion_grade=True)


class TestEndToEnd(unittest.TestCase):
    def test_a_real_equal_volume_experiment_runs_on_this_population(self):
        rows, art = _synthetic(60)
        pop = cp.build_eligible_population(rows, art, fair_test_only=False)
        exp = EqualVolumeExperiment(
            population=pop,
            champion=SelectionPolicy("score_v1", "1",
                                     rank_by(lambda r: r["score"])),
            challenger=SelectionPolicy("prob_v1", "1",
                                       rank_by(lambda r: r["predicted_prob"])),
            volume=10, outcome_policy=OutcomePolicy())
        rep = exp.run(bootstrap_iterations=50)
        self.assertEqual(rep["champion"]["selected_n"], 10)
        self.assertEqual(rep["challenger"]["selected_n"], 10)
        self.assertIsNotNone(rep["champion"]["hit_rate"])
        self.assertTrue(rep["integrity"]["outcomes_joined_after_selection"])

    def test_selection_still_cannot_read_the_outcome(self):
        """The bridge must not reopen the leak equal_volume.py just closed."""
        rows, art = _synthetic(60)
        pop = cp.build_eligible_population(rows, art, fair_test_only=False)
        oracle = SelectionPolicy("oracle", "1", rank_by(lambda r: r["outcome"]))
        exp = EqualVolumeExperiment(
            population=pop,
            champion=SelectionPolicy("s", "1", rank_by(lambda r: r["score"])),
            challenger=oracle, volume=10)
        with self.assertRaises(OutcomeLeakage):
            exp.run(bootstrap_iterations=10)


class TestCoverageReport(unittest.TestCase):
    def test_reports_what_the_artifact_cannot_support(self):
        rows, art = _synthetic()
        pop = cp.build_eligible_population(rows, art, fair_test_only=False)
        cov = cp.describe_population_coverage(pop)
        self.assertTrue(cov["supports_realized_hit_rate"])
        self.assertFalse(cov["supports_calibration_comparison"])
        self.assertFalse(cov["supports_usable_volume"])
        self.assertEqual(cov["clustering_unit"], "game_pk")
        self.assertGreater(cov["n_games"], 1)



class TestFixtureBranchAlwaysRuns(unittest.TestCase):
    """The integrity guarantees, exercised with no network dependency.

    These mirror TestAgainstRealCanonicalRows but cannot skip.
    """

    def test_loads_and_verifies_every_date(self):
        with tempfile.TemporaryDirectory() as td:
            repo, ref = _make_fixture_repo(td, n_dates=3)
            rows, art = cp.load_canonical_rows(RUN_ID, ref=ref, repo_root=repo)
            self.assertEqual(len(rows), 12)
            self.assertEqual(art["artifact_row_count"], 12)
            self.assertEqual(art["n_dates"], 3)
            self.assertEqual(art["source_artifact_sha256"], "c" * 64)

    def test_tampered_rows_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            repo, ref = _make_fixture_repo(td, n_dates=3,
                                           tamper_date="2024-04-02")
            with self.assertRaises(cp.CanonicalPopulationError) as ctx:
                cp.load_canonical_rows(RUN_ID, ref=ref, repo_root=repo)
            self.assertIn("not the bytes the run certified", str(ctx.exception))

    def test_non_ok_status_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            repo, ref = _make_fixture_repo(td, n_dates=3,
                                           tamper_date="2024-04-03",
                                           status_override="partial")
            with self.assertRaises(cp.CanonicalPopulationError):
                cp.load_canonical_rows(RUN_ID, ref=ref, repo_root=repo)

    def test_no_games_date_is_skipped_not_refused(self):
        """2024-07-15 is the All-Star break. The run records it 'no_games'.
        Refusing it would make any population spanning it unloadable."""
        with tempfile.TemporaryDirectory() as td:
            repo, ref = _make_fixture_repo(td, n_dates=3,
                                           tamper_date="2024-04-02",
                                           status_override="no_games")
            rows, art = cp.load_canonical_rows(RUN_ID, ref=ref, repo_root=repo)
            self.assertEqual(art["n_dates"], 2)
            self.assertEqual(art["dates_no_games"], ["2024-04-02"])
            self.assertEqual(len(rows), 8)

    def test_error_status_is_still_refused(self):
        with tempfile.TemporaryDirectory() as td:
            repo, ref = _make_fixture_repo(td, n_dates=3,
                                           tamper_date="2024-04-02",
                                           status_override="error")
            with self.assertRaises(cp.CanonicalPopulationError):
                cp.load_canonical_rows(RUN_ID, ref=ref, repo_root=repo)

    def test_unknown_date_raises_instead_of_shrinking(self):
        with tempfile.TemporaryDirectory() as td:
            repo, ref = _make_fixture_repo(td, n_dates=2)
            with self.assertRaises(cp.CanonicalPopulationError):
                cp.load_canonical_rows(RUN_ID, ref=ref, repo_root=repo,
                                       dates=["1999-01-01"])

    def test_end_to_end_population_from_fixture_branch(self):
        with tempfile.TemporaryDirectory() as td:
            repo, ref = _make_fixture_repo(td, n_dates=3)
            pop = cp.load_eligible_population(RUN_ID, fair_test_only=False,
                                              ref=ref, repo_root=repo)
            cov = cp.describe_population_coverage(pop)
            self.assertTrue(cov["supports_realized_hit_rate"])
            self.assertEqual(cov["n_dates"], 3)


@unittest.skipUnless(HAVE_REAL, "durable checkpoint branch not fetched")
class TestAgainstRealCanonicalRows(unittest.TestCase):
    def test_loads_real_rows_and_verifies_every_date_sha(self):
        rows, art = cp.load_canonical_rows(RUN_ID)
        self.assertGreater(len(rows), 0)
        self.assertEqual(art["artifact_row_count"], len(rows))
        self.assertEqual(len(art["source_artifact_sha256"]), 64)

    def test_unknown_date_raises_instead_of_silently_shrinking(self):
        with self.assertRaises(cp.CanonicalPopulationError):
            cp.load_canonical_rows(RUN_ID, dates=["1999-01-01"])

    def test_tampered_rows_are_refused(self):
        """A date whose bytes no longer match its recorded sha must not load."""
        idx = cp.read_run_index(RUN_ID)
        date = sorted(idx["dates"])[0]
        idx["dates"][date]["data_sha256"] = "0" * 64
        with self.assertRaises(cp.CanonicalPopulationError) as ctx:
            cp.load_canonical_rows(RUN_ID, dates=[date], index=idx)
        self.assertIn("not the bytes the run certified", str(ctx.exception))

    def test_non_ok_date_is_refused(self):
        idx = cp.read_run_index(RUN_ID)
        date = sorted(idx["dates"])[0]
        idx["dates"][date]["status"] = "partial"
        with self.assertRaises(cp.CanonicalPopulationError):
            cp.load_canonical_rows(RUN_ID, dates=[date], index=idx)

    def test_real_population_is_measurable(self):
        pop = cp.load_eligible_population(RUN_ID, fair_test_only=False)
        cov = cp.describe_population_coverage(pop)
        self.assertTrue(cov["supports_realized_hit_rate"])
        self.assertFalse(cov["supports_calibration_comparison"])
        self.assertGreater(cov["n_games"], 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)

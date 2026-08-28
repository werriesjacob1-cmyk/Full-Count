#!/usr/bin/env python3
"""Preflight must be decisive and must fail closed.

A preflight that passes when the environment is broken is worse than no
preflight: it converts "we did not check" into "we checked and it was
fine". Each check here is asserted in BOTH directions.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import canonical_preflight as pf


def _bare_repo(work):
    """Identity goes into os.environ, not just a local dict.

    check_durable_push_real() spawns git from os.environ, so an identity
    set only here would pass on a machine with a global git identity and
    fail on a runner without one -- the same defect a3017bce fixed, which
    this suite reproduced twice more today. Verified by running the whole
    suite with HOME redirected and GIT_CONFIG_GLOBAL/SYSTEM at /dev/null.
    """
    os.environ.setdefault("GIT_AUTHOR_NAME", "fc-test")
    os.environ.setdefault("GIT_AUTHOR_EMAIL", "fc@test")
    os.environ.setdefault("GIT_COMMITTER_NAME", "fc-test")
    os.environ.setdefault("GIT_COMMITTER_EMAIL", "fc@test")
    bare = os.path.join(work, "r.git")
    repo = os.path.join(work, "repo")
    env = dict(os.environ)
    subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    with open(os.path.join(repo, "s"), "w") as fh:
        fh.write("x")
    for a in (["add", "-A"], ["commit", "-qm", "s"]):
        subprocess.run(["git", "-C", repo] + a, check=True, env=env)
    subprocess.run(["git", "-C", repo, "remote", "add", "origin", bare], check=True)
    subprocess.run(["git", "-C", repo, "push", "-q", "-u", "origin", "main"],
                   check=True, env=env)
    return repo


class TestCachePersistence(unittest.TestCase):
    def test_passes_on_a_writable_directory(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(pf.check_cache_persists(td).ok)

    def test_fails_on_an_unwritable_location(self):
        c = pf.check_cache_persists("/proc/fc-cannot-write-here")
        self.assertFalse(c.ok)


class TestCacheLocation(unittest.TestCase):
    def test_refuses_a_cache_inside_the_worktree(self):
        """A cache inside the tree is destroyed by the checkout that pins
        the run -- the reason the external location exists at all."""
        inside = os.path.join(pf.REPO_ROOT, "data", "would-be-destroyed")
        self.assertFalse(pf.check_cache_outside_worktree(inside).ok)

    def test_accepts_an_external_cache(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertTrue(pf.check_cache_outside_worktree(td).ok)


class TestRealPush(unittest.TestCase):
    """--dry-run reported success on this host for refs the server then
    refused at the RPC. Only a real push is evidence."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="pf-test-")
        self.addCleanup(shutil.rmtree, self.work, True)
        self._orig = pf.REPO_ROOT
        pf.REPO_ROOT = _bare_repo(self.work)
        self.addCleanup(setattr, pf, "REPO_ROOT", self._orig)

    def test_passes_against_a_reachable_remote(self):
        self.assertTrue(pf.check_durable_push_real(remote="origin").ok)

    def test_fails_against_an_unreachable_remote(self):
        subprocess.run(["git", "-C", pf.REPO_ROOT, "remote", "add", "dead",
                        "/nonexistent/x.git"], check=True)
        self.assertFalse(pf.check_durable_push_real(remote="dead").ok)


class TestPinnedSha(unittest.TestCase):
    def test_rejects_a_sha_that_is_not_a_commit(self):
        self.assertFalse(pf.check_pinned_sha("0" * 40).ok)

    def test_accepts_head(self):
        head = subprocess.run(["git", "-C", pf.REPO_ROOT, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        self.assertTrue(pf.check_pinned_sha(head).ok)


class TestVerdictAggregation(unittest.TestCase):
    def test_a_fatal_failure_blocks(self):
        rep = pf.run_preflight(source_cache="/proc/nope", skip_push=True)
        self.assertFalse(rep["ok"])
        self.assertIn("CACHE_PERSISTS", rep["blocking"])

    def test_skipping_the_push_check_is_non_fatal_but_stated(self):
        with tempfile.TemporaryDirectory() as td:
            rep = pf.run_preflight(source_cache=td, skip_push=True)
            names = {c.name: c for c in rep["checks"]}
            self.assertIn("SKIPPED", names["DURABLE_PUSH_REAL"].detail)
            self.assertFalse(names["DURABLE_PUSH_REAL"].fatal)


if __name__ == "__main__":
    unittest.main(verbosity=2)

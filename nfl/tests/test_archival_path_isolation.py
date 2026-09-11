#!/usr/bin/env python3
"""The NFL capture job cannot write outside nfl/raw/. Enforcement 6.

READ nfl/archive/commit_guard.py's docstring FIRST for what is server-enforced
versus software-guarded. Short version: this is a software guard. The Actions
token can write any path in the repository; GitHub has no path-scoped write
permission. These tests verify that the guard refuses, not that the token is
incapable.

Each test below is the MUTATION as well as the assertion: it constructs a real
git repository, actually dirties a forbidden path, and requires the guard to
refuse. A guard that has never been watched to refuse is not a guard.
"""
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from nfl.paths import (  # noqa: E402
    ALLOWED_WRITE_PREFIXES, MLB_EVIDENCE_PREFIXES, PathViolation,
    assert_only_allowed, classify, is_allowed,
)


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, check=check)


class Allowlist(unittest.TestCase):
    def test_archive_paths_are_allowed(self):
        self.assertTrue(is_allowed("nfl/raw/2026-09-13/20260913T140000Z-morning/manifest.json"))

    def test_mlb_evidence_paths_are_not_allowed(self):
        for path in ("results/grades_2026-09-10.json",
                     "data/public_top_picks/registry.json",
                     "output/board.json",
                     "docs/index.html"):
            with self.subTest(path=path):
                self.assertFalse(is_allowed(path))

    def test_nfl_source_code_is_not_writable_by_the_capture_job(self):
        """Narrower than `nfl/` on purpose: code changes need human review."""
        self.assertFalse(is_allowed("nfl/archive/capture.py"))
        self.assertFalse(is_allowed("nfl/paths.py"))

    def test_mlb_evidence_is_named_as_such_in_the_refusal(self):
        self.assertIn("MLB PUBLIC EVIDENCE",
                      classify("data/public_top_picks/registry.json"))


class GuardRefusesRealMutations(unittest.TestCase):
    """Build a throwaway repo, dirty a forbidden path, watch the guard refuse."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _git(self.tmp, "init", "-q")
        _git(self.tmp, "config", "user.email", "t@example.com")
        _git(self.tmp, "config", "user.name", "t")
        for path in ("results/grades_2026-09-10.json",
                     "data/public_top_picks/registry.json"):
            full = os.path.join(self.tmp, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as handle:
                handle.write('{"public_top_picks": [], "entries": {}}\n')
        os.makedirs(os.path.join(self.tmp, "nfl", "raw"), exist_ok=True)
        with open(os.path.join(self.tmp, "nfl", "raw", ".gitkeep"), "w"):
            pass
        _git(self.tmp, "add", "-A")
        _git(self.tmp, "commit", "-q", "-m", "base")

    def test_clean_tree_passes(self):
        self.assertEqual(assert_only_allowed(self.tmp), [])

    def test_writing_only_under_nfl_raw_passes(self):
        target = os.path.join(self.tmp, "nfl", "raw", "2026-09-13", "cap")
        os.makedirs(target)
        with open(os.path.join(target, "manifest.json"), "w") as handle:
            handle.write("{}\n")
        allowed = assert_only_allowed(self.tmp)
        self.assertTrue(allowed)
        self.assertTrue(all(p.startswith("nfl/raw/") for p in allowed))

    def test_MUTATION_modifying_the_mlb_graded_ledger_is_refused(self):
        with open(os.path.join(self.tmp, "results", "grades_2026-09-10.json"), "w") as h:
            h.write('{"public_top_picks": [{"id": "tampered"}]}\n')
        with self.assertRaises(PathViolation) as caught:
            assert_only_allowed(self.tmp)
        message = str(caught.exception)
        self.assertIn("results/grades_2026-09-10.json", message)
        self.assertIn("MLB PUBLIC EVIDENCE", message)

    def test_MUTATION_modifying_the_publication_registry_is_refused(self):
        path = os.path.join(self.tmp, "data", "public_top_picks", "registry.json")
        with open(path, "w") as handle:
            handle.write('{"entries": {}}\n')
        with self.assertRaises(PathViolation) as caught:
            assert_only_allowed(self.tmp)
        self.assertIn("data/public_top_picks/registry.json", str(caught.exception))

    def test_MUTATION_an_untracked_stray_file_is_also_refused(self):
        """A capture that wrote somewhere odd and never staged it still counts."""
        with open(os.path.join(self.tmp, "stray_output.json"), "w") as handle:
            handle.write("{}\n")
        with self.assertRaises(PathViolation) as caught:
            assert_only_allowed(self.tmp)
        self.assertIn("stray_output.json", str(caught.exception))

    def test_MUTATION_deleting_mlb_evidence_is_refused(self):
        os.remove(os.path.join(self.tmp, "results", "grades_2026-09-10.json"))
        with self.assertRaises(PathViolation) as caught:
            assert_only_allowed(self.tmp)
        self.assertIn("results/grades_2026-09-10.json", str(caught.exception))

    def test_a_good_nfl_write_beside_a_bad_mlb_write_is_still_refused(self):
        """A healthy archive must not buy a pass for a forbidden edit."""
        target = os.path.join(self.tmp, "nfl", "raw", "2026-09-13", "cap")
        os.makedirs(target)
        with open(os.path.join(target, "manifest.json"), "w") as handle:
            handle.write("{}\n")
        with open(os.path.join(self.tmp, "results", "grades_2026-09-10.json"), "w") as h:
            h.write("{}\n")
        with self.assertRaises(PathViolation):
            assert_only_allowed(self.tmp)

    def test_unreadable_repo_fails_closed(self):
        """No enumeration means no verdict. Missing evidence is never a pass."""
        with self.assertRaises(PathViolation):
            assert_only_allowed(os.path.join(self.tmp, "does-not-exist"))


class HonestyAboutEnforcement(unittest.TestCase):
    def test_guard_documents_that_it_is_not_a_github_permission(self):
        """Locks in the correction; this claim was previously stated falsely."""
        path = os.path.join(REPO, "nfl", "archive", "commit_guard.py")
        with open(path, encoding="utf-8") as handle:
            doc = handle.read()
        self.assertIn("REPOSITORY-SCOPED", doc)
        self.assertIn("no path-scoped GitHub write permission", doc)

    def test_allowlist_is_narrow(self):
        self.assertEqual(ALLOWED_WRITE_PREFIXES, ("nfl/raw/",))

    def test_mlb_estates_are_enumerated(self):
        for prefix in ("results/", "data/public_top_picks/"):
            self.assertIn(prefix, MLB_EVIDENCE_PREFIXES)


if __name__ == "__main__":
    unittest.main(verbosity=2)

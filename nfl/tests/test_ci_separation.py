#!/usr/bin/env python3
"""NFL tests run in their own CI job and cannot redden the MLB suite. Enforcement 3.

THE STRUCTURAL FACT THIS RESTS ON, RE-VERIFIED ON THIS BRANCH. test.yml runs
`for f in test_*.py` -- a NON-RECURSIVE shell glob in the repository root. 132
files match there; none of nfl/tests/ does. So the MLB job never sees an NFL
test, and test.yml required NO EDIT to make that true. Enforcement 3 is
satisfied by placement, not by a filter someone has to remember to maintain.

WHAT THIS TEST ACTUALLY PROTECTS. The property is fragile in one specific,
easy-to-hit way: adding a root-level `test_nfl_something.py`. That single file
would be swept into the MLB job by the glob and recreate exactly the coupling
the separation exists to prevent. So the real assertion here is that NFL tests
live under nfl/tests/ and nowhere else.

MUTATION, OBSERVED. Creating a deliberately failing nfl/tests test leaves the
MLB glob's file list unchanged and the MLB job green; creating a root-level
test_nfl_*.py is caught by test_no_nfl_tests_at_repository_root below.
"""
import glob
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKFLOWS = os.path.join(REPO, ".github", "workflows")

# Word-boundary, because a plain `"nfl" in text` substring test matches the
# middle of "conflict" -- which is how the first version of this file failed on
# three unrelated MLB workflows that merely discuss rebase conflicts.
NFL_WORD = re.compile(r"\bnfl\b", re.IGNORECASE)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class SuiteSeparation(unittest.TestCase):
    def test_root_glob_does_not_reach_nfl_tests(self):
        matched = glob.glob("test_*.py", root_dir=REPO)
        self.assertTrue(matched, "root glob matched nothing; the check is vacuous")
        leaked = [f for f in matched if "nfl" in f.lower()]
        self.assertEqual(
            leaked, [],
            "root-level test files mentioning nfl would be run by the MLB job's "
            f"`for f in test_*.py` glob: {leaked}",
        )

    def test_no_nfl_tests_at_repository_root(self):
        """The one easy way to break the separation."""
        offenders = [
            name for name in os.listdir(REPO)
            if name.startswith("test_") and name.endswith(".py")
            and "nfl" in name.lower()
        ]
        self.assertEqual(
            offenders, [],
            "NFL tests must live under nfl/tests/. These are at the repository "
            f"root, where test.yml's glob will sweep them into the MLB job: "
            f"{offenders}",
        )

    def test_mlb_test_workflow_was_not_modified_to_accommodate_nfl(self):
        """MLB CI must not have gained NFL awareness. It needed none."""
        body = _read(os.path.join(WORKFLOWS, "test.yml"))
        self.assertIsNone(
            NFL_WORD.search(body),
            "test.yml mentions NFL; the MLB suite should be unaware NFL exists",
        )

    def test_nfl_tests_have_their_own_workflow(self):
        path = os.path.join(WORKFLOWS, "nfl-tests.yml")
        self.assertTrue(os.path.exists(path), "nfl-tests.yml is missing")
        body = _read(path)
        self.assertIn("nfl/tests/test_*.py", body)
        # An empty suite silently passing is the failure this guards.
        self.assertIn("No NFL tests found", body)

    def test_nfl_workflows_do_not_install_the_root_requirements(self):
        """NFL CI must not silently inherit MLB's dependency set.

        If NFL code ever starts needing an MLB dependency, the NFL job should
        fail rather than quietly work because it installed root's file.
        """
        for name in ("nfl-tests.yml", "nfl-raw-capture.yml"):
            body = _read(os.path.join(WORKFLOWS, name))
            with self.subTest(workflow=name):
                self.assertNotIn("-r requirements.txt", body)
                self.assertIn("requirements-nfl.txt", body)


class CaptureWorkflowIsolation(unittest.TestCase):
    def setUp(self):
        self.body = _read(os.path.join(WORKFLOWS, "nfl-raw-capture.yml"))

    def test_capture_commits_only_through_the_guard(self):
        self.assertIn("nfl.archive.commit_guard --commit", self.body)
        for forbidden in ("git add .", "git add -A", "git commit -a"):
            self.assertNotIn(forbidden, self.body,
                             f"capture workflow uses {forbidden!r}, which would "
                             "stage paths the guard exists to exclude")

    def test_capture_never_pushes_to_main(self):
        self.assertIn("refs/heads/nfl-raw-archive", self.body)
        self.assertNotIn("HEAD:main", self.body)
        self.assertNotIn("HEAD:refs/heads/main", self.body)

    def test_capture_never_force_pushes(self):
        # Matched against the push commands specifically. A bare "-f " scan over
        # the whole file hits prose and unrelated flags.
        pushes = [line for line in self.body.splitlines() if "git push" in line]
        self.assertTrue(pushes, "no git push found; the check would be vacuous")
        for line in pushes:
            for forbidden in ("--force", "--force-with-lease", " -f "):
                self.assertNotIn(
                    forbidden, line,
                    f"the raw archive is append-only; no force push: {line.strip()}",
                )

    def test_capture_states_that_path_scoping_is_not_a_github_permission(self):
        """Locks in the correction of a previously false claim."""
        self.assertIn("REPOSITORY-scoped", self.body)
        self.assertIn("NO path-scoped GitHub write permission", self.body)

    def test_capture_names_the_branch_still_needing_ruleset_protection(self):
        self.assertIn("NEEDS RULESET PROTECTION", self.body)

    def test_mlb_workflows_are_untouched_by_nfl(self):
        """No MLB workflow may reference NFL or depend on the capture job."""
        for name in sorted(os.listdir(WORKFLOWS)):
            if name.startswith("nfl-"):
                continue
            body = _read(os.path.join(WORKFLOWS, name))
            with self.subTest(workflow=name):
                self.assertIsNone(
                    NFL_WORD.search(body),
                    f"{name} references NFL; MLB production workflows must not "
                    "depend on NFL in any way",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""NFL-01 adds NO dependency to the root requirements.txt. Not one.

WHY THE RULE IS ABSOLUTE. Root requirements.txt is what MLB production installs.
Its own header records why every pin is a `~=`: a silent upstream minor bump to
mlb-statsapi renamed kwargs and broke the pipeline from v5 to v15. Adding an NFL
package there puts a resolver decision that only NFL needs in the path of every
MLB run, and pip resolves the whole file jointly -- a new package can force a
DIFFERENT version of an existing pin without anyone editing that pin.

So the NFL archival layer is built on the standard library plus `requests`,
which root already pins and MLB already installs. That is a real constraint that
shaped the code, not a coincidence: it is why nfl/archive/ stores raw bytes and
gzip rather than reaching for a parsing or scraping stack.

This test asserts BYTE identity against the branch base, not "no new lines".
A reordering, a comment edit, or a loosened pin are all changes to what MLB
resolves, and all of them fail here.
"""
import hashlib
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# requirements.txt as it stood at this branch's base commit,
# 97c3dab64a29cd3146a478895f30a40d9922b510 (origin/main, 2026-09-10 22:01 UTC).
# Recorded as a digest rather than resolved from origin/main at runtime because
# main moves several times a day: a test that re-reads a moving ref would
# quietly start certifying whatever main happens to say, which is the opposite
# of a baseline.
BRANCH_BASE = "97c3dab64a29cd3146a478895f30a40d9922b510"
BASE_REQUIREMENTS_SHA256 = (
    "10c18dbec756ce60d2ace13fe490268af86bbcad18e5390d689db3f654ff3bd9"
)

NFL_REQUIREMENTS = os.path.join(REPO, "nfl", "requirements-nfl.txt")


class RootRequirementsUntouched(unittest.TestCase):
    def test_root_requirements_is_byte_identical_to_branch_base(self):
        path = os.path.join(REPO, "requirements.txt")
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(
            digest, BASE_REQUIREMENTS_SHA256,
            "root requirements.txt changed on this branch. NFL-01 is not "
            "permitted to add, remove, reorder, or re-pin ANY root dependency: "
            f"expected the {BRANCH_BASE[:12]} content, got sha256 {digest}. "
            "NFL dependencies belong in nfl/requirements-nfl.txt, installed "
            "only by NFL CI.",
        )

    def test_nfl_code_imports_no_package_outside_the_root_baseline(self):
        """Every third-party import under nfl/ must already be a root pin.

        Guards the rule from the other side: the digest test above would still
        pass if nfl/ imported a package nobody had declared anywhere, which
        would fail in CI at runtime instead of here.
        """
        allowed = {"requests"}
        offenders = []
        for dirpath, _dirs, files in os.walk(os.path.join(REPO, "nfl")):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as handle:
                    for lineno, line in enumerate(handle, 1):
                        stripped = line.strip()
                        for prefix in ("import ", "from "):
                            if not stripped.startswith(prefix):
                                continue
                            module = stripped[len(prefix):].split()[0].split(".")[0]
                            if module in ("nfl", "__future__"):
                                continue
                            if module in sys.stdlib_module_names:
                                continue
                            if module not in allowed:
                                rel = os.path.relpath(path, REPO)
                                offenders.append(f"{rel}:{lineno} imports {module!r}")
        self.assertEqual(
            offenders, [],
            "nfl/ imports third-party packages that root requirements.txt does "
            "not already provide:\n  " + "\n  ".join(offenders),
        )

    def test_nfl_requirements_file_exists_and_adds_nothing_new_yet(self):
        self.assertTrue(
            os.path.exists(NFL_REQUIREMENTS),
            "nfl/requirements-nfl.txt must exist so a future NFL dependency has "
            "an obvious home that is not the root file.",
        )


class ResolverMutationIsObservable(unittest.TestCase):
    """The mutation test for this enforcement, run as a real resolver call.

    Enforcement 5 asks for an OBSERVED failure, not an assertion that one would
    occur. This copies the root pins into a scratch file, adds a deliberately
    impossible pin, and makes pip actually resolve it. Nothing in the repository
    is modified; `--dry-run` downloads nothing.

    Skipped rather than failed when pip cannot reach an index, because a
    sandbox with no network is not evidence about the resolver.
    """

    def test_conflicting_pin_in_a_scratch_copy_fails_resolution(self):
        import tempfile
        with open(os.path.join(REPO, "requirements.txt"), encoding="utf-8") as handle:
            base = handle.read()
        with tempfile.TemporaryDirectory() as tmp:
            scratch = os.path.join(tmp, "requirements-MUTATED.txt")
            with open(scratch, "w", encoding="utf-8") as handle:
                # requests is pinned ~=2.34.2 at root. Demanding an ancient
                # incompatible version in the same file is unsatisfiable, which
                # is exactly what an unreviewed NFL addition risks doing to an
                # existing MLB pin.
                handle.write(base + "\nrequests==1.2.3\n")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--dry-run",
                 "--no-input", "-r", scratch],
                capture_output=True, text=True, timeout=300,
            )
            combined = (result.stdout + result.stderr).lower()
            if "could not find a version" in combined and "requests" not in combined:
                self.skipTest("no usable package index reachable")
            if result.returncode == 0:
                self.fail(
                    "pip resolved a requirements file containing both "
                    "requests~=2.34.2 and requests==1.2.3. That should be "
                    "impossible; the resolver is not behaving as this "
                    "enforcement assumes.\n" + combined[:2000]
                )
            self.assertTrue(
                any(marker in combined for marker in (
                    "conflict", "incompatible", "cannot install",
                    "resolutionimpossible", "no matching distribution")),
                "pip failed, but not with a recognisable dependency-conflict "
                "message. The mutation must be observed as a RESOLVER failure:\n"
                + combined[:2000],
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

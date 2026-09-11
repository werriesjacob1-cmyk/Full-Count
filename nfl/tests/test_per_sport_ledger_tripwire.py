#!/usr/bin/env python3
"""Identity monotonicity works PER SPORT. Deliverable 2.

Two things must both hold, and the second is the one that is easy to get wrong:

  1. A lost NFL identity FAILS.
  2. A healthy MLB estate DOES NOT MASK IT. 270 intact MLB identities beside one
     lost NFL identity must still fail, and the failure must name NFL.

Pooling the two estates into a single identity set is what would break (2):
against a large healthy set, one missing row looks like noise. So the estates
are compared independently and reported per sport.

Also asserted here: the historical MLB incident shape still behaves exactly as
before. On 2026-09-03 a force-push cost 12 identities from the graded ledger and
6 from the publication registry -- DIFFERENT amounts, which is why the estates
are compared separately rather than pooled. That detection must be unchanged by
the NFL extension, and byte parity of the MLB code paths was verified separately
against real refs before and after.

Every test builds a real throwaway git repository and runs the real
ledger_integrity.py against it as a subprocess, so nothing here depends on
importing internals that could drift.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHECKER = os.path.join(REPO, "ledger_integrity.py")

MLB_ID = "fc2:{game}:player-{p}:hits:1:over"
NFL_ID = "fcnfl1:nflverse:2026_01_TB_CIN:player-{p}:receiving_yards:64.5:over"


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args],
                          capture_output=True, text=True, check=True)


def commit_all(repo, message):
    """Commit, allowing an empty commit.

    --allow-empty because some fixtures below deliberately re-write byte-identical
    content to produce two refs with the SAME estate, and git refuses an empty
    commit by default. This is a throwaway fixture repository, not a way to kick
    CI.
    """
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


class PerSportTripwire(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        shutil.copy(CHECKER, os.path.join(self.repo, "ledger_integrity.py"))

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    # -- fixture helpers ---------------------------------------------------

    def put_mlb(self, n=270):
        write_json(os.path.join(self.repo, "results", "grades_2026-09-01.json"),
                   {"public_top_picks": [{"id": MLB_ID.format(game=1, p=i)}
                                         for i in range(n)]})
        write_json(os.path.join(self.repo, "data", "public_top_picks", "registry.json"),
                   {"entries": {MLB_ID.format(game=1, p=i): {} for i in range(n)}})

    def put_nfl(self, n=3):
        write_json(os.path.join(self.repo, "nfl", "results", "grades_2026-09-13.json"),
                   {"public_top_picks": [{"id": NFL_ID.format(p=i)} for i in range(n)]})
        write_json(os.path.join(self.repo, "nfl", "data", "public_top_picks",
                                "registry.json"),
                   {"entries": {NFL_ID.format(p=i): {} for i in range(n)}})

    def commit(self, message):
        return commit_all(self.repo, message)

    def check(self, before, after):
        result = subprocess.run([sys.executable, "ledger_integrity.py", before, after],
                                cwd=self.repo, capture_output=True, text=True)
        return result.returncode, result.stdout + result.stderr

    # -- the tests ---------------------------------------------------------

    def test_no_nfl_estate_at_either_ref_is_not_a_failure(self):
        """NFL estates do not exist yet. That must not fail the live MLB check."""
        self.put_mlb()
        a = self.commit("mlb only")
        self.put_mlb()
        b = self.commit("mlb only again")
        code, out = self.check(a, b)
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)

    def test_healthy_both_sports_passes(self):
        self.put_mlb()
        self.put_nfl()
        a = self.commit("both")
        with open(os.path.join(self.repo, "note.txt"), "w") as fh:
            fh.write("unrelated\n")
        b = self.commit("unrelated change")
        code, out = self.check(a, b)
        self.assertEqual(code, 0, out)

    def test_MUTATION_a_lost_nfl_identity_fails(self):
        self.put_mlb()
        self.put_nfl(3)
        a = self.commit("both healthy")
        self.put_nfl(2)          # one NFL identity disappears
        b = self.commit("nfl identity dropped")
        code, out = self.check(a, b)
        self.assertEqual(code, 1, out)
        self.assertIn("[NFL]", out)
        self.assertIn(NFL_ID.format(p=2), out)

    def test_MUTATION_healthy_mlb_does_not_mask_a_lost_nfl_identity(self):
        """THE point of per-sport comparison. 270 intact MLB rows must not help."""
        self.put_mlb(270)
        self.put_nfl(3)
        a = self.commit("both healthy")
        self.put_mlb(280)        # MLB grows and is perfectly healthy
        self.put_nfl(2)          # a single NFL identity vanishes
        b = self.commit("mlb grows, nfl loses one")
        code, out = self.check(a, b)
        self.assertEqual(code, 1, out)
        self.assertIn("[NFL]", out)
        self.assertNotIn("[MLB]", out)
        self.assertIn("1 lost", out)

    def test_MUTATION_the_whole_nfl_estate_vanishing_fails(self):
        """Deleting an established estate must be louder than losing one row."""
        self.put_mlb()
        self.put_nfl(3)
        a = self.commit("both healthy")
        shutil.rmtree(os.path.join(self.repo, "nfl", "results"))
        os.remove(os.path.join(self.repo, "nfl", "data", "public_top_picks",
                               "registry.json"))
        b = self.commit("nfl estate deleted")
        code, out = self.check(a, b)
        self.assertEqual(code, 1, out)
        self.assertIn("ABSENT", out)

    def test_MUTATION_cross_sport_contamination_fails(self):
        """An NFL id in the MLB estate means a writer crossed the partition."""
        self.put_mlb(5)
        a = self.commit("clean")
        write_json(os.path.join(self.repo, "results", "grades_2026-09-01.json"),
                   {"public_top_picks": [{"id": MLB_ID.format(game=1, p=i)}
                                         for i in range(5)]
                                        + [{"id": NFL_ID.format(p=0)}]})
        b = self.commit("nfl id written into the MLB ledger")
        code, out = self.check(a, b)
        self.assertEqual(code, 1, out)
        self.assertIn("another sport's identities", out)
        self.assertIn(NFL_ID.format(p=0), out)


class HistoricalMLBIncidentStillDetected(unittest.TestCase):
    """The 2026-09-03 shape: 12 graded identities lost and 6 registry ones.

    DIFFERENT amounts, which is the reason the estates are compared separately.
    This must behave exactly as it did before the NFL extension.
    """

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        shutil.copy(CHECKER, os.path.join(self.repo, "ledger_integrity.py"))

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _commit(self, graded_n, registry_n, message):
        write_json(os.path.join(self.repo, "results", "grades_2026-09-01.json"),
                   {"public_top_picks": [{"id": MLB_ID.format(game=1, p=i)}
                                         for i in range(graded_n)]})
        write_json(os.path.join(self.repo, "data", "public_top_picks", "registry.json"),
                   {"entries": {MLB_ID.format(game=1, p=i): {}
                                for i in range(registry_n)}})
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", message)
        return git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def test_twelve_graded_and_six_registry_losses_are_both_reported(self):
        a = self._commit(100, 100, "healthy")
        b = self._commit(88, 94, "force-push aftermath")
        result = subprocess.run([sys.executable, "ledger_integrity.py", a, b],
                                cwd=self.repo, capture_output=True, text=True)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn("12 lost", out)
        self.assertIn("6 lost", out)
        self.assertIn("graded ledger", out)
        self.assertIn("publication registry", out)
        self.assertIn("restore the commits that carried it", out)

    def test_a_missing_mlb_estate_still_fails_closed(self):
        """MLB estates stay REQUIRED; their absence is the incident."""
        a = self._commit(10, 10, "healthy")
        os.remove(os.path.join(self.repo, "data", "public_top_picks", "registry.json"))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "registry deleted")
        b = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        result = subprocess.run([sys.executable, "ledger_integrity.py", a, b],
                                cwd=self.repo, capture_output=True, text=True)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn("cannot verify the public estate", out)

    def test_substitution_is_caught_because_identities_not_counts_are_compared(self):
        """Same total, different rows: a count check would pass this."""
        a = self._commit(10, 10, "healthy")
        write_json(os.path.join(self.repo, "results", "grades_2026-09-01.json"),
                   {"public_top_picks": [{"id": MLB_ID.format(game=1, p=i)}
                                         for i in range(1, 11)]})
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "one swapped, same count")
        b = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        result = subprocess.run([sys.executable, "ledger_integrity.py", a, b],
                                cwd=self.repo, capture_output=True, text=True)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn("1 lost", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""The source artifact must survive a container reclamation, provably.

Run canonical-20260827T232203Z-cfb15819 died on 2026-08-28 with 142 dates
and 295,999 rows safely on the durable branch -- and was still permanently
unrecoverable, because /root/.fc-statcast-cache lived in the container and
went with it. Durable rows plus an ephemeral source is not a durable run:
the rows survive and can never be extended, since extending them under a
different artifact is precisely what the source-identity gate refuses.

These tests run END TO END against a real local bare remote -- real push,
real fetch, real bytes -- rather than mocking git. The first version of
this work passed a mock-shaped check and still failed for real, because
durable_source_artifact() resolved f"{remote}/{branch}" while
_read_durable_blob() resolved remote-then-local: the index was found and
its artifact was looked for on a ref that did not exist. Recovery is the
worst place for that kind of disagreement.
"""
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import canonical_durability as cd

RUN = "e2e-src-durability"
BRANCH = "fc-test-src-durability"


class SourceArtifactDurabilityE2E(unittest.TestCase):
    """One real push/reclaim/restore cycle, asserted from every angle."""

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(prefix="fc-e2e-src-")
        bare = os.path.join(cls.work, "remote.git")
        cls.repo = os.path.join(cls.work, "repo")
        cls.cache = os.path.join(cls.work, "cache")
        cls.run_dir = os.path.join(cls.work, "run")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", cls.repo], check=True)
        with open(os.path.join(cls.repo, "seed"), "w") as fh:
            fh.write("x")
        for args in (["add", "-A"], ["commit", "-qm", "seed"]):
            subprocess.run(["git", "-C", cls.repo] + args, check=True, env=env)
        subprocess.run(["git", "-C", cls.repo, "remote", "add", "origin", bare], check=True)
        subprocess.run(["git", "-C", cls.repo, "push", "-q", "-u", "origin", "main"],
                       check=True, env=env)

        os.makedirs(cls.cache)
        os.makedirs(os.path.join(cls.run_dir, "checkpoints"))
        # Incompressible bytes, multi-MB: a parquet, not a toy string.
        random.seed(11)
        cls.blob = bytes(random.getrandbits(8) for _ in range(2_000_000))
        cls.art = os.path.join(cls.cache, "statcast_2024_through_2026-08-24.parquet")
        with open(cls.art, "wb") as fh:
            fh.write(cls.blob)
        cls.sha = hashlib.sha256(cls.blob).hexdigest()

        cls.manifest = {"run_id": RUN, "requested_start_date": "2024-04-01",
                        "requested_end_date": "2024-04-02",
                        "code_git_sha": "deadbeef", "created_at": cd._now_iso()}
        with open(os.path.join(cls.run_dir, "manifest.json"), "w") as fh:
            json.dump(cls.manifest, fh)
        d = "2024-04-01"
        with open(os.path.join(cls.run_dir, "checkpoints", f"{d}.jsonl"), "w") as fh:
            fh.write(json.dumps({"date": d, "outcome": 1}) + "\n")
        with open(os.path.join(cls.run_dir, "checkpoints", f"{d}.meta.json"), "w") as fh:
            json.dump({"date": d, "status": "ok", "row_count": 1}, fh)
        cls.lineage = [{"source": "statcast_leaguewide", "content_sha256": cls.sha,
                        "row_count": 123, "date_coverage": "2024-03-15..2026-08-24",
                        "request_identity": "statcast:2024:2026-08-24"}]
        cls.K = dict(branch=BRANCH, remote="origin", repo_root=cls.repo)

        cls.first = cd.push_durable_checkpoint(
            cls.run_dir, cls.manifest, dates=[d], lineage=cls.lineage,
            cache_mode="fresh_source", source_artifact=cls.art, **cls.K)
        cls.second = cd.push_durable_checkpoint(
            cls.run_dir, cls.manifest, dates=[d], lineage=cls.lineage,
            cache_mode="fresh_source", source_artifact=cls.art, **cls.K)
        # THE RECLAMATION.
        shutil.rmtree(cls.cache)
        subprocess.run(["git", "-C", cls.repo, "fetch", "-q", "origin", BRANCH], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_artifact_is_pushed_with_its_bound_hash(self):
        self.assertTrue(self.first["pushed"], self.first.get("reason"))
        self.assertTrue(self.first["source_artifact_written"])
        self.assertEqual(self.first["source_artifact_bytes"], len(self.blob))
        self.assertEqual(self.first["source_artifact_sha256"], self.sha)

    def test_artifact_is_written_once_not_once_per_push(self):
        """The bound sha cannot change within a run, so rewriting it would
        grow the branch by 23MB per push for no information."""
        self.assertFalse(self.second["source_artifact_written"])
        self.assertTrue(self.second["source_artifact_skipped_present"])

    def test_resume_is_feasible_after_the_cache_is_destroyed(self):
        # Its own empty dir: other tests in this class restore INTO
        # self.cache, and unittest orders by name, so sharing it would make
        # this assertion depend on which test ran first.
        empty = tempfile.mkdtemp(prefix="fc-e2e-empty-")
        self.addCleanup(shutil.rmtree, empty, True)
        fz = cd.resume_feasibility(RUN, cache_dir=empty, **self.K)
        self.assertTrue(fz["resumable"])
        self.assertTrue(fz["artifact_on_branch"])
        self.assertFalse(fz["artifact_on_disk"])
        self.assertEqual(fz["bound_sha256"], self.sha)

    def test_restore_returns_byte_identical_bytes(self):
        rep = cd.restore_source_artifact(RUN, self.cache, **self.K)
        self.assertTrue(rep["restored"])
        with open(rep["path"], "rb") as fh:
            self.assertEqual(fh.read(), self.blob)
        self.assertEqual(cd._sha256_file(rep["path"]), self.sha)

    def test_a_corrupted_on_disk_artifact_is_refused(self):
        rep = cd.restore_source_artifact(RUN, self.cache, **self.K)
        with open(rep["path"], "r+b") as fh:
            fh.seek(500)
            fh.write(b"\x00\x01\x02")
        with self.assertRaises(cd.SourceVintageMismatch):
            cd.restore_source_artifact(RUN, self.cache, **self.K)
        os.remove(rep["path"])

    def test_a_run_with_no_artifact_cannot_be_restored(self):
        with self.assertRaises(cd.SourceArtifactUnavailable):
            cd.restore_source_artifact("ghost-run", self.cache, **self.K)

    def test_an_unknown_run_is_reported_unresumable_not_guessed(self):
        fz = cd.resume_feasibility("ghost-run", cache_dir=self.cache, **self.K)
        self.assertFalse(fz["resumable"])
        self.assertTrue(fz["blocker"])


class RefResolutionAgreement(unittest.TestCase):
    """Both readers must resolve the SAME ref. They once did not."""

    def test_index_reader_and_artifact_reader_share_resolution(self):
        import inspect
        src = inspect.getsource(cd.durable_source_artifact)
        self.assertIn("_resolve_durable_ref", src,
                      "durable_source_artifact must not hardcode a ref; it "
                      "diverged from _read_durable_blob once already")
        src2 = inspect.getsource(cd._read_durable_blob)
        self.assertIn("_resolve_durable_ref", src2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""FC-MLB-001B drill transport binding (Codex pre-drill audit MUST FIX 1 + 2). Synthetic GitHub API; no network."""
import gzip
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import b_artifacts as BA  # noqa: E402
import tape_store as TS  # noqa: E402

REPO = "werriesjacob1-cmyk/Full-Count"
RUN, COMMIT = 37400000001, "a" * 40


def zbytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, b in files.items():
            zf.writestr(n, b)
    return buf.getvalue()


class Binding(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="bart_")
        self.tape = gzip.compress(os.urandom(200_000), mtime=0)
        self.tsha = hashlib.sha256(self.tape).hexdigest()
        self.tname = f"fc-mlb-001b-drill-tape-{self.tsha}.json.gz"
        self.zips = {"files": zbytes({"B_DRILL.json": b"{}", "SHA256SUMS": b"x"}), "tape": zbytes({self.tname: self.tape})}
        self.ids = {"files": 9001, "tape": 9002}
        self.names = {"files": "fc-mlb-001b-drill-files-B_DRILL_20261006_NIGHT", "tape": "tape-B_DRILL_20261006_NIGHT"}
        hexd = {k: hashlib.sha256(v).hexdigest() for k, v in self.zips.items()}
        # frozen representation: bare lowercase hex (actions/upload-artifact `artifact-digest` output)
        self.rec = {"repository": REPO, "record_run_id": RUN, "record_commit": COMMIT, "drill": "B_DRILL_20261006_NIGHT",
                    "files_artifact": {"name": self.names["files"], "id": self.ids["files"], "zip_sha256": hexd["files"]},
                    "tape": {"artifact_name": self.names["tape"], "artifact_id": self.ids["tape"],
                             "zip_sha256": hexd["tape"], "asset_name": self.tname, "sha256": self.tsha,
                             "bytes": len(self.tape)}}
        # GitHub's artifact API reports `sha256:<hex>`
        self.arts = {self.ids[k]: {"id": self.ids[k], "name": self.names[k], "expired": False,
                                   "digest": f"sha256:{hexd[k]}", "expires_at": "2027-01-04T19:00:00Z",
                                   "workflow_run": {"id": RUN, "head_sha": COMMIT, "head_branch": "x"}}
                     for k in ("files", "tape")}
        self.run = {"id": RUN, "head_sha": COMMIT, "repository": {"full_name": REPO}}
        self.served = {self.ids[k]: self.zips[k] for k in ("files", "tape")}

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def get_json(self, url, token):
        if "/actions/runs/" in url:
            if int(url.rsplit("/", 1)[1]) != self.run["id"]:
                raise BA.ArtifactBindingError("GitHub API 404")
            return self.run
        aid = int(url.rsplit("/", 1)[1])
        if aid not in self.arts:
            raise BA.ArtifactBindingError("GitHub API 404")
        return self.arts[aid]

    def download(self, url, token, dest):
        aid = int(url.split("/artifacts/")[1].split("/")[0])
        if aid not in self.served:
            raise BA.ArtifactBindingError("artifact download 404")
        open(dest, "wb").write(self.served[aid])
        return dest

    def fetch(self, kind, rec=None):
        return BA.fetch_bound(rec or self.rec, kind, "tok", REPO, os.path.join(self.d, f"{kind}.zip"),
                              self.get_json, self.download)

    def assertFails(self, kind="tape", rec=None, msg=None):
        with self.assertRaisesRegex(BA.ArtifactBindingError, msg or ""):
            self.fetch(kind, rec)

    # MUST FIX 1 -- one frozen digest representation
    def test_digest_normalization(self):
        h = "ab" * 32
        self.assertEqual(BA.normalize_digest(h), h)
        self.assertEqual(BA.normalize_digest("sha256:" + h), h)
        self.assertEqual(BA.normalize_digest("SHA256:" + h.upper()), h)
        for bad in ("", None, "sha256:", "md5:" + h, h[:-1], h + "0", "sha256:" + "g" * 64):
            with self.assertRaises(BA.ArtifactBindingError):
                BA.normalize_digest(bad)

    def test_valid_binding_passes_for_both_artifacts(self):
        for kind in ("files", "tape"):
            s = self.fetch(kind)
            self.assertEqual((s["artifact_id"], s["record_run_id"], s["record_commit"], s["repository"]),
                             (self.ids[kind], RUN, COMMIT, REPO))
            self.assertEqual(s["zip_sha256"], hashlib.sha256(self.zips[kind]).hexdigest())

    def test_prefix_convention_never_creates_a_false_mismatch(self):
        rec = json.loads(json.dumps(self.rec))
        rec["tape"]["zip_sha256"] = "sha256:" + rec["tape"]["zip_sha256"]          # committed with prefix
        self.fetch("tape", rec)
        self.arts[self.ids["tape"]]["digest"] = self.rec["tape"]["zip_sha256"]      # API without prefix
        self.fetch("tape")
        self.fetch("tape", rec)

    def test_one_byte_zip_corruption_fails(self):
        b = bytearray(self.zips["tape"])
        b[len(b) // 2] ^= 1
        self.served[self.ids["tape"]] = bytes(b)
        self.assertFails(msg="zip digest")

    # MUST FIX 2 -- provenance binding
    def test_wrong_artifact_name_fails(self):
        self.arts[self.ids["tape"]]["name"] = "tape-B_DRILL_20261005_NIGHT"
        self.assertFails(msg="artifact name")
        rec = json.loads(json.dumps(self.rec))
        rec["files_artifact"]["name"] = "other"
        self.assertFails("files", rec, "artifact name")

    def test_wrong_record_run_id_fails(self):
        self.arts[self.ids["tape"]]["workflow_run"]["id"] = RUN + 1             # artifact from another run
        self.assertFails(msg="workflow_run.id")
        self.arts[self.ids["tape"]]["workflow_run"]["id"] = RUN
        rec = dict(self.rec, record_run_id=RUN + 1)                              # committed record names another run
        self.run["id"] = RUN + 1
        self.assertFails(rec=rec, msg="workflow_run.id")

    def test_wrong_record_commit_fails(self):
        self.arts[self.ids["files"]]["workflow_run"]["head_sha"] = "b" * 40
        self.assertFails("files", msg="workflow_run.head_sha")
        self.arts[self.ids["files"]]["workflow_run"]["head_sha"] = COMMIT
        self.run["head_sha"] = "b" * 40
        self.assertFails("files", msg="run head_sha")
        self.run["head_sha"] = COMMIT
        self.assertFails("files", dict(self.rec, record_commit="c" * 40), "head_sha")

    def test_wrong_repository_fails(self):
        self.run["repository"]["full_name"] = "someone/fork"
        self.assertFails(msg="run repository")
        self.run["repository"]["full_name"] = REPO
        self.assertFails(rec={k: v for k, v in self.rec.items() if k != "repository"}, msg="repository")

    def test_wrong_zip_digest_fails(self):
        rec = json.loads(json.dumps(self.rec))
        rec["tape"]["zip_sha256"] = "0" * 64
        self.assertFails(rec=rec, msg="digest")
        self.arts[self.ids["files"]]["digest"] = "sha256:" + "1" * 64            # API metadata disagrees
        self.assertFails("files", msg="metadata digest")

    def test_wrong_artifact_id_and_missing_artifact_fail(self):
        self.arts[self.ids["tape"]]["id"] = 1
        self.assertFails(msg="artifact id")
        del self.arts[self.ids["tape"]]
        self.assertFails(msg="404")
        self.setUp()
        del self.served[self.ids["tape"]]                                        # metadata present, bytes gone
        self.assertFails(msg="download 404")

    def test_expired_artifact_fails(self):
        self.arts[self.ids["tape"]]["expired"] = True
        self.assertFails(msg="expired")
        del self.arts[self.ids["tape"]]["expired"]                               # unknown availability -> fail
        self.assertFails(msg="expired")

    def test_one_byte_tape_corruption_still_fails(self):
        """A corrupted tape inside a self-consistent zip (metadata digest matching the corrupted zip) is still
        rejected: by the committed zip digest here, and by the sealed tape size + sha256 afterwards."""
        bad = bytearray(self.tape)
        bad[1000] ^= 1
        z = zbytes({self.tname: bytes(bad)})
        self.served[self.ids["tape"]] = z
        self.arts[self.ids["tape"]]["digest"] = "sha256:" + hashlib.sha256(z).hexdigest()
        self.assertFails(msg="digest")
        p = os.path.join(self.d, "t.json.gz")
        open(p, "wb").write(bytes(bad))
        with self.assertRaises(TS.TapeHashMismatch):
            TS._verify(p, self.tsha, len(self.tape))


if __name__ == "__main__":
    unittest.main()

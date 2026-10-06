#!/usr/bin/env python3
"""TEMPORARY Actions-artifact tape store + retention monitor + byte-preserving R2 migration (Jacob, 2026-10-06).

Synthetic fixtures only: a fake GitHub artifacts API (injected), the mock S3 server from test_b, and a synthetic
evidence chain. Every mutant must fail closed; nothing silently falls back to another source; migration never
changes a sealed byte."""
import gzip
import hashlib
import http.server
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from unittest import mock

import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import artifact_api as AA  # noqa: E402
import capture as CP  # noqa: E402
import retention_monitor as RM  # noqa: E402
import runner as RN  # noqa: E402
import schedule_plan as SP  # noqa: E402
import tape_migration as MG  # noqa: E402
import tape_store as TS  # noqa: E402
import verify_evidence as VE  # noqa: E402
from test_b import _S3  # noqa: E402

REPO = "werriesjacob1-cmyk/Full-Count"
T0 = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)


def zip_of(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, b in files.items():
            zf.writestr(n, b)
    return buf.getvalue()


class FakeGitHub:
    """Artifacts API of one run. `zips[id]` are the bytes served for that artifact's download."""

    def __init__(self):
        self.arts, self.zips = [], {}

    def add(self, aid, name, zbytes, expired=False, created=T0, retention=90, digest=None):
        self.arts.append({"id": aid, "name": name, "expired": expired, "size_in_bytes": len(zbytes),
                          "digest": digest or "sha256:" + hashlib.sha256(zbytes).hexdigest(),
                          "created_at": created.isoformat().replace("+00:00", "Z"),
                          "expires_at": (created + timedelta(days=retention)).isoformat().replace("+00:00", "Z")})
        self.zips[aid] = zbytes

    def get_json(self, url, token):
        assert token == "tok"
        return {"artifacts": [a for a in self.arts if f"name={a['name']}" in url]}

    def download(self, url, token, dest):
        aid = int(url.split("/artifacts/")[1].split("/")[0])
        if aid not in self.zips:
            raise TS.TapeMissing("404")
        open(dest, "wb").write(self.zips[aid])
        return dest


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="tmpstore_")
        self.tape = os.path.join(self.d, "tape.json.gz")
        open(self.tape, "wb").write(gzip.compress(os.urandom(300_000), mtime=0))
        self.sha, self.n = TS.file_identity(self.tape)
        self.stage = TS.ActionsArtifactStage(os.path.join(self.d, "stage"), REPO, 4242, 1, 90)
        self.loc = self.stage.stage(self.tape, self.sha, self.n)
        self.gh = FakeGitHub()
        self.tape_bytes = open(self.tape, "rb").read()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def upload(self, aid=900, files=None, **kw):
        self.gh.add(aid, self.loc["artifact_name"], zip_of(files or {self.loc["file_name"]: self.tape_bytes}), **kw)

    def fetch(self, loc=None):
        return AA.fetch(loc or self.loc, "tok", os.path.join(self.d, "dl"), self.gh.get_json, self.gh.download)


class Stage(Base):
    def test_locator_binds_identity_and_names(self):
        self.assertEqual((self.loc["store"], self.loc["storage_contract"]), (TS.GHA_KIND, TS.GHA_CONTRACT))
        self.assertEqual(self.loc["artifact_name"], f"v3-tape-{self.sha}")
        self.assertEqual(self.loc["file_name"], f"fc-v3-tape-{self.sha}.json.gz")
        self.assertEqual((self.loc["key"], self.loc["bytes"], self.loc["retention_days"]), (TS.key_for(self.sha), self.n, 90))
        self.assertFalse(self.loc["verified_readback"])                 # staging is NOT proof
        self.assertIn("TEMPORARY", self.loc["durability"])
        self.assertEqual(os.listdir(os.path.join(self.d, "stage")), [self.loc["file_name"]])

    def test_stage_refusals(self):
        with self.assertRaises(TS.StoreError):                           # exactly one tape per artifact
            self.stage.stage(self.tape, self.sha, self.n)
        with self.assertRaises(TS.TapeHashMismatch):
            TS.ActionsArtifactStage(os.path.join(self.d, "s2"), REPO, 1, 1, 90).stage(self.tape, "0" * 64, self.n)
        with self.assertRaises(TS.StoreError):                           # no run identity / retention -> refuse
            TS.ActionsArtifactStage(os.path.join(self.d, "s3"), REPO, None, 1, 90)

    def test_runner_store_selection_and_failures_are_miss_units(self):
        env = {"V3B_TAPE_STORE": "actions-artifact", "V3B_ARTIFACT_STAGE_DIR": os.path.join(self.d, "s4"),
               "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": "77", "GITHUB_RUN_ATTEMPT": "1",
               "V3B_ARTIFACT_RETENTION_DAYS": "90"}
        with mock.patch.dict(os.environ, env):
            loc = RN._store_tape("prospective", self.tape, self.sha, self.n)
            self.assertEqual((loc["store"], loc["run_id"], loc["write_status"]), (TS.GHA_KIND, 77, "STAGED_FOR_UPLOAD"))
        with mock.patch.dict(os.environ, dict(env, GITHUB_RUN_ID="")), self.assertRaises(SP.MissUnit):
            RN._store_tape("prospective", self.tape, self.sha, self.n)
        with mock.patch.dict(os.environ, {"V3B_TAPE_STORE": "localfs:/tmp/x"}), self.assertRaises(SP.MissUnit):
            RN._store_tape("prospective", self.tape, self.sha, self.n)  # never a local store for prospective


class Fetch(Base):
    def test_exact_fetch_and_confirm_record(self):
        self.upload(created=T0, retention=90)
        p, a = self.fetch()
        self.assertEqual(TS.file_identity(p), (self.sha, self.n))
        out = os.path.join(self.d, "tape_artifact.json")
        rec = AA.confirm_upload(self.loc, "tok", out, self.gh.get_json, self.gh.download, now=T0)
        self.assertEqual(json.load(open(out)), rec)
        self.assertEqual((rec["artifact_id"], rec["sha256"], rec["bytes"], rec["verified_readback"]), (900, self.sha, self.n, True))
        self.assertEqual((rec["retention_days_configured"], rec["expires_at"]), (90, "2027-01-05T00:00:00Z"))

    def test_mutants_fail_closed(self):
        cases = []
        flipped = bytearray(self.tape_bytes)
        flipped[len(flipped) // 2] ^= 1
        cases.append(("one flipped tape byte", {self.loc["file_name"]: bytes(flipped)}, {}, TS.TapeHashMismatch))
        cases.append(("truncated tape", {self.loc["file_name"]: self.tape_bytes[:-10]}, {}, TS.TapeSizeMismatch))
        cases.append(("extra file in artifact", {self.loc["file_name"]: self.tape_bytes, "x": b"1"}, {}, TS.TapeHashMismatch))
        cases.append(("wrong file name", {"other.json.gz": self.tape_bytes}, {}, TS.TapeHashMismatch))
        cases.append(("zip digest mismatch", None, {"digest": "sha256:" + "0" * 64}, TS.TapeHashMismatch))
        cases.append(("expired artifact", None, {"expired": True}, TS.TapeExpired))
        for label, files, kw, exc in cases:
            with self.subTest(label):
                self.gh = FakeGitHub()
                self.upload(files=files, **kw)
                with self.assertRaises(exc):
                    self.fetch()

    def test_missing_ambiguous_and_wrong_id(self):
        with self.assertRaises(TS.TapeMissing):                          # nothing uploaded
            self.fetch()
        self.upload(aid=1)
        self.upload(aid=2)
        with self.assertRaises(TS.TapeMissing):                          # two candidates: never guess
            self.fetch()
        with self.assertRaises(TS.TapeMissing):                          # sealed id not present
            self.fetch(dict(self.loc, artifact_id=3))
        self.assertEqual(self.fetch(dict(self.loc, artifact_id=2))[1]["id"], 2)

    def test_corrupt_zip(self):
        self.gh.add(900, self.loc["artifact_name"], b"PK\x03\x04garbage")
        with self.assertRaises(TS.TapeHashMismatch):
            self.fetch()

    def test_verifier_reads_only_the_exact_downloaded_artifact_no_fallback(self):
        for sub in ("v1", "v2", "v3", "v4"):
            os.makedirs(os.path.join(self.d, sub))
        with mock.patch.dict(os.environ, {"V3B_ARTIFACT_DOWNLOAD_DIR": ""}), self.assertRaises(TS.TapeMissing):
            TS.fetch_verified(self.loc, os.path.join(self.d, "v1"))      # the staged copy exists but is never used
        self.upload()
        dl, _ = self.fetch()
        with mock.patch.dict(os.environ, {"V3B_ARTIFACT_DOWNLOAD_DIR": os.path.dirname(dl)}):
            self.assertEqual(TS.file_identity(TS.fetch_verified(self.loc, os.path.join(self.d, "v2"))), (self.sha, self.n))
            with open(dl, "r+b") as fh:                                  # corrupted after download
                fh.seek(100)
                fh.write(b"\xff")
            with self.assertRaises(TS.TapeHashMismatch):
                TS.fetch_verified(self.loc, os.path.join(self.d, "v3"))
            with self.assertRaises(TS.StoreError):                       # file name not bound to the sha
                TS.fetch_verified(dict(self.loc, file_name="fc-v3-tape-x.json.gz"), os.path.join(self.d, "v4"))


def _write_unit(root, date, window, loc, proof, prev):
    unit = f"{date}_{window}"
    d = os.path.join(root, "seals", unit)
    os.makedirs(d)
    man = {"manifest_sha256": hashlib.sha256(unit.encode()).hexdigest(), "shadow_provenance": {"tape_store": loc}}
    with gzip.GzipFile(os.path.join(d, "manifest.json.gz"), "wb", mtime=0) as fh:
        fh.write(json.dumps(man).encode())
    arts = {"manifest.json.gz": VE.sha256_bytes(open(os.path.join(d, "manifest.json.gz"), "rb").read())}
    if proof is not None:
        json.dump(proof, open(os.path.join(d, "tape_artifact.json"), "w"))
        arts["tape_artifact.json"] = VE.sha256_bytes(open(os.path.join(d, "tape_artifact.json"), "rb").read())
    seal = {"date": date, "window": window, "prev_seal_sha256": prev, "manifest_sha256": man["manifest_sha256"],
            "artifacts_sha256": arts}
    seal["seal_sha256"] = CP.canonical_sha256(seal)
    json.dump(seal, open(os.path.join(d, "seal.json"), "w"))
    return unit, seal


class Evidence(Base):
    """Synthetic chain: unit 1 artifact-backed (proof sealed), unit 2 legacy (not temporary)."""

    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _S3)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.endpoint = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        super().setUp()
        _S3.objects.clear()
        _S3.faults.clear()
        self.env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        self.env.start()
        self.upload(created=T0)
        self.proof = AA.confirm_upload(self.loc, "tok", os.path.join(self.d, "p.json"), self.gh.get_json,
                                       self.gh.download, now=T0)
        self.root = os.path.join(self.d, "evidence")
        os.makedirs(self.root)
        self.unit, seal1 = _write_unit(self.root, "2026-10-07", "NIGHT", self.loc, self.proof, VE.GENESIS_SEAL_SHA256)
        u2, seal2 = _write_unit(self.root, "2026-10-08", "NIGHT", {"store": "r2", "key": "k"}, None, seal1["seal_sha256"])
        json.dump([{"index": 0, "unit": "GENESIS", "seal_sha256": VE.GENESIS_SEAL_SHA256},
                   {"index": 1, "unit": self.unit, "seal_sha256": seal1["seal_sha256"]},
                   {"index": 2, "unit": u2, "seal_sha256": seal2["seal_sha256"]}],
                  open(os.path.join(self.root, "CHAIN.json"), "w"))
        self.seal = seal1
        self.src, _ = self.fetch()

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def r2(self, secret="SK"):
        return TS.R2Store(self.endpoint, "fc-v3-evidence-tapes", "AK", secret, sleep=lambda s: None, timeout_s=20)

    def snapshot(self):
        out = {}
        for dp, _, fs in os.walk(os.path.join(self.root, "seals")):
            for f in fs:
                out[os.path.join(dp, f)] = VE.sha256_bytes(open(os.path.join(dp, f), "rb").read())
        out["CHAIN"] = VE.sha256_bytes(open(os.path.join(self.root, "CHAIN.json"), "rb").read())
        return out

    def status(self, days_after, live=None):
        rep = RM.scan(self.root, now=T0 + timedelta(days=days_after), live=live)
        return rep, {r["unit"]: r["status"] for r in rep["units"]}

    def test_retention_thresholds_and_exit_codes(self):
        for days, want, code in ((10, "OK", 0), (44.9, "OK", 0), (45, "MIGRATE_WARNING", 10),
                                 (59.9, "MIGRATE_WARNING", 10), (60, "MIGRATE_CRITICAL", 20), (89.9, "MIGRATE_CRITICAL", 20),
                                 (90, "EXPIRED", 20), (120, "EXPIRED", 20)):
            with self.subTest(days=days):
                rep, st = self.status(days)
                self.assertEqual((st[self.unit], st["2026-10-08_NIGHT"], rep["exit_code"]), (want, "NOT_TEMPORARY", code))
        rep, _ = self.status(10)
        row = rep["units"][0]
        self.assertEqual((row["days_remaining"], row["retention_days_configured"], row["artifact_id"]), (80.0, 90, 900))

    def test_live_check_detects_expiry_and_shorter_live_retention(self):
        self.gh.arts[0]["expired"] = True
        _, st = self.status(1, live=lambda loc: AA.find_artifact(loc, "tok", self.gh.get_json))
        self.assertEqual(st[self.unit], "EXPIRED")
        self.gh.arts[0].update(expired=False, expires_at="2026-11-15T00:00:00Z")
        _, st = self.status(1, live=lambda loc: AA.find_artifact(loc, "tok", self.gh.get_json))
        self.assertEqual(st[self.unit], "MIGRATE_WARNING")                # 38 days left on GitHub's current view
        self.gh.arts.clear()
        _, st = self.status(1, live=lambda loc: AA.find_artifact(loc, "tok", self.gh.get_json))
        self.assertEqual(st[self.unit], "LIVE_MISMATCH")

    def test_verifier_requires_the_sealed_upload_proof(self):
        d = os.path.join(self.root, "seals", self.unit)
        prov = {"tape_store": self.loc}
        self.assertEqual(VE.temporary_store_problems(d, self.seal["artifacts_sha256"], prov), [])
        arts = {k: v for k, v in self.seal["artifacts_sha256"].items() if k != "tape_artifact.json"}
        self.assertTrue(VE.temporary_store_problems(d, arts, prov))       # proof dropped
        bad = dict(self.loc, sha256="0" * 64)
        self.assertTrue(VE.temporary_store_problems(d, self.seal["artifacts_sha256"], {"tape_store": bad}))

    def test_byte_preserving_migration(self):
        before = self.snapshot()
        rec = MG.migrate_unit(self.root, self.unit, self.src, self.r2(), now=T0 + timedelta(days=40))
        self.assertEqual(before, self.snapshot())                         # no sealed byte, no CHAIN byte changed
        stored = _S3.objects[f"/fc-v3-evidence-tapes/{TS.key_for(self.sha)}"]
        self.assertEqual(stored, self.tape_bytes)                         # same bytes, no recompression
        self.assertEqual(rec["original_locator"], self.loc)
        self.assertEqual((rec["original_artifact"]["artifact_id"], rec["original_artifact"]["run_id"]), (900, 4242))
        self.assertEqual((rec["durable_locator"]["sha256"], rec["durable_locator"]["bytes"]), (self.sha, self.n))
        self.assertTrue(rec["durable_locator"]["verified_readback"])
        _, st = self.status(80)
        self.assertEqual(st[self.unit], "MIGRATED_DURABLE")               # no expiry risk once migrated
        dur = VE.tape_locator_for(self.root, self.unit, self.seal, {"tape_store": self.loc})
        with mock.patch.dict(os.environ, {"V3B_R2_ACCESS_KEY_ID": "AK", "V3B_R2_SECRET_ACCESS_KEY": "SK"}):
            self.assertEqual(TS.file_identity(TS.fetch_verified(dur, tempfile.mkdtemp(dir=self.d))), (self.sha, self.n))
        with mock.patch.dict(os.environ, {"V3B_VERIFY_TAPE_SOURCE": "sealed"}):
            self.assertIsNone(VE.tape_locator_for(self.root, self.unit, self.seal, {"tape_store": self.loc}))
        with self.assertRaises(MG.MigrationError):                        # create-only record
            MG.migrate_unit(self.root, self.unit, self.src, self.r2())

    def test_tampered_migration_record_is_never_ignored(self):
        MG.migrate_unit(self.root, self.unit, self.src, self.r2())
        p = MG.record_path(self.root, self.unit)
        rec = json.load(open(p))
        for label, mut in (("durable sha", lambda r: r["durable_locator"].update(sha256="0" * 64)),
                           ("original locator", lambda r: r["original_locator"].update(run_id=1)),
                           ("record hash", lambda r: r.update(migrated_at="x"))):
            with self.subTest(label):
                bad = json.loads(json.dumps(rec))
                mut(bad)
                if label != "record hash":
                    bad["record_sha256"] = MG._canon_sha({k: v for k, v in bad.items() if k != "record_sha256"})
                json.dump(bad, open(p, "w"))
                _, st = self.status(10)
                self.assertEqual(st[self.unit], "MIGRATION_INVALID")
                with self.assertRaises(MG.MigrationError):
                    VE.tape_locator_for(self.root, self.unit, self.seal, {"tape_store": self.loc})

    def test_migration_refusals_leave_no_record(self):
        corrupt = os.path.join(self.d, "corrupt.json.gz")
        b = bytearray(self.tape_bytes)
        b[7] ^= 1
        open(corrupt, "wb").write(bytes(b))
        _S3.objects[f"/fc-v3-evidence-tapes/{TS.key_for(self.sha)}"] = b"other bytes"
        for label, args, exc in (("corrupt source", (self.unit, corrupt, self.r2()), TS.TapeHashMismatch),
                                 ("R2 address holds other bytes", (self.unit, self.src, self.r2()), TS.ObjectConflict),
                                 ("not an artifact-backed unit", ("2026-10-08_NIGHT", self.src, self.r2()), MG.MigrationError),
                                 ("unknown unit", ("2026-10-09_DAY", self.src, self.r2()), MG.MigrationError)):
            with self.subTest(label), self.assertRaises(exc):
                MG.migrate_unit(self.root, *args)
            self.assertFalse(os.path.exists(MG.record_path(self.root, self.unit)))
        _S3.objects.clear()
        with self.assertRaises(TS.StoreAuthError):
            MG.migrate_unit(self.root, self.unit, self.src, self.r2(secret="bad"))
        self.assertFalse(os.path.exists(MG.record_path(self.root, self.unit)))

    def test_sealed_proof_tamper_blocks_migration(self):
        p = os.path.join(self.root, "seals", self.unit, "tape_artifact.json")
        json.dump(dict(self.proof, artifact_id=1), open(p, "w"))
        with self.assertRaises(MG.MigrationError):
            MG.migrate_unit(self.root, self.unit, self.src, self.r2())


if __name__ == "__main__":
    unittest.main()

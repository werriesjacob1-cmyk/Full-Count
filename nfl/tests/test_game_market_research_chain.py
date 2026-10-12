"""FC-NFL-004 evidence chain (Codex P1 review 6129802534): no silent restart."""
import hashlib
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

from nfl.prospective.game_market_research_chain import (
    MANIFEST_NAME,
    ChainError,
    GitHubArtifacts,
    artifact_name,
    build_anchor,
    canonical_sha256,
    load_anchor,
    load_chain,
    seal_chain,
)
from nfl.prospective.game_market_research_freeze import write_research_freeze
from nfl.tests.test_game_market_research_pathway import _binding, _freeze, _snapshot

REPO = Path(__file__).resolve().parents[2]
REAL_ANCHOR = REPO / "data/nfl_game_market_research/LEDGER_ANCHOR.json"
WORKFLOW = REPO / ".github/workflows/nfl-live-game-market-shadow.yml"
BRANCH = "claude/test"


def _rec(sealed="2026-09-20T16:01:00Z", frozen="2026-09-20T16:02:00Z", captured="2026-09-20T16:00:00Z"):
    snap = _snapshot(sealed_at=sealed, captured=captured)
    return _freeze(snapshot=snap, binding=_binding(snap), frozen_at=frozen)


class FakeAPI:
    """Duck-typed GitHubArtifacts over local chain dirs."""

    def __init__(self):
        self.artifacts, self.dirs, self.runs, self.run_arts = [], {}, [], {}

    def publish(self, work, *, aid, run_id, created, branch=BRANCH, expired=False, success=True):
        dest = Path(tempfile.mkdtemp()) / "src"
        shutil.copytree(work, dest)
        art = {"id": aid, "name": artifact_name(self.epoch), "expired": expired, "created_at": created,
               "workflow_run": {"id": run_id, "head_branch": branch}}
        self.artifacts.append(art)
        self.dirs[aid] = dest
        self.run_arts.setdefault(run_id, []).append(art)
        if success:
            self.runs.append({"id": run_id, "created_at": created})
        return art

    def chain_artifacts(self, name):
        return [a for a in self.artifacts if a["name"] == name]

    def successful_runs(self, branch):
        return sorted(self.runs, key=lambda r: r["created_at"], reverse=True)

    def run_artifacts(self, run_id):
        return [a for a in self.run_arts.get(run_id, []) if a in self.artifacts]

    def download(self, art, dest):
        out = Path(dest) / f"artifact_{art['id']}"
        shutil.copytree(self.dirs[art["id"]], out, dirs_exist_ok=True)
        return out


class ChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base_rel = "baseline"
        base = self.root / self.base_rel
        write_research_freeze(base, _rec())
        write_research_freeze(base, _rec(sealed="2026-09-20T16:11:00Z", frozen="2026-09-20T16:12:00Z",
                                         captured="2026-09-20T16:10:00Z"))
        self.anchor = build_anchor(base, epoch_id="epoch-test", reason="test", created_at="2026-10-12T00:00:00Z",
                                   baseline_path=self.base_rel)
        (self.root / "anchor.json").write_text(json.dumps(self.anchor))
        self.api = FakeAPI()
        self.api.epoch = "epoch-test"
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def work(self):
        self.n += 1
        return self.root / f"work{self.n}"

    def load(self, run_id="current", bootstrap=""):
        w = self.work()
        state = load_chain(api=self.api, anchor=self.anchor, repo_root=self.root, branch=BRANCH,
                           current_run_id=run_id, bootstrap_epoch=bootstrap, work=w)
        return w, state

    def run_and_seal(self, *, run_id, created, bootstrap="", add=None, publish=True, **pub):
        w, state = self.load(run_id=run_id, bootstrap=bootstrap)
        if add is not None:
            write_research_freeze(w / "ledger", add)
        manifest = seal_chain(state=state, anchor=self.anchor, work=w, run={"run_id": run_id})
        if publish:
            self.api.publish(w, aid=int(run_id), run_id=run_id, created=created, **pub)
        return manifest

    def bootstrap(self):
        return self.run_and_seal(run_id="100", created="2026-10-12T21:00:00Z", bootstrap="epoch-test")

    # --- first run / bootstrap ------------------------------------------------
    def test_first_run_without_explicit_bootstrap_fails_closed(self):
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "NO_CHAIN")

    def test_explicit_bootstrap_seeds_anchor_byte_for_byte(self):
        m = self.bootstrap()
        self.assertEqual(m["sequence"], 1)
        self.assertEqual(m["previous_manifest_sha256"], self.anchor["anchor_sha256"])
        self.assertEqual({k: m["files"][k] for k in self.anchor["files"]}, self.anchor["files"])

    def test_wrong_epoch_bootstrap_value_is_not_a_bootstrap(self):
        with self.assertRaises(ChainError) as cm:
            self.load(bootstrap="some-other-epoch")
        self.assertEqual(cm.exception.code, "NO_CHAIN")

    def test_bootstrap_refused_when_chain_exists_even_if_expired(self):
        self.bootstrap()
        self.api.artifacts[0]["expired"] = True
        with self.assertRaises(ChainError) as cm:
            self.load(bootstrap="epoch-test")
        self.assertEqual(cm.exception.code, "BOOTSTRAP_REFUSED")

    # --- continuity -----------------------------------------------------------
    def test_monotonic_continuation_and_idempotent_replay(self):
        m1 = self.bootstrap()
        new = _rec(sealed="2026-09-20T16:21:00Z", frozen="2026-09-20T16:22:00Z", captured="2026-09-20T16:20:00Z")
        m2 = self.run_and_seal(run_id="101", created="2026-10-12T21:10:00Z", add=new)
        self.assertEqual(m2["sequence"], 2)
        self.assertEqual(m2["previous_manifest_sha256"], m1["manifest_sha256"])
        self.assertTrue(set(m1["files"].items()) <= set(m2["files"].items()))
        self.assertEqual(m2["file_count"], 3)
        # A run that adds nothing reproduces the identical evidence set.
        m3 = self.run_and_seal(run_id="102", created="2026-10-12T21:20:00Z")
        self.assertEqual(m3["files"], m2["files"])
        self.assertEqual(m3["sequence"], 3)

    def test_interrupted_run_does_not_break_chain(self):
        self.bootstrap()
        w, state = self.load(run_id="101")  # loaded, froze, then died before seal/upload
        write_research_freeze(w / "ledger", _rec(sealed="2026-09-20T16:31:00Z", frozen="2026-09-20T16:32:00Z",
                                                 captured="2026-09-20T16:30:00Z"))
        _, state2 = self.load(run_id="102")
        self.assertEqual(state2["sequence"], 1)  # continues from the last sealed link

    # --- missing / expired / wrong branch ------------------------------------
    def test_expired_head_blocks(self):
        self.bootstrap()
        self.api.artifacts[0]["expired"] = True
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "EXPIRED_CHAIN_HEAD")

    def test_wrong_branch_artifacts_do_not_start_or_continue_a_chain(self):
        self.run_and_seal(run_id="100", created="2026-10-12T21:00:00Z", bootstrap="epoch-test", branch="main")
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "NO_CHAIN")

    def test_deleted_newest_artifact_is_missing_evidence_not_a_rollback(self):
        self.bootstrap()
        self.run_and_seal(run_id="101", created="2026-10-12T21:10:00Z",
                          add=_rec(sealed="2026-09-20T16:21:00Z", frozen="2026-09-20T16:22:00Z",
                                   captured="2026-09-20T16:20:00Z"))
        self.api.artifacts = [a for a in self.api.artifacts if a["id"] != 101]  # newest link vanished
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "MISSING_EXPECTED_ARTIFACT")

    def test_successful_run_without_chain_artifact_is_missing_evidence(self):
        self.bootstrap()
        self.api.runs.append({"id": "103", "created_at": "2026-10-12T22:00:00Z"})
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "MISSING_EXPECTED_ARTIFACT")

    # --- tamper / incomplete / conflict / fork / duplicate ---------------------
    def _head_dir(self):
        return self.api.dirs[max(self.api.dirs)]

    def test_tampered_incomplete_unlisted_and_forged_manifest(self):
        self.bootstrap()
        head = self._head_dir()
        rec_path = next((head / "ledger").rglob("*.json"))
        original = rec_path.read_bytes()
        cases = []
        rec = json.loads(original)
        rec["selections"][0]["decision"] = "RESEARCH_LEAN"
        cases.append(("TAMPERED", lambda: rec_path.write_text(json.dumps(rec))))
        cases.append(("INCOMPLETE", lambda: rec_path.unlink()))
        cases.append(("UNLISTED_FILE", lambda: (head / "ledger" / "extra.json").write_bytes(original)))
        for code, mutate in cases:
            mutate()
            with self.assertRaises(ChainError) as cm:
                self.load()
            self.assertEqual(cm.exception.code, code)
            rec_path.write_bytes(original)
            (head / "ledger" / "extra.json").unlink(missing_ok=True)
        mpath = head / MANIFEST_NAME
        m = json.loads(mpath.read_text())
        m["sequence"] = 99  # not re-hashed
        mpath.write_text(json.dumps(m))
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "MANIFEST_INVALID")

    def test_internally_consistent_manifest_dropping_baseline_is_non_monotonic(self):
        self.bootstrap()
        head = self._head_dir()
        dropped = next(iter(self.anchor["files"]))
        (head / "ledger" / dropped).unlink()
        m = json.loads((head / MANIFEST_NAME).read_text())
        del m["files"][dropped]
        m["file_count"] -= 1
        body = {k: v for k, v in m.items() if k != "manifest_sha256"}
        m["manifest_sha256"] = canonical_sha256(body)
        (head / MANIFEST_NAME).write_text(json.dumps(m))
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "NON_MONOTONIC")

    def test_fork_is_detected(self):
        self.bootstrap()
        a = _rec(sealed="2026-09-20T16:21:00Z", frozen="2026-09-20T16:22:00Z", captured="2026-09-20T16:20:00Z")
        b = _rec(sealed="2026-09-20T16:41:00Z", frozen="2026-09-20T16:42:00Z", captured="2026-09-20T16:40:00Z")
        w1, s1 = self.load(run_id="101")
        w2, s2 = self.load(run_id="102")  # both loaded seq 1 concurrently
        write_research_freeze(w1 / "ledger", a)
        write_research_freeze(w2 / "ledger", b)
        seal_chain(state=s1, anchor=self.anchor, work=w1, run={})
        seal_chain(state=s2, anchor=self.anchor, work=w2, run={})
        self.api.publish(w1, aid=101, run_id="101", created="2026-10-12T21:10:00Z")
        self.api.publish(w2, aid=102, run_id="102", created="2026-10-12T21:11:00Z")
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertIn(cm.exception.code, {"CHAIN_FORK", "NON_MONOTONIC"})

    def test_duplicate_observation_rejected(self):
        self.bootstrap()
        w, state = self.load(run_id="101")
        snap = _snapshot()  # same snapshot as a baseline record, re-frozen later
        write_research_freeze(w / "ledger", _freeze(snapshot=snap, binding=_binding(snap),
                                                    frozen_at="2026-09-20T16:05:00Z"))
        with self.assertRaises(ChainError) as cm:
            seal_chain(state=state, anchor=self.anchor, work=w, run={})
        self.assertEqual(cm.exception.code, "DUPLICATE_OBSERVATION")

    def test_replay_conflict_during_run_is_refused(self):
        self.bootstrap()
        w, state = self.load(run_id="101")
        victim = next((w / "ledger").rglob("*.json"))
        victim.write_text(victim.read_text().replace('"NO_LEAN"', '"RESEARCH_LEAN"', 1))
        with self.assertRaises(ChainError) as cm:
            seal_chain(state=state, anchor=self.anchor, work=w, run={})
        self.assertEqual(cm.exception.code, "TAMPERED")

    def test_changed_committed_baseline_is_refused(self):
        rel = next(iter(self.anchor["files"]))
        p = self.root / self.base_rel / rel
        p.write_bytes(p.read_bytes().replace(b"NO_LEAN", b"RESEARCH_LEAN", 1))
        with self.assertRaises(ChainError) as cm:
            load_anchor(self.root / "anchor.json", self.root)
        self.assertEqual(cm.exception.code, "ANCHOR_MISMATCH")


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


class _Resp:
    def __init__(self, content=b""):
        self.content = content

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, blob):
        self.blob = blob

    def get(self, url, **kw):
        return _Resp(self.blob)


class DownloadTests(unittest.TestCase):
    def test_digest_and_zip_slip(self):
        good = _zip({"ledger/x.json": "{}"})
        with tempfile.TemporaryDirectory() as tmp:
            api = GitHubArtifacts(repo="o/r", token="t", session=_Session(good))
            art = {"id": 1, "archive_download_url": "u", "digest": "sha256:" + hashlib.sha256(good).hexdigest()}
            self.assertTrue((api.download(art, Path(tmp)) / "ledger/x.json").exists())
            with self.assertRaises(ChainError) as cm:
                api.download({**art, "id": 2, "digest": "sha256:" + "0" * 64}, Path(tmp))
            self.assertEqual(cm.exception.code, "DIGEST_MISMATCH")
            evil = _zip({"../escape.json": "{}"})
            api = GitHubArtifacts(repo="o/r", token="t", session=_Session(evil))
            with self.assertRaises(ChainError) as cm:
                api.download({"id": 3, "archive_download_url": "u",
                              "digest": "sha256:" + hashlib.sha256(evil).hexdigest()}, Path(tmp))
            self.assertEqual(cm.exception.code, "UNSAFE_ARTIFACT")


class RealAnchorTests(unittest.TestCase):
    def test_committed_anchor_covers_the_30_frozen_records_byte_for_byte(self):
        anchor = load_anchor(REAL_ANCHOR, REPO)
        self.assertEqual(anchor["epoch_id"], "fc-nfl-004-epoch-1")
        self.assertEqual(anchor["file_count"], 30)
        self.assertEqual(anchor["anchor_sha256"],
                         "25e816a5aac517f0dee9f0b3468e5fefd45a0760d79f137029f31723bab62c09")

    def test_workflow_uploads_the_anchor_epoch_artifact_and_never_cancels_in_progress(self):
        anchor = json.loads(REAL_ANCHOR.read_text())
        wf = yaml.safe_load(WORKFLOW.read_text())
        steps = wf["jobs"]["shadow"]["steps"]
        names = {s.get("name"): s for s in steps}
        upload = names["Upload sealed evidence-chain link"]
        self.assertEqual(upload["with"]["name"], artifact_name(anchor["epoch_id"]))
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertIn("chain_seal", upload["if"])
        self.assertFalse(wf["concurrency"]["cancel-in-progress"])
        self.assertEqual(wf["permissions"], {"contents": "read", "actions": "read"})
        for step in ("Freeze spread/total research records", "Grade completed games in the chained ledger",
                     "Seal next chain link (monotonic superset)"):
            self.assertIn("chain_load", names[step]["if"])


if __name__ == "__main__":
    unittest.main()

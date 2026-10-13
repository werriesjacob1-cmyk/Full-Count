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
    ARTIFACT_LAYOUT,
    MANIFEST_NAME,
    PACKAGE_DIR,
    verify_chain_dir,
    verify_sealed_package,
    ChainError,
    GitHubArtifacts,
    artifact_name,
    build_anchor,
    canonical_sha256,
    load_anchor,
    load_chain,
    seal_chain,
    upload_step_name,
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
    """Duck-typed GitHubArtifacts over local chain dirs. ``history`` models run/step
    history, which (unlike artifacts) survives artifact deletion and expiry."""

    def __init__(self):
        self.artifacts, self.dirs, self.history = [], {}, []

    def publish(self, work, *, aid, run_id, created, branch=BRANCH, expired=False, conclusion="success"):
        # Mirrors the workflow: only the sealed package is uploaded (PACKAGE_DIR).
        src = Path(work) / PACKAGE_DIR if (Path(work) / PACKAGE_DIR).exists() else Path(work)
        dest = Path(tempfile.mkdtemp()) / "src"
        shutil.copytree(src, dest)
        art = {"id": aid, "name": artifact_name(self.epoch), "expired": expired, "created_at": created,
               "workflow_run": {"id": run_id, "head_branch": branch}}
        self.artifacts.append(art)
        self.dirs[aid] = dest
        self.history.append({"id": run_id, "created_at": created, "conclusion": conclusion,
                             "branch": branch, "epoch": self.epoch})
        return art

    def sealed_runs(self, branch, epoch_id, since):
        return [r for r in self.history
                if r["branch"] == branch and r["epoch"] == epoch_id and r["created_at"] >= since]

    def chain_artifacts(self, name):
        return [a for a in self.artifacts if a["name"] == name]

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

    def test_wrong_epoch_bootstrap_value_is_explicitly_refused(self):
        with self.assertRaises(ChainError) as cm:
            self.load(bootstrap="some-other-epoch")
        self.assertEqual(cm.exception.code, "BOOTSTRAP_REFUSED")

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

    def test_sealed_run_without_listed_artifact_is_missing_evidence(self):
        self.bootstrap()
        self.api.history.append({"id": "103", "created_at": "2026-10-12T22:00:00Z", "conclusion": "failure",
                                 "branch": BRANCH, "epoch": "epoch-test"})
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "MISSING_EXPECTED_ARTIFACT")

    # --- Codex P1 (review 6133401456): total deletion / same-epoch restart ------
    def _two_links(self):
        self.bootstrap()
        return self.run_and_seal(run_id="101", created="2026-10-12T21:10:00Z",
                                 add=_rec(sealed="2026-09-20T16:21:00Z", frozen="2026-09-20T16:22:00Z",
                                          captured="2026-09-20T16:20:00Z"))

    def test_total_artifact_deletion_blocks_continue_and_same_epoch_restart(self):
        self._two_links()
        self.api.artifacts = []  # every chain artifact deleted
        for bootstrap, code in (("", "MISSING_EXPECTED_ARTIFACT"), ("epoch-test", "BOOTSTRAP_REFUSED")):
            with self.assertRaises(ChainError) as cm:
                self.load(bootstrap=bootstrap)
            self.assertEqual(cm.exception.code, code)

    def test_failed_run_history_alone_blocks_same_epoch_restart(self):
        # A failed run that nevertheless uploaded a link, whose artifact was then deleted.
        self.api.history.append({"id": "99", "created_at": "2026-10-12T20:00:00Z", "conclusion": "failure",
                                 "branch": BRANCH, "epoch": "epoch-test"})
        with self.assertRaises(ChainError) as cm:
            self.load(bootstrap="epoch-test")
        self.assertEqual(cm.exception.code, "BOOTSTRAP_REFUSED")
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "MISSING_EXPECTED_ARTIFACT")

    def test_fully_expired_history_never_restarts(self):
        self._two_links()
        for a in self.api.artifacts:
            a["expired"] = True
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "EXPIRED_CHAIN_HEAD")
        with self.assertRaises(ChainError) as cm:
            self.load(bootstrap="epoch-test")
        self.assertEqual(cm.exception.code, "BOOTSTRAP_REFUSED")

    def test_deleted_intermediate_link_and_deleted_failed_run_link_cannot_roll_back(self):
        self._two_links()
        self.api.artifacts = [a for a in self.api.artifacts if a["id"] != 100]  # oldest link gone
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "MISSING_EXPECTED_ARTIFACT")

    def test_failed_run_link_deleted_after_upload_cannot_roll_back(self):
        self.bootstrap()
        new = _rec(sealed="2026-09-20T16:21:00Z", frozen="2026-09-20T16:22:00Z", captured="2026-09-20T16:20:00Z")
        self.run_and_seal(run_id="101", created="2026-10-12T21:10:00Z", add=new, conclusion="failure")
        self.api.artifacts = [a for a in self.api.artifacts if a["id"] != 101]
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "MISSING_EXPECTED_ARTIFACT")

    def test_deleted_run_history_is_a_sequence_gap(self):
        self._two_links()
        self.api.history = [r for r in self.api.history if r["id"] != "100"]  # run record purged
        with self.assertRaises(ChainError) as cm:
            self.load()
        self.assertEqual(cm.exception.code, "SEQUENCE_GAP")

    def test_new_epoch_authorization_boundaries(self):
        self._two_links()
        with self.assertRaises(ChainError) as cm:  # wrong epoch value is never a bootstrap
            self.load(bootstrap="epoch-test-2")
        self.assertEqual(cm.exception.code, "BOOTSTRAP_REFUSED")
        # A reviewed NEW anchor epoch may bootstrap exactly once; old evidence untouched.
        new_anchor = build_anchor(self.root / self.base_rel, epoch_id="epoch-test-2", reason="recovery",
                                  created_at="2026-10-12T23:00:00Z", baseline_path=self.base_rel)
        old_anchor, self.anchor, self.api.epoch = self.anchor, new_anchor, "epoch-test-2"
        m = self.run_and_seal(run_id="200", created="2026-10-12T23:05:00Z", bootstrap="epoch-test-2")
        self.assertEqual((m["epoch_id"], m["sequence"]), ("epoch-test-2", 1))
        with self.assertRaises(ChainError) as cm:
            self.load(bootstrap="epoch-test-2")
        self.assertEqual(cm.exception.code, "BOOTSTRAP_REFUSED")
        self.assertEqual(len([a for a in self.api.artifacts if a["name"] == artifact_name("epoch-test")]), 2)
        self.anchor, self.api.epoch = old_anchor, "epoch-test"
        self.assertEqual(self.load()[1]["sequence"], 2)  # epoch-1 chain still intact

    # --- failure injection: partial evidence can never be sealed ---------------
    def test_partially_written_freeze_or_grade_is_refused_at_seal(self):
        self.bootstrap()
        for rel in ("2026/week_3/event_x/partial.json", "2026/week_3/event_x/grades/partial.grade.json"):
            w, state = self.load(run_id="101")
            p = w / "ledger" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('{"schema": "fc-nfl-004-game-market-research-freeze-v2", "event_id": ')  # truncated
            with self.assertRaises(ChainError) as cm:
                seal_chain(state=state, anchor=self.anchor, work=w, run={})
            self.assertEqual(cm.exception.code, "TAMPERED")

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
        # Two sealed links from one predecessor: history (3 uploads) exceeds the head
        # sequence (2), so the fork is refused at the history check, before link checks.
        self.assertIn(cm.exception.code, {"SEQUENCE_GAP", "CHAIN_FORK", "NON_MONOTONIC"})

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
        upload = names[upload_step_name(anchor["epoch_id"])]  # durable run-history marker
        self.assertEqual(upload["with"]["name"], artifact_name(anchor["epoch_id"]))
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertFalse(wf["concurrency"]["cancel-in-progress"])
        self.assertEqual(wf["permissions"], {"contents": "read", "actions": "read"})

    def test_failure_injection_gating_seal_and_upload_need_every_upstream_success(self):
        # Codex P1 (review 6133401456): evaluate each step's `if` under injected
        # upstream outcomes; a failed freeze or grade must never seal or upload.
        wf = yaml.safe_load(WORKFLOW.read_text())
        steps = {s.get("id") or s.get("name"): s for s in wf["jobs"]["shadow"]["steps"]}
        anchor = json.loads(REAL_ANCHOR.read_text())
        upload_if = steps[upload_step_name(anchor["epoch_id"])]["if"]

        def runs(cond, outcomes):
            expr = cond.strip()
            assert expr.startswith("${{") and expr.endswith("}}"), cond
            expr = expr[3:-2].replace("!cancelled()", "True").replace("&&", " and ").replace("||", " or ")
            for sid, outcome in outcomes.items():
                expr = expr.replace(f"steps.{sid}.outcome", repr(outcome))
            return eval(expr, {"__builtins__": {}})  # noqa: S307 - test-only, fixed workflow text

        ok = {"unit_tests": "success", "chain_load": "success", "research_freeze": "success",
              "research_grade": "success", "chain_seal": "success"}
        self.assertTrue(runs(steps["chain_seal"]["if"], ok))
        self.assertTrue(runs(upload_if, ok))
        for failed in ("chain_load", "research_freeze", "research_grade"):
            for outcome in ("failure", "skipped"):
                state = {**ok, failed: outcome}
                self.assertFalse(runs(steps["chain_seal"]["if"], state), (failed, outcome))
                self.assertFalse(runs(upload_if, state), (failed, outcome))
        self.assertFalse(runs(steps["research_grade"]["if"], {**ok, "research_freeze": "failure"}))
        self.assertFalse(runs(upload_if, {**ok, "chain_seal": "failure"}))


class SealedRunHistoryTests(unittest.TestCase):
    class _JSONResp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def _api(self, runs, jobs):
        test = self

        class S:
            def get(self, url, **kw):
                if url.endswith("/runs"):
                    return test._JSONResp({"workflow_runs": runs})
                rid = url.rstrip("/").split("/")[-2]
                return test._JSONResp({"jobs": jobs[rid]})
        return GitHubArtifacts(repo="o/r", token="t", session=S())

    def test_markers_survive_artifact_loss_and_ignore_other_epochs(self):
        runs = [{"id": i, "created_at": "2026-10-12T21:%02d:00Z" % i, "status": "completed",
                 "conclusion": c} for i, c in ((1, "success"), (2, "failure"), (3, "failure"), (4, "success"))]
        step = lambda n, c: {"steps": [{"name": n, "conclusion": c}]}  # noqa: E731
        jobs = {"1": [step("Upload sealed evidence-chain link", "success")],            # legacy epoch-1
                "2": [step(upload_step_name("fc-nfl-004-epoch-1"), "success")],         # failed run, uploaded
                "3": [step(upload_step_name("fc-nfl-004-epoch-1"), "skipped")],         # never uploaded
                "4": [step(upload_step_name("fc-nfl-004-epoch-2"), "success")]}         # other epoch
        got = self._api(runs, jobs).sealed_runs("b", "fc-nfl-004-epoch-1", "2026-10-12T00:00:00Z")
        self.assertEqual([r["id"] for r in got], [1, 2])
        got2 = self._api(runs, jobs).sealed_runs("b", "fc-nfl-004-epoch-2", "2026-10-12T00:00:00Z")
        self.assertEqual([r["id"] for r in got2], [4])  # legacy name is epoch-1 only

    def test_unreadable_history_fails_closed(self):
        class Boom:
            def get(self, *a, **k):
                raise OSError("api down")
        with self.assertRaises(ChainError) as cm:
            GitHubArtifacts(repo="o/r", token="t", session=Boom()).sealed_runs("b", "e", "2026-01-01")
        self.assertEqual(cm.exception.code, "HISTORY_UNVERIFIABLE")


class PackagingTests(unittest.TestCase):
    """Sealed links are only manifest + ledger/ (no recursive prior/ copies).

    Before this repair, links 1-7 uploaded the whole work dir, including
    prior/ (every earlier link, each with its own prior/). The artifact
    doubled every run: real chain prior/ grew 9.5 -> 20 -> 40 -> 77 MB.
    """

    setUp = ChainTests.setUp
    tearDown = ChainTests.tearDown
    work = ChainTests.work
    load = ChainTests.load
    run_and_seal = ChainTests.run_and_seal
    bootstrap = ChainTests.bootstrap

    def _legacy_publish(self, run_id, created, bootstrap=""):
        """Publish a link the way links 1-7 were published: whole work dir, prior/
        included, manifest without the layout declaration."""
        w, state = self.load(run_id=run_id, bootstrap=bootstrap)
        seal_chain(state=state, anchor=self.anchor, work=w, run={"run_id": run_id})
        legacy = Path(tempfile.mkdtemp()) / "legacy"
        shutil.copytree(w, legacy, ignore=shutil.ignore_patterns(PACKAGE_DIR))
        m = json.loads((w / PACKAGE_DIR / MANIFEST_NAME).read_text())
        m.pop("artifact_layout")
        body = {k: v for k, v in m.items() if k != "manifest_sha256"}
        m["manifest_sha256"] = canonical_sha256(body)
        (legacy / MANIFEST_NAME).write_text(json.dumps(m))
        self.api.publish(legacy, aid=int(run_id), run_id=run_id, created=created)
        return m

    def test_sealed_package_contains_only_manifest_and_ledger(self):
        self.bootstrap()
        w, state = self.load(run_id="101")
        m = seal_chain(state=state, anchor=self.anchor, work=w, run={"run_id": "101"})
        pkg = w / PACKAGE_DIR
        self.assertEqual(sorted(p.name for p in pkg.iterdir()), sorted([MANIFEST_NAME, "ledger"]))
        self.assertEqual(m["artifact_layout"], ARTIFACT_LAYOUT)
        self.assertTrue((w / "prior").exists())          # prior/ exists in work ...
        self.assertFalse((pkg / "prior").exists())       # ... but never in the package
        files = [p for p in pkg.rglob("*") if p.is_file()]
        self.assertEqual(len(files), m["file_count"] + 1)  # ledger files + manifest, nothing else

    def test_artifact_grows_linearly_not_exponentially(self):
        self.bootstrap()
        sizes = []
        for i in range(5):
            rid = str(101 + i)
            rec = _rec(sealed=f"2026-09-20T16:{21 + 5 * i}:00Z", frozen=f"2026-09-20T16:{22 + 5 * i}:00Z",
                       captured=f"2026-09-20T16:{20 + 5 * i}:00Z")
            self.run_and_seal(run_id=rid, created=f"2026-10-12T22:0{i}:00Z", add=rec)
            pub = self.api.dirs[int(rid)]
            sizes.append(sum(p.stat().st_size for p in pub.rglob("*") if p.is_file()))
            self.assertFalse(any(p.name == "prior" for p in pub.rglob("*")))
        steps = [b - a for a, b in zip(sizes, sizes[1:])]
        self.assertLess(max(steps), 2 * min(steps) + 4096)   # ~constant increment per new record
        self.assertLess(sizes[-1], 2 * sizes[1])             # nowhere near doubling per run

    def test_package_with_nested_prior_copy_is_rejected(self):
        self.bootstrap()
        w, state = self.load(run_id="101")
        seal_chain(state=state, anchor=self.anchor, work=w, run={"run_id": "101"})
        pkg = w / PACKAGE_DIR
        shutil.copytree(w / "prior", pkg / "prior")
        with self.assertRaises(ChainError) as cm:
            verify_sealed_package(pkg, self.anchor)
        self.assertEqual(cm.exception.code, "ARTIFACT_LAYOUT")

    def test_package_with_unexpected_top_level_file_is_rejected(self):
        self.bootstrap()
        w, state = self.load(run_id="101")
        seal_chain(state=state, anchor=self.anchor, work=w, run={"run_id": "101"})
        (w / PACKAGE_DIR / "notes.txt").write_text("x")
        with self.assertRaises(ChainError) as cm:
            verify_chain_dir(w / PACKAGE_DIR, self.anchor)
        self.assertEqual(cm.exception.code, "ARTIFACT_LAYOUT")

    def test_package_with_unlisted_ledger_file_is_rejected(self):
        self.bootstrap()
        w, state = self.load(run_id="101")
        seal_chain(state=state, anchor=self.anchor, work=w, run={"run_id": "101"})
        (w / PACKAGE_DIR / "ledger" / "stray.json").write_text("{}")
        with self.assertRaises(ChainError) as cm:
            verify_sealed_package(w / PACKAGE_DIR, self.anchor)
        self.assertIn(cm.exception.code, {"UNLISTED_FILE", "TAMPERED"})

    def test_prior_dir_hidden_inside_ledger_is_rejected(self):
        self.bootstrap()
        w, state = self.load(run_id="101")
        seal_chain(state=state, anchor=self.anchor, work=w, run={"run_id": "101"})
        (w / PACKAGE_DIR / "ledger" / "prior").mkdir()
        with self.assertRaises(ChainError) as cm:
            verify_sealed_package(w / PACKAGE_DIR, self.anchor)
        self.assertEqual(cm.exception.code, "ARTIFACT_LAYOUT")

    def test_seal_refuses_preexisting_package_dir(self):
        self.bootstrap()
        w, state = self.load(run_id="101")
        (w / PACKAGE_DIR).mkdir()
        with self.assertRaises(ChainError) as cm:
            seal_chain(state=state, anchor=self.anchor, work=w, run={"run_id": "101"})
        self.assertEqual(cm.exception.code, "ARTIFACT_LAYOUT")

    def test_loaded_link_declaring_layout_but_carrying_prior_fails_closed(self):
        self.bootstrap()
        self.run_and_seal(run_id="101", created="2026-10-12T22:00:00Z")
        shutil.copytree(self.api.dirs[100], self.api.dirs[101] / "prior" / "artifact_100")
        with self.assertRaises(ChainError) as cm:
            self.load(run_id="102")
        self.assertEqual(cm.exception.code, "ARTIFACT_LAYOUT")

    def test_legacy_links_without_declaration_still_load_and_continue(self):
        # Real links 1-7 are immutable and carry prior/; they must stay loadable.
        self._legacy_publish("100", "2026-10-12T21:00:00Z", bootstrap="epoch-test")
        m_legacy = self._legacy_publish("101", "2026-10-12T22:00:00Z")
        self.assertTrue((self.api.dirs[101] / "prior").exists())   # legacy shape, as real links 1-7
        self.assertIsNone(m_legacy.get("artifact_layout"))
        m = self.run_and_seal(run_id="102", created="2026-10-12T23:00:00Z")
        self.assertEqual(m["sequence"], 3)
        self.assertEqual(m["previous_manifest_sha256"], m_legacy["manifest_sha256"])
        self.assertEqual(m["artifact_layout"], ARTIFACT_LAYOUT)
        self.assertFalse((self.api.dirs[102] / "prior").exists())

    def test_layout_declaration_can_never_be_dropped_later(self):
        self.bootstrap()                                  # packaged link (declares layout)
        self._legacy_publish("101", "2026-10-12T22:00:00Z")  # later link without declaration
        with self.assertRaises(ChainError) as cm:
            self.load(run_id="102")
        self.assertEqual(cm.exception.code, "ARTIFACT_LAYOUT")

    def test_workflow_uploads_only_the_package(self):
        wf = yaml.safe_load(WORKFLOW.read_text())
        steps = wf["jobs"]["shadow"]["steps"]
        up = [s for s in steps if s.get("name") == upload_step_name("fc-nfl-004-epoch-1")]
        self.assertEqual(len(up), 1)
        self.assertEqual(up[0]["with"]["path"], f"/tmp/nfl-gm-chain/{PACKAGE_DIR}")


if __name__ == "__main__":
    unittest.main()

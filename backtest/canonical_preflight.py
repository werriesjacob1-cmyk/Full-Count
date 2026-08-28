#!/usr/bin/env python3
"""canonical_preflight.py -- prove a canonical run can survive, before starting it.

Every check here exists because the corresponding failure actually happened
in this project. None is speculative, and checks that would merely look
thorough are deliberately absent:

  * PINNED_SHA / CLEAN_WORKTREE -- a run is identified by the code that
    produced it; an uncommitted edit makes that identity a lie.
  * CACHE_OUTSIDE_WORKTREE -- a cache inside the worktree is destroyed by
    the checkout that pins the run.
  * CACHE_PERSISTS -- the cache directory must survive a write/read/reopen
    cycle. Cheap, and the one property whose absence killed cfb15819.
  * DURABLE_PUSH_REAL -- this host answers `git push --dry-run` with success
    on refs it then refuses for real (refs/fc-autosave/* returned 403 at the
    RPC while --dry-run reported OK). A dry run is not evidence. This does a
    REAL push of a tiny blob to a scratch ref and reads it back.
  * GIT_IDENTITY_IN_ENV -- identity set in a local dict rather than
    os.environ passed locally and failed in CI, where no global identity
    exists. Checked the way git will actually see it.
  * RESUME_FEASIBLE -- an impossible resume should be known in seconds, not
    discovered when the source gate fires mid-run.
  * DISK_HEADROOM -- writable disk is a fixed allowance here; a run that
    fills it loses the checkpoint it is writing.

Deliberately NOT checked: network reachability of Statcast (the run's own
warmup proves it far better than a ping), row-count expectations (that is
the source-identity gate's job, and duplicating it would create a second
opinion about the same fact), and anything requiring a full generation
pass (that is the run).

    python3 -m backtest.canonical_preflight --source-cache /root/.fc-statcast-cache
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from backtest import canonical_durability as _cd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_FREE_BYTES = 2 * 1024 ** 3  # 2 GiB


class Check:
    def __init__(self, name, ok, detail, fatal=True):
        self.name, self.ok, self.detail, self.fatal = name, ok, detail, fatal

    def line(self):
        mark = "PASS" if self.ok else ("FAIL" if self.fatal else "WARN")
        return f"  [{mark}] {self.name:24s} {self.detail}"


def _git(args, cwd=REPO_ROOT, env=None, timeout=120):
    return subprocess.run(["git", "-C", cwd] + args, capture_output=True,
                          text=True, env=env, timeout=timeout)


def check_pinned_sha(sha):
    if not sha:
        return Check("PINNED_SHA", False, "no --pinned-sha given")
    p = _git(["cat-file", "-t", sha])
    ok = p.returncode == 0 and p.stdout.strip() == "commit"
    return Check("PINNED_SHA", ok,
                 f"{sha[:12]} resolves to a commit" if ok
                 else f"{sha[:12]} is not a commit in this repository")


def check_clean_worktree():
    p = _git(["status", "--porcelain"])
    dirty = [l for l in p.stdout.splitlines() if l.strip()]
    return Check("CLEAN_WORKTREE", not dirty,
                 "clean" if not dirty
                 else f"{len(dirty)} uncommitted change(s); run identity would be a lie")


def check_cache_outside_worktree(cache_dir):
    try:
        resolved = _cd.resolve_canonical_source_cache(
            canonical=True, repo_root=REPO_ROOT, explicit=cache_dir)
        return Check("CACHE_OUTSIDE_TREE", True, f"{resolved}")
    except Exception as exc:
        return Check("CACHE_OUTSIDE_TREE", False, str(exc)[:120])


def check_cache_persists(cache_dir):
    """Write, close, reopen, read back, delete. The property cfb15819 lacked."""
    try:
        os.makedirs(cache_dir, exist_ok=True)
        probe = os.path.join(cache_dir, ".fc-preflight-probe")
        payload = os.urandom(4096)
        with open(probe, "wb") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        with open(probe, "rb") as fh:
            back = fh.read()
        os.remove(probe)
        ok = back == payload
        return Check("CACHE_PERSISTS", ok,
                     f"{cache_dir} write/fsync/read-back verified" if ok
                     else f"{cache_dir} read back different bytes")
    except Exception as exc:
        return Check("CACHE_PERSISTS", False, f"{cache_dir}: {exc}"[:120])


def check_git_identity():
    """As git will see it -- os.environ or config, not a caller's dict."""
    env_ok = bool(os.environ.get("GIT_AUTHOR_NAME") and
                  os.environ.get("GIT_AUTHOR_EMAIL"))
    cfg = _git(["config", "user.email"]).stdout.strip()
    ok = env_ok or bool(cfg)
    return Check("GIT_IDENTITY", ok,
                 f"env={env_ok} config={cfg or '(unset)'}" if ok
                 else "no author identity in os.environ or git config; commits will fail")


def check_durable_push_real(remote="origin", branch=_cd.DURABLE_BRANCH):
    """A REAL push to a scratch ref, then read back. Not --dry-run.

    --dry-run reported success on this host for refs the server then refused
    at the RPC. The only evidence that a push works is a push that worked.
    """
    git_dir = _cd._git_common_dir(REPO_ROOT)
    if git_dir is None:
        return Check("DURABLE_PUSH_REAL", False, "not a git checkout")
    env = dict(os.environ, GIT_DIR=git_dir)
    ref = f"refs/heads/fc-preflight-{int(time.time())}"
    try:
        blob = subprocess.run(["git", "hash-object", "-w", "--stdin"],
                              input=b"fc-preflight", capture_output=True,
                              cwd=REPO_ROOT, env=env, timeout=60)
        if blob.returncode != 0:
            return Check("DURABLE_PUSH_REAL", False, "hash-object failed")
        sha = blob.stdout.decode().strip()
        idx = os.path.join(tempfile.mkdtemp(), "index")
        ienv = dict(env, GIT_INDEX_FILE=idx)
        subprocess.run(["git", "update-index", "--add", "--cacheinfo",
                        f"100644,{sha},preflight"], cwd=REPO_ROOT, env=ienv,
                       capture_output=True, timeout=30)
        tree = subprocess.run(["git", "write-tree"], cwd=REPO_ROOT, env=ienv,
                              capture_output=True, text=True, timeout=30)
        commit = subprocess.run(["git", "commit-tree", tree.stdout.strip(),
                                 "-m", "fc preflight"], cwd=REPO_ROOT, env=env,
                                capture_output=True, text=True, timeout=30)
        if commit.returncode != 0:
            return Check("DURABLE_PUSH_REAL", False,
                         f"commit-tree failed: {commit.stderr.strip()[:80]}")
        cid = commit.stdout.strip()
        push = subprocess.run(["git", "push", remote, f"{cid}:{ref}"],
                              cwd=REPO_ROOT, env=env, capture_output=True,
                              text=True, timeout=180)
        if push.returncode != 0:
            return Check("DURABLE_PUSH_REAL", False,
                         f"real push refused: {push.stderr.strip()[:100]}")
        # Clean up. Deleting refs may itself be refused here; that is not fatal
        # to the run, but say so rather than leaving a silent stray ref.
        rm = subprocess.run(["git", "push", remote, f":{ref}"], cwd=REPO_ROOT,
                            env=env, capture_output=True, text=True, timeout=180)
        note = "" if rm.returncode == 0 else f" (scratch ref {ref} could not be deleted)"
        return Check("DURABLE_PUSH_REAL", True, f"real push to {remote} verified{note}")
    except Exception as exc:
        return Check("DURABLE_PUSH_REAL", False, f"{type(exc).__name__}: {exc}"[:110])


def check_disk_headroom(path=REPO_ROOT, minimum=MIN_FREE_BYTES):
    try:
        free = shutil.disk_usage(path).free
        ok = free >= minimum
        return Check("DISK_HEADROOM", ok,
                     f"{free / 1024**3:.1f} GiB free (need {minimum / 1024**3:.0f})")
    except Exception as exc:
        return Check("DISK_HEADROOM", False, str(exc)[:100])


def check_resume_feasible(run_id, cache_dir):
    """Only meaningful when resuming. Absent run_id is not a failure."""
    if not run_id:
        return Check("RESUME_FEASIBLE", True, "fresh run; nothing to resume",
                     fatal=False)
    try:
        rep = _cd.resume_feasibility(run_id, cache_dir=cache_dir)
        return Check("RESUME_FEASIBLE", rep["resumable"],
                     f"{run_id}: " + ("resumable" if rep["resumable"]
                                      else (rep["blocker"] or "")[:100]))
    except Exception as exc:
        return Check("RESUME_FEASIBLE", False, str(exc)[:110])


def run_preflight(*, source_cache, pinned_sha=None, resume_run_id=None,
                  remote="origin", skip_push=False):
    checks = [
        check_pinned_sha(pinned_sha) if pinned_sha else
        Check("PINNED_SHA", True, "not pinned (fresh run)", fatal=False),
        check_clean_worktree(),
        check_cache_outside_worktree(source_cache),
        check_cache_persists(source_cache),
        check_git_identity(),
        check_disk_headroom(),
        check_resume_feasible(resume_run_id, source_cache),
    ]
    if not skip_push:
        checks.append(check_durable_push_real(remote=remote))
    else:
        checks.append(Check("DURABLE_PUSH_REAL", True,
                            "SKIPPED -- durability is unproven", fatal=False))
    blocking = [c for c in checks if not c.ok and c.fatal]
    return {"ok": not blocking, "checks": checks,
            "blocking": [c.name for c in blocking]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source-cache", default=_cd.DEFAULT_CANONICAL_SOURCE_CACHE)
    ap.add_argument("--pinned-sha")
    ap.add_argument("--resume-run-id")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--skip-push", action="store_true",
                    help="skip the real-push check (leaves durability unproven)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rep = run_preflight(source_cache=a.source_cache, pinned_sha=a.pinned_sha,
                        resume_run_id=a.resume_run_id, remote=a.remote,
                        skip_push=a.skip_push)
    if a.json:
        print(json.dumps({"ok": rep["ok"], "blocking": rep["blocking"],
                          "checks": [{"name": c.name, "ok": c.ok,
                                      "detail": c.detail, "fatal": c.fatal}
                                     for c in rep["checks"]]}, indent=2))
    else:
        print("CANONICAL RUN PREFLIGHT")
        for c in rep["checks"]:
            print(c.line())
        print()
        print("  VERDICT: " + ("CLEARED" if rep["ok"]
                               else "BLOCKED -- " + ", ".join(rep["blocking"])))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

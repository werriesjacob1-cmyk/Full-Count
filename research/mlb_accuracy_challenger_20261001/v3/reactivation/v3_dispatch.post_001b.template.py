#!/usr/bin/env python3
"""MLB V3 prospective-collection dispatcher (operational wiring only; NOT part of the
activated implementation). It never changes v3 code, the prereg, coefficients or old
seals, never reads outcomes, never backfills.

POST-001B TEMPLATE (FC-MLB-001B reactivation package). Every placeholder (at-at-NAME-at-at) is bound ONLY by
reactivation/prepare_activation.py from the Codex-passed, SUPERCHAD/Jacob-approved commit and Jacob's
authorization comment. A file with any placeholder left refuses to run.

Each invocation:
  1. bootstraps an exact, clean worktree of the ACTIVATED implementation commit
     @@IMPLEMENTATION_COMMIT@@ (v3 tree @@IMPLEMENTATION_TREE@@), restores the persisted activation record
     from the evidence ref (sha256-verified), runs the dispatcher preflight (bound template, git, request branch
     carrying the bound record workflow), and runs the EXISTING activation verifier (must be (True, []));
  2. plans today's (America/New_York) DAY/NIGHT units with the EXISTING
     schedule_plan.plan() (first-pitch-aware, TBD games excluded);
  3. for every unit that is post-boundary, not yet sealed on the evidence ref, not yet requested, and
     whose latest_start_utc is 0..AHEAD minutes away, pushes ONE request file requests/<unit>.json to the request
     branch. The bound workflow .github/workflows/v3-unit-record.yml (reactivation/v3_unit_record.workflow.template.yml)
     then runs the EXISTING runner.py --mode prospective in a GitHub Actions job (the pinned runtime sandbox needs
     root): --phase stage -> tape uploaded as ONE Actions artifact (TEMPORARY authoritative store, Jacob 2026-10-06)
     -> artifact_api confirm (read-back proof) -> --phase publish (seal, #91 receipt, TSA) -> verify-b on a fresh
     runner. A unit whose latest start has passed is reported MISSED and never run (no backfill).
  4. every tick runs retention_monitor.py on the evidence ref: MIGRATE_WARNING (<= 45 days left) and worse are
     reported as RETENTION_ALERT so the trigger session raises it on #91 (migration milestone FC-MLB-001C).
  5. --verify-sealed additionally runs verify_evidence.verify_unit on every chained seal (no outcomes are read).
     Artifact-backed tapes can only be downloaded inside Actions, so here they are reported TAPE_MISSING unless
     V3B_ARTIFACT_DOWNLOAD_DIR holds them; the authoritative post-seal replay is the workflow's verify-b job.

STORAGE: R2 is NOT required for this path (no Cloudflare account action). It remains the durable target: every
artifact-backed tape is migrated byte-for-byte by tape_migration.py before retention threatens it.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

AUTH_COMMIT = "@@IMPLEMENTATION_COMMIT@@"
AUTH_TREE = "@@IMPLEMENTATION_TREE@@"
RUNTIME_DIGEST = "@@RUNTIME_DIGEST@@"          # must equal runtime_image.MANIFEST_DIGEST at AUTH_COMMIT
PREREG_BLOB = "71e7d2828df20a01f476376efcc0c651828dcaf2"
SHADOW_PIN = "7d3ebacd55c34c6ec78030bdeca70799e56d4764"
EVIDENCE_REF = "claude/mlb-challenger-v3-evidence"
ACTIVATION_ON_EVIDENCE = "ACTIVATION/ACTIVATION_@@JACOB_COMMENT_ID@@.json"
ACTIVATION_SHA256 = "@@ACTIVATION_SHA256@@"
REQUEST_BRANCH = "@@REQUEST_BRANCH@@"          # carries ONLY the bound record workflow + requests/<unit>.json
RECORD_WORKFLOW_SHA256 = "@@RECORD_WORKFLOW_SHA256@@"   # the bound .github/workflows/v3-unit-record.yml
V3_REL = "research/mlb_accuracy_challenger_20261001/v3"
PREREG_REL = "research/mlb_accuracy_challenger_prereg_v3_20261001.md"
BOUNDARY_UTC = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)
AHEAD = timedelta(minutes=75)          # > 60-minute firing interval, so every unit gets a firing
BASE = os.environ.get("MLBV3_BASE", "/tmp/mlbv3")
ET = ZoneInfo("America/New_York")


def sh(*cmd, cwd=None, check=True, env=None):
    r = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, env=env)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)} -> rc {r.returncode}: {r.stderr.strip()[-800:]}")
    return r.stdout.strip()


def git(repo, *a, check=True):
    return sh("git", "-C", repo, *a, check=check)


def fetch_refs(repo):
    for ref in (EVIDENCE_REF, "main"):
        spec = f"+refs/heads/{ref}:refs/remotes/origin/{ref}"
        have = git(repo, "config", "--get-all", "remote.origin.fetch", check=False).splitlines()
        if spec not in have and "+refs/heads/*:refs/remotes/origin/*" not in have:
            git(repo, "config", "--add", "remote.origin.fetch", spec)   # so runner.py's own fetch updates origin/<ref>
        git(repo, "fetch", "-q", "--depth", "1", "origin", spec)


def bootstrap(repo):
    impl = os.path.join(BASE, "impl")
    os.makedirs(BASE, exist_ok=True)
    fetch_refs(repo)
    for sha in (AUTH_COMMIT, SHADOW_PIN):
        if git(repo, "cat-file", "-t", sha, check=False) != "commit":
            git(repo, "fetch", "-q", "--depth", "1", "origin", sha)   # exact pinned commit only
    if not os.path.exists(os.path.join(impl, ".git")):
        git(repo, "worktree", "add", "--detach", impl, AUTH_COMMIT)
    git(impl, "sparse-checkout", "disable", check=False)
    head = git(impl, "rev-parse", "HEAD")
    tree = git(impl, "rev-parse", f"HEAD:{V3_REL}")
    blob = git(impl, "rev-parse", f"HEAD:{PREREG_REL}")
    if (head, tree, blob) != (AUTH_COMMIT, AUTH_TREE, PREREG_BLOB):
        raise SystemExit(f"IDENTITY MISMATCH head={head} tree={tree} prereg_blob={blob}: refusing")
    act = os.path.join(impl, V3_REL, "ACTIVATION.json")
    raw = subprocess.check_output(["git", "-C", repo, "show", f"origin/{EVIDENCE_REF}:{ACTIVATION_ON_EVIDENCE}"])
    if hashlib.sha256(raw).hexdigest() != ACTIVATION_SHA256:
        raise SystemExit("persisted activation record hash mismatch: refusing")
    if not os.path.exists(act) or open(act, "rb").read() != raw:
        with open(act, "wb") as fh:
            fh.write(raw)
    env = dict(os.environ, MLB_V3_ACTIVATION="JACOB_AUTHORIZED",
               V3B_RUNTIME_CACHE=os.path.join(BASE, "runtime"), V3B_WHEEL_DIR=os.path.join(BASE, "runtime", "wheels"))
    preflight_001b(impl, env)
    out = sh(sys.executable, "-c", "import sys,json; sys.path.insert(0, sys.argv[1]); import activation as AC; "
             "ok, r = AC.verify_activation(sys.argv[2]); print(json.dumps([ok, r]))",
             os.path.join(impl, V3_REL), impl, env=env)
    ok, reasons = json.loads(out.splitlines()[-1])
    if not ok:
        raise SystemExit(f"ACTIVATION VERIFIER REJECTED: {reasons}")
    return impl, env


def preflight_001b(impl, env):
    """Dispatcher-side preflight. Recording itself runs in GitHub Actions (the bound record workflow), so the
    dispatcher session needs no root, sandbox tools, runtime cache or tape-store credentials -- only an exact,
    bound template and a request branch whose record workflow is byte-identical to the bound one."""
    import shutil
    if "@@" in AUTH_COMMIT + AUTH_TREE + RUNTIME_DIGEST + ACTIVATION_ON_EVIDENCE + ACTIVATION_SHA256 + REQUEST_BRANCH \
            + RECORD_WORKFLOW_SHA256:
        raise SystemExit("UNBOUND TEMPLATE: placeholders remain; bind with reactivation/prepare_activation.py")
    if not shutil.which("git"):
        raise SystemExit("PREFLIGHT: git missing")
    repo = git(impl, "rev-parse", "--path-format=absolute", "--git-common-dir")
    git(repo, "fetch", "-q", "origin", f"+refs/heads/{REQUEST_BRANCH}:refs/remotes/origin/{REQUEST_BRANCH}")
    wf = subprocess.check_output(["git", "-C", repo, "show", f"origin/{REQUEST_BRANCH}:.github/workflows/v3-unit-record.yml"])
    if hashlib.sha256(wf).hexdigest() != RECORD_WORKFLOW_SHA256:
        raise SystemExit("PREFLIGHT: the request branch's record workflow is not the bound one: refusing")
    ident = sh(sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import runtime_image as RI; "
               "print(RI.MANIFEST_DIGEST)", os.path.join(impl, V3_REL))
    if ident.splitlines()[-1] != RUNTIME_DIGEST:
        raise SystemExit(f"PREFLIGHT: runtime digest at the implementation != bound {RUNTIME_DIGEST}")
    return {"manifest_digest": RUNTIME_DIGEST}


def requested_units(repo):
    out = git(repo, "ls-tree", "--name-only", f"origin/{REQUEST_BRANCH}", "requests/", check=False)
    return {os.path.basename(p)[:-5] for p in out.splitlines() if p.endswith(".json")}


def push_request(repo, u, now):
    """One request = one new file on the request branch (append-only; never rewritten, never force-pushed)."""
    name = f"{u['date']}_{u['window']}"
    wt = os.path.join(BASE, "req_wt")
    git(repo, "fetch", "-q", "origin", f"+refs/heads/{REQUEST_BRANCH}:refs/remotes/origin/{REQUEST_BRANCH}")
    git(repo, "worktree", "remove", "--force", wt, check=False)
    git(repo, "worktree", "add", "--detach", wt, f"origin/{REQUEST_BRANCH}")
    try:
        path = os.path.join(wt, "requests", f"{name}.json")
        if os.path.exists(path):
            return "ALREADY_REQUESTED"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "x") as fh:
            json.dump({"unit": name, "date": u["date"], "window": u["window"], "impl": AUTH_COMMIT,
                       "first_pitch_utc": u["first_pitch_utc"], "latest_start_utc": u["latest_start_utc"],
                       "requested_at": now.isoformat()}, fh, indent=1, sort_keys=True)
        git(wt, "add", "--", f"requests/{name}.json")
        git(wt, "commit", "-q", "-m", f"MLB v3 unit request {name}")
        git(wt, "push", "-q", "origin", f"HEAD:{REQUEST_BRANCH}")
        return "REQUESTED"
    finally:
        git(repo, "worktree", "remove", "--force", wt, check=False)


def retention(repo, impl):
    """retention_monitor.py over the evidence ref (sealed expires_at; offline). Never silent."""
    ev = os.path.join(BASE, "retention_ev")
    git(repo, "worktree", "remove", "--force", ev, check=False)
    git(repo, "worktree", "add", "--detach", ev, f"origin/{EVIDENCE_REF}")
    try:
        r = subprocess.run([sys.executable, os.path.join(impl, V3_REL, "retention_monitor.py"), "--evidence-root", ev],
                           capture_output=True, text=True)
        out = r.stdout[r.stdout.index("{"):] if "{" in r.stdout else "{}"
        rep = json.loads(out)
        rep["alert"] = "RETENTION_ALERT" if r.returncode else None
        return rep
    finally:
        git(repo, "worktree", "remove", "--force", ev, check=False)


def sealed_units(repo):
    out = git(repo, "ls-tree", "--name-only", f"origin/{EVIDENCE_REF}", "seals/", check=False)
    return {os.path.basename(p) for p in out.splitlines() if p}


def plan_today(impl, now):
    v3 = os.path.join(impl, V3_REL)
    sys.path.insert(0, v3)
    import runner as RN          # noqa: E402  (existing, activated code)
    import schedule_plan as SP   # noqa: E402
    date = now.astimezone(ET).date().isoformat()
    sched = RN.schedule_snapshot(date)
    return date, SP.plan(sched, now.isoformat())


def utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def verify_sealed(repo, impl):
    ev = os.path.join(BASE, "verify_ev")
    git(repo, "worktree", "remove", "--force", ev, check=False)
    git(repo, "worktree", "add", "--detach", ev, f"origin/{EVIDENCE_REF}")
    try:
        v3 = os.path.join(impl, V3_REL)
        code = ("import sys,json; sys.path.insert(0, sys.argv[1]); import verify_evidence as VE; "
                "out=[]\nfor s in VE.load_chain(sys.argv[2]):\n"
                "    st, d, _ = VE.verify_unit(sys.argv[2], s); out.append([s['date']+'_'+s['window'], s['seal_sha256'], st, d])\n"
                "print(json.dumps(out, default=str))")
        return json.loads(sh(sys.executable, "-c", code, v3, ev, cwd=impl).splitlines()[-1])
    finally:
        git(repo, "worktree", "remove", "--force", ev, check=False)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="a clone of werriesjacob1-cmyk/Full-Count")
    ap.add_argument("--check", action="store_true", help="bootstrap + verify + plan only; never runs a unit")
    ap.add_argument("--verify-sealed", action="store_true")
    ap.add_argument("--simulate-now", help="--check only: plan as if it were this UTC instant (never runs a unit)")
    a = ap.parse_args(argv)
    if a.simulate_now and not a.check:
        raise SystemExit("--simulate-now is allowed only with --check")
    os.makedirs(BASE, exist_ok=True)
    lock = open(os.path.join(BASE, "dispatch.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({"status": "BUSY_ANOTHER_DISPATCH_RUNNING"}))
        return 0
    impl, env = bootstrap(a.repo)
    now = utc(a.simulate_now) if a.simulate_now else datetime.now(timezone.utc)
    clock = (lambda: now) if a.simulate_now else (lambda: datetime.now(timezone.utc))
    date, units = plan_today(impl, now)
    done = sealed_units(a.repo)
    asked = requested_units(a.repo)
    report = {"at": now.isoformat(), "et_date": date, "impl": AUTH_COMMIT, "activation": "VERIFIED", "units": []}
    for u in sorted(units, key=lambda x: x["latest_start_utc"]):
        name = f"{u['date']}_{u['window']}"
        ls, fp = utc(u["latest_start_utc"]), utc(u["first_pitch_utc"])
        row = {"unit": name, "first_pitch_utc": u["first_pitch_utc"], "latest_start_utc": u["latest_start_utc"]}
        if fp <= BOUNDARY_UTC:
            row["action"] = "SKIP_PRE_BOUNDARY_NONCONFIRMATORY"
        elif name in done:
            row["action"] = "ALREADY_SEALED"
        elif name in asked:
            row["action"] = "ALREADY_REQUESTED"          # the workflow run owns it; a failed run is a MISS, not retried
        elif u["status"] != "PLANNED" or clock() > ls:
            row["action"] = "MISSED_NO_BACKFILL"
        elif ls - clock() > AHEAD:
            row["action"] = "NOT_YET_DUE"
        elif a.check:
            row["action"] = "DUE_BUT_CHECK_ONLY"
        else:
            fetch_refs(a.repo)
            if name in sealed_units(a.repo):
                row["action"] = "ALREADY_SEALED"
            else:
                row["action"] = push_request(a.repo, u, clock())
        report["units"].append(row)
    report["retention"] = retention(a.repo, impl)
    if a.verify_sealed:
        fetch_refs(a.repo)
        report["verify_sealed"] = verify_sealed(a.repo, impl)
    report["chain_head"] = git(a.repo, "rev-parse", f"origin/{EVIDENCE_REF}")
    print(json.dumps(report, indent=1, default=str))
    with open(os.path.join(BASE, "dispatch_log.jsonl"), "a") as fh:
        fh.write(json.dumps(report, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

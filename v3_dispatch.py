#!/usr/bin/env python3
"""MLB V3 prospective-collection dispatcher (operational wiring only; NOT part of the
activated implementation). It never changes v3 code, the prereg, coefficients or old
seals, never reads outcomes, never backfills.

Each invocation:
  1. bootstraps an exact, clean worktree of the ACTIVATED implementation commit
     504ca9cdb9 (v3 tree a7c9443), restores the persisted activation record from the
     evidence ref (sha256-verified), installs the hash-pinned time-machine wheel if
     missing, and runs the EXISTING activation verifier (must be (True, []));
  2. plans today's (America/New_York) DAY/NIGHT units with the EXISTING
     schedule_plan.plan() (first-pitch-aware, TBD games excluded);
  3. for every unit that is post-boundary, not yet sealed on the evidence ref, and
     whose latest_start_utc is 0..AHEAD minutes away, runs the EXISTING
     runner.py --mode prospective (which re-verifies activation, guards every
     step, seals, posts the #91 receipt, requests TSA tokens, and verifies them).
     A unit whose latest start has passed is reported MISSED and never run.
  4. --verify-sealed additionally runs verify_evidence.verify_unit on every chained
     seal (no outcomes are read).
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

AUTH_COMMIT = "504ca9cdb972d218907eb4eec13852cf5a7981d2"
AUTH_TREE = "a7c94431628c7714cfadd0e9ed911c490687ad20"
PREREG_BLOB = "71e7d2828df20a01f476376efcc0c651828dcaf2"
SHADOW_PIN = "7d3ebacd55c34c6ec78030bdeca70799e56d4764"
EVIDENCE_REF = "claude/mlb-challenger-v3-evidence"
ACTIVATION_ON_EVIDENCE = "ACTIVATION/ACTIVATION_5953838152.json"
ACTIVATION_SHA256 = "99d2655192506817b2ae18339adfe14dfa8131c6ed90448cbee38afba1a5794e"
V3_REL = "research/mlb_accuracy_challenger_20261001/v3"
PREREG_REL = "research/mlb_accuracy_challenger_prereg_v3_20261001.md"
BOUNDARY_UTC = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)
AHEAD = timedelta(minutes=75)          # > 60-minute firing interval, so every unit gets a firing
RUN_TIMEOUT_S = 75 * 60
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
    try:
        import time_machine  # noqa: F401
    except ImportError:
        sh(sys.executable, "-m", "pip", "install", "-q", "--require-hashes", "-r",
           os.path.join(impl, V3_REL, "requirements-v3.txt"))
    env = dict(os.environ, MLB_V3_ACTIVATION="JACOB_AUTHORIZED")
    out = sh(sys.executable, "-c", "import sys,json; sys.path.insert(0, sys.argv[1]); import activation as AC; "
             "ok, r = AC.verify_activation(sys.argv[2]); print(json.dumps([ok, r]))",
             os.path.join(impl, V3_REL), impl, env=env)
    ok, reasons = json.loads(out.splitlines()[-1])
    if not ok:
        raise SystemExit(f"ACTIVATION VERIFIER REJECTED: {reasons}")
    return impl, env


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
    report = {"at": now.isoformat(), "et_date": date, "impl": AUTH_COMMIT, "activation": "VERIFIED", "units": []}
    for u in sorted(units, key=lambda x: x["latest_start_utc"]):
        name = f"{u['date']}_{u['window']}"
        ls, fp = utc(u["latest_start_utc"]), utc(u["first_pitch_utc"])
        row = {"unit": name, "first_pitch_utc": u["first_pitch_utc"], "latest_start_utc": u["latest_start_utc"]}
        if fp <= BOUNDARY_UTC:
            row["action"] = "SKIP_PRE_BOUNDARY_NONCONFIRMATORY"
        elif name in done:
            row["action"] = "ALREADY_SEALED"
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
                out = os.path.join(BASE, "runs", f"{name}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
                os.makedirs(out)
                cmd = [sys.executable, os.path.join(impl, V3_REL, "runner.py"), "--mode", "prospective",
                       "--repo", impl, "--date", u["date"], "--window", u["window"], "--out", out]
                with open(out + ".log", "w") as log:
                    try:
                        rc = subprocess.run(cmd, cwd=impl, env=env, stdout=log, stderr=subprocess.STDOUT,
                                            timeout=RUN_TIMEOUT_S).returncode
                    except subprocess.TimeoutExpired:
                        rc = "TIMEOUT"
                tail = open(out + ".log").read().strip().splitlines()[-3:]
                row.update({"action": "RAN", "rc": rc, "out": out, "log_tail": tail})
        report["units"].append(row)
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

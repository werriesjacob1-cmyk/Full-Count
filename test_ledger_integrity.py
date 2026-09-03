#!/usr/bin/env python3
"""Tests for ledger_integrity.py -- the public-ledger monotonicity tripwire.

Every assertion here has a matching NEGATIVE case. A check nobody has watched
fail is not a check: the 2026-09-03 review found an acceptance suite whose
"no phantom tools" assertion passed a tool named
`mcp__github__merge_pull_request_and_deploy_TOTALLY_FAKE`, because the
predicate exempted the whole namespace it was meant to police.

These build throwaway git repositories in a temp dir. No network, no fixtures
committed to this repository, and deliberately no synthetic public picks
written anywhere near real results/.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import ledger_integrity

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}\n         got  {got!r}\n         want {want!r}")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def pick(gid):
    return {"id": gid, "grade": "hit", "hit_probability": 0.61, "market_implied": 0.54}


def build(repo, grades, registry_ids):
    """Write one grades file and a registry, commit, return the commit sha."""
    os.makedirs(os.path.join(repo, "results"), exist_ok=True)
    os.makedirs(os.path.join(repo, "data", "public_top_picks"), exist_ok=True)
    with open(os.path.join(repo, "results", "grades_2026-09-01.json"), "w") as fh:
        json.dump({"public_top_picks": [pick(g) for g in grades]}, fh)
    with open(os.path.join(repo, "data", "public_top_picks", "registry.json"), "w") as fh:
        json.dump({"entries": {r: {"slate_date": "2026-09-01"} for r in registry_ids}}, fh)
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "state", "--allow-empty")
    return git(repo, "rev-parse", "HEAD")


def main():
    tmp = tempfile.mkdtemp(prefix="ledger-integrity-")
    cwd = os.getcwd()
    try:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        git(repo, "init", "-q")
        A = build(repo, ["id-1", "id-2", "id-3"], ["id-1", "id-2", "id-3"])
        os.chdir(repo)

        # 1. unchanged estate -> clean
        B_same = build(repo, ["id-1", "id-2", "id-3"], ["id-1", "id-2", "id-3"])
        check("identical estate is clean", ledger_integrity.compare(A, B_same), [])

        # 2. growth -> clean. Publishing new picks must never trip the wire.
        B_grow = build(repo, ["id-1", "id-2", "id-3", "id-4"],
                       ["id-1", "id-2", "id-3", "id-4"])
        check("added identity is clean", ledger_integrity.compare(A, B_grow), [])

        # 3. THE CASE THAT MATTERS: one identity vanishes from the graded
        #    ledger while everything else is a legitimate forward step.
        B_lose1 = build(repo, ["id-1", "id-3", "id-4"], ["id-1", "id-2", "id-3", "id-4"])
        lost = ledger_integrity.compare(A, B_lose1)
        check("single graded loss is caught, one estate only", len(lost), 1)
        check("single graded loss names the exact identity",
              lost[0][1] if lost else None, ["id-2"])
        check("single graded loss is attributed to the graded ledger",
              lost[0][0].startswith("graded ledger") if lost else None, True)

        # 4. The two estates regressed by different amounts in the real
        #    incident (12 vs 6), so a registry-only loss must be caught on its
        #    own and not masked by a healthy graded ledger.
        B_reg = build(repo, ["id-1", "id-2", "id-3"], ["id-1", "id-3"])
        lost = ledger_integrity.compare(A, B_reg)
        check("registry-only loss is caught", len(lost), 1)
        check("registry-only loss is attributed to the registry",
              lost[0][0].startswith("publication registry") if lost else None, True)
        check("registry-only loss names the identity",
              lost[0][1] if lost else None, ["id-2"])

        # 5. Both estates regressing is reported as both, not collapsed.
        B_both = build(repo, ["id-1"], ["id-1"])
        check("both estates regressing reports both",
              len(ledger_integrity.compare(A, B_both)), 2)

        # 6. Fail closed. Unreadable evidence must never read as PASS.
        os.remove(os.path.join(repo, "data", "public_top_picks", "registry.json"))
        git(repo, "add", "-A")
        git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "drop registry")
        B_missing = git(repo, "rev-parse", "HEAD")
        try:
            ledger_integrity.compare(A, B_missing)
            check("missing registry raises rather than passing", False, True)
        except ledger_integrity.Unreadable:
            check("missing registry raises rather than passing", True, True)
        check("missing registry exits nonzero",
              ledger_integrity.main(["x", A, B_missing]), 1)

        # 7. Corrupt JSON is unverifiable, which is also not a pass.
        with open(os.path.join(repo, "results", "grades_2026-09-01.json"), "w") as fh:
            fh.write("{not json")
        git(repo, "add", "-A")
        git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "corrupt")
        B_corrupt = git(repo, "rev-parse", "HEAD")
        check("corrupt grades file exits nonzero",
              ledger_integrity.main(["x", A, B_corrupt]), 1)

        # 8. A clean comparison exits zero.
        check("clean comparison exits zero", ledger_integrity.main(["x", A, B_grow]), 0)
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

"""Deliberate race proof for the prospective research ledger.

Protocol section 10 requires remote durability that is actually concurrency
safe. Mission 1's implementation retried `git push` with backoff, which cannot
resolve a non-fast-forward rejection: the local ref never advances, so attempt 4
is rejected exactly like attempt 1.

These tests stand up a real bare git remote and race two independent writers
against it on purpose.
"""

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

from backtest import prospective_ledger as pl

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILURES.append(name)


def raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def new_clone(remote, path, name):
    git(["clone", "--quiet", remote, path], "/")
    git(["config", "user.name", name], path)
    git(["config", "user.email", f"{name}@test"], path)
    return path


def ev(key, payload, etype=pl.EVENT_SNAPSHOT_CAPTURED):
    return pl.make_event(etype, key, payload)


def remote_events(remote, slate, tmp):
    """Read the ledger file straight out of the bare remote."""
    probe = os.path.join(tmp, f"probe-{random.randint(0, 10**9)}")
    git(["clone", "--quiet", "--branch", pl.LEDGER_BRANCH, remote, probe], "/")
    path = os.path.join(probe, pl.ledger_relpath(slate))
    return pl.read_events(path) if os.path.exists(path) else []


TMP = tempfile.mkdtemp(prefix="ledger-race-")
SLATE = "2026-09-02"
try:
    REMOTE = os.path.join(TMP, "remote.git")
    git(["init", "--quiet", "--bare", REMOTE], "/")

    print("Check 1: event ontology keeps the lifecycle facts distinct")
    check("9 event types", len(pl.EVENT_TYPES) == 9, str(len(pl.EVENT_TYPES)))
    for t in ("snapshot_captured", "deployment_observed", "public_exposure_bound",
              "epoch_selection_sealed", "decisive_epoch_designated",
              "pregame_receipt", "settlement", "epoch_failed_closed",
              "no_primary_epoch"):
        check(f"has {t}", t in pl.EVENT_TYPES)
    check("epoch_bound is GONE (it was the semantic defect)",
          "epoch_bound" not in pl.EVENT_TYPES)
    check("settlement is the only non-pregame type",
          set(pl.EVENT_TYPES) - set(pl.PREGAME_EVENT_TYPES) == {"settlement"})

    print("\nCheck 2: outcome scanning covers EVERY pregame type, not just receipts")
    for t in pl.PREGAME_EVENT_TYPES:
        check(f"{t} refuses an outcome",
              raises(lambda tt=t: pl.make_event(tt, "k", {"outcome": 1}), Exception))
    check("settlement MAY carry a decision",
          pl.make_event(pl.EVENT_SETTLEMENT, "k", {"outcome": "hit"})["body"]["outcome"] == "hit")

    print("\nCheck 3: stage 1 and stage 2 no longer collide on one key")
    # The Mission 1 defect: capture wrote the predeployment candidate under
    # `epoch_bound`, so the real binding could never be recorded.
    p = os.path.join(TMP, "collide", pl.ledger_relpath(SLATE))
    same_key = "2026-09-02:abc123"
    r1 = pl.append_events(p, [ev(same_key, {"publicly_converged": False})])
    r2 = pl.append_events(p, [pl.make_event(pl.EVENT_PUBLIC_EXPOSURE_BOUND,
                                            same_key, {"publicly_converged": True})])
    check("snapshot_captured appended", r1["appended"] == 1)
    check("public_exposure_bound appended under the SAME id", r2["appended"] == 1)
    check("both events coexist", len(pl.read_events(p)) == 2)

    print("\nCheck 4: ledger is partitioned per slate date")
    check("path carries the date", pl.ledger_relpath("2026-09-02")
          == "prospective/hits_pa_v1/2026-09-02.jsonl")
    check("different dates are different files",
          pl.ledger_relpath("2026-09-02") != pl.ledger_relpath("2026-09-03"))

    print("\nCheck 5: THE RACE — two writers, one remote branch, both survive")
    A = new_clone(REMOTE, os.path.join(TMP, "writerA"), "writerA")
    B = new_clone(REMOTE, os.path.join(TMP, "writerB"), "writerB")
    C = new_clone(REMOTE, os.path.join(TMP, "racer"), "racer")

    a_ev = ev("epoch-A", {"who": "A"})
    b_ev = ev("epoch-B", {"who": "B"})
    c_ev = ev("epoch-C", {"who": "C"})

    ra = pl.append_and_push(A, SLATE, [a_ev], remote="origin")
    check("writer A durable", ra.get("durable") is True, str(ra))

    # Force a REAL non-fast-forward: inject a competing push from a third
    # writer in the window between B resolving the remote and B pushing.
    original_resolve = pl._resolve_onto_remote
    injected = {"n": 0}

    def racing_resolve(worktree, branch, remote):
        state = original_resolve(worktree, branch, remote)
        if worktree == B and injected["n"] == 0:
            injected["n"] += 1
            # C wins the race while B holds a now-stale base.
            pl.append_and_push(C, SLATE, [c_ev], remote="origin")
        return state

    pl._resolve_onto_remote = racing_resolve
    try:
        rb = pl.append_and_push(B, SLATE, [b_ev], remote="origin",
                                base_delay=0.01, rng=random.Random(1))
    finally:
        pl._resolve_onto_remote = original_resolve

    check("a real race was injected", injected["n"] == 1)
    check("writer B recovered and is durable", rb.get("durable") is True, str(rb))
    check("B needed more than one attempt (it was really rejected)",
          rb.get("attempts", 1) > 1, f"attempts={rb.get('attempts')}")

    final = remote_events(REMOTE, SLATE, TMP)
    keys = sorted(e["idempotent_key"] for e in final)
    check("ALL THREE non-conflicting events survive on the remote",
          keys == ["epoch-A", "epoch-B", "epoch-C"], str(keys))
    check("no event was lost to the race", len(final) == 3, str(len(final)))

    print("\nCheck 6: identical re-append dedupes; nothing duplicated remotely")
    again = pl.append_and_push(A, SLATE, [a_ev], remote="origin")
    check("re-push of an identical event is durable", again.get("durable") is True)
    check("and is a no-op", again.get("noop") is True, str(again))
    check("remote still holds exactly 3", len(remote_events(REMOTE, SLATE, TMP)) == 3)

    print("\nCheck 7: same key + different content is a HARD failure, never merged")
    mutated = ev("epoch-A", {"who": "A", "tampered": True})
    check("append_and_push raises LedgerConflict",
          raises(lambda: pl.append_and_push(A, SLATE, [mutated], remote="origin"),
                 pl.LedgerConflict))
    check("remote is unchanged after the refused edit",
          len(remote_events(REMOTE, SLATE, TMP)) == 3)

    print("\nCheck 8: a local-only commit is NOT reported as durable evidence")
    D = new_clone(REMOTE, os.path.join(TMP, "writerD"), "writerD")
    git(["remote", "set-url", "origin", os.path.join(TMP, "does-not-exist.git")], D)
    rd = pl.append_and_push(D, SLATE, [ev("epoch-D", {"who": "D"})],
                            remote="origin", max_attempts=2, base_delay=0.01,
                            rng=random.Random(2))
    check("durable is False", rd.get("durable") is False, str(rd))
    check("pushed is False", rd.get("pushed") is False)
    check("the report says local-only is not evidence",
          "not durable evidence" in (rd.get("note") or "").lower())
    check("epoch-D never reached the remote",
          "epoch-D" not in [e["idempotent_key"] for e in remote_events(REMOTE, SLATE, TMP)])

    print("\nCheck 9: two writers both starting from NO remote branch")
    R2 = os.path.join(TMP, "remote2.git")
    git(["init", "--quiet", "--bare", R2], "/")
    E = new_clone(R2, os.path.join(TMP, "writerE"), "writerE")
    F = new_clone(R2, os.path.join(TMP, "writerF"), "writerF")
    re_ = pl.append_and_push(E, SLATE, [ev("epoch-E", {"who": "E"})], remote="origin")
    rf = pl.append_and_push(F, SLATE, [ev("epoch-F", {"who": "F"})], remote="origin",
                            base_delay=0.01, rng=random.Random(3))
    check("first orphan writer durable", re_.get("durable") is True, str(re_))
    check("second writer durable despite starting orphan-less",
          rf.get("durable") is True, str(rf))
    k2 = sorted(e["idempotent_key"] for e in remote_events(R2, SLATE, TMP))
    check("both events survive on the second remote", k2 == ["epoch-E", "epoch-F"], str(k2))

    print("\nCheck 10: a corrupt ledger fails loudly")
    corrupt = os.path.join(TMP, "corrupt", pl.ledger_relpath(SLATE))
    pl.append_events(corrupt, [ev("k", {"a": 1})])
    with open(corrupt, "a") as fh:
        fh.write("{not json\n")
    check("read_events raises", raises(lambda: pl.read_events(corrupt), pl.LedgerConflict))

    print("\nCheck 11: crash safety is preserved")
    csafe = os.path.join(TMP, "csafe", pl.ledger_relpath(SLATE))
    for i in range(15):
        pl.append_events(csafe, [ev(f"k{i}", {"i": i})])
    raw = open(csafe, "rb").read()
    check("file ends with a newline", raw.endswith(b"\n"))
    check("all 15 parse", len(pl.read_events(csafe)) == 15)
    check("no temp files left", not [f for f in os.listdir(os.path.dirname(csafe))
                                     if f.startswith(".ledger-")])

    print("\nCheck 12: never force-pushes over evidence")
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "backtest", "prospective_ledger.py")).read()
    body = src.split('"""', 2)[2]
    check("no --force anywhere", "--force" not in body)
    check("no force-with-lease", "force-with-lease" not in body)
    check("no rebase (re-derive instead)", "rebase" not in body)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print()
if FAILURES:
    print(f"FAILED {len(FAILURES)}: {FAILURES}")
    sys.exit(1)
print("All ledger concurrency checks passed.")

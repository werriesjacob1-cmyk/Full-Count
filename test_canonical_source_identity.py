#!/usr/bin/env python3
"""Adversarial proof that a canonical resume cannot silently change source vintage.

Reproduces the 2026-08-27 near-miss. Run …d6a1050f generated 40 dates from a
Statcast cache inside its worktree; the container was reclaimed; a DIFFERENT
cache survived elsewhere with the same schema, overlapping coverage and a
plausible row count. StatcastStore.load() would have accepted it, and
validate_statcast_cache() would have called it usable -- because that function
proves a cache is USABLE, never that it is THE SAME ONE.

Tests A-H below are the mission's required cases. The important ones are B/C/D:
each substitutes a cache that matches on every supporting property a reasonable
person would check (filename, coverage, schema, and in C the exact row count)
and differs only in bytes. All must fail closed BEFORE any row is generated.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _k, _v in (("GIT_AUTHOR_NAME", "fc-test"), ("GIT_AUTHOR_EMAIL", "fc@example.invalid"),
               ("GIT_COMMITTER_NAME", "fc-test"), ("GIT_COMMITTER_EMAIL", "fc@example.invalid")):
    os.environ[_k] = _v

import backtest.canonical_durability as cd

PASS = FAIL = 0


def ok(m):
    global PASS
    PASS += 1
    print(f"  ok   {m}")


def bad(m):
    global FAIL
    FAIL += 1
    print(f"  FAIL {m}")


def check(m, got, want):
    ok(m) if got == want else bad(f"{m} (want {want!r}, got {got!r})")


def make_parquet(path, rows, *, start="2024-03-15", end="2026-08-24", salt=0.0):
    """A Statcast-shaped parquet. `salt` changes BYTES without changing shape."""
    import pandas as pd
    from backtest.engine import STATCAST_COLUMNS
    cols = {c: [0] * rows for c in STATCAST_COLUMNS}
    cols["game_date"] = [start] * (rows - 1) + [end]
    cols["launch_speed"] = [90.0 + salt + i * 1e-9 for i in range(rows)]
    pd.DataFrame(cols).to_parquet(path, index=False)
    return path


def ident(p, end="2026-08-24"):
    return cd.statcast_artifact_identity(p, expected_end=end)


def rec(p, end="2026-08-24"):
    return cd.statcast_source_record(ident(p, end), year=2024, through=end,
                                     cache_mode=cd.CACHE_MODE_FROZEN)


def main():
    box = tempfile.mkdtemp(prefix="fc-srcid-")
    try:
        import pandas  # noqa: F401
    except ImportError:
        print("pandas unavailable; cannot run source-identity tests")
        return 1
    try:
        name = "statcast_2024_through_2026-08-24.parquet"

        # ── A. same cache across resume ──────────────────────────────────
        print("== A. SAME cache across resume -> SUCCESS ==")
        a_dir = os.path.join(box, "durable_cache"); os.makedirs(a_dir)
        A = make_parquet(os.path.join(a_dir, name), 500)
        idx = {"source_lineage": [rec(A)]}
        bound_sha = idx["source_lineage"][0]["content_sha256"]
        check("bound digest is a FULL sha256 (64 hex)", len(bound_sha), 64)
        r = cd.assert_source_identity(idx, [rec(A)])
        check("identical artifact verifies", r["verified"], True)
        check("it actually compared something", r["compared"], 1)

        # ── B. different cache, same coverage ────────────────────────────
        print("== B. DIFFERENT cache, same filename+coverage -> FAIL CLOSED ==")
        b_dir = os.path.join(box, "b"); os.makedirs(b_dir)
        B = make_parquet(os.path.join(b_dir, name), 504, salt=1.0)
        check("same filename", os.path.basename(A), os.path.basename(B))
        check("both usable by validate_statcast_cache",
              ident(A)["usable"] and ident(B)["usable"], True)
        try:
            cd.assert_source_identity(idx, [rec(B)]); bad("B accepted")
        except cd.SourceVintageMismatch:
            ok("substituted cache rejected before generation")

        # ── C. different cache, IDENTICAL row count ──────────────────────
        print("== C. DIFFERENT cache, SAME row count -> FAIL CLOSED ==")
        c_dir = os.path.join(box, "c"); os.makedirs(c_dir)
        C = make_parquet(os.path.join(c_dir, name), 500, salt=7.5)
        check("row counts identical", ident(A)["row_count"], ident(C)["row_count"])
        check("digests differ", ident(A)["content_sha256"] != ident(C)["content_sha256"], True)
        try:
            cd.assert_source_identity(idx, [rec(C)]); bad("C accepted")
        except cd.SourceVintageMismatch:
            ok("same row count is NOT identity -- rejected")

        # ── D. different cache, same schema + coverage ───────────────────
        print("== D. DIFFERENT cache, same schema+coverage -> FAIL CLOSED ==")
        check("schema fingerprints identical",
              ident(A)["schema_fingerprint"], ident(C)["schema_fingerprint"])
        check("coverage identical",
              (ident(A)["min_date"], ident(A)["max_date"]),
              (ident(C)["min_date"], ident(C)["max_date"]))
        try:
            cd.assert_source_identity(idx, [rec(C)]); bad("D accepted")
        except cd.SourceVintageMismatch:
            ok("same schema+coverage is NOT identity -- rejected")

        # ── E. lineage conflict ──────────────────────────────────────────
        print("== E. LINEAGE CONFLICT -> FAIL CLOSED, prior intact ==")
        prior = [rec(A)]
        snapshot = [dict(x) for x in prior]
        try:
            cd.merge_lineage(prior, [rec(B)]); bad("conflicting lineage accepted")
        except cd.SourceLineageConflict:
            ok("conflicting lineage raises")
        check("prior lineage untouched", prior, snapshot)
        merged = cd.merge_lineage(prior, [rec(A)])
        check("identical record does not duplicate", len(merged), 1)
        other = dict(rec(A)); other["request_identity"] = "lineups:2024-04-01"
        check("a genuinely new source appends", len(cd.merge_lineage(prior, [other])), 2)

        # ── F. missing / ambiguous source configuration ──────────────────
        print("== F. SOURCE CONFIG MISSING or in-worktree -> FAIL CLOSED ==")
        repo = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                              cwd=os.path.dirname(os.path.abspath(__file__)),
                              capture_output=True, text=True).stdout.strip()
        try:
            cd.resolve_canonical_source_cache(explicit=os.path.join(repo, "backtest", ".cache"))
            bad("in-worktree cache accepted for canonical")
        except cd.SourceConfigError:
            ok("in-worktree cache refused for canonical")
        check("non-canonical development still works",
              cd.resolve_canonical_source_cache(canonical=False).endswith(".cache"), True)
        try:
            cd.statcast_source_record({"path": "/nope"}, year=2024, through="x",
                                      cache_mode=cd.CACHE_MODE_FROZEN)
            bad("unfingerprinted source accepted")
        except cd.SourceConfigError:
            ok("unfingerprinted source refused")
        try:
            cd.assert_source_identity(idx, [])
            bad("bound-but-absent source accepted")
        except cd.SourceVintageMismatch:
            ok("bound source absent -> fail closed")

        # ── G. fresh canonical run is not blocked ────────────────────────
        print("== G. FRESH canonical run still starts ==")
        r = cd.assert_source_identity({}, [rec(A)])
        check("no binding yet -> permitted", r["verified"], True)
        check("and it says so honestly", "first push" in r["reason"], True)
        d = cd.build_durable_index({"run_id": "r", "code_git_sha": "s"}, {},
                                   lineage=[rec(A)])
        check("first push binds the digest",
              d["source_lineage"][0]["content_sha256"], bound_sha)
        check("fingerprint recorded", bool(d["source_lineage_fingerprint"]), True)

        # ── H. end-to-end container reclamation ──────────────────────────
        print("== H. END-TO-END: reclamation, restore, resume; then substitution ==")
        # Path 1: the durable cache lives OUTSIDE the worktree and survives.
        wt = os.path.join(box, "worktree", "backtest", ".cache")
        os.makedirs(wt)
        shutil.copy(A, os.path.join(wt, name))
        check("worktree copy exists pre-reclamation",
              os.path.exists(os.path.join(wt, name)), True)
        shutil.rmtree(os.path.join(box, "worktree"))          # container reclaimed
        check("worktree destroyed", os.path.exists(wt), False)
        check("durable cache SURVIVED (outside the worktree)", os.path.exists(A), True)
        r = cd.assert_source_identity(idx, [rec(A)])
        check("resume on the restored artifact VERIFIES", r["verified"], True)

        # Path 2: same reclamation, but only a substitute survives.
        try:
            cd.assert_source_identity(idx, [rec(B)])
            bad("resume on a substituted artifact accepted")
        except cd.SourceVintageMismatch as exc:
            ok("resume on a substituted artifact FAILS CLOSED")
            check("error names both digests",
                  bound_sha[:16] in str(exc) and ident(B)["content_sha256"][:16] in str(exc), True)
            check("error states no rows were generated",
                  "No rows have been generated" in str(exc), True)

        print()
        print(f"passed: {PASS}   failed: {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

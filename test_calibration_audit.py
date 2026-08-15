#!/usr/bin/env python3
"""test_calibration_audit.py — coverage for backtest/calibration_audit.py,
Phase 3 item 4: probability buckets (50-55/55-60/.../75+), tracked
predicted vs actual, Brier, log loss, per prop family, with real
sample-size gating so a thin bucket is never confused with a real
calibration problem.

    /tmp/mlbvenv/bin/python3 test_calibration_audit.py
"""
import sys
import io
import contextlib

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
sys.path.insert(0, "backtest")

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        tag = "PASS" if cond else "FAIL"
        line = "  [%s] %s" % (tag, msg)
        if detail and (VERBOSE or not cond):
            line += "\n         " + detail
        print(line)


def head(t):
    if VERBOSE:
        print()
    print("-- %s" % t)


import calibration_audit as ca
import eval_lib as el

head("1. _print_table() flags a real (reportable-n, |gap|>=8pt) miscalibration, and does "
     "NOT flag a thin bucket even with a huge gap")

# 30 picks at predicted 0.70 that actually hit 40% of the time -- a real,
# large, reportable-n gap.
big_gap = [(0.70, 1.0 if i < 12 else 0.0) for i in range(30)]
# 2 picks at predicted 0.90 that both miss -- an enormous gap but n=2, thin.
thin_gap = [(0.90, 0.0), (0.90, 0.0)]

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ca._print_table(big_gap + thin_gap)
out = buf.getvalue()
check("REAL GAP" in out, "the large, reportable-n gap is flagged as a REAL GAP", out)
check("thin -- treat as noise" in out, "the thin bucket is labelled thin, not flagged "
      "as a real problem despite its huge raw gap", out)

head("2. main() runs end to end against a real (small, hermetic) picks fixture without "
     "crashing, and correctly separates current-architecture from legacy picks in its "
     "summary line")

import tempfile, os, json, shutil
TMPDIR = tempfile.mkdtemp(prefix="gridiron_test_calibration_audit_")
old_results_dir = el.RESULTS_DIR
el.RESULTS_DIR = TMPDIR


def mk_pick(pid, prob, grade, status=None, stat="hits"):
    return {"player_id": pid, "hit_probability": prob, "grade": grade,
           "recommendation_status": status, "projection": {"stat": stat, "needs": 1}}


grades_payload = {"date": "2026-08-16", "picks": [
    mk_pick(1, 0.65, "hit", status="top_pick"),
    mk_pick(2, 0.60, "miss", status=None),  # legacy
]}
with open(os.path.join(TMPDIR, "grades_2026-08-16.json"), "w") as f:
    json.dump(grades_payload, f)

buf2 = io.StringIO()
try:
    with contextlib.redirect_stdout(buf2):
        rc = ca.main()
finally:
    el.RESULTS_DIR = old_results_dir
    shutil.rmtree(TMPDIR, ignore_errors=True)

out2 = buf2.getvalue()
check(rc == 0, "main() returns 0 (success) on a real small fixture", f"got {rc}")
check("1 of these carry a current-architecture" in out2,
      "main() correctly counts exactly 1 current-architecture pick out of 2 total",
      out2.splitlines()[1] if len(out2.splitlines()) > 1 else out2)

n_pass = sum(1 for ok, _, _ in _results if ok)
n_total = len(_results)
print("\n" + "=" * 78)
print(f"RESULT: {n_pass}/{n_total} checks passed")
if n_pass < n_total:
    print()
    for ok, msg, detail in _results:
        if not ok:
            print(f"  FAILED: {msg}")
            if detail:
                print(f"          {detail}")
print("=" * 78)
sys.exit(0 if n_pass == n_total else 1)

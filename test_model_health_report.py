#!/usr/bin/env python3
"""test_model_health_report.py — coverage for model_health_report.py,
Phase 3 item 11: the automated early-warning report. Focuses on the two
places a real bug would be easy to miss: picking the right picks file
(never a timestamped archive copy) and correctly degrading when a picks
file predates the recommendation-layer rebuild.

    /tmp/mlbvenv/bin/python3 test_model_health_report.py
"""
import sys
import os
import json
import shutil
import tempfile
import io
import contextlib

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

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


import model_health_report as mhr
import eval_lib as el

TMPDIR = tempfile.mkdtemp(prefix="gridiron_test_model_health_")
mhr.OUTPUT_DIR = os.path.join(TMPDIR, "output")
mhr.RESULTS_DIR = os.path.join(TMPDIR, "results")
el.RESULTS_DIR = mhr.RESULTS_DIR
os.makedirs(mhr.OUTPUT_DIR, exist_ok=True)
os.makedirs(mhr.RESULTS_DIR, exist_ok=True)

head("1. _latest_picks_file() ignores timestamped archive copies "
     "(picks_DATE_TIMESTAMP.json), only real picks_DATE.json files")

open(os.path.join(mhr.OUTPUT_DIR, "picks_2026-08-14.json"), "w").write("{}")
open(os.path.join(mhr.OUTPUT_DIR, "picks_2026-08-15.json"), "w").write("{}")
open(os.path.join(mhr.OUTPUT_DIR, "picks_2026-08-15_2026-08-15T154711.json"), "w").write("{}")
found = mhr._latest_picks_file()
check(found == os.path.join(mhr.OUTPUT_DIR, "picks_2026-08-15.json"),
      "picks the real, most recent dated file, never the longer archive-copy filename",
      f"got {found}")

head("2. section_today() degrades gracefully on a legacy-shape file (no "
     "recommendation_status anywhere) instead of crashing")

legacy_payload = {"picks": [{"name": "Old Pick", "hit_probability": 0.6}]}
legacy_path = os.path.join(mhr.OUTPUT_DIR, "picks_legacy.json")
with open(legacy_path, "w") as f:
    json.dump(legacy_payload, f)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    mhr.section_today(legacy_path)
out = buf.getvalue()
check("legacy shape" in out, "a file with no recommendation_status is correctly identified "
      "as legacy shape, not crashed on or silently misreported", out)

head("3. section_today() correctly flags a large model/market disagreement using "
     "eval_lib.market_probability, and correctly counts missing-CI-but-should-have-one "
     "picks")

new_payload = {"picks": [
    {"name": "Big Gap", "prop": "Over 0.5 Hits", "hit_probability": 0.75,
     "market_odds": 400, "recommendation_status": "value",
     "probability_basis": "empirical", "prob_ci": None},
    {"name": "Fine", "prop": "Over 0.5 Hits", "hit_probability": 0.62,
     "market_odds": -140, "recommendation_status": "top_pick",
     "probability_basis": "empirical", "prob_ci": [0.55, 0.7]},
]}
new_path = os.path.join(mhr.OUTPUT_DIR, "picks_new.json")
with open(new_path, "w") as f:
    json.dump(new_payload, f)
buf2 = io.StringIO()
with contextlib.redirect_stdout(buf2):
    mhr.section_today(new_path)
out2 = buf2.getvalue()
check("Big Gap" in out2, "the large model/market disagreement is surfaced by name", out2)
check("1 empirical/blended picks with NO ci" in out2,
      "exactly 1 pick is flagged for a missing CI it should have (empirical basis, "
      "prob_ci=None) -- the other pick has a real CI and isn't flagged", out2)

shutil.rmtree(TMPDIR, ignore_errors=True)

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

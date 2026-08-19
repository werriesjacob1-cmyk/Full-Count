#!/usr/bin/env python3
"""test_recommendation_funnel.py — coverage for recommendation_funnel.py.

The single invariant that matters most here: gate_trace()/blocking_gate()
independently re-derive the same booleans classify_recommendation() computes
internally, and the two are NOT structurally coupled (recommendation.py is
deliberately left untouched -- see recommendation_funnel.py's own docstring
for why). Section 1 below is a direct consistency sweep across a wide battery
of candidates: for every one, "all Top Pick gates pass" must be true if and
only if classify_recommendation() actually returns "top_pick". If the two
ever drift, this is the test that catches it.

    /tmp/mlbvenv/bin/python3 test_recommendation_funnel.py
"""
import sys
from datetime import datetime, timezone

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


import recommendation as rec
import recommendation_funnel as rf

NOW = datetime.now(timezone.utc)
BOARD_NOW = NOW.isoformat()


def _fresh():
    return rec.freshness_check(now=NOW, odds_fetched_at=BOARD_NOW, board_generated_at=BOARD_NOW)


def cand(prob, odds, ci=None, reliability="A", lineup_assumed=False, lift=0.10):
    return {"hit_probability": prob, "market_odds": odds, "prob_ci": ci,
            "reliability": reliability, "lineup_assumed": lineup_assumed, "lift": lift}


head("1. gate_trace()/blocking_gate() are CONSISTENT with the real "
     "classify_recommendation() across a wide battery of candidates -- 'all Top Pick "
     "gates pass' must hold if and only if classify_recommendation() actually returns "
     "'top_pick'. This is what catches the two silently drifting apart.")

fresh, fresh_reasons = _fresh()

battery = [
    cand(0.65, -140, ci=[0.62, 0.72]),                                  # clean top pick
    cand(0.65, -140, ci=[0.62, 0.72], lineup_assumed=True),             # blocked: lineup
    cand(0.65, -140, ci=[0.62, 0.72], reliability="D"),                 # blocked: evidence
    cand(0.65, -140, ci=[0.62, 0.72], reliability="C"),                 # blocked: evidence
    cand(0.20, -140, ci=[0.15, 0.25]),                                  # blocked: prob floor
    cand(0.022, 8000),                                                  # real audit example
    cand(0.65, None),                                                   # blocked: no odds
    cand(None, -140),                                                   # blocked: no prob
    cand(0.60, -140, ci=[0.55, 0.65]),                                  # right at the floor
    cand(0.75, 2000, ci=[0.70, 0.80]),                                  # bad price, high prob
    cand(0.65, -140, ci=None),                                         # no CI -> no robust test
    cand(0.65, -140, ci=[0.62, 0.72], lift=None),                       # top pick, lift irrelevant
]

n_top_pick_agree = 0
for i, c in enumerate(battery):
    real = rec.classify_recommendation(c, now=NOW, data_fresh=fresh, fresh_reasons=fresh_reasons)
    traced = rf.classify_with_trace(c, now=NOW, data_fresh=fresh, fresh_reasons=fresh_reasons)
    all_gates_pass = traced["blocking_gate"] is None
    is_top_pick = real["status"] == "top_pick"
    check(all_gates_pass == is_top_pick,
         f"battery[{i}]: all-gates-pass ({all_gates_pass}) matches real status=="
         f"top_pick ({is_top_pick})",
         f"real status={real['status']!r}, blocking_gate={traced['blocking_gate']!r}, "
         f"gates={traced['gates']}")
    if is_top_pick:
        n_top_pick_agree += 1

check(n_top_pick_agree >= 1, "at least one battery case actually reaches top_pick, so "
     "the consistency check above is not vacuously true on the positive side")

head("2. gate_trace(): each individual gate reads the field it claims to, independent "
     "of the others -- a targeted probe per gate")

check(rf.gate_trace(cand(None, -140))["has_prob"] is False, "no probability -> has_prob False")
check(rf.gate_trace(cand(0.65, -140))["has_prob"] is True, "a real probability -> has_prob True")

check(rf.gate_trace(cand(0.59, -140))["meets_prob_floor"] is False,
     "just under the floor -> meets_prob_floor False")
check(rf.gate_trace(cand(0.60, -140))["meets_prob_floor"] is True,
     "exactly at the floor -> meets_prob_floor True (>=, not >)")

check(rf.gate_trace(cand(0.65, -140, reliability="C"))["evidence_ok"] is False,
     "reliability C is below the A/B floor -> evidence_ok False")
check(rf.gate_trace(cand(0.65, -140, reliability="B"))["evidence_ok"] is True,
     "reliability B clears the floor -> evidence_ok True")

check(rf.gate_trace(cand(0.65, -140, lineup_assumed=True))["lineup_ok"] is False,
     "an assumed lineup -> lineup_ok False")
check(rf.gate_trace(cand(0.65, -140, lineup_assumed=False))["lineup_ok"] is True,
     "a confirmed lineup -> lineup_ok True")
check(rf.gate_trace({"hit_probability": 0.65, "market_odds": -140})["lineup_ok"] is True,
     "lineup_assumed entirely absent (e.g. a game-level prop) -> treated as confirmed, "
     "matching classify_recommendation's own bool(None)==False convention")

check(rf.gate_trace(cand(0.65, None))["has_odds"] is False, "no market price -> has_odds False")
check(rf.gate_trace(cand(0.65, -140))["has_odds"] is True, "a real market price -> has_odds True")

no_ci_gates = rf.gate_trace(cand(0.65, -140, ci=None))
check(no_ci_gates["clears_value"] is False, "no prob_ci at all -> clears_value False, "
     "matching require_robust=True's own absent-interval-fails treatment",
     f"got {no_ci_gates}")

head("3. blocking_gate(): reports the FIRST gate in GATE_ORDER that fails, not just "
     "any failing gate -- so a candidate failing multiple gates is attributed to exactly "
     "one, and every candidate's counts sum to n_total")

multi_fail = rf.gate_trace(cand(0.20, -140, reliability="D", lineup_assumed=True))
check(rf.blocking_gate(multi_fail) == "meets_prob_floor",
     "a candidate failing prob floor AND evidence AND lineup is attributed to the FIRST "
     "one in GATE_ORDER (prob floor), not evidence or lineup",
     f"got {rf.blocking_gate(multi_fail)!r}")

all_pass = rf.gate_trace(cand(0.65, -140, ci=[0.62, 0.72]))
check(rf.blocking_gate(all_pass) is None, "every gate passing -> blocking_gate is None")

head("4. funnel_report(): tallies status distribution, sequential retention, and "
     "blocking-gate attribution correctly across a small known batch")

batch = [
    cand(0.65, -140, ci=[0.62, 0.72]),                       # top_pick
    cand(0.65, -140, ci=[0.62, 0.72], lineup_assumed=True),  # blocked at lineup_ok
    cand(0.20, -140, ci=[0.15, 0.25]),                       # blocked at meets_prob_floor
    cand(0.65, None),                                        # blocked at has_odds
]
report = rf.funnel_report(batch, now=NOW, odds_fetched_at=BOARD_NOW, board_generated_at=BOARD_NOW)

check(report["n_total"] == 4, "counts every candidate in the batch")
check(report["status_counts"]["top_pick"] == 1, "exactly one real top_pick in this batch",
     f"got {report['status_counts']}")
check(sum(report["status_counts"].values()) == 4, "status counts sum to n_total")
check(sum(report["blocking_counts"].values()) == 3, "blocking_counts sum to the 3 "
     "NON-top-pick candidates (the top_pick itself has no blocking gate)")
check(report["blocking_counts"]["lineup_ok"] == 1, "the lineup_assumed candidate is "
     "attributed to lineup_ok", f"got {report['blocking_counts']}")
check(report["blocking_counts"]["meets_prob_floor"] == 1, "the low-prob candidate is "
     "attributed to meets_prob_floor")
check(report["blocking_counts"]["has_odds"] == 1, "the no-odds candidate is attributed "
     "to has_odds")
check(report["funnel_retained"]["has_prob"] == 4, "all 4 candidates have a real probability")
check(report["funnel_retained"]["meets_prob_floor"] == 3, "3 of 4 clear the prob floor "
     "(the 0.20-probability candidate drops here)")
check(report["funnel_retained"]["lineup_ok"] == 2, "2 of those 3 have a confirmed lineup "
     "(the lineup_assumed candidate already dropped at this stage, not counted further)")
check(report["funnel_retained"]["has_odds"] == 1, "only 1 of those 2 has real odds -- "
     "retention is SEQUENTIAL/cumulative, so the no-odds candidate is the only one still "
     "in the running by this stage (the lineup_assumed candidate individually HAS odds "
     "too, but already dropped out of the funnel one stage earlier)")

head("5. merge_reports(): sums several days' reports into one real aggregate, larger "
     "than any single day's sample")

report_a = rf.funnel_report(batch[:2], now=NOW, odds_fetched_at=BOARD_NOW,
                            board_generated_at=BOARD_NOW)
report_b = rf.funnel_report(batch[2:], now=NOW, odds_fetched_at=BOARD_NOW,
                            board_generated_at=BOARD_NOW)
merged = rf.merge_reports([report_a, report_b])
check(merged["n_total"] == report["n_total"], "the merged 2-report total matches the "
     "single 4-candidate report computed directly")
check(merged["status_counts"] == report["status_counts"], "merged status counts match")
check(merged["n_days"] == 2, "records how many reports were merged")

head("6. report_from_picks_file(): reads a REAL output/picks_{date}.json file end to "
     "end -- the exact shape generate_picks.py's write_json() actually produces")

import json
import os
import tempfile

fixture_doc = {
    "date": "2026-08-19",
    "generated": BOARD_NOW,
    "recommendation_metadata": {
        "board_generated_at": BOARD_NOW,
        "odds_fetched_at": BOARD_NOW,
    },
    "picks": [
        {"hit_probability": 0.65, "market_odds": -140, "prob_ci": [0.62, 0.72],
         "reliability": "A", "lineup_assumed": False, "lift": 0.1},
        {"hit_probability": 0.20, "market_odds": -140, "prob_ci": [0.15, 0.25],
         "reliability": "A", "lineup_assumed": False, "lift": 0.05},
    ],
}
tmp_dir = tempfile.mkdtemp(prefix="gridiron_test_rec_funnel_")
fixture_path = os.path.join(tmp_dir, "picks_2026-08-19.json")
with open(fixture_path, "w", encoding="utf-8") as f:
    json.dump(fixture_doc, f)

file_report = rf.report_from_picks_file(fixture_path)
check(file_report["n_total"] == 2, "reads both picks from the fixture file",
     f"got n_total={file_report['n_total']}")
check(file_report["status_counts"]["top_pick"] == 1, "the clean 65% favorite is a real "
     "top_pick when read back through the file path")

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

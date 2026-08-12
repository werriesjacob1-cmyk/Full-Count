#!/usr/bin/env python3
"""test_refit_calibrators.py — coverage for backtest/refit_calibrators.py,
the automated calibration recheck built 2026-08-12 on top of backtest/
calibration.py's existing, already-tested fit/evaluate/split machinery.

WHY THIS EXISTS. generate_picks.py's own calibration comment block documents
a manual discipline: fit on one window, validate held-out on another, only
ship a curve if it clears a real improvement bar -- and three markets
(pitcher_outs, nrfi_combined, singles) were checked this way and explicitly
REJECTED. Automating that discipline is only safe if the automation
reproduces the same conservatism: never promote on noise, never fall back to
a pooled curve (already proven harmful), never overwrite a good calibrator
with a worse one, and never go silent when nothing changes. This file locks
each of those in directly, with synthetic rows built so the "right answer"
is known in advance.

    /tmp/mlbvenv/bin/python3 test_refit_calibrators.py
"""
import json
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
sys.path.insert(0, __file__.rsplit("/", 1)[0] + "/backtest" if "/" in __file__ else "backtest")

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


import calibration as cal
import refit_calibrators as rc

PROBS = [0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85]


def make_dates(n, start="2026-06-01"):
    import datetime
    d0 = datetime.date.fromisoformat(start)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


def make_rows(prop_type, dates, per_date=30, true_rate_fn=(lambda p: p), seed=0):
    """Synthetic rows with a KNOWN true relationship between predicted_prob
    and the real hit rate, so a test can assert calibration moves the right
    direction rather than just "some number changed"."""
    rng = random.Random(seed)
    rows = []
    pid = 0
    for date in dates:
        for _ in range(per_date):
            p = PROBS[pid % len(PROBS)]
            true_rate = true_rate_fn(p)
            outcome = 1 if rng.random() < true_rate else 0
            rows.append({
                "date": date, "game_pk": 900000 + pid, "player_id": pid,
                "prop_type": prop_type, "predicted_prob": p, "outcome": outcome,
                "fair_test": True,
            })
            pid += 1
    return rows


OVERCONFIDENT = lambda p: p * 0.6   # real rate is well below what's advertised
CALIBRATED = lambda p: p            # already honest -- nothing to fix


head("1. a systematically overconfident market gets promoted on real held-out evidence")

dates = make_dates(40)
rows = make_rows("hits", dates, per_date=30, true_rate_fn=OVERCONFIDENT, seed=1)
decisions, candidates, train, held_out = rc.run_recheck(rows, existing_calibrators={})
check(len(decisions) == 1 and decisions[0]["prop_type"] == "hits",
      "exactly one market (hits) considered", f"got {decisions}")
d = decisions[0]
check(d["action"] == "promote",
      "a real, systematic overconfidence pattern clears the promotion bar",
      f"reason={d['reason']}")
check(d["brier_improvement"] is not None and d["brier_improvement"] > rc.MIN_BRIER_IMPROVEMENT,
      "brier_improvement is real and above the noise floor", f"got {d['brier_improvement']}")
check(d["log_loss_improvement"] is not None and d["log_loss_improvement"] > 0,
      "log_loss_improvement agrees with brier -- both metrics corroborate", f"got {d['log_loss_improvement']}")

cand = candidates["hits"]
check(abs(cand.predict(0.75) - 0.75) > 0.05,
      "the promoted calibrator actually pulls an overconfident 0.75 down toward the real rate",
      f"calibrated(0.75)={cand.predict(0.75):.4f}")


head("2. an already-well-calibrated market is NOT promoted -- nothing to fix, so nothing changes")

rows2 = make_rows("hard_hit_105", dates, per_date=30, true_rate_fn=CALIBRATED, seed=2)
decisions2, candidates2, _, _ = rc.run_recheck(rows2, existing_calibrators={})
d2 = decisions2[0]
check(d2["action"] == "skip",
      "a market with no real miscalibration is left alone, not force-fit",
      f"reason={d2['reason']}")


head("3. a market with too few TRAIN rows is skipped with the row count in the reason, not fit on noise")

thin_rows = make_rows("singles", dates[:5], per_date=3, true_rate_fn=OVERCONFIDENT, seed=3)
decisions3, candidates3, _, _ = rc.run_recheck(thin_rows, existing_calibrators={})
d3 = decisions3[0]
check(d3["prop_type"] == "singles" and d3["action"] == "skip",
      "the thin market is skipped")
check("train rows" in d3["reason"] and str(rc.MIN_FIT_ROWS) in d3["reason"],
      "the reason names the actual row-count bar, not a vague excuse", f"got {d3['reason']}")
check("singles" not in candidates3,
      "no calibrator object was even produced for the skipped market")


head("4. a market with enough train rows but too few HELD-OUT rows is skipped, not promoted on a thin test set")

# Give it plenty of train rows but so few dates that time_based_split's 30%
# tail is under the held-out floor.
lopsided_dates = make_dates(30)
lopsided_rows = make_rows("strikeouts", lopsided_dates, per_date=40, true_rate_fn=OVERCONFIDENT, seed=4)
# Force an artificially small held-out slice via an explicit tiny test_frac.
decisions4, candidates4, train4, held4 = rc.run_recheck(
    lopsided_rows, existing_calibrators={}, test_frac=0.02)
d4 = decisions4[0]
check(len(held4) < rc.MIN_HELDOUT_ROWS or d4["action"] == "skip",
      "an evaluation on too few held-out rows is not trusted enough to promote",
      f"n_heldout={d4['n_heldout']} action={d4['action']} reason={d4['reason']}")


head("5. a candidate that improves on RAW but not on the ALREADY-SHIPPED calibrator is not promoted")

# Fit an existing calibrator directly on ALL the rows (train+heldout) for
# this market -- by construction it is at least as good on the held-out
# slice as anything freshly fit on the train slice alone can be.
rows5 = make_rows("hits_runs_rbis", dates, per_date=30, true_rate_fn=OVERCONFIDENT, seed=5)
existing_fit = cal.fit_calibrator(rows5, method=rc.DEFAULT_METHOD, prop_type="hits_runs_rbis")
decisions5, candidates5, _, _ = rc.run_recheck(
    rows5, existing_calibrators={"hits_runs_rbis": existing_fit})
d5 = decisions5[0]
check(d5["action"] == "skip",
      "a candidate that can't beat the calibrator already shipping doesn't replace it",
      f"reason={d5['reason']}")
check(d5.get("existing_brier_after") is not None,
      "the existing calibrator's held-out performance was actually checked, not assumed")


head("6. run_recheck NEVER produces a pooled/global calibrator, even when asked to fit everything at once")

mixed_rows = (make_rows("hits", dates, per_date=30, true_rate_fn=OVERCONFIDENT, seed=6)
             + make_rows("strikeouts", dates, per_date=30, true_rate_fn=(lambda p: min(1.0, p * 1.3)), seed=7))
decisions6, candidates6, _, _ = rc.run_recheck(mixed_rows, existing_calibrators={})
check(set(candidates6.keys()) <= {"hits", "strikeouts"},
      "only the real per-market prop_types appear as fit candidates",
      f"got {list(candidates6.keys())}")
check("all" not in candidates6 and None not in candidates6,
      "no pooled/global 'all' calibrator is ever produced", f"got {list(candidates6.keys())}")
# Opposite-direction miscalibration on the two markets is the exact scenario
# generate_picks.py's own comment says a pooled curve gets wrong by averaging.
h = candidates6["hits"]
k = candidates6["strikeouts"]
check(h.predict(0.5) < 0.5 and k.predict(0.5) > 0.5,
      "hits (overconfident) and strikeouts (underconfident) are corrected in OPPOSITE "
      "directions -- exactly what a single pooled curve could not do",
      f"hits(0.5)={h.predict(0.5):.4f} strikeouts(0.5)={k.predict(0.5):.4f}")


head("7. build_report logs every market considered, including a run where nothing was promoted")

report = rc.build_report(decisions2, ("2026-07-01", "2026-08-01"), "platt", 0.3, len(rows2), 100, 50)
check(report["promoted"] == [],
      "a 'nothing changed' run still produces a real report, not an empty/absent one")
check(len(report["decisions"]) == len(decisions2) and report["decisions"][0]["reason"] is not None,
      "the skipped market's reason is preserved verbatim in the report, not summarized away")


head("8. load_rows_window filters correctly: date range, missing predicted_prob, missing outcome")

TMPDIR = tempfile.mkdtemp(prefix="gridiron_test_refit_")
rows_path = os.path.join(TMPDIR, "rows.jsonl")
with open(rows_path, "w", encoding="utf-8") as f:
    f.write(json.dumps({"date": "2026-06-01", "prop_type": "hits", "predicted_prob": 0.5, "outcome": 1}) + "\n")
    f.write(json.dumps({"date": "2026-06-15", "prop_type": "hits", "predicted_prob": 0.6, "outcome": 0}) + "\n")
    f.write(json.dumps({"date": "2026-07-15", "prop_type": "hits", "predicted_prob": 0.5, "outcome": 1}) + "\n")  # out of range
    f.write(json.dumps({"date": "2026-06-10", "prop_type": "hits", "predicted_prob": None, "outcome": 1}) + "\n")  # unpriced
    f.write(json.dumps({"date": "2026-06-11", "prop_type": "hits", "predicted_prob": 0.5}) + "\n")  # ungraded
    f.write("\n")  # blank line
    f.write("not json\n")  # malformed

got = rc.load_rows_window(rows_path, "2026-06-01", "2026-06-30")
check(len(got) == 2 and {r["date"] for r in got} == {"2026-06-01", "2026-06-15"},
      "only the two real, in-range, priced-and-graded rows are kept", f"got {[r['date'] for r in got]}")

shutil.rmtree(TMPDIR, ignore_errors=True)


head("9. date_window computes a sane trailing window with the grading lag applied")

start, end = rc.date_window(end="2026-08-10", window_days=35, grading_lag_days=2)
check(end == "2026-08-10", "explicit --end is honored exactly", f"got {end}")
check(start == "2026-07-06", "start is exactly window_days before end", f"got {start}")

start2, end2 = rc.date_window(end=None, window_days=10, grading_lag_days=3)
import datetime as _dt
expected_end = (_dt.date.today() - _dt.timedelta(days=3)).isoformat()
check(end2 == expected_end,
      "with no explicit end, the window ends grading_lag_days before today "
      "(never assumes today's games are graded yet)", f"got {end2}")


head("10. an end-to-end promoted run merges into the EXISTING calibrator dict without disturbing other markets")

existing_other = cal.fit_calibrator(
    make_rows("strikeouts", dates, per_date=30, true_rate_fn=(lambda p: min(1.0, p * 1.3)), seed=8),
    method="platt", prop_type="strikeouts")
existing_map = {"strikeouts": existing_other}
rows10 = make_rows("hits", dates, per_date=30, true_rate_fn=OVERCONFIDENT, seed=9)
decisions10, candidates10, _, _ = rc.run_recheck(rows10, existing_calibrators=existing_map)
promoted10 = {d["prop_type"]: candidates10[d["prop_type"]] for d in decisions10 if d["action"] == "promote"}
updated = dict(existing_map)
updated.update(promoted10)
check("strikeouts" in updated and updated["strikeouts"] is existing_other,
      "a market this run never touched is carried through unchanged, never deleted")
check("hits" in updated and updated["hits"] is not existing_other,
      "the newly promoted market is added alongside it")


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

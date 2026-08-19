#!/usr/bin/env python3
"""test_accuracy_lab.py — coverage for accuracy_lab.py, Stage 6: a locked
historical holdout partition of backtest/rows.jsonl, and a Champion-vs-
Challenger comparison guaranteed to run on IDENTICAL conditions (the same
locked rows, every time) rather than two overlapping-but-different
samples compared as if they were the same one.

ISOLATION: accuracy_lab.LAB_DIR/MANIFEST_PATH/RESULTS_DIR are repointed to
a temp directory for the whole file, matching test_champion_challenger.py's
own established pattern for this exact class of module-level-constant trap.

    /tmp/mlbvenv/bin/python3 test_accuracy_lab.py
"""
import sys
import os
import json
import tempfile

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


import accuracy_lab as al

TMPDIR = tempfile.mkdtemp(prefix="gridiron_test_accuracy_lab_")
al.LAB_DIR = os.path.join(TMPDIR, "accuracy_lab")
al.MANIFEST_PATH = os.path.join(al.LAB_DIR, "holdout_manifest.json")
al.RESULTS_DIR = os.path.join(al.LAB_DIR, "results")


def write_rows(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def fixture_row(date, player_id, outcome, predicted_prob=0.6, prop_type="hits",
                calibrated_prob=None):
    row = {"date": date, "player_id": player_id, "prop_type": prop_type,
          "predicted_prob": predicted_prob, "outcome": outcome}
    if calibrated_prob is not None:
        row["calibrated_prob"] = calibrated_prob
    return row


# 10 distinct dates, 2 rows each -- a clean, deterministic fixture.
DATES = [f"2026-0{m}-{d:02d}" for m, d in
        [(1, 1), (1, 5), (1, 10), (1, 15), (1, 20), (1, 25), (2, 1), (2, 5), (2, 10), (2, 15)]]


def make_fixture_path():
    rows = []
    for i, d in enumerate(DATES):
        rows.append(fixture_row(d, player_id=100 + i, outcome=1 if i % 2 == 0 else 0))
        rows.append(fixture_row(d, player_id=200 + i, outcome=0 if i % 2 == 0 else 1))
    path = os.path.join(TMPDIR, "rows.jsonl")
    write_rows(path, rows)
    return path


head("1. lock_holdout(): first call locks a REAL chronological cutoff and writes the "
     "manifest; every later call returns the identical partition, never recomputing it")

rows_path = make_fixture_path()
train1, holdout1, cutoff1 = al.lock_holdout(rows_path, holdout_frac=0.2)
check(os.path.exists(al.MANIFEST_PATH), "the manifest file now exists on disk")
check(cutoff1 == DATES[8], "10 dates * 0.2 = 2 holdout dates -> cutoff is the 9th date "
     "(index 8), matching time_based_split's own chronological-tail convention",
     f"got cutoff={cutoff1!r}, expected {DATES[8]!r}")
check(all(r["date"] >= cutoff1 for r in holdout1), "every holdout row's date is >= cutoff",
     f"holdout dates={sorted({r['date'] for r in holdout1})}")
check(all(r["date"] < cutoff1 for r in train1), "every train row's date is < cutoff")
check(len(train1) + len(holdout1) == len(DATES) * 2, "no rows lost or duplicated across the split")

train2, holdout2, cutoff2 = al.lock_holdout(rows_path, holdout_frac=0.2)
check(cutoff2 == cutoff1, "a second call with the SAME holdout_frac returns the identical "
     "cutoff, not a freshly recomputed one")
check(len(holdout2) == len(holdout1), "the identical row count too")

head("2. lock_holdout(): a MISMATCHED holdout_frac on an already-locked partition raises, "
     "rather than silently honoring the new argument or silently keeping the old one")

raised = False
try:
    al.lock_holdout(rows_path, holdout_frac=0.5)
except ValueError as e:
    raised = True
    check("does not match" in str(e), "the error explains the mismatch honestly",
         f"got: {e}")
check(raised, "ValueError was actually raised")

head("3. champion_predict_fn(): prefers calibrated_prob when Stage 5 set one, falls back "
     "to raw predicted_prob otherwise -- never fabricates a third number")

check(al.champion_predict_fn({"predicted_prob": 0.4}) == 0.4,
     "no calibrated_prob at all -> falls back to raw predicted_prob")
check(al.champion_predict_fn({"predicted_prob": 0.4, "calibrated_prob": 0.55}) == 0.55,
     "calibrated_prob present -> that one wins, not the raw value")

head("4. evaluate_predictor_on_holdout(): scores ONLY the locked holdout rows (never "
     "train), skips a None prediction honestly, and appends (never overwrites) results")

result1 = al.evaluate_predictor_on_holdout("champion", al.champion_predict_fn, rows_path)
check(result1["n_holdout_rows"] == len(holdout1), "scored exactly the holdout row count",
     f"got {result1['n_holdout_rows']} vs holdout size {len(holdout1)}")
check(result1["n_scored"] == len(holdout1), "every holdout row had a real predicted_prob, "
     "so none were skipped for this fixture")
check(result1["brier"] is not None and result1["log_loss"] is not None,
     "real Brier/log-loss numbers were computed, not left null")
check(result1["cutoff_date"] == cutoff1, "the result records which cutoff it was scored "
     "against, so a later comparison can verify the two sides actually match")

def half_the_time_no_opinion(row):
    if row["player_id"] % 2 == 0:
        return None
    return 0.5

result_skip = al.evaluate_predictor_on_holdout("skipper", half_the_time_no_opinion, rows_path)
check(result_skip["n_skipped_no_opinion"] > 0, "rows this predictor declined to score "
     "(returned None) are counted as skipped, not silently coerced into a guess",
     f"got n_skipped_no_opinion={result_skip['n_skipped_no_opinion']}")
check(result_skip["n_scored"] + result_skip["n_skipped_no_opinion"] == result_skip["n_holdout_rows"],
     "scored + skipped accounts for every holdout row")

al.evaluate_predictor_on_holdout("champion", al.champion_predict_fn, rows_path)
all_champion_results = al._load_results("champion")
check(len(all_champion_results) == 2, "running the SAME label twice appends a second "
     "record rather than overwriting the first -- the full audit trail survives, "
     "directly enforcing 'do not tune repeatedly against a locked holdout' by making "
     "every attempt visible, not just the most favorable one",
     f"got {len(all_champion_results)} records")

head("5. compare(): two labels evaluated against the identical locked holdout produce a "
     "real delta; a label with no recorded evaluation is reported honestly, never guessed")

al.evaluate_predictor_on_holdout("always_half", lambda r: 0.5, rows_path)
cmp = al.compare("champion", "always_half")
check(cmp["brier_delta"] is not None, "a real numeric delta was computed",
     f"got {cmp}")
check(cmp["cutoff_date"] == cutoff1, "the comparison records the shared cutoff both "
     "sides were actually scored against")

cmp_missing = al.compare("champion", "nonexistent_label")
check(cmp_missing["brier_delta"] is None, "no fabricated delta when one side was never "
     "evaluated", f"got {cmp_missing}")
check(cmp_missing["b_evaluated"] is False, "honestly reports which side is missing")

head("6. compare(): two results scored against DIFFERENT cutoff dates (the lock was "
     "deleted and re-locked between them) are reported as not comparable, never silently "
     "compared across two different holdout partitions as if they were the same one")

fake_old_result = dict(result1)
fake_old_result["cutoff_date"] = "2020-01-01"
fake_old_result["label"] = "stale_lock"
os.makedirs(al.RESULTS_DIR, exist_ok=True)
with open(os.path.join(al.RESULTS_DIR, "stale_lock.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps(fake_old_result) + "\n")
cmp_stale = al.compare("champion", "stale_lock")
check(cmp_stale["brier_delta"] is None, "no delta computed across mismatched cutoffs")
check("NOT comparable" in cmp_stale["note"], "the reason is stated explicitly",
     f"got {cmp_stale.get('note')!r}")

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

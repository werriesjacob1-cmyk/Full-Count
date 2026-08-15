#!/usr/bin/env python3
"""test_phase3_versioning.py — coverage for Phase 3 item 3: "Verify that
every future saved recommendation contains enough information to
reproduce what produced it."

Direct instruction, verbatim, of what a saved recommendation must carry:
model version, recommendation/selection-policy version, calibration
version, feature version, git SHA, prediction timestamp, odds timestamp,
lineup status/timestamp, exact player/game/market/side/threshold,
displayed probability, recommendation status -- and "make sure grading
preserves this metadata."

This locks in two things end to end, using the real production functions:
  1. generate_picks.write_json() stamps every one of those fields onto
     EVERY row (not just a board-level wrapper), sourced from a single
     recommendation.build_metadata() call passed in by main() -- never a
     fresh, independently-drifting one per row.
  2. grade_results.grade_day() -- the exact function that turns a picks
     file into results/grades_*.json, the one every downstream analysis
     script in Phase 3 reads -- preserves every one of those fields
     through grading untouched, because grade_pick() spreads {**pick, ...}
     rather than rebuilding a narrower dict.

    /tmp/mlbvenv/bin/python3 test_phase3_versioning.py
"""
import sys
import os
import json
import shutil
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


import generate_picks as gp
import recommendation as rec
import grade_results as gr

TMPDIR = tempfile.mkdtemp(prefix="gridiron_test_phase3_versioning_")
gp.OUTPUT_DIR = TMPDIR
gp.PICKS_JSON_FILE = os.path.join(TMPDIR, "picks_test.json")
gr.OUTPUT_DIR = TMPDIR
gr.RESULTS_DIR = TMPDIR
# grade_results.HISTORY_FILE is bound to RESULTS_DIR's value at IMPORT time
# (module-level `HISTORY_FILE = os.path.join(RESULTS_DIR, "history.json")`),
# so reassigning gr.RESULTS_DIR above does NOT retroactively repoint it --
# it must be overridden explicitly, or grade_day() below silently reads AND
# WRITES the real production results/history.json. Confirmed the hard way:
# an earlier version of this test without this line corrupted the real
# history.json with a fake graded pick before being caught and reverted.
gr.HISTORY_FILE = os.path.join(TMPDIR, "history.json")


def candidate(name, player_id, prob=0.65, status="top_pick", lineup_assumed=False):
    return {
        "type": "batter", "name": name, "player_id": player_id, "team": "Team",
        "matchup": "A @ B", "game_pk": 900001, "side": None,
        "prop": "Over 0.5 Hits", "projection": {"stat": "hits", "value": 0.5, "needs": 1},
        "score": 70.0, "confidence": "High", "notable_signals": 1,
        "hit_probability": prob, "reliability": "A", "sample_n": 90,
        "prob_ci": [0.60, 0.72], "lift": 0.10, "lineup_assumed": lineup_assumed,
        "market_odds": -140, "market_implied": 0.58, "market_hold": 0.061,
        "market_edge": 0.07, "price_clears": True,
        "status": status, "status_reasons": ["a real, clean Top Pick"],
    }


head("1. write_json stamps every Phase 3 item-3 field onto EVERY row, from one "
     "recommendation.build_metadata() call passed in by the caller")

meta = rec.build_metadata(odds_fetched_at="2026-08-16T18:00:00+00:00",
                          board_generated_at="2026-08-16T18:00:00+00:00")
c1 = candidate("Player One", 111)
gp.write_json([c1], recommendation_metadata=meta)

with open(gp.PICKS_JSON_FILE) as f:
    payload = json.load(f)
row = payload["picks"][0]

REQUIRED_FIELDS = {
    "model_version": rec.MODEL_VERSION,
    "selection_policy_version": rec.SELECTION_POLICY_VERSION,
    "calibration_version": rec.CALIBRATION_VERSION,
    "feature_version": rec.FEATURE_VERSION,
}
for field, expected in REQUIRED_FIELDS.items():
    check(row.get(field) == expected, f"row carries the real {field} ({expected!r})",
          f"got {row.get(field)!r}")

check(row.get("odds_timestamp") == "2026-08-16T18:00:00+00:00",
      "row carries the real odds timestamp", f"got {row.get('odds_timestamp')!r}")
check(row.get("prediction_timestamp") is not None,
      "row carries a real prediction timestamp (not fabricated, not absent)")
check(row.get("lineup_checked_at") == "2026-08-16T18:00:00+00:00",
      "row carries a lineup status timestamp", f"got {row.get('lineup_checked_at')!r}")
check(row.get("lineup_assumed") is False,
      "row carries the real lineup_assumed status (the exact field classify_recommendation "
      "already reads internally, now also persisted for reproducibility)")
check("git_sha" in row, "row carries a git_sha key -- honestly None outside a checkout, "
      "never simply absent", f"got {row.get('git_sha')!r}")

head("2. exact player/game/market/side/threshold/displayed probability/recommendation "
     "status are all on the row -- the rest of item 3's required list")

check(row["name"] == "Player One" and row["player_id"] == 111, "exact player")
check(row["game_pk"] == 900001, "exact game")
check(row["projection"] == {"stat": "hits", "value": 0.5, "needs": 1},
      "exact market + threshold (stat/needs/value)")
check(row["side"] is None, "side field present (None here -- no side on a plain hits prop)")
check(row["hit_probability"] == 0.65, "the exact displayed probability")
check(row["recommendation_status"] == "top_pick", "the exact recommendation status")

head("3. market_hold (the real, exactly-measured two-sided hold) survives to disk -- "
     "was computed on the candidate and silently dropped at this boundary before Phase 3")

check(row.get("market_hold") == 0.061, "market_hold reaches the saved row",
      f"got {row.get('market_hold')!r}")

head("4. two DIFFERENT boards produce two DIFFERENT, internally-consistent version stamps "
     "-- this is per-RUN metadata, not a hardcoded constant copy-pasted into the row builder")

meta2 = rec.build_metadata(odds_fetched_at="2026-08-17T12:00:00+00:00",
                           board_generated_at="2026-08-17T12:00:00+00:00")
c2 = candidate("Player Two", 222)
gp.write_json([c2], recommendation_metadata=meta2)
with open(gp.PICKS_JSON_FILE) as f:
    payload2 = json.load(f)
row2 = payload2["picks"][0]
check(row2.get("odds_timestamp") == "2026-08-17T12:00:00+00:00",
      "the second board's row carries ITS OWN odds timestamp, not the first board's",
      f"got {row2.get('odds_timestamp')!r}")
check(payload2.get("recommendation_metadata", {}).get("odds_fetched_at")
      == "2026-08-17T12:00:00+00:00",
      "the board-level convenience copy matches the per-row stamp exactly -- one real "
      "metadata dict, reused, never two independently-computed ones")

head("5. grade_results.grade_day() PRESERVES every one of these fields through grading -- "
     "the metadata is only useful if it survives into results/grades_*.json, which is what "
     "every Phase 3 analysis script actually reads")

DATE = "2026-08-16"
c3 = candidate("Grade Me", 333, prob=0.65, status="top_pick")
gp.write_json([c3], recommendation_metadata=meta)
shutil.copy(gp.PICKS_JSON_FILE, gr.picks_path(DATE))

FINAL = {"codedGameState": "F", "detailedState": "Final"}
gr.fetch_game_statuses = lambda date: {900001: FINAL}
gr.get_box_line = lambda game_pk, player_id, is_pitcher: (
    ({"h": 2, "ab": 4, "bb": 0, "substitution": False}, None) if player_id == 333
    else (None, None))

gr.grade_day(DATE)
with open(gr.grades_path(DATE)) as f:
    grades = json.load(f)
graded_row = grades["picks"][0]

check(graded_row["grade"] == "hit", "sanity: the pick actually graded (2 hits clears 0.5)")
for field in ("model_version", "selection_policy_version", "calibration_version",
             "feature_version", "git_sha", "prediction_timestamp", "odds_timestamp",
             "lineup_checked_at", "lineup_assumed", "market_hold",
             "recommendation_status", "hit_probability"):
    check(field in graded_row and graded_row[field] == row.get(field) if field != "hit_probability"
          else graded_row[field] == 0.65,
          f"grading preserved {field} unchanged from the saved pick",
          f"got {graded_row.get(field)!r}")

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

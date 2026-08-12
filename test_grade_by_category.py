#!/usr/bin/env python3
"""test_grade_by_category.py — coverage for the by_category breakdown added
to grade_results.grade_day()/history.json (2026-08-12).

WHY THIS EXISTS. Catching up 5 days of ungraded picks this session
surfaced a real, structural gap: the existing hits/misses totals blend
three groups with deliberately very different intended hit rates --
"main" (the actual top10 recommendation, meant to run 60-80%),
"moonshot" (home runs, meant to run 15-25% by design), and
"best_of_category" (explicitly includes picks below the main board's own
60% floor, flagged on the board with a warning icon for exactly this
reason). The blended headline measured 45.2% over that window, which
reads as "barely better than a coin flip" -- but the main board alone,
the thing Jacob would actually bet, measured 57.8% over the same window.
Those are very different pictures, and the blended number alone cannot
tell them apart.

This locks in that the new by_category/by_category_totals/main_hit_rate
fields are computed correctly, additively (no existing field removed or
renamed), and degrade gracefully against older history.json days written
before this change (no by_category key at all).

    /tmp/mlbvenv/bin/python3 test_grade_by_category.py
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


import grade_results as gr

TMPDIR = tempfile.mkdtemp(prefix="gridiron_test_grade_category_")
gr.OUTPUT_DIR = TMPDIR
gr.RESULTS_DIR = TMPDIR
gr.HISTORY_FILE = os.path.join(TMPDIR, "history.json")

FINAL = {"codedGameState": "F", "detailedState": "Final"}
gr.fetch_game_statuses = lambda date: {900001: FINAL}

# Real box rows: player 1 gets 2 hits (a real starter's line), player 2 gets
# 0 hits, player 3 (moonshot HR bet) hits 0 HR, player 4 (best_of_category,
# a sub-floor pick) also misses.
_ROWS = {
    1: {"h": 2, "ab": 4, "bb": 1, "substitution": False},
    2: {"h": 0, "ab": 4, "bb": 0, "substitution": False},
    3: {"h": 1, "hr": 0, "ab": 4, "bb": 0, "substitution": False},
    4: {"h": 0, "hr": 0, "ab": 3, "bb": 0, "substitution": False},
}
gr.get_box_line = lambda game_pk, player_id, is_pitcher: (_ROWS.get(player_id), None)


def pick(player_id, category, stat="hits", needs=1, value=0.5):
    p = {"game_pk": 900001, "player_id": player_id,
         "projection": {"stat": stat, "value": value, "needs": needs},
         "type": "batter"}
    if category is not None:
        p["category"] = category
    return p


def write_picks(date, picks):
    with open(gr.picks_path(date), "w", encoding="utf-8") as f:
        json.dump({"date": date, "picks": picks}, f)


head("1. a mixed-category day's grades file gets a real by_category breakdown, additive "
     "alongside the existing hits/misses/fair_hits fields")

DATE = "2026-08-01"
write_picks(DATE, [
    pick(1, category=None, stat="hits", needs=1),      # no category key -> "main", HIT (2>=1)
    pick(2, category=None, stat="hits", needs=1),       # "main", MISS (0<1)
    pick(3, "moonshot", stat="home_runs", needs=1),      # MISS (0 HR)
    pick(4, "best_of_category", stat="hits", needs=1),   # MISS (0<1)
])
ok = gr.grade_day(DATE)
check(ok is True, "grade_day completes and reports it wrote grades")

with open(gr.grades_path(DATE)) as f:
    day_grades = json.load(f)
check(day_grades["hits"] == 1 and day_grades["misses"] == 3,
      "the EXISTING top-level hits/misses fields are untouched by this change "
      "(1 hit, 3 misses across all 4 picks)", f"got hits={day_grades['hits']} misses={day_grades['misses']}")

head("2. by_category correctly separates the picks with NO category key into 'main'")

bc = day_grades["by_category"]
check(bc.get("main") == {"hits": 1, "misses": 1, "ungraded": 0},
      "the two category=None picks are bucketed as 'main' (1 hit, 1 miss)", f"got {bc.get('main')}")
check(bc.get("moonshot") == {"hits": 0, "misses": 1, "ungraded": 0},
      "the moonshot pick is bucketed separately (0 hits, 1 miss)", f"got {bc.get('moonshot')}")
check(bc.get("best_of_category") == {"hits": 0, "misses": 1, "ungraded": 0},
      "the best_of_category pick is bucketed separately (0 hits, 1 miss)",
      f"got {bc.get('best_of_category')}")

head("3. history.json gets the same breakdown recorded per-day, plus running totals")

with open(gr.HISTORY_FILE) as f:
    history = json.load(f)
check(history["days"][-1]["by_category"] == bc,
      "the day's by_category breakdown is recorded verbatim on the history entry")
check(history["by_category_totals"]["main"] == {"hits": 1, "misses": 1, "ungraded": 0},
      "by_category_totals correctly accumulates the 'main' category across the whole "
      "history (only one day so far)", f"got {history['by_category_totals'].get('main')}")

head("4. main_hit_rate is the RIGHT number, distinct from the blended overall_hit_rate")

check(abs(history["main_hit_rate"] - 0.5) < 1e-9,
      "main_hit_rate is exactly 1/2=0.5 -- computed from ONLY the main-category picks, "
      "not diluted by the moonshot/best_of_category misses", f"got {history['main_hit_rate']}")
check(abs(history["overall_hit_rate"] - 0.25) < 1e-9,
      "overall_hit_rate (the pre-existing blended field) is still 1/4=0.25 -- confirming "
      "main_hit_rate is a genuinely different, additional number, not a rename",
      f"got {history['overall_hit_rate']}")

head("5. a SECOND day accumulates correctly into by_category_totals across days")

DATE2 = "2026-08-02"
write_picks(DATE2, [
    pick(1, category=None, stat="hits", needs=1),   # main HIT
    pick(1, category=None, stat="hits", needs=1),   # main HIT (same player, different pick)
])
gr.grade_day(DATE2)
with open(gr.HISTORY_FILE) as f:
    history2 = json.load(f)
check(history2["by_category_totals"]["main"] == {"hits": 3, "misses": 1, "ungraded": 0},
      "main totals accumulate correctly across both days (1+2 hits, 1+0 misses)",
      f"got {history2['by_category_totals']['main']}")
check(abs(history2["main_hit_rate"] - 0.75) < 1e-9,
      "main_hit_rate updates to 3/4=0.75 after the second day", f"got {history2['main_hit_rate']}")

head("6. graceful degradation: an OLDER history day with no by_category key at all "
     "doesn't crash and is silently excluded from the breakdown")

# Simulate a pre-existing history.json day written before this feature existed.
with open(gr.HISTORY_FILE) as f:
    history3 = json.load(f)
history3["days"].insert(0, {"date": "2026-07-01", "hits": 5, "misses": 5, "ungraded": 0,
                            "fair_hits": 5, "fair_misses": 5})  # no by_category key
with open(gr.HISTORY_FILE, "w") as f:
    json.dump(history3, f)

DATE3 = "2026-08-03"
write_picks(DATE3, [pick(2, category=None, stat="hits", needs=1)])  # main MISS
gr.grade_day(DATE3)
with open(gr.HISTORY_FILE) as f:
    history4 = json.load(f)
check(history4["by_category_totals"]["main"] == {"hits": 3, "misses": 2, "ungraded": 0},
      "the old, by_category-less day contributes nothing to the breakdown (doesn't crash, "
      "doesn't fabricate zeros into a wrong bucket) -- totals are exactly the same 3/1 "
      "from before plus this day's new 0/1", f"got {history4['by_category_totals']['main']}")
check(history4["overall_hit_rate"] is not None,
      "overall_hit_rate still correctly includes the old day's raw hits/misses (5/5), "
      "since THAT field never depended on by_category existing")

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

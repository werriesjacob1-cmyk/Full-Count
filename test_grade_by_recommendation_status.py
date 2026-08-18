#!/usr/bin/env python3
"""test_grade_by_recommendation_status.py — coverage for the
by_recommendation_status breakdown added to grade_results.grade_day()/
history.json as part of the 2026-08-15 recommendation-layer rebuild.

WHY THIS EXISTS. Direct instruction from the rebuild spec: "The public Top
Pick hit rate should measure the bets Full Count actually designated as
Top Picks" and "do not display blended 14-day record beside Top Pick
record as comparable... track Top Picks/Best Value/Longshots/Leans
independently." test_grade_by_category.py already locks in the
prop-family axis (main/moonshot/best_of_category); this is a genuinely
different axis -- recommendation_status (top_pick/lean/value/neutral),
set by recommendation.classify_recommendation() and persisted through
generate_picks.write_json()'s _row(). A pick can be category=None (i.e.
"main", it won by rank_for_board's ordering) while its own
recommendation_status is "lean", not "top_pick" -- exactly the case the
old blended reporting could never separate out.

This locks in that by_recommendation_status/by_recommendation_status_totals
retain the complete modelled-recommendation population, while the official
top_pick_hit_rate fields are reserved for the separate deployment-proven public
population. The modelled Top Pick rate remains available under an explicit
modeled_* name. Both populations degrade gracefully
against older history.json days written before recommendation_status
existed at all (no by_recommendation_status key, or picks with the field
simply absent -- bucketed "unclassified", never guessed into "top_pick").

    /tmp/mlbvenv/bin/python3 test_grade_by_recommendation_status.py
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

TMPDIR = tempfile.mkdtemp(prefix="gridiron_test_grade_recstatus_")
gr.OUTPUT_DIR = TMPDIR
gr.RESULTS_DIR = TMPDIR
gr.HISTORY_FILE = os.path.join(TMPDIR, "history.json")

FINAL = {"codedGameState": "F", "detailedState": "Final"}
gr.fetch_game_statuses = lambda date: {900001: FINAL}

# player 1: top_pick, HIT. player 2: top_pick, MISS. player 3: lean, MISS.
# player 4: value, MISS. player 5: no recommendation_status at all (a pick
# from before the rebuild shipped), HIT.
_ROWS = {
    1: {"h": 2, "ab": 4, "bb": 1, "substitution": False},
    2: {"h": 0, "ab": 4, "bb": 0, "substitution": False},
    3: {"h": 0, "ab": 4, "bb": 0, "substitution": False},
    4: {"h": 0, "ab": 4, "bb": 0, "substitution": False},
    5: {"h": 2, "ab": 4, "bb": 0, "substitution": False},
}
gr.get_box_line = lambda game_pk, player_id, is_pitcher: (_ROWS.get(player_id), None)


def pick(player_id, recommendation_status, stat="hits", needs=1, value=0.5):
    p = {"game_pk": 900001, "player_id": player_id,
         "projection": {"stat": stat, "value": value, "needs": needs},
         "type": "batter"}
    if recommendation_status is not None:
        p["recommendation_status"] = recommendation_status
    return p


def write_picks(date, picks):
    with open(gr.picks_path(date), "w", encoding="utf-8") as f:
        json.dump({"date": date, "picks": picks}, f)


head("1. a mixed-status day's grades file gets a real by_recommendation_status breakdown, "
     "additive alongside the existing by_category/hits/misses fields")

DATE = "2026-08-15"
write_picks(DATE, [
    pick(1, "top_pick", stat="hits", needs=1),   # HIT
    pick(2, "top_pick", stat="hits", needs=1),   # MISS
    pick(3, "lean", stat="hits", needs=1),        # MISS
    pick(4, "value", stat="hits", needs=1),       # MISS
    pick(5, None, stat="hits", needs=1),          # HIT, no recommendation_status at all
])
ok = gr.grade_day(DATE)
check(ok is True, "grade_day completes and reports it wrote grades")

with open(gr.grades_path(DATE)) as f:
    day_grades = json.load(f)
check(day_grades["hits"] == 2 and day_grades["misses"] == 3,
      "the EXISTING top-level hits/misses fields are untouched by this change",
      f"got hits={day_grades['hits']} misses={day_grades['misses']}")

head("2. by_recommendation_status correctly separates each status, and buckets a pick with "
     "NO recommendation_status field as 'unclassified', never guessed into 'top_pick'")

brs = day_grades["by_recommendation_status"]
check(brs.get("top_pick") == {"hits": 1, "misses": 1, "ungraded": 0},
      "the two top_pick picks are bucketed correctly (1 hit, 1 miss)", f"got {brs.get('top_pick')}")
check(brs.get("lean") == {"hits": 0, "misses": 1, "ungraded": 0},
      "the lean pick is bucketed separately", f"got {brs.get('lean')}")
check(brs.get("value") == {"hits": 0, "misses": 1, "ungraded": 0},
      "the value pick is bucketed separately", f"got {brs.get('value')}")
check(brs.get("unclassified") == {"hits": 1, "misses": 0, "ungraded": 0},
      "the pick with no recommendation_status at all lands in 'unclassified', not silently "
      "counted as a top_pick", f"got {brs.get('unclassified')}")

head("3. history.json preserves modelled-status totals without guessing public exposure")

with open(gr.HISTORY_FILE) as f:
    history = json.load(f)
check(history["days"][-1]["by_recommendation_status"] == brs,
      "the day's by_recommendation_status breakdown is recorded verbatim on the history entry")
check(history["by_recommendation_status_totals"]["top_pick"] == {"hits": 1, "misses": 1, "ungraded": 0},
      "by_recommendation_status_totals correctly accumulates 'top_pick' across history "
      "(only one day so far)", f"got {history['by_recommendation_status_totals'].get('top_pick')}")
check(abs(history["modeled_top_pick_hit_rate"] - 0.5) < 1e-9,
      "modeled_top_pick_hit_rate is exactly 1/2=0.5 for all qualified model outputs",
      f"got {history['modeled_top_pick_hit_rate']}")
check(history["top_pick_hit_rate"] is None and history["public_top_pick_totals"]["hits"] == 0,
      "canonical status alone is not guessed into the deployment-proven public record",
      f"got rate={history['top_pick_hit_rate']} totals={history['public_top_pick_totals']}")
check(abs(history["overall_hit_rate"] - (2 / 5)) < 1e-9,
      "overall_hit_rate (the pre-existing blended field) is still 2/5 -- confirming "
      "top_pick_hit_rate is a genuinely different, additional number, not a rename",
      f"got {history['overall_hit_rate']}")

head("4. the rolling 14-day Top-Pick-only rate is a SEPARATE number from the blended "
     "last_14_days_hit_rate -- direct instruction: never display them as comparable")

check(abs(history["last_14_days_modeled_top_pick_hit_rate"] - 0.5) < 1e-9,
      "the separate rolling modelled Top Pick rate is 1/2",
      f"got {history['last_14_days_modeled_top_pick_hit_rate']}")
check(history["last_14_days_modeled_top_pick_n"] == 2,
      "the modelled window reports exactly the 2 qualified Top Picks",
      f"got {history['last_14_days_modeled_top_pick_n']}")
check(history["last_14_days_top_pick_hit_rate"] is None
      and history["last_14_days_top_pick_n"] == 0,
      "the official rolling public record remains empty without deployment proof",
      f"got rate={history['last_14_days_top_pick_hit_rate']} "
      f"n={history['last_14_days_top_pick_n']}")
check(abs(history["last_14_days_hit_rate"] - (2 / 5)) < 1e-9,
      "the pre-existing blended last_14_days_hit_rate is untouched (2/5), genuinely "
      "different from the explicit modelled Top Pick rate (1/2) -- they must never be shown "
      "as though they answer the same question",
      f"blended={history['last_14_days_hit_rate']} top_pick_only="
      f"{history['last_14_days_modeled_top_pick_hit_rate']}")

head("5. a SECOND day accumulates correctly into by_recommendation_status_totals across days")

DATE2 = "2026-08-16"
write_picks(DATE2, [
    pick(1, "top_pick", stat="hits", needs=1),   # HIT
])
gr.grade_day(DATE2)
with open(gr.HISTORY_FILE) as f:
    history2 = json.load(f)
check(history2["by_recommendation_status_totals"]["top_pick"] == {"hits": 2, "misses": 1, "ungraded": 0},
      "top_pick totals accumulate correctly across both days (1+1 hits, 1+0 misses)",
      f"got {history2['by_recommendation_status_totals']['top_pick']}")
check(abs(history2["modeled_top_pick_hit_rate"] - round(2 / 3, 3)) < 1e-9,
      "modeled_top_pick_hit_rate updates to 2/3 after the second day",
      f"got {history2['modeled_top_pick_hit_rate']}")
check(history2["top_pick_hit_rate"] is None,
      "official public rate remains empty after another non-deployment-proven day")

head("6. graceful degradation: an OLDER history day with no by_recommendation_status key at "
     "all doesn't crash and is silently excluded from the breakdown")

with open(gr.HISTORY_FILE) as f:
    history3 = json.load(f)
history3["days"].insert(0, {"date": "2026-07-01", "hits": 5, "misses": 5, "ungraded": 0,
                            "fair_hits": 5, "fair_misses": 5})  # no by_recommendation_status key
with open(gr.HISTORY_FILE, "w") as f:
    json.dump(history3, f)

DATE3 = "2026-08-17"
write_picks(DATE3, [pick(2, "top_pick", stat="hits", needs=1)])  # MISS
gr.grade_day(DATE3)
with open(gr.HISTORY_FILE) as f:
    history4 = json.load(f)
check(history4["by_recommendation_status_totals"]["top_pick"] == {"hits": 2, "misses": 2, "ungraded": 0},
      "the old, by_recommendation_status-less day contributes nothing to the breakdown "
      "(doesn't crash, doesn't fabricate zeros into a wrong bucket)",
      f"got {history4['by_recommendation_status_totals']['top_pick']}")
check(history4["overall_hit_rate"] is not None,
      "overall_hit_rate still correctly includes the old day's raw hits/misses (5/5), since "
      "THAT field never depended on by_recommendation_status existing")

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

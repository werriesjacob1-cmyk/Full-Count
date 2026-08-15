#!/usr/bin/env python3
"""test_backtest_engine.py — coverage for backtest/engine.py's
best_of_category_extras(), the fix for a real gap: total_bases/home_runs/
runs/rbis/doubles/triples never produced a single backtest row, ever,
across the entire multi-year history, because build_candidates() gives
each batter exactly ONE candidate (chosen by _pick_line competing every
line_options family against each other), and hits/hits_runs_rbis/singles
structurally win that competition almost every time. Those six markets
ship live every night via select_best_by_category()'s independent
per-family selection -- this wires the SAME function into the backtest so
they finally get real backtest/calibration coverage too.

Does not test simulate_date() itself (that needs real Statcast/statsapi
access via PointInTime/build_inputs, exercised by the module's own --verify
and a real one-day smoke run instead) -- only the pure, directly-testable
selection+dedup logic simulate_date() now calls.

    /tmp/mlbvenv/bin/python3 test_backtest_engine.py
"""
import sys

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


import engine as bt
import generate_picks as gp


def batter_with_options(name="Batter", player_id=5, score=70, game_pk=900001, **over):
    c = {
        "type": "batter", "name": name, "player_id": player_id, "team": "Athletics",
        "matchup": "Athletics @ Astros", "game_pk": game_pk, "score": score,
        "confidence": "Medium", "notable_signals": 0, "signals": {},
        "why": [], "watchouts": [],
        # This candidate's OWN main-board pick -- always the hits line here,
        # same as almost every real batter, so total_bases/runs/rbis never
        # win via build_candidates()'s own selection.
        "projection": {"stat": "hits", "value": 0.5, "needs": 1},
        "hit_probability": 0.72,
        "line_options": [
            {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.72, "base_rate": 0.60, "lift": 0.12, "basis": "empirical_shrunk"},
            {"stat": "total_bases", "needs": 1, "line": 0.5, "prob": 0.55, "base_rate": 0.45, "lift": 0.10, "basis": "empirical_shrunk"},
            {"stat": "runs", "needs": 1, "line": 0.5, "prob": 0.40, "base_rate": 0.35, "lift": 0.05, "basis": "empirical_shrunk"},
        ],
    }
    c.update(over)
    return c


head("1. a batter whose main pick is 'hits' still yields total_bases/runs rows via "
     "the per-family expansion -- THE CORE FIX")

candidates = [batter_with_options()]
extras = bt.best_of_category_extras(candidates)
stats = sorted((e.get("projection") or {}).get("stat") for e in extras)
check("total_bases" in stats, "total_bases -- never a main-board winner for this batter -- "
     "still produces an extra candidate", f"got stats={stats}")
check("runs" in stats, "runs -- same story -- also produces an extra candidate",
     f"got stats={stats}")
check("hits" not in stats, "hits is NOT duplicated as an extra -- it's already the main pick",
     f"got stats={stats}")

head("2. dedup: a batter whose main pick genuinely IS the best line in one of these "
     "families is not double-counted")

# This batter's OWN main projection is total_bases itself (the rare case
# where it actually wins _pick_line's cross-family competition).
tb_winner = batter_with_options(name="TB Winner", player_id=6,
                                projection={"stat": "total_bases", "value": 0.5, "needs": 1},
                                line_options=[
                                    {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.50, "base_rate": 0.60, "lift": -0.10, "basis": "empirical_shrunk"},
                                    {"stat": "total_bases", "needs": 1, "line": 0.5, "prob": 0.65, "base_rate": 0.45, "lift": 0.20, "basis": "empirical_shrunk"},
                                ])
extras2 = bt.best_of_category_extras([tb_winner])
tb_extras = [e for e in extras2 if (e.get("projection") or {}).get("stat") == "total_bases"
            and e.get("player_id") == 6]
check(len(tb_extras) == 0, "this batter's total_bases line is NOT re-emitted as an extra "
     "candidate -- it's already covered by the main candidates list, avoiding double-counting "
     "the same (player, stat, needs) outcome as two independent data points",
     f"got {len(tb_extras)} extra total_bases entries for player 6")

head("3. multiple batters in the same category all survive -- volume matters here: "
     "n_per_category must be uncapped, not capped at 1 (verified live: capping at 1 "
     "produced exactly 1 row/day for these markets, ~200x too thin for calibration)")

many = [batter_with_options(name=f"B{i}", player_id=100 + i, game_pk=900000 + i)
       for i in range(12)]
extras3 = bt.best_of_category_extras(many)
tb_count = sum(1 for e in extras3 if (e.get("projection") or {}).get("stat") == "total_bases")
check(tb_count == 12, "all 12 batters' total_bases lines survive -- not truncated to a "
     "handful, which is exactly the bug an accidentally-low n_per_category would reintroduce",
     f"got {tb_count} total_bases extras out of 12 batters")

head("4. an empty candidate list returns no extras (no crash)")

check(bt.best_of_category_extras([]) == [], "no candidates -> no extras, no exception")

head("5. every extra candidate carries player_id/game_pk/projection -- the fields "
     "to_row()/grade_pick() actually need to grade and shape a real row")

extras5 = bt.best_of_category_extras([batter_with_options()])
for e in extras5:
    check(e.get("player_id") is not None, f"{e.get('projection', {}).get('stat')}: player_id present")
    check(e.get("game_pk") is not None, f"{e.get('projection', {}).get('stat')}: game_pk present")
    check((e.get("projection") or {}).get("needs") is not None,
         f"{e.get('projection', {}).get('stat')}: projection.needs present")

head("6. PHASE 3 ITEM 7: every to_row() output is stamped with code_git_sha/"
     "backtest_generated_at -- which COMMIT's scoring code produced this row, distinct "
     "from `date`, the historical date being replayed. Without this, two backtest runs "
     "of the identical date range on two different commits could blend into rows.jsonl "
     "with no way to tell which row came from which formula version.")

pick6 = {"projection": {"stat": "hits", "needs": 1, "value": 0.5}, "game_pk": 1,
        "player_id": 2, "name": "Test Player", "hit_probability": 0.65, "score": 70.0}
graded6 = {"grade": "hit", "actual": 2, "fair_test": True, "actual_pa_est": 4}
row6 = bt.to_row("2026-06-14", pick6, graded6)
check(row6["date"] == "2026-06-14",
      "the historical date being replayed is untouched by the new fields")
check("code_git_sha" in row6, "row carries a code_git_sha key (None outside a git checkout, "
      "never simply absent)", f"got {row6.get('code_git_sha')!r}")
check(row6.get("code_git_sha") == bt.BACKTEST_CODE_GIT_SHA,
      "the row's code_git_sha matches the module-level constant computed once at import -- "
      "not re-derived (and potentially drifted) per row",
      f"got {row6.get('code_git_sha')!r} vs module constant {bt.BACKTEST_CODE_GIT_SHA!r}")
check(row6.get("backtest_generated_at") == bt.BACKTEST_RUN_AT,
      "backtest_generated_at is the one real run-level timestamp, not a fresh datetime.now() "
      "per row (which would make every row in one run look like it happened at a "
      "microscopically different instant for no reason)",
      f"got {row6.get('backtest_generated_at')!r}")

row6b = bt.to_row("2026-06-15", pick6, graded6)
check(row6b["code_git_sha"] == row6["code_git_sha"] and
     row6b["backtest_generated_at"] == row6["backtest_generated_at"],
     "two different historical dates in the SAME run share the identical code/run stamp -- "
     "confirming this is a real run-level constant, not per-row noise")

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

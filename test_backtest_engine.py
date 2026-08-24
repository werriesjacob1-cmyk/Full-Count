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

head("7. STAGE 5 (policy-accurate replay): to_row() only carries "
     "recommendation_status/status_reasons/reliability when apply_policy actually set them "
     "on the pick -- a default (apply_policy=False) run's row shape must stay byte-for-byte "
     "identical to before this existed")

row7_bare = bt.to_row("2026-06-14", pick6, graded6)
check("recommendation_status" not in row7_bare, "no recommendation_status key at all when "
      "the pick never carried one -- not a None value, genuinely absent",
      f"row keys={sorted(row7_bare)}")
check("status_reasons" not in row7_bare, "same for status_reasons")
check("reliability" not in row7_bare, "same for reliability")

pick7_policy = dict(pick6, status="lean",
                    status_reasons=["a real, positive read that doesn't clear every Top "
                                    "Pick requirement"], reliability="B")
row7_policy = bt.to_row("2026-06-14", pick7_policy, graded6)
check(row7_policy.get("recommendation_status") == "lean",
      "the candidate's own 'status' field (classify_recommendation()'s real return key, "
      "matching generate_picks.write_json()'s own rename convention) becomes the row's "
      "recommendation_status")
check(row7_policy.get("status_reasons") == pick7_policy["status_reasons"],
      "status_reasons carried through verbatim")
check(row7_policy.get("reliability") == "B", "reliability carried through")

head("8. STAGE 5: apply_replay_policy_precalibration() runs the REAL "
     "apply_calibration()/attach_reliability(), in generate_picks.py's own real order, "
     "against the real shipped calibrator")

cand8 = {
    "player_id": 501, "type": "batter",
    "projection": {"stat": "hits", "needs": 1, "value": 0.5},
    "hit_probability": 0.55, "line_options": [], "alternatives": [],
}
bt.apply_replay_policy_precalibration([cand8], emp_batters={501: {"games": 62}}, emp_pitchers={})
check(cand8.get("reliability") is not None,
      "attach_reliability() ran and set a real letter grade", f"got {cand8.get('reliability')!r}")
check("raw_hit_probability" in cand8 or cand8.get("calibrated_by") is None,
      "either the real hits calibrator transformed this probability (raw_hit_probability kept "
      "alongside it, matching apply_calibration()'s own contract) or it genuinely declined to "
      "(calibrated_by is None) -- never silently neither",
      f"hit_probability={cand8.get('hit_probability')!r} "
      f"calibrated_by={cand8.get('calibrated_by')!r} "
      f"raw_hit_probability={cand8.get('raw_hit_probability')!r}")

head("9. STAGE 5: apply_replay_policy_classification() runs the REAL "
     "recommendation.attach_recommendations() -- and recommendation_status can only ever "
     "reach lean/neutral here, NEVER top_pick/value, because no real historical market_odds "
     "exists for a point-in-time replay (this is a structural ceiling, not a bug)")

strong_no_odds = {
    "player_id": 502, "type": "batter",
    "projection": {"stat": "hits", "needs": 1, "value": 0.5},
    "hit_probability": 0.90, "reliability": "A", "lineup_assumed": False,
    "lift": 0.30, "line_options": [], "alternatives": [],
    # Deliberately no market_odds/prob_ci -- exactly what a real backtest
    # replay candidate looks like (see this module's own coverage_report:
    # backtest never fetches live FanDuel prices).
}
weak = {
    "player_id": 503, "type": "batter",
    "projection": {"stat": "hits", "needs": 1, "value": 0.5},
    "hit_probability": 0.50, "reliability": "D", "lineup_assumed": False,
    "lift": 0.0, "line_options": [], "alternatives": [],
}
bt.apply_replay_policy_classification([strong_no_odds, weak], [], "2026-06-14")
check(strong_no_odds.get("status") in ("lean", "neutral", "value", "top_pick"),
      "a real status was assigned at all", f"got {strong_no_odds.get('status')!r}")
check(strong_no_odds.get("status") not in ("top_pick", "value"),
      "even a 90% probability, grade-A, confirmed-lineup, strongly-lifted candidate cannot "
      "reach top_pick/value without real market_odds -- classify_recommendation()'s own "
      "require_robust gate makes this structurally unreachable, exactly matching what a "
      "real historical board could and could not have shown without real historical prices",
      f"got {strong_no_odds.get('status')!r}")
check(strong_no_odds.get("status") == "lean",
      "specifically lean: strong probability+evidence+lineup with no price to test is exactly "
      "classify_recommendation()'s own odds-is-None -> lean branch",
      f"got {strong_no_odds.get('status')!r} / "
      f"reasons={strong_no_odds.get('status_reasons')!r}")
check(any("market price" in r for r in (strong_no_odds.get("status_reasons") or [])),
      "the real reason string explains the missing price honestly, not a generic refusal",
      f"got {strong_no_odds.get('status_reasons')!r}")
check(weak.get("status") == "neutral",
      "a genuinely weak read with no price stays neutral, not lean",
      f"got {weak.get('status')!r}")

head("9b. STAGE 5: to_row() correctly renders the FULL replay pipeline's output -- "
     "apply_replay_policy_classification()'s candidate (carrying 'status') becomes a row "
     "carrying 'recommendation_status', end to end")

row9 = bt.to_row("2026-06-14", strong_no_odds, graded6)
check(row9.get("recommendation_status") == "lean",
      "the row correctly shows 'lean', not the raw internal 'status' key name nor None",
      f"got row keys={sorted(row9)}")

head("10. STAGE 5, real bug caught while building this: apply_calibration() mutates "
     "pick['hit_probability'] to the CALIBRATED value in place -- to_row()'s predicted_prob "
     "must stay the RAW value regardless of --apply-policy, or every future calibration fit "
     "against a policy-annotated row would be fitting a curve on already-calibrated numbers")

cand10 = {
    "player_id": 504, "type": "batter",
    "projection": {"stat": "hits", "needs": 1, "value": 0.5},
    "hit_probability": 0.55, "line_options": [], "alternatives": [],
}
bt.apply_replay_policy_precalibration([cand10], emp_batters={504: {"games": 62}}, emp_pitchers={})
check(cand10.get("hit_probability") != 0.55,
      "sanity check on the fixture itself: the real hits calibrator actually transformed "
      "this probability (otherwise this test would pass vacuously)",
      f"got hit_probability={cand10.get('hit_probability')!r}")
row10 = bt.to_row("2026-06-14", cand10, graded6)
check(row10.get("predicted_prob") == 0.55,
      "predicted_prob is still the RAW 0.55, not the calibrated in-place value",
      f"got predicted_prob={row10.get('predicted_prob')!r}")
check(row10.get("calibrated_prob") == cand10.get("hit_probability"),
      "the calibrated number is recorded separately as calibrated_prob, so it isn't lost -- "
      "just kept out of predicted_prob's single, stable meaning",
      f"got calibrated_prob={row10.get('calibrated_prob')!r} "
      f"vs candidate hit_probability={cand10.get('hit_probability')!r}")
check(row10.get("calibrated_by") == "hits", "calibrated_by carried through too",
      f"got {row10.get('calibrated_by')!r}")

head("11. REAL BUG, 2026-08-24 accuracy investigation: predicted_prob must fall back to "
     "hit_probability when raw_hit_probability is a REAL KEY set to None (every candidate "
     "from generate_picks.py's per-family builders carries this -- best.get('raw_prob') is "
     "None until apply_calibration() overwrites it, and 7 of 13 markets have no fitted "
     "curve to ever overwrite it with), not just when the key is absent entirely. "
     "dict.get(key, default) only falls back on an ABSENT key -- a present key holding None "
     "silently wins, which is exactly what made backtest/rows_backfill.jsonl 100% null on "
     "predicted_prob for home_run/total_bases/singles/doubles/triples/runs/rbis despite "
     "every one of those candidates having a real, non-null hit_probability the whole time")

cand11 = {"projection": {"stat": "total_bases", "needs": 2, "value": 1.5}, "game_pk": 1,
         "player_id": 9, "name": "Uncalibrated Market Player", "hit_probability": 0.61,
         "score": 65.0, "raw_hit_probability": None, "calibrated_by": None}
row11 = bt.to_row("2026-06-14", cand11, graded6)
check(row11.get("predicted_prob") == 0.61,
      "predicted_prob falls back to the real hit_probability, not the explicitly-None "
      "raw_hit_probability key",
      f"got predicted_prob={row11.get('predicted_prob')!r}")

head("11b. the same fallback still correctly stays RAW (not calibrated) when "
     "raw_hit_probability genuinely WAS set by a real calibration transform -- 11 must not "
     "have fixed the null-fallback bug by breaking check 10's raw-vs-calibrated distinction")

cand11b = {"projection": {"stat": "hits", "needs": 1, "value": 0.5}, "game_pk": 1,
          "player_id": 10, "name": "Calibrated Market Player", "hit_probability": 0.70,
          "raw_hit_probability": 0.55, "calibrated_by": "hits"}
row11b = bt.to_row("2026-06-14", cand11b, graded6)
check(row11b.get("predicted_prob") == 0.55,
      "predicted_prob is still the real pre-calibration 0.55, never the calibrated 0.70, "
      "when raw_hit_probability was genuinely set (not the None-default sentinel)",
      f"got predicted_prob={row11b.get('predicted_prob')!r}")

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

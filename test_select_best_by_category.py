#!/usr/bin/env python3
"""test_select_best_by_category.py — coverage for generate_picks.select_
best_by_category(), the "best available in EVERY prop family" board. Had
zero test coverage despite its own comments documenting a real, previously
-shipped bug: strikeout candidates always showed market_odds=null even
when odds_fanduel had already found a real two-sided price, because this
function used to blindly recompute prices from a one-sided feed instead of
reusing the already-attached result.

    /tmp/mlbvenv/bin/python3 test_select_best_by_category.py
"""
import sys

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
import odds_fanduel as fd
import prop_probability as pp


def batter_with_options(name="Batter", score=70, player_id=5, **over):
    c = {
        "type": "batter", "name": name, "player_id": player_id, "team": "Athletics",
        "matchup": "Athletics @ Astros", "game_pk": 900001, "score": score,
        "confidence": "Medium", "notable_signals": 0, "signals": {},
        "why": [], "watchouts": [],
        "line_options": [
            {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.72, "base_rate": 0.60, "lift": 0.12, "basis": "empirical_shrunk"},
            {"stat": "hits", "needs": 2, "line": 1.5, "prob": 0.30, "base_rate": 0.20, "lift": 0.10, "basis": "empirical_shrunk"},
            {"stat": "total_bases", "needs": 1, "line": 0.5, "prob": 0.75, "base_rate": 0.65, "lift": 0.10, "basis": "empirical_shrunk"},
        ],
    }
    c.update(over)
    return c


def single_line_pitcher(name="SP", score=70, stat="strikeouts", prob=0.65, needs=5, **over):
    c = {
        "type": "pitcher", "name": name, "player_id": 501, "team": "Astros",
        "matchup": "Athletics @ Astros", "game_pk": 900001, "score": score,
        "confidence": "Medium", "notable_signals": 0, "signals": {}, "why": [], "watchouts": [],
        "prop": f"Over {needs - 0.5} {stat}", "lean": None,
        "projection": {"stat": stat, "value": needs - 0.5, "needs": needs},
        "hit_probability": prob, "base_rate": 0.5, "lift": round(prob - 0.5, 4),
        "probability_basis": "modelled_shrunk", "probability_detail": {"empirical": None, "modelled": prob},
        "market_odds": -120, "market_implied": 0.545, "market_edge": round(prob - 0.545, 4),
        "price_clears": True, "alternatives": [],
    }
    c.update(over)
    return c


head("1. MIN_QUALITY_SCORE gates every category, including the line_options branch")

low_score = batter_with_options(score=gp.MIN_QUALITY_SCORE - 1)
out = gp.select_best_by_category([low_score], {}, fd)
check(out == {}, "a batter scoring under MIN_QUALITY_SCORE contributes to NO category at all")

head("2. a batter's line_options are re-split per stat family, each choosing its own best line "
     "via _pick_line (not just re-using the batter's own chosen main-board projection)")

out = gp.select_best_by_category([batter_with_options()], {}, fd)
check("hits" in out and "total_bases" in out, "both hits and total_bases categories are "
      "populated from the same batter's line_options", f"got keys={sorted(out.keys())}")
check(out["hits"][0]["projection"]["needs"] == 1,
      "the hits category picks the line _pick_line would choose among this player's own "
      "hits options (needs=1 at 72% beats needs=2 at 30% on lift/floor)",
      f"got {out['hits'][0]['projection']}")

head("3. single-line families (pitcher/game/pitcher_combo types) reuse the candidate's "
     "OWN already-attached market price -- THE BUG THIS REPLACES: this must NOT be "
     "recomputed from a one-sided `prices` feed that strikeouts was never even in")

sp = single_line_pitcher(market_odds=-115, price_clears=True)
out = gp.select_best_by_category([sp], {}, fd)
check("strikeouts" in out, "a qualifying strikeout candidate populates the strikeouts category")
check(out["strikeouts"][0]["market_odds"] == -115,
      "market_odds is the candidate's OWN already-attached value (-115), not recomputed "
      "from the (empty, one-sided) `prices` dict passed to this call -- proves single-line "
      "families reuse attach_market_prices()'s own result rather than re-deriving it",
      f"got {out['strikeouts'][0]['market_odds']}")

head("4. line_options-branch candidates DO get a fresh price lookup via prices/fd.normalize_name")

prices = {fd.normalize_name("Batter"): {("hits", 1): -140}}
out = gp.select_best_by_category([batter_with_options(name="Batter")], prices, fd)
check(out["hits"][0]["market_odds"] == -140,
      "the line_options branch looks up its own market price via normalize_name + "
      "(stat, needs), separately from any price the batter's main candidate carried",
      f"got {out['hits'][0]['market_odds']}")

head("5. clears_main_board_floor reflects MIN_LINE_PROB honestly, independent of ranking here")

high_prob_sp = single_line_pitcher(hit_probability=0.75)
low_prob_sp = single_line_pitcher(hit_probability=0.40, player_id=502, name="Weak SP")
out = gp.select_best_by_category([high_prob_sp, low_prob_sp], {}, fd, n_per_category=2)
by_name = {e["name"]: e for e in out["strikeouts"]}
check(by_name["SP"]["clears_main_board_floor"] is True,
      "a 75% candidate is flagged as clearing the main board's 60% floor")
check(by_name["Weak SP"]["clears_main_board_floor"] is False,
      "a 40% candidate (well under the floor) is honestly flagged as NOT clearing it, "
      "yet still appears here -- this board deliberately has no MIN_LINE_PROB floor of "
      "its own")

head("6. n_per_category truncates and ranks by hit_probability descending")

pitchers = [single_line_pitcher(player_id=500 + i, name=f"SP{i}", hit_probability=p)
           for i, p in enumerate([0.55, 0.80, 0.65, 0.90], start=1)]
out = gp.select_best_by_category(pitchers, {}, fd, n_per_category=2)
probs = [e["hit_probability"] for e in out["strikeouts"]]
check(probs == [0.90, 0.80], "only the top 2 by hit_probability survive, in descending order",
      f"got {probs}")

head("7. a home_runs candidate can appear here too (select_moonshots owns display, but "
     "this function's own docstring flags home_runs as the category a missing dict entry "
     "silently excluded before) -- CATEGORY_LABELS must include it")

check("home_runs" in gp.CATEGORY_LABELS, "home_runs is present in CATEGORY_LABELS -- the "
      "exact regression this function's own audit comment describes")

head("8. an unpriceable candidate (hit_probability=None) never enters any category")

no_prob = single_line_pitcher(hit_probability=None)
out = gp.select_best_by_category([no_prob], {}, fd)
check(out == {}, "a candidate with no hit_probability at all is excluded from every category")

head("9. an empty candidate list returns an empty dict")

check(gp.select_best_by_category([], {}, fd) == {}, "no candidates returns an empty dict")

head("10. min_score=0 recovers a category the default MIN_QUALITY_SCORE floor drops entirely "
     "-- direct request: \"we should always track every prop... We can't just throw them away\"")

below_floor = single_line_pitcher(stat="strikeouts", score=gp.MIN_QUALITY_SCORE - 5)
default_out = gp.select_best_by_category([below_floor], {}, fd)
unfloored_out = gp.select_best_by_category([below_floor], {}, fd, min_score=0)
check(default_out == {}, "the default call (no min_score) still drops it, unchanged behavior")
check("strikeouts" in unfloored_out and unfloored_out["strikeouts"][0]["score"] == below_floor["score"],
      "min_score=0 recovers the exact same below-floor candidate instead of discarding it",
      f"got {unfloored_out}")

head("11. lineup_assumed survives into the output row, in BOTH branches -- real bug, found "
     "live 2026-08-15: this function's own output dicts are fixed field lists that silently "
     "dropped lineup_assumed, so a batter whose lineup slot is a guess (quality_control()'s "
     "assumed=True tag) came out of here indistinguishable from a fully confirmed one, with "
     "no way for a caller to badge it")

assumed_batter_out = gp.select_best_by_category([batter_with_options(lineup_assumed=True)], {}, fd)
check(assumed_batter_out["hits"][0]["lineup_assumed"] is True,
      "the line_options branch (batters) preserves lineup_assumed", f"got {assumed_batter_out}")
confirmed_batter_out = gp.select_best_by_category(
    [batter_with_options(name="Confirmed Batter", player_id=6)], {}, fd)
check(confirmed_batter_out["hits"][0].get("lineup_assumed") is None,
      "a normal (confirmed) batter with no lineup_assumed key stays honestly absent")

assumed_pitcher_out = gp.select_best_by_category([single_line_pitcher(lineup_assumed=True)], {}, fd)
check(assumed_pitcher_out["strikeouts"][0]["lineup_assumed"] is True,
      "the single-line branch (pitchers/stolen_base/nrfi) preserves lineup_assumed too",
      f"got {assumed_pitcher_out}")

head("12. 2026-08-18 Pre-Phase-V finding A4, end to end: apply_calibration() -> "
     "select_best_by_category() -- the corrected pipeline order every real caller (live "
     "dashboard, static pipeline, value_board.py, backtest/threshold_sensitivity.py all "
     "share via score_slate()/_build_and_score()). The calibrated, market-specific "
     "probability must be what reaches pricing/classification, not the raw pre-calibration "
     "value this finding was about.")

per_market = {"hits": lambda p: p * 0.9, "total_bases": lambda p: p * 0.7}
glob = lambda p: p * 0.5
batter = batter_with_options()
gp.apply_calibration([batter], (per_market, glob))
prices = {fd.normalize_name("Batter"): {("hits", 1): -140, ("total_bases", 1): -200}}
out12 = gp.select_best_by_category([batter], prices, fd)

check(abs(out12["hits"][0]["hit_probability"] - (0.72 * 0.9)) < 1e-9,
      "the hits category's final hit_probability is the CALIBRATED value (0.72*0.9=0.648), "
      "not the raw pre-calibration 0.72 this finding was about",
      f"got {out12['hits'][0]['hit_probability']}")
check(out12["hits"][0]["raw_hit_probability"] == 0.72 and out12["hits"][0]["calibrated_by"] == "hits",
      "the hits category's own provenance is correct and market-specific",
      str({k: out12["hits"][0][k] for k in ("raw_hit_probability", "calibrated_by")}))
check(abs(out12["total_bases"][0]["hit_probability"] - (0.75 * 0.7)) < 1e-9,
      "the total_bases category independently uses the TOTAL_BASES curve (0.75*0.7=0.525), "
      "never the hits curve or the primary line's own calibration",
      f"got {out12['total_bases'][0]['hit_probability']}")
check(out12["total_bases"][0]["calibrated_by"] == "total_bases",
      "total_bases reports its own market-specific calibrated_by, distinct from hits'")

check(abs(out12["hits"][0]["market_edge"] - round(0.648 - pp.implied_probability(-140), 4)) < 1e-9,
      "market_edge for the hits category is computed from the CALIBRATED probability "
      "(requirement: pricing/market_edge must use the calibrated probability)",
      f"got {out12['hits'][0]['market_edge']}")

head("13. 2026-08-24 combined-strikeouts settlement investigation: combo_player_ids survives "
     "into the single-line branch's output row -- real bug, found live via 28/28 real "
     "combined_strikeouts rows in results/grades_*.json permanently stuck 'ungraded: missing "
     "combo_player_ids'. Same failure class as lineup_assumed in check 11: this function's own "
     "fixed field list silently dropped combo_player_ids, the one field grade_pick()'s "
     "combined_strikeouts branch requires to settle from two starters' box scores.")

combo_pitcher_out = gp.select_best_by_category(
    [single_line_pitcher(name="Starter A & Starter B", stat="combined_strikeouts", needs=11,
                         type="pitcher_combo", combo_player_ids=[501, 502])],
    {}, fd,
)
check(combo_pitcher_out["combined_strikeouts"][0].get("combo_player_ids") == [501, 502],
      "the single-line branch (pitcher_combo) preserves combo_player_ids -- this is the exact "
      "field grade_pick() reads to settle from both starters' box scores",
      f"got {combo_pitcher_out['combined_strikeouts'][0]}")

single_starter_out = gp.select_best_by_category(
    [single_line_pitcher(name="Solo Starter", stat="strikeouts", needs=5)], {}, fd,
)
check(single_starter_out["strikeouts"][0].get("combo_player_ids") is None,
      "a normal (non-combo) pitcher with no combo_player_ids key stays honestly absent, not "
      "manufactured", f"got {single_starter_out['strikeouts'][0]}")

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

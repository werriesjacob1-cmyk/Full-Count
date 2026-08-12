#!/usr/bin/env python3
"""test_current_weight_score.py — coverage for backtest.signals.current_
weight_score() and, specifically, regression tests for two real drift bugs
found and fixed 2026-08-12:

1. CURRENT_WEIGHTS["stolen_base"] documented the weights as skill*0.55 +
   matchup*0.30 + context*0.15, but generate_picks.score_stolen_base()
   actually computes skill*0.50 + matchup*0.28 + context*0.22 -- an older,
   stale copy of the formula.
2. CURRENT_WEIGHTS["walks"] documented the ORIGINAL hand-picked weights
   (0.40/0.40/0.20), but score_walk's weights were refit from real data to
   0.66/0.24/0.10 the same day this table was written, and walks scoring
   stayed live in build_candidates() for roughly another 27 hours before
   the market was removed entirely -- confirmed via git history
   (b9b6359 refit the weights the same day e18fb92 added this table,
   neither commit touched the other side).

current_weight_score() exists specifically to reconstruct "what the
shipped formula scores" as the baseline every fitted-alternative
comparison in this module is measured against, so a stale weight table
silently biases every such comparison (or, for the now-dead walks market,
biases any future re-grading of the picks from that ~27-hour window).

This locks the corrected weights in directly against the real scoring
functions' own output, so a future edit to either side (the scoring
formula or this reconstruction table) that lets them drift apart again is
caught immediately, rather than silently, the way these two were.

    /tmp/mlbvenv/bin/python3 test_current_weight_score.py
"""
import sys

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


import generate_picks as gp
from backtest import signals as bt_signals

GM = {"matchup": "Athletics @ Astros", "game_pk": 900001}
BATTER = {"name": "Speedy Runner", "id": 5, "team": "Athletics", "order": 1}

head("1. THE REGRESSION THIS PREVENTS: CURRENT_WEIGHTS['stolen_base'] weight VALUES "
     "now match score_stolen_base()'s real, shipped 0.50/0.28/0.22 split")

table = bt_signals.CURRENT_WEIGHTS["stolen_base"]
check(abs(table["sprint_speed"][0] - 0.50) < 1e-9,
      "sprint_speed's weight is 0.50, matching score_stolen_base's real skill*0.50 term",
      f"got {table['sprint_speed'][0]}")
check(abs(table["catcher_poptime"][0] - 0.28) < 1e-9,
      "catcher_poptime's weight is 0.28, matching score_stolen_base's real matchup*0.28 "
      "term -- was stale at 0.30 (an older version of the formula) before this fix",
      f"got {table['catcher_poptime'][0]}")
check(abs(table["season_sb"][0] - 0.22) < 1e-9,
      "the third slot's weight is 0.22, matching score_stolen_base's real context*0.22 "
      "term -- was stale at 0.15 before this fix", f"got {table['season_sb'][0]}")
check(abs(sum(w for w, _ in table.values()) - 1.00) < 1e-9,
      "the three weights still sum to exactly 1.00")

head("2. END-TO-END on the two slots that DO share a name: sprint_speed and "
     "catcher_poptime reconstruct exactly, using the corrected weights, against a "
     "candidate that has no on_base/season_sb signal at all (so the third slot -- "
     "deliberately never aliased, see the fix comment above CURRENT_WEIGHTS -- "
     "falls back to the same NEUTRAL=50 on both sides and drops out of the diff)")

c = gp.score_stolen_base(BATTER, GM, opp_catcher_poptime=2.05, sprint_speed=28.7,
                         batter_season=None)  # no season data -> no on_base signal at all
check("on_base" not in c["signals"], "sanity: no batter_season means no on_base signal "
      "is recorded at all, so score_stolen_base itself used NEUTRAL (50) for context")
reconstructed = bt_signals.current_weight_score(
    {"signals": c["signals"], "prop_type": "stolen_base"})
check(reconstructed is not None, "a real stolen_base candidate's signals reconstruct "
      "to a real number, not None")
check(abs(reconstructed - c["score"]) < 0.05,
      "with context neutral on BOTH sides (score_stolen_base has no on_base signal to "
      "score with, and current_weight_score's season_sb slot never matches anyway), "
      "the corrected sprint_speed/catcher_poptime weights alone reproduce the exact "
      "real score", f"reconstructed={reconstructed} actual_score={c['score']}")

head("2b. DOCUMENTED, DELIBERATE MISMATCH: when a real on_base signal IS present, the "
     "reconstruction diverges from the real score specifically because season_sb never "
     "matches on_base -- proving the 'coverage warning, not silent mislabeling' design "
     "actually behaves as documented, not just as claimed in a comment")

c_with_onbase = gp.score_stolen_base(BATTER, GM, opp_catcher_poptime=2.05, sprint_speed=28.7,
                                     batter_season={"wOBA": 0.350, "pa": 200})
check("on_base" in c_with_onbase["signals"], "sanity: a real wOBA reading produces a "
      "real on_base signal this time")
reconstructed_mismatch = bt_signals.current_weight_score(
    {"signals": c_with_onbase["signals"], "prop_type": "stolen_base"})
expected_if_neutral_context = (
    0.50 * c_with_onbase["signals"]["sprint_speed"]
    + 0.28 * c_with_onbase["signals"]["catcher_poptime"]
    + 0.22 * 50.0)
check(abs(reconstructed_mismatch - expected_if_neutral_context) < 1e-6,
      "the reconstruction with a real on_base signal present still falls back to "
      "NEUTRAL for the context slot, exactly as if on_base weren't there at all -- "
      "the deliberate, documented behavior", f"got {reconstructed_mismatch}, "
      f"want {expected_if_neutral_context}")

head("3. THE SECOND REGRESSION THIS PREVENTS: CURRENT_WEIGHTS['walks'] weight VALUES "
     "now match score_walk()'s real, fitted 0.66/0.24/0.10 split, not the original "
     "hand-picked 0.40/0.40/0.20 the table was stuck at")

walks_table = bt_signals.CURRENT_WEIGHTS["walks"]
check(abs(walks_table["batter_bb_pct"][0] - 0.66) < 1e-9,
      "batter_bb_pct's weight is 0.66, matching score_walk's real fitted skill*0.66 term",
      f"got {walks_table['batter_bb_pct'][0]}")
check(abs(walks_table["sp_bb_pct"][0] - 0.24) < 1e-9,
      "sp_bb_pct's weight is 0.24, matching score_walk's real fitted matchup*0.24 term",
      f"got {walks_table['sp_bb_pct'][0]}")
check(abs(walks_table["ump_accuracy"][0] - 0.10) < 1e-9,
      "ump_accuracy's weight is 0.10, matching score_walk's real fitted context*0.10 term",
      f"got {walks_table['ump_accuracy'][0]}")
check(abs(sum(w for w, _ in walks_table.values()) - 1.00) < 1e-9,
      "the three walks weights still sum to exactly 1.00")

head("4. END-TO-END: current_weight_score() reconstructs the SAME number score_walk() "
     "itself actually computes, from a real call's own recorded signals")

WALK_GM = {"matchup": "Athletics @ Astros", "game_pk": 900001}
walk_c = gp.score_walk(
    {"name": "Patient Hitter", "id": 1, "team": "Athletics"}, WALK_GM,
    {"BB%": 11.0}, {"Athletics @ Astros": {"accuracy": 93.0}}, {"BB%": 9.5})
walk_reconstructed = bt_signals.current_weight_score(
    {"signals": walk_c["signals"], "prop_type": "walks"})
check(walk_reconstructed is not None,
      "a real score_walk candidate's signals reconstruct to a real number, not None")
check(abs(walk_reconstructed - walk_c["score"]) < 0.05,
      "current_weight_score()'s reconstruction matches score_walk()'s own real score "
      "almost exactly, using the corrected fitted weights",
      f"reconstructed={walk_reconstructed} actual_score={walk_c['score']}")

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

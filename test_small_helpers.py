#!/usr/bin/env python3
"""test_small_helpers.py — coverage for several small but load-bearing
generate_picks.py helpers that had zero test coverage: _team_label,
_american_to_prob, _implied_total_from_line, compute_bullpen_era,
load_calibrator, and score_walk (the walk-prop scorer, no longer wired
into build_candidates since no real "Player to Draw a Walk" FanDuel market
exists, but its own docstring says the fitted model itself is real and
kept in place for a market that might return).

_team_label's own docstring documents a real, previously-shipped bug:
score_combined_strikeouts's picks have team=None (a PRESENT key with a
None value, since the pick spans two teams), and bracket access on that
key rendered the literal text "(None)" in the markdown board.

    /tmp/mlbvenv/bin/python3 test_small_helpers.py
"""
import sys
import os
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
import pandas as pd

head("== _team_label ==")
head("1. THE BUG THIS FIXES: team=None (a present key, combined_strikeouts picks) "
     "renders as 'combined', not the literal string 'None'")

check(gp._team_label({"team": None}) == "combined",
      "a candidate with team=None (a present key with a None value) renders as "
      "'combined', not '(None)' rendered via naive f-string interpolation",
      f"got {gp._team_label({'team': None})!r}")
check(gp._team_label({"team": "Athletics"}) == "Athletics",
      "a normal candidate's real team name passes through unchanged")
check(gp._team_label({}) == "combined",
      "a candidate with no 'team' key at all also falls back to 'combined', not a KeyError")

head("== _american_to_prob ==")
head("2. standard American-odds conversions, both sides of the line")

check(abs(gp._american_to_prob(-150) - 0.6) < 1e-9, "-150 implies exactly 60%",
      f"got {gp._american_to_prob(-150)}")
check(abs(gp._american_to_prob(150) - 0.4) < 1e-9, "+150 implies exactly 40%",
      f"got {gp._american_to_prob(150)}")
check(abs(gp._american_to_prob(-110) - (110 / 210)) < 1e-9, "-110 (standard vig line) "
      "implies 110/210", f"got {gp._american_to_prob(-110)}")

head("3. degenerate/missing inputs return None, never a fabricated probability")

check(gp._american_to_prob(None) is None, "odds=None returns None")
check(gp._american_to_prob(0) is None, "odds=0 (meaningless/degenerate) returns None")
check(gp._american_to_prob("garbage") is None, "a non-numeric string returns None, not a crash")
check(gp._american_to_prob(float("nan")) is None, "NaN odds return None, not a NaN probability")

head("== _implied_total_from_line ==")
head("4. missing/invalid inputs return None, never a guessed total")

check(gp._implied_total_from_line(None, -110, -110) is None, "line=None returns None")
check(gp._implied_total_from_line(3.5, None, -110) is None, "over_odds=None returns None")
check(gp._implied_total_from_line(3.5, -110, None) is None, "under_odds=None returns None")
check(gp._implied_total_from_line(float("nan"), -110, -110) is None,
      "a NaN line returns None (the exact NaN guard this file's scale() docstring "
      "describes being bitten by before)")
check(gp._implied_total_from_line("garbage", -110, -110) is None,
      "a non-numeric line returns None, not a crash")

head("5. a real, live-verified case: relative to a SYMMETRIC (no-lean) market on the same "
     "line, a team the market expects BELOW its line prices LOWER, and one the market "
     "expects ABOVE its line prices HIGHER -- checked relative to the no-lean baseline, "
     "not against the raw line number, since the solver's own docstring documents a real, "
     "known, caller-corrected systematic high bias that a synthetic no-lean case would "
     "otherwise be mistaken for a test failure")

below_line = gp._implied_total_from_line(3.5, 114, -145)   # over +114 / under -145: market leans UNDER
sym_35 = gp._implied_total_from_line(3.5, -110, -110)
above_line = gp._implied_total_from_line(4.5, -125, 110)   # over -125 / under +110: market leans OVER
sym_45 = gp._implied_total_from_line(4.5, -110, -110)
check(below_line is not None and above_line is not None,
      "both real two-sided prices resolve to a real implied total, not None",
      f"below={below_line} above={above_line}")
check(below_line < sym_35,
      "the UNDER-favored 3.5 line resolves BELOW the same line's own no-lean baseline",
      f"under-leaning={below_line} no-lean baseline={sym_35}")
check(above_line > sym_45,
      "the OVER-favored 4.5 line resolves ABOVE the same line's own no-lean baseline",
      f"over-leaning={above_line} no-lean baseline={sym_45}")

head("6. symmetric (no-vig-lean) two-sided pricing increases monotonically with the line")

sym_lines = [gp._implied_total_from_line(l, -110, -110) for l in (2.5, 3.5, 4.5, 5.5)]
check(all(a < b for a, b in zip(sym_lines, sym_lines[1:])),
      "a higher symmetric per-team line always resolves to a higher implied total, "
      "strictly monotonic", f"got {sym_lines}")

head("== compute_bullpen_era ==")
head("7. None/empty dataframe, or one missing required columns, returns {} not a crash")

check(gp.compute_bullpen_era(None) == {}, "pit_season_df=None returns {}")
check(gp.compute_bullpen_era(pd.DataFrame()) == {}, "an empty DataFrame returns {}")
check(gp.compute_bullpen_era(pd.DataFrame({"Team": ["NYY"], "ERA": [3.5]})) == {},
      "a DataFrame missing required columns (G/GS/IP) returns {}, not a KeyError")

head("8. a real reliever frame aggregates to an IP-weighted team ERA, with a thin-sample floor")

df = pd.DataFrame([
    {"Team": "NYY", "G": 40, "GS": 0, "ERA": 3.00, "IP": 40.0},
    {"Team": "NYY", "G": 35, "GS": 0, "ERA": 4.50, "IP": 30.0},
    {"Team": "NYY", "GS": 20, "G": 20, "ERA": 3.20, "IP": 110.0},  # a starter -- excluded
    {"Team": "BOS", "G": 10, "GS": 0, "ERA": 2.00, "IP": 8.0},     # too thin (<30 IP) -- excluded
])
orig_get_team_ids = gp.m.get_team_ids
gp.m.get_team_ids = lambda: [{"id": 1, "abbr": "NYY", "name": "New York Yankees"},
                             {"id": 2, "abbr": "BOS", "name": "Boston Red Sox"}]
try:
    out = gp.compute_bullpen_era(df)
finally:
    gp.m.get_team_ids = orig_get_team_ids

check("New York Yankees" in out, "the Yankees' real relievers (70 IP total, over the "
      "30 IP floor) produce a bullpen entry keyed by full team name", f"got {out}")
expected_era = round((3.00 * 40.0 + 4.50 * 30.0) / 70.0, 2)
check(abs(out["New York Yankees"]["era"] - expected_era) < 0.01,
      "the ERA is IP-weighted across the two real relievers, EXCLUDING the starter row",
      f"got {out['New York Yankees']['era']}, want {expected_era}")
check("Boston Red Sox" not in out,
      "Boston's relievers (8 IP total, under the 30 IP thin-sample floor) are excluded "
      "entirely rather than reporting an unreliable team ERA")

head("9. FanGraphs team-abbreviation quirks (CHW/KCR/SDP/SFG/TBR/WSN) are bridged to the "
     "MLB Stats API's own abbreviations, not left to silently never match")

df2 = pd.DataFrame([{"Team": "SFG", "G": 30, "GS": 0, "ERA": 3.80, "IP": 50.0}])
gp.m.get_team_ids = lambda: [{"id": 1, "abbr": "SF", "name": "San Francisco Giants"}]
try:
    out2 = gp.compute_bullpen_era(df2)
finally:
    gp.m.get_team_ids = orig_get_team_ids
check("San Francisco Giants" in out2,
      "FanGraphs' 'SFG' abbreviation correctly bridges to the Stats API's 'SF' and "
      "resolves to the real team name, rather than being silently dropped",
      f"got {out2}")

head("== load_calibrator ==")
head("10. no calibrator files present at all returns None, never fatal")

tmpdir = tempfile.mkdtemp()
orig_cal_path = gp.CALIBRATOR_PATH
orig_by_market_path = gp.CALIBRATORS_BY_MARKET_PATH
gp.CALIBRATOR_PATH = os.path.join(tmpdir, "nonexistent_calibrator.pkl")
gp.CALIBRATORS_BY_MARKET_PATH = os.path.join(tmpdir, "nonexistent_by_market.pkl")
try:
    result = gp.load_calibrator()
finally:
    gp.CALIBRATOR_PATH = orig_cal_path
    gp.CALIBRATORS_BY_MARKET_PATH = orig_by_market_path
check(result is None, "with neither calibrator file present, load_calibrator() returns "
      "None cleanly rather than raising", f"got {result}")

head("== score_walk ==")
head("11. no BB% signal at all (bb_pct and sp_bb_pct both None) returns None, not a crash")

GM = {"matchup": "Athletics @ Astros", "game_pk": 900001}
check(gp.score_walk({"name": "X", "id": 1, "team": "Athletics"}, GM, None, {}, None) is None,
      "with no batter season BB% and no opposing SP BB%, there is no real signal to work "
      "with, so this returns None rather than a neutral-everything score")

head("12. a real call with both signals present returns a well-formed, never-shipped candidate")

REQUIRED_KEYS = {"type", "name", "player_id", "team", "matchup", "game_pk", "prop",
                 "projection", "signals", "score", "projected_pa", "why", "watchouts",
                 "notable_signals", "confidence"}
c = gp.score_walk({"name": "Patient Hitter", "id": 1, "team": "Athletics", "order": 3}, GM,
                  {"BB%": 11.0}, {"Athletics @ Astros": {"accuracy": 93.0}}, {"BB%": 10.0})
check(REQUIRED_KEYS.issubset(c.keys()), "a real call with batter/pitcher BB% present "
      "returns a well-formed candidate", f"missing: {REQUIRED_KEYS - c.keys()}")
check(0 <= c["score"] <= 100, "score is bounded to [0, 100]")
check(c["projection"] == {"stat": "walks", "value": 0.7},
      "the projection is pinned at the documented fixed value (0.7), matching "
      "attach_hit_probabilities' P(>=1 BB in projected_pa trials) pricing model")

head("13. a higher batter BB% scores strictly higher, at identical pitcher/ump context")

c_patient = gp.score_walk({"name": "P", "id": 1, "team": "Athletics"}, GM,
                          {"BB%": 8.0}, {}, {"BB%": 14.0})
c_free_swinger = gp.score_walk({"name": "F", "id": 2, "team": "Athletics"}, GM,
                               {"BB%": 8.0}, {}, {"BB%": 6.0})
check(c_patient["score"] > c_free_swinger["score"],
      "a batter with 14% BB% scores higher than one with 6% BB%, same opposing pitcher",
      f"patient={c_patient['score']} free_swinger={c_free_swinger['score']}")

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

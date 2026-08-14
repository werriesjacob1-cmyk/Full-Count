#!/usr/bin/env python3
"""test_score_moonshot.py — coverage for generate_picks.score_moonshot(),
the "To Hit a Moonshot (420+ FT)" FanDuel market (PLAYER_TO_HIT_A_HOME_RUN_
420+_FEET), built 2026-08-14 after the user's own FanDuel screenshot
confirmed the market is real and matched exactly against a live API pull.
Same shape as score_laser (a single Statcast-derived, Beta-shrunk per-game
rate used directly as hit_probability), just one threshold instead of two,
so this mirrors test_score_laser.py's structure directly.

    /tmp/mlbvenv/bin/python3 test_score_moonshot.py
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

GM = {"matchup": "Athletics @ Astros", "game_pk": 900001}
BATTER = {"name": "Slugger", "id": 5, "team": "Athletics"}

REQUIRED_KEYS = {"type", "name", "player_id", "team", "matchup", "game_pk", "prop",
                 "projection", "hit_probability", "base_rate", "lift", "probability_basis",
                 "probability_detail", "sample_n", "alternatives", "signals", "score",
                 "why", "watchouts", "notable_signals", "confidence"}


def rates_table(p=None, n=80, lg=0.02):
    if p is None:
        return {}
    return {"rates": {"moonshot_420plus": {"p_hat": p, "league_p": lg, "n": n}}}


head("1. no id / not in moonshot_rates / no rates dict -> None, not a crash")

check(gp.score_moonshot({"name": "No ID"}, GM, {}) is None, "a batter with no id returns None")
check(gp.score_moonshot(BATTER, GM, {}) is None, "batter not present in moonshot_rates returns None")
check(gp.score_moonshot(BATTER, GM, {5: {}}) is None, "an entry present but with no 'rates' key returns None")
check(gp.score_moonshot(BATTER, GM, {5: {"rates": {}}}) is None,
      "an entry with an empty rates dict returns None")

head("2. a normal call with real rate data returns a well-formed candidate")

c = gp.score_moonshot(BATTER, GM, {5: rates_table(p=0.08, n=90, lg=0.02)})
check(REQUIRED_KEYS.issubset(c.keys()), "the return dict carries every key downstream code depends on",
      f"missing: {REQUIRED_KEYS - c.keys()}")
check(c["type"] == "batter" and c["player_id"] == 5, "identity fields pass through correctly")
check(c["probability_basis"] == "empirical_shrunk", "uses the empirical-shrunk basis, no modelled blend")
check(c["probability_detail"] == {"empirical": c["hit_probability"], "modelled": None},
      "probability_detail records empirical only, modelled=None")
check(c["hit_probability"] == 0.08, "hit_probability is the shrunk p_hat directly, no further transform")
check(c["projection"] == {"stat": "moonshot_420", "value": 1, "needs": 1},
      "projection uses the real market's stat name and needs=1 (a single yes/no line)",
      f"got {c['projection']}")
check(c["alternatives"] == [], "no alternatives -- unlike score_laser, there is only one real "
      "threshold FanDuel posts, confirmed live (no separate 400+ FT market exists)")

head("3. thin sample (< MOONSHOT_SCORE_CONFIDENCE_GAMES) caps the score at 55")

# Deliberately inflated p/lift (not a realistic real-world moonshot rate --
# verified live the real ceiling is ~10-11%) specifically so the RAW score
# clears 55 before capping, the same way score_laser's own test does. Real
# moonshot rates never get anywhere near this, so the confidence cap is a
# near-total no-op against realistic values -- this isolates the mechanism
# itself rather than testing it against a value too small to ever trigger it.
c_thin = gp.score_moonshot(BATTER, GM, {5: rates_table(p=0.30, lg=0.02, n=10)})
check(c_thin["score"] <= 55, "a 10-game sample (well under the confidence floor) caps the score "
      "at 55 even though the raw rate/lift alone would score well above it", f"got {c_thin['score']}")
check(c_thin["sample_n"] == 10, "sample_n correctly reports the real (thin) sample size")

c_thick = gp.score_moonshot(BATTER, GM, {5: rates_table(p=0.30, lg=0.02, n=200)})
check(c_thick["score"] > c_thin["score"],
      "the identical rate/lift with a real 200-game sample scores strictly higher than the "
      "10-game version, purely from the confidence cap lifting", f"thick={c_thick['score']} thin={c_thin['score']}")

head("4. notable_signals reflects whether the lift clears the 0.03 bar (a lower bar than "
     "score_laser's 0.05 -- this market's real league rate is much smaller, ~2%, so a "
     "meaningfully large lift here is smaller in absolute terms too)")

c_big_lift = gp.score_moonshot(BATTER, GM, {5: rates_table(p=0.10, lg=0.02, n=100)})
check(c_big_lift["notable_signals"] == 1, "a lift of +0.08 (well over 0.03) sets notable_signals=1")

c_small_lift = gp.score_moonshot(BATTER, GM, {5: rates_table(p=0.03, lg=0.02, n=100)})
check(c_small_lift["notable_signals"] == 0, "a lift of +0.01 (under 0.03) leaves notable_signals=0")

head("5. a realistic elite-slugger rate (verified live 2026-08-14: top real batter this "
     "season, 97 games, p_hat=0.1062, league_p=0.0215) still produces a genuinely low "
     "overall score -- this market is structurally a longshot, same as home_runs, and the "
     "board's dedicated select_deep_moonshots exists specifically because MIN_QUALITY_SCORE "
     "would otherwise exclude every real candidate here")

c_real = gp.score_moonshot(BATTER, GM, {5: rates_table(p=0.1062, lg=0.0215, n=97)})
check(c_real["score"] < gp.MIN_QUALITY_SCORE,
      "even the best real rate found live this season scores well under MIN_QUALITY_SCORE "
      "-- confirms this market needs its own selection path, not the general quality gate",
      f"score={c_real['score']} MIN_QUALITY_SCORE={gp.MIN_QUALITY_SCORE}")

head("6. why explains the real rate in plain language")

check("420+ ft" in c_real["why"][0] and "10.6%" in c_real["why"][0],
      "the why string states the real threshold and percentage", f"got {c_real['why']}")

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

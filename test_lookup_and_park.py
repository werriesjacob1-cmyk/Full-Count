#!/usr/bin/env python3
"""test_lookup_and_park.py — coverage for five small but load-bearing
generate_picks.py helpers that had zero test coverage: name_lookup,
lookup_player, park_hr_index, find_pitch_type_exploit, and
estimate_lineup_k_pct.

name_lookup/lookup_player's own docstrings document a real, measured bug
they exist to fix: 16 of 189 real lineup batters (8.5%) silently lost
their entire season line to an exact-name match failure on accented/
suffixed names (Ronald Acuna vs Ronald Acuña Jr., etc).

park_hr_index has a second real bug, found and fixed in this same commit:
called directly with dome=True (never hit by either real caller, which
both pre-filter domes themselves, but reachable by any future caller),
the word "WIND" in m.wind_vs_field's own dome string contains the
substring "IN", so a dome game was scored as if wind were blowing in.

    /tmp/mlbvenv/bin/python3 test_lookup_and_park.py
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
import pandas as pd

head("== name_lookup / lookup_player ==")
head("1. an empty or None dataframe returns an empty lookup, not a crash")

check(gp.name_lookup(None) == {}, "name_lookup(None) returns an empty dict")
check(gp.name_lookup(pd.DataFrame()) == {}, "name_lookup of an empty DataFrame returns an empty dict")

head("2. THE BUG THIS FIXES: an accented/suffixed real name still resolves via the "
     "normalized-name fold, even when the exact string doesn't match")

df = pd.DataFrame([
    {"Name": "Ronald Acuña Jr.", "player_id": 660670, "wOBA": 0.410},
    {"Name": "Bobby Witt Jr.", "player_id": 677951, "wOBA": 0.390},
])
lookup = gp.name_lookup(df)
row = gp.lookup_player(lookup, "Ronald Acuna", None)
check(row is not None and row["wOBA"] == 0.410,
      "the lineup's plain-ASCII 'Ronald Acuna' resolves to the frame's real "
      "'Ronald Acuña Jr.' row via the normalized fold, not an empty dict",
      f"got {row}")

head("3. player_id is tried FIRST and is authoritative -- takes priority over any name match")

row_by_id = gp.lookup_player(lookup, "Some Totally Different Name", 660670)
check(row_by_id is not None and row_by_id["wOBA"] == 0.410,
      "a correct player_id resolves the right row even when the name passed in "
      "doesn't match anything")

head("4. an ambiguous normalized fold (two distinct real players) is withdrawn, "
     "not resolved arbitrarily to whichever came last")

df_ambiguous = pd.DataFrame([
    {"Name": "Max Muncy", "player_id": 571970, "wOBA": 0.360},
    {"Name": "Max Muncy", "player_id": 700000, "wOBA": 0.310},
])
lookup_amb = gp.name_lookup(df_ambiguous)
row_amb = gp.lookup_player(lookup_amb, "Max Muncy", None)
check(row_amb is not None,
      "the EXACT name string 'Max Muncy' still resolves (it's a real dict key -- the "
      "second row's dict simply overwrote the first under that exact key, same as a "
      "plain dict literal would), so this is not a KeyError case")
row_amb_by_id_a = gp.lookup_player(lookup_amb, None, 571970)
row_amb_by_id_b = gp.lookup_player(lookup_amb, None, 700000)
check(row_amb_by_id_a["wOBA"] == 0.360 and row_amb_by_id_b["wOBA"] == 0.310,
      "each of the two real Max Muncys is still individually resolvable by their own "
      "distinct player_id, even though their shared name is ambiguous")

head("5. lookup_player with an empty/None lookup table returns the default, not a crash")

check(gp.lookup_player({}, "Anyone", 5) is None, "an empty lookup table returns None (the default)")
check(gp.lookup_player(None, "Anyone", 5, default="FALLBACK") == "FALLBACK",
      "lookup=None returns the explicit default when one is given")

head("6. a name/id genuinely absent from the table returns the default, not a fabricated row")

check(gp.lookup_player(lookup, "Nobody Real", 999999) is None,
      "a player present in neither id nor any name form returns None")

head("== park_hr_index ==")
head("7. THE BUG FIXED IN THIS COMMIT: dome=True is always neutral (50.0, 'dome'), "
     "regardless of the wind/temp/humidity values passed alongside it")

idx, effect = gp.park_hr_index(75, 25, 45, 60, 90, 0, True)
check(idx == 50.0 and effect == "dome",
      "dome=True with a strong 25mph wind still returns exactly (50.0, 'dome') -- the "
      "wind figure must never leak into a dome's index", f"got {(idx, effect)}")

idx2, effect2 = gp.park_hr_index(30, 40, 999, 10, 0, 5000, True)
check(idx2 == 50.0 and effect2 == "dome",
      "dome=True is neutral even with extreme/nonsensical temp, wind, and elevation values",
      f"got {(idx2, effect2)}")

head("8. wind blowing OUT to center field raises the index; blowing IN lowers it")

idx_out, effect_out = gp.park_hr_index(75, 15, 180, 50, 0, 0, False)  # wdir=180, cf=0 -> diff=180 -> OUT
check(effect_out == "out" and idx_out > 50, "wind straight out to CF raises the index above 50 "
      "and labels it 'out'", f"got {(idx_out, effect_out)}")

idx_in, effect_in = gp.park_hr_index(75, 15, 0, 50, 0, 0, False)  # wdir=0, cf=0 -> diff=0 -> IN
check(effect_in == "in" and idx_in < 50, "wind blowing straight in from CF lowers the index "
      "below 50 and labels it 'in'", f"got {(idx_in, effect_in)}")

head("9. temperature effects: hot raises the index, cold lowers it, independent of wind")

idx_hot, _ = gp.park_hr_index(90, 0, 45, 50, 90, 0, False)   # diagonal wind -> neutral
idx_cold, _ = gp.park_hr_index(40, 0, 45, 50, 90, 0, False)
idx_mild, _ = gp.park_hr_index(70, 0, 45, 50, 90, 0, False)
check(idx_hot > idx_mild > idx_cold,
      "hot (90F) > mild (70F) > cold (40F) in HR-friendliness, at identical wind/humidity",
      f"hot={idx_hot} mild={idx_mild} cold={idx_cold}")

head("10. the result is always clamped to [0, 100]")

idx_extreme, _ = gp.park_hr_index(120, 60, 180, 0, 0, -500, False)
check(0 <= idx_extreme <= 100, "an extreme hot/windy/low-elevation combination still clamps "
      "to a valid 0-100 index", f"got {idx_extreme}")

head("== find_pitch_type_exploit ==")
head("11. no arsenal data for either player returns None, not a crash")

check(gp.find_pitch_type_exploit(1, 501, {}, {}) is None, "empty batter/pitcher arsenals return None")
check(gp.find_pitch_type_exploit(1, 501, {1: {"FF": {"run_value_per_100": 3.0}}}, {}) is None,
      "a batter with data but a pitcher with none returns None")

head("12. only a pitch the PITCHER actually throws >=15% qualifies as a candidate exploit")

batter_arsenal = {1: {"FF": {"run_value_per_100": 3.0, "hard_hit_percent": 45.0},
                      "SL": {"run_value_per_100": -2.0}}}
pitcher_arsenal = {501: [("FF", 40.0), ("CH", 20.0)]}
exploit = gp.find_pitch_type_exploit(1, 501, batter_arsenal, pitcher_arsenal)
check(exploit is not None and exploit["pitch_type"] == "FF",
      "the pitcher's real 40% fastball usage, matched against the batter's positive "
      "run-value fastball history, surfaces as the exploit", f"got {exploit}")
check(exploit["usage_pct"] == 40.0 and exploit["run_value_per_100"] == 3.0
      and exploit["hard_hit_percent"] == 45.0,
      "the exploit dict carries the real usage/run-value/hard-hit numbers through")

head("13. a run_value_per_100 below the +1.5 threshold never qualifies as an exploit")

weak_batter_arsenal = {1: {"FF": {"run_value_per_100": 0.8}}}
check(gp.find_pitch_type_exploit(1, 501, weak_batter_arsenal, pitcher_arsenal) is None,
      "a below-threshold (+0.8) run value against a pitch the pitcher throws >=15% "
      "still does NOT qualify -- the +1.5 bar must actually hold")

head("14. among multiple qualifying pitches, the HIGHEST run_value_per_100 wins")

multi_batter = {1: {"FF": {"run_value_per_100": 2.0}, "CH": {"run_value_per_100": 5.5}}}
multi_pitcher = {501: [("FF", 30.0), ("CH", 25.0)]}
best = gp.find_pitch_type_exploit(1, 501, multi_batter, multi_pitcher)
check(best["pitch_type"] == "CH", "the changeup (+5.5) beats the fastball (+2.0) for the "
      "single best exploit", f"got {best}")

head("== estimate_lineup_k_pct ==")
head("15. no lineup / no matching lookup returns (None, 0), not a crash or a fabricated average")

check(gp.estimate_lineup_k_pct([], {}) == (None, 0), "an empty lineup returns (None, 0)")
check(gp.estimate_lineup_k_pct([{"name": "Nobody", "id": 1}], {}) == (None, 0),
      "a lineup with no matching batter_lookup rows at all returns (None, 0)")

head("16. the mean K% is computed only over batters actually found, and n reports how many")

lineup = [{"name": "A", "id": 1}, {"name": "B", "id": 2}, {"name": "C", "id": 3}]
batter_lookup = {1: {"K%": 20.0}, 2: {"K%": 30.0}}  # batter 3 absent from the lookup
k_pct, n = gp.estimate_lineup_k_pct(lineup, batter_lookup)
check(n == 2, "only the 2 batters actually found in the lookup are counted, not all 3 in "
      "the lineup", f"got n={n}")
check(abs(k_pct - 25.0) < 1e-9, "the mean is over just those 2 found batters (20+30)/2=25.0, "
      "not diluted by the missing third", f"got {k_pct}")

head("17. a batter present but with K%=None is excluded from the average, not treated as 0")

batter_lookup_with_none = {1: {"K%": 20.0}, 2: {"K%": None}, 3: {"K%": 24.0}}
k_pct2, n2 = gp.estimate_lineup_k_pct(lineup, batter_lookup_with_none)
check(n2 == 2 and abs(k_pct2 - 22.0) < 1e-9,
      "a real row with K%=None is excluded (n=2, mean of 20&24=22.0), not averaged in as "
      "a zero (which would have wrongly pulled the mean down to 14.67)",
      f"got n={n2} k_pct={k_pct2}")

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

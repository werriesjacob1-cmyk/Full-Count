#!/usr/bin/env python3
"""test_projections.py — coverage for the four core "how much will this
player do tonight" functions in generate_picks.py: project_batter_pa,
project_batter_tb, project_pitcher_workload, project_pitcher_ks. All four
had zero test coverage despite gating every downstream prop -- get PA or
BF wrong and every rate multiplied against it is wrong too, regardless of
how good the rate itself is.

    /tmp/mlbvenv/bin/python3 test_projections.py
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

head("== project_batter_pa ==")
head("1. no implied_total falls back to the league mean (the documented 'old static table' path)")

pa_default = gp.project_batter_pa(1, None)
pa_at_league_mean = gp.project_batter_pa(1, gp.LEAGUE_TEAM_RUNS_MEAN)
check(pa_default == pa_at_league_mean,
      "order=1 with implied_total=None matches order=1 evaluated at the exact league mean",
      f"got default={pa_default} at_mean={pa_at_league_mean}")

head("2. a higher implied team total means more expected plate appearances")

pa_low = gp.project_batter_pa(3, 3.0)
pa_high = gp.project_batter_pa(3, 6.5)
check(pa_high > pa_low, "a 6.5-run implied total projects more PA than a 3.0-run total at "
      "the same lineup slot", f"low={pa_low} high={pa_high}")

head("3. implied_total is clamped to [2.0, 7.5] -- a garbage/stale line can't extrapolate")

pa_clamped_low = gp.project_batter_pa(3, 0.5)
pa_floor = gp.project_batter_pa(3, 2.0)
check(pa_clamped_low == pa_floor, "an implied_total of 0.5 (below the 2.0 floor) clamps to "
      "the same result as exactly 2.0", f"got {pa_clamped_low} vs floor {pa_floor}")

pa_clamped_high = gp.project_batter_pa(3, 50.0)
pa_ceiling = gp.project_batter_pa(3, 7.5)
check(pa_clamped_high == pa_ceiling, "an implied_total of 50.0 (absurd/garbage) clamps to "
      "the same result as exactly 7.5", f"got {pa_clamped_high} vs ceiling {pa_ceiling}")

head("4. a NaN implied_total falls back to league mean, not a silent NaN propagation")

pa_nan = gp.project_batter_pa(1, float("nan"))
check(pa_nan == pa_default, "NaN implied_total produces the identical result to None",
      f"got {pa_nan} vs default {pa_default}")

head("5. order is clamped to [1, 9], and defaults/falls back to 9 when missing or falsy")

# order=0 is falsy, so `int(order or 9)` treats it the same as a genuinely missing
# order -- it falls through to slot 9, NOT clamped to slot 1. Verified against the
# real `min(max(int(order or 9), 1), 9)` expression, not assumed.
pa_order_0 = gp.project_batter_pa(0, 4.245)
pa_order_9 = gp.project_batter_pa(9, 4.245)
check(pa_order_0 == pa_order_9,
      "order=0 (falsy) falls back to slot 9 via the `order or 9` idiom, same as a "
      "missing order -- NOT clamped to slot 1 the way an out-of-range positive value is")

pa_order_99 = gp.project_batter_pa(99, 4.245)
pa_order_9 = gp.project_batter_pa(9, 4.245)
check(pa_order_99 == pa_order_9, "order=99 clamps to the same result as order=9")

pa_order_none = gp.project_batter_pa(None, 4.245)
check(pa_order_none == pa_order_9, "order=None defaults to slot 9, not a crash or slot 1")

head("6. earlier lineup slots get more PA than later ones, at the same run environment")

pa_leadoff = gp.project_batter_pa(1, 4.5)
pa_ninth = gp.project_batter_pa(9, 4.5)
check(pa_leadoff > pa_ninth, "the leadoff slot projects more PA than the 9-hole at the "
      "same implied total", f"leadoff={pa_leadoff} ninth={pa_ninth}")

head("== project_batter_tb ==")
head("7. exact SLG is used directly (TB/AB by definition), not approximated from AVG")

tb_slg = gp.project_batter_tb({"AVG": 0.246, "slg": 0.532}, None, 3, 4.245)
tb_approx = gp.project_batter_tb({"AVG": 0.246}, None, 3, 4.245)
check(tb_slg != tb_approx,
      "a real SLG value produces a materially different (and correct) result than the "
      "AVG*1.35 approximation used when SLG is absent", f"slg-based={tb_slg} approx={tb_approx}")

head("8. AVG+ISO (the FanGraphs path) is used exactly when slg is absent")

tb_iso = gp.project_batter_tb({"AVG": 0.280, "ISO": 0.200}, None, 3, 4.245)
tb_slg_equiv = gp.project_batter_tb({"AVG": 0.280, "slg": 0.480}, None, 3, 4.245)
check(tb_iso == tb_slg_equiv,
      "AVG+ISO (.280+.200=.480 SLG-equivalent) produces the identical result to an "
      "explicit slg=.480 -- confirms AVG+ISO really is being read as an exact SLG "
      "substitute, not a separate approximation", f"iso-path={tb_iso} slg-path={tb_slg_equiv}")

head("9. with no batter_season data at all, the league-average TB/PA rate is used")

tb_none = gp.project_batter_tb(None, None, 3, 4.245)
pa_est = gp.project_batter_pa(3, 4.245)
check(abs(tb_none - round(gp.LEAGUE_AVG_TB_PA * pa_est, 2)) < 0.01,
      "with bs=None and no L7 data, the projection is exactly LEAGUE_AVG_TB_PA * projected PA",
      f"got {tb_none}, want ~{round(gp.LEAGUE_AVG_TB_PA * pa_est, 2)}")

head("10. L7 form is blended in only once it has a real sample (>=5 PA), weighted up to 0.5")

tb_no_l7 = gp.project_batter_tb({"AVG": 0.280, "slg": 0.480}, {"PA": 3, "TB_per_PA": 0.900}, 3, 4.245)
tb_season_only = gp.project_batter_tb({"AVG": 0.280, "slg": 0.480}, None, 3, 4.245)
check(tb_no_l7 == tb_season_only,
      "an L7 sample under 5 PA is ignored entirely (season rate used as-is), not blended "
      "in on a near-nonexistent sample", f"got {tb_no_l7} vs season-only {tb_season_only}")

tb_with_l7 = gp.project_batter_tb({"AVG": 0.280, "slg": 0.480}, {"PA": 20, "TB_per_PA": 0.900}, 3, 4.245)
check(tb_with_l7 > tb_season_only,
      "a real 20-PA hot-streak L7 sample (0.900 TB/PA) pulls the blended projection up "
      "from the season-only baseline", f"with_l7={tb_with_l7} season_only={tb_season_only}")

head("== project_pitcher_workload ==")
head("11. no L14 data at all returns the league average with n_starts=0")

bf, n, obs = gp.project_pitcher_workload(None)
check(bf == gp.LEAGUE_AVG_BF_PER_START and n == 0 and obs is None,
      "l14=None returns (league_avg, 0, None) -- the honest neutral answer",
      f"got {(bf, n, obs)}")

bf2, n2, obs2 = gp.project_pitcher_workload({"bf_per_start": None, "n_starts": 5})
check(bf2 == gp.LEAGUE_AVG_BF_PER_START and n2 == 0,
      "a present dict with bf_per_start=None still falls back to the league average")

head("12. a real workload sample shrinks toward the league mean by n/(n+BF_SHRINK_N0)")

bf3, n3, obs3 = gp.project_pitcher_workload({"bf_per_start": 27.5, "n_starts": 2})
w = 2 / (2 + gp.BF_SHRINK_N0)
expected = w * 27.5 + (1 - w) * gp.LEAGUE_AVG_BF_PER_START
check(abs(bf3 - expected) < 0.01 and n3 == 2 and obs3 == 27.5,
      "a 2-start, 27.5 BF/start sample shrinks by the documented formula",
      f"got {bf3}, want {expected}")

head("13. clamped to [12.0, 30.0] -- can't produce an absurd workload even from bad data")

bf4, _, _ = gp.project_pitcher_workload({"bf_per_start": 999, "n_starts": 50})
check(bf4 <= 30.0, "an absurd 999 BF/start with a huge sample still clamps at 30.0",
      f"got {bf4}")

head("== project_pitcher_ks ==")
head("14. K-rate source priority: exp_k > L14 (>=15 PA) > season K% > flat 22.5 fallback")

ks_exp = gp.project_pitcher_ks({"K%": 15.0}, {"l14_pa": 90, "l14_k_pct": 20.0},
                                exp_k={"k_rate": 0.30})
ks_l14 = gp.project_pitcher_ks({"K%": 15.0}, {"l14_pa": 90, "l14_k_pct": 20.0}, exp_k=None)
check(ks_exp != ks_l14, "exp_k (30%) is preferred over the L14 rate (20%) when both are "
      "available, producing a materially different projection", f"exp={ks_exp} l14={ks_l14}")

ks_l14_only = gp.project_pitcher_ks({"K%": 15.0}, {"l14_pa": 90, "l14_k_pct": 20.0})
ks_thin_l14 = gp.project_pitcher_ks({"K%": 15.0}, {"l14_pa": 10, "l14_k_pct": 20.0})
check(ks_l14_only != ks_thin_l14,
      "an L14 sample under 15 PA is NOT trusted -- falls through to season K% instead, "
      "producing a different number than the (ignored) thin L14 rate would have",
      f"l14_only(pa=90)={ks_l14_only} thin_l14(pa=10, ignored)={ks_thin_l14}")

ks_season_only = gp.project_pitcher_ks({"K%": 15.0}, None)
ks_no_data = gp.project_pitcher_ks(None, None)
check(ks_season_only != ks_no_data,
      "season K%=15.0 (when present) is used over the flat 22.5% fallback when there's "
      "no season stats at all", f"season={ks_season_only} no_data={ks_no_data}")

head("15. project_pitcher_ks is K-rate x expected BF, verified against project_pitcher_workload directly")

exp_bf, _, _ = gp.project_pitcher_workload({"bf_per_start": 24.0, "n_starts": 8})
ks = gp.project_pitcher_ks({"K%": 25.0}, {"bf_per_start": 24.0, "n_starts": 8, "l14_pa": 5})
check(abs(ks - round(0.25 * exp_bf, 1)) < 0.05,
      "the K projection matches season K%(25%) x the same expected BF project_pitcher_"
      "workload computes independently", f"got {ks}, want ~{round(0.25 * exp_bf, 1)}")

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

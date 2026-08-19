#!/usr/bin/env python3
"""test_singles_modelled_probability.py — coverage for the 2026-08-19
accuracy fix: singles gets a real modelled probability component
(prop_probability.p_at_least_singles), wired into _batter_options'
"singles" family the same way home_runs already uses p_at_least_home_runs.

WHY THIS FILE EXISTS, SPECIFICALLY. Large-scale calibration measurement
(694 real graded singles rows across 130 real dates, see
backtest/calibration_audit.py) found singles running a real, reportable
+8.9 point underconfidence gap (predicted 35.9%, actual 44.8%) -- the ONLY
market among hits/total_bases/home_runs/runs/rbis/hits_runs_rbis/doubles/
triples/stolen_base/etc. that showed one. The root cause traced to
architecture, not tuning: singles was the one batter hit-type market with
NO modelled component at all (fn=None in generate_picks.py's `families`
list) despite having exactly the same structural basis as home_runs -- a
single is "exactly 1 base" in the SAME per-PA outcome distribution
(pa_dist[1]) that home_runs already reads at pa_dist[4].

This file exists to make three things true FOREVER, not just today:
  1. p_at_least_singles is mathematically correct (independently checked
     against scipy.stats.binom, not just internally self-consistent).
  2. The modelled component is ACTUALLY wired into _batter_options' real
     probability pipeline, not just defined and unused (section 3) --
     with a direct regression guard so a future refactor cannot silently
     revert singles back to the empirical-only path (section 3's last
     check) without a test failing.
  3. Doubles, triples, hits, total_bases, and home_runs are demonstrably
     UNCHANGED by this addition -- the biggest risk in wiring a new lambda
     into a shared families list is an accidental cross-market effect via
     a mutated dict, shared closure state, or an off-by-one in which slot
     a market's own fn lands in.

    /tmp/mlbvenv/bin/python3 test_singles_modelled_probability.py
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


import prop_probability as pp
import generate_picks as gp

try:
    from scipy.stats import binom as _scipy_binom
    HAVE_SCIPY = True
except ImportError:
    HAVE_SCIPY = False


# ══════════════════════════════════════════════════════════════════════════
#  1. p_at_least_singles: mathematical correctness
# ══════════════════════════════════════════════════════════════════════════

head("1a. p_at_least_singles reads pa_dist[1] (the single-specific per-PA "
     "rate) -- direct analogue of p_at_least_home_runs reading pa_dist[4], "
     "verified by construction: changing ONLY pa_dist[1] changes the result, "
     "changing every OTHER key does not")

dist_a = {0: 0.70, 1: 0.15, 2: 0.08, 3: 0.02, 4: 0.05}
dist_b = dict(dist_a); dist_b[1] = 0.25  # only the singles component moved
dist_c = dict(dist_a); dist_c[4] = 0.20  # only the HR component moved (singles untouched)

p_a = pp.p_at_least_singles(1, dist_a, 4.0)
p_b = pp.p_at_least_singles(1, dist_b, 4.0)
p_c = pp.p_at_least_singles(1, dist_c, 4.0)
check(p_b > p_a, "raising ONLY pa_dist[1] raises the singles probability",
     f"p_a={p_a!r} p_b={p_b!r}")
check(abs(p_c - p_a) < 1e-9, "changing pa_dist[4] (home runs) leaves the "
     "singles probability UNCHANGED -- it reads only its own bucket",
     f"p_a={p_a!r} p_c={p_c!r}")

head("1b. p_at_least_singles matches p_at_least_home_runs's own structure "
     "EXACTLY under a key-swap -- same n_pa, same threshold, singles-rate-"
     "as-home-run-rate must produce the identical number")

dist_swap = {0: 0.70, 1: 0.05, 2: 0.08, 3: 0.02, 4: 0.15}
p_singles_swapped = pp.p_at_least_singles(1, {0: 0.70, 1: 0.15, 2: 0.08, 3: 0.02, 4: 0.05}, 4.0)
p_hr_normal = pp.p_at_least_home_runs(1, dist_swap, 4.0)
check(abs(p_singles_swapped - p_hr_normal) < 1e-9,
     "singles reading a 0.15 rate at key 1 == home_runs reading a 0.15 rate "
     "at key 4 -- proves this is the direct analogue, not a different "
     "construction", f"{p_singles_swapped!r} vs {p_hr_normal!r}")

if HAVE_SCIPY:
    head("1c. independent mathematical validation against scipy.stats.binom "
         "(a reference implementation this codebase does not itself use)")
    for n_pa, p1, k in [(4, 0.15, 1), (5, 0.30, 2), (3, 0.05, 1), (6, 0.40, 3)]:
        got = pp.p_at_least_singles(k, {0: 1 - p1, 1: p1}, n_pa)
        want = float(_scipy_binom.sf(k - 1, n_pa, p1))
        check(abs(got - want) < 1e-9,
             f"n_pa={n_pa} p1={p1} k={k}: matches scipy.stats.binom.sf",
             f"got={got!r} want={want!r}")
else:
    head("1c. SKIPPED -- scipy not available in this environment")

head("1d. edge cases: zero probability, zero PA, threshold above what's "
     "possible, fractional PA (opportunity aggregation)")

check(pp.p_at_least_singles(1, {0: 1.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}, 4.0) == 0.0,
     "zero singles rate -> zero probability of at least one, regardless of PA")
check(pp.p_at_least_singles(1, {0: 0.7, 1: 0.15, 2: 0.08, 3: 0.02, 4: 0.05}, 0.0) == 0.0,
     "zero plate appearances -> zero probability, regardless of rate")
check(pp.p_at_least_singles(1, {0: 0.0, 1: 1.0}, 1.0) == 1.0,
     "a certain single on the only PA -> probability 1.0")
check(pp.p_at_least_singles(10, {0: 0.7, 1: 0.15, 2: 0.08, 3: 0.02, 4: 0.05}, 4.0) == 0.0,
     "asking for 10+ singles in 4 PAs is impossible -> 0.0")
# Fractional PA: real batters get a projected_pa like 4.3, not an integer.
# _binom_at_least mixes between floor/ceil (see _mix_fractional) -- verify
# the fractional result sits strictly between the two integer neighbors,
# the sanity property any sane mixture must have for a monotonically
# increasing-in-n function like "at least 1".
p_int4 = pp.p_at_least_singles(1, dist_a, 4)
p_int5 = pp.p_at_least_singles(1, dist_a, 5)
p_frac = pp.p_at_least_singles(1, dist_a, 4.5)
check(p_int4 < p_frac < p_int5, "a fractional 4.5 PA falls strictly between "
     "the integer 4-PA and 5-PA results (opportunity aggregation is sane, "
     "not just internally consistent)",
     f"p4={p_int4!r} p4.5={p_frac!r} p5={p_int5!r}")

head("1e. threshold <= 0 returns 1.0, matching _binom_at_least's own "
     "documented convention (shared with every other p_at_least_* here)")
check(pp.p_at_least_singles(0, dist_a, 4.0) == 1.0,
     "asking for 'at least 0' singles is certain, same convention as "
     "p_at_least_home_runs/hits/total_bases")


# ══════════════════════════════════════════════════════════════════════════
#  2. Wiring into _batter_options: the modelled component is ACTUALLY used
# ══════════════════════════════════════════════════════════════════════════

def make_comp(singles=0.16, doubles=0.05, triples=0.004, hr=0.045):
    return {"singles_rate": singles, "double_rate": doubles,
           "triple_rate": triples, "hr_rate": hr}


def make_emp(games=200, singles_p=0.30):
    # A realistic empirical table covering every family this file actually
    # asserts on -- without a real rate (or a real league fallback, not
    # supplied here) _batter_options skips a threshold ENTIRELY rather than
    # emitting it with prob=None (see its own "NO PROP GOES UNSCORED" /
    # true_league_rates-only comment), so a family missing here would make
    # a `next(...)` lookup below raise StopIteration for an unrelated
    # reason -- a fixture gap, not a real finding about the code under test.
    def rate(p):
        return {"p_hat": p, "p": p, "n": games, "hit": int(p * games)}
    return {"games": games, "rates": {
        "singles_1plus": rate(singles_p),
        "doubles_1plus": rate(0.12), "triples_1plus": rate(0.015),
        "hits_1plus": rate(0.55), "total_bases_2plus": rate(0.35),
        "home_runs_1plus": rate(0.10), "runs_1plus": rate(0.42),
    }}


head("2a. with a real PA distribution AND real PA count, singles now "
     "carries a real 'modelled' value in its option -- not None, as it "
     "always was before this fix")

opts = gp._batter_options({"projected_pa": 4.3}, make_comp(), make_emp())
singles_opt = next(o for o in opts if o["stat"] == "singles" and o["needs"] == 1)
check(singles_opt["modelled"] is not None,
     "singles option now carries a real modelled probability",
     f"got option: {singles_opt}")

head("2b. the modelled value is EXACTLY what p_at_least_singles computes "
     "from the SAME pa_dist/pa the option was built from -- not some other "
     "number, not the empirical rate mislabeled")

dist_expected = pp.pa_outcome_distribution(singles_rate=0.16, double_rate=0.05,
                                            triple_rate=0.004, hr_rate=0.045)
expected_modelled = round(pp.p_at_least_singles(1, dist_expected, 4.3), 4)
check(singles_opt["modelled"] == expected_modelled,
     "modelled field matches a direct p_at_least_singles call on the same inputs",
     f"got {singles_opt['modelled']!r}, want {expected_modelled!r}")

head("2c. basis reflects a REAL blend/shrink now, not the old 'empirical'-"
     "or-nothing choice -- proves the modelled term is actually feeding "
     "the final probability, not just decorating the option with an unused "
     "field")

check(singles_opt["basis"] in ("blended", "modelled_shrunk"),
     "singles basis is a real blend of empirical+modelled (or "
     "league-rate-shrunk-modelled), never bare 'modelled' or 'empirical' "
     "alone when both a real empirical rate AND a real dist/pa exist",
     f"got basis={singles_opt['basis']!r}")


# ══════════════════════════════════════════════════════════════════════════
#  3. Fallback: missing/unusable PA distribution -> the OLD empirical-only
#     path, honestly, never a fabricated modelled number
# ══════════════════════════════════════════════════════════════════════════

head("3a. comp is None (no Statcast composition data at all) -> singles "
     "falls back to empirical-only, modelled stays None -- the exact "
     "pre-fix behavior, preserved as the fallback")

opts_no_comp = gp._batter_options({"projected_pa": 4.3}, None, make_emp())
singles_no_comp = next(o for o in opts_no_comp if o["stat"] == "singles" and o["needs"] == 1)
check(singles_no_comp["modelled"] is None,
     "no comp -> no dist -> no modelled value for singles",
     f"got {singles_no_comp}")
check(singles_no_comp["basis"] == "empirical",
     "falls back to pure empirical basis, matching pre-fix behavior exactly",
     f"got basis={singles_no_comp['basis']!r}")

head("3b. projected_pa is missing/zero -> same fallback, even with a real "
     "comp available -- pa is required for ANY modelled binomial term, not "
     "singles-specific")

opts_no_pa = gp._batter_options({"projected_pa": None}, make_comp(), make_emp())
singles_no_pa = next(o for o in opts_no_pa if o["stat"] == "singles" and o["needs"] == 1)
check(singles_no_pa["modelled"] is None,
     "no projected_pa -> no modelled value for singles either",
     f"got {singles_no_pa}")

head("3c. REGRESSION GUARD: the singles family in _batter_options' families "
     "list actually HAS a real, callable fn when dist/pa are present -- this "
     "is the specific check that would fail if a future refactor silently "
     "reverted singles back to fn=None (the crude historical-only path)")

check(gp._batter_options({"projected_pa": 4.3}, make_comp(), make_emp())[0] is not None,
     "sanity: _batter_options still returns real options at all")
# The strongest possible guard: modelled MUST be populated whenever dist/pa
# both exist, for every threshold in the singles family. If a future edit
# reverts the families-list entry to (..., None), this becomes vacuously
# None and fails immediately -- exactly the silent-revert this test exists
# to catch.
singles_opts_all = [o for o in gp._batter_options(
    {"projected_pa": 4.3}, make_comp(), make_emp()) if o["stat"] == "singles"]
check(len(singles_opts_all) >= 1, "at least one singles threshold is offered")
check(all(o["modelled"] is not None for o in singles_opts_all),
     "EVERY singles option has a real modelled value when dist/pa exist -- "
     "would fail immediately if singles' fn were ever silently reverted to "
     "None", f"got: {singles_opts_all}")


# ══════════════════════════════════════════════════════════════════════════
#  4. No cross-market contamination: doubles, triples, hits, total_bases,
#     home_runs are demonstrably UNCHANGED
# ══════════════════════════════════════════════════════════════════════════

head("4a. doubles and triples remain empirical-only (fn=None) -- explicitly "
     "NOT extended to match singles, per the narrow authorized scope. Their "
     "modelled field must stay None even with a full, real dist/pa present")

opts_full = gp._batter_options({"projected_pa": 4.3}, make_comp(), make_emp())
doubles_opt = next(o for o in opts_full if o["stat"] == "doubles" and o["needs"] == 1)
triples_opt = next(o for o in opts_full if o["stat"] == "triples" and o["needs"] == 1)
check(doubles_opt["modelled"] is None,
     "doubles has NO modelled component, even now -- unevidenced, not "
     "extended", f"got {doubles_opt}")
check(triples_opt["modelled"] is None,
     "triples has NO modelled component, even now -- unevidenced, not "
     "extended", f"got {triples_opt}")

head("4b. hits/total_bases/home_runs' modelled values are IDENTICAL to what "
     "they were before this change (a direct value check against "
     "p_at_least_hits/total_bases/home_runs run independently) -- proves "
     "adding singles' lambda into the same families list did not perturb "
     "a neighboring entry")

dist_check = pp.pa_outcome_distribution(singles_rate=0.16, double_rate=0.05,
                                        triple_rate=0.004, hr_rate=0.045)
hits_opt = next(o for o in opts_full if o["stat"] == "hits" and o["needs"] == 1)
tb_opt = next(o for o in opts_full if o["stat"] == "total_bases" and o["needs"] == 2)
hr_opt = next(o for o in opts_full if o["stat"] == "home_runs" and o["needs"] == 1)
check(hits_opt["modelled"] == round(pp.p_at_least_hits(1, dist_check, 4.3), 4),
     "hits' modelled value is unchanged, computed independently and matches")
check(tb_opt["modelled"] == round(pp.p_at_least_total_bases(2, dist_check, 4.3), 4),
     "total_bases' modelled value is unchanged")
check(hr_opt["modelled"] == round(pp.p_at_least_home_runs(1, dist_check, 4.3), 4),
     "home_runs' modelled value is unchanged")

head("4c. an UNRELATED market (runs, which never had and still has no "
     "modelled component at all) produces an identical option whether or "
     "not comp/dist exists -- proves this change has zero footprint outside "
     "the batter hit-type families")

emp_with_runs = dict(make_emp())
emp_with_runs["rates"] = dict(emp_with_runs["rates"])
emp_with_runs["rates"]["runs_1plus"] = {"p_hat": 0.42, "p": 0.42, "n": 200, "hit": 84}
opts_with_dist = gp._batter_options({"projected_pa": 4.3}, make_comp(), emp_with_runs)
opts_without_dist = gp._batter_options({"projected_pa": 4.3}, None, emp_with_runs)
runs_with = next(o for o in opts_with_dist if o["stat"] == "runs" and o["needs"] == 1)
runs_without = next(o for o in opts_without_dist if o["stat"] == "runs" and o["needs"] == 1)
check(runs_with["prob"] == runs_without["prob"] and runs_with["basis"] == runs_without["basis"],
     "runs' option is byte-identical whether or not a PA distribution "
     "exists -- this change has no reach into runs/rbis/hits_runs_rbis",
     f"with={runs_with} without={runs_without}")


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

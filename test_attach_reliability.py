#!/usr/bin/env python3
"""test_attach_reliability.py — coverage for generate_picks.attach_
reliability() and its helper _wilson_interval(). Had zero test coverage
despite its own source comments documenting THREE separate historical
instances of the same bug class: a stat-specific sample size silently
looked up in the wrong table (emp_batters for a pitcher-side stat, or a
brand-new market with no table at all) and reported as 0 real evidence.
This locks in the current, already-fixed routing for every stat family so
a fourth instance can't slip back in unnoticed.

    /tmp/mlbvenv/bin/python3 test_attach_reliability.py
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


def cand(stat, player_id=5, needs=None, **over):
    c = {"player_id": player_id, "projection": {"stat": stat, "needs": needs}, "signals": {}}
    c.update(over)
    return c


head("1. _wilson_interval: known edge cases")

lo, hi = gp._wilson_interval(0, 0)
check((lo, hi) == (0.0, 1.0), "n=0 returns the widest possible interval (0.0, 1.0), not a "
      "division by zero", f"got {(lo, hi)}")

lo, hi = gp._wilson_interval(50, 100)
check(abs((lo + hi) / 2 - 0.5) < 0.02, "a 50/100 sample centers close to 0.5", f"got {(lo, hi)}")
check(lo < 0.5 < hi, "the true rate 0.5 falls inside its own interval")

lo, hi = gp._wilson_interval(0, 20)
check(lo == 0.0 and hi > 0.0, "0 hits out of 20 doesn't collapse to a zero-width interval "
      "at zero (the whole reason Wilson is used over the normal approximation)", f"got {(lo, hi)}")

lo, hi = gp._wilson_interval(20, 20)
check(hi == 1.0 and lo < 1.0, "20/20 doesn't collapse to a zero-width interval at one either",
      f"got {(lo, hi)}")

head("2. generic batter stat: n comes from emp_batters, keyed by player_id")

c = cand("hits", player_id=5)
out = gp.attach_reliability([c], emp_batters={5: {"games": 62}}, emp_pitchers={})
check(out[0]["sample_n"] == 62, "a generic batter stat's n comes from emp_batters[pid]['games']",
      f"got {out[0]['sample_n']}")
check(out[0]["reliability"] == "B", "62 games lands in tier B per RELIABILITY_TIERS (45<=n<80)",
      f"got {out[0]['reliability']}")

head("3. strikeouts is the one stat routed to emp_pitchers instead of emp_batters")

c = cand("strikeouts", player_id=501)
out = gp.attach_reliability([c], emp_batters={501: {"games": 999}}, emp_pitchers={501: {"starts": 18}})
check(out[0]["sample_n"] == 18, "strikeouts pulls n from emp_pitchers[pid]['starts'], "
      "NOT the (wrong-player-type) emp_batters entry even though one exists under the same id",
      f"got {out[0]['sample_n']}")

head("4. hard_hit_105/hard_hit_110/pitcher_outs/combined_strikeouts: n from c['sample_n'] directly")

for stat in ("hard_hit_105", "hard_hit_110", "pitcher_outs", "combined_strikeouts"):
    c = cand(stat, player_id=7, sample_n=44)
    out = gp.attach_reliability([c], emp_batters={7: {"games": 999}}, emp_pitchers={7: {"starts": 999}})
    check(out[0]["sample_n"] == 44, f"{stat}: n comes from the candidate's own sample_n (44), "
          "not emp_batters or emp_pitchers even though both have an entry for this id",
          f"got {out[0]['sample_n']}")

head("5. combined_strikeouts / hard_hit / pitcher_outs with no sample_n at all defaults to 0 honestly")

c = cand("pitcher_outs", player_id=7)  # no sample_n key
out = gp.attach_reliability([c], emp_batters={}, emp_pitchers={})
check(out[0]["sample_n"] == 0, "a missing sample_n reports 0 real evidence rather than crashing "
      "or fabricating a number", f"got {out[0]['sample_n']}")
check(out[0]["reliability"] == "D", "0 sample size lands in the bottom tier D")

head("6. first_inning_run/nrfi_combined: n from signals['fi_n_starts']")

c = cand("first_inning_run", player_id=9, signals={"fi_n_starts": 30})
out = gp.attach_reliability([c], emp_batters={}, emp_pitchers={})
check(out[0]["sample_n"] == 30, "first_inning_run's n comes from signals.fi_n_starts",
      f"got {out[0]['sample_n']}")

c = cand("nrfi_combined", player_id=9, signals={"fi_n_starts": 12})
out = gp.attach_reliability([c], emp_batters={}, emp_pitchers={})
check(out[0]["sample_n"] == 12, "nrfi_combined routes through the same fi_n_starts path as "
      "first_inning_run", f"got {out[0]['sample_n']}")

head("7. RELIABILITY_TIERS boundary assignment (80/45/25/0)")

for n, want_grade in ((150, "A"), (80, "A"), (79, "B"), (45, "B"), (44, "C"),
                       (25, "C"), (24, "D"), (0, "D")):
    c = cand("hits", player_id=1)
    out = gp.attach_reliability([c], emp_batters={1: {"games": n}}, emp_pitchers={})
    check(out[0]["reliability"] == want_grade, f"n={n} grades as {want_grade!r}",
          f"got {out[0]['reliability']!r}")
    check(out[0]["reliability_note"], f"n={n} always carries a non-empty reliability_note")

head("8. player entirely absent from both empirical tables doesn't crash")

c = cand("hits", player_id=999999)
out = gp.attach_reliability([c], emp_batters={}, emp_pitchers={})
check(out[0]["sample_n"] == 0 and out[0]["reliability"] == "D",
      "an unknown player_id defaults to n=0, tier D, rather than a KeyError")

head("9. prob_ci is only attached when a rate is actually found (needs + rates present)")

c = cand("hits", player_id=1, needs=2)
out = gp.attach_reliability(
    [c], emp_batters={1: {"games": 100, "rates": {"hits_2plus": {"hit": 40, "n": 100}}}},
    emp_pitchers={})
check("prob_ci" in out[0], "a candidate with needs=2 and a matching rate entry gets a prob_ci",
      f"got keys={sorted(out[0].keys())}")
check(len(out[0]["prob_ci"]) == 2 and out[0]["prob_ci"][0] <= out[0]["prob_ci"][1],
      "prob_ci is a valid [lo, hi] pair", f"got {out[0]['prob_ci']}")

c2 = cand("hits", player_id=2, needs=None)
out2 = gp.attach_reliability([c2], emp_batters={2: {"games": 100}}, emp_pitchers={})
check("prob_ci" not in out2[0], "a candidate with no 'needs' (e.g. a continuous-value stat) "
      "gets no prob_ci rather than a fabricated one", f"got keys={sorted(out2[0].keys())}")

head("10. attach_reliability mutates and returns the same list (in place, matching call sites)")

cands = [cand("hits", player_id=1)]
out = gp.attach_reliability(cands, emp_batters={1: {"games": 10}}, emp_pitchers={})
check(out is cands, "the function returns the same list object it was given (in-place mutation), "
      "matching how build_candidates()/main() chain calls on the result")

head("11. an empty candidate list returns cleanly")

check(gp.attach_reliability([], {}, {}) == [], "an empty candidate list returns an empty list")

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

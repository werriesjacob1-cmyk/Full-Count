#!/usr/bin/env python3
"""test_pricing_freshness_warning.py — direct coverage for
generate_picks.pricing_freshness_warning(), the defensive check added
2026-08-13 after a real bug: market pricing (attach_market_prices) ran
AFTER rank_for_board/select_main_board had already read price_clears,
so every market except the two that price themselves early
(pitcher_outs, combined_strikeouts) was structurally locked out of the
main board for ~3 hours, regardless of real edge -- confirmed live via
Brandon Marsh's Over 0.5 Singles sitting at price_clears=True,
market_edge=+0.019 in "best_of_category" while the main board shipped
only 1 pick. This function exists so a regression of that exact bug
class prints a loud warning instead of silently shipping a suspiciously
thin board again.

    /tmp/mlbvenv/bin/python3 test_pricing_freshness_warning.py
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


def cand(stat, clears):
    return {"name": "Test Player", "projection": {"stat": stat}, "price_clears": clears}


head("1. THE BUG ITSELF, reproduced: every general-market candidate has "
     "price_clears=None (pricing never ran / ran too late) -- must warn")

pool = [cand("hits", None), cand("total_bases", None), cand("home_runs", None)]
warning = gp.pricing_freshness_warning(pool)
check(warning is not None, "a pool where every general-market candidate is unpriced triggers a warning",
      f"got {warning!r}")

head("2. healthy case: at least one general-market candidate has a real "
     "price_clears value (True or False, either is fine -- just not None "
     "for ALL of them) -- no warning")

pool2 = [cand("hits", True), cand("total_bases", False), cand("home_runs", None)]
warning2 = gp.pricing_freshness_warning(pool2)
check(warning2 is None, "a mixed pool (some priced, some not) does not trigger a false alarm",
      f"got {warning2!r}")

head("3. a pool of ONLY early-priced markets (pitcher_outs/combined_strikeouts) "
     "is NOT suspicious -- nothing else to check pricing freshness against, "
     "and this is a legitimate thin-slate shape, not a bug")

pool3 = [cand("pitcher_outs", True), cand("combined_strikeouts", None)]
warning3 = gp.pricing_freshness_warning(pool3)
check(warning3 is None,
      "a pool with no general-market candidates at all doesn't false-alarm",
      f"got {warning3!r}")

head("4. early-priced markets with price_clears=None don't count toward the "
     "general-market check -- only non-early-priced markets matter")

pool4 = [cand("pitcher_outs", None), cand("hits", False)]
warning4 = gp.pricing_freshness_warning(pool4)
check(warning4 is None,
      "one priced (even if False) general-market candidate is enough to prove pricing ran",
      f"got {warning4!r}")

head("5. an empty pool doesn't crash and isn't suspicious")

check(gp.pricing_freshness_warning([]) is None, "empty input returns None, not a crash")

head("6. missing/malformed projection doesn't crash (defensive real-world shape)")

malformed = [{"name": "No Projection", "price_clears": None}]
check(gp.pricing_freshness_warning(malformed) is not None,
      "a candidate with no projection dict at all is treated as a general market "
      "(stat defaults to None, which isn't in the early-priced set) and still "
      "triggers the warning when unpriced")


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

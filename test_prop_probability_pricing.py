#!/usr/bin/env python3
"""test_prop_probability_pricing.py — coverage for prop_probability.py's
pricing/value functions: max_acceptable_price, american_odds, format_odds,
expected_roi, kelly_fraction, value_verdict, devig, market_agreement,
devig_two_sided, price_quality. All ten had zero direct test coverage
despite being the functions that turn a model probability into an actual
price, ROI, Kelly stake, and buy/no-buy verdict a user could act on with
real money -- the last mile between "the model has a read" and "should I
bet this."

    /tmp/mlbvenv/bin/python3 test_prop_probability_pricing.py
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

head("== american_odds / format_odds ==")
head("1. round-trip against implied_probability, with and without the assumed vig")

o = pp.american_odds(0.60, include_vig=False)
check(o is not None, "a normal 60% probability returns a real price")
check(abs(pp.implied_probability(o) - 0.60) < 0.01,
      "the no-vig price round-trips back to ~60% implied probability", f"got {o} -> {pp.implied_probability(o)}")

o_vig = pp.american_odds(0.60, include_vig=True)
check(pp.implied_probability(o_vig) > 0.60,
      "the WITH-vig price implies a higher probability than the true 60% -- the vig is "
      "genuinely added, not a no-op", f"got {o_vig} -> {pp.implied_probability(o_vig)}")

head("2. degenerate probabilities (0, 1, negative, >1) return None, never a fabricated price")

check(pp.american_odds(0.0, include_vig=False) is None, "prob=0.0 returns None")
check(pp.american_odds(1.0, include_vig=False) is None, "prob=1.0 returns None")
check(pp.american_odds(0.998, include_vig=True) is not None,
      "a very high prob with vig added is clamped (0.995 cap) rather than blowing past 1.0 "
      "into None", f"got {pp.american_odds(0.998, include_vig=True)}")

head("3. the 50% boundary: favorite vs underdog pricing sign")

check(pp.american_odds(0.50, include_vig=False) < 0,
      "exactly 50% prices as a favorite (negative), matching the >= 0.5 branch",
      f"got {pp.american_odds(0.50, include_vig=False)}")
check(pp.american_odds(0.49, include_vig=False) > 0,
      "just under 50% prices as an underdog (positive)",
      f"got {pp.american_odds(0.49, include_vig=False)}")

head("4. format_odds renders a real +/- sign and 'n/a' for the unpriceable case")

check(pp.format_odds(0.70).startswith("-"), "a favorite formats with a leading '-'")
check(pp.format_odds(0.30).startswith("+"), "an underdog formats with a leading '+'")
check(pp.format_odds(-1.0) == "n/a",
      "a genuinely out-of-range probability (-1.0, invalid even after the vig addition) "
      "formats as 'n/a', not a crash", f"got {pp.format_odds(-1.0)!r}")

head("== max_acceptable_price / price_is_acceptable ==")
head("5. the tighter of fair-value and the user's limit wins")

# A very likely bet (95%) has a fair price far tighter (more negative) than the
# USER_MAX_PRICE=-350 limit, so the USER'S LIMIT should win (be the looser/larger bound).
limit_95 = pp.max_acceptable_price(0.95)
check(limit_95 == pp.USER_MAX_PRICE,
      "for a 95% probability, fair value would demand an extreme price far worse than "
      "the user's -350 limit, so the user's limit is the binding (tighter, larger-number) "
      "constraint", f"got {limit_95}, USER_MAX_PRICE={pp.USER_MAX_PRICE}")

# A modest bet (55%) has fair value close to even money, LOOSER (smaller/more negative
# is wrong direction -- fair is closer to -100/+100) than -350, so FAIR VALUE should win.
limit_55 = pp.max_acceptable_price(0.55)
fair_55 = pp.american_odds(0.55, include_vig=False)
check(limit_55 == fair_55,
      "for a 55% probability, fair value (~even money) is the tighter constraint, not "
      "the user's -350 limit", f"got {limit_55}, fair={fair_55}")

head("6. margin demands a real cushion below fair value")

no_margin = pp.max_acceptable_price(0.70, margin=0.0)
with_margin = pp.max_acceptable_price(0.70, margin=0.05)
check(with_margin != no_margin,
      "a real margin (0.05) changes the acceptable price vs no margin at all",
      f"no_margin={no_margin} with_margin={with_margin}")

head("7. price_is_acceptable: a real posted price at/better than the limit passes, worse fails")

check(pp.price_is_acceptable(-110, 0.55) is True,
      "a fair-ish -110 price on a 55% read clears the bar")
check(pp.price_is_acceptable(-900, 0.55) is False,
      "a wildly unfavorable -900 price on a 55% read does not clear")
check(pp.price_is_acceptable(None, 0.55) is None,
      "posted_odds=None returns None (unknown), not True or False")

head("== expected_roi / kelly_fraction ==")
head("8. expected_roi matches the worked examples in this module's own comment block "
     "(the intuition it exists to correct)")

roi_78_at_300 = pp.expected_roi(0.78, -300)
check(abs(roi_78_at_300 - 0.04) < 0.005,
      "a 78% read at -300 returns approximately +4%, matching the documented example",
      f"got {roi_78_at_300}")

roi_74_at_300 = pp.expected_roi(0.74, -300)
check(roi_74_at_300 < 0,
      "a 74% read at the SAME -300 price is a losing bet (documented: -1.3%) -- four "
      "points of estimation error genuinely flips the sign", f"got {roi_74_at_300}")

head("9. kelly_fraction is 0 for a losing-edge bet, positive for a real edge, and never "
     "negative")

check(pp.kelly_fraction(0.40, -300) == 0.0,
      "a bad bet (40% at a -300 favorite price) returns exactly 0.0 stake, not negative")
check(pp.kelly_fraction(0.80, -300) > 0.0,
      "a genuine edge (80% vs a -300/75% break-even price) returns a positive stake")

head("10. kelly_fraction handles decimal_odds degenerate case (d<=1) safely")

check(pp.kelly_fraction(0.5, -100000) == 0.0,
      "an absurdly short price doesn't produce a negative or nonsensical fraction",
      f"got {pp.kelly_fraction(0.5, -100000)}")

head("== value_verdict ==")
head("11. a real, clearly profitable bet (ROI well above the 5% MIN_ROI floor) returns "
     "BET with the arithmetic shown")

# 80% at -300 (break-even 75%): roi = 0.80*1.3333-1 = +6.67%, clears the 5% floor
v = pp.value_verdict(0.80, -300)
check(v["verdict"] == "BET", "an 80% read at -300 (+6.67% ROI, above the 5% floor) verdicts BET",
      f"got {v}")
check(v["roi"] > pp.MIN_ROI, "the recorded ROI actually clears MIN_ROI")

head("12. a positive but sub-5%-floor ROI (the documented +4% example) still returns "
     "NO BET -- MIN_ROI is a real floor, not just 'better than break-even'")

v2 = pp.value_verdict(0.78, -300)  # +4% ROI, positive but under the 5% MIN_ROI floor
check(v2["verdict"] == "NO BET" and v2["roi"] > 0,
      "a 78% read at -300 is a genuinely profitable +4% ROI, but still NO BET because "
      "4% is under the 5% MIN_ROI floor -- positive expectation alone isn't the bar",
      f"got {v2}")
check("under the" in v2["why"] and "floor" in v2["why"],
      "the NO BET reason explains the ROI floor wasn't cleared")

v2b = pp.value_verdict(0.74, -300)  # genuinely negative ROI
check(v2b["verdict"] == "NO BET" and v2b["roi"] < 0,
      "a 74% read at the same -300 price is genuinely negative ROI and also NO BET",
      f"got {v2b}")

head("13. THE ROBUSTNESS TEST: a bet that clears MIN_ROI at the point estimate but NOT at "
     "the pessimistic end of its confidence interval is still NO BET")

# 85% at -300 clears MIN_ROI easily (+13.3%); prob_lo=0.60 is well below break-even (75%)
v3 = pp.value_verdict(0.85, -300, prob_lo=0.60)
check(v3["verdict"] == "NO BET",
      "even though the point estimate (85%) clears MIN_ROI comfortably, a pessimistic "
      "60% floor is well below break-even at this price, so the verdict is NO BET",
      f"got {v3}")
check(v3["robust_to_uncertainty"] is False,
      "robust_to_uncertainty is explicitly False, showing WHY it failed")
check("pessimistic end" in v3["why"],
      "the NO BET reason specifically names the confidence-interval test, not the ROI one")

v4 = pp.value_verdict(0.85, -300, prob_lo=0.78)  # pessimistic end still above break-even
check(v4["verdict"] == "BET" and v4["robust_to_uncertainty"] is True,
      "a pessimistic floor (78%) that's STILL above break-even (75%) at this price lets "
      "the bet through as BET, robust_to_uncertainty=True", f"got {v4}")

head("== devig / market_agreement / devig_two_sided ==")
head("14. devig removes the assumed hold, always producing a smaller number than the raw implied")

d = pp.devig(0.60, hold=0.08)
check(d < 0.60, "de-vigging a 60% implied probability at 8% hold produces a smaller "
      "number", f"got {d}")
check(abs(d - 0.60 / 1.08) < 1e-9, "the exact proportional de-vig formula is used",
      f"got {d}, want {0.60/1.08}")

head("15. market_agreement: the exact worked example from this module's own comment -- "
     "an absolute-points-only test would miss a real 2x disagreement on a rare event")

# model says 8.9%, de-vigged market ~4.3% (implied slightly higher before devig)
result = pp.market_agreement(0.089, pp.american_odds(0.043 * 1.08, include_vig=False))
check(result["agreement"] == "SUSPECT",
      "an ~2x disagreement on a rare event (8.9% model vs ~4.3% market) is flagged "
      "SUSPECT via the ratio test, exactly the CJ Abrams loophole this function's own "
      "comment describes closing", f"got {result}")

head("16. market_agreement: a genuinely close read agrees")

close_result = pp.market_agreement(0.60, pp.american_odds(0.60, include_vig=True))
check(close_result["agreement"] == "AGREE",
      "a model probability that matches the (vig-included) market price almost exactly "
      "agrees once de-vigged", f"got {close_result}")

head("17. devig_two_sided: the exact worked example from this module's own docstring")

over_p, under_p, hold = pp.devig_two_sided(-111, -115)
check(abs(hold - 0.061) < 0.005,
      "the -111/-115 two-sided example measures a ~6.1% hold, matching the docstring",
      f"got hold={hold}")
check(abs(over_p - 0.496) < 0.01,
      "the true de-vigged Over probability is ~0.496, matching the docstring's worked "
      "number exactly", f"got {over_p}")
check(abs(over_p + under_p - 1.0) < 1e-9,
      "the two de-vigged sides always sum to exactly 1.0")

head("18. devig_two_sided degenerate case (both implieds are genuinely zero) returns all-None")

result_degenerate = pp.devig_two_sided(float("inf"), float("inf"))
check(result_degenerate == (None, None, None),
      "odds of +inf on both sides implies a probability of exactly 0.0 on each "
      "(100/(inf+100)), so their sum is exactly 0 and devig_two_sided returns "
      "(None, None, None) rather than a division-by-zero crash", f"got {result_degenerate}")

head("== price_quality ==")
head("19. a price better than fair shows negative tax (you're being UNDERCHARGED, a good thing)")

pq_good = pp.price_quality(0.60, 150)  # 60% true prob getting paid out at +150 -- a great price
check(pq_good["better_than_fair"] is True, "a plus-money price on a 60% read is flagged "
      "as better than fair")
check(pq_good["tax"] == 0.0, "a positive-ROI price reports tax=0.0, not a negative tax "
      "value (tax is only ever reported when ROI < 0, per the function's own logic)")

head("20. a price worse than fair shows a real positive tax")

pq_bad = pp.price_quality(0.55, -400)  # bad price for a 55% read
check(pq_bad["better_than_fair"] is False, "a -400 price on a 55% read is flagged as "
      "worse than fair")
check(pq_bad["tax"] > 0, "a losing price reports a real positive tax (the cost of paying "
      "an unfavorable price)", f"got {pq_bad}")

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

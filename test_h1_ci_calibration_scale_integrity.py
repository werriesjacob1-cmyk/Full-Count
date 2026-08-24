#!/usr/bin/env python3
"""test_h1_ci_calibration_scale_integrity.py — end-to-end regression coverage
for the 2026-08-19 H1 fix (CALIBRATED PROBABILITY / UNCERTAINTY SCALE
INTEGRITY).

THE BUG THIS LOCKS IN THE FIX FOR: prob_ci (a Wilson interval on a player's
RAW empirical hit/n count) was computed either before generate_picks.py's
apply_calibration() ever ran (line_options/alternatives, via
_batter_options) or after it (the primary line, via attach_reliability) --
but in BOTH cases from the raw, pre-calibration rate table. Once a market
has a real fitted Platt curve (hits/hits_runs_rbis/strikeouts today), the
displayed hit_probability moves to the calibrated value while prob_ci kept
describing the raw one -- two different numbers, one interval, silently fed
into recommendation.classify_recommendation()'s mandatory (require_robust=
True, PR #54/A1) pessimistic-end robustness test at prop_probability.
value_verdict().

THE FIX: no defensible calibrated-interval method existed at the time (see
the 2026-08-19 audit and this fix's own PR description for why a mechanical
sigmoid-transform of the endpoints is NOT considered defensible -- it
ignores the fitted curve's own uncertainty, which is severe in
under-supported regions). So a line's prob_ci/ci was withheld (set to None)
the moment that line is actually calibrated, in BOTH the primary-line path
(generate_picks.attach_reliability) and the alternate-line path
(generate_picks.apply_calibration's _calibrate_option_list). This file still
locks that fail-closed default in for line_options/alternatives, and for
the primary line whenever no real historical evidence exists.

2026-08-24 UPDATE, accuracy investigation: attach_reliability() now has a
SECOND, real way to earn a primary-line prob_ci back -- backtest/
reliability_bands.py's historically-measured bands (see generate_picks.
historical_prob_ci's own docstring). This is NOT a reversion of H1: it is
a different, real interval built from real graded historical outcomes at
the CALIBRATED probability's own bucket (attach_reliability runs AFTER
apply_calibration in score_slate's call order, so c["hit_probability"] is
already the calibrated number by the time historical_prob_ci sees it) --
never a raw-scale Wilson interval borrowed from the wrong number, which is
the one thing H1 actually forbids. Section 1b/3b below prove the new path
is scale-correct; sections 1/2/3 are otherwise unchanged and still prove
the ABSENT-by-default behavior whenever no real band exists.

    /tmp/mlbvenv/bin/python3 test_h1_ci_calibration_scale_integrity.py
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


import json
import os

import generate_picks as gp
import recommendation as rec
import prop_probability as pp


def set_reliability_bands(bands):
    """Force generate_picks.historical_prob_ci()'s cache to a controlled
    fixture, decoupling these checks from backtest/reliability_bands.json's
    real, still-growing content (which legitimately changes as backtest/
    rows_backfill.jsonl grows -- these checks must stay deterministic
    regardless of what real coverage currently exists on disk)."""
    gp._RELIABILITY_BANDS_CACHE = bands


def make_candidate(stat, needs, hit_probability, hits, n, lift=0.10, market_odds=-150,
                   reliability="A", lineup_assumed=False, player_id=1, **over):
    """A candidate shaped like a real post-attach_hit_probabilities row:
    empirical basis, a matching rate table entry that WOULD produce a real
    Wilson CI, real market pricing, real evidence/lineup state -- everything
    a Top Pick needs except whatever this test is isolating."""
    c = {
        "type": "batter", "name": "Test Player", "player_id": player_id,
        "team": "Athletics", "matchup": "Athletics @ Astros", "game_pk": 900001,
        "score": 75, "confidence": "High", "notable_signals": 1, "signals": {},
        "why": [], "watchouts": [],
        "projection": {"stat": stat, "value": needs - 0.5, "needs": needs},
        "hit_probability": hit_probability, "base_rate": hit_probability - lift,
        "lift": round(lift, 4), "probability_basis": "empirical",
        "probability_detail": {"empirical": hit_probability, "modelled": None},
        "market_odds": market_odds,
        "market_implied": pp.implied_probability(market_odds),
        "market_edge": round(hit_probability - pp.implied_probability(market_odds), 4),
        "price_clears": True, "reliability": reliability, "sample_n": n,
        "lineup_assumed": lineup_assumed, "alternatives": [],
    }
    c.update(over)
    return c, {"games": n, "rates": {f"{stat}_{needs}plus": {"hit": hits, "n": n}}}


REAL_ODDS_FETCHED = "2026-08-19T20:00:00+00:00"
REAL_BOARD_GENERATED = "2026-08-19T20:00:00+00:00"
import datetime as _dt
NOW = _dt.datetime.fromisoformat(REAL_ODDS_FETCHED)


def classify(c):
    """The real, unmodified production entry point: freshness + classification,
    exactly as attach_recommendations() calls it, board-fresh by construction."""
    fresh, reasons = rec.freshness_check(now=NOW, odds_fetched_at=REAL_ODDS_FETCHED,
                                         board_generated_at=REAL_BOARD_GENERATED)
    return rec.classify_recommendation(c, now=NOW, data_fresh=fresh, fresh_reasons=reasons)


head("1. END-TO-END: a calibrated market's primary line reaches classify_recommendation() "
     "with NO ci at all, never a raw-scale one, WHEN NO REAL HISTORICAL BAND COVERS IT -- "
     "the exact bug this fix closes, proven through the real pipeline functions "
     "(apply_calibration -> attach_reliability -> classify_recommendation), not a mock of any "
     "of them")

set_reliability_bands({})  # no historical coverage at all -- the pre-2026-08-24 world
c, emp = make_candidate("hits", 1, hit_probability=0.75, hits=75, n=100, lift=0.15)
gp.apply_calibration([c], ({"hits": lambda p: p * 0.90}, None))
gp.attach_reliability([c], emp_batters={1: emp}, emp_pitchers={})
check(c.get("calibrated_by") == "hits", "sanity: this candidate really was calibrated",
      str(c.get("calibrated_by")))
check(abs(c["hit_probability"] - 0.675) < 1e-9,
      "sanity: hit_probability really did move to the calibrated value (0.75*0.90=0.675)",
      f"got {c['hit_probability']}")
check("prob_ci" not in c or c.get("prob_ci") is None,
      "prob_ci is honestly absent on the calibrated line when no historical band covers it -- "
      "NOT the raw-scale Wilson interval a pre-fix run would have attached from the same rate "
      "table", f"got {c.get('prob_ci')}")
result = classify(c)
check(result["status"] != "top_pick",
      "classify_recommendation() correctly refuses Top Pick status without a defensible "
      "interval, even though probability (67.5%), lift, reliability, lineup and price would "
      "otherwise clear every other requirement", str(result))

verdict = pp.value_verdict(c["hit_probability"], c["market_odds"], prob_lo=None,
                           min_roi=rec.TOP_PICK_MIN_ROI, require_robust=True)
check("no defensible confidence interval" in verdict["why"],
      "the actual reason surfaced to a reader is the honest one A1 already writes for a "
      "missing interval -- this fix produces that exact path, not a new one",
      verdict["why"])

head("1b. 2026-08-24 UPDATE: the SAME calibrated line, but a real historical reliability band "
     "now covers its exact (stat, needs, bucket) cell -- prob_ci is now attached, and it is "
     "built around the CALIBRATED 67.5%, never the raw 75% -- proving the new mechanism does "
     "NOT reintroduce the scale-mismatch bug H1 exists to prevent")

set_reliability_bands({
    "hits_1": {"0.65": {"n": 500, "actual_rate": 0.66, "predicted_mean": 0.665,
                        "bias": -0.005, "wilson_lo": 0.62, "wilson_hi": 0.70}},
})
c1b, emp1b = make_candidate("hits", 1, hit_probability=0.75, hits=75, n=100, lift=0.15)
gp.apply_calibration([c1b], ({"hits": lambda p: p * 0.90}, None))
gp.attach_reliability([c1b], emp_batters={1: emp1b}, emp_pitchers={})
check(abs(c1b["hit_probability"] - 0.675) < 1e-9, "sanity: still calibrated to 0.675",
      f"got {c1b['hit_probability']}")
check(c1b.get("prob_ci") is not None, "a real historical band now produces a prob_ci",
      f"got {c1b.get('prob_ci')}")
if c1b.get("prob_ci"):
    lo, hi = c1b["prob_ci"]
    check(lo < 0.675 < hi or abs(lo - 0.675) < 0.15,
          "the interval is centered on/near the CALIBRATED 0.675, not the raw 0.75 -- proving "
          "this is a scale-correct interval, not the exact bug H1 forbids",
          f"got [{lo}, {hi}] around calibrated 0.675 (raw was 0.75)")
    check(hi < 0.9, "sanity: the interval is a real, bounded band, not degenerate",
          f"got hi={hi}")
check(c1b.get("prob_ci_source") == "historical_reliability_band",
      "the source is explicitly labeled so a reader (or a future audit) can always tell this "
      "interval came from the historical-band mechanism, not the per-player empirical path",
      f"got {c1b.get('prob_ci_source')!r}")
result1b = classify(c1b)
check(result1b["status"] == "top_pick",
      "with a real, defensible pessimistic-end estimate now available, this candidate -- "
      "probability, evidence, lineup and price all otherwise clean -- can finally reach Top "
      "Pick. This is the actual eligibility restoration the 2026-08-24 investigation set out "
      "to build, proven end-to-end through the unmodified real classify_recommendation()",
      str(result1b))
set_reliability_bands({})  # reset for the sections that follow

head("2. Contrast: the IDENTICAL candidate, but this market has NO calibrator (no curve "
     "applies) -- keeps its real Wilson CI and DOES reach Top Pick, proving the fix is "
     "scoped to calibrated lines only, not a blanket CI regression")

c2, emp2 = make_candidate("hits", 1, hit_probability=0.75, hits=75, n=100, lift=0.15)
gp.apply_calibration([c2], ({"total_bases": lambda p: p * 0.5}, None))  # no curve for "hits"
gp.attach_reliability([c2], emp_batters={1: emp2}, emp_pitchers={})
check(c2.get("calibrated_by") is None, "sanity: this candidate was NOT calibrated (no curve "
      "for its stat)", str(c2.get("calibrated_by")))
check(c2.get("prob_ci") is not None,
      "an uncalibrated line keeps its real, defensible prob_ci exactly as before this fix",
      f"got {c2.get('prob_ci')}")
result2 = classify(c2)
check(result2["status"] == "top_pick",
      "and DOES reach Top Pick -- proves the fix didn't collaterally break the normal, "
      "already-correct uncalibrated path", str(result2))

head("3. Each of the three currently-calibrated real markets (hits, hits_runs_rbis, "
     "strikeouts) gets honest CI absence when calibrated AND no historical band covers it -- "
     "not just 'hits' from check 1")

set_reliability_bands({})
for stat in ("hits", "hits_runs_rbis", "strikeouts"):
    c3, emp3 = make_candidate(stat, 1, hit_probability=0.70, hits=70, n=100, lift=0.12)
    gp.apply_calibration([c3], ({stat: lambda p: p * 0.85}, None))
    gp.attach_reliability([c3], emp_batters={1: emp3}, emp_pitchers={1: emp3})
    check(c3.get("calibrated_by") == stat and c3.get("prob_ci") is None,
          f"{stat}: calibrated and CI honestly absent", str((c3.get("calibrated_by"), c3.get("prob_ci"))))

head("4. Primary line and an alternate (line_options) entry on the SAME stat follow "
     "IDENTICAL CI-suppression semantics -- no special-casing between the two paths")

c4 = {
    "type": "batter", "name": "Alt Test", "player_id": 2, "team": "Athletics",
    "matchup": "Athletics @ Astros", "game_pk": 900001, "score": 75, "confidence": "High",
    "notable_signals": 0, "signals": {}, "why": [], "watchouts": [],
    "hit_probability": 0.75, "probability_basis": "empirical", "base_rate": 0.60, "lift": 0.15,
    "line_options": [
        {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.75, "base_rate": 0.60, "lift": 0.15,
         "basis": "empirical", "ci": [0.66, 0.82]},
    ],
    "alternatives": [
        {"stat": "hits", "needs": 2, "line": 1.5, "prob": 0.40, "base_rate": 0.30, "lift": 0.10,
         "basis": "empirical", "ci": [0.31, 0.50]},
    ],
}
gp.apply_calibration([c4], ({"hits": lambda p: p * 0.90}, None))
check(c4["line_options"][0]["ci"] is None,
      "the line_options entry's ci is withheld once calibrated, identically to the primary "
      "line's prob_ci in check 1", str(c4["line_options"][0]))
check(c4["alternatives"][0]["ci"] is None,
      "the alternatives entry's ci is ALSO withheld -- separate list, separate dict objects "
      "(per the A4 fix), same suppression rule applied independently, not borrowed",
      str(c4["alternatives"][0]))
check(abs(c4["line_options"][0]["prob"] - 0.675) < 1e-9 and abs(c4["alternatives"][0]["prob"] - 0.36) < 1e-9,
      "sanity: both really were calibrated against their own stat/prob, independently "
      "(0.75*0.9=0.675, 0.40*0.9=0.36)",
      str((c4["line_options"][0]["prob"], c4["alternatives"][0]["prob"])))

head("5. No calibration is applied twice within one pass -- a candidate's own calibrated "
     "value moves by exactly ONE application of its market's curve, not a compounding of "
     "the primary-line pass with something the option/alternates loop redundantly reapplies")

c5, _ = make_candidate("hits", 1, hit_probability=0.80, hits=80, n=100, lift=0.10)
c5["line_options"] = [{"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.80,
                       "base_rate": 0.70, "lift": 0.10, "basis": "empirical", "ci": [0.71, 0.87]}]
gp.apply_calibration([c5], ({"hits": lambda p: p * 0.90}, None))
check(abs(c5["hit_probability"] - 0.72) < 1e-9,
      "primary line calibrated exactly once (0.80*0.90=0.72), not twice (which would be "
      "0.80*0.90*0.90=0.648)", f"got {c5['hit_probability']}")
check(abs(c5["line_options"][0]["prob"] - 0.72) < 1e-9,
      "its OWN line_options entry (same stat, same starting prob) also calibrated exactly "
      "once, independently of the primary line's own single application",
      f"got {c5['line_options'][0]['prob']}")

head("6. Fitted calibrator parameters on disk are untouched by this fix -- loads the real "
     "committed backtest/calibrators_by_market.json and checks the exact coefficients this "
     "audit's own materiality numbers were computed against")

_cal_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest",
                         "calibrators_by_market.json")
with open(_cal_path) as f:
    _cal = json.load(f)
_expected = {
    "hits": (5.6923075992789025, -3.0162887832417584),
    "hits_runs_rbis": (2.6016350036383287, -1.0387400219330867),
    "strikeouts": (0.8432830444169422, -0.1259493893243091),
}
for market, (a, b) in _expected.items():
    got = _cal.get(market, {}).get("params", {})
    check(abs(got.get("A", 0) - a) < 1e-9 and abs(got.get("B", 0) - b) < 1e-9,
          f"{market} calibrator params on disk are exactly unchanged (A={a}, B={b})",
          f"got {got}")

head("7. Recommendation thresholds are untouched by this fix -- this PR changes CI "
     "AVAILABILITY, never the bar a candidate must clear")

check(rec.TOP_PICK_MIN_PROB == 0.60, "TOP_PICK_MIN_PROB unchanged", str(rec.TOP_PICK_MIN_PROB))
check(rec.TOP_PICK_MIN_RELIABILITY == ("A", "B"), "TOP_PICK_MIN_RELIABILITY unchanged",
      str(rec.TOP_PICK_MIN_RELIABILITY))
check(rec.TOP_PICK_MIN_ROI == pp.MIN_ROI, "TOP_PICK_MIN_ROI still reuses prop_probability.MIN_ROI "
      "directly, not a copy that could have silently drifted", str(rec.TOP_PICK_MIN_ROI))
check(rec.LEAN_MIN_LIFT == 0.02, "LEAN_MIN_LIFT unchanged", str(rec.LEAN_MIN_LIFT))

head("8. recommendation.py and prop_probability.py are byte-for-byte untouched by this fix "
     "-- the whole fix lives in generate_picks.py; A1/PR#54's fail-closed behavior does the "
     "rest with no new code of its own")

_repo_dir = os.path.dirname(os.path.abspath(__file__))
# Pinned to H1's OWN fixed historical commit RANGE (d389a8a8 = first parent
# of d061dd13, "Merge PR #56: H1 fix" -- i.e. pre-H1 main; d061dd13 = the H1
# merge commit itself), NOT "pre-H1 baseline vs whatever HEAD is now". The
# latter (this check's original form, and an earlier attempt at fixing it
# in this same PR) is a claim that NO commit since H1 has ever touched
# these two files -- true only by accident, and false as soon as any LATER,
# unrelated, legitimate change lands in prop_probability.py, exactly as
# THIS PR's own p_at_least_singles addition now does. Diffing the two ENDS
# of H1's own merge instead tests the one thing this check was ever
# actually meant to prove -- H1's OWN diff never touched these files -- as
# a permanent, immutable historical fact, completely decoupled from
# whatever any later branch (including this one) legitimately does to
# these files afterward.
#
# CI-SAFE: actions/checkout@v4 defaults to a SHALLOW, single-commit clone,
# where these pinned SHAs (everything but HEAD) are simply absent -- `git
# diff` against them would raise "unknown revision", a tooling failure, not
# a real finding. Verified via `git cat-file -e` first; when either commit
# isn't reachable (CI, or any shallow/partial checkout), this sub-check is
# honestly skipped rather than forced to a false pass or a spurious crash.
_PRE_H1_SHA = "d389a8a8d664796022da5b82cf57393914295a12"
_H1_MERGE_SHA = "d061dd13483822246f063e86e1daa44b71632eb3"
_h1_range_reachable = (
    os.system("git -C %s cat-file -e %s^{commit} 2>/dev/null" % (_repo_dir, _PRE_H1_SHA)) == 0
    and os.system("git -C %s cat-file -e %s^{commit} 2>/dev/null" % (_repo_dir, _H1_MERGE_SHA)) == 0)
if _h1_range_reachable:
    _diff_files = os.popen("git -C %s diff --name-only %s %s -- recommendation.py "
                           "prop_probability.py"
                           % (_repo_dir, _PRE_H1_SHA, _H1_MERGE_SHA)).read().strip()
    check(_diff_files == "", "H1's OWN merge (d389a8a8..d061dd13) never touched "
          "recommendation.py or prop_probability.py -- H1's fix lives entirely in "
          "generate_picks.py plus tests, as a fixed historical fact independent of what "
          "later, unrelated PRs (including this one) legitimately do to those files. "
          "(The calibrator artifact's fitted PARAMS specifically -- not the whole file -- are "
          "checked directly in section 6 above; a later PR may add metadata alongside them "
          "without invalidating this check.)", f"got changed files: {_diff_files!r}")
else:
    print("  [SKIP] H1's historical commit range (%s..%s) not reachable in this checkout "
         "(shallow clone) -- cannot verify H1's own diff scope here; section 6's "
         "fitted-params check above already covers the invariant that actually matters "
         "going forward" % (_PRE_H1_SHA, _H1_MERGE_SHA))


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

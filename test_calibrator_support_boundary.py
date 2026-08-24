#!/usr/bin/env python3
"""test_calibrator_support_boundary.py — coverage for the 2026-08-19
calibrator-support-boundary fix (accuracy-north-star, calibrator-support PR).

THE BUG THIS LOCKS IN THE FIX FOR: a fitted 2-parameter Platt curve is
mathematically defined over all of [0,1], but only has real EVIDENCE where
its own fitting data actually had rows. The strikeouts calibrator's own
inflection point sits at raw p~=0.15 -- a region its real 609 training rows
never touched at all (real support floor, computed from the exact real
fitting rows: 0.55) -- yet the pre-fix code applied it there anyway,
manufacturing a ~+40 percentage point correction with zero supporting
evidence. Confirmed live for all 3 currently-calibrated markets using the
REAL committed backtest/rows.jsonl, filtered to each calibrator's own
recorded prop_type + date_range (exact bit-for-bit match for strikeouts,
609/609 rows; close but not bit-identical for hits/hits_runs_rbis since
rows.jsonl grows over time -- see backtest/backfill_support_bins.py's own
docstring for the honest accounting of that gap):

  hits            supported only in [0.50, 0.70)  (4 of 20 bins)
  hits_runs_rbis  supported only in [0.55, 0.80)  (5 of 20 bins)
  strikeouts      supported only in [0.55, 0.80)  (5 of 20 bins)

THE FIX: backtest/calibration.py's compute_support_bins()/in_support() plus
generate_picks.py's _calibrate_one()/_in_calibrator_support() gate every
calibration lookup on whether the input probability falls in a bin with
>=MIN_BIN_COUNT (30) real fitting rows. Outside support: the probability
ships UNCHANGED (raw fallback), calibrated_by stays honestly None -- never
suppressed/dropped, never a manufactured correction. Inside support:
behavior is byte-identical to before this fix.

    /tmp/mlbvenv/bin/python3 test_calibrator_support_boundary.py
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


import json
import os

import generate_picks as gp
import recommendation as rec
import prop_probability as pp

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
import calibration as cal


class FakeCalibrator:
    """A minimal stand-in shaped like backtest.calibration.Calibrator (a
    plain callable with a .meta dict) -- exercises the exact interface
    _calibrate_one/_in_calibrator_support actually depend on, without
    dragging in a real fit."""
    def __init__(self, fn, support_bins=None, meta_extra=None):
        self._fn = fn
        self.meta = {"support_bins": support_bins, **(meta_extra or {})}

    def __call__(self, p):
        return self._fn(p)


def make_support_bins(supported_ranges, bin_width=0.05, min_count=30):
    """[(lo, hi), ...] of SUPPORTED ranges -> a full support_bins list
    matching compute_support_bins' own shape, everything else unsupported."""
    n_bins = int(round(1.0 / bin_width))
    bins = []
    for i in range(n_bins):
        lo, hi = round(i * bin_width, 10), round((i + 1) * bin_width, 10)
        supported = any(slo <= lo < shi for slo, shi in supported_ranges)
        bins.append({"lo": lo, "hi": hi, "count": min_count if supported else 0,
                    "supported": supported})
    return bins


head("1. compute_support_bins: real rows produce the exact expected bin shape")

rows = ([{"predicted_prob": 0.62, "outcome": 1}] * 40  # a real, dense bin
       + [{"predicted_prob": 0.12, "outcome": 0}] * 5)  # a real, thin bin
bins = cal.compute_support_bins(rows, bin_width=0.05, min_count=30)
check(len(bins) == 20, "20 bins at width 0.05 across [0,1]", f"got {len(bins)}")
dense_bin = next(b for b in bins if b["lo"] <= 0.62 < b["hi"])
thin_bin = next(b for b in bins if b["lo"] <= 0.12 < b["hi"])
check(dense_bin["count"] == 40 and dense_bin["supported"] is True,
      "a bin with 40 real rows (>=30) is marked supported", str(dense_bin))
check(thin_bin["count"] == 5 and thin_bin["supported"] is False,
      "a bin with 5 real rows (<30) is marked unsupported, not hidden", str(thin_bin))
empty_bin = next(b for b in bins if b["lo"] <= 0.90 < b["hi"])
check(empty_bin["count"] == 0 and empty_bin["supported"] is False,
      "a bin with zero rows is reported (count=0), not omitted", str(empty_bin))

head("2. in_support: absence of support_bins data returns None (unknown), never guesses True or False")

check(cal.in_support(0.5, None) is None, "None support_bins -> None (unknown)")
check(cal.in_support(0.5, []) is None, "empty support_bins -> None (unknown)")
check(cal.in_support(0.62, bins) is True, "a real supported bin -> True")
check(cal.in_support(0.12, bins) is False, "a real unsupported (but non-empty) bin -> False")

head("3. fit_calibrator now attaches support_bins automatically, computed from the SAME "
     "rows it fit on -- train-only basis, never re-derived from a different row set")

fit_rows = [{"date": "2026-07-01", "predicted_prob": 0.60 + 0.001 * i, "outcome": i % 2}
           for i in range(40)]
c = cal.fit_calibrator(fit_rows, method="platt", min_rows=30)
check(c.meta.get("support_bin_width") == cal.SUPPORT_BIN_WIDTH, "support_bin_width recorded")
check(c.meta.get("support_min_count") == cal.MIN_BIN_COUNT, "support_min_count recorded")
check(c.meta.get("support_rows_basis") == "train_only", "basis is explicitly train_only")
sb = c.meta.get("support_bins")
check(sb is not None and len(sb) == 20, "support_bins present with the expected shape")
supported_bin = next(b for b in sb if b["lo"] <= 0.60 < b["hi"])
check(supported_bin["count"] == 40 and supported_bin["supported"] is True,
      "the bin all 40 fitting rows land in is correctly marked supported", str(supported_bin))

head("4. generate_picks._calibrate_one: INSIDE support behaves byte-identically to before "
     "this fix -- real transform applied, calibrated_by set")

per_market = {"hits": FakeCalibrator(lambda p: p * 0.8,
                                     support_bins=make_support_bins([(0.5, 0.8)]))}
cp, by = gp._calibrate_one(0.70, "hits", per_market, None)
check(abs(cp - 0.56) < 1e-9 and by == "hits",
      "0.70 (inside [0.5,0.8) support) calibrates normally to 0.56, calibrated_by='hits'",
      f"got {(cp, by)}")

head("5. OUTSIDE support: probability does NOT receive the unsupported transformation -- "
     "raw fallback, calibrated_by honestly None")

cp2, by2 = gp._calibrate_one(0.20, "hits", per_market, None)
check(cp2 == 0.20, "0.20 (outside [0.5,0.8) support) is returned UNCHANGED, not 0.20*0.8=0.16",
      f"got {cp2}")
check(by2 is None, "calibrated_by is honestly None for the out-of-support case", f"got {by2}")

head("6. Distinguishing 'no calibrator for this market at all' from 'calibrator exists, "
     "probability outside its support' -- both return calibrated_by=None, but only the "
     "latter still returns a real (unchanged) probability from a market that DOES have a fit")

cp3, by3 = gp._calibrate_one(0.20, "total_bases", per_market, None)  # no curve for total_bases at all
check(cp3 is None and by3 is None,
      "a market with NO calibrator at all returns (None, None), not (prob, None) -- "
      "these are genuinely different facts even though both are calibrated_by=None "
      "in apply_calibration's final output", f"got {(cp3, by3)}")

head("7. Missing support_bins metadata (an older/malformed calibrator artifact) preserves "
     "PRE-FIX behavior exactly -- never silently disables a real calibrator")

per_market_no_meta = {"hits": FakeCalibrator(lambda p: p * 0.8, support_bins=None)}
cp4, by4 = gp._calibrate_one(0.05, "hits", per_market_no_meta, None)
check(abs(cp4 - 0.04) < 1e-9 and by4 == "hits",
      "with no support_bins recorded at all, calibration applies unconditionally, exactly "
      "matching every pre-fix caller (existing tests use plain lambdas with no .meta at "
      "all, which is exactly this case)", f"got {(cp4, by4)}")

head("8. Primary line and alternate (line_options/alternatives) semantics are IDENTICAL -- "
     "same support gate, same outcome, for the same stat+probability wherever it appears")

cand = {
    "type": "batter", "name": "Support Test", "player_id": 9, "team": "Athletics",
    "matchup": "Athletics @ Astros", "game_pk": 900001, "score": 75, "confidence": "High",
    "notable_signals": 0, "signals": {}, "why": [], "watchouts": [],
    "hit_probability": 0.20, "probability_basis": "empirical", "base_rate": 0.15, "lift": 0.05,
    "line_options": [
        {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.20, "base_rate": 0.15, "lift": 0.05,
         "basis": "empirical", "ci": [0.10, 0.32]},
    ],
    "alternatives": [
        {"stat": "hits", "needs": 2, "line": 1.5, "prob": 0.20, "base_rate": 0.15, "lift": 0.05,
         "basis": "empirical", "ci": [0.10, 0.32]},
    ],
}
gp.apply_calibration([cand], (per_market, None))
check(cand["hit_probability"] == 0.20 and cand.get("calibrated_by") is None,
      "primary line: out-of-support, unchanged, calibrated_by None", str(cand.get("calibrated_by")))
check(cand["line_options"][0]["prob"] == 0.20 and cand["line_options"][0].get("calibrated_by") is None,
      "line_options entry: identical out-of-support outcome", str(cand["line_options"][0]))
check(cand["alternatives"][0]["prob"] == 0.20 and cand["alternatives"][0].get("calibrated_by") is None,
      "alternatives entry: identical out-of-support outcome", str(cand["alternatives"][0]))

head("9. CI REGRESSION GUARD (H1 semantics preserved): an out-of-support probability's ci "
     "is NOT suppressed, because the point estimate never actually moved -- this is exactly "
     "the regression the support-boundary fix could have silently introduced into H1")

check(cand["line_options"][0]["ci"] == [0.10, 0.32],
      "line_options ci survives untouched -- no real transform happened, so H1's "
      "'suppress ci only when actually calibrated' rule correctly leaves it alone",
      str(cand["line_options"][0]["ci"]))
check(cand["alternatives"][0]["ci"] == [0.10, 0.32],
      "alternatives ci survives untouched, independently, same reasoning",
      str(cand["alternatives"][0]["ci"]))

head("10. Contrast: an IN-support probability on the same candidate shape still correctly "
     "triggers H1's ci suppression -- the guard in check 9 is scoped correctly, not a "
     "blanket 'never suppress' regression of its own")

cand2 = {
    "type": "batter", "name": "In Support Test", "player_id": 10, "team": "Athletics",
    "matchup": "Athletics @ Astros", "game_pk": 900001, "score": 75, "confidence": "High",
    "notable_signals": 0, "signals": {}, "why": [], "watchouts": [],
    "projection": {"stat": "hits", "value": 0.5, "needs": 1},
    "hit_probability": 0.70, "probability_basis": "empirical", "base_rate": 0.60, "lift": 0.10,
    "line_options": [
        {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.70, "base_rate": 0.60, "lift": 0.10,
         "basis": "empirical", "ci": [0.63, 0.77]},
    ],
    "alternatives": [],
}
# 2026-08-24 second-CI-path fix: a nulled ci can now be earned back by a real
# historical reliability band (own dedicated coverage in
# test_h1_ci_calibration_scale_integrity.py sections 9-13). Isolate this check
# from backtest/reliability_bands.json's real, still-growing content so it
# keeps testing the support-boundary guard itself, not whichever buckets
# happen to have real coverage today.
_prior_bands_cache = gp._RELIABILITY_BANDS_CACHE
gp._RELIABILITY_BANDS_CACHE = {}
gp.apply_calibration([cand2], (per_market, None))
gp._RELIABILITY_BANDS_CACHE = _prior_bands_cache
check(abs(cand2["hit_probability"] - 0.56) < 1e-9 and cand2.get("calibrated_by") == "hits",
      "sanity: this one really was calibrated (0.70*0.8=0.56)", str(cand2["hit_probability"]))
check(cand2["line_options"][0]["ci"] is None,
      "a REAL transform (in-support) still correctly nulls the now-stale ci when no "
      "historical band covers it -- H1's own behavior, unregressed", str(cand2["line_options"][0]))

head("11. No double calibration: a candidate evaluated once produces exactly one "
     "application of the curve, in-support or out -- verified both ways")

per_market_double = {"hits": FakeCalibrator(lambda p: p * 0.9,
                                            support_bins=make_support_bins([(0.5, 0.9)]))}
c_once = {"projection": {"stat": "hits", "value": 0.5, "needs": 1},
         "hit_probability": 0.70, "probability_basis": "empirical", "base_rate": 0.60}
gp.apply_calibration([c_once], (per_market_double, None))
check(abs(c_once["hit_probability"] - 0.63) < 1e-9,
      "in-support: calibrated exactly once (0.70*0.9=0.63), not twice (0.5670)",
      f"got {c_once['hit_probability']}")

head("12. Real-board proof: the 3 currently-shipped calibrators' own committed support_bins "
     "correctly gate their own real, documented unsupported region (the strikeouts tail "
     "this whole investigation started from)")

_cal_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest",
                         "calibrators_by_market.json")
with open(_cal_path) as f:
    _shipped = json.load(f)
for market in ("hits", "hits_runs_rbis", "strikeouts"):
    meta = _shipped[market]["meta"]
    check("support_bins" in meta and meta["support_bins"],
          f"{market}: real shipped calibrator carries non-empty support_bins metadata")
    n_supported = sum(1 for b in meta["support_bins"] if b["supported"])
    check(0 < n_supported < len(meta["support_bins"]),
          f"{market}: support is genuinely partial (some bins supported, some not) -- "
          f"neither 'nothing is ever calibrated' nor 'everything always is'",
          f"{n_supported}/{len(meta['support_bins'])} bins supported")

_strikeouts_bins = _shipped["strikeouts"]["meta"]["support_bins"]
_low_tail = next(b for b in _strikeouts_bins if b["lo"] <= 0.10 < b["hi"])
check(_low_tail["supported"] is False,
      "the real strikeouts curve's own low-probability tail (raw ~0.10, near its own "
      "inflection point) is correctly marked UNSUPPORTED by its real 609-row fitting "
      "sample -- the exact defect this whole investigation started from",
      str(_low_tail))

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

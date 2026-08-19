#!/usr/bin/env python3
"""test_strikeout_alternatives_stat_fix.py — regression coverage for the
missing-stat-key defect in generate_picks.py's strikeouts branch of
attach_hit_probabilities().

THE BUG. Every other option-list builder in this file stamps "stat" onto
each option dict at construction time (pitcher_outs, combined_strikeouts,
_batter_options). The strikeouts branch (attach_hit_probabilities, the
`elif stat == "strikeouts":` block) built its per-threshold `opts` list
without a "stat" key at all. c["line_options"] survived by accident --
_keep_options(opts, "strikeouts") backfills a default_stat when one is
missing. c["alternatives"], built directly from the raw opts list with no
such backfill, did not: every alternative strikeout line's "stat" resolved
to None everywhere downstream. apply_calibration's _calibrate_one(prob,
None, per_market, glob) then hits per_market.get(None) -> None, falls to
glob (None on disk today) -> fn is None -> returns (None, None) -> the
option is silently `continue`d out of calibration entirely.
select_shadow_tracking() has the identical `alt.get("stat") is None:
continue` guard, so strikeout alternatives were also silently dropped from
shadow tracking, not just calibration.

THE FIX. One line: the opts.append({...}) literal at the strikeouts
branch's construction site now includes "stat": "strikeouts", matching
every sibling option-list literal in this file. Because line_options,
alternatives, and the primary line are all built from the SAME opts list,
this single edit fixes all three call sites at once -- no second
calibration path, no per-consumer patch.

Every check here goes through the REAL generated-candidate path
(generate_picks.attach_hit_probabilities(), not a hand-built opts dict)
before calibration is ever applied, per the requirement that this repair
be proven through actual candidate construction, not just a helper-level
_calibrate_one() call.

    /tmp/mlbvenv/bin/python3 test_strikeout_alternatives_stat_fix.py
"""
import copy
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
import prop_probability as pp


def pitcher_cand(name, pid, bf, k_rate):
    """A minimal, real-shaped pitcher candidate -- same keys score_pitcher()
    actually returns (see its own return dict): type, player_id, projection,
    expected_bf, k_rate. No emp_pitchers/league_rates data is supplied on
    purpose, so attach_hit_probabilities' _blend(None, modelled) returns the
    modelled probability UNCHANGED (basis="modelled") -- every expected
    number below is computed directly from prop_probability.p_at_least_
    strikeouts, with no shrinkage math to duplicate."""
    return {"type": "pitcher", "name": name, "player_id": pid,
            "team": "BOS", "matchup": "BOS @ NYY", "game_pk": 999999,
            "prop": "placeholder", "projection": {"stat": "strikeouts", "value": bf * k_rate},
            "expected_bf": bf, "k_rate": k_rate,
            "score": 60.0, "why": [], "watchouts": [], "notable_signals": 0,
            "confidence": "Medium", "signals": {}}


# Real, current shipped strikeouts calibrator support region (backtest/
# calibrators_by_market.json, merged in PR #57): [0.55, 0.80).
_cal = gp.load_calibrator()
check(_cal is not None, "the real shipped calibrator loads from disk (backtest/calibrators_by_market.json)")
_per_market, _glob = _cal
_strikeout_fn = _per_market.get("strikeouts")
check(_strikeout_fn is not None, "a real, fitted strikeouts calibrator exists in the shipped artifact")
_support = _strikeout_fn.meta["support_bins"]
_supported_lo = min(b["lo"] for b in _support if b["supported"])
_supported_hi = max(b["hi"] for b in _support if b["supported"])
check(abs(_supported_lo - 0.55) < 1e-9 and abs(_supported_hi - 0.80) < 1e-9,
      "sanity: the real shipped strikeouts support region is [0.55, 0.80), matching the "
      "accepted PR #57 report", f"got [{_supported_lo}, {_supported_hi})")


head("1. REGRESSION: every strikeouts option built by the real candidate-construction "
     "path (attach_hit_probabilities) now carries its own 'stat' key -- primary, "
     "line_options, AND alternatives -- reproducing the exact bug and proving it fixed")

# bf=27, k_rate=0.24 -> P(K>=t) for t=4..8: .9173 .8116 .6582 .4805 .3122
# (Wide spread on purpose: t=4/5 land above the real support ceiling, t=6
# lands inside it, t=7/8 land below the floor and below MIN_LINE_PROB.)
c1 = pitcher_cand("Pitcher A", 111, bf=27, k_rate=0.24)
out = gp.attach_hit_probabilities([c1], comp_table={}, emp_batters={}, emp_pitchers={},
                                   league_rates=None, k_prices=None)
c1 = out[0]

check(c1.get("hit_probability") is not None,
      "the real candidate-construction path produced a primary probability at all "
      "(sanity -- if this is None the rest of the test proves nothing)",
      str({k: c1.get(k) for k in ("hit_probability", "projection")}))
check(c1["projection"]["stat"] == "strikeouts", "the primary line's own projection.stat is 'strikeouts'")
check(bool(c1.get("line_options")), "line_options was populated by the real path")
check(all(o.get("stat") == "strikeouts" for o in c1["line_options"]),
      "EVERY line_options entry carries stat='strikeouts' (this already worked pre-fix "
      "via _keep_options' default_stat backfill -- confirms no regression there)",
      str(c1["line_options"]))
check(bool(c1.get("alternatives")), "alternatives was populated by the real path")
check(all(o.get("stat") == "strikeouts" for o in c1["alternatives"]),
      "THE FIX: every alternatives entry now carries stat='strikeouts' -- pre-fix this "
      "was None on every one of these dicts, since c['alternatives'] is built from the "
      "raw opts list with no backfill",
      str(c1["alternatives"]))
check(len(c1["alternatives"]) == min(3, 4),
      "alternatives is capped at 3 entries (the pre-existing [:3] slice), unchanged by this fix")

# apply_calibration mutates its candidates' nested option dicts IN PLACE.
# Every scenario below needs its own untouched copy of c1 -- a shallow
# dict(c) would still share the same nested "alternatives"/"line_options"
# list objects across scenarios and silently corrupt later checks with an
# earlier scenario's mutation. Freeze a pristine deep copy once, here, and
# deepcopy FROM this frozen copy for every scenario below that needs c1.
c1_pristine = copy.deepcopy(c1)


head("2. PRIMARY, IN SUPPORT: a candidate whose best (highest-probability, floor-clearing) "
     "line lands inside the real calibrator's support region gets genuinely calibrated")

# bf=24, k_rate=0.19 -> P(K>=t) for t=4..8: .695 .4903 .2982 .1556 .0696
# Only t=4 (0.695) clears MIN_LINE_PROB=0.60, so it is the only eligible
# line and therefore _pick_line's choice -- and 0.695 sits inside [0.55,0.80).
c2 = pitcher_cand("Pitcher B", 222, bf=24, k_rate=0.19)
out2 = gp.attach_hit_probabilities([c2], comp_table={}, emp_batters={}, emp_pitchers={},
                                    league_rates=None, k_prices=None)
c2 = out2[0]
check(abs(c2["hit_probability"] - 0.695) < 5e-4,
      "the real path picked the expected in-support threshold as primary",
      f"got {c2['hit_probability']}")
c2_pristine = copy.deepcopy(c2)
raw_before = c2["hit_probability"]
out2_cal = gp.apply_calibration([copy.deepcopy(c2_pristine)], _cal)
c2_cal = out2_cal[0]
check(c2_cal.get("calibrated_by") == "strikeouts",
      "the primary line's calibrated_by correctly reports 'strikeouts' -- a real transform "
      "happened because 0.695 is inside the fitted curve's own support",
      str({k: c2_cal.get(k) for k in ("hit_probability", "raw_hit_probability", "calibrated_by")}))
check(c2_cal["raw_hit_probability"] == raw_before,
      "the raw, pre-calibration probability is preserved exactly")
check(abs(c2_cal["hit_probability"] - round(float(_strikeout_fn(raw_before)), 4)) < 1e-9,
      "the calibrated probability (rounded to 4dp, matching _calibrate_one's own rounding) "
      "matches what the real shipped Platt curve actually returns for this raw input -- "
      "not a hand-invented number")


head("3. PRIMARY, OUT OF SUPPORT: a candidate whose best line lands above the real "
     "calibrator's support ceiling ships RAW, honestly uncalibrated")

out1_cal = gp.apply_calibration([copy.deepcopy(c1_pristine)], _cal)
c1_cal = out1_cal[0]
check(c1_cal["hit_probability"] == c1_pristine["hit_probability"],
      "the primary line (0.9173, above the 0.80 support ceiling) is left COMPLETELY "
      "unchanged -- no manufactured correction outside the curve's own evidence",
      f"raw={c1_pristine['hit_probability']} after={c1_cal['hit_probability']}")
check(c1_cal.get("calibrated_by") is None,
      "calibrated_by correctly stays None -- honestly reporting that no real transform "
      "happened, distinct from silence")
check(c1_cal.get("raw_hit_probability") == c1_pristine["hit_probability"],
      "raw_hit_probability is still stamped even though the value didn't change -- this "
      "candidate really was EVALUATED against the curve, just found unsupported")


head("4. ALTERNATIVES, IN SUPPORT: an alternative strikeout line inside support is now "
     "REACHABLE and genuinely calibrated exactly once -- this is the actual bug fix, "
     "proven end to end through the real path")

alts_before = {round(o["prob"], 4): dict(o) for o in c1_pristine["alternatives"]}
in_support_alt_raw = next(p for p in alts_before if 0.55 <= p < 0.80)
check(abs(in_support_alt_raw - 0.6582) < 5e-4,
      "sanity: the t=6 alternative (0.6582) is the one inside support in this fixture",
      str(sorted(alts_before)))

out1b = gp.apply_calibration([copy.deepcopy(c1_pristine)], _cal)
alts_after = {round(o.get("raw_prob", o["prob"]), 4): o for o in out1b[0]["alternatives"]}
in_support_after = alts_after[round(in_support_alt_raw, 4)]
check(in_support_after.get("calibrated_by") == "strikeouts",
      "PRE-FIX this alternative would have been silently skipped entirely (stat=None -> "
      "_calibrate_one returns (None, None) -> apply_calibration's `if ocp is None: continue` "
      "never touches it). POST-FIX it reaches the real strikeouts curve and is calibrated",
      str(in_support_after))
check(abs(in_support_after["prob"] - round(float(_strikeout_fn(in_support_alt_raw)), 4)) < 1e-9,
      "the calibrated value matches the real curve's own output for this exact input")
check(in_support_after["raw_prob"] == in_support_alt_raw,
      "the alternative's own raw probability is preserved independently of the primary line's")


head("5. ALTERNATIVES, OUT OF SUPPORT: an alternative strikeout line outside support stays "
     "raw, with honest provenance, never dropped from the list")

out_of_support_alt_raw = next(p for p in alts_before if not (0.55 <= p < 0.80))
out_after = alts_after[round(out_of_support_alt_raw, 4)]
check(out_after["prob"] == out_of_support_alt_raw,
      "an out-of-support alternative's probability is left completely unchanged",
      str(out_after))
check(out_after.get("calibrated_by") is None,
      "calibrated_by correctly reports None for the out-of-support alternative")
check(len(out1b[0]["alternatives"]) == len(c1["alternatives"]),
      "no alternative is dropped from the list by calibration -- in-support and "
      "out-of-support entries both survive, just with different provenance")


head("6. EXACTLY-ONCE CALIBRATION: the pipeline has exactly one apply_calibration() call "
     "site (score_slate), so a candidate's strikeout alternatives are calibrated once, not "
     "accumulated -- demonstrated by showing a second call visibly re-transforms the value, "
     "which is exactly why calling it twice would be wrong and why the single-call-site "
     "architecture (unchanged by this fix) is what actually prevents it in production")

once = gp.apply_calibration([copy.deepcopy(c2_pristine)], _cal)[0]
p_once = once["hit_probability"]
twice = gp.apply_calibration([copy.deepcopy(once)], _cal)[0]
check(once.get("calibrated_by") == "strikeouts" and once.get("raw_hit_probability") == c2_pristine["hit_probability"],
      "a single real application stamps calibrated_by and raw_hit_probability exactly once, "
      "matching primary-line provenance for every other calibrated market",
      str({k: once.get(k) for k in ("hit_probability", "raw_hit_probability", "calibrated_by")}))
check(twice["hit_probability"] != p_once,
      "feeding an ALREADY-calibrated probability through apply_calibration a second time "
      "visibly moves it again (0.613 is itself inside [0.55,0.80), so it re-enters the "
      "curve) -- proving double-application is observably wrong, and that safety here "
      "comes from the pipeline's single call site, not from any idempotency guarantee "
      "in apply_calibration itself",
      f"once={p_once} twice={twice['hit_probability']}")


head("7. LOWER SUPPORT BOUNDARY and adjacent values: 0.55 is supported, immediately below "
     "it is not -- exercised through _calibrate_one with the real shipped curve")

lo_in = gp._calibrate_one(0.55, "strikeouts", _per_market, _glob)
lo_out = gp._calibrate_one(0.5499, "strikeouts", _per_market, _glob)
check(lo_in[1] == "strikeouts", "0.55 itself (the closed lower boundary) is treated as supported",
      str(lo_in))
check(lo_out == (0.5499, None),
      "0.5499 (immediately below the boundary) is treated as unsupported and returned raw",
      str(lo_out))


head("8. UPPER SUPPORT BOUNDARY and adjacent values: just below 0.80 is supported, 0.80 "
     "itself is not (bins are half-open [lo, hi))")

hi_in = gp._calibrate_one(0.7999, "strikeouts", _per_market, _glob)
hi_out = gp._calibrate_one(0.80, "strikeouts", _per_market, _glob)
check(hi_in[1] == "strikeouts", "0.7999 (just inside the open upper boundary) is supported",
      str(hi_in))
check(hi_out == (0.80, None),
      "0.80 itself (the open boundary, half-open bins are [lo, hi)) is unsupported",
      str(hi_out))


head("9. NO CALIBRATOR AVAILABLE: a strikeouts alternative fails safely to raw when no "
     "strikeouts calibrator and no pooled fallback exist at all")

no_cal_result = gp.apply_calibration([copy.deepcopy(c1_pristine)], ({}, None))
no_cal_alt = no_cal_result[0]["alternatives"][0]
check(no_cal_alt["prob"] == c1_pristine["alternatives"][0]["prob"],
      "with no applicable calibrator anywhere, every alternative ships its raw probability "
      "unchanged", str(no_cal_alt))
check(no_cal_alt.get("calibrated_by") is None,
      "calibrated_by correctly stays None when nothing could apply")
check(no_cal_alt.get("raw_prob") is None,
      "raw_prob is correctly NOT stamped when calibration never actually ran (distinct from "
      "the out-of-support case, which DID evaluate against a real curve) -- matches "
      "apply_calibration's existing 'if ocp is None: continue' contract",
      str(no_cal_alt))


head("10. H1 CI semantics: strikeouts options never carry a real 'ci' key at all (score_"
     "pitcher's strikeouts branch never computes a Wilson interval, unlike _batter_"
     "options) -- H1's existing, market-agnostic suppression rule (unchanged by this fix) "
     "correctly still only touches 'ci' when a REAL transform happened")

check(out_after.get("ci", "<absent>") == "<absent>",
      "the OUT-OF-support alternative never had a real transform (oby is None), so H1's "
      "suppression line ( `if oby is not None: opt['ci']=None` ) never runs for it -- 'ci' "
      "correctly stays completely absent, not even set to None",
      str(out_after.get("ci", "<absent>")))
check(in_support_after.get("ci") is None and "ci" in in_support_after,
      "the IN-support alternative DID have a real transform, so H1's existing suppression "
      "rule correctly fires and sets ci=None -- this is not new behavior from this fix, "
      "it's H1's pre-existing, market-agnostic rule finally being exercised for strikeouts "
      "alternatives now that they are reachable at all; None is exactly as honest here as "
      "it is for any other market's newly-calibrated option with no defensible interval",
      str(in_support_after.get("ci", "<absent>")))


head("11. MISSING/UNKNOWN MARKET IDENTITY still fails safely: a genuinely unidentifiable "
     "option (stat=None, simulating a different, not-yet-fixed bug elsewhere) does not "
     "borrow the strikeouts curve or crash")

unknown = gp._calibrate_one(0.65, None, _per_market, _glob)
check(unknown == (None, None),
      "an option with no resolvable stat identity at all is left completely uncalibrated "
      "(not silently matched to strikeouts or any other curve) -- this is the correct "
      "fail-safe for the separate missing-identity case, unaffected by this fix",
      str(unknown))


head("12. PRIMARY / line_options / ALTERNATIVES PARITY: all three carry consistent "
     "stat identity and consistent calibration semantics for the same real candidate")

full = gp.apply_calibration([copy.deepcopy(c1_pristine)], _cal)[0]
all_stats = {full["projection"]["stat"]}
all_stats |= {o["stat"] for o in full.get("line_options") or []}
all_stats |= {o["stat"] for o in full.get("alternatives") or []}
check(all_stats == {"strikeouts"},
      "primary, every line_options entry, and every alternatives entry all report the "
      "SAME 'strikeouts' identity for this candidate", str(all_stats))
lo_by_needs = {o["needs"]: o for o in full["line_options"]}
alt_by_needs = {o["needs"]: o for o in full["alternatives"]}
shared_needs = set(lo_by_needs) & set(alt_by_needs)
check(bool(shared_needs), "line_options and alternatives overlap on at least one threshold "
      "in this fixture (sanity)", str(shared_needs))
for needs in shared_needs:
    check(lo_by_needs[needs].get("calibrated_by") == alt_by_needs[needs].get("calibrated_by"),
          f"needs={needs}: line_options and alternatives agree on calibration provenance "
          "for the identical underlying threshold",
          str((lo_by_needs[needs].get("calibrated_by"), alt_by_needs[needs].get("calibrated_by"))))


head("13. NON-STRIKEOUT MARKET UNAFFECTED: total_bases alternatives (built by "
     "_batter_options, which already stamped 'stat' correctly) are untouched by this fix")

tb_opts = [{"stat": "total_bases", "needs": 2, "line": 1.5, "prob": 0.55,
            "base_rate": 0.45, "lift": 0.10, "basis": "empirical"}]
c_tb = {"projection": {"stat": "total_bases"}, "hit_probability": 0.70, "base_rate": 0.50,
        "alternatives": tb_opts}
out_tb = gp.apply_calibration([c_tb], ({"total_bases": lambda p: p * 0.9}, None))
check(out_tb[0]["alternatives"][0].get("stat") == "total_bases",
      "a non-strikeout market's alternatives keep their own correct stat, unaffected by "
      "the strikeouts-branch edit")
check(out_tb[0]["alternatives"][0].get("calibrated_by") == "total_bases",
      "a non-strikeout market's alternatives calibrate exactly as before this fix")


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

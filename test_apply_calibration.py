#!/usr/bin/env python3
"""test_apply_calibration.py — coverage for generate_picks.apply_
calibration(), the last step before a candidate's hit_probability ships to
the board. Had zero test coverage despite being the final transform on the
single most consequential number in the whole pipeline (what the board
ranks and prices every pick by).

    /tmp/mlbvenv/bin/python3 test_apply_calibration.py
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


def cand(stat, hit_probability=0.65, base_rate=0.55, **over):
    c = {"projection": {"stat": stat}, "hit_probability": hit_probability, "base_rate": base_rate}
    c.update(over)
    return c


head("1. calibrator=None returns the candidates completely unchanged")

cands = [cand("hits")]
out = gp.apply_calibration(cands, None)
check(out is cands, "with no calibrator, the exact same list object is returned")
check("raw_hit_probability" not in out[0] and "calibrated_by" not in out[0],
      "no calibration bookkeeping fields are added when calibrator is None")
check(out[0]["hit_probability"] == 0.65, "hit_probability is untouched")

head("2. a candidate with hit_probability=None is skipped, not calibrated")

cands = [cand("hits", hit_probability=None)]
out = gp.apply_calibration(cands, ({}, lambda p: p + 0.5))
check(out[0]["hit_probability"] is None, "a None hit_probability stays None -- never fed to a "
      "calibration function that could turn it into a fabricated number")
check("raw_hit_probability" not in out[0],
      "no raw_hit_probability is stamped for a candidate that was never actually calibrated")

head("3. a market with its own fitted curve uses it, not the pooled fallback")

per_market = {"hits": lambda p: p * 0.8, "strikeouts": lambda p: p * 0.9}
glob = lambda p: p * 0.5  # deliberately very different, to prove per-market wins
cands = [cand("hits", hit_probability=0.70, base_rate=0.50)]
out = gp.apply_calibration(cands, (per_market, glob))
check(abs(out[0]["hit_probability"] - 0.56) < 1e-9,
      "hits uses its own fitted curve (0.70*0.8=0.56), not the pooled fallback (0.70*0.5=0.35)",
      f"got {out[0]['hit_probability']}")
check(out[0]["calibrated_by"] == "hits", "calibrated_by correctly names the per-market curve used")
check(out[0]["raw_hit_probability"] == 0.70, "the original, uncalibrated probability is preserved "
      "alongside the calibrated one")

head("4. a market with NO fitted curve falls back to the pooled global curve")

cands = [cand("total_bases", hit_probability=0.60, base_rate=0.40)]
out = gp.apply_calibration(cands, (per_market, glob))
check(abs(out[0]["hit_probability"] - 0.30) < 1e-9,
      "total_bases (no per-market curve) falls back to the pooled curve (0.60*0.5=0.30)",
      f"got {out[0]['hit_probability']}")
check(out[0]["calibrated_by"] == "pooled", "calibrated_by correctly reports 'pooled' for the fallback")

head("5. no per-market curve AND no global curve (glob=None) leaves the candidate uncalibrated")

cands = [cand("total_bases", hit_probability=0.60, base_rate=0.40)]
out = gp.apply_calibration(cands, ({}, None))
check(out[0]["hit_probability"] == 0.60, "with glob=None and no matching per-market curve, "
      "the original probability is left completely untouched")
check("calibrated_by" not in out[0],
      "no calibrated_by is stamped when nothing was actually applied")

head("6. lift moves WITH the calibrated probability, so the two never disagree")

cands = [cand("hits", hit_probability=0.70, base_rate=0.50)]
out = gp.apply_calibration(cands, (per_market, glob))
check(abs(out[0]["lift"] - (out[0]["hit_probability"] - 0.50)) < 1e-9,
      "lift is recomputed against the CALIBRATED probability, not left stale against the raw one",
      f"got lift={out[0]['lift']} hit_probability={out[0]['hit_probability']} base_rate=0.50")

head("7. base_rate=None leaves lift untouched (never computed against a missing base)")

cands = [cand("hits", hit_probability=0.70, base_rate=None, lift=None)]
out = gp.apply_calibration(cands, (per_market, glob))
check(out[0]["lift"] is None, "with no base_rate, lift is never recomputed (stays None, "
      "not a garbage subtraction against None)")

head("8. a calibration function that raises is skipped for that candidate, not fatal to the run")

def _boom(p):
    raise ValueError("bad curve")

cands = [cand("hits", hit_probability=0.70, base_rate=0.50), cand("total_bases", hit_probability=0.55)]
out = gp.apply_calibration(cands, ({"hits": _boom}, glob))
check(out[0]["hit_probability"] == 0.70,
      "a per-market curve that raises leaves that candidate's probability untouched, "
      "rather than crashing the whole calibration pass")
check(abs(out[1]["hit_probability"] - 0.275) < 1e-9,
      "the OTHER candidate in the same batch is still calibrated normally",
      f"got {out[1]['hit_probability']}")

head("9. mutates and returns the same list object (matches how the pipeline chains calls)")

cands = [cand("hits")]
out = gp.apply_calibration(cands, (per_market, glob))
check(out is cands, "apply_calibration returns the same list it was given, mutated in place")

head("10. an empty candidate list returns cleanly")

check(gp.apply_calibration([], (per_market, glob)) == [], "an empty list returns an empty list")

head("11. 2026-08-18 Pre-Phase-V finding A4: every line_options entry is calibrated against "
     "its OWN stat, never the candidate's primary stat -- a Home Runs alternate on a "
     "Hits-primary candidate must use the Home Runs curve")

per_market_multi = {"hits": lambda p: p * 0.8, "home_runs": lambda p: p * 0.6}
glob_multi = lambda p: p * 0.5
c = cand("hits", hit_probability=0.70, base_rate=0.50)
c["line_options"] = [
    {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.70, "base_rate": 0.50, "lift": 0.20, "basis": "empirical"},
    {"stat": "home_runs", "needs": 1, "line": 0.5, "prob": 0.20, "base_rate": 0.10, "lift": 0.10, "basis": "empirical"},
    {"stat": "total_bases", "needs": 2, "line": 1.5, "prob": 0.55, "base_rate": 0.45, "lift": 0.10, "basis": "empirical"},
]
out = gp.apply_calibration([c], (per_market_multi, glob_multi))
opts_by_stat = {o["stat"]: o for o in out[0]["line_options"]}

check(abs(opts_by_stat["hits"]["prob"] - 0.56) < 1e-9,
      "the hits alternate line uses the hits curve (0.70*0.8=0.56), matching the primary line's "
      "own treatment for the same stat", f"got {opts_by_stat['hits']['prob']}")
check(abs(opts_by_stat["home_runs"]["prob"] - 0.12) < 1e-9,
      "the home_runs alternate line uses the HOME_RUNS curve (0.20*0.6=0.12), NOT the hits curve "
      "(which would give 0.16) and NOT the candidate's own primary stat", f"got {opts_by_stat['home_runs']['prob']}")
check(abs(opts_by_stat["total_bases"]["prob"] - 0.275) < 1e-9,
      "the total_bases alternate line (no per-market curve) correctly falls back to the pooled "
      "curve (0.55*0.5=0.275), independently of the other two options",
      f"got {opts_by_stat['total_bases']['prob']}")

head("12. raw_prob is preserved per-option, and calibrated_by is market-specific -- never "
     "borrowed from the primary line or another option")

check(opts_by_stat["hits"]["raw_prob"] == 0.70 and opts_by_stat["hits"]["calibrated_by"] == "hits",
      "the hits option's own raw value and calibrated_by are correct and self-consistent",
      str(opts_by_stat["hits"]))
check(opts_by_stat["home_runs"]["raw_prob"] == 0.20 and opts_by_stat["home_runs"]["calibrated_by"] == "home_runs",
      "the home_runs option's raw value and calibrated_by are its OWN, not the hits primary "
      "line's (0.70/'hits')", str(opts_by_stat["home_runs"]))
check(opts_by_stat["total_bases"]["raw_prob"] == 0.55 and opts_by_stat["total_bases"]["calibrated_by"] == "pooled",
      "the total_bases option correctly reports 'pooled', distinct from the two per-market options",
      str(opts_by_stat["total_bases"]))
check(out[0]["raw_hit_probability"] == 0.70 and out[0]["calibrated_by"] == "hits",
      "the PRIMARY line's own provenance is unaffected by calibrating its line_options siblings",
      str({k: out[0][k] for k in ("raw_hit_probability", "calibrated_by")}))

head("13. lift is recomputed per-option from the calibrated probability, exactly matching "
     "the primary line's own treatment")

check(abs(opts_by_stat["hits"]["lift"] - (0.56 - 0.50)) < 1e-9,
      "the hits option's lift moves with its own calibrated probability, not left stale "
      "against the raw 0.70", f"got {opts_by_stat['hits']['lift']}")
check(abs(opts_by_stat["home_runs"]["lift"] - (0.12 - 0.10)) < 1e-9,
      "the home_runs option's lift is independently recomputed from ITS OWN calibrated "
      "probability and base_rate", f"got {opts_by_stat['home_runs']['lift']}")

head("14. an option with no applicable calibrator (no per-market fit, no pooled fallback) "
     "stays explicitly, honestly uncalibrated -- never invented")

c2 = cand("hits", hit_probability=0.70, base_rate=0.50)
c2["line_options"] = [
    {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.70, "base_rate": 0.50, "lift": 0.20, "basis": "empirical"},
    {"stat": "runs", "needs": 1, "line": 0.5, "prob": 0.40, "base_rate": 0.30, "lift": 0.10, "basis": "empirical"},
]
out2 = gp.apply_calibration([c2], ({"hits": lambda p: p * 0.8}, None))  # no pooled fallback
opt_runs = next(o for o in out2[0]["line_options"] if o["stat"] == "runs")
check(opt_runs["prob"] == 0.40, "with no per-market curve for 'runs' and no pooled fallback, "
      "the option's raw probability is left completely untouched")
check("raw_prob" not in opt_runs and "calibrated_by" not in opt_runs,
      "no calibration bookkeeping is stamped on an option that was never actually calibrated -- "
      "truthful absence, not a fabricated 'calibrated_by: none' or similar")
check(opt_runs["lift"] == 0.10, "lift is also left untouched when no calibration was applied "
      "to this option")

head("15. an option that raises during calibration is skipped for THAT option only, not fatal "
     "to the candidate or the rest of its line_options")

def _boom_opt(p):
    raise ValueError("bad curve")

c3 = cand("hits", hit_probability=0.70, base_rate=0.50)
c3["line_options"] = [
    {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.70, "base_rate": 0.50, "lift": 0.20, "basis": "empirical"},
    {"stat": "total_bases", "needs": 2, "line": 1.5, "prob": 0.55, "base_rate": 0.45, "lift": 0.10, "basis": "empirical"},
]
out3 = gp.apply_calibration([c3], ({"total_bases": _boom_opt}, glob_multi))
opts3 = {o["stat"]: o for o in out3[0]["line_options"]}
check(opts3["total_bases"]["prob"] == 0.55,
      "the option whose curve raises keeps its raw probability, not a crash or a fabricated value")
check(abs(opts3["hits"]["prob"] - 0.35) < 1e-9,
      "the OTHER option in the same candidate's line_options is still calibrated normally "
      "(0.70*0.5=0.35, pooled since no per-market 'hits' curve was given here)",
      f"got {opts3['hits']['prob']}")

head("16. a candidate with no line_options at all (pitcher/game/no alternates) is unaffected -- "
     "the new per-option loop must not require the key to exist")

c4 = cand("strikeouts", hit_probability=0.65, base_rate=0.50)
out4 = gp.apply_calibration([c4], (per_market_multi, glob_multi))
check("line_options" not in out4[0] or not out4[0].get("line_options"),
      "a candidate with no line_options is calibrated normally on its primary line with no error")
check(abs(out4[0]["hit_probability"] - 0.325) < 1e-9,
      "primary-line calibration for a candidate with no line_options is unaffected",
      f"got {out4[0]['hit_probability']}")

head("17. calibration never touches, invents, or widens a line_options entry's ci -- CI "
     "semantics are entirely orthogonal to whether a calibrator exists for that market")

c5 = cand("hits", hit_probability=0.70, base_rate=0.50)
c5["line_options"] = [
    {"stat": "hits", "needs": 1, "line": 0.5, "prob": 0.70, "base_rate": 0.50, "lift": 0.20,
     "basis": "empirical", "ci": [0.63, 0.77]},
    {"stat": "home_runs", "needs": 1, "line": 0.5, "prob": 0.20, "base_rate": 0.10, "lift": 0.10,
     "basis": "modelled_shrunk", "ci": None},
]
out5 = gp.apply_calibration([c5], (per_market_multi, glob_multi))
opts5 = {o["stat"]: o for o in out5[0]["line_options"]}
check(opts5["hits"]["ci"] == [0.63, 0.77],
      "a real, defensible ci is left exactly as computed -- calibration changes the "
      "probability it describes, not the interval itself", str(opts5["hits"]["ci"]))
check(opts5["home_runs"]["ci"] is None,
      "an honestly-absent ci (modelled_shrunk basis) stays None even though this option WAS "
      "successfully calibrated (home_runs has a real per-market curve here) -- calibration "
      "existing is not license to invent a CI that was never defensible", str(opts5["home_runs"]))
check(opts5["home_runs"]["prob"] == 0.12,
      "sanity: the home_runs option really was calibrated (0.20*0.6=0.12) even though its ci "
      "correctly remains None", f"got {opts5['home_runs']['prob']}")

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

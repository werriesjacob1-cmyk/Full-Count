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

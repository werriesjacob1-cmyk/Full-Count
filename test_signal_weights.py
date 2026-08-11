#!/usr/bin/env python3
"""test_signal_weights.py — checks load_signal_trust()/apply_signal_weights()
in generate_picks.py, the continuous signal-weighting system that replaced
measure_signals.py's old binary n>=100 gate. Direct request: every signal
should nudge the score in proportion to its measured sample size and effect
size, never sit at exactly zero just because it hasn't crossed an arbitrary
n line, and a signal measured running BACKWARDS should push the opposite
way from its own formula's original intent.

    /tmp/mlbvenv/bin/python3 test_signal_weights.py
    python3 test_signal_weights.py -v
"""
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import generate_picks as gp

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


def measurement_row(signal, auc, n, se):
    return {"signal": signal, "n": n, "auc": auc, "se": se}


def write_table(rows):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"dates": ["2026-08-07"], "n_graded": 1, "signals": rows}, f)
    f.close()
    return f.name


def cand(score, signals, reliability="A"):
    return {"score": score, "signals": dict(signals), "reliability": reliability}


head("1. load_signal_trust: a strong, well-measured signal earns near-full trust")

path = write_table([
    measurement_row("hard_hit_rate", auc=0.677, n=245, se=0.0429),   # real, per measure_signals.py's own output
    measurement_row("coinflip_signal", auc=0.500, n=500, se=0.02),   # lots of data, genuinely no effect
    measurement_row("thin_signal", auc=0.70, n=8, se=0.30),          # promising point estimate, tiny n
    measurement_row("backwards_signal", auc=0.30, n=300, se=0.03),   # well-measured, runs the WRONG way
])
trust = gp.load_signal_trust(path)

check(trust["hard_hit_rate"] > 0.5, "a real, well-measured signal (AUC .677, n=245) earns strong positive trust",
      f"trust={trust['hard_hit_rate']:.3f}")
check(abs(trust["coinflip_signal"]) < 0.05,
      "a signal measured at exactly 0.5 AUC earns ~zero trust NO MATTER how much data backs it (n=500)",
      f"trust={trust['coinflip_signal']:.4f}")
check(0 < trust["thin_signal"] < trust["hard_hit_rate"],
      "a thin sample (n=8) with a promising point estimate earns SOME trust, but less than a proven one",
      f"thin={trust['thin_signal']:.3f} vs proven={trust['hard_hit_rate']:.3f}")
check(trust["backwards_signal"] < 0,
      "a signal measured running backwards (AUC .30) earns NEGATIVE trust -- applied opposite its own formula's intent",
      f"trust={trust['backwards_signal']:.3f}")
os.unlink(path)

head("2. load_signal_trust: missing file / missing signal degrades to no information, not a crash")

check(gp.load_signal_trust("/tmp/definitely_does_not_exist_xyz.json") == {},
      "a missing measurement file returns an empty table rather than raising")
empty_path = write_table([])
check(gp.load_signal_trust(empty_path) == {}, "a measurement file with no signals returns an empty table")
os.unlink(empty_path)

head("3. apply_signal_weights: nothing sits at exactly zero just because n < 100 -- but real zero-effect signals do")

path = write_table([
    measurement_row("thin_signal", auc=0.70, n=8, se=0.30),
    measurement_row("coinflip_signal", auc=0.500, n=500, se=0.02),
])
trust = gp.load_signal_trust(path)
c = cand(score=50, signals={"thin_signal": 4.0, "coinflip_signal": 4.0})
gp.apply_signal_weights([c], trust=trust)
check(c["score"] != 50, "a thin-but-promising signal (n=8) DOES move the score -- not held to zero by a hard n>=100 gate",
      f"score={c['score']}")
check("signal_weight_adjustment" in c, "the applied adjustment is recorded on the candidate, not silent")
os.unlink(path)

c2 = cand(score=50, signals={"coinflip_signal": 4.0})
gp.apply_signal_weights([c2], trust=gp.load_signal_trust(write_table([
    measurement_row("coinflip_signal", auc=0.500, n=5000, se=0.01)])))
check(c2["score"] == 50, "a signal proven to have NO effect, even with a huge sample, correctly contributes nothing",
      f"score={c2['score']}")

head("4. apply_signal_weights: a backwards-measured signal pushes the OPPOSITE direction from its raw delta")

path = write_table([measurement_row("backwards_signal", auc=0.25, n=400, se=0.025)])
trust = gp.load_signal_trust(path)
c3 = cand(score=50, signals={"backwards_signal": 4.0})   # raw delta says "push score UP"
gp.apply_signal_weights([c3], trust=trust)
check(c3["score"] < 50,
      "a signal measured running backwards LOWERS the score even though its own raw delta was positive",
      f"score={c3['score']}")
os.unlink(path)

head("5. apply_signal_weights: total adjustment is bounded, and a thin-sample pick can't be pushed to High off signals alone")

path = write_table([measurement_row(f"sig{i}", auc=0.95, n=10000, se=0.005) for i in range(20)])
trust = gp.load_signal_trust(path)
c4 = cand(score=40, signals={f"sig{i}": 6.0 for i in range(20)}, reliability="D")
gp.apply_signal_weights([c4], trust=trust)
check(c4["score"] <= 40 + gp.MAX_SIGNAL_WEIGHT_ADJUSTMENT + 0.01,
      "twenty simultaneously-firing strong signals still can't exceed the total adjustment cap",
      f"score={c4['score']} (cap={gp.MAX_SIGNAL_WEIGHT_ADJUSTMENT})")
check(c4["confidence"] != "High",
      "a reliability-D (thin sample) pick can't be promoted to High confidence by signal weighting alone",
      f"confidence={c4['confidence']} score={c4['score']}")
os.unlink(path)

c5 = cand(score=65, signals={"sig0": 6.0}, reliability="A")
gp.apply_signal_weights([c5], trust=gp.load_signal_trust(write_table(
    [measurement_row("sig0", auc=0.95, n=10000, se=0.005)])))
check(c5["score"] >= 70 and c5["confidence"] == "High",
      "a reliability-A pick CAN be pushed to High confidence by a strong, well-measured signal",
      f"score={c5['score']} confidence={c5['confidence']}")

head("6. apply_signal_weights: a candidate with no recorded signals is left untouched")

c6 = cand(score=55, signals={})
gp.apply_signal_weights([c6], trust=gp.load_signal_trust(write_table(
    [measurement_row("sig0", auc=0.95, n=10000, se=0.005)])))
check(c6["score"] == 55 and "signal_weight_adjustment" not in c6,
      "a candidate with an empty signals dict is untouched, not crashed on or given a phantom adjustment")

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

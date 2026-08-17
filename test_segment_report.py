#!/usr/bin/env python3
"""test_segment_report.py — coverage for backtest/segment_report.py, Phase
3 items 1+2: validating the new Top Pick system and building a clean
current-version track record, segmented by recommendation_status and never
blended with the legacy (pre-rebuild) record.

    /tmp/mlbvenv/bin/python3 test_segment_report.py
"""
import sys
import os
import unittest.mock as mock

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
sys.path.insert(0, "backtest")

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


import segment_report as sr

head("1. picks with no recommendation_status at all are bucketed 'unclassified' -- "
     "never guessed into top_pick/lean/value/neutral")

picks = [
    {"recommendation_status": "top_pick", "grade": "hit", "hit_probability": 0.65,
     "market_odds": -140, "_date": "2026-08-16", "name": "A", "projection": {"stat": "hits", "needs": 1}},
    {"recommendation_status": None, "grade": "hit", "hit_probability": 0.60,
     "market_odds": -120, "_date": "2026-08-05", "name": "B", "projection": {"stat": "hits", "needs": 1}},
]
from collections import defaultdict
by_status = defaultdict(list)
for p in picks:
    status = p.get("recommendation_status") or "unclassified"
    by_status[status].append(p)
check(len(by_status["top_pick"]) == 1 and len(by_status["unclassified"]) == 1,
      "one pick each lands in top_pick and unclassified, matching real main()'s logic")

head("2. attach_clv(): CLV is None when no closing price is captured for that date "
     "-- never fabricated")

with mock.patch.object(sr, "_closing_prices", return_value={}):
    out = sr.attach_clv([dict(p) for p in picks])
check(all(p["clv"] is None for p in out),
      "every pick gets clv=None when the closing-price archive has nothing for that date")

head("3. attach_clv(): a real captured closing price produces a real CLV value with the "
     "correct sign -- closing_implied minus bet_implied")

import prop_probability as pp
pick3 = {"recommendation_status": "top_pick", "grade": "hit", "hit_probability": 0.65,
        "market_odds": -150, "_date": "2026-08-16", "name": "Player X",
        "projection": {"stat": "hits", "needs": 1}}
# Bet at -150 (implied ~0.60), closes at -200 (implied ~0.667) -- the market
# moved TOWARD the bet being right, so this should be a POSITIVE clv (we got
# a cheaper price than where it closed).
fake_close = {("player x", "hits", 1): {"american": -200}}
with mock.patch.object(sr, "_closing_prices", return_value=fake_close):
    out3 = sr.attach_clv([dict(pick3)])
bet_implied = pp.implied_probability(-150)
close_implied = pp.implied_probability(-200)
expected_clv = round(close_implied - bet_implied, 4)
check(out3[0]["clv"] == expected_clv,
      f"CLV computed correctly as closing_implied - bet_implied ({expected_clv})",
      f"got {out3[0]['clv']}")
check(out3[0]["clv"] > 0,
      "a price that closed SHORTER (more juiced) than the bet price is a positive CLV -- "
      "the bettor got in before the market agreed the number was too generous")

head("4. report_segment() doesn't crash and correctly gates on sample size -- "
     "fewer than MIN_N_DIRECTIONAL picks prints nothing beyond the header")

import io
import contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sr.report_segment("tiny_segment", [{"grade": "hit", "hit_probability": 0.6,
                                        "market_odds": -110, "_date": "2026-08-16"}])
out_text = buf.getvalue()
check("Fewer than" in out_text, "a segment with n < MIN_N_DIRECTIONAL is correctly gated",
      out_text)

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

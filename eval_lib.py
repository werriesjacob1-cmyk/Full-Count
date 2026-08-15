#!/usr/bin/env python3
"""eval_lib.py — shared statistical primitives for Phase 3's evaluation
framework: market_benchmark.py, calibration_audit.py, threshold_
sensitivity.py, model_health_report.py, and challenger.py all import from
here instead of five slightly-different reimplementations of Brier score
drifting apart over time. This is deliberately a library, not a script --
no argparse, no __main__, nothing here fetches data or hits the network.

WHY market_probability() IS THE MOST IMPORTANT FUNCTION HERE.

Phase 3 item 5 found a real, concrete gap: for the two-sided markets
(pitcher strikeouts, pitcher outs, nrfi_combined), odds_fanduel.py already
computes an EXACT no-vig probability via prop_probability.devig_two_sided()
at pick time and persists it as market_hold + market_implied -- but every
prior analysis script re-derived a WORSE approximation from market_odds
alone (an assumed 8% one-sided hold), throwing the exact number away. This
function is the one place that decision gets made correctly: use the
exact number when market_hold proves one was actually measured, fall back
to the assumed-hold approximation only when it wasn't, and never confuse
the two kinds of estimate with each other in a result the caller can't
tell apart.

    from eval_lib import brier, log_loss, calibration_table, market_probability
"""
import glob
import json
import math
import os

import prop_probability as pp

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


# ══════════════════════════════════════════════════════════════════════════
#  SCORING RULES
# ══════════════════════════════════════════════════════════════════════════

def brier(probs_outcomes):
    """Mean squared error of a probability against a real 0/1 outcome.
    Lower is better. 0.25 is what a coin flip scores against a 50/50 event;
    a well-calibrated, informative model scores meaningfully below the
    pooled base-rate-only Brier score for the same population."""
    if not probs_outcomes:
        return None
    return sum((p - o) ** 2 for p, o in probs_outcomes) / len(probs_outcomes)


def log_loss(probs_outcomes, eps=1e-6):
    """Lower is better; penalizes a confident WRONG call far harder than
    Brier does. Clamped away from exactly 0/1 so one lucky/unlucky
    near-certain pick can't produce an infinite score."""
    if not probs_outcomes:
        return None
    total = 0.0
    for p, o in probs_outcomes:
        p = min(max(p, eps), 1 - eps)
        total += -(o * math.log(p) + (1 - o) * math.log(1 - p))
    return total / len(probs_outcomes)


def calibration_table(probs_outcomes, buckets=(0.0, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 1.01)):
    """Predicted vs actual hit rate per probability bucket, with n, so a
    caller can see AND gate on sample size per bucket rather than just
    reading a single pooled number that hides which buckets are thin.
    Default buckets match Phase 3 item 4's explicit request: 50-55, 55-60,
    60-65, 65-70, 70-75, 75+ (plus a below-50% catch-all)."""
    rows = []
    for lo, hi in zip(buckets, buckets[1:]):
        in_bucket = [(p, o) for p, o in probs_outcomes if lo <= p < hi]
        if not in_bucket:
            continue
        n = len(in_bucket)
        pred = sum(p for p, _ in in_bucket) / n
        actual = sum(o for _, o in in_bucket) / n
        rows.append({
            "range": f"{lo:.2f}-{hi:.2f}", "n": n,
            "predicted": round(pred, 4), "actual": round(actual, 4),
            "gap": round(actual - pred, 4),
            "brier": round(brier(in_bucket), 4),
            "log_loss": round(log_loss(in_bucket), 4),
        })
    return rows


# ══════════════════════════════════════════════════════════════════════════
#  THE MARKET'S OWN PROBABILITY -- EXACT WHERE POSSIBLE, LABELLED WHEN NOT
# ══════════════════════════════════════════════════════════════════════════

def market_probability(pick):
    """The best available no-vig market probability for one graded pick,
    plus HOW GOOD that estimate actually is -- never blur the two.

    Returns (prob, exact: bool) or (None, None) if the pick carries no
    usable market price at all.

    - market_hold present (pitcher strikeouts / pitcher_outs / nrfi_combined
      -- see odds_fanduel.attach_market_prices) means BOTH sides of that
      exact market were quoted and de-vigged exactly; market_implied IS the
      true no-vig probability already. exact=True.
    - market_hold absent but market_odds present (every one-sided batter
      YES/NO prop -- hits/total_bases/home_runs/RBIs/runs/stolen_base/
      singles/doubles/triples/hits_runs_rbis/combined_strikeouts/lasers/
      moonshots) means FanDuel structurally does not post a second side to
      devig against at all (verified live 2026-08-16: every one of these
      market types returns one runner per PLAYER, not two runners for two
      sides of one player's line -- there is nothing missing to capture).
      The assumed ASSUMED_PROP_HOLD is applied here as a labelled
      approximation. exact=False.
    - Neither field present: no market read for this pick. (None, None).
    """
    odds = pick.get("market_odds")
    if odds is None:
        return None, None
    if pick.get("market_hold") is not None:
        implied = pick.get("market_implied")
        if implied is None:
            return None, None
        return float(implied), True
    implied = pick.get("market_implied")
    if implied is None:
        implied = pp.implied_probability(odds)
    if implied is None:
        return None, None
    return pp.devig(float(implied)), False


# ══════════════════════════════════════════════════════════════════════════
#  REALIZED (NOT EXPECTED) ROI -- same flat-1-unit convention grade_value.py
#  already uses, reused rather than reinvented.
# ══════════════════════════════════════════════════════════════════════════

def realized_roi(picks):
    """Flat 1-unit-per-pick realized ROI over already-graded (hit/miss),
    market-priced picks. Returns a dict with staked/returned/roi/units, or
    all-None fields if nothing in the list qualifies (no market_odds, or no
    real hit/miss grade)."""
    staked = 0.0
    returned = 0.0
    n = 0
    for p in picks:
        odds = p.get("market_odds")
        grade = p.get("grade")
        if odds is None or grade not in ("hit", "miss"):
            continue
        n += 1
        staked += 1.0
        if grade == "hit":
            returned += pp.decimal_odds(odds)
    if n == 0:
        return {"n": 0, "staked": None, "returned": None, "roi": None, "units": None}
    return {
        "n": n, "staked": staked, "returned": round(returned, 4),
        "roi": round((returned - staked) / staked, 4),
        "units": round(returned - staked, 4),
    }


# ══════════════════════════════════════════════════════════════════════════
#  LOADING GRADED PICKS -- the one shared reader every Phase 3 script uses
# ══════════════════════════════════════════════════════════════════════════

def load_graded_picks(start_date=None, end_date=None, results_dir=None,
                      include_shadow=False):
    """Every graded (hit/miss/ungraded) pick across results/grades_*.json in
    [start_date, end_date] (inclusive, "YYYY-MM-DD" strings; either bound
    may be None for open-ended), flattened into one list with the source
    date attached. This is deliberately unfiltered (includes ungraded rows,
    picks with no market price, every recommendation_status including
    "unclassified" legacy picks) -- callers filter for their own question
    rather than this function silently deciding what counts."""
    rdir = results_dir or RESULTS_DIR
    out = []
    for path in sorted(glob.glob(os.path.join(rdir, "grades_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        date = d.get("date")
        if start_date and (not date or date < start_date):
            continue
        if end_date and (not date or date > end_date):
            continue
        for p in d.get("picks", []):
            out.append({**p, "_date": date})
        if include_shadow:
            for p in d.get("shadow_tracking", []):
                out.append({**p, "_date": date, "_shadow": True})
    return out


def graded_only(picks):
    """picks with a real hit/miss grade -- drops ungraded rows (game not
    final, missing ids, grader error) that would otherwise silently corrupt
    a hit-rate/Brier/ROI computation."""
    return [p for p in picks if p.get("grade") in ("hit", "miss")]


def priced_only(picks):
    """picks that carry a real posted market price -- the precondition for
    ROI/market-comparison/CLV work, never assumed."""
    return [p for p in picks if p.get("market_odds") is not None]


# ══════════════════════════════════════════════════════════════════════════
#  SAMPLE-SIZE HONESTY -- one shared gate so "too small to say anything" is
#  applied the same way (same floor, same wording) everywhere in Phase 3.
# ══════════════════════════════════════════════════════════════════════════

MIN_N_DIRECTIONAL = 5     # below this: don't even compute a rate, pure noise
MIN_N_REPORTABLE = 20     # below this: report the number but label it thin
MIN_N_CONFIDENT = 100     # below this: never call a result "confirmed"


def sample_size_label(n):
    if n < MIN_N_DIRECTIONAL:
        return "insufficient"
    if n < MIN_N_REPORTABLE:
        return "thin"
    if n < MIN_N_CONFIDENT:
        return "directional"
    return "reportable"

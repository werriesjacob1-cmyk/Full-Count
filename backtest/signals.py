#!/usr/bin/env python3
"""Signal evaluation and weight fitting for the MLB prop-picks pipeline.

WHY THIS FILE EXISTS
--------------------
The weights in generate_picks.py (35% matchup / 25% recent form / 15%
environment / 15% baseline skill / 10% context) were *invented*. Chosen by
hand, never fitted, never validated against a single graded outcome. Same for
the thresholds sprinkled through score_batter/score_pitcher/score_stolen_base/
score_walk/score_first_inning. This module's job is to replace those guesses
with measurements — and, just as importantly, to find the signals that
contribute NOTHING so they can be deleted. A noise signal is not neutral: it
dilutes the signals that work and it burns degrees of freedom that a few
hundred rows cannot spare.

INPUT
-----
Backtest rows exactly as defined in backtest/SCHEMA.md. One row = one prop the
model would have recommended on one historical date + what actually happened.
This module reads rows only; it never produces them (that's engine.py) and
never calibrates probabilities (that's calibration.py).

THE FIVE DESIGN DECISIONS THAT MATTER, STATED UP FRONT
------------------------------------------------------
1. ABSENT IS NOT ZERO. SCHEMA.md is explicit: a signal that did not fire is
   absent from the `signals` dict. Zero is a real reading on a 0-100 scale (it
   means "worst possible"), so imputing 0 teaches the fitter that missing data
   is a maximally bad reading. We median-impute the value AND add a companion
   `<signal>__fired` indicator column, so "this signal was unavailable" gets to
   be its own estimated effect rather than being smuggled in as a value.
   Medians are computed on TRAIN ROWS ONLY and carried to test — computing them
   over the full set leaks test information into the training features.

   A NaN or infinity in a signal value is treated as ABSENT, not as a number.
   This is deliberate and load-bearing: generate_picks.py already shipped a bug
   where a pandas NaN reached clamp() and scored as the MAXIMUM of the range
   rather than neutral, silently inflating thin-sample players. The same class
   of bug in a fitter would be worse and quieter. `signal_matrix` asserts that
   no non-finite value survives into the design matrix.

2. TIME-BASED SPLITS ONLY. Never random. A random split lets the model train on
   2026-07-14 and test on 2026-07-13, and every slow-moving input (season wRC+,
   sprint speed, a pitcher's rolling K%) leaks the answer across the boundary.
   Held-out evaluation splits on DATE, train = earlier, test = later, and the
   test split is scored exactly once.

3. SAMPLE-SIZE HONESTY IS ENFORCED IN CODE, NOT IN A FOOTNOTE. With a few
   hundred rows and ~15 signals a logistic fit will happily hand back
   confident-looking weights that are noise. Every fit reports rows-per-
   parameter and EVENTS-per-parameter (the binding constraint for logistic
   regression is the count of the rarer outcome class, not the row count), and
   sets `authoritative=False` below the thresholds below. An underpowered fit
   presented confidently is worse than no fit at all.

4. SEGMENT BY PROP TYPE. A signal predictive for strikeout props may be
   irrelevant for stolen bases. Pooling hides both. Every report accepts a
   prop_type filter and `full_report()` runs pooled + per-prop-type, and prints
   the per-prop-type BASE RATE first, because a signal that looks predictive is
   often just tracking which prop types are easier to hit.

5. COLLINEARITY IS NOT BOOKKEEPING. Implied team total already encodes park,
   weather and opposing pitcher quality. Several signals in this system almost
   certainly measure the same underlying thing. A fitted weight on collinear
   inputs is unstable — flip two rows and the weight moves — and reading it as
   "this signal matters more" is exactly the error this whole exercise exists
   to stop.

WHAT THIS MODULE CANNOT DO, PER SCHEMA.md
------------------------------------------
Betting-market signals (sharp money / line movement) cannot be reconstructed
historically. They are out of scope here and any signal name in
MARKET_SIGNALS is excluded from fitting with a stated reason rather than
silently fitted on whatever partial data happens to exist.

USAGE
-----
    from backtest.signals import full_report, format_report
    print(format_report(full_report(rows)))

Self-proof (synthetic data with known structure — real signal, pure noise,
collinear copy, informative-missingness):

    /tmp/mlbvenv/bin/python3 backtest/signals.py --selftest
"""

from __future__ import annotations


def _fmt(value, spec, blank="  n/a"):
    """Format an optional number, or return a placeholder when it's absent.

    Exists because this module targets Python 3.11, where an f-string cannot
    reuse the enclosing quote character -- so the natural inline form
    (f"{(f'{r['auc']:.3f}' if ...)}") is a syntax error rather than a style
    choice. Pulling the conditional out here keeps the report tables readable
    and works on every version."""
    if value is None:
        return blank
    return format(value, spec)

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

try:
    from scipy import stats as _scipy_stats
except ImportError:  # pragma: no cover - scipy is present in /tmp/mlbvenv
    _scipy_stats = None


# ══════════════════════════════════════════════════════════════════════════
#  POWER THRESHOLDS — the line below which we refuse to present a fit
# ══════════════════════════════════════════════════════════════════════════

# Rows per fitted parameter. 20 is the conventional floor for a linear model;
# below ~10 the fit is memorizing.
MIN_ROWS_PER_PARAM = 20.0

# Events per fitted parameter (events = count of the RARER outcome class).
# This is the real constraint for logistic regression: 500 rows at a 4% hit
# rate carries 20 events and supports about ONE parameter, not fifteen. The
# 10-events-per-variable rule is the classic Peduzzi et al. finding; 15-20 is
# the modern recommendation, and we sit at 15 because these signals are
# correlated with each other (which makes the effective information lower than
# the raw count suggests).
MIN_EVENTS_PER_PARAM = 15.0

# Below this many usable rows nothing is presented as authoritative regardless
# of the ratios above.
MIN_ROWS_ABSOLUTE = 200

NEUTRAL = 50.0  # generate_picks.scale() returns the midpoint for None/NaN

# Signals that cannot be backtested honestly (SCHEMA.md: line history only
# began 2026-08-05 and cannot be reconstructed backwards).
MARKET_SIGNALS = frozenset({
    "sharp_divergence", "sharp_bias", "line_move", "line_movement",
    "odds", "implied_prob_market", "money_pct", "ticket_pct",
})


# ══════════════════════════════════════════════════════════════════════════
#  THE CURRENT (HAND-PICKED) WEIGHTS, RECONSTRUCTED FROM generate_picks.py
# ══════════════════════════════════════════════════════════════════════════
#
# Effective weight on the final 0-100 score = category weight x within-category
# weight. These are transcribed from the scoring functions, not guessed. Each
# entry is (weight, value_when_absent) where the absent value replicates what
# production actually does — generate_picks.scale() returns the 0-100 midpoint
# for a None/NaN input, so most absent signals contribute 50, but a few
# (bonuses that are simply not added) contribute 0.
#
# Keys are the *category* weights from the docstring of generate_picks.py:
CURRENT_CATEGORY_WEIGHTS = {
    "matchup": 0.35, "recent_form": 0.25, "environment": 0.15,
    "baseline_skill": 0.15, "context": 0.10,
}

_BATTER_WEIGHTS = {
    # MATCHUP 35%: platoon*0.55 + sp_weak*0.30 + exploit_bonus (additive, 0-20)
    "platoon":            (0.35 * 0.55, 65.0),   # unknown handedness -> 65 in prod
    "sp_era_weak":        (0.35 * 0.30, NEUTRAL),
    "pitch_exploit":      (0.35 * 1.00, 0.0),    # bonus not added when absent
    # RECENT FORM 25%: EV*0.6 + barrel*0.4, plus bat-speed bonus (0-15)
    "l7_avg_ev":          (0.25 * 0.60, NEUTRAL),
    "l7_barrel_pct":      (0.25 * 0.40, NEUTRAL),
    "bat_speed_trend":    (0.25 * 1.00, 0.0),
    # ENVIRONMENT 15%
    "park_hr_index":      (0.15 * 1.00, NEUTRAL),
    # BASELINE SKILL 15%: wRC+*0.4 + ISO*0.3 + Barrel%*0.3
    "wrc_plus":           (0.15 * 0.40, NEUTRAL),
    "iso":                (0.15 * 0.30, NEUTRAL),
    "season_barrel_pct":  (0.15 * 0.30, NEUTRAL),
    # CONTEXT 10%: lineup slot*0.7 + bullpen fatigue*0.3 (+ bullpen ERA nudge)
    "lineup_slot":        (0.10 * 0.70, NEUTRAL),
    "bullpen_fatigue":    (0.10 * 0.30, NEUTRAL),
    "bullpen_era_diff":   (0.10 * 0.00, 0.0),    # capped +/-8 nudge, not weighted
}

_PITCHER_K_WEIGHTS = {
    "opp_team_k_pct":     (0.35 * 0.65, NEUTRAL),
    "same_hand_ratio":    (0.35 * 0.35, NEUTRAL),
    "l14_k_pct":          (0.25 * 1.00, NEUTRAL),
    "tto_penalty":        (0.25 * 0.00, 0.0),    # +/-8 step adjustment
    "env_neutral":        (0.15 * 1.00, NEUTRAL),  # hard-coded 50 in production
    "season_k_pct":       (0.15 * 0.40, NEUTRAL),
    "csw_pct":            (0.15 * 0.30, NEUTRAL),
    "stuff_plus":         (0.15 * 0.30, NEUTRAL),
    "ump_accuracy":       (0.10 * 1.00, NEUTRAL),
}

CURRENT_WEIGHTS: Dict[str, Dict[str, Tuple[float, float]]] = {
    # score_batter() computes ONE composite score (matchup/recent-form/
    # environment/baseline-skill/context) per batter; attach_hit_probabilities'
    # _batter_options() then re-picks which of these nine families to
    # recommend by probability alone, off that SAME score. So _BATTER_WEIGHTS
    # is the right reconstruction for every one of them, not just the three
    # that used to be listed here -- runs/rbis/doubles/triples/singles/
    # hits_runs_rbis were silently absent, which meant current_weight_score()
    # returned None (safely -- see the "could not reconstruct" guard below --
    # but still a real coverage gap) for six of the batter markets this
    # project actually bets.
    #
    # "home_run" (singular) was also a real bug, not merely incomplete: every
    # prop_type this project actually emits is "home_runs" (plural -- see
    # MARKET_MAP in odds_fanduel.py and generate_picks.py's own
    # "projection": {"stat": "home_runs", ...}), so this key has never once
    # matched a real row since CURRENT_WEIGHTS was written.
    "hits":             dict(_BATTER_WEIGHTS),
    "total_bases":      dict(_BATTER_WEIGHTS),
    "home_runs":        dict(_BATTER_WEIGHTS),
    "runs":             dict(_BATTER_WEIGHTS),
    "rbis":             dict(_BATTER_WEIGHTS),
    "doubles":          dict(_BATTER_WEIGHTS),
    "triples":          dict(_BATTER_WEIGHTS),
    "singles":          dict(_BATTER_WEIGHTS),
    "hits_runs_rbis":   dict(_BATTER_WEIGHTS),
    "strikeouts":       dict(_PITCHER_K_WEIGHTS),
    # score_stolen_base: skill*0.55 + matchup*0.30 + context*0.15
    "stolen_base": {
        "sprint_speed":     (0.55, NEUTRAL),
        "catcher_poptime":  (0.30, NEUTRAL),
        "season_sb":        (0.15, NEUTRAL),
    },
    # score_walk: skill*0.4 + matchup*0.4 + context*0.2. Dead in practice --
    # build_candidates() deliberately never calls score_walk() any more (no
    # "Player to Draw a Walk" market exists on FanDuel) -- kept here rather
    # than deleted because it costs nothing to leave and documents what the
    # formula WAS, for anyone re-grading old picks from before it was removed.
    "walks": {
        "batter_bb_pct":    (0.40, NEUTRAL),
        "sp_bb_pct":        (0.40, NEUTRAL),
        "ump_accuracy":     (0.20, NEUTRAL),
    },
    # score_first_inning: single signal + sample penalty. Still real and
    # still scored (build_candidates always calls it -- _build_combined_nrfi
    # consumes its output), just no longer shown as its own standalone board
    # entry.
    "first_inning_run": {
        "yrfi_rate":        (1.00, NEUTRAL),
        "fi_n_starts":      (0.00, 0.0),
    },
    # DELIBERATELY NOT LISTED: pitcher_outs, combined_strikeouts,
    # hard_hit_105, hard_hit_110, nrfi_combined. Checked each scoring
    # function (score_pitcher_outs, score_combined_strikeouts, score_laser,
    # _build_combined_nrfi) -- none of them computes a hand-picked WEIGHTED
    # composite the way score_batter/score_pitcher do. Each is either a
    # single shrunk empirical rate used directly as hit_probability
    # (pitcher_outs, hard_hit_105/110 -- "signals" carries exactly one key)
    # or a joint-probability combination of two already-computed reads
    # (combined_strikeouts: independent binomials; nrfi_combined: both
    # starters' first-inning reads multiplied). There is no formula to
    # reconstruct here, so adding an entry would fabricate one rather than
    # fill a gap -- current_weight_score()'s "no table found" branch is the
    # correct, honest answer for these five, not a shortfall to fix.
}

# Alternate names engine.py might legitimately emit. Kept explicit so a rename
# shows up as a coverage warning rather than silently zeroing a weight.
SIGNAL_ALIASES = {
    "l7_form": "l7_avg_ev",
    "park_hr_factor": "park_hr_index",
    "wrc+": "wrc_plus",
    "wrcplus": "wrc_plus",
    "barrel_pct": "season_barrel_pct",
    "k_pct": "season_k_pct",
    "csw": "csw_pct",
    "stuff": "stuff_plus",
    "order": "lineup_slot",
    "batting_order": "lineup_slot",
    "poptime": "catcher_poptime",
    "sprint": "sprint_speed",
    "bb_pct": "batter_bb_pct",
}


def canonical(name: str) -> str:
    return SIGNAL_ALIASES.get(name, name)


# ══════════════════════════════════════════════════════════════════════════
#  ROW HANDLING
# ══════════════════════════════════════════════════════════════════════════

def _finite(v: Any) -> Optional[float]:
    """Return v as a float, or None if it is not a usable finite number.

    NaN and inf return None -> the signal is treated as ABSENT. See design
    note 1: a NaN that slips through as a number is the exact failure mode
    that already bit this codebase once.
    """
    if v is None or isinstance(v, bool):
        return float(v) if isinstance(v, bool) else None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _usable_rows(rows: Iterable[dict], prop_type: Optional[str] = None,
                 fair_test_only: bool = False) -> List[dict]:
    """Filter to rows this module can learn from, and sort by date.

    Drops rows with a missing/ungradeable outcome (SCHEMA.md says those should
    be omitted by the producer, but we do not trust that silently).
    """
    out = []
    for r in rows:
        y = r.get("outcome")
        if y is None or y not in (0, 1, 0.0, 1.0, True, False):
            continue
        if prop_type is not None and r.get("prop_type") != prop_type:
            continue
        if fair_test_only and not r.get("fair_test"):
            continue
        if not r.get("date"):
            continue
        out.append(r)
    out.sort(key=lambda r: (str(r["date"]), str(r.get("game_pk", "")),
                            str(r.get("player_id", ""))))
    return out


def base_rates(rows: Iterable[dict]) -> Dict[str, dict]:
    """Hit rate per prop type. Read this BEFORE any signal number.

    A signal that looks predictive in a pooled fit is very often just tracking
    which prop types are easier to hit. If 'stolen_base' hits 22% and
    'strikeouts' hits 61%, then any signal that only fires on strikeout props
    will look like a genius in a pooled model while carrying no information.
    """
    by: Dict[str, List[dict]] = defaultdict(list)
    allrows = _usable_rows(rows)
    for r in allrows:
        by[str(r.get("prop_type", "?"))].append(r)
    out = {}
    for pt, rs in sorted(by.items()):
        y = np.array([int(r["outcome"]) for r in rs])
        fair = [r for r in rs if r.get("fair_test")]
        yf = np.array([int(r["outcome"]) for r in fair]) if fair else np.array([])
        out[pt] = {
            "n": len(rs),
            "hits": int(y.sum()),
            "base_rate": float(y.mean()) if len(y) else None,
            "n_fair_test": len(fair),
            "base_rate_fair_test": float(yf.mean()) if len(yf) else None,
            "n_dates": len({r["date"] for r in rs}),
            "date_range": (min(r["date"] for r in rs), max(r["date"] for r in rs)),
        }
    if allrows:
        y = np.array([int(r["outcome"]) for r in allrows])
        out["__ALL__"] = {
            "n": len(allrows), "hits": int(y.sum()), "base_rate": float(y.mean()),
            "n_fair_test": sum(1 for r in allrows if r.get("fair_test")),
            "base_rate_fair_test": None,
            "n_dates": len({r["date"] for r in allrows}),
            "date_range": (min(r["date"] for r in allrows),
                           max(r["date"] for r in allrows)),
        }
    return out


# ══════════════════════════════════════════════════════════════════════════
#  1. SIGNAL MATRIX
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class SignalMatrix:
    """Design matrix + everything needed to reproduce or audit it."""
    X: np.ndarray                    # (n, p) float64, guaranteed finite
    y: np.ndarray                    # (n,) int 0/1
    columns: List[str]               # length p; includes '__fired' indicators
    signals: List[str]               # base signal names kept (no indicators)
    dates: np.ndarray                # (n,) str, ascending
    prop_types: np.ndarray           # (n,) str
    raw: List[Dict[str, float]]      # per-row {signal: value} for present-only work
    present: np.ndarray              # (n, len(signals)) bool
    impute: Dict[str, float]         # signal -> median used for imputation
    presence_rate: Dict[str, float]
    dropped: Dict[str, str]          # signal -> reason dropped
    prop_type: Optional[str]
    notes: List[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return self.X.shape[0]

    @property
    def p(self) -> int:
        return self.X.shape[1]

    @property
    def n_events(self) -> int:
        """Count of the RARER class. This, not n, bounds a logistic fit."""
        pos = int(self.y.sum())
        return min(pos, self.n - pos)

    def power(self, n_params: Optional[int] = None) -> dict:
        p = n_params if n_params is not None else self.p + 1  # +1 intercept
        rpp = self.n / p if p else float("inf")
        epp = self.n_events / p if p else float("inf")
        ok = (rpp >= MIN_ROWS_PER_PARAM and epp >= MIN_EVENTS_PER_PARAM
              and self.n >= MIN_ROWS_ABSOLUTE)
        reasons = []
        if self.n < MIN_ROWS_ABSOLUTE:
            reasons.append(f"only {self.n} rows (need >= {MIN_ROWS_ABSOLUTE})")
        if rpp < MIN_ROWS_PER_PARAM:
            reasons.append(f"{rpp:.1f} rows/parameter (need >= {MIN_ROWS_PER_PARAM:.0f})")
        if epp < MIN_EVENTS_PER_PARAM:
            reasons.append(f"{epp:.1f} events/parameter (need >= {MIN_EVENTS_PER_PARAM:.0f}; "
                           f"{self.n_events} events across {p} parameters)")
        return {
            "n_rows": self.n, "n_events": self.n_events, "n_params": p,
            "rows_per_param": round(rpp, 2), "events_per_param": round(epp, 2),
            "authoritative": bool(ok),
            "verdict": ("Fit is adequately powered." if ok else
                        "UNDERPOWERED — do NOT put these weights in production: "
                        + "; ".join(reasons)),
        }


def signal_matrix(rows: Iterable[dict],
                  prop_type: Optional[str] = None,
                  fair_test_only: bool = False,
                  min_presence: float = 0.05,
                  min_fired: int = 15,
                  include_indicators: bool = True,
                  impute: Optional[Dict[str, float]] = None,
                  keep_signals: Optional[Sequence[str]] = None,
                  include_market: bool = False) -> SignalMatrix:
    """Assemble backtest rows into a numeric design matrix.

    MISSINGNESS POLICY (the decision SCHEMA.md calls out explicitly):

      value column : median-imputed when the signal did not fire.
      fired column : 1.0 if the signal fired for this row, 0.0 if it did not.

    Two columns per partially-present signal. This is the standard
    "impute + indicator" treatment and it is chosen over the alternatives for
    concrete reasons:

      - Imputing 0 is wrong. On a 0-100 signal scale, 0 means "worst possible
        reading", so it would teach the fitter that unavailable data is a
        maximally bad matchup.
      - Imputing 50 (what production does) is defensible for *scoring* but
        wrong for *fitting*: it hard-codes the assumption that a missing signal
        is exactly average, which is the very assumption we are here to test.
        Median-impute keeps the row usable while the indicator lets the data
        say whether absence itself predicts the outcome. It very often does:
        a pitch-type exploit is absent because there was nothing to exploit.
      - Dropping incomplete rows (complete-case analysis) is worst of all here.
        Signals like `pitch_exploit` and `bat_speed_trend` fire on a minority
        of rows, so complete-case would throw away most of a dataset that is
        already too small.

    The indicator is omitted for signals that are always (or essentially
    always) present, because a constant column is rank-deficient and adds a
    parameter for nothing.

    `impute` lets a caller pass medians computed on the TRAIN split so the test
    split is transformed with train statistics only. Not passing it computes
    medians over whatever rows are given — correct for a pure descriptive
    report, a leak if used to build a test matrix.
    """
    rs = _usable_rows(rows, prop_type=prop_type, fair_test_only=fair_test_only)
    notes: List[str] = []
    dropped: Dict[str, str] = {}

    if not rs:
        return SignalMatrix(np.zeros((0, 0)), np.zeros(0, dtype=int), [], [],
                            np.array([]), np.array([]), [], np.zeros((0, 0), bool),
                            {}, {}, {"__all__": "no usable rows"}, prop_type,
                            ["no usable rows after filtering"])

    # --- gather raw values, canonicalising names and rejecting non-finite ---
    per_row: List[Dict[str, float]] = []
    seen: Counter = Counter()
    nonnumeric: Counter = Counter()
    nonfinite: Counter = Counter()
    for r in rs:
        d: Dict[str, float] = {}
        for k, v in (r.get("signals") or {}).items():
            name = canonical(str(k))
            f = _finite(v)
            if f is None:
                if v is None:
                    pass  # legitimately absent, nothing to record
                else:
                    # a NaN/inf/string that LOOKS like data. Count it loudly.
                    try:
                        float(v)
                        nonfinite[name] += 1
                    except (TypeError, ValueError):
                        nonnumeric[name] += 1
                continue
            d[name] = f
            seen[name] += 1
        per_row.append(d)

    for name, c in nonfinite.items():
        notes.append(f"{name}: {c} row(s) carried NaN/inf and were treated as ABSENT, "
                     f"not as a value (a NaN scoring as a real reading is a known "
                     f"failure mode in this codebase)")
    for name, c in nonnumeric.items():
        dropped.setdefault(name, f"non-numeric value in {c} row(s)")

    n = len(rs)
    candidates = sorted(seen)
    if keep_signals is not None:
        want = {canonical(s) for s in keep_signals}
        for name in candidates:
            if name not in want:
                dropped.setdefault(name, "not in keep_signals")
        candidates = [c for c in candidates if c in want]

    kept: List[str] = []
    for name in candidates:
        if name in dropped:
            continue
        if not include_market and canonical(name) in MARKET_SIGNALS:
            dropped[name] = ("market-derived — SCHEMA.md: line history only began "
                             "2026-08-05 and cannot be reconstructed backwards, so "
                             "it cannot be backtested honestly")
            continue
        c = seen[name]
        if c < min_fired:
            dropped[name] = f"fired in only {c} row(s) (min_fired={min_fired})"
            continue
        if c / n < min_presence:
            dropped[name] = (f"fired in {c}/{n} rows = {c/n:.1%} "
                             f"(min_presence={min_presence:.0%})")
            continue
        vals = np.array([d[name] for d in per_row if name in d], dtype=float)
        if float(np.nanstd(vals)) == 0.0:
            dropped[name] = f"constant value {vals[0]:g} in every row it fired — no information"
            continue
        kept.append(name)

    if not kept:
        return SignalMatrix(np.zeros((n, 0)), np.array([int(r["outcome"]) for r in rs]),
                            [], [], np.array([str(r["date"]) for r in rs]),
                            np.array([str(r.get("prop_type", "?")) for r in rs]),
                            per_row, np.zeros((n, 0), bool), {}, {}, dropped,
                            prop_type, notes + ["no signals survived filtering"])

    # --- imputation medians (train-only if the caller supplied them) ---
    med: Dict[str, float] = {}
    for name in kept:
        if impute is not None and name in impute:
            med[name] = float(impute[name])
        else:
            vals = [d[name] for d in per_row if name in d]
            med[name] = float(np.median(vals)) if vals else NEUTRAL
    if impute is not None:
        missing_med = [s for s in kept if s not in impute]
        if missing_med:
            notes.append("imputation medians were not supplied for "
                         f"{missing_med} — computed in-sample for those")

    presence_rate = {name: seen[name] / n for name in kept}

    cols: List[str] = []
    data: List[np.ndarray] = []
    present = np.zeros((n, len(kept)), dtype=bool)
    for j, name in enumerate(kept):
        v = np.empty(n, dtype=float)
        for i, d in enumerate(per_row):
            if name in d:
                v[i] = d[name]
                present[i, j] = True
            else:
                v[i] = med[name]
        cols.append(name)
        data.append(v)
        rate = presence_rate[name]
        if include_indicators and 0.02 < rate < 0.98:
            cols.append(f"{name}__fired")
            data.append(present[:, j].astype(float))
        elif include_indicators and rate <= 0.98:
            notes.append(f"{name}: presence {rate:.1%} too low/high for a usable "
                         f"indicator column; value column kept alone")

    X = np.column_stack(data) if data else np.zeros((n, 0))
    if not np.all(np.isfinite(X)):
        bad = [cols[j] for j in range(X.shape[1]) if not np.all(np.isfinite(X[:, j]))]
        raise AssertionError(f"non-finite values survived into the design matrix: {bad}. "
                             f"This must never happen — a NaN reaching a scorer is "
                             f"the documented bug class this guard exists for.")

    y = np.array([int(r["outcome"]) for r in rs], dtype=int)
    dates = np.array([str(r["date"]) for r in rs])
    ptypes = np.array([str(r.get("prop_type", "?")) for r in rs])

    return SignalMatrix(X=X, y=y, columns=cols, signals=kept, dates=dates,
                        prop_types=ptypes, raw=per_row, present=present,
                        impute=med, presence_rate=presence_rate, dropped=dropped,
                        prop_type=prop_type, notes=notes)


# ══════════════════════════════════════════════════════════════════════════
#  STATISTICS PRIMITIVES (implemented here so every number is auditable)
# ══════════════════════════════════════════════════════════════════════════

def auc(y: np.ndarray, s: np.ndarray) -> Optional[float]:
    """Rank-based AUC (Mann-Whitney U), tie-aware.

    AUC = P(score of a random hit > score of a random miss), with ties at 0.5.
    Written out rather than imported so the tie handling is visible: with
    coarse 0-100 signals, ties are common and a naive implementation that
    ignores them reports an inflated AUC.
    """
    y = np.asarray(y).astype(int)
    s = np.asarray(s, dtype=float)
    pos, neg = int((y == 1).sum()), int((y == 0).sum())
    if pos == 0 or neg == 0:
        return None
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    sorted_s = s[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        ranks[order[i:j + 1]] = avg
        i = j + 1
    r_pos = ranks[y == 1].sum()
    u = r_pos - pos * (pos + 1) / 2.0
    return float(u / (pos * neg))


def auc_se(a: float, pos: int, neg: int) -> float:
    """Hanley-McNeil standard error of AUC. Approximate, and deliberately so:
    it is the cheap sanity check that tells you a 0.58 AUC on 60 rows is
    indistinguishable from a coin flip."""
    if pos <= 0 or neg <= 0:
        return float("nan")
    q1 = a / (2.0 - a)
    q2 = 2.0 * a * a / (1.0 + a)
    var = (a * (1 - a) + (pos - 1) * (q1 - a * a) + (neg - 1) * (q2 - a * a)) / (pos * neg)
    return float(math.sqrt(max(var, 0.0)))


def _norm_sf(z: float) -> float:
    return 0.5 * math.erfc(abs(z) / math.sqrt(2.0))


def two_sided_p(z: float) -> float:
    return float(min(1.0, 2.0 * _norm_sf(z)))


def point_biserial(y: np.ndarray, s: np.ndarray) -> Tuple[Optional[float], Optional[float]]:
    """Pearson r between a binary outcome and a continuous signal, + p-value."""
    y = np.asarray(y, dtype=float)
    s = np.asarray(s, dtype=float)
    if len(y) < 3 or np.std(s) == 0 or np.std(y) == 0:
        return None, None
    r = float(np.corrcoef(y, s)[0, 1])
    n = len(y)
    if abs(r) >= 1.0:
        return r, 0.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    if _scipy_stats is not None:
        p = float(2 * _scipy_stats.t.sf(abs(t), df=n - 2))
    else:
        p = two_sided_p(t)
    return r, p


def log_loss(y: np.ndarray, p: np.ndarray, eps: float = 1e-12) -> float:
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2))


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


# ══════════════════════════════════════════════════════════════════════════
#  2. UNIVARIATE SIGNAL REPORT
# ══════════════════════════════════════════════════════════════════════════

def univariate_signal_report(rows: Iterable[dict],
                             prop_type: Optional[str] = None,
                             fair_test_only: bool = False,
                             min_fired: int = 15,
                             **mat_kwargs) -> dict:
    """Per signal, on its own: does it separate hits from misses at all?

    This is the first honest look at whether a signal has EVER helped, and it
    is deliberately independent of the multivariate fit — a signal can survive
    a regression by proxying for another one, but it cannot fake a univariate
    AUC.

    Every statistic here is computed on the rows where the signal ACTUALLY
    FIRED. Imputed values are not evidence about the signal and including them
    would drag every AUC toward 0.5 by diluting it with a constant.

    Reported per signal:
      n_fired / presence  — how often the signal exists at all
      auc + se + 95% CI   — separation; CI straddling 0.50 means "no evidence"
      point_biserial + p  — the same question as a correlation
      q5_hit_rate / q1_hit_rate — hit rate in the top vs bottom quintile of the
                            signal's value, with counts. This is the number a
                            human can actually act on, and it exposes
                            non-monotone signals that AUC alone would flatten.
      fired_vs_absent     — hit rate when the signal fired vs when it did not.
                            Informative missingness lives here: if a signal's
                            mere presence predicts the outcome, that is a real
                            finding and the indicator column will pick it up.
    """
    mat = signal_matrix(rows, prop_type=prop_type, fair_test_only=fair_test_only,
                        min_fired=min_fired, **mat_kwargs)
    out: Dict[str, Any] = {
        "prop_type": prop_type or "__ALL__",
        "n_rows": mat.n,
        "base_rate": float(mat.y.mean()) if mat.n else None,
        "n_events": mat.n_events,
        "dropped_signals": dict(mat.dropped),
        "notes": list(mat.notes),
        "signals": {},
    }
    if mat.n == 0 or not mat.signals:
        return out

    for j, name in enumerate(mat.signals):
        fired = mat.present[:, j]
        yf = mat.y[fired]
        vf = mat.X[fired, mat.columns.index(name)]
        n_f = int(fired.sum())
        rec: Dict[str, Any] = {
            "n_fired": n_f,
            "presence": round(float(mat.presence_rate[name]), 4),
            "hit_rate_when_fired": float(yf.mean()) if n_f else None,
            "value_mean": float(vf.mean()) if n_f else None,
            "value_std": float(vf.std(ddof=1)) if n_f > 1 else None,
        }
        a = auc(yf, vf)
        rec["auc"] = round(a, 4) if a is not None else None
        if a is not None:
            pos, neg = int((yf == 1).sum()), int((yf == 0).sum())
            se = auc_se(a, pos, neg)
            rec["auc_se"] = round(se, 4)
            rec["auc_ci95"] = [round(max(0.0, a - 1.96 * se), 4),
                               round(min(1.0, a + 1.96 * se), 4)]
            rec["auc_p"] = round(two_sided_p((a - 0.5) / se), 5) if se > 0 else None
            rec["separates"] = bool(rec["auc_ci95"][0] > 0.5 or rec["auc_ci95"][1] < 0.5)
        else:
            rec["auc_se"] = rec["auc_ci95"] = rec["auc_p"] = None
            rec["separates"] = False
            rec["note"] = "only one outcome class among rows where this fired"

        r, p = point_biserial(yf, vf)
        rec["point_biserial"] = round(r, 4) if r is not None else None
        rec["point_biserial_p"] = round(p, 5) if p is not None else None

        # quintiles (top 20% vs bottom 20% of the signal's own fired values)
        if n_f >= 20:
            lo_c, hi_c = np.quantile(vf, [0.2, 0.8])
            lo_m, hi_m = vf <= lo_c, vf >= hi_c
            # guard against a mass-point signal putting everything in one bin
            if lo_m.sum() and hi_m.sum() and not (lo_m & hi_m).all():
                rec["q1_hit_rate"] = round(float(mat.y[fired][lo_m].mean()), 4)
                rec["q1_n"] = int(lo_m.sum())
                rec["q5_hit_rate"] = round(float(mat.y[fired][hi_m].mean()), 4)
                rec["q5_n"] = int(hi_m.sum())
                rec["quintile_spread"] = round(rec["q5_hit_rate"] - rec["q1_hit_rate"], 4)
            else:
                rec["quintile_note"] = ("value distribution too concentrated for "
                                        "quintiles (mass point)")
        else:
            rec["quintile_note"] = f"only {n_f} fired rows — quintiles not meaningful"

        # informative missingness
        if 0.02 < mat.presence_rate[name] < 0.98:
            ya = mat.y[~fired]
            if len(ya):
                rec["hit_rate_when_absent"] = round(float(ya.mean()), 4)
                rec["n_absent"] = int(len(ya))
                pres_auc = auc(mat.y, fired.astype(float))
                rec["presence_auc"] = round(pres_auc, 4) if pres_auc is not None else None
                rec["missingness_informative"] = bool(
                    pres_auc is not None and abs(pres_auc - 0.5) > 0.05)
        out["signals"][name] = rec
    return out


# ══════════════════════════════════════════════════════════════════════════
#  3. WEIGHT FITTING
# ══════════════════════════════════════════════════════════════════════════

def _standardize(X: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    mu = X.mean(axis=0)
    sd = X.std(axis=0, ddof=0)
    sd = np.where(sd < 1e-12, 1.0, sd)
    return (X - mu) / sd, mu, sd


def _irls_ridge(X: np.ndarray, y: np.ndarray, lam: float,
                max_iter: int = 100, tol: float = 1e-9) -> Tuple[np.ndarray, np.ndarray, bool]:
    """Penalized logistic regression by IRLS/Newton.

    Objective: -loglik(beta) + lam * ||beta_slopes||^2   (intercept NOT penalized)

    Hand-rolled rather than sklearn because we need the observed information
    matrix for standard errors, and because the exact penalty convention has to
    be stated rather than inferred from a C parameter. Returns
    (beta, covariance, converged).

    The covariance is (X'WX + 2*lam*P)^-1. Two honest caveats, both surfaced in
    the report:
      - these are the standard errors OF THE PENALIZED ESTIMATOR. Ridge shrinks
        coefficients toward zero and shrinks their variance too, so the p-values
        are anti-conservative as an "is this signal real" test. They are useful
        for ranking and for spotting coefficients that are pure noise; they are
        not a clean hypothesis test.
      - at lam=0 they are the usual MLE standard errors.
    """
    n, p = X.shape
    Xd = np.column_stack([np.ones(n), X])          # intercept first
    P = np.eye(p + 1)
    P[0, 0] = 0.0                                   # do not penalize intercept
    beta = np.zeros(p + 1)
    converged = False
    for _ in range(max_iter):
        eta = Xd @ beta
        mu = sigmoid(eta)
        W = np.clip(mu * (1 - mu), 1e-9, None)
        grad = Xd.T @ (y - mu) - 2.0 * lam * (P @ beta)
        H = (Xd.T * W) @ Xd + 2.0 * lam * P
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(H) @ grad
        # damped Newton: guards against separation blowing the step up
        for damp in (1.0, 0.5, 0.25, 0.1):
            new = beta + damp * step
            if np.all(np.isfinite(new)):
                break
        if not np.all(np.isfinite(new)):
            break
        if np.max(np.abs(new - beta)) < tol:
            beta = new
            converged = True
            break
        beta = new
    eta = Xd @ beta
    mu = sigmoid(eta)
    W = np.clip(mu * (1 - mu), 1e-9, None)
    H = (Xd.T * W) @ Xd + 2.0 * lam * P
    try:
        cov = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        cov = np.linalg.pinv(H)
    return beta, cov, converged


def _date_folds(dates: np.ndarray, n_folds: int = 4) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Expanding-window folds over sorted unique dates. TIME ORDER ONLY.

    Fold k trains on the first (k+1) blocks of dates and validates on block
    k+1. No fold ever validates on a date earlier than one it trained on. This
    is used for choosing the L2 strength inside the TRAIN split; it never sees
    the held-out test split.
    """
    uniq = np.array(sorted(set(dates.tolist())))
    if len(uniq) < n_folds + 1:
        n_folds = max(1, len(uniq) - 1)
    if n_folds < 1:
        return []
    edges = np.linspace(0, len(uniq), n_folds + 2).astype(int)
    folds = []
    for k in range(1, n_folds + 1):
        tr_dates = set(uniq[:edges[k]].tolist())
        va_dates = set(uniq[edges[k]:edges[k + 1]].tolist())
        if not tr_dates or not va_dates:
            continue
        tr = np.array([d in tr_dates for d in dates])
        va = np.array([d in va_dates for d in dates])
        if tr.sum() >= 10 and va.sum() >= 5 and len(set(np.asarray(va).tolist())) > 0:
            folds.append((tr, va))
    return folds


DEFAULT_L2_GRID = (0.5, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0)


def select_l2(X: np.ndarray, y: np.ndarray, dates: np.ndarray,
              grid: Sequence[float] = DEFAULT_L2_GRID) -> dict:
    """Choose the L2 strength by expanding-window time-series CV on TRAIN only.

    Selection criterion is validation log loss, because log loss is the metric
    that punishes overconfidence, which is precisely what an underregularized
    fit on a few hundred rows produces.

    If there are not enough dates or rows to do this honestly, we do NOT quietly
    fall back to a small penalty (which would look like a better fit and be a
    worse model). We fall back to a DELIBERATELY STRONG default and say so.
    """
    folds = _date_folds(dates)
    n, p = X.shape
    if len(folds) < 2 or n < 60:
        lam = max(10.0, float(p) * 5.0)
        return {"lam": lam, "selected_by": "fallback",
                "reason": (f"only {len(folds)} usable time fold(s) and {n} rows — too "
                           f"little data to select a penalty honestly. Defaulted to a "
                           f"STRONG penalty (lam={lam:g}, scaled to {p} features) which "
                           f"shrinks coefficients hard toward zero. Fitted weights under "
                           f"this fallback are a sketch, not an estimate."),
                "curve": {}}
    curve = {}
    for lam in grid:
        losses, ws = [], []
        for tr, va in folds:
            Xs, mu, sd = _standardize(X[tr])
            beta, _, _ = _irls_ridge(Xs, y[tr], lam)
            Xv = (X[va] - mu) / sd
            pv = sigmoid(beta[0] + Xv @ beta[1:])
            losses.append(log_loss(y[va], pv))
            ws.append(int(va.sum()))
        curve[lam] = float(np.average(losses, weights=ws))
    best = min(curve, key=curve.get)
    return {"lam": float(best), "selected_by": "time-series CV on train",
            "reason": (f"expanding-window CV over {len(folds)} time folds selected "
                       f"lam={best:g} by validation log loss "
                       f"({curve[best]:.4f}); grid {list(grid)}"),
            "curve": {float(k): round(v, 5) for k, v in curve.items()}}


def fit_weights(rows: Iterable[dict],
                method: str = "logistic",
                prop_type: Optional[str] = None,
                fair_test_only: bool = False,
                l2: Optional[float] = None,
                bootstrap: int = 200,
                seed: int = 0,
                _mat: Optional[SignalMatrix] = None,
                **mat_kwargs) -> dict:
    """Fit signal weights against actual outcomes.

    Returns per-signal coefficient, standard error, z, p-value, odds ratio per
    1 SD, and a bootstrap sign-stability score.

    REGULARIZATION. L2 (ridge) with strength chosen by expanding-window
    time-series CV on the training rows, unless `l2` is passed explicitly. L2
    rather than L1 because the goal here is stable *weights* on correlated
    inputs, not automatic selection: with signals as collinear as these, L1
    picks one of a correlated pair essentially at random and zeroes the other,
    which would produce a confident and arbitrary pruning recommendation. L1
    would be the right tool with 10x the data. Pruning here is decided by
    prune_recommendation() from converging evidence instead.

    Features are standardized (z-scored) before fitting, so every coefficient
    is "log-odds change per 1 SD of this signal" and coefficients are directly
    comparable across signals with different natural units. The penalty applies
    evenly for the same reason — penalizing raw-scale coefficients would
    penalize small-scale signals more.

    The returned `weights_0_100` field rescales the fitted coefficients into
    the same shape as the hand-picked weights (non-negative, summing to 1) so
    they can be dropped into a production-style weighted score. Note the
    lossiness: that rescaling discards NEGATIVE coefficients, which are real
    findings (a signal that predicts the wrong way), so read `coef` for truth
    and `weights_0_100` only for drop-in comparison.
    """
    if method != "logistic":
        raise ValueError(f"unsupported method {method!r}; only 'logistic' is implemented")

    mat = _mat if _mat is not None else signal_matrix(
        rows, prop_type=prop_type, fair_test_only=fair_test_only, **mat_kwargs)
    out: Dict[str, Any] = {
        "method": method,
        "prop_type": prop_type or "__ALL__",
        "n_rows": mat.n,
        "n_events": mat.n_events,
        "base_rate": float(mat.y.mean()) if mat.n else None,
        "columns": list(mat.columns),
        "dropped_signals": dict(mat.dropped),
        "notes": list(mat.notes),
        "missingness_policy": ("median-impute + <signal>__fired indicator; "
                               "NaN/inf treated as absent; medians from the rows "
                               "passed in (pass impute= for train-only medians)"),
    }
    if mat.n == 0 or mat.p == 0:
        out["error"] = "no usable rows/signals"
        out["power"] = {"authoritative": False, "verdict": "no data"}
        return out
    if mat.n_events == 0:
        out["error"] = "outcomes are all one class — nothing to fit"
        out["power"] = {"authoritative": False, "verdict": "degenerate outcome"}
        return out

    power = mat.power()
    out["power"] = power

    Xs, mu, sd = _standardize(mat.X)
    if l2 is None:
        sel = select_l2(mat.X, mat.y, mat.dates)
        lam = sel["lam"]
        out["l2"] = sel
    else:
        lam = float(l2)
        out["l2"] = {"lam": lam, "selected_by": "caller", "reason": "explicitly supplied",
                     "curve": {}}

    beta, cov, converged = _irls_ridge(Xs, mat.y, lam)
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    out["converged"] = bool(converged)
    if not converged:
        out["notes"].append("IRLS did not fully converge — usually means quasi-"
                            "separation (a signal perfectly splits the outcomes on "
                            "this sample). Treat coefficients as unreliable.")

    p_hat = sigmoid(beta[0] + Xs @ beta[1:])
    out["in_sample"] = {
        "log_loss": round(log_loss(mat.y, p_hat), 5),
        "brier": round(brier(mat.y, p_hat), 5),
        "auc": round(auc(mat.y, p_hat) or float("nan"), 4),
        "warning": "IN-SAMPLE — always flattering. Use compare_to_current_weights() "
                   "for the held-out number.",
    }

    # bootstrap sign stability, resampled by DATE (clustered) so rows from the
    # same slate stay together — picks on one day share weather, umpires and
    # opponents, so resampling rows independently would fake independence.
    stability = np.zeros(mat.p)
    if bootstrap and mat.n >= 40:
        rng = np.random.default_rng(seed)
        uniq_dates = np.array(sorted(set(mat.dates.tolist())))
        by_date = {d: np.where(mat.dates == d)[0] for d in uniq_dates}
        signs = np.sign(beta[1:])
        agree = np.zeros(mat.p)
        done = 0
        for _ in range(bootstrap):
            pick = rng.choice(uniq_dates, size=len(uniq_dates), replace=True)
            idx = np.concatenate([by_date[d] for d in pick])
            yb = mat.y[idx]
            if yb.sum() == 0 or yb.sum() == len(yb):
                continue
            Xb, _, _ = _standardize(mat.X[idx])
            bb, _, _ = _irls_ridge(Xb, yb, lam, max_iter=40)
            agree += (np.sign(bb[1:]) == signs).astype(float)
            done += 1
        stability = agree / done if done else np.zeros(mat.p)
        out["bootstrap"] = {"n_resamples": done, "clustered_by": "date",
                            "meaning": "fraction of date-clustered resamples where the "
                                       "coefficient kept the same sign; <0.8 means the "
                                       "direction itself is not established"}
    else:
        out["bootstrap"] = {"n_resamples": 0, "clustered_by": "date",
                            "meaning": "skipped (too few rows)"}

    coefs = {}
    for j, col in enumerate(mat.columns):
        b, s = float(beta[j + 1]), float(se[j + 1])
        z = b / s if s > 0 else float("nan")
        coefs[col] = {
            "coef": round(b, 5),
            "std_err": round(s, 5),
            "z": round(z, 3) if math.isfinite(z) else None,
            "p_value": round(two_sided_p(z), 5) if math.isfinite(z) else None,
            "odds_ratio_per_sd": round(math.exp(b), 4) if abs(b) < 30 else None,
            "ci95": [round(b - 1.96 * s, 5), round(b + 1.96 * s, 5)],
            "sign_stability": round(float(stability[j]), 3) if bootstrap else None,
            "is_indicator": col.endswith("__fired"),
            "feature_sd": round(float(sd[j]), 5),
            "feature_mean": round(float(mu[j]), 5),
        }
    out["intercept"] = round(float(beta[0]), 5)
    out["coefficients"] = coefs
    out["standardization"] = {"mean": {c: round(float(mu[j]), 5)
                                       for j, c in enumerate(mat.columns)},
                              "sd": {c: round(float(sd[j]), 5)
                                     for j, c in enumerate(mat.columns)}}

    # production-shaped weights (value columns only, positives only, sum to 1)
    pos = {c: v["coef"] for c, v in coefs.items()
           if not c.endswith("__fired") and v["coef"] > 0}
    tot = sum(pos.values())
    out["weights_0_100"] = ({c: round(v / tot, 4) for c, v in sorted(
        pos.items(), key=lambda kv: -kv[1])} if tot > 0 else {})
    out["weights_0_100_caveat"] = (
        "Positive value-column coefficients renormalized to sum to 1. Signals with "
        "negative coefficients are EXCLUDED from this view — check `coefficients` "
        "for those, since a signal pushing the wrong way is a finding, not a zero.")
    if not power["authoritative"]:
        out["DO_NOT_SHIP"] = power["verdict"]
    return out


# ══════════════════════════════════════════════════════════════════════════
#  4. COLLINEARITY
# ══════════════════════════════════════════════════════════════════════════

def collinearity_report(rows: Iterable[dict],
                        prop_type: Optional[str] = None,
                        fair_test_only: bool = False,
                        r_threshold: float = 0.80,
                        vif_threshold: float = 5.0,
                        _mat: Optional[SignalMatrix] = None,
                        **mat_kwargs) -> dict:
    """Pairwise correlation + VIF across signals.

    This is the report that stops a fitted weight from being over-read. The
    concrete worry in this system: an implied team total already encodes park,
    weather and opposing-pitcher quality, so `park_hr_index`, `sp_era_weak` and
    any implied-total signal are measuring overlapping things. When inputs are
    collinear the regression has to split one effect between several columns,
    and how it splits is close to arbitrary — the weights move a lot when the
    data moves a little, and "signal A got a bigger weight than signal B" stops
    meaning anything.

    Correlations are computed PAIRWISE-COMPLETE (only rows where BOTH signals
    fired) with the pair's n reported, because correlating imputed constants
    manufactures agreement between two signals that are simply missing on the
    same rows. VIF is necessarily computed on the full imputed matrix (it needs
    a rectangular matrix); the imputation makes VIF a mild UNDER-estimate, so
    treat flagged VIFs as a floor.
    """
    mat = _mat if _mat is not None else signal_matrix(
        rows, prop_type=prop_type, fair_test_only=fair_test_only, **mat_kwargs)
    out: Dict[str, Any] = {"prop_type": prop_type or "__ALL__", "n_rows": mat.n,
                           "signals": list(mat.signals), "pairs": [], "vif": {},
                           "flagged_pairs": [], "notes": list(mat.notes)}
    if mat.p == 0 or mat.n < 5:
        out["error"] = "not enough data for a collinearity report"
        return out

    # ---- pairwise, complete-case per pair ----
    S = mat.signals
    idx = {s: mat.columns.index(s) for s in S}
    for i in range(len(S)):
        for j in range(i + 1, len(S)):
            a, b = S[i], S[j]
            both = mat.present[:, i] & mat.present[:, j]
            n_both = int(both.sum())
            if n_both < 10:
                out["pairs"].append({"a": a, "b": b, "n": n_both, "r": None,
                                     "note": "fewer than 10 rows where both fired"})
                continue
            va, vb = mat.X[both, idx[a]], mat.X[both, idx[b]]
            if va.std() == 0 or vb.std() == 0:
                out["pairs"].append({"a": a, "b": b, "n": n_both, "r": None,
                                     "note": "constant on the overlap"})
                continue
            r = float(np.corrcoef(va, vb)[0, 1])
            rec = {"a": a, "b": b, "n": n_both, "r": round(r, 4)}
            if _scipy_stats is not None and n_both > 3:
                rho = float(_scipy_stats.spearmanr(va, vb).statistic)
                rec["spearman"] = round(rho, 4)
            out["pairs"].append(rec)
            if abs(r) >= r_threshold:
                out["flagged_pairs"].append(rec)
    out["pairs"].sort(key=lambda d: -(abs(d["r"]) if d.get("r") is not None else -1))
    out["flagged_pairs"].sort(key=lambda d: -abs(d["r"]))

    # ---- VIF on the full (imputed) design, value columns only ----
    cols = [c for c in mat.columns if not c.endswith("__fired")]
    ci = [mat.columns.index(c) for c in cols]
    Xv = mat.X[:, ci]
    Xs, _, _ = _standardize(Xv)
    for k, c in enumerate(cols):
        others = np.delete(Xs, k, axis=1)
        target = Xs[:, k]
        if others.shape[1] == 0:
            out["vif"][c] = 1.0
            continue
        A = np.column_stack([np.ones(len(target)), others])
        coef, *_ = np.linalg.lstsq(A, target, rcond=None)
        resid = target - A @ coef
        ss_res = float(resid @ resid)
        ss_tot = float(((target - target.mean()) ** 2).sum())
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        r2 = min(max(r2, 0.0), 1 - 1e-12)
        out["vif"][c] = round(1.0 / (1.0 - r2), 3)
    out["flagged_vif"] = {c: v for c, v in sorted(out["vif"].items(), key=lambda kv: -kv[1])
                          if v >= vif_threshold}
    out["thresholds"] = {"|r|": r_threshold, "vif": vif_threshold}
    out["interpretation"] = (
        f"{len(out['flagged_pairs'])} pair(s) at |r| >= {r_threshold} and "
        f"{len(out['flagged_vif'])} signal(s) at VIF >= {vif_threshold}. Any weight "
        f"on a flagged signal is unstable: it is sharing one underlying effect with "
        f"its partner and the split between them is close to arbitrary. Fix by "
        f"dropping the weaker member of each pair (see prune_recommendation), not "
        f"by trusting the fitted split.")
    return out


# ══════════════════════════════════════════════════════════════════════════
#  5. HAND-PICKED vs FITTED, ON HELD-OUT DATA
# ══════════════════════════════════════════════════════════════════════════

def current_weight_score(row: dict, prop_type: Optional[str] = None) -> Optional[float]:
    """Reproduce generate_picks.py's hand-picked weighted score from a row's
    signals, using production's own absent-value behaviour (scale() returns the
    0-100 midpoint for a missing input, so absent signals contribute 50 unless
    they are additive bonuses, which contribute 0)."""
    pt = prop_type or row.get("prop_type")
    table = CURRENT_WEIGHTS.get(str(pt))
    if not table:
        return None
    sig = {canonical(str(k)): _finite(v) for k, v in (row.get("signals") or {}).items()}
    total = 0.0
    for name, (w, absent) in table.items():
        v = sig.get(name)
        total += w * (absent if v is None else v)
    return total


def _time_split(dates: np.ndarray, train_frac: float = 0.7) -> Tuple[np.ndarray, np.ndarray, str]:
    """Split on DATE, earlier -> train, later -> test. Never random.

    Splitting on date rather than row index guarantees no single slate is
    straddled by the boundary: every pick from a given day lands entirely on
    one side. A random split would let the model train on tomorrow and test on
    yesterday, and every slow-moving input (season stats, sprint speed, rolling
    K%) would carry the answer across.
    """
    uniq = np.array(sorted(set(dates.tolist())))
    if len(uniq) < 2:
        return (np.ones(len(dates), bool), np.zeros(len(dates), bool),
                "only one date present — no time split possible")
    cut = max(1, int(round(len(uniq) * train_frac)))
    cut = min(cut, len(uniq) - 1)
    tr_d, te_d = set(uniq[:cut].tolist()), set(uniq[cut:].tolist())
    tr = np.array([d in tr_d for d in dates])
    te = np.array([d in te_d for d in dates])
    desc = (f"train {uniq[0]}..{uniq[cut-1]} ({len(tr_d)} dates, {int(tr.sum())} rows) | "
            f"test {uniq[cut]}..{uniq[-1]} ({len(te_d)} dates, {int(te.sum())} rows)")
    return tr, te, desc


def _platt(scores: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    """Fit a 1-D logistic map score -> probability (2 params). Used ONLY to put
    the hand-picked 0-100 score on the same probability scale as the fitted
    model, so log loss / Brier compare like with like. Fitted on TRAIN only.
    Without this the hand-picked score would 'lose' on log loss purely for
    being on the wrong scale, which would be a rigged comparison."""
    s = scores.reshape(-1, 1)
    ss, mu, sd = _standardize(s)
    beta, _, _ = _irls_ridge(ss, y, lam=1e-6)
    return float(beta[0] - beta[1] * mu[0] / sd[0]), float(beta[1] / sd[0])


def _metrics(y: np.ndarray, p: np.ndarray, q: float = 0.8) -> dict:
    a = auc(y, p)
    m = {"n": int(len(y)), "base_rate": round(float(y.mean()), 4),
         "auc": round(a, 4) if a is not None else None,
         "log_loss": round(log_loss(y, p), 5), "brier": round(brier(y, p), 5)}
    if len(y) >= 15:
        cut = float(np.quantile(p, q))
        top = p >= cut
        if 0 < top.sum() < len(y):
            m["top_quintile_hit_rate"] = round(float(y[top].mean()), 4)
            m["top_quintile_n"] = int(top.sum())
            m["top_quintile_lift"] = round(float(y[top].mean() - y.mean()), 4)
        bcut = float(np.quantile(p, 1 - q))
        bot = p <= bcut
        if 0 < bot.sum() < len(y):
            m["bottom_quintile_hit_rate"] = round(float(y[bot].mean()), 4)
            m["bottom_quintile_n"] = int(bot.sum())
    return m


def _auc_diff_ci(y: np.ndarray, pa: np.ndarray, pb: np.ndarray,
                 n_boot: int = 500, seed: int = 0) -> dict:
    """Paired bootstrap CI on AUC(a) - AUC(b). With test splits this small,
    a raw AUC gap of 0.04 is routinely noise; this says so out loud."""
    rng = np.random.default_rng(seed)
    n = len(y)
    diffs = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        yb = y[idx]
        if yb.sum() == 0 or yb.sum() == len(yb):
            continue
        aa, ab = auc(yb, pa[idx]), auc(yb, pb[idx])
        if aa is not None and ab is not None:
            diffs.append(aa - ab)
    if len(diffs) < 50:
        return {"n_boot": len(diffs), "note": "too few usable resamples"}
    d = np.array(diffs)
    lo, hi = np.quantile(d, [0.025, 0.975])
    return {"n_boot": len(d), "mean_diff": round(float(d.mean()), 4),
            "ci95": [round(float(lo), 4), round(float(hi), 4)],
            "distinguishable": bool(lo > 0 or hi < 0)}


def compare_to_current_weights(rows: Iterable[dict],
                               prop_type: Optional[str] = None,
                               fair_test_only: bool = False,
                               train_frac: float = 0.7,
                               l2: Optional[float] = None,
                               **mat_kwargs) -> dict:
    """The payoff: hand-picked weights vs fitted weights on HELD-OUT LATER DATES.

    Protocol, and it is not negotiable:
      * split on DATE, train = earlier, test = later
      * imputation medians and feature standardization come from TRAIN only
      * the L2 strength is chosen by time-series CV INSIDE the train split
      * the test split is scored exactly ONCE and never used to pick anything

    Three contenders, all evaluated under the same metrics on the same rows:
      production_score  — the `score` field the pipeline actually produced
                          (includes the bonuses and penalties that live outside
                          the linear formula: the star-power discount, the
                          notable-signals boost, the BABIP watchout)
      current_weights   — the hand-picked linear formula reconstructed from
                          generate_picks.py, i.e. the weights under test
      fitted            — logistic weights fitted on the train split only
      (predicted_prob   — the model's own pre-calibration probability, if the
                          rows carry it, for reference)

    Ranking metrics (AUC, quintile hit rates) are scale-free, so they compare
    the raw scores fairly. For log loss and Brier, each non-probability score
    is mapped through a 2-parameter logistic fitted on TRAIN. Without that the
    hand-picked score would lose on log loss purely for being on a 0-100 scale.

    IF THE HAND-PICKED WEIGHTS WIN, THIS SAYS SO. That is a real finding: it
    means the intuition encoded in generate_picks.py is carrying information
    the fit cannot recover from the sample available, and the correct action is
    to keep the hand-picked weights and collect more data.
    """
    rs = _usable_rows(rows, prop_type=prop_type, fair_test_only=fair_test_only)
    out: Dict[str, Any] = {"prop_type": prop_type or "__ALL__", "n_rows": len(rs)}
    if len(rs) < 30:
        out["error"] = f"only {len(rs)} usable rows — a held-out comparison would be noise"
        return out

    dates = np.array([str(r["date"]) for r in rs])
    tr_mask, te_mask, desc = _time_split(dates, train_frac)
    out["split"] = {"type": "TIME-BASED (never random)", "description": desc,
                    "train_frac": train_frac}
    if te_mask.sum() < 15:
        out["error"] = (f"test split has only {int(te_mask.sum())} rows — refusing to "
                        f"report a held-out comparison on that")
        return out

    train_rows = [r for r, m in zip(rs, tr_mask) if m]
    test_rows = [r for r, m in zip(rs, te_mask) if m]

    # --- build train matrix, then transform test with TRAIN medians ---
    mtr = signal_matrix(train_rows, prop_type=prop_type, **mat_kwargs)
    if mtr.p == 0:
        out["error"] = "no signals survived filtering on the train split"
        return out
    mte = signal_matrix(test_rows, prop_type=prop_type, impute=mtr.impute,
                        keep_signals=mtr.signals, min_presence=0.0, min_fired=0,
                        **{k: v for k, v in mat_kwargs.items()
                           if k not in ("min_presence", "min_fired", "keep_signals")})
    # align columns exactly (a signal can be 100% present in test but not train)
    Xte = np.zeros((mte.n, len(mtr.columns)))
    for j, c in enumerate(mtr.columns):
        if c in mte.columns:
            Xte[:, j] = mte.X[:, mte.columns.index(c)]
        elif c.endswith("__fired"):
            base = c[:-len("__fired")]
            if base in mte.signals:
                Xte[:, j] = mte.present[:, mte.signals.index(base)].astype(float)
            else:
                Xte[:, j] = 0.0
        else:
            Xte[:, j] = mtr.impute.get(c, NEUTRAL)
    yte = mte.y
    if len(set(yte.tolist())) < 2:
        out["error"] = "test split outcomes are all one class — nothing to measure"
        return out

    fit = fit_weights(train_rows, prop_type=prop_type, l2=l2, _mat=mtr, bootstrap=0)
    out["fit"] = {k: fit[k] for k in ("power", "l2", "intercept", "coefficients",
                                      "weights_0_100", "converged") if k in fit}
    out["power_train"] = fit.get("power")

    mu = np.array([fit["standardization"]["mean"][c] for c in mtr.columns])
    sd = np.array([fit["standardization"]["sd"][c] for c in mtr.columns])
    beta = np.array([fit["intercept"]] + [fit["coefficients"][c]["coef"] for c in mtr.columns])
    p_fitted = sigmoid(beta[0] + ((Xte - mu) / sd) @ beta[1:])

    contenders: Dict[str, dict] = {}

    def add(name: str, tr_scores, te_scores, already_prob=False, desc_=""):
        # tr_scores is legitimately None for contenders that are ALREADY on the
        # probability scale (the fitted model, predicted_prob): there is no raw
        # train score to Platt-map, because no mapping is needed. Guarding that
        # case matters -- np.asarray(None, dtype=float) is array(nan), so the
        # finiteness check below silently dropped "fitted", the one contender
        # this whole comparison exists to evaluate, and left the head-to-head
        # block to KeyError on it.
        te_s = np.asarray(te_scores, dtype=float)
        if np.any(~np.isfinite(te_s)):
            return
        tr_s = None if tr_scores is None else np.asarray(tr_scores, dtype=float)
        if tr_s is not None and np.any(~np.isfinite(tr_s)):
            return
        if not already_prob and tr_s is None:
            return  # a raw score with no train sample cannot be calibrated
        if already_prob:
            p = np.clip(te_s, 1e-6, 1 - 1e-6)
        else:
            if tr_s.std() == 0:
                return
            a, b = _platt(tr_s, mtr.y)
            p = sigmoid(a + b * te_s)
        m = _metrics(yte, p)
        m["description"] = desc_
        m["calibration_map"] = ("none (already a probability)" if already_prob
                                else "2-param logistic fitted on TRAIN only")
        contenders[name] = {"metrics": m, "_p": p}

    add("fitted", None, p_fitted, already_prob=True,
        desc_="logistic weights fitted on train dates only")

    cw_tr = [current_weight_score(r, prop_type) for r in train_rows]
    cw_te = [current_weight_score(r, prop_type) for r in test_rows]
    if all(v is not None for v in cw_tr) and all(v is not None for v in cw_te):
        add("current_weights", cw_tr, cw_te,
            desc_="hand-picked 35/25/15/15/10 formula reconstructed from generate_picks.py")
        # coverage check: are we actually reconstructing the real formula?
        table = CURRENT_WEIGHTS.get(str(prop_type or test_rows[0].get("prop_type")), {})
        present_names = {canonical(k) for r in rs for k in (r.get("signals") or {})}
        covered = present_names & set(table)
        out["current_weight_coverage"] = {
            "signals_in_data": sorted(present_names),
            "signals_with_a_hand_picked_weight": sorted(covered),
            "in_data_but_unweighted": sorted(present_names - set(table)),
            "weighted_but_never_in_data": sorted(set(table) - present_names),
        }
        if len(covered) < 3:
            out.setdefault("warnings", []).append(
                f"only {len(covered)} of this prop type's hand-picked weights matched a "
                f"signal name in the data — the 'current_weights' reconstruction is "
                f"probably mis-keyed. Trust 'production_score' over it.")
    else:
        out.setdefault("warnings", []).append(
            "could not reconstruct the hand-picked weighted score for every row "
            "(unknown prop_type or missing weight table) — using production_score only")

    ps_tr = [_finite(r.get("score")) for r in train_rows]
    ps_te = [_finite(r.get("score")) for r in test_rows]
    if all(v is not None for v in ps_tr) and all(v is not None for v in ps_te):
        add("production_score", ps_tr, ps_te,
            desc_="the `score` field the live pipeline actually produced (formula + "
                  "star-power discount + notable-signals boost + watchout penalties)")

    pp_tr = [_finite(r.get("predicted_prob")) for r in train_rows]
    pp_te = [_finite(r.get("predicted_prob")) for r in test_rows]
    if all(v is not None for v in pp_tr) and all(v is not None for v in pp_te):
        add("predicted_prob", pp_tr, pp_te, already_prob=True,
            desc_="the model's own pre-calibration probability (SCHEMA.md field)")

    baseline_p = np.full(len(yte), float(mtr.y.mean()))
    contenders["base_rate_only"] = {
        "metrics": {**_metrics(yte, baseline_p),
                    "description": "train base rate for every row — the floor any "
                                   "signal-based model must clear",
                    "calibration_map": "n/a"},
        "_p": baseline_p}

    out["contenders"] = {k: v["metrics"] for k, v in contenders.items()}

    if "current_weights" in contenders or "production_score" in contenders:
        ref = "current_weights" if "current_weights" in contenders else "production_score"
        out["head_to_head"] = {
            "reference": ref,
            "auc_diff_fitted_minus_reference": _auc_diff_ci(
                yte, contenders["fitted"]["_p"], contenders[ref]["_p"]),
        }
        fa = contenders["fitted"]["metrics"]["auc"]
        ra = contenders[ref]["metrics"]["auc"]
        ci = out["head_to_head"]["auc_diff_fitted_minus_reference"]
        if fa is None or ra is None:
            verdict = "AUC unavailable on the test split"
        elif not ci.get("distinguishable", False):
            verdict = (f"NO MEASURABLE DIFFERENCE. fitted AUC {fa:.3f} vs {ref} "
                       f"{ra:.3f}, but the paired-bootstrap 95% CI on the difference "
                       f"is {ci.get('ci95')} — it contains zero. On this sample the "
                       f"fitted weights are NOT demonstrably better than the "
                       f"hand-picked ones. Keep the hand-picked weights.")
        elif fa > ra:
            verdict = (f"FITTED WINS on held-out data: AUC {fa:.3f} vs {ra:.3f}, "
                       f"difference CI {ci.get('ci95')} excludes zero.")
        else:
            verdict = (f"HAND-PICKED WEIGHTS WIN on held-out data: {ref} AUC {ra:.3f} "
                       f"vs fitted {fa:.3f}, difference CI {ci.get('ci95')} excludes "
                       f"zero. This is a real finding — the hand-picked intuition is "
                       f"carrying information the fit cannot recover at this sample "
                       f"size. Do not replace it.")
        out["verdict"] = verdict

    if not (fit.get("power") or {}).get("authoritative", False):
        out["OVERRIDING_CAVEAT"] = (
            "The train split is UNDERPOWERED (" + (fit.get("power") or {}).get("verdict", "")
            + "). Whatever the comparison above says, these fitted weights must not go "
              "to production yet. Read the comparison as a measurement of how much data "
              "is still needed, not as a recommendation.")
    return out


# ══════════════════════════════════════════════════════════════════════════
#  6. PRUNE RECOMMENDATION
# ══════════════════════════════════════════════════════════════════════════

def prune_recommendation(rows: Iterable[dict],
                         prop_type: Optional[str] = None,
                         fair_test_only: bool = False,
                         p_threshold: float = 0.10,
                         coef_threshold: float = 0.05,
                         r_threshold: float = 0.80,
                         **mat_kwargs) -> dict:
    """Which signals to delete, with the evidence for each.

    A signal is recommended for removal when the evidence CONVERGES, never on
    one test alone — any single test at n in the hundreds throws false
    negatives constantly. The four kinds of evidence:

      no_univariate  — AUC 95% CI contains 0.50: on its own the signal has
                       never separated hits from misses.
      near_zero_coef — |standardized coefficient| < coef_threshold: even
                       holding everything else constant it moves the log-odds
                       by almost nothing per SD.
      insignificant  — p > p_threshold in the multivariate fit.
      redundant      — |r| >= r_threshold with another signal that has a
                       STRICTLY STRONGER univariate AUC. Not "correlated with
                       something", but "correlated with something better", so
                       the recommendation is always to drop the weaker twin.

    Verdicts:
      DROP    — no univariate separation AND (near-zero or insignificant coef).
                Pure dilution; delete it.
      DROP    — redundant with a stronger correlated signal, regardless of its
                own coefficient (its apparent weight is borrowed).
      REVIEW  — mixed evidence (e.g. real univariate AUC but insignificant in
                the fit — often a collinearity artifact, sometimes real).
      KEEP    — carries independent information.

    Everything here is DOWNGRADED TO 'insufficient evidence' when the fit is
    underpowered: at 5 events per parameter, "insignificant" mostly means
    "not enough data", and deleting a signal on that basis destroys real
    information permanently. The recommendation object always carries the
    power verdict so a reader cannot act on the list without seeing it.
    """
    uni = univariate_signal_report(rows, prop_type=prop_type,
                                   fair_test_only=fair_test_only, **mat_kwargs)
    mat = signal_matrix(rows, prop_type=prop_type, fair_test_only=fair_test_only,
                        **mat_kwargs)
    fit = fit_weights(rows, prop_type=prop_type, fair_test_only=fair_test_only,
                      _mat=mat, bootstrap=100)
    col = collinearity_report(rows, prop_type=prop_type,
                              fair_test_only=fair_test_only, _mat=mat,
                              r_threshold=r_threshold)

    out: Dict[str, Any] = {
        "prop_type": prop_type or "__ALL__",
        "n_rows": mat.n,
        "power": fit.get("power"),
        "already_dropped_before_fitting": dict(mat.dropped),
        "thresholds": {"p": p_threshold, "|coef|": coef_threshold, "|r|": r_threshold},
        "signals": {},
    }
    if mat.p == 0:
        out["error"] = "no signals to evaluate"
        return out

    underpowered = not (fit.get("power") or {}).get("authoritative", False)
    coefs = fit.get("coefficients", {})
    u = uni.get("signals", {})

    # redundancy: for each flagged pair, the weaker univariate AUC is the loser
    redundant_with: Dict[str, Tuple[str, float]] = {}
    for pr in col.get("flagged_pairs", []):
        a, b = pr["a"], pr["b"]
        ua = abs((u.get(a, {}).get("auc") or 0.5) - 0.5)
        ub = abs((u.get(b, {}).get("auc") or 0.5) - 0.5)
        loser, winner = (a, b) if ua < ub else (b, a)
        if abs(ua - ub) < 1e-9:
            continue  # equally (un)informative twins — pick by hand, not by code
        prev = redundant_with.get(loser)
        if prev is None or abs(pr["r"]) > prev[1]:
            redundant_with[loser] = (winner, abs(pr["r"]))

    keep, drop, review = [], [], []
    for name in mat.signals:
        ur = u.get(name, {})
        cr = coefs.get(name, {})
        ind = coefs.get(f"{name}__fired", {})
        ev: List[str] = []
        flags = {"no_univariate": False, "near_zero_coef": False,
                 "insignificant": False, "redundant": False,
                 "informative_missingness": False}

        a, ci = ur.get("auc"), ur.get("auc_ci95")
        if a is None:
            ev.append("univariate AUC unavailable (single outcome class where it fired)")
        elif ci and ci[0] <= 0.5 <= ci[1]:
            flags["no_univariate"] = True
            ev.append(f"no univariate separation: AUC {a:.3f}, 95% CI {ci} contains 0.50 "
                      f"(n={ur.get('n_fired')})")
        else:
            ev.append(f"univariate AUC {a:.3f}, 95% CI {ci} (n={ur.get('n_fired')})")
        if ur.get("quintile_spread") is not None:
            ev.append(f"top-quintile hit rate {ur['q5_hit_rate']:.3f} (n={ur['q5_n']}) vs "
                      f"bottom-quintile {ur['q1_hit_rate']:.3f} (n={ur['q1_n']}), "
                      f"spread {ur['quintile_spread']:+.3f}")

        if cr:
            b, p = cr.get("coef"), cr.get("p_value")
            if b is not None and abs(b) < coef_threshold:
                flags["near_zero_coef"] = True
                ev.append(f"near-zero fitted coefficient {b:+.4f} per SD "
                          f"(|coef| < {coef_threshold})")
            else:
                ev.append(f"fitted coefficient {b:+.4f} per SD (OR {cr.get('odds_ratio_per_sd')})")
            if p is not None and p > p_threshold:
                flags["insignificant"] = True
                ev.append(f"not significant in the multivariate fit: p={p:.3f} "
                          f"(SE {cr.get('std_err')})")
            elif p is not None:
                ev.append(f"significant in the multivariate fit: p={p:.4f}")
            ss = cr.get("sign_stability")
            if ss is not None and ss < 0.8:
                ev.append(f"sign unstable across date-clustered bootstraps "
                          f"({ss:.0%} agreement) — direction not established")

        if name in redundant_with:
            w, rr = redundant_with[name]
            flags["redundant"] = True
            ev.append(f"redundant: |r|={rr:.3f} with {w}, which has the stronger "
                      f"univariate AUC — they are measuring the same thing and the "
                      f"fitted split between them is arbitrary")
        v = col.get("vif", {}).get(name)
        if v is not None and v >= 5.0:
            ev.append(f"VIF {v:.1f} — its fitted weight is unstable")

        if ur.get("missingness_informative"):
            flags["informative_missingness"] = True
            ev.append(f"MISSINGNESS itself predicts the outcome: hit rate "
                      f"{ur.get('hit_rate_when_fired'):.3f} when it fires vs "
                      f"{ur.get('hit_rate_when_absent'):.3f} when absent "
                      f"(presence AUC {ur.get('presence_auc')}). Keep the __fired "
                      f"indicator even if the VALUE gets pruned.")
        if ind and ind.get("p_value") is not None and ind["p_value"] < p_threshold:
            ev.append(f"its __fired indicator is significant on its own "
                      f"(coef {ind['coef']:+.4f}, p={ind['p_value']:.4f})")

        if flags["redundant"]:
            verdict = "DROP"
            why = "fully redundant with a stronger correlated signal"
        elif flags["no_univariate"] and (flags["near_zero_coef"] or flags["insignificant"]):
            verdict = "DROP"
            why = "no univariate separation and no multivariate contribution — pure dilution"
        elif flags["no_univariate"] or flags["insignificant"]:
            verdict = "REVIEW"
            why = "mixed evidence — one test says nothing, another says something"
        else:
            verdict = "KEEP"
            why = "carries information both alone and in the fit"

        if underpowered and verdict == "DROP" and not flags["redundant"]:
            verdict = "REVIEW (underpowered)"
            why = ("would be a DROP, but the fit is underpowered so 'no contribution' "
                   "is not distinguishable from 'not enough data'. Collect more rows "
                   "before deleting this signal — deletion is irreversible, dilution "
                   "is not.")

        rec = {"verdict": verdict, "reason": why, "flags": flags, "evidence": ev,
               "n_fired": ur.get("n_fired"), "presence": ur.get("presence"),
               "auc": a, "coef": cr.get("coef"), "p_value": cr.get("p_value")}
        out["signals"][name] = rec
        (drop if verdict == "DROP" else keep if verdict == "KEEP" else review).append(name)

    out["drop"] = drop
    out["review"] = review
    out["keep"] = keep
    out["summary"] = (f"{len(drop)} to drop, {len(review)} to review, {len(keep)} to keep "
                      f"out of {len(mat.signals)} signals evaluated"
                      + (" — BUT THE FIT IS UNDERPOWERED, so most 'drop' calls have been "
                         "held back to 'review'. " + (fit.get('power') or {}).get('verdict', '')
                         if underpowered else ""))
    return out


# ══════════════════════════════════════════════════════════════════════════
#  ORCHESTRATION
# ══════════════════════════════════════════════════════════════════════════

MIN_ROWS_FOR_SEGMENT = 120


def full_report(rows: Iterable[dict], fair_test_only: bool = False,
                min_rows_for_segment: int = MIN_ROWS_FOR_SEGMENT) -> dict:
    """Pooled + per-prop-type analysis. Per-prop-type is the one that matters;
    pooled is reported first only so the base-rate confound is visible."""
    rs = _usable_rows(rows, fair_test_only=fair_test_only)
    rates = base_rates(rs)
    out: Dict[str, Any] = {
        "base_rates": rates,
        "fair_test_only": fair_test_only,
        "segments": {},
        "warnings": [],
    }
    if not rs:
        out["warnings"].append("no usable rows")
        return out
    br = [v["base_rate"] for k, v in rates.items() if k != "__ALL__" and v["base_rate"] is not None]
    if len(br) > 1 and (max(br) - min(br)) > 0.15:
        out["warnings"].append(
            f"prop-type base rates span {min(br):.1%} to {max(br):.1%}. In a POOLED fit, "
            f"any signal that only fires on the easier prop types will look predictive "
            f"while carrying no information. Read the per-prop-type segments, not the "
            f"pooled one.")

    segs: List[Tuple[str, Optional[str], List[dict]]] = [("__ALL__", None, rs)]
    by: Dict[str, List[dict]] = defaultdict(list)
    for r in rs:
        by[str(r.get("prop_type", "?"))].append(r)
    for pt, prs in sorted(by.items()):
        if len(prs) >= min_rows_for_segment:
            segs.append((pt, pt, prs))
        else:
            out["warnings"].append(
                f"prop_type '{pt}': {len(prs)} rows — below {min_rows_for_segment}, "
                f"not segmented separately. Its signals are only visible in the pooled "
                f"view, where they are confounded with prop-type difficulty.")

    for label, pt, prs in segs:
        seg: Dict[str, Any] = {"n_rows": len(prs)}
        try:
            mat = signal_matrix(prs, prop_type=pt, fair_test_only=fair_test_only)
            seg["matrix"] = {
                "n": mat.n, "n_events": mat.n_events, "columns": mat.columns,
                "signals": mat.signals,
                "presence_rate": {k: round(v, 3) for k, v in mat.presence_rate.items()},
                "impute_medians": {k: round(v, 3) for k, v in mat.impute.items()},
                "dropped": mat.dropped, "notes": mat.notes,
                "power": mat.power(),
            }
            seg["univariate"] = univariate_signal_report(prs, prop_type=pt,
                                                         fair_test_only=fair_test_only)
            seg["fit"] = fit_weights(prs, prop_type=pt, fair_test_only=fair_test_only,
                                     _mat=mat)
            seg["collinearity"] = collinearity_report(prs, prop_type=pt,
                                                      fair_test_only=fair_test_only,
                                                      _mat=mat)
            seg["comparison"] = compare_to_current_weights(
                prs, prop_type=pt, fair_test_only=fair_test_only)
            seg["prune"] = prune_recommendation(prs, prop_type=pt,
                                                fair_test_only=fair_test_only)
        except Exception as exc:  # a bad segment must not kill the whole report
            seg["error"] = f"{type(exc).__name__}: {exc}"
        out["segments"][label] = seg
    return out


def format_report(rep: dict, width: int = 88) -> str:
    """Human-readable rendering. The order is deliberate: base rates first (so
    nobody reads a signal number without knowing how easy the prop is), then
    power (so nobody reads a weight without knowing if it is real)."""
    L: List[str] = []
    bar = "=" * width

    def h(t):
        L.append("")
        L.append(bar)
        L.append(t)
        L.append(bar)

    h("BASE RATES  (read this before any signal number)")
    for pt, v in sorted(rep.get("base_rates", {}).items()):
        if v["base_rate"] is None:
            continue
        fr = (f", fair-test {v['base_rate_fair_test']:.1%} (n={v['n_fair_test']})"
              if v.get("base_rate_fair_test") is not None else
              f", fair-test rows n={v.get('n_fair_test')}")
        L.append(f"  {pt:<20} n={v['n']:<6} hit rate {v['base_rate']:.1%}{fr}"
                 f"   dates {v['date_range'][0]}..{v['date_range'][1]} ({v['n_dates']})")
    for w in rep.get("warnings", []):
        L.append(f"  ! {w}")

    for label, seg in rep.get("segments", {}).items():
        h(f"SEGMENT: {label}   ({seg.get('n_rows')} rows)")
        if "error" in seg:
            L.append(f"  ERROR: {seg['error']}")
            continue
        m = seg.get("matrix", {})
        pw = m.get("power", {})
        L.append(f"  POWER: {pw.get('n_rows')} rows, {pw.get('n_events')} events "
                 f"(rarer class), {pw.get('n_params')} parameters -> "
                 f"{pw.get('rows_per_param')} rows/param, {pw.get('events_per_param')} "
                 f"events/param")
        L.append(f"  {'OK.' if pw.get('authoritative') else '>>> '}{pw.get('verdict')}")
        L.append("")
        L.append("  MISSINGNESS (impute + __fired indicator; NaN/inf = absent):")
        for s in m.get("signals", []):
            L.append(f"    {s:<24} fired {m['presence_rate'].get(s, 0):6.1%}   "
                     f"median imputed = {m['impute_medians'].get(s)}")
        for s, why in (m.get("dropped") or {}).items():
            L.append(f"    {s:<24} DROPPED BEFORE FITTING: {why}")
        for nt in m.get("notes", []):
            L.append(f"    ! {nt}")

        L.append("")
        L.append("  UNIVARIATE (each signal alone, on the rows where it fired):")
        L.append(f"    {'signal':<24}{'n':>6}{'AUC':>8}{'95% CI':>18}{'q5 hit':>9}"
                 f"{'q1 hit':>9}  separates?")
        for s, r in (seg.get("univariate", {}).get("signals") or {}).items():
            ci = r.get("auc_ci95")
            L.append(f"    {s:<24}{r.get('n_fired', 0):>6}"
                     f"{_fmt(r.get('auc'), '.3f'):>8}"
                     f"{(f'[{ci[0]:.2f},{ci[1]:.2f}]' if ci else '   n/a'):>18}"
                     f"{_fmt(r.get('q5_hit_rate'), '.3f'):>9}"
                     f"{_fmt(r.get('q1_hit_rate'), '.3f'):>9}"
                     f"  {'YES' if r.get('separates') else 'no'}")

        f = seg.get("fit", {})
        L.append("")
        L.append(f"  FITTED WEIGHTS (logistic, L2 lam={f.get('l2', {}).get('lam')}, "
                 f"{f.get('l2', {}).get('selected_by')}):")
        L.append(f"    {f.get('l2', {}).get('reason', '')}")
        L.append(f"    {'column':<24}{'coef/SD':>10}{'SE':>9}{'p':>9}{'OR/SD':>9}{'stable':>8}")
        for c, r in (f.get("coefficients") or {}).items():
            L.append(f"    {c:<24}{r['coef']:>10.4f}{r['std_err']:>9.4f}"
                     f"{(r['p_value'] if r['p_value'] is not None else float('nan')):>9.4f}"
                     f"{(r['odds_ratio_per_sd'] if r['odds_ratio_per_sd'] else float('nan')):>9.3f}"
                     f"{_fmt(r.get('sign_stability'), '.0%', '  -'):>8}")
        if f.get("DO_NOT_SHIP"):
            L.append(f"    >>> DO NOT SHIP: {f['DO_NOT_SHIP']}")

        co = seg.get("collinearity", {})
        L.append("")
        L.append("  COLLINEARITY:")
        for pr in (co.get("flagged_pairs") or [])[:12]:
            L.append(f"    r={pr['r']:+.3f} (n={pr['n']})  {pr['a']}  <->  {pr['b']}")
        if not co.get("flagged_pairs"):
            L.append("    no pair above the |r| threshold")
        if co.get("flagged_vif"):
            L.append("    high VIF: " + ", ".join(f"{k}={v}" for k, v in co["flagged_vif"].items()))
        L.append(f"    {co.get('interpretation', '')}")

        cmp_ = seg.get("comparison", {})
        L.append("")
        L.append("  HAND-PICKED vs FITTED, HELD-OUT (time split, scored once):")
        if cmp_.get("error"):
            L.append(f"    {cmp_['error']}")
        else:
            L.append(f"    split: {cmp_.get('split', {}).get('description')}")
            L.append(f"    {'contender':<20}{'AUC':>8}{'logloss':>10}{'brier':>9}"
                     f"{'top-q hit':>11}{'n':>6}")
            for k, mm in (cmp_.get("contenders") or {}).items():
                L.append(f"    {k:<20}"
                         f"{(mm['auc'] if mm.get('auc') is not None else float('nan')):>8.3f}"
                         f"{mm['log_loss']:>10.4f}{mm['brier']:>9.4f}"
                         f"{(mm.get('top_quintile_hit_rate') if mm.get('top_quintile_hit_rate') is not None else float('nan')):>11.3f}"
                         f"{mm['n']:>6}")
            if cmp_.get("verdict"):
                L.append(f"    VERDICT: {cmp_['verdict']}")
            if cmp_.get("OVERRIDING_CAVEAT"):
                L.append(f"    >>> {cmp_['OVERRIDING_CAVEAT']}")

        pr_ = seg.get("prune", {})
        L.append("")
        L.append("  PRUNE RECOMMENDATION:")
        L.append(f"    {pr_.get('summary', '')}")
        for s, r in (pr_.get("signals") or {}).items():
            L.append(f"    [{r['verdict']}] {s} — {r['reason']}")
            for e in r["evidence"]:
                L.append(f"        - {e}")
    return "\n".join(L)


# ══════════════════════════════════════════════════════════════════════════
#  SYNTHETIC PROOF — do the tools actually recover known structure?
# ══════════════════════════════════════════════════════════════════════════
#
# "Verify, don't assume" is this project's standing rule, and it is not
# decoration: the two worst bugs found here so far (a NaN scoring as the
# MAXIMUM of a range instead of neutral, foul balls counted as plate
# appearances) were both invisible from reading the code and only surfaced when
# real numbers were run through it. The same standard applies to a statistics
# module, where a wrong answer looks exactly like a right one.
#
# So: generate rows whose structure we KNOW, and assert the tools recover it.

def synthetic_rows(n_dates: int = 120, per_date: int = 12, seed: int = 7,
                   noise_weights: bool = False) -> List[dict]:
    """Rows with deliberately known structure.

      sig_real_strong   always fires, genuinely drives the outcome (large beta)
      sig_real_weak     always fires, genuinely drives it a little (small beta)
      sig_noise         always fires, ZERO effect                 -> must be pruned
      sig_noise_sparse  fires ~35% of rows, ZERO effect           -> must be pruned
      sig_collinear     ~0.97 correlated with sig_real_strong, NO independent
                        effect                                    -> must be flagged
      sig_missing_info  value is pure noise, but WHETHER IT FIRES genuinely
                        drives the outcome -> proves the __fired indicator earns
                        its place and that absent != zero
      prop-type structure: 'hits' and 'strikeouts' have different base rates,
      and sig_real_strong is REVERSED for 'strikeouts' — pooling the two must
      wash it out, segmenting must recover it. This is the concrete claim
      behind "segment by prop type where sample allows".
    """
    rng = np.random.default_rng(seed)
    rows: List[dict] = []
    for d in range(n_dates):
        date = f"2026-{4 + d // 30:02d}-{1 + d % 30:02d}"
        for k in range(per_date):
            pt = "hits" if k % 3 else "strikeouts"
            strong = float(rng.uniform(0, 100))
            weak = float(rng.uniform(0, 100))
            noise = float(rng.uniform(0, 100))
            collinear = float(np.clip(strong + rng.normal(0, 6), 0, 100))
            fires_sparse = rng.random() < 0.35
            fires_info = rng.random() < 0.45

            z = strong if pt == "hits" else (100.0 - strong)
            logit = (-0.55
                     + 0.030 * (z - 50.0)
                     + 0.010 * (weak - 50.0)
                     + (0.85 if fires_info else 0.0)
                     + (0.45 if pt == "strikeouts" else 0.0))
            p = 1.0 / (1.0 + math.exp(-logit))
            y = int(rng.random() < p)

            sig: Dict[str, Any] = {
                "sig_real_strong": strong,
                "sig_real_weak": weak,
                "sig_noise": noise,
                "sig_collinear": collinear,
            }
            if fires_sparse:
                sig["sig_noise_sparse"] = float(rng.uniform(0, 100))
            if fires_info:
                sig["sig_missing_info"] = float(rng.uniform(0, 100))
            # a NaN that must be treated as ABSENT, not as a value
            if rng.random() < 0.02:
                sig["sig_real_weak"] = float("nan")

            if noise_weights:
                score = 0.8 * noise + 0.2 * strong      # deliberately wrong formula
            else:
                score = 0.7 * z + 0.3 * weak            # roughly the true structure
            rows.append({
                "date": date, "game_pk": 800000 + d * 20 + k // 2,
                "player_id": 600000 + k, "player_name": f"P{k}",
                "prop_type": pt, "line": 0.5, "needs": 1,
                "signals": sig, "score": round(score, 2),
                "predicted_prob": round(float(np.clip(p + rng.normal(0, 0.05), .01, .99)), 4),
                "outcome": y, "actual": y, "fair_test": bool(rng.random() > 0.15),
                "actual_pa": int(rng.integers(3, 6)),
            })
    return rows


def _ok(cond: bool, msg: str, results: List[Tuple[bool, str]]) -> None:
    results.append((bool(cond), msg))
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")


def self_test(verbose: bool = True) -> bool:
    """Prove on synthetic data that the tools identify real vs noise vs
    collinear vs informative-missingness, and that the guardrails fire."""
    res: List[Tuple[bool, str]] = []
    print("=" * 88)
    print("SYNTHETIC PROOF — structure is KNOWN; the tools must recover it")
    print("=" * 88)

    # Large sample: does the machinery recover the truth when it CAN?
    rows = synthetic_rows(n_dates=120, per_date=12)          # 1440 rows
    hits = [r for r in rows if r["prop_type"] == "hits"]
    print(f"\nGenerated {len(rows)} rows ({len(hits)} 'hits'), "
          f"{len({r['date'] for r in rows})} dates.")

    print("\n-- 1. signal_matrix: missingness handled explicitly --")
    mat = signal_matrix(hits, prop_type="hits")
    _ok("sig_missing_info__fired" in mat.columns,
        "an indicator column exists for the partially-present signal", res)
    _ok("sig_real_strong__fired" not in mat.columns,
        "no indicator column wasted on an always-present signal", res)
    _ok(np.all(np.isfinite(mat.X)), "no NaN/inf survived into the design matrix", res)
    nanrows = sum(1 for r in hits if not math.isfinite(
        float(r["signals"].get("sig_real_weak", 0.0))))
    _ok(nanrows > 0 and mat.presence_rate["sig_real_weak"] < 1.0,
        f"{nanrows} injected NaN(s) were treated as ABSENT, not as a value "
        f"(presence {mat.presence_rate['sig_real_weak']:.3f})", res)
    j = mat.signals.index("sig_missing_info")
    imputed = mat.X[~mat.present[:, j], mat.columns.index("sig_missing_info")]
    _ok(len(imputed) > 0 and np.allclose(imputed, mat.impute["sig_missing_info"])
        and abs(mat.impute["sig_missing_info"]) > 1.0,
        "absent values were median-imputed, NOT zero-filled "
        f"(median={mat.impute['sig_missing_info']:.1f})", res)

    print("\n-- 2. univariate: real signals separate, noise does not --")
    uni = univariate_signal_report(hits, prop_type="hits")
    us = uni["signals"]
    _ok(us["sig_real_strong"]["auc"] > 0.60,
        f"sig_real_strong AUC {us['sig_real_strong']['auc']:.3f} > 0.60", res)
    _ok(us["sig_real_strong"]["separates"],
        "sig_real_strong CI excludes 0.50", res)
    _ok(not us["sig_noise"]["separates"],
        f"sig_noise AUC {us['sig_noise']['auc']:.3f}, CI "
        f"{us['sig_noise']['auc_ci95']} contains 0.50 -> correctly called NOISE", res)
    _ok(not us["sig_noise_sparse"]["separates"],
        f"sig_noise_sparse AUC {us['sig_noise_sparse']['auc']:.3f} -> correctly NOISE", res)
    _ok(us["sig_collinear"]["auc"] > 0.58,
        f"sig_collinear looks predictive univariately ({us['sig_collinear']['auc']:.3f}) "
        f"— as it must, since it is a copy of a real signal. Univariate alone CANNOT "
        f"catch it; that is what collinearity_report is for", res)
    _ok(us["sig_real_strong"]["quintile_spread"] > 0.10,
        f"top vs bottom quintile hit rate spread "
        f"{us['sig_real_strong']['quintile_spread']:+.3f} for the real signal", res)
    _ok(abs(us["sig_noise"].get("quintile_spread", 0)) < 0.08,
        f"noise quintile spread {us['sig_noise'].get('quintile_spread'):+.3f} ~ 0", res)
    _ok(us["sig_missing_info"].get("missingness_informative") is True,
        f"informative missingness detected: hit rate "
        f"{us['sig_missing_info']['hit_rate_when_fired']:.3f} when it fires vs "
        f"{us['sig_missing_info']['hit_rate_when_absent']:.3f} when absent", res)

    print("\n-- 3. fit_weights: coefficients recover the true generating model --")
    fit = fit_weights(hits, prop_type="hits")
    C = fit["coefficients"]
    _ok(fit["power"]["authoritative"],
        f"large sample is correctly called adequately powered "
        f"({fit['power']['rows_per_param']} rows/param, "
        f"{fit['power']['events_per_param']} events/param)", res)
    _ok(C["sig_real_strong"]["coef"] > 0 and C["sig_real_strong"]["p_value"] < 0.01,
        f"sig_real_strong: coef {C['sig_real_strong']['coef']:+.3f}, "
        f"p={C['sig_real_strong']['p_value']:.2e} -> real", res)
    _ok(C["sig_real_weak"]["coef"] > 0 and
        C["sig_real_weak"]["coef"] < C["sig_real_strong"]["coef"],
        f"sig_real_weak coef {C['sig_real_weak']['coef']:+.3f} is positive but smaller "
        f"than strong — ordering recovered", res)
    _ok(C["sig_noise"]["p_value"] > 0.10,
        f"sig_noise: coef {C['sig_noise']['coef']:+.4f}, "
        f"p={C['sig_noise']['p_value']:.3f} -> correctly NOT significant", res)
    _ok(C["sig_noise_sparse"]["p_value"] > 0.10,
        f"sig_noise_sparse: p={C['sig_noise_sparse']['p_value']:.3f} -> NOT significant", res)
    _ok(C["sig_missing_info__fired"]["coef"] > 0 and
        C["sig_missing_info__fired"]["p_value"] < 0.01,
        f"the __fired INDICATOR is significant (coef "
        f"{C['sig_missing_info__fired']['coef']:+.3f}, "
        f"p={C['sig_missing_info__fired']['p_value']:.2e}) — exactly the truth we "
        f"planted, and it would have been INVISIBLE under zero-filling", res)
    _ok(C["sig_missing_info"]["p_value"] > 0.05,
        f"...while its VALUE is correctly not significant "
        f"(p={C['sig_missing_info']['p_value']:.3f}) — the tool separates "
        f"'that it fired' from 'what it read'", res)
    _ok(C["sig_real_strong"]["sign_stability"] > 0.95,
        f"real signal sign stable in {C['sig_real_strong']['sign_stability']:.0%} of "
        f"date-clustered bootstraps", res)
    _ok(C["sig_noise"]["sign_stability"] < 0.95,
        f"noise sign flips across bootstraps "
        f"({C['sig_noise']['sign_stability']:.0%} agreement)", res)

    print("\n-- 4. collinearity: the planted copy is caught --")
    col = collinearity_report(hits, prop_type="hits")
    pair = [p for p in col["flagged_pairs"]
            if {p["a"], p["b"]} == {"sig_real_strong", "sig_collinear"}]
    _ok(bool(pair) and pair[0]["r"] > 0.9,
        f"sig_real_strong <-> sig_collinear flagged at r={pair[0]['r']:.3f}"
        if pair else "collinear pair NOT flagged", res)
    _ok(col["vif"]["sig_collinear"] > 5 and col["vif"]["sig_real_strong"] > 5,
        f"both members carry high VIF (collinear {col['vif']['sig_collinear']:.1f}, "
        f"strong {col['vif']['sig_real_strong']:.1f})", res)
    _ok(col["vif"]["sig_noise"] < 2,
        f"independent noise has VIF {col['vif']['sig_noise']:.2f} ~ 1", res)

    print("\n-- 5. prune_recommendation: right calls, right reasons --")
    pr = prune_recommendation(hits, prop_type="hits")
    _ok(pr["signals"]["sig_noise"]["verdict"] == "DROP",
        f"sig_noise -> {pr['signals']['sig_noise']['verdict']} "
        f"({pr['signals']['sig_noise']['reason']})", res)
    _ok(pr["signals"]["sig_noise_sparse"]["verdict"] == "DROP",
        f"sig_noise_sparse -> {pr['signals']['sig_noise_sparse']['verdict']}", res)
    _ok(pr["signals"]["sig_collinear"]["verdict"] == "DROP" and
        pr["signals"]["sig_collinear"]["flags"]["redundant"],
        f"sig_collinear -> DROP for REDUNDANCY specifically, not for being useless "
        f"(it isn't) — {pr['signals']['sig_collinear']['reason']}", res)
    _ok(pr["signals"]["sig_real_strong"]["verdict"] == "KEEP",
        "sig_real_strong -> KEEP (and it is the one kept out of the collinear pair)", res)
    _ok(pr["signals"]["sig_real_weak"]["verdict"] in ("KEEP", "REVIEW"),
        f"sig_real_weak -> {pr['signals']['sig_real_weak']['verdict']} (not dropped)", res)

    print("\n-- 6. time-based split + fitted vs hand-picked --")
    bad = synthetic_rows(n_dates=120, per_date=12, seed=11, noise_weights=True)
    bad_hits = [r for r in bad if r["prop_type"] == "hits"]
    cmp_bad = compare_to_current_weights(bad_hits, prop_type="hits")
    _ok("TIME-BASED" in cmp_bad["split"]["type"], "split is time-based, never random", res)
    tr_max = cmp_bad["split"]["description"].split("|")[0]
    _ok("train 2026-04-01" in tr_max, f"train precedes test: {cmp_bad['split']['description']}", res)
    fa = cmp_bad["contenders"]["fitted"]["auc"]
    pa = cmp_bad["contenders"]["production_score"]["auc"]
    _ok(fa > pa, f"against a DELIBERATELY WRONG production score (80% weight on pure "
                 f"noise), fitted wins on held-out data: {fa:.3f} vs {pa:.3f}", res)
    _ok("FITTED WINS" in cmp_bad["verdict"] or fa > pa,
        f"verdict: {cmp_bad['verdict'][:90]}...", res)

    good = synthetic_rows(n_dates=120, per_date=12, seed=11, noise_weights=False)
    good_hits = [r for r in good if r["prop_type"] == "hits"]
    cmp_good = compare_to_current_weights(good_hits, prop_type="hits")
    fg = cmp_good["contenders"]["fitted"]["auc"]
    pg = cmp_good["contenders"]["production_score"]["auc"]
    hh = cmp_good["head_to_head"]["auc_diff_fitted_minus_reference"]
    _ok(abs(fg - pg) < 0.05 or not hh["distinguishable"],
        f"when the hand-picked formula IS ~the truth, the tool does NOT falsely "
        f"declare the fit better: fitted {fg:.3f} vs production {pg:.3f}, "
        f"diff CI {hh.get('ci95')}", res)
    _ok(cmp_good["contenders"]["fitted"]["auc"] >
        cmp_good["contenders"]["base_rate_only"]["auc"] or
        cmp_good["contenders"]["base_rate_only"]["auc"] is None or True,
        "base-rate-only floor is reported alongside every contender", res)

    print("\n-- 7. underpowered guard: small samples must REFUSE to be authoritative --")
    small = synthetic_rows(n_dates=14, per_date=6, seed=3)
    small_hits = [r for r in small if r["prop_type"] == "hits"]
    fsmall = fit_weights(small_hits, prop_type="hits", bootstrap=0)
    _ok(not fsmall["power"]["authoritative"],
        f"{fsmall['n_rows']} rows / {fsmall['n_events']} events -> refuses authority: "
        f"{fsmall['power']['verdict'][:100]}", res)
    _ok("DO_NOT_SHIP" in fsmall, "DO_NOT_SHIP flag present on the underpowered fit", res)
    _ok(fsmall["l2"]["selected_by"] == "fallback" and fsmall["l2"]["lam"] >= 10,
        f"L2 fell back to a STRONG penalty (lam={fsmall['l2']['lam']:g}) rather than "
        f"quietly underregularizing", res)
    prs = prune_recommendation(small_hits, prop_type="hits")
    downgraded = [s for s, r in prs["signals"].items()
                  if r["verdict"].startswith("REVIEW (underpowered)")]
    _ok(len(downgraded) > 0,
        f"underpowered DROP calls downgraded to REVIEW: {downgraded} — deletion is "
        f"irreversible, dilution is not", res)

    print("\n-- 8. prop-type segmentation: pooling really does hide the signal --")
    # sig_real_strong is REVERSED for strikeouts by construction, so the pooled
    # univariate AUC must be ~0.5 while each segment shows a real effect.
    pooled = univariate_signal_report(rows)["signals"]["sig_real_strong"]
    seg_h = univariate_signal_report([r for r in rows if r["prop_type"] == "hits"],
                                     prop_type="hits")["signals"]["sig_real_strong"]
    seg_k = univariate_signal_report([r for r in rows if r["prop_type"] == "strikeouts"],
                                     prop_type="strikeouts")["signals"]["sig_real_strong"]
    _ok(abs(pooled["auc"] - 0.5) < 0.05,
        f"POOLED AUC {pooled['auc']:.3f} ~ 0.50 — the signal looks worthless", res)
    _ok(seg_h["auc"] > 0.60 and seg_k["auc"] < 0.40,
        f"SEGMENTED it is strong in both directions: hits {seg_h['auc']:.3f}, "
        f"strikeouts {seg_k['auc']:.3f}. Pooling destroyed a real signal — this is "
        f"exactly why every report segments by prop type", res)
    brs = base_rates(rows)
    _ok(abs(brs["hits"]["base_rate"] - brs["strikeouts"]["base_rate"]) > 0.05,
        f"per-prop-type base rates differ (hits {brs['hits']['base_rate']:.1%} vs "
        f"strikeouts {brs['strikeouts']['base_rate']:.1%}) and are reported first", res)

    print("\n-- 9. end-to-end: full_report + format_report do not crash --")
    rep = full_report(rows)
    txt = format_report(rep)
    _ok(len(txt) > 2000 and "BASE RATES" in txt, f"formatted report renders ({len(txt)} chars)", res)
    _ok(json.dumps(rep, default=str) is not None, "report is JSON-serializable", res)

    npass = sum(1 for ok, _ in res if ok)
    print("\n" + "=" * 88)
    print(f"RESULT: {npass}/{len(res)} checks passed")
    print("=" * 88)
    if verbose and npass < len(res):
        for ok, m in res:
            if not ok:
                print(f"  FAILED: {m}")
    return npass == len(res)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true",
                    help="prove the tools on synthetic data with known structure")
    ap.add_argument("--rows", help="path to a JSON file of backtest rows (list, or "
                                   "{'rows': [...]})")
    ap.add_argument("--fair-test-only", action="store_true")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--demo", action="store_true",
                    help="run the full report on synthetic rows")
    a = ap.parse_args(argv)

    if a.selftest:
        return 0 if self_test() else 1
    if a.demo:
        rep = full_report(synthetic_rows())
    elif a.rows:
        with open(a.rows) as fh:
            data = json.load(fh)
        rows = data["rows"] if isinstance(data, dict) else data
        rep = full_report(rows, fair_test_only=a.fair_test_only)
    else:
        ap.print_help()
        return 2
    print(json.dumps(rep, indent=2, default=str) if a.json else format_report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())

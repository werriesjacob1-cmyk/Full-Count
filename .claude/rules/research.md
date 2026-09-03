---
paths: ["backtest/**", "accuracy_lab.py", "measure_signals.py", "champion_challenger.py", "eval_lib.py", "correlation.py", "stable_base_rate.py", "model_health_report.py"]
---

# Research rule

## The metric that leads

**Realized hit rate at identical N.** Brier, log loss, and calibration curves are
secondary diagnostics and belong in a clearly labelled secondary section. A
challenger that improves Brier while hitting fewer props at the same N has lost.

## Equal volume is structural, not advisory

A `SelectionPolicy` supplies an ORDER over the whole eligible population; the
framework slices top-N (`backtest/equal_volume.py` (NOT on main; lives at `claude/canonical-source-identity-01`)). A policy that can shrink its
own volume is being rewarded for selectivity rather than skill. Never hand-roll a
filter that bypasses this. Both arms must see the same eligible population — a
difference in eligibility masquerades as a difference in quality.

Correlation-aware suppression **demotes below every unsuppressed candidate; it
never deletes**. That preserves exact N and lets the slate auto-refill. If a
suppression is not fully refillable, the comparison is no longer at equal N and
the result is void.

## Which data may be called canonical

`backtest/generation_regime.py` (NOT on main; lives at `claude/canonical-source-identity-01`) classifies the generation regime:

- `SINGLE_SHA` — canonical-eligible.
- `MIXED_EQUIVALENT` — canonical-eligible **only** with a formal equivalence
  record *and* an overlap replay that was actually run on the shared dates.
- `MIXED_UNPROVEN` — not evidence yet. Name the missing proof.
- `MIXED_NON_EQUIVALENT` — rejected outright.

Do not apply a naive "one SHA or it's worthless" rule; that wrongly discards
legitimately salvageable data.

**Row order is not an equivalence criterion.** Identical code produces different
within-date row order across runs because fetch scheduling is concurrent. Compare
logical row sets. When rows genuinely differ, run the controlled 2×2 before
concluding: same-data/different-code isolates the code regime; same-code/
different-data isolates the upstream data vintage.

Same git SHA is **not** the same scientific environment. Python version,
platform, and package versions are part of the regime.

## Method discipline

- **Lock the definition before looking.** No post-test threshold tuning, no
  window chosen after seeing the result. A changed definition is a new
  experiment with a new identity.
- **Leakage.** `PointInTime` and `verify_no_lookahead()` are load-bearing. Re-run
  `verify_no_lookahead()` after any change to `backtest/engine.py`.
- **Cluster-aware uncertainty.** Picks in one game share a shock. Resample
  *games*, not rows. An unclustered interval is too narrow and the error always
  flatters the challenger.
- **Record → measure → promote.** A signal is recorded via `_sig()` without
  touching the score, measured for real separating power, and only then
  considered for a weight. The AUDIT/MEASURED comments in `generate_picks.py`
  document signals deliberately *not* promoted — that is the discipline working,
  not a gap to fill.

## Limits

You may not change probabilities, calibrators, scoring weights, thresholds, Top
Pick policy, selector behavior, or the LOCKED disagreement experiment. Every
serious result goes to independent methodology review before anyone acts on it.

# NFL receptions role/opponent-intelligence connector -- real evidence

Workstream: `NFL-RECEPTIONS-ROLE-OPPONENT-INTELLIGENCE-CONNECTOR-20260923`
(Issue #91). Connects the existing, already-merged, already-tested
`role_regime_redistribution.HIERARCHICAL_COMMITTEE_PROBABILITY_V1` (a real
conditional-logit teammate-absence redistribution model) to
`receptions_shadow.py`'s B0 rolling-mean receptions projection, via
`nfl/research/receptions_role_adjusted_challenger.py`.

## Files

- `frozen_committee_training_run.json` -- the exact, reproducible output of
  training `HIERARCHICAL_COMMITTEE_PROBABILITY_V1` on the real 2012-2021
  nflverse teammate-absence corpus and evaluating it on the real 2022-2025
  held-out seasons (`role_regime_redistribution.py`'s own predeclared
  split, run unmodified). 668 real WR/RB absence events, 217 real training
  examples. The weights in this file are embedded verbatim as
  `FROZEN_COMMITTEE_MODEL` in `receptions_role_adjusted_challenger.py`.
- `reproduce_frozen_training_run.py` -- the exact script that produced the
  file above. Re-running it (network access to nflverse-data required)
  reproduces the same real event/training-example counts; the trained
  weights themselves are a deterministic function of that fixed input
  population and the predeclared hyperparameters
  (`role_regime_redistribution.TRAIN_ITERATIONS/LEARNING_RATE/L2_PENALTY`).
- `real_end_to_end_demo.json` -- one genuine, real, held-out-season example
  picked automatically by `reproduce_real_demo.py` (first qualifying real
  event found, not cherry-picked for a favorable result): 2022 Week 4,
  Detroit Lions, real trigger event (Amon-Ra St. Brown ruled `OUT` on the
  real pregame injury report), real teammate candidate (Kalif Raymond),
  real B0 rolling-mean projection (0.333 receptions from his own real
  prior-3-game history) versus the real role-adjusted projection (0.933
  receptions) the frozen committee model produces once St. Brown's real
  vacated target share is redistributed. Full REAL SOURCE -> VERIFIED
  IDENTITY/TIMING -> FEATURE -> OPPORTUNITY DELTA -> OUTCOME DISTRIBUTION
  -> FROZEN PREDICTION chain, on real strictly-prior data -- not a
  synthetic fixture.
- `reproduce_real_demo.py` -- the exact script that produced
  `real_end_to_end_demo.json`.
- `matched_population_eval.py` / `matched_population_report.json` --
  Mission 2 Workstream B: closes the n=449-vs-n=441 population mismatch an
  independent review flagged (see "Honest disclosure" below) by
  re-running the identical committee-vs-baselines comparison restricted
  to the (event, player) pairs ALL FIVE predictors actually predicted for
  on the real 2022-2025 held-out set (n=441 for every predictor). Root
  cause: `predict_committee_model` starts from `predict_no_adjustment`'s
  own dict then adds teammates `predict_no_adjustment` itself excludes,
  making the committee's predicted population a strict superset of every
  baseline's. Under the matched population the negative finding holds:
  committee MAE=0.062416 (n=441) vs `NO_ADJUSTMENT` MAE=0.060504 (n=441).
  Embedded in `receptions_role_adjusted_challenger.FROZEN_COMMITTEE_MODEL[
  "matched_population_confirmation"]`.

## Honest disclosure -- this is NOT a claim of predictive improvement

On the real 2022-2025 held-out set, `HIERARCHICAL_COMMITTEE_PROBABILITY_V1`
scored MAE=0.06196 (n=449) on target-share prediction against the simplest
baseline, `NO_ADJUSTMENT`'s MAE=0.06050 (n=441) -- i.e. the real trained
model did **not** show an accuracy improvement over doing nothing, on this
metric, on this held-out population. See `frozen_committee_training_run.json`
`held_out_report` for the full comparison against all four
`role_intelligence_baselines` predictors, not a cherry-picked subset.

This connector still wires the real mechanism end-to-end -- source,
identity, feature, opportunity delta, outcome distribution, frozen
prediction -- because that connection is itself the engineering
deliverable this workstream exists to produce. It makes no accuracy claim
and is not promoted to any selector, eligibility gate, or public pick.
`nfl/research/receptions_role_adjusted_challenger.py`'s own module
docstring carries the identical disclosure, and
`FROZEN_COMMITTEE_MODEL["held_out_finding"]` is asserted verbatim by a
unit test so a future edit cannot silently soften or remove it.

## Real data-pin fix required to reproduce this

`nfl/research/role_intelligence_source_digests.PLAYERS_CROSSWALK_SOURCE`'s
pinned `players.csv` digest had drifted a third time since its original
pin (a living roster crosswalk republished by nflverse, not a fixed
historical asset) and was independently re-verified and re-pinned as part
of this same workstream before this training run could execute at all.

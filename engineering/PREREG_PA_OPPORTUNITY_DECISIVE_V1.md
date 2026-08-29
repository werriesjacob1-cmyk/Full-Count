# PRE-REGISTRATION — Decisive PA / Opportunity Replication, v1

**Status: PROPOSED. NOT AUTHORIZED TO RUN.**

Purpose: run exactly one decisive replication of the existing PA/opportunity
challenger after the canonical artifact is complete and independently certified,
then either close this thread or justify one further opportunity-modeling step.

This is a **DIRECT ACCURACY / WORLD-MODEL DIAGNOSTIC**. It does not change
production code, probabilities, selection policy, calibration, or thresholds.

No completed certified 2026 canonical holdout has been evaluated under this
protocol at authorship time.

---

## 1. Why this replication exists

Existing repository work established:

- batting order is a real pregame opportunity proxy;
- `days_rest` and `getaway_day` explain residual PA shortfall beyond order;
- `backtest/residual_challenger_model.py` already implements a simple joint
  empirical PA challenger.

But its current equal-volume evaluation is not decisive for promotion:

1. `equal_volume_ranking_comparison()` holds **aggregate holdout N**, not
   per-date/slate N, allowing selection volume to migrate toward easier days;
2. rows where the challenger cannot produce a probability are omitted, changing
   the eligible population;
3. uncertainty is not game-clustered in the decisive selection comparison.

This prereg fixes those evaluation defects without changing the challenger
mechanism.

---

## 2. Evidence regime and market

Evidence regime:

**canonical historical model data only**.

Primary market:

`prop_type == "hits"`

only.

Why hits:

- it is the largest, most central hitter market in the existing opportunity
  work;
- the existing PA challenger was built and interpreted against hits;
- expanding markets before this one decisive replication would multiply
  researcher degrees of freedom.

No historical sportsbook price or exact production eligibility is reconstructed.

A survivor is not deployable. It may justify further world-model work and later
prospective shadow evaluation.

---

## 3. Frozen champion

Champion probability:

`current_prob = canonical predicted_prob`

unmodified.

Eligible evaluation row must have:

- `date`
- `game_pk`
- `player_id`
- `prop_type == "hits"`
- `line`
- finite `predicted_prob`
- settled `outcome in {0,1}`

Candidate identity:

`(date, game_pk, player_id, prop_type, line)`

must be complete and unique.

No arm may change the eligible population.

---

## 4. Frozen challenger mechanism

Use the existing mechanism in:

`backtest/residual_challenger_model.py`

with these values locked:

- training years: 2024 + 2025 only;
- holdout year: 2026 only;
- `MIN_CELL_N = 200`;
- PA states: the existing `PA_STATES`;
- joint key:
  `(batting_order, days_rest_group, getaway_day_group)`;
- joint-cell PA distribution when the training cell has >=200 player-games;
- otherwise fallback to the existing order-only PA distribution;
- `P(hit | PA state)` fitted on training-year hits rows only;
- challenger probability:
  `sum_k P(PA=k | context) * P(hit | PA=k)`,
  normalized exactly as current code does.

No alternate cell threshold.
No smoothing variant.
No parametric model.
No additional opportunity feature.
No hyperparameter sweep.

### Missing challenger state

The existing script currently drops a row when no challenger probability can be
constructed.

That is forbidden in this replication because it changes the universe.

Locked behavior:

`challenger_prob = current_prob`

for any otherwise-eligible row where the joint + order fallback still cannot
produce a challenger probability.

Count and report these exact-fallback rows.

---

## 5. Train / holdout discipline

Training:

`date <= 2025-12-31`

Holdout:

`date >= 2026-01-01`

inside the independently certified canonical artifact.

Every input feature to the opportunity model must be the canonical pregame
signal already carried on the row.

`actual_pa` and `outcome` are training targets/evaluation truth only and may
never enter a holdout ranking decision.

No 2026 row may fit:

- PA distributions;
- hit-given-PA rates;
- grouping thresholds;
- fallback policy.

---

## 6. Per-date equal volume — primary correction

The current code defines:

`MIN_LINE_PROB = 0.60`

as the model's board-eligibility probability floor.

Use that already-existing value as the historical **model-floor volume anchor**.

For each holdout date `D`:

```
date_population_D =
    all eligible hits rows on D

champion_selected_D =
    rows in date_population_D with current_prob >= 0.60

n_D = len(champion_selected_D)
```

Challenger:

- ranks the exact same `date_population_D` by `challenger_prob` descending;
- selects exactly `n_D`;
- if `n_D == 0`, selects zero;
- may not borrow selection volume across dates.

Tie-break:

ascending canonical candidate identity.

Assertions must fail closed if for any date:

- populations differ;
- champion/challenger N differs;
- a selected identity is outside that date's frozen population.

This is **historical model-floor candidate volume**, not exact customer
operational pick volume. Historical prices are absent.

---

## 7. Prediction freeze before outcome reveal

Before holdout outcomes are joined into any selection report, persist and hash:

- certified canonical artifact identity;
- exact code SHA;
- training counts;
- fitted joint PA table;
- fitted order-only PA table;
- fitted hit-given-PA table;
- every holdout candidate identity;
- current probability;
- challenger probability;
- whether joint / order fallback / exact-champion fallback was used;
- per-date champion selected identities;
- per-date challenger selected identities;
- overlap / added / removed identities.

The selection artifact is immutable after hashing.

If any identity, population, or construction assertion fails before the freeze:

**STOP. Do not reveal outcomes and then repair/re-run the same holdout.**

---

## 8. Primary evaluation

After the prediction freeze, join `outcome` by canonical candidate identity.

Report first:

- total selected N;
- champion hits / misses / hit rate;
- challenger hits / misses / hit rate;
- realized-winner difference;
- hit-rate delta;
- overlap N and hit rate;
- added N and hit rate;
- removed N and hit rate;
- added-minus-removed hit-rate difference;
- changed-selection fraction;
- count/fraction using joint cell;
- count/fraction using order fallback;
- count/fraction exact-fallback-to-champion.

Because date-level N is matched, realized-winner difference is a direct
same-volume comparison.

---

## 9. Paired game-cluster uncertainty

Use 5,000 paired bootstrap replicates.

Seed:

`20260829`

Cluster:

`game_pk`

Resample games with replacement and carry every frozen champion/challenger
selection from each sampled game with the sampled multiplicity.

Report:

- 95% percentile CI for challenger-minus-champion hit-rate delta;
- `P(delta <= 0)`;
- 95% percentile CI for added-minus-removed when estimable;
- valid/invalid changed-set replicate count;
- unique game-cluster count.

For a changed-set replicate with no added or no removed rows:

- mark that changed-set statistic unavailable;
- do not coerce an empty rate to zero;
- do not use pseudocounts.

If fewer than 95% of bootstrap replicates yield a valid added-minus-removed
statistic, that statistic is considered too unstable to support continuation.

No row-wise bootstrap.
No unclustered substitute.
No seed redraw.

---

## 10. Stability / diagnostics

Required descriptive splits:

- calendar month;
- batting-order group 1-3 / 4-6 / 7-9;
- joint-cell vs order-fallback vs exact-champion-fallback;
- champion probability bands:
  - [0.00, 0.50)
  - [0.50, 0.60)
  - [0.60, 0.65)
  - [0.65, 0.70)
  - [0.70, 0.80)
  - [0.80, 1.00]

These splits are descriptive only.
They may not rescue a failed primary result or create a new post-hoc variant.

---

## 11. GO / KILL rule

This is intentionally a one-shot close-or-continue decision.

The PA/opportunity challenger **SURVIVES** only if ALL are true:

1. challenger produces more realized winners than champion at the exact matched
   per-date volume;
2. 95% paired game-cluster CI for overall hit-rate delta is strictly above zero;
3. added-minus-removed point estimate is positive;
4. when the changed-set CI is estimable under §9, its lower bound is strictly
   above zero;
5. no population / timing / identity / per-date-volume assertion failed.

Otherwise:

**KILL / CLOSE THE PA-OPPORTUNITY SELECTION THREAD.**

Do not tune:

- MIN_CELL_N;
- probability floor;
- feature groups;
- fallback;
- PA states;
- month subset;
- probability subset

on this same holdout after a failure.

A killed result is useful evidence: stop spending research cycles on this
mechanism and move to the preregistered HR contact-state world-model work.

---

## 12. What a survivor earns

A survivor does NOT earn production promotion.

It earns only:

- confirmation that explicit opportunity modeling contains incremental ranking
  information in the certified historical regime;
- consideration for a separately preregistered world-model integration;
- later prospective shadow verification using real live candidate availability
  and prices.

No historical ROI claim.
No fabricated production eligibility claim.

---

## 13. Execution gate

Do not run until:

1. canonical generation is complete;
2. independent canonical certification passes;
3. this prereg receives methodology review;
4. Jacob explicitly authorizes the replication.

Until then:

- no completed certified 2026 holdout evaluation;
- no challenger result;
- no model change;
- no production change.

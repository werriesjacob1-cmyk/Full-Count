# PRE-REGISTRATION (COMPANION) — HR Contact-State EXECUTION Spec, v2

**Status: PROPOSED. LOCKED ON THIS COMMIT IF ACCEPTED. NOT AUTHORIZED TO RUN.**

This document is a corrected companion to the frozen
`engineering/PREREG_HR_CONTACT_STATE_V1.md` on branch
`accuracy/hr-rare-event-prereg` at frozen HEAD
`8057bcd08b6b13d7662a91370ad791568355ef14`.

It supersedes the unmerged draft `engineering/PREREG_HR_EXECUTION_V1.md`
from PR #74 if and only if this v2 is accepted. The frozen feature prereg is
not modified.

No 2026 holdout has been accessed in preparing this document.
No challenger has been trained.
No result exists.

The purpose of v2 is to remove five degrees of freedom found by independent
methodology review before any holdout read:

1. the v1 free intercept could reward support membership rather than contact-state information;
2. v1 described two different estimator implementations under one label;
3. per-slate selection volume `n_D` was undefined in the price-less historical regime;
4. the player/team/park/month removal stop rule was not operationalized;
5. "read once per arm" still allowed sequential result-aware handling.

Labels: **VERIFIED** = checked against repository/runtime evidence before this
spec · **LOCKED** = fixed here and may not change on this holdout ·
**UNKNOWN** = must be measured during execution and only in the manner stated.

---

## 1. Scientific question and evidence regime

The frozen question remains unchanged:

> Do strictly-pregame bat-tracking / swing-geometry state features improve
> out-of-sample HR ranking and realized HR winners beyond the existing
> champion at the same selection volume?

This is **canonical historical WORLD-MODEL / RANKING evidence only**.

The canonical historical rows do not contain reconstructable historical
sportsbook prices. Therefore:

- this experiment does **not** replay exact historical production eligibility;
- this experiment does **not** estimate historical betting ROI;
- no sportsbook price is fabricated, inferred, or backfilled;
- a positive result can advance only to prospective shadow evaluation, never
  directly to production promotion.

Canonical market identity is **VERIFIED** at pinned generator SHA
`fc589447ec157bff9a96071edc3ceb6c7dc734eb`:

`backtest/engine.py::PROP_TYPE_BY_STAT["home_runs"] == "home_run"`.

The experiment population therefore uses `prop_type == "home_run"`.

---

## 2. Arms — inherited unchanged from frozen prereg

- **A** — champion `predicted_prob`, unmodified.
- **B** — A + bat-speed state:
  - trailing mean `bat_speed`
  - trailing 90th percentile `bat_speed`
- **C** — A + swing-geometry state:
  - trailing mean `attack_angle`
  - trailing mean `swing_length`
  - trailing mean `swing_path_tilt`
  - trailing mean `attack_direction`
- **D** — B + C.
- **E** — D + trailing `hit_distance_sc`, only if D earns continuation under
  the frozen stop rule.

`arm_angle` remains excluded.

No feature may be added, removed, transformed into an interaction, binned,
winsorized, or substituted after this lock.

---

## 3. Point-in-time feature construction

### 3.0 Source artifact and retained columns — LOCKED

No new Statcast pull is permitted for this experiment.

At the pinned canonical scientific SHA
`fc589447ec157bff9a96071edc3ceb6c7dc734eb`,
`backtest/engine.py::STATCAST_COLUMNS` explicitly retained, before the
2026-08-28 canonical source pull:

- `bat_speed`
- `swing_length`
- `attack_angle`
- `swing_path_tilt`
- `attack_direction`
- `hit_distance_sc`

as retention-only columns. The source comment is explicit that retaining them
did **not** authorize using them in the champion. That makes them available for
this challenger from the already-bound immutable source parquet without a new
source vintage.

The feature extractor must therefore be a pure read of the certified source
artifact. It may not call pybaseball, Baseball Savant, or any other network
endpoint while constructing B/C/D/E features.

For Arm E, the previously underspecified "trailing `hit_distance_sc` state"
is locked here as the **arithmetic mean** of non-null `hit_distance_sc`
values inside the same fixed last-100 tracked-swing window used by B/C/D.
This closes the statistic before holdout access; no max/p90/recent-subwindow
variant may be substituted later.

For a candidate on date `D`, every contact-state feature uses only Statcast
swings with:

`game_date < D`

for that batter.

Never `<= D`.

Window and support contract, inherited and made executable:

1. Define a **tracked swing** as a pre-`D` Statcast row with non-null
   `bat_speed`. This uses the exact tracking marker the frozen parent prereg
   already verified in the real pybaseball frame; it does not infer swings
   from postgame outcomes.
2. Sort those tracked swings chronologically and take the most recent **100**
   for that batter. This one fixed 100-row window is shared by every arm and
   every feature for that candidate.
3. For each required feature, compute its statistic from the non-null values
   for that feature **inside those same 100 tracked-swing rows**.
4. A feature is available only when it has at least **30 non-null
   observations** inside the fixed window. Fewer than 30 -> that feature is
   `None`.
5. Arm support is then exact:
   - B: `bat_speed` count >=30 (mean + p90 come from the same values);
   - C: each of `attack_angle`, `swing_length`, `swing_path_tilt`,
     `attack_direction` individually has count >=30;
   - D: B and C are both supported;
   - E: D is supported and `hit_distance_sc` has count >=30.
6. Unsupported row -> `p_challenger = p_champion` exactly.

No feature-specific lookback horizon beyond the fixed last-100 tracked-swing
window.
No partial-arm model.
No mean/zero imputation.
No same-day or future pitch may enter a feature.

Coverage reporting must include the per-feature non-null-count distribution and
the exact supported-row count for B/C/D/E before any effect size is revealed.

Training uses SUPPORTED rows for that arm.
Evaluation uses the entire eligible population, with unsupported rows falling
back exactly to champion.

Coverage is reported before any effect size.

---

## 4. Challenger model — one exact estimator

For every SUPPORTED row `i`:

```
o_i = logit(clip(p_champion_i, 1e-6, 1 - 1e-6))
eta_i = o_i + x_i^T beta
p_challenger_i = sigmoid(eta_i)
```

### 4.1 No free intercept — LOCKED

There is **NO fitted intercept**.

This is deliberate.

Unsupported rows remain exactly champion. A free intercept on supported rows
would move every supported hitter relative to unsupported hitters even when all
physical-state coefficients are zero, allowing "has enough bat-tracking data"
to become a hidden ranking signal.

The null must be exact:

`beta = 0 -> p_challenger = p_champion`

for every supported row, and unsupported rows are champion by definition.

There is no support-indicator/control arm in the frozen ablation ladder, so none
is introduced here.

### 4.2 Training standardization — LOCKED

For each arm separately:

- compute feature means on that arm's SUPPORTED training rows only;
- compute population standard deviation with `ddof=0` on those same rows;
- standardize using those training-only means/SDs;
- apply the frozen transform unchanged to the holdout;
- persist each mean and SD in the prediction-freeze artifact.

If any required training feature has zero or non-finite SD, that arm aborts.

### 4.3 Penalized objective — LOCKED

Fit `beta` by minimizing exactly:

```
J(beta) =
    sum_i [ -y_i log(p_i) - (1-y_i) log(1-p_i) ]
    + 0.5 * sum_j beta_j^2
```

where:

`p_i = sigmoid(o_i + x_i^T beta)`.

There is no intercept term and no other penalty.

Equivalent gradient used by the optimizer:

```
grad J(beta) = X^T (p - y) + beta
```

This is a fixed ridge coefficient of **1.0 on the summed-loss scale**.
No `C` parameter is used and no library-default regularization semantics are
borrowed.

No regularization sweep, cross-validation, alternate lambda, or family search
is permitted on this holdout.

### 4.4 Optimizer — LOCKED

Use:

`scipy.optimize.minimize(method="L-BFGS-B")`

with:

- `x0 = zeros(n_features)`
- analytic gradient above
- `maxiter = 1000`
- `ftol = 1e-12`
- `gtol = 1e-8`
- `maxls = 50`

No scikit-learn `LogisticRegression` path is used.
There is no runtime-dependent estimator fallback.

Abort the arm before holdout outcome reveal if:

- optimizer `success` is false;
- any coefficient is non-finite;
- any frozen transform parameter is non-finite;
- final objective is non-finite;
- max absolute analytic gradient exceeds `1e-5`.

Do not repair and retry with different optimizer settings on the same holdout.

---

## 5. Target and chronological split

Target:

- canonical row `outcome`
- binary 0/1
- `prop_type == "home_run"`
- no re-derivation.

Training:

`date <= 2025-12-31`

Holdout:

`date >= 2026-01-01`

within the independently certified canonical artifact.

No holdout row may be used for:

- fitting;
- standardization;
- penalty choice;
- feature design;
- volume choice;
- band definition;
- optimizer choice;
- bug-driven reruns after outcome reveal.

---

## 6. Eligible population

For each date, eligible historical ranking rows are the certified canonical
`home_run` rows carrying all of:

- `date`
- `game_pk`
- `player_id`
- `team`
- `line`
- `predicted_prob`
- `score`
- settled `outcome`

No arm changes the population.

Candidate identity:

`(date, game_pk, player_id, prop_type, line)`

must be complete and unique.

A duplicate or incomplete identity aborts the run.

---

## 7. Equal volume — fixed historical ranking capacity

Exact historical production Top Pick volume is **not reconstructable** because
canonical history carries no historical sportsbook prices.

Therefore this companion does **not** pretend to recover production `n_D`.

Instead it locks one explicit model-ranking capacity before holdout access:

```
K_PRIMARY = 5
n_D = min(5, number_of_eligible_home_run_rows_on_date_D)
```

On each date `D`:

- champion ranks the complete date population by `predicted_prob` descending;
- challenger ranks the same rows by `p_challenger` descending;
- both take exactly `n_D`;
- ties use ascending canonical candidate identity;
- volume may not move between dates;
- a date with zero eligible rows contributes zero to every arm.

Why five:

- this is not a newly invented experiment capacity. The pinned scientific
  generator already defines the dedicated HR product surface as
  `select_moonshots(candidates, prices, fd, n=5)` and documents it as the
  **top five home-run bets by hit probability**;
- therefore `K_PRIMARY = 5` preserves a pre-existing HR-specific shortlist
  capacity instead of choosing a convenient K after seeing holdout results;
- the historical canonical regime still cannot replay exact Moonshots
  eligibility/pricing because sportsbook prices and full historical live
  eligibility are unavailable;
- the immutable public Top Pick ledger cannot supply an HR-specific alternative
  volume either. Pinned evidence: `data/public_top_picks/registry.json` blob
  `e99a7de887c3becf08b599b2a0af23b48fe6caa9`, registry
  `updated_at=2026-08-29T14:12:28.963169+00:00`, contains 63 published Top
  Picks across six dates and 0 published `home_run` entries;
- importing all-market Top Pick counts as though they were an HR policy would
  fabricate a historical selector.

This is labeled **historical top-5-per-slate HR ranking capacity**, not exact
historical production eligibility. The number five is operationally anchored
by the pre-existing Moonshots surface, while the eligibility regime remains
canonical model-only evidence.

Promotion still requires prospective shadow evidence at the real operational
volume.

No alternate K is used to decide GO/KILL on this holdout.

---

## 8. Ranking and selection freeze

Champion ordering key:

`predicted_prob` descending.

Challenger ordering key:

`p_challenger` descending.

Neither ranker may read:

- outcome;
- actual;
- final game result;
- postgame field;
- sportsbook price;
- future contact-state data.

Before any holdout outcome is joined, persist and hash for every arm/date:

- complete eligible candidate identities;
- champion probability;
- challenger probability;
- support flag;
- all standardized feature values;
- selected champion identities;
- selected challenger identities;
- overlap / added / removed identities;
- training transform parameters;
- fitted `beta`;
- optimizer diagnostics;
- exact code SHA;
- certified canonical artifact identity.

This is the **prediction-freeze artifact**.

No selection or model value may change after this artifact is hashed.

---

## 9. Holdout access — one immutable evaluation pass

The 2026 holdout is handled in two stages inside one locked run.

### Stage 1 — prediction/selection freeze

After all code/specs are frozen:

1. open the certified holdout once;
2. immediately record its artifact identity/checksum;
3. pass rows to prediction/ranking code with outcome/postgame fields masked;
4. construct point-in-time features strictly from pitches before each date;
5. build the locked venue map described in §11;
6. fit nothing on 2026;
7. create and hash the prediction-freeze artifact.

If any Stage-1 kill condition occurs:

**STOP. No outcome reveal. No repair-and-rerun on this holdout.**

### Stage 2 — outcome reveal

Only after Stage 1 is complete and hashed:

- join the canonical `outcome` field by candidate identity;
- compute every preregistered B/C/D result from the same frozen selections;
- compute E only if the frozen continuation rule permits it;
- write one immutable evaluation report.

No code, parameter, feature, volume, grouping, or band edge may change between
Stage 1 and Stage 2.

---

## 10. Primary result and selection anatomy

For each arm vs champion report first:

- total selected N;
- champion hits / misses / hit rate;
- challenger hits / misses / hit rate;
- hit-rate delta;
- additional realized winners;
- overlap N and hit rate;
- added N and hit rate;
- removed N and hit rate;
- added-minus-removed hit-rate difference.

The North Star quantity for this historical ranking test remains realized
winners at exact matched per-slate volume.

Calibration/Brier/log loss may be reported only as secondary diagnostics.

---

## 11. Park provenance — locked before outcome reveal

Canonical rows carry `game_pk` but no venue field.

Join:

`game_pk -> venue.id, venue.name`

from MLB StatsAPI schedule metadata.

Rules:

- group robustness by integer `venue.id`, never venue name;
- venue is treated only as scheduled game identity, not an outcome-derived field;
- no linescore, result, weather-at-play, or postgame field is read to build the map;
- every holdout `game_pk` must resolve before Stage-1 prediction freeze;
- unresolved game -> Stage-1 abort;
- materialize the mapping once with row count + SHA256 and include that identity in
  the prediction-freeze artifact.

The map may be constructed from the 2026 game identities only inside the locked
Stage-1 pass after this spec is frozen. Its construction may not be changed after
any outcome is revealed.

---

## 12. Uncertainty — exact paired game-cluster bootstrap

Primary bootstrap population is the union of champion and challenger selected
rows.

Cluster key:

`game_pk`

For each of 5,000 bootstrap replicates:

1. draw `G` game IDs with replacement from the `G` unique selected game IDs;
2. each drawn game contributes all champion and challenger selected rows from
   that game, preserving multiplicity;
3. compute champion hit rate, challenger hit rate, and their difference;
4. apply the same sampled-game multiplicities to the added and removed sets.

For the changed-set statistic:

- if a bootstrap replicate contains at least one added AND one removed pick,
  compute added-minus-removed hit-rate difference normally;
- if either side is empty in that replicate, record that replicate as
  `changed_stat_unavailable` — never coerce an empty rate to zero and never
  add a pseudocount;
- the 95% added-minus-removed CI is computed only from valid changed-set
  replicates;
- at least **4,750 of 5,000** replicates must be valid. Fewer means changed-set
  uncertainty is not estimable robustly enough and the arm does not earn
  continuation.

Seed:

`20260828`

Report:

- 95% percentile CI for overall hit-rate delta using all 5,000 replicates;
- 95% percentile CI for added-minus-removed using the valid changed-set
  replicates;
- valid/invalid changed-set replicate counts;
- `P(overall_delta <= 0)`;
- `P(added_minus_removed <= 0)` over valid changed-set replicates;
- number of unique game clusters.

No row-wise bootstrap.
No seed redraw.
No unclustered substitute.

If the observed added or removed set is empty, or the 4,750-valid-replicate
minimum is not met, the arm does not earn continuation.

---

## 13. Exact robustness removal rule

The frozen parent closes the thread if a positive effect depends materially on
one player, team, park, or month.

Operational definition for each grouping axis:

- player -> `player_id`
- team -> canonical row `team`
- park -> joined integer `venue.id`
- month -> UTC/MLB schedule date formatted `YYYY-MM`

Let:

`Delta_full = added_hit_rate - removed_hit_rate`.

For every group value `g` on one axis:

1. remove from BOTH the added and removed sets every changed selection belonging
   to `g`;
2. recompute `Delta_without_g`;
3. define contribution impact:
   `impact_g = Delta_full - Delta_without_g`.

The **largest contributor** is the group with the greatest positive
`impact_g`.
Ties break by lexical representation of the group key.
If every `impact_g <= 0`, that axis has no positive single-group contributor
and therefore cannot trigger the sign-flip dependency rule.

Fail closed for that axis if removal of the chosen largest positive contributor
leaves either added or removed empty.

Frozen dependency stop condition is triggered if, for ANY axis:

`Delta_full > 0`, a positive contributor exists, and
`Delta_without_largest <= 0`.

No alternate definition of "largest contributor" is allowed after holdout
access.

---

## 14. Stability reporting

Required descriptive stability outputs:

### Month
One row per `YYYY-MM`, with n and realized comparison.

### Champion-probability band
Bands are fixed now:

- `[0.00, 0.05)`
- `[0.05, 0.10)`
- `[0.10, 0.15)`
- `[0.15, 0.20)`
- `[0.20, 0.30)`
- `[0.30, 1.00]`

Band assignment uses champion `predicted_prob`, never challenger probability.

These band tables are **descriptive only**.
They may not create a new GO/KILL rule, rescue a failed primary result, or
justify changing a model after the fact.

---

## 15. Frozen stop / kill conditions

The parent stop rule remains authoritative.

The HR contact-state thread closes if:

- added-minus-removed 95% CI includes zero;
- removing the largest contributor on player/team/park/month flips the positive
  effect to non-positive under §13;
- fewer than 500 holdout HR candidates carry a real trailing-state feature.

Execution-side aborts before result interpretation:

- any same-day/future pitch enters a feature;
- any `<= D` feature cutoff where strict `< D` is required;
- any incomplete/duplicate candidate identity;
- any per-date volume mismatch;
- any arm shrinks the eligible population;
- any holdout row is used in fitting/standardization;
- any venue mapping is unresolved;
- optimizer/transform failure under §4;
- any selection changes after prediction-freeze hash;
- any non-preregistered K, seed, grouping rule, band edge, feature, optimizer,
  penalty, or fallback is introduced.

On any execution abort:

**STOP and report. Do not repair and rerun the same holdout after outcome
visibility.**

---

## 16. What success means — and what it does not

A surviving historical result means:

- physical contact-state information appears to improve HR ranking beyond the
  champion on one untouched season;
- at fixed five-per-slate historical ranking volume, it produced more realized
  HR winners;
- the changed-pick effect survived game clustering and the frozen dependency
  removals.

It does **not** mean:

- exact historical production eligibility improved;
- historical ROI improved;
- the challenger should be deployed;
- model promotion is authorized.

A survivor advances to **prospective full-candidate shadow** with real live
market/availability state and the real operational selection volume.

---

## 17. Authority and execution gate

Execution is forbidden until ALL are true:

1. canonical run completes;
2. independent canonical certification passes;
3. this v2 receives independent methodology review;
4. SuperClaude configuration/runtime state is resolved;
5. Jacob explicitly authorizes the experiment.

Until then:

- no 2026 holdout access;
- no challenger training;
- no venue map against 2026;
- no experiment runner execution;
- no model/calibration/scoring/selector/recommendation change;
- no production promotion.


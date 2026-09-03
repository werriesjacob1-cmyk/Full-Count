# PRE-REGISTRATION (COMPANION) — HR Contact-State **EXECUTION** Spec, v1

**Status: NOT LOCKED. NOT AUTHORIZED TO RUN. REVISION REQUIRED.**

> An independent methodology red team reviewed this draft on 2026-09-01 and
> returned **PREREG NEEDS REVISION BEFORE LOCK**, with 15 findings and 10
> must-fix items. Its full report is
> `engineering/PREREG_HR_EXECUTION_V1_REDTEAM.md`.
>
> **Four of its findings are factual errors in this document, corrected inline
> below and marked `[RED TEAM CORRECTION]`.** One of them — §7's `VERIFIED`
> label — was a mislabeled evidence claim, which under this project's own
> labelling discipline is the most serious kind of error here.
>
> The remaining findings change the experiment's design (control arms, an
> underpowered-null verdict, naming a primary arm). Those are scientific
> decisions for Jacob, recorded as recommendations, **not applied
> unilaterally.** This document must not be treated as locked until they are
> resolved.

Companion to the frozen `engineering/PREREG_HR_CONTACT_STATE_V1.md` (branch
`accuracy/hr-rare-event-prereg`, frozen HEAD
`8057bcd08b6b13d7662a91370ad791568355ef14`). **That document is not modified by
this one** and remains authoritative for the question, the ablation ladder, the
feature definitions, the evaluation contract and the stop rule.

Labels: `VERIFIED` measured here · `INFERRED` · `HYPOTHESIS` · `UNKNOWN`.

---

## 0. Why this companion exists

The frozen prereg locks **which features** may enter each arm. It does not lock
**how a number is produced from them**. Between "these four trailing means are
the arm" and "here is a challenger probability" sit a large number of unstated
researcher degrees of freedom:

learner family · target · objective · preprocessing · standardization ·
regularization strength · hyperparameter search · missing-feature fallback ·
solver · seed · how the challenger combines with the champion · how unsupported
candidates are treated · the exact training cutoff.

Each is a knob that can be turned after seeing a result. Locking the feature
list while leaving the estimator free is **not a preregistration** — it is a
preregistration of the least flexible part.

This document closes that gap **before any holdout access**. Nothing here
authorises execution.

## 1. Learner — exactly one, no family search

The challenger is a **champion-anchored logistic offset**:

```
logit(p_challenger) = logit(p_champion) + Xstd · beta
```

- `p_champion` is the canonical row's existing `predicted_prob` for
  `home_run`, unmodified, clipped to `[1e-6, 1-1e-6]` before the logit purely
  for numerical safety.
- `Xstd` is the arm's feature vector after the standardization in §3.
- `beta` is fitted by **L2-regularized logistic regression on the offset** —
  the champion logit supplied as a fixed, unfitted, unpenalized offset.

**Why anchored rather than free-standing.** A free-standing learner would
re-learn PA volume, lineup slot and season HR rate from scratch, then be scored
against a champion that already knows them. The frozen prereg's §2 asks
specifically about improvement *"beyond the existing champion."* Only an offset
model answers that question. It also makes the null exactly `beta = 0` — a
point in the parameter space, not an artifact of tuning.

**[RED TEAM CORRECTION — F6] The `UNKNOWN` is now resolved, and the answer
invalidates this section as written.** scikit-learn **1.9.0** cannot express
this model: `LogisticRegression.__init__` exposes no offset/exposure parameter
and `.fit` accepts only `X, y, sample_weight`. **The hand-written L-BFGS
"fallback" is therefore the primary and only estimator** — and its objective is
not specified here, which is fatal to the claim that `C = 1.0` is locked:

- sklearn minimizes `C * sum_i loss_i + 0.5*||w||^2` — loss **summed**, `C`
  multiplying the **loss**.
- The natural hand-rolled form is `mean_i loss_i + (1/(2C))*||w||^2`.

These differ in effective shrinkage by a factor of **n** — tens of thousands of
training rows. Two runs, both truthfully described as "L2, C=1.0, the same
penalized objective," would produce betas differing by orders of magnitude.
**The escape hatch is not a hyperparameter sweep; it is the normalization
convention inside the fallback.** This section is unusable until the objective
is written in closed form with its exact normalization, plus the L-BFGS
convergence criterion and a recorded final gradient norm.

Noted by the red team: §0 argues that locking features while leaving the
estimator free "is not a preregistration." This clause reproduced that exact
failure one level down — it locked the estimator's *name* while leaving its
*objective* free.

Explicitly NOT permitted: gradient boosting, random forests, neural networks,
calibration-on-top, ensembling, cross-family model selection, or "we also
tried X."

## 2. Target and objective

- **Target:** the canonical row's `outcome` for `prop_type == "home_run"`, as
  binary 0/1 — the settled result already in the certified artifact. No
  re-derivation, no alternative definition.
- **Objective:** binary cross-entropy plus L2 on `beta` only. The offset is
  never penalized and never fitted.
- **Class weighting: none.** HR is rare; reweighting changes the implied base
  rate, which is the champion's job, not the challenger's.

## 3. Preprocessing and standardization

- Features are exactly the trailing statistics defined in §4 of the frozen
  prereg. No new feature is introduced here.
- **Standardization:** per-feature mean/SD fitted on **training rows only**
  (dates through 2025), applied unchanged to the holdout. The fitted means and
  SDs are written to the run artifact so the transform is auditable.
- No target encoding, binning, interactions, or polynomial terms.
- No winsorization or outlier removal. A real extreme swing is signal, and a
  trimming rule is a knob.

## 4. Regularization and hyperparameters — fixed, not searched

| | |
|---|---|
| penalty | **L2** |
| inverse strength | **`C = 1.0`**, fixed |
| solver | **`lbfgs`**, `max_iter=1000`, `tol=1e-6` |
| intercept | **[RED TEAM CORRECTION — F2] This row was wrong and is withdrawn.** See below. |

**[RED TEAM CORRECTION — F2] The intercept justification was inverted.** This
table previously said the intercept "absorbs constant champion miscalibration
on this population, and is not itself a finding." That would hold only if the
transform were applied to **every** row: adding a constant to every logit is
strictly monotone, leaves ranking identical, and cannot change any selected set
at any volume.

But §5 applies the transform to **SUPPORTED rows only** — unsupported rows take
`p_champion` exactly, with no intercept. So the intercept is not a global
constant; it is a **differential shift between two subpopulations.** A positive
fitted intercept promotes every supported row above unsupported rows of equal
champion probability, and SUPPORTED means ">=30 tracked swings" — a regular
starter, not a bench bat or a call-up.

**Consequence: the challenger can beat the champion with `beta = 0`.** The
entire result could be "the champion under-prices regular starters" — a
coverage artifact containing zero bat-tracking information — while this
document pre-emptively disclaimed the intercept as "not itself a finding."

Red team's proposed correction, **not applied unilaterally**: set
`fit_intercept=False`, and add a mandatory pre-registered control arm
`A_shift` = champion + intercept only, no features, fitted and evaluated
identically. If `A_shift` alone moves added-minus-removed, the experiment is
measuring coverage, not swing state. Both REMOVE degrees of freedom.

**No hyperparameter sweep is preregistered, therefore none may be run.** If
`C = 1.0` is later argued to be wrong, that is a NEW preregistration with a new
commit, on a population that is not this holdout.

Why the library default rather than a tuned value: with a handful of
standardized features under a strong offset, the fit is not
regularization-sensitive enough for one default to distort it, and any tuning
procedure needs its own inner split — a second set of degrees of freedom to
lock. Declaring the default is more honest than tuning and reporting the winner.

## 5. Missing features — fallback is exactly the champion

The frozen prereg sets a 30-swing minimum and says the row "falls back to the
champion arm for that feature." Made operational:

- A row is **SUPPORTED** for an arm only if **every** feature in that arm is
  present (>= 30 tracked swings strictly before `D`).
- A row that is not SUPPORTED yields `p_challenger := p_champion` **exactly**.
  Not a mean, not zero, not a partial fit on the available subset.
- **Partial-feature fitting is forbidden.** It would make the effective model
  differ row to row, turning one arm into several.
- **Training uses SUPPORTED rows only.** Unsupported rows carry no information
  about `beta`; including them as champion-equals-challenger would bias `beta`
  toward zero by construction.
- **Evaluation uses the FULL eligible population**, unsupported rows scored at
  the champion value. Any other choice lets the challenger quietly shrink the
  population, which the frozen §6.5 forbids.
- The SUPPORTED fraction is reported **before** any effect size, per the frozen
  coverage caveat.

## 6. Training cutoff and seeds

- **Training:** canonical `home_run` rows with `date <= 2025-12-31`.
- **Holdout:** `date >= 2026-01-01`, read **once per arm**.
- No validation split is taken from the holdout for any purpose.
- **Seeds:** the estimator is deterministic (L-BFGS on a convex objective), so
  no seed affects `beta`. The one stochastic component is the paired bootstrap,
  fixed at seed **20260828** and recorded in the artifact. Bootstrap seeds are
  never re-drawn to change an interval.

## 7. Equal volume is **per slate**, not global top-N

This is the correction the aggregate contract most needs.

A global top-N comparison lets a challenger win by **moving its selections onto
easier days** — concentrating on high-HR-environment slates and abstaining on
hard ones, while the champion is forced to spread. That is a scheduling
advantage, not a world-model improvement, and at the same headline N it is
invisible.

**Contract:** for every date `D`, let `n_D` be the number of `home_run`
candidates the CHAMPION selects on `D` under the locked selection policy. Each
challenger arm selects **exactly `n_D`** on `D`, ranked by its own
`p_challenger`, from the same eligible population on `D`.

- Totals match by construction, and so does the per-day distribution.
- A date where the champion selects zero is a date where every arm selects zero.
- No arm may borrow volume across dates.
**[RED TEAM CORRECTION — F4] The `VERIFIED` label here was mislabeled evidence
and is withdrawn.** What was verified is that `backtest/equal_volume.py`
**exists**. What was implied is that it **implements** per-slate volume. It does
not. `EqualVolumeExperiment` takes a single global integer `volume` and does
`selected = first[:self.volume]` over the whole population, requiring
`SelectionPolicy.rank()` to return a **global** total order. There is no date
parameter, no per-slate slicing, and no per-date assertion anywhere in the file.
**The module implements exactly the global top-N design this section opens by
declaring wrong.** The per-slate wrapper is new code that does not exist yet.

Three further code/prereg mismatches the red team found:
- `_clustered_bootstrap` defaults to `iterations=2000, seed=20260827` — §8
  requires 5,000 and seed **20260828**. The default seed is off by one day from
  the required one, and §10 kills on a wrong seed but is silent on iteration
  count.
- Its CI is on the **overall hit-rate delta**, not on **added-minus-removed**,
  which is the primary quantity. No implementation of the primary quantity's CI
  exists anywhere.
- Draws where either side ends empty are silently `continue`d and excluded from
  the denominator.

**[RED TEAM CORRECTION — F5] "the locked selection policy" does not exist.**
Neither this document nor the frozen parent defines it, and there is no
`SelectionPolicy(...)` construction in the repository. **`n_D` — the quantity
this entire equal-volume contract rests on — is undefined and would be chosen
after reading this prereg.** Worse: the production selector
(`rank_for_board` → reliability tier, then `market_edge`, then
`hit_probability`; `select_main_board` keeps only `price_clears is True`) cannot
be used, because the frozen §5 verifies **no prices exist for this
population** — so the real board would select zero rows. `n_D` is therefore
necessarily a synthetic research construct nobody has specified.

Any deviation must be preregistered explicitly as an alternative and justified;
it may not be adopted after seeing results.

## 8. Uncertainty

Restated from the frozen prereg so the runner cannot drift:

- **Paired** bootstrap on the SAME candidate rows, arm vs champion.
- **Clustered on `game_pk`** — two HR candidates in one game are not
  independent; ignoring this previously widened an interval ~35% and flipped a
  conclusion.
- 5,000 resamples, 95% CI, seed 20260828.
- Primary quantity: **added-minus-removed** hit-rate difference at matched
  per-slate volume, with n for added, removed and overlap.

## 9. Park provenance — the join is specified NOW

`VERIFIED` by direct inspection of the durable canonical artifact
(`canonical/canonical-20260828T153143Z-2b79304f/rows/*.jsonl.gz`), a row carries:

```
actual, actual_pa, backtest_generated_at, cat_baseline_skill, cat_context,
cat_environment, cat_matchup, cat_recent_form, code_git_sha, date, fair_test,
game_pk, line, needs, outcome, player_id, player_name, predicted_prob,
prop_type, sb_cat_context, sb_cat_matchup, sb_cat_skill, score, signals, team
```

**There is no venue/park field.** The frozen stop rule requires a
park-robustness check, so without a join that rule is unevaluable — and
building the join *after* seeing results would let its construction be
influenced by them.

**Locked join plan:**

1. **Key:** `game_pk` → `venue.id` (integer) and `venue.name` (string).
2. **Source:** the MLB StatsAPI schedule endpoint, which `mlb_daily.py:283`
   already hydrates with `venue` and reads at `mlb_daily.py:304`.
3. **Provenance safety:** venue is a **scheduled, pregame property of the
   game**, fixed before first pitch and not outcome-conditioned. The join reads
   no result, linescore, weather-at-play, or any field resolving after first
   pitch. No lookahead.
4. **Identity:** group on **`venue.id`, never `venue.name`.** Names change while
   the park does not — `mlb_daily.py:166` records Minute Maid Park becoming
   Daikin Park, `:168` the A's at Sutter Health Park. Grouping on names would
   split one park in two and could manufacture or mask a robustness failure.
5. **Coverage:** every distinct `game_pk` in the evaluation population must
   resolve. Unresolved ones are reported as an explicit count, never silently
   dropped; if any are unresolved the park check is reported **incomplete**,
   not passed.
6. **Materialization:** built once, frozen to a checked-in artifact with a row
   count and SHA256, reused. Not rebuilt per analysis run.
7. **Timing:** may be built for the TRAINING range at any time. **Must not be
   built against 2026 game_pks in this mission** — see §11.

## 10. Kill / stop conditions

The frozen §7 stop rule applies unchanged and is not weakened. This companion
adds **execution-side** kill conditions, all of which abort the run rather than
adjust it:

- per-date equal-volume assertion fails for any date;
- any feature is computed with `<=` rather than a strict `<` date comparison;
- the SUPPORTED population differs between fitting and evaluation in any way
  not described by §5;
- any 2026 row is touched during fitting or standardization;
- the venue map has unresolved `game_pk`s in the evaluation population;
- the installed scikit-learn cannot express the offset model and the §1 fallback
  was not recorded in advance;
- the bootstrap runs with a seed other than 20260828, or unclustered.

On any kill condition: **stop, record, report.** Do not repair and continue in
the same run.

## 11. What has NOT happened

- **NO 2026 HOLDOUT DATA HAS BEEN ACCESSED.**
- No challenger has been trained.
- No champion-vs-challenger result exists.
- No venue map has been built against 2026.
- No model, calibration, threshold, selector or settlement code has changed.
- The frozen prereg branch is untouched at
  `8057bcd08b6b13d7662a91370ad791568355ef14`.

Execution remains gated on: canonical run completion, independent canonical
certification, SuperClaude activation, a fresh-session methodology review, and
Jacob's explicit authorization.

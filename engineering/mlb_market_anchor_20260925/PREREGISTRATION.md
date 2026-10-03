# Pre-registration: market-anchored probability vs market-only vs model (MLB)

Mission 12 Workstream D follow-up. This file is committed **alone**, before
any evaluation data exist: the first evaluation slate is 2026-09-25, and its
frozen board has not been produced when this file is committed. Commit
order is the only guarantee of pre-registration. This is research only. No
model, selector, threshold, workflow, customer surface or public-pick policy
changes. Nothing here authorizes promotion.

## What is already known (disclosed before locking)

- From PR #201 (`claude/mlb-selection-overconfidence-20260924`), including its
  independent review:
  - Published Top Picks were overconfident by -11.0pp. The shortfall is
    largest in pitcher markets at large model-minus-market disagreement.
  - Market prices were closer to outcomes than the model in pooled data.
  - `market_implied` is de-vigged for strikeouts and pitcher_outs, and raw
    for batter markets.
  - The frozen boards (`output/board_freeze_{date}.json`) are last-run-wins.
- Outcomes for all slates up to 2026-09-23 have been examined in aggregate.
  They are the **fit** data here, never evaluation data.
- The 2026 regular season ends 2026-09-27 (MLB Stats API). Only three
  regular-season slates remain: 09-25 (16 games), 09-26 (15) and 09-27 (14).

## Question

**Primary question.** Does the model's probability add information beyond
the posted price?

- A per-family blend `p2 = logistic(a_f + b_f*logit(p_model) + c_f*logit(q))`
  is tested against:
  - a market-only arm, `p1 = logistic(a_f' + c_f'*logit(q))`;
  - the champion, `p0 = p_model`.
- The comparison is proper-score prediction of frozen-board candidates.

**Secondary question.** Would the blend have selected more winners at equal
volume?

**Reading a null result.** A null on the primary question is a legitimate
result: "no demonstrated edge beyond the market". It is not a failed
method.

## Definitions (locked)

- **q.** The raw posted implied probability, computed from the candidate's
  own American `market_odds`:
  - `-o/(-o+100)` for negative odds;
  - `100/(o+100)` for positive odds.

  It is never taken from `market_implied`, which mixes de-vigged and raw
  prices. No de-vig is imputed; the intercept absorbs the hold.
- **Families f.** `hits`, `hits_runs_rbis`, `strikeouts`, `pitcher_outs`, and
  `other`. `other` pools every remaining market, and its coefficients are
  fitted once, pooled across all of those markets.
- **y.** 1 for `hit`, 0 for `miss`. Every other grade (void, push, ungraded)
  is excluded.
- **Fit sample.** Rows dated on or before 2026-09-23 that have `market_odds`
  and `hit_probability` and a hit or miss grade, drawn from:
  - frozen-board records (`output/board_freeze_graded_{date}.json` records
    whose `source_board_sha256` matches the board);
  - the final-run board (`results/grades_{date}.json` `picks` with a
    recommendation status).

  Rows are deduplicated by canonical id, with the frozen-board record
  preferred. Data are read via `git show` at the commit recorded in the fit
  commit.
- **Fitting.** Maximum likelihood logistic regression by Newton-Raphson,
  with an L2 penalty of 1.0 on `b_f` and `c_f` (not on the intercepts) for
  numerical stability. Probabilities are clipped to [1e-4, 1 - 1e-4] before
  the logit.
  - The coefficients, their fit-sample game-clustered bootstrap CIs (B=2000,
    seed 20260925) and the code are committed in one **fit commit**.
  - The fit commit lands before the analysis commit, and it touches no
    evaluation outcome.
  - After that commit the coefficients are frozen.
- **Evaluation population (FB_E).** Frozen-board records for slates
  2026-09-25, 2026-09-26 and 2026-09-27 with:
  - `eligibility.qc_status == "kept"`, a confirmed lineup (not
    `lineup_assumed`), non-null `market_odds` and `hit_probability`;
  - a graded file whose `source_board_sha256` matches the board;
  - a hit or miss grade.

  Postseason slates are a **separately labelled regime**: reported
  descriptively only, never pooled into the primary estimate.
- **Champion volume N_d.** On each slate, N_d is the number of FB_E records
  with `selector.recommendation_status == "top_pick"`.

## Endpoints (locked)

1. **Primary.**
   - The paired per-candidate log-loss difference `LL(p2) - LL(p1)`, pooled
     over FB_E.
   - 95% CI from a game-clustered bootstrap: cluster (slate, game_pk),
     B=2000, seed 20260925, percentile.
   - **Success** requires both:
     - the CI upper bound is below 0;
     - at least one family has a fit-sample CI for `b_f` that is entirely
       above 0.
2. **Secondary A.** The same paired statistic for `LL(p2) - LL(p0)`: does the
   blend beat the champion model?
3. **Secondary B (equal volume).**
   - On each slate, the challenger takes the top N_d FB_E records by
     `p2 - q`, among those with `p2 >= 0.60`.
   - If fewer than N_d qualify, the challenger takes fewer. There is no
     backfill.
   - Report, for champion vs challenger: the counts, the overlap, the
     added-vs-removed outcomes, the realized hit rate, stated-minus-realized,
     and the 1-unit ROI at the posted `market_odds`.
   - No significance claim is made below 60 settled picks per arm.
4. **Descriptive.**
   - Per-family log loss and Brier score for p0, p1 and p2.
   - Reliability by band.

CLV is **not** available: no closing line is captured for frozen records. It
is not reported, and not imputed.

## Minimums and analysis timing (locked)

- **Single analysis date:** 2026-09-29. That is after the 09-27 slate is
  final and graded. There is no optional stopping and no earlier look at
  outcomes.
- **Minimum for a primary verdict:** at least 1,000 settled FB_E records and
  at least 150 records with `p0 >= 0.60`. Below that, the verdict is
  `insufficient n`; the estimates are reported, but no success or failure is
  declared.
- **Any continuation into 2027** needs a new pre-registration: the model
  version and the market may change. These coefficients are not carried
  forward silently.

## Known limitations (stated in advance)

- **The frozen boards are last-run-wins.** FB_E is the candidate universe of
  the last pregame run each day, which is mostly the late games. It is not
  every run's universe.
- **The fit sample mixes populations.** DB is a p-surfaced subset of the
  board, while FB is the full board. Evaluation uses FB only.
- **Three slates are a single short regime.** Even a successful primary
  result would be prospective evidence, not confirmation. Promotion would
  require its own proposal and Jacob's approval.
- **The equal-volume comparison is expected to be underpowered.** It is
  about 1 to 13 champion picks per slate.

Alligator

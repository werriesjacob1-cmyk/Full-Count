# Disagreement equal-volume promotion protocol -- LOCKED before the rebuilt canonical dataset exists

Written 2026-08-25, before `rows_canonical.jsonl` has been rebuilt, per
the standing research-integrity instruction: define the promotion
methodology BEFORE seeing the new data, so the eventual result cannot be
unconsciously tuned toward a desired answer. Once the rebuild finishes,
`backtest/disagreement_experiment_runner.py` executes exactly this
protocol -- do not modify this document's decision logic after the data
returns, except to fix a genuine methodological bug found during
execution (and if that happens, document the fix and why, here, before
re-running).

## Scope

Batter markets only, restricted to the two markets with real (non-constant)
`cat_*` component data: `hits` and `hits_runs_rbis` -- confirmed in
`backtest/disagreement_priority1_2_3_2026-08-25.md`'s component audit
(`cat_environment` is a constant 50 across all rows due to `--no-weather`;
`cat_context` is also constant for `strikeouts`, the only pitcher market
with any `cat_*` data at all). No pitcher-market disagreement claim is in
scope for this protocol.

## The metric under test

`baseline_context_conflict = cat_baseline_skill - cat_context`, three
tiers (`conflict_tier()` in `backtest/disagreement_decomposition.py`,
unchanged since before the restart):
- `high_empirical_low_context`: conflict >= +20 (Weston-like)
- `high_context_low_empirical`: conflict <= -20
- `balanced`: everything else

These thresholds (+20/-20) are the ones already used in the pre-restart
analysis that produced the reported 7-11pp separation. Not re-tuned here.

## SAFE POOL / eligible population

`predicted_prob >= 0.60` (`generate_picks.py`'s own `MIN_LINE_PROB`),
matching every other equal-volume test run this session
(`pa_opportunity_model.py`, `residual_challenger_model.py`,
`disagreement_challenger_model.py`) -- not a new definition invented for
this protocol.

## VOLUME / matched-volume logic

Exactly `pa_opportunity_model.equal_volume_ranking_comparison()`'s
existing, already-tested logic, reused verbatim: current selection is
every row with `predicted_prob >= 0.60`; challenger selection is the
SAME COUNT (`n`) of rows, ranked by the challenger's own score,
descending. No new volume-matching logic is written for this protocol.

## PRIMARY CHALLENGER -- declared now, before results

`disagreement_challenger_model.py`'s existing implementation:
`challenger_prob(row) = cell_rate[(prob_bucket, conflict_tier)]`, fit on
2024-2025 training data ONLY, applied to 2026 holdout rows ONLY, falling
back to the bucket's tier-blind average when a specific
`(bucket, tier)` cell is too sparse (`MIN_CELL_N = 200`). This is
Priority 5's item C ("disagreement-aware secondary sort") in spirit --
it reorders within the safe pool by how the SAME nominal-probability
bucket's disagreement tiers have empirically realized historically, on
data the challenger has never seen (train/holdout split, not the whole
dataset).

**This is the ONE challenger the promotion verdict is based on.** No
other variant changes the verdict.

## SECONDARY variants -- predeclared, reported for context only, NOT decision-determining

At most these two, run and reported alongside the primary result, never
substituted for it if they happen to look better:
1. **Tiebreak-only variant**: keep the CURRENT board's own ranking as
   primary sort key; use `conflict_tier` only to break ties among rows
   with identical `predicted_prob` (a much weaker intervention than the
   primary challenger's full reordering).
2. **Pooled-market variant**: run the primary challenger's exact method
   with `hits` and `hits_runs_rbis` pooled into one training population,
   instead of fit separately per market -- tests whether market-specific
   fitting materially matters.

## SIGNIFICANCE

Two-proportion z-test on added-picks-hit-rate vs removed-picks-hit-rate
(exactly the test already used in
`backtest/priority3_4_5_residual_challenger_closure_2026-08-25.md` --
same formula, same interpretation: pooled proportion, standard error from
both group sizes, report z and an approximate two-sided p-value). No new
statistical test invented for this protocol.

## TIME STABILITY

Report added-vs-removed and overall challenger-vs-current comparisons
split by year (2024/2025/2026, matching every other year-stability check
this session) AND by season phase (using
`canonical_baseline_report.season_phase()`, unchanged). A result driven
by one year or one phase, with the opposite sign in others, does not
pass this protocol's promotion bar even if the pooled number looks good.

## PROMOTION BAR

Disagreement EARNS shadow testing only if ALL of the following hold on
the primary challenger's holdout result:
1. Positive net equal-volume gain (challenger hit rate > current hit rate).
2. Added-picks hit rate materially exceeds removed-picks hit rate --
   "materially" is operationalized as the two-proportion z-test above
   reaching z >= 1.96 (p < 0.05, two-sided) -- the same bar the
   opportunity-thread closure implicitly used (it closed at z=0.80,
   p≈0.42; this protocol makes explicit what "not close" already meant
   in practice).
3. The added-vs-removed direction is consistent (not reversed) in at
   least 2 of the 3 years AND in the season phase(s) with adequate
   sample (>=200 rows) to make a judgment.
4. No leakage: every input to the challenger (`cat_baseline_skill`,
   `cat_context`, `predicted_prob`) is pregame-computed per
   `generate_picks.py`'s own scoring pass (already established, not
   re-litigated here); the train/holdout split is strictly by year with
   holdout never touching fitting.

If ANY of these fail, the verdict is **CLOSED**, following exactly the
precedent set by the opportunity-shortfall thread's own closure
(`backtest/priority3_4_5_residual_challenger_closure_2026-08-25.md`):
"[mechanism] is a real outcome mechanism but is already sufficiently
priced into current selection for practical purposes" (or the analogous
statement if the disagreement mechanism itself doesn't reproduce at all
-- see the runner's own REPRODUCED/NOT_REPRODUCED check, which runs
before this promotion logic and can short-circuit it).

## What would NOT justify reopening this protocol after seeing results

- A secondary variant scoring higher than the primary challenger.
- Widening the conflict-tier thresholds (+20/-20) to manufacture a larger
  gap.
- Lowering the significance bar because the observed z is close but
  under 1.96.
- Extending the eligible population beyond `predicted_prob >= 0.60` to
  find a subgroup where it works.

Any of these, if genuinely warranted by a real methodological flaw found
during execution, must be documented as a NEW, dated addendum to this
file explaining the flaw -- never a silent retroactive edit.

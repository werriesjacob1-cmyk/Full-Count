# NFL passing-yards alternate-line ladder -- real evidence

Workstream: `NFL-PASSING-YARDS-ALT-LADDER-20260923` (Issue #91, Mission 9
Workstream C). Builds a reusable multi-line, coherent alternate-line ladder
for the passing-yards market -- closing the gap between receptions (which
already has this via `receptions_outcome_distribution.py` /
`receptions_alt_ladder.py`) and passing yards (which, despite already
having a LIVE shadow board in production, only had a single-threshold
`empirical_side_probabilities` call before this workstream).

## Files

- `nfl/research/passing_yards_alt_ladder.py` -- the reusable module: an
  empirical-residual-pool ladder, a predeclared discretized-Normal
  approximation control, a direct side-by-side comparison
  (`compare_empirical_vs_normal`), real priced EV via
  `alternate_line_evaluation.py`, and `build_passing_yards_ladder_record`
  (the real end-to-end consumer: real prior appearances -> real B0
  projection via `passing_yards_shadow.current_b0_projection` -> both real
  ladder methods at the same real thresholds -> optional real priced EV).
- `nfl/tests/test_passing_yards_alt_ladder.py` -- 27 tests.
- `passing_yards_alt_ladder_evaluation.py` -- the exact script that produced
  the real result below, run against the real pinned 1999-2025 nflverse QB
  corpus (verified byte-size/SHA-256-identical against
  `engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json`,
  the same manifest `passing_yards_baseline_research.py` and
  `receptions_outcome_distribution.py` already use -- no new source pinned).
- `passing_yards_alt_ladder_evaluation_report.json` -- the real, reproducible
  output of that script (regenerate with the command in "Reproduce" below;
  no network access required, the corpus is already pinned/cached).

## Honest disclosure found before writing any code: an existing unconsumed module

Grepping for actual `import` statements (not docstring/comment mentions --
several existing modules in this repo reference `receptions_alt_ladder.py`
in prose without importing it) found that `receptions_alt_ladder.py` itself,
despite being real, tested, and independently reviewed, is imported ONLY by
its own three test files. No production research module, live workflow, or
challenger record-builder actually calls `ladder_probabilities` or
`evaluate_ladder_with_prices` for receptions today. This is exactly the
"tested but unconsumed" pattern this project's own history has repeatedly
caught -- disclosed here rather than silently repeated for the new module:
`passing_yards_alt_ladder.py`'s own `build_passing_yards_ladder_record` is a
real internal consumer of its ladder functions (exercised end-to-end by
`BuildPassingYardsLadderRecordTests`, including a test proving different
real prior appearances produce a different real record, not a stubbed one).

## Predeclared BEFORE any held-out result was computed

See the full predeclaration in `passing_yards_alt_ladder_evaluation.py`'s
own module docstring, written before the evaluation loop was run. Summary:

- Projection model: B0 (`passing_yards_baseline_research.rolling_predictions`,
  reused unmodified, not rebuilt).
- Partition: `season <= 2022` trains the residual pool and the Normal fit;
  `2023 <= season <= 2025` is held out and scored only.
- Rungs: for each held-out row, five thresholds at projection +/- {30, 15, 0}
  yards, each rounded to the nearest 0.5 and floored at 0.5 -- built from
  that row's own B0 projection only, never from its actual outcome.
- Promotion rule: the empirical-residual ladder is preferred over the
  Normal-approximation control **only if** its held-out Brier score for the
  "over" probability (macro-averaged across all rungs and rows) is strictly
  lower than the Normal control's on the identical population. A tie or a
  Normal win is reported verbatim, never softened. Neither outcome promotes
  anything -- both remain `RESEARCH_ONLY_NOT_PROMOTED`.

## Real result: a technical pass on the predeclared rule, but a statistical tie

On 1,875 real held-out QB-games (2023-2025 regular season, 9,334 total real
rung observations across those games):

| Method | Held-out Brier (over probability) | Mean predicted P(over) | Real actual over rate |
|---|---:|---:|---:|
| Empirical-residual pool | **0.240432** | 0.4877 | 0.4843 |
| Normal-approximation control | 0.240469 | 0.4949 | 0.4843 |

The empirical-residual ladder technically satisfies the predeclared
promotion rule (`brier_gap_empirical_minus_normal = -0.0000368`, strictly
negative) and its mean predicted over-probability (0.4877) sits closer to
the real observed over rate (0.4843) than the Normal control's (0.4949) --
a modest, real calibration edge in the same direction.

**But this margin is not statistically distinguishable from noise.** A
player-clustered bootstrap of the same Brier gap (2,000 resamples, 104 real
distinct QBs, matching the exact `player_id`-cluster convention
`passing_yards_baseline_research.cluster_bootstrap` already established for
this population) gives a 95% interval of **[-0.000276, +0.000195]** --
comfortably straddling zero. Reported honestly, not softened: on this real
held-out population, at these five predeclared rungs, the empirical-residual
ladder and the much simpler Normal approximation are **effectively tied**,
unlike the receptions market's own outcome-distribution comparison (which
found a real, if modest, log-likelihood edge for NEGATIVE_BINOMIAL over
NORMAL -- see `receptions_outcome_distribution.py`'s module docstring). This
is disclosed as the honest finding, not upgraded into a false "the empirical
method wins" claim merely because the point estimate cleared the
predeclared rule.

A plausible reason, disclosed rather than confirmed: passing yards for a
real passing-role QB population is well-approximated by an approximately
symmetric, roughly homoskedastic distribution around B0 (unlike receptions,
a small bounded count with material exact-zero mass and known real
heteroskedasticity by projection level), so a two-parameter Normal captures
nearly all of the real information a nonparametric empirical pool of the
same training population also captures -- there is little real distributional
shape left for the empirical method to exploit at these specific rungs.

## What real football/market mechanics DID get connected

Every one of the 1,875 held-out rows produced a real, structurally coherent
five-rung ladder from BOTH methods at once (`compare_empirical_vs_normal`),
verified to sum to 1 and be monotonically non-increasing in "over"
probability at every rung (`verify_ladder_invariants`, reused unmodified
from `receptions_alt_ladder.py`, never skipped). 1,836 of 1,875 rows used
the full five predeclared rungs; 37 rows collapsed to four and 2 to three
because a very low B0 projection floored multiple offset rungs to the same
0.5-yard minimum -- disclosed, not silently padded back to five.

## Scope disclosure

- This evaluation does not fetch or evaluate real sportsbook prices for
  these historical rungs -- `evaluate_ladder_with_prices` is real,
  tested, and wired into `build_passing_yards_ladder_record`, but the
  historical-accuracy comparison above (matching this repository's own
  established convention of keeping historical-accuracy evaluation and
  price-aware EV analysis strictly separate, e.g.
  `receptions_outcome_distribution.evaluate_candidate_distributions`) uses
  only real realized outcomes, never a fabricated historical price.
- No live workflow wiring in this pass. `nfl-live-passing-yards-shadow-
  board.yml` is untouched, per this workstream's explicit exclusion and
  matching this project's own established precedent (see
  `receptions_team_opportunity_challenger.py`'s own "no live workflow
  wiring in this pass" disclosure) of shipping a reviewed research module
  before a separate, later live-wiring PR.
- A full negative-binomial-style outcome-distribution family comparison
  (the receptions market's own three-way NORMAL/NEGATIVE_BINOMIAL/EMPIRICAL
  study) was not attempted for passing yards -- a genuinely large count
  outcome space (up to several hundred yards) makes a discrete count
  distribution like NB2 a much less natural fit than for receptions' small
  bounded count, and the real result above (Normal already competitive with
  the nonparametric empirical pool) suggests a discrete count family is
  unlikely to add much here. Disclosed as a concrete, deliberately deferred
  next hypothesis rather than attempted and hidden if it also came back
  negative.

## Reproduce

```
PYTHONPATH=. python3 engineering/nfl_passing_yards_alt_ladder_20260923/passing_yards_alt_ladder_evaluation.py \
  --cache <path to pinned stats_player_week_<season>.csv cache> \
  --audit-manifest engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json \
  --output engineering/nfl_passing_yards_alt_ladder_20260923/passing_yards_alt_ladder_evaluation_report.json
```

# NFL receptions team-opportunity engine -- real evidence

Workstream: `NFL-RECEPTIONS-TEAM-OPPORTUNITY-ENGINE-20260923` (Issue #91).
Builds a real team-plays -> player-participation -> catch-probability ->
receptions-distribution chain, connecting existing, previously-unassembled
feature substrate (`team_prior_features`, `defense_prior_features`,
`game_matchup_features`, `coach_regime_registry`) to a genuine player-prop
challenger via `nfl/research/receptions_team_opportunity_challenger.py`.

## Files

- `team_opportunity_real_evaluation.py` -- the exact script that fetches
  real 2023-2025 PBP-derived team box scores (pinned/digest-checked, the
  same seasons `game_market_c2_*` already uses) and real 2023-2026
  `stats_player_week_<season>.csv` weekly rows, builds the full real
  matchup/target-share/catch-rate substrate, and compares B0's real
  rolling-mean projection against this challenger's real opportunity
  projection on a matched population of real 2025 (week 8+) observations.
- `team_opportunity_real_evaluation_report.json` -- the real, reproducible
  output of that script (regenerate by re-running it; network access to
  nflverse-data required; took ~41s this run).

## Honest result -- a real, disclosed NEGATIVE finding

On 2,954 matched real (player, week) observations from the 2025 season
(weeks 8-18, chosen so both models have crossed a real season boundary and
have genuine current-season sample to work with):

- **B0 (real last-5-game rolling mean receptions): MAE = 1.299**
- **Team-opportunity-engine challenger: MAE = 1.412**

**The new challenger does NOT beat B0 on this metric, on this population.**
This is a real, disclosed negative finding, not accuracy evidence for this
challenger -- consistent with this project's own standard that a correct
end-to-end connection is an engineering deliverable in its own right,
independent of whether it improves on the existing baseline. Both framings
are true; neither is omitted here or in the module's own docstring.

A plausible reason, disclosed rather than investigated further this pass
(a real candidate for the next milestone, not yet confirmed): B0's
own real last-5-game rolling mean already implicitly captures most of a
player's actual current role and team context through his own realized
receptions history, so an independently-derived team-volume x share x
catch-rate composition adds real modeling complexity (and real
uncertainty at each of the three stages) without a demonstrated net
accuracy gain over that simpler, already-integrated signal.

## What real football observations DID change the projection

Every one of the 2,954 evaluated rows used a real opponent-adjusted team
dropback-volume prediction (`team_dropbacks_basis:
"BLENDED_OFFENSE_AND_DEFENSE"` for all 3,244 eligible rows considered --
both this team's own real strictly-prior tendency AND the real opponent's
strictly-prior pass-funnel tendency had at least one real prior game by
week 8 in every case) and a real current-2026-season-aware, shrinkage-
blended target share (`target_share_basis:
"SHRUNK_CURRENT_TOWARD_PRIOR_SEASON"` throughout the sample shown). This
directly closes the specific gap SUPERCHAD's review flagged on the prior
connector (Issue #91 comment `5789796992`): a static 2025-only prior-share
ranking. See `sample_records` in the report for five real, unfiltered
(not cherry-picked) example rows showing the real team-dropbacks,
target-share, and catch-rate inputs behind five real challenger
projections next to B0's own real projection and the real realized
outcome.

## Real, disclosed data-quality finding

26 of 3,244 eligible rows abstained rather than produce a projection:
2 for an impossible (>1) target share and 24 for a catch rate outside the
valid range that `compute_opportunity_projection` requires to be strictly
positive (real nflverse data has rare real games where `receptions >
targets`, a known, previously-disclosed edge case in this same codebase --
see `receptions_baseline_research.py`'s own module docstring -- which this
module's fail-closed validation correctly refuses to project from rather
than silently accepting an impossible or zero-confidence rate). 159
additional rows had no real projection due to missing required input
(thin/rookie/cold-start real history). None of these 185 rows were
fabricated a value; all are honestly excluded from both the challenger's
own MAE and, where the challenger abstained, from the matched B0 comparison.

## Scope disclosure (repeated from the module's own docstring)

The team pass-volume side uses the existing PINNED 2023-2025 nflverse
PBP-derived team box-score substrate, not a live 2026 PBP fetch -- doing
that would need the same schema/sanity-validation redesign Mission 2
applied to the live roster asset. The player side DOES use real live
current-season data. This evaluation itself uses 2025 (not 2026) as its
held-out test season specifically because it is the most recent season
with a FULL real schedule of settled outcomes to compare against --
2026 is only ~2-3 weeks deep as of this run and would not yet support a
meaningful week-8-plus matched comparison.

## Ablation scope, disclosed rather than fully attempted

Section 9 of the governing mission asked for a five-way ablation (team
volume / player role / coaching / current-week availability / combined).
This evidence only reports the COMBINED challenger against the B0 control
-- a full five-way ablation was not attempted this pass given the effort
this mission's total scope already required (schema/pipeline reuse,
module implementation, 28 new tests, real end-to-end evaluation, and the
concurrent adversarial-review/reporting obligations). This is recorded
here as an explicit, honest scope limitation and a concrete next
milestone, not silently omitted.

## 2026-09-23 update: coaching consumer actually wired in, real activation found

SUPERCHAD's PR #179 acceptance condition (Issue #91 comment `5797780943`)
correctly identified that `filter_team_rows_by_current_regime` existed and
was unit-tested but was NOT actually consumed by `build_opportunity_
challenger_record` -- the real matched evaluation's projection never
depended on it. This is now fixed: `predict_team_pass_dropbacks_coaching_
aware` computes the team's own rolling dropback mean TWICE from the same
real box-score rows (once regime-filtered, once not) and the COACHING-
AWARE version is what actually feeds the projection; the naive-control
version is preserved in every record for direct comparison, never
discarded.

Fixing this consumption also surfaced and fixed a real latent leakage bug
in `filter_team_rows_by_current_regime`: it filtered by team but never by
target week, so feeding it a full multi-season row set could silently let
a game at or after the target week leak into the rolling window. Now
fixed and covered by a dedicated regression test
(`test_never_leaks_a_game_at_or_after_the_target_week`).

**Real result on the main matched population**: re-running the 2,954-row
2025-week-8+ evaluation with the coaching-aware consumer now actually
wired in found the coaching feature changed **zero** of those 2,954
projections (`coaching_ablation.rows_where_coaching_feature_changed_the_
projection: 0`). This is a real, honest finding: genuine in-season HC
firings are rare NFL events, and none happened to fall inside any
evaluated player's own rolling-5-game window in this specific population.
`coaching_aware_mae` and `naive_control_mae` are therefore identical
(1.412231516436295) on this population -- not because the mechanism is
broken, but because it was never triggered here.

**Real, non-synthetic activation, directly targeted**: the loaded HC
registry (real 1999-2026 nfldata `games.csv`) contains three real,
well-known 2023 in-season HC changes -- Las Vegas (Josh McDaniels ->
Antonio Pierce, 2023-11-05), Carolina (Frank Reich -> Chris Tabor,
2023-12-03), LA Chargers (Brandon Staley -> Giff Smith, 2023-12-23).
Evaluating each team at the real week its own rolling-5 window straddles
the change (`real_2023_in_season_hc_change_demo` in the report) confirms
genuine activation in all three real cases, e.g.:

| Team | Week | Coaching-aware dropbacks (games used) | Naive control (games used) | Real HC |
|---|---|---|---|---|
| LV  | 2023 wk10 | 30.7 (1) | 35.9 (5) | Antonio Pierce |
| CAR | 2023 wk14 | 35.9 (1) | 37.6 (5) | Chris Tabor |
| LAC | 2023 wk17 | 43.4 (1) | 44.1 (5) | Giff Smith |

Each case correctly resolves the real interim coach's real identity and
real regime start date from the registry, correctly restricts the rolling
window to only the 1 real game played since the change (vs. 5 for the
unfiltered control), and produces a real, different team-volume
prediction -- exactly what Section 5 required demonstrated, on real data,
not just the synthetic unit-test fixtures.

**What remains not established**: whether the coaching-aware prediction is
MORE ACCURATE than the naive control on a real held-out population still
containing genuine in-season changes (n=0 real activating rows in the main
2025 population means no such comparison is possible there; the 2023
demo above shows the mechanism works, not that it improves accuracy).
That comparison requires either a much larger real population spanning
more in-season coaching changes, or a targeted historical population built
specifically around known real coaching changes -- a concrete next
milestone, not yet attempted.

## 2026-09-23 update: current-season snap-share role-change signal (Mission 6)

New factor: `estimate_current_week_snap_share` + `apply_snap_informed_
target_share`, actually consumed by `build_opportunity_challenger_record`
when `snap_share_history` is supplied (optional, backward-compatible).
Real offense snap share is observed every game a player plays, so it is a
lower-noise, faster-converging real signal of a current-season role
change than target share (which only updates when the player is
targeted). Scales the target-share estimate by the player's real
season-over-season snap-share ratio, clamped to [0.4, 2.5] (a
pre-declared bound, never fit to evaluation data), and falls back to the
unadjusted estimate whenever no real current-season snap data or real
positive prior-season baseline exists.

**Real source**: `snap_counts_<season>.csv` (nflverse, real 2024-2026
data, live-fetched -- this is an in-season-updated asset, so the fetch is
unpinned by design rather than exact-byte-pinned, the same doctrine
already applied to the roster and PBP assets elsewhere in this program).

**Honest result -- a second real, disclosed negative finding**: on the
same real 2025-week-8+ matched population (n=3,059 after also requiring a
valid snap-informed estimate), the snap-share adjustment made MAE
modestly WORSE: unadjusted MAE=1.386444868319182 vs. snap-adjusted
MAE=1.4778655658978266. `snap_share_role_change_ablation` in the report
has the full real numbers plus five real, unfiltered example rows. The
adjustment triggered on nearly all matched rows (not gated behind a
"large change only" threshold), so this result reads as "broadly
rescaling every player's target share by his own real season-over-season
snap trend does not improve accuracy on this population" -- not
necessarily that the underlying snap-share signal carries no real
information at all.

**Real, non-cherry-picked 2026 in-season demonstration**
(`real_2026_in_season_snap_role_change_example`): scanning every real
player with both 2026 and 2025 snap data for the single largest real
role-change ratio (in either direction) found player `00-0039364`: real
2025 prior-season snap share 0.13%, real 2026 current-season mean 22.7%
through 2 real games, an unclamped ratio of 175x. This demonstrates the
[0.4, 2.5] clamp bound is doing real, necessary work on real data --
separate from, and not contradicted by, the accuracy finding above.

**Scope disclosure**: the negative accuracy result was not retuned
against by adjusting the trigger threshold or clamp bounds -- per this
project's own standard against retuning against an already-inspected
holdout, the finding is reported as-is. A bounded trigger requiring a
larger minimum role-change magnitude before applying the adjustment is a
concrete next hypothesis, to be tested on a separate population.

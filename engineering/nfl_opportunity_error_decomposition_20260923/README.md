# NFL receptions opportunity engine: component error decomposition

Mission 9, Workstream D (`NFL-OPPORTUNITY-ERROR-DECOMPOSITION-20260923`).
Independent diagnostic research, not the original author of any module
evaluated here. Every predictive function used is read-only, unmodified
reuse of existing code:

- `nfl/research/receptions_team_opportunity_challenger.py` (merged on
  `main`): `predict_team_pass_dropbacks`,
  `predict_team_pass_dropbacks_coaching_aware`,
  `filter_team_rows_by_current_regime`, `estimate_current_week_target_share`,
  `estimate_current_week_catch_rate`, `estimate_current_week_snap_share`,
  `apply_snap_informed_target_share`, `compute_opportunity_projection`.
- `nfl/research/qb_change_team_dropbacks.py` (draft PR #185): brought into
  this branch as a **byte-identical, unmodified** copy because it exists
  only on that unmerged branch and this workstream needs to import it. It
  will be superseded automatically once PR #185 merges to `main`; nothing
  in it was edited here.
- `receptions_shadow.current_b0_projection` -- B0, the baseline everything
  in this report is measured against.
- `engineering/nfl_opportunity_error_decomposition_20260923/
  decomposition_lib.py` -- the **only genuinely new code** this workstream
  introduces (three functions, unit-tested in
  `nfl/tests/test_nfl_opportunity_error_decomposition_lib.py`): a combined
  coaching+QB-continuity team-volume estimator
  (`predict_team_pass_dropbacks_coaching_and_qb_aware`), a pure-OR
  role-transition subgroup flag (`role_transition_subgroup_flag`), and a
  player-clustered bootstrap MAE-difference helper
  (`player_clustered_bootstrap_mae_diff`, matching draft PR #184's own
  bootstrap methodology template).

## Population: why 2023, and what is confirmatory vs. exploratory here

Every prior evaluation of this challenger used either the 2025 season
(weeks 8+ -- Missions 3, 4, 6) or the 2024 season (weeks 8+ -- draft PR
#184's diagnostic ablation) as its test population. Per this project's own
anti-retuning doctrine (`engineering/AGENT_BRIDGE_PROTOCOL.md`: "held-out
results cannot be tuned into the challenger"), neither is a fair target for
a new confirmatory result about the same features.

**This report's primary evidence uses the 2023 season (weeks 8+)** --
genuinely never used as an *aggregate MAE evaluation target* in this
repository before this script. The one narrow, disclosed overlap: three
specific 2023 team-weeks (LV week 10, CAR week 14, LAC week 17) were
inspected *qualitatively* in Mission 4 to demonstrate that the coaching
filter mechanically fires on a real regime change -- never as an aggregate
accuracy number, and it played no role in choosing this script's design or
any threshold. Every MAE/bootstrap number in the tables below is therefore
**confirmatory** (fresh population), except:

- The `identity_transition_subgroup_supplementary` cut, which is explicitly
  labeled **EXPLORATORY** in its own JSON block: its subgroup definition
  was chosen only *after* seeing that the pre-registered OR-of-three
  subgroup was degenerate (see below), so it is disclosed as a post-hoc
  choice, not a pre-registered confirmatory test -- even though it still
  uses only 2023 data and an already-existing flag, never inventing a new
  threshold from an outcome.
- One comparison to draft PR #184's own 2024-holdout gating experiment,
  explicitly labeled `RE_ANALYSIS_OF_INSPECTED_POPULATION` below, used only
  as corroborating context, never as new evidence on its own.

Real data sources (all via already-existing, unmodified fetch/build
utilities on `main`): team box scores from pinned nflverse PBP for
2021-2023 (`game_market_c2_data_prep.process_pbp_season`), weekly player
stats for 2021-2023 (`nflverse_history.player_stats_url`, all positions --
QB pass-attempt rows are required to infer real weekly starters via
`qb_continuity_features.infer_team_week_starters`), snap counts for
2022-2023, and the pinned nfldata HC-games registry
(`coach_regime_registry.HC_GAMES_SOURCE`). Full real fetch-and-build run:
75.3 seconds, reproducible via
`engineering/nfl_opportunity_error_decomposition_20260923/
component_error_decomposition.py`; full output in
`component_error_decomposition_report.json`.

Population: 3,150 eligible (player, week) rows (WR/TE/RB, 2023 weeks 8-18).
153 excluded for insufficient real B0 history, 19 excluded per experiment
for a missing real required input at that stage (never fabricated) --
final matched populations range n=2,978 (stage ablation / three-signal) to
n=2,313 (oracle decomposition, which additionally requires the player to
have been targeted at least once that game for a real ex-post catch rate).
All bootstrap CIs below resample by **player**, not by row (2,000+ resamples
each), matching draft PR #184's own template.

## Experiment 1: team-volume-stage ablation (share/rate held fixed)

Target share and catch rate held FIXED at the same real shrinkage-blended
estimate for every variant (no snap adjustment); only the team-volume
estimator differs.

| Variant | MAE | n |
|---|---:|---:|
| B0 | 1.3222 | 2,978 |
| naive/plain blended team volume | 1.4461 | 2,978 |
| coaching-aware | 1.4447 | 2,978 |
| QB-aware | 1.4496 | 2,978 |
| coaching+QB combined (new) | 1.4484 | 2,978 |

All four team-volume variants are worse than B0 by a statistically real
margin (bootstrap 95% CI on the difference excludes zero for all four vs.
B0, e.g. naive vs. B0: +0.1234 [0.0885, 0.1599]). But the four team-volume
variants are **barely distinguishable from each other**: the largest
pairwise real difference is qb-aware vs. naive (+0.0035, CI [0.0004,
0.0069] -- statistically real given n=2,978 but a 0.24% relative change,
practically negligible), and combined vs. naive is not even statistically
distinguishable (CI [-0.0010, 0.0058], includes zero). **Which team-volume
estimator you use barely matters; something else is driving essentially
all of the gap versus B0.**

## Experiment 2: oracle stage-substitution decomposition (diagnostic only)

For each row, exactly ONE of (team dropbacks, target share, catch rate) is
replaced by its REAL, ex-post-observed value for that specific game -- a
real fact never available before kickoff, used here purely to bound how
much each stage's own estimation error contributes to the chain's total
error -- while the other two stay at the model's own real estimate (team
volume = coaching+QB combined, share = snap-informed, matching the "full
chain" as currently composed). This is historical diagnosis, never a
predictive claim.

| Configuration | MAE | Δ vs. full model | 95% CI on Δ |
|---|---:|---:|---|
| Full model (all three estimated) | 1.6059 | -- | -- |
| Oracle team volume, others estimated | 1.5684 | -0.038 | [-0.067, -0.009] (excludes 0) |
| Oracle target share, others estimated | **1.0451** | **-0.561** | **[-0.639, -0.479]** (excludes 0) |
| Oracle catch rate, others estimated | 1.5943 | -0.012 | [-0.069, 0.043] (includes 0) |

n=2,313 for all four rows in this table (matched population requiring a
real ex-post value at every stage).

**This is the single most informative result in this report.** Knowing the
real target share exactly would cut the full chain's MAE nearly in half
(1.61 -> 1.05) -- comfortably *below* B0's own 1.32 MAE on the larger
population. Knowing the real team volume exactly helps a little (a real,
statistically distinguishable but small effect). Knowing the real catch
rate exactly does **not** produce a statistically distinguishable
improvement at all. **The target-share estimation stage is overwhelmingly
the dominant source of this chain's excess error** -- not team volume, and
not catch rate.

## Experiment 3: three-signal combination test (never run before this script)

Same four team-volume variants, now with the current merged snap-informed
target-share adjustment layered on top (the row currently labeled
`coaching_snap` is this repository's actual production-research form;
`combined_snap` -- coaching+QB team volume plus snap-informed share -- has
never been evaluated anywhere in this repository before this script).

| Variant (+ snap-informed share) | MAE | n |
|---|---:|---:|
| B0 | 1.3222 | 2,978 |
| naive + snap | 1.5767 | 2,978 |
| coaching + snap (current production form) | 1.5754 | 2,978 |
| QB + snap | 1.5799 | 2,978 |
| coaching+QB combined + snap (all three signals) | 1.5781 | 2,978 |

All four are worse than B0 by a large, statistically real margin (~+0.19 to
+0.26 MAE, every CI excludes zero). But **combining all three signals
produces no improvement over, and no meaningful difference from, any
single signal alone**: combined-vs-coaching-alone Δ=+0.0027 (CI
[-0.0022, 0.0052], includes 0), combined-vs-QB-alone Δ=-0.0018 (CI
[-0.0042, 0.0003], includes 0), combined-vs-naive+snap Δ=+0.0014 (CI
[-0.0022, 0.0052], includes 0). **This directly answers Mission 9's
"combined" question: no, combining coaching + snap-share + QB-change does
not produce a different result than each alone -- it is still a null
result, with no synergy and no additional damage either.**

**Correction from independent adversarial review**: an earlier version of
this report's PR description and `ENGINEERING_HANDOFF.md` entry
generalized this to "every pairwise 95% CI [among the four variants]
includes zero." That is not accurate: only the 3 pairs above (each pivoted
on `combined`) were originally computed. The reviewer added the missing 3
pairs (`bootstrap_full_pairwise_round_robin` in the JSON report, all 6
unordered pairs) and reran the real evaluation. Result: 4 of 6 pairs
include zero (the 3 above, plus coaching-vs-naive+snap, Δ=-0.0013, CI
[-0.0030, 0.0001]), but **2 of 6 do NOT**: coaching-vs-QB-alone (Δ=-0.0045,
CI [-0.0085, -0.0007], excludes 0) and QB-vs-naive+snap (Δ=+0.0032, CI
[0.0000, 0.0066], excludes 0). Both are small in absolute/relative
magnitude (<0.3% of the ~1.58 MAE level) and consistent in size with the
already-disclosed Experiment 1 finding that QB-aware team volume differs
from naive by a similarly small, statistically real amount (+0.0035, CI
[0.0004, 0.0069]). **This does not change the headline conclusion** --
all four variants remain solidly worse than B0 and clustered tightly
together relative to that gap -- but the precise claim should read: most,
not all, pairwise differences among the four variants are statistically
indistinguishable from zero; two of six show a small but real difference
in the same direction and magnitude already documented for the team-volume
stage alone in Experiment 1.

Comparing this table to Experiment 1 (no snap: ~1.446-1.450) shows the
snap-informed adjustment itself adds roughly **+0.13 MAE on top of any
team-volume variant** -- reproducing Mission 6's original negative finding
about the snap-share signal specifically, now confirmed fresh on an
independent 2023 population, and showing it is not an artifact of which
team-volume estimator it happens to be paired with.

## Experiment 4: role-transition subgroup analysis

**Primary, pre-registered cut** (OR of the three flags this codebase's own
challengers already emit unmodified: `coaching_feature_changed_the_
projection`, `qb_feature_changed_the_projection`, `snap_role_change_
applied`): **degenerate**. `snap_role_change_applied` alone fires on
2,957/2,978 rows (99.3%) -- consistent with Mission 6's own disclosure that
it "triggered on nearly all matched rows... not gated behind a
large-change-only threshold" -- so the OR-of-three subgroup covers
2,963/2,978 rows (99.5%). The complementary "general" group has only 15
rows (12 distinct players), far too small to support inference (point
estimate reverses direction -- combined+snap MAE 0.95 vs. B0 1.17 -- but
the CI on that difference includes zero and n=15 cannot rule out chance).
**This flag-based test cannot answer Mission 9's "genuine role-transition
situations only" question as pre-registered**, because the flag it must
rely on does not discriminate.

**Supplementary, EXPLORATORY cut** (disclosed as chosen only after finding
the primary cut degenerate): using only `combined_feature_changed_the_
projection` (the more selective coaching-OR-QB identity flag, 1,022/2,978
rows = 34.3%, excluding the near-universal snap flag) --

| Subgroup | MAE (combined+snap) | MAE (B0) | Δ | 95% CI on Δ | n |
|---|---:|---:|---:|---|---:|
| Real identity transition fired | 1.4981 | 1.2596 | +0.2385 | [0.145, 0.339] | 1,022 |
| No identity transition | 1.6199 | 1.3550 | +0.2650 | [0.193, 0.341] | 1,956 |

Even restricted to rows where a real coaching or QB identity change
actually altered the team-volume prediction, the chain is **still clearly
worse than B0** (CI excludes zero in both subgroups). The gap is somewhat
smaller inside the transition subgroup (+0.24 vs. +0.27), but both are
real, both are negative, and the CIs overlap substantially -- this is not
evidence the signal "works" specifically in genuine transition situations.
**The strong form of the "role signals only help under evidenced role
change" hypothesis is not supported for the coaching/QB identity signals on
this population.**

`RE_ANALYSIS_OF_INSPECTED_POPULATION` (draft PR #184, 2024 holdout,
already inspected -- cited here only as corroborating exploratory context,
not new evidence): a gated (large-change-only) version of the snap-share
adjustment was tested at two thresholds and both remained worse than the
unadjusted baseline, though less damaging than applying it universally
(unadjusted MAE=1.4249, universal=1.6384, gated [0.7,1.4]=1.6141, gated
[0.5,2.0]=1.5785). Consistent with this report's own finding: gating/
restricting to "more extreme" or "more evidenced" signal activity reduces
but does not eliminate the damage from this chain's role-adjustment
signals.

## Hypothesis verdicts

| Hypothesis | Verdict | Basis |
|---|---|---|
| Team passing-volume estimation introduces excess error | **Small, real effect, minor contributor** | Oracle team-volume substitution recovers only ~0.04 MAE (Exp. 2); the 4 team-volume variants barely differ from each other (Exp. 1) |
| Target-share estimation adds noise | **Strongly supported -- the dominant driver** | Oracle target-share substitution recovers ~0.56 MAE, more than the entire gap vs. B0 (Exp. 2) |
| Catch-rate estimates are unstable | **Not supported on this population** | Oracle catch-rate substitution's effect is not statistically distinguishable from zero (Exp. 2) |
| B0 already captures role information the chain's explicit estimators re-derive worse | **Supported, indirectly** | Even the base (non-snap) share/rate estimate is ~0.12 MAE worse than B0 before any of the three new signals are added (Exp. 1); an oracle share fixes nearly the entire gap and would even beat B0 (Exp. 2) |
| Broad population-wide adjustments overreact vs. genuine role-transition value | **Cannot be tested as pre-registered (degenerate flag); exploratory cut finds no support** | Primary OR-of-three flag fires on 99.5% of rows (Exp. 4); the more selective identity-only cut still shows the chain losing to B0 inside real transitions |
| Do the three signals combine to a different (better/worse/still-null) result than each alone | **Still null -- no synergy, no additional damage** | All four snap-informed variants (naive/coaching/QB/combined) are clearly worse than B0 and cluster tightly together (Exp. 3); full round-robin pairwise testing shows 4 of 6 pairs statistically indistinguishable and 2 of 6 with a small but real difference of the same size already seen in Exp. 1's team-volume-only comparison -- no evidence of synergy or amplified damage from combining signals either way |

## What remains genuinely unresolved

- **Why is the target-share estimator (the base shrinkage blend, before any
  of the three new signals) already worse than B0's implicit approach?**
  This report identifies the *stage* responsible but does not diagnose the
  estimator's own internal failure mode (e.g., is the shrinkage constant
  `k=3.0` miscalibrated, does the current-season/prior-season split
  systematically lag real trades/depth-chart changes, or is
  `targets / team_week_targets` itself a noisier per-game quantity than
  receptions?). A follow-up decomposing target-share estimation error by
  its own sub-components (current-season sample size, shrinkage weight,
  team-target-total volatility) is a concrete next step, not attempted
  here.
- **Whether a genuinely selective, PROSPECTIVELY chosen gate on the
  snap-share signal could close any of the gap** is not resolved by this
  report or by PR #184's exploratory re-analysis -- both are consistent
  with "gating helps some, not enough," but neither constitutes a clean
  test of a gate chosen without having seen a holdout result. This report
  does not attempt to pick or validate such a gate, consistent with this
  project's anti-retuning discipline.
- **Whether QB-continuity or coaching signals would show real value in a
  larger or differently-selected sample of real transitions** is not
  resolved: the exploratory subgroup here (n=1,022) is real but modest, and
  a dedicated, pre-registered "targeted at real known transitions" study
  (similar in spirit to Mission 4's three hand-picked 2023 coaching-change
  weeks, but done as an aggregate accuracy comparison rather than a
  mechanism demonstration) has not been run.

## Tests

`nfl/tests/test_nfl_opportunity_error_decomposition_lib.py` -- 17 new tests
covering `predict_team_pass_dropbacks_coaching_and_qb_aware` (intersection
behavior, no-op fallback when neither filter changes anything, real blend
with an opponent-allowed value, empty-intersection abstention, no-lookahead
regression), `role_transition_subgroup_flag` (each flag individually, all
combinations, missing-flag-as-false), and
`player_clustered_bootstrap_mae_diff` (zero-diff sanity check, hand-computed
means, player-level row grouping, determinism given a fixed seed, and
fail-closed errors on empty input or non-positive `n_boot`).

## What this does NOT do

- Does not edit `receptions_team_opportunity_challenger.py`,
  `qb_change_team_dropbacks.py`, `injury_availability_features.py`, or any
  workflow YAML.
- Does not merge, promote, or wire anything into a live decision path.
- Does not pick or validate a new gate/threshold against this report's own
  2023 result -- the two subgroup cuts reported here (primary and
  supplementary) are both diagnostic, and the supplementary one is
  explicitly labeled exploratory rather than confirmatory.
- Does not reuse the 2024 or 2025 populations for any new confirmatory
  comparison; the one reference to PR #184's 2024 gating result is labeled
  `RE_ANALYSIS_OF_INSPECTED_POPULATION` and used only as corroborating
  context.

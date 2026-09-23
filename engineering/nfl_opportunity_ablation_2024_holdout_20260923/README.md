# NFL component-level opportunity-engine ablation, 2024 holdout

Workstream `NFL-OPPORTUNITY-ABLATION-2024-HOLDOUT-20260923` (Mission 7,
Workstream C). Diagnostic only -- nothing here is production, validated, or
promoted. `nfl/research/receptions_team_opportunity_challenger.py` was not
modified; every number below comes from real, read-only reuse of that
already-merged module.

## Why a new holdout

Every prior evaluation of this challenger (Missions 3, 4, and 6) used the
2025 season (weeks 8+) as its test population. That population has been
repeatedly inspected while building and re-checking these exact features, so
it is no longer a fair holdout for a new hypothesis about the same features
-- this project's own doctrine ("held-out results cannot be tuned into the
challenger", `engineering/AGENT_BRIDGE_PROTOCOL.md`) forbids reusing it. This
script uses the **2024 season, weeks 8+** instead: a genuinely fresh
population, never inspected by any evaluation in this repository before this
script ran.

Team substrate: real PBP-derived team box scores for 2022-2024 (the existing
pinned `game_market_c2_source_digests.PBP_SOURCE_ASSET_DIGESTS`, which
covers 1999-2025). Player substrate: real weekly stats for 2022-2024 and
real snap counts for 2023-2024 (live-fetched, same pattern as the existing
2025 evaluation script -- not the digest-gated `fetch_snap_count_rows`).
Real HC coaching-regime registry from the same pinned `nfldata/games.csv`
commit already used elsewhere in this repo.

Reproduce with:

```
python3 engineering/nfl_opportunity_ablation_2024_holdout_20260923/component_ablation_2024.py
```

Full real output: `component_ablation_2024_report.json` (generated in ~24s
against live network data on 2026-09-23).

## Population

3,175 real eligible (player, week) observations: WR/TE/RB, 2024 weeks 8-18,
matched to a real scheduled game and a real built matchup row. Each ablation
group below reports its own matched `n` and honestly discloses which rows it
had to exclude and why -- no projection is ever fabricated for a row with a
missing real required input.

## 1. Core comparison: B0 vs. team-volume-only (naive share) vs. full engine

Matched n = 2,907 (247 rows excluded for insufficient B0 history -- fewer
than 3 real prior appearances; 21 more excluded for an out-of-range naive or
shrunk catch rate/target share, the same real nflverse `receptions > targets`
edge case already disclosed for the 2025 population).

| Component | MAE | vs. B0 |
|---|---:|---:|
| **B0** (real last-5-game rolling mean) | **1.3532** | -- |
| Team-volume-only (real dropbacks x naive, unshrunk recent-average share/rate) | 1.5044 | +0.1512 worse |
| Full opportunity engine (coaching-aware dropbacks x shrinkage-blended share/rate) | 1.4555 | +0.1023 worse |

**Both real challengers are worse than B0 on this fresh 2024 holdout.** The
direction matches the already-established 2025 finding (B0 MAE=1.299 vs.
opportunity-engine MAE=1.412, a *different, non-comparable* matched
population -- do not average or combine these two MAE figures). This is a
second, independent real negative result, not a re-run of the first: the
team-opportunity engine underperforming B0 is not an artifact of the 2025
population's specific games.

The team-volume-only baseline (naive share, no shrinkage) is the WORST of
the three here -- worse than the full engine, not better -- so the
shrinkage-blended share/rate estimators are doing real, measurable work
relative to a naive recent average; they just aren't enough to close the gap
with B0's own rolling-mean approach.

## 2. Coaching-adjustment isolation

Matched n = 3,026. `coaching_aware_mae` and `naive_control_mae` are
**numerically identical: 1.4249**. `rows_where_coaching_feature_changed_the_
projection = 0` out of 3,175 eligible rows -- every one of the 3,175 real
`lookup_regime` calls resolved (`RESOLVED`: 3175), but no real in-season HC
change happened to fall inside any evaluated player's own rolling-5 window
in this population.

This is the same honest null result Mission 4 found on the 2025 population,
now confirmed independently on 2024: genuine in-season HC changes are rare,
and none of the real, known in-season 2023 HC changes (LV, CAR, LAC) reach
into a 2024-week-8+ player's own 5-game rolling window, since that window
never crosses back into a prior season for a week-8+ target. This does not
mean the coaching consumer is broken -- Mission 4's own targeted 2023 demo
already confirmed genuine, non-synthetic activation when a real regime
change *does* fall inside the window -- it means this particular matched
population (like the 2025 one) contains zero such cases.

## 3. Snap-share adjustment: universal vs. gated (the hypothesis under test)

Matched n = 3,026 (real plain team dropbacks x shrunk share/rate as the
"unadjusted" control -- the same comparison shape the 2025 snap-share
evaluation used, so the *method* is directly comparable even though the
population is not).

| Variant | MAE | Rows adjustment applied | vs. unadjusted |
|---|---:|---:|---:|
| **Unadjusted** (shrunk target share, no snap signal) | **1.4249** | -- | -- |
| Universal (current merged form, no gate) | 1.6384 | 2,978 / 3,026 (98.4%) | +0.2135 worse |
| Gated, ratio outside [0.7, 1.4] | 1.6141 | 1,010 / 3,026 (33.4%) | +0.1892 worse |
| Gated, ratio outside [0.5, 2.0] | 1.5785 | 495 / 3,026 (16.4%) | +0.1536 worse |

**Headline finding, stated plainly: on this fresh 2024 holdout, EVERY
variant of the snap-share adjustment makes MAE worse than the unadjusted
baseline -- universal, and both gated thresholds.** This extends the 2025
negative finding to a second, independent population: broadly rescaling
target share by a season-over-season snap-share ratio does not improve
receptions-projection accuracy here, whether it is applied to (almost)
every row or gated to only the rows with the most extreme real ratios.

**The one genuinely positive-direction finding in this report**: gating
*does* reduce the damage relative to applying the adjustment universally,
and the effect is monotonic with how selective the gate is --

- gate [0.7, 1.4] (1,010 rows gated in) recovers 0.0243 MAE versus universal;
- gate [0.5, 2.0] (495 rows gated in, the most selective) recovers 0.0599
  MAE versus universal -- more than double the recovery of the tighter band,
  despite (because of) gating in fewer than half as many rows.

So the real signal is directionally consistent with SUPERCHAD's suggestion
(Issue #91 comment `5799901415`): restricting the adjustment to more
extreme, more clearly evidenced role-change rows hurts less than applying it
to the whole population, and hurts less the more selective the gate is made.
**But neither gate closes the gap with the unadjusted baseline** -- the best
tested gate ([0.5, 2.0]) is still 0.1536 MAE worse than simply not applying
the adjustment at all. Read honestly: "gating reduces harm relative to
universal application" is a real, positive-direction finding; "gating makes
the snap-share adjustment worth using" is not supported by this evidence --
on this population, leaving target share unadjusted remains the best of all
five snap-related variants tested.

Two real gated-in example rows (gate [0.5, 2.0]) are in
`component_ablation_2024_report.json` under
`snap_share_ablation.gated_thresholds[].real_gated_in_examples` for direct
inspection -- e.g. player `00-0032385` (TB, 2024 week 8): real target share
0.079 unadjusted vs. 0.198 gated (raw ratio 3.45x), projection 1.83 vs. 4.58,
against a real realized 3 receptions -- the adjustment moved the projection
in the right direction for this one row even though the aggregate MAE across
all rows did not improve.

## What this does NOT establish

- Does not identify *which* real mechanism causes the opportunity engine and
  snap-share adjustment to underperform B0 -- only that they do, on two
  independent real populations now. A per-position or per-role-change-size
  breakdown was not attempted here (a disclosed scope limit, not hidden).
- Does not test any gate threshold beyond the two specified thresholds
  ([0.7, 1.4] and [0.5, 2.0]) -- an even more selective gate might recover
  more, but that would require a THIRD fresh holdout to test honestly rather
  than further narrowing against this same 2024 population, per this
  project's own anti-retuning doctrine.
- Does not re-test the coaching-adjustment's real accuracy impact (Mission
  4's own open question) -- zero activating rows existed in this matched
  population either, same as 2025.
- This is historical operational testing on real, already-settled games, not
  prospective evidence.

## Reproducibility

Real, executed, network-fetched (2026-09-23). Full JSON output committed
alongside this README: `component_ablation_2024_report.json`. New gating
helper: `gating.py`, unit-tested in
`nfl/tests/test_opportunity_ablation_2024_gating.py` (7 tests).

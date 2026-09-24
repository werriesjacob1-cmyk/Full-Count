# Mission 10 pre-registration: unit-consistent pregame target-share stage

**Locked:** 2026-09-24, before any 2019-2022 outcome was read by this
workstream. The commit that adds this file must come BEFORE the commit
that adds `holdout_evaluation_report.json`. The git history is the proof of
order; nothing below may change once the holdout is run.

## Why this challenger (exploratory evidence, already-inspected data)

Exploratory diagnosis on already-inspected 2024+2025 weeks 8+ (n=5,877,
`exploratory_diagnosis_report.json`):

- Six pregame share estimators all landed within 0.001 share-MAE of each
  other (~0.050). The share estimate itself is not the problem.
- The existing chain multiplies a share of TEAM TARGETS by predicted TEAM
  DROPBACKS. Real teams produce 0.828 targets per dropback (p10 0.772, p90
  0.880), so every projection is inflated about 21%. Engine bias: +0.495
  receptions; B0 bias: +0.023.
- Converting with the team's own strictly-prior targets-per-dropback ratio
  gives MAE 1.2982 (bias +0.012) against B0's 1.3242, the unadjusted
  engine's 1.4322, and the full snap-informed engine's 1.5869.

These numbers are EXPLORATORY. They were produced on data this program has
already inspected many times, and they are not evidence of anything
confirmatory.

## Locked challenger (C1)

`nfl/research/pregame_target_share.py`, exactly as committed with this file:

    expected receptions = predicted_team_dropbacks
                          x team_targets_per_dropback(window=8)
                          x existing target-share estimate
                          x existing catch-rate estimate

- `predicted_team_dropbacks`: the existing `predict_team_pass_dropbacks`
  from real `game_matchup_features` rows, unchanged.
- Target share: the existing `estimate_current_week_target_share`
  (shrinkage k=3), unchanged.
- Catch rate: the existing `estimate_current_week_catch_rate` (k=5),
  unchanged.
- `team_targets_per_dropback`: the team's ratio of summed real team targets
  (from nflverse weekly player stats) to summed real dropbacks
  (PBP-derived attempts + sacks, digest-pinned) over its last 8 games
  strictly before the target game. It crosses season boundaries. With no
  prior game it abstains; it never substitutes a constant.

No parameter was tuned. `window=8` was chosen before the exploratory run
and was not varied there.

## Locked comparison population (genuinely untouched by this workstream)

- Seasons **2019, 2020, 2021, 2022**, REG weeks **8 and later**.
- Positions WR, TE, RB with a real stats row in the target game (the same
  population contract used by every prior opportunity-engine evaluation).
- Paired rows only: every model must produce a projection for the row.
  Abstentions are counted and reported by reason.
- Pregame inputs use only rows strictly before the target (season, week).

Disclosed prior use of these seasons elsewhere in the repo (not by this
workstream's design):

- `role_regime_redistribution.py` used 2012-2021 as TRAIN and 2022-2025 as
  held-out for a different model (teammate-absence committee
  redistribution) on a subset of absence-event rows.
- `receptions_baseline_research.py` scored B0 across 2000-2025. That
  validates the champion; if it biases anything, it biases toward B0.
- No opportunity-engine or target-share-stage aggregate evaluation has used
  2019-2022 weeks 8+ as its target (grep of `engineering/nfl_*`).

## Locked models compared on identical rows

1. **B0**: `receptions_shadow.current_b0_projection` (authoritative).
2. **Existing unadjusted target-share control**: the existing chain with
   the unadjusted share (dropbacks x share x catch).
3. **Existing full engine**: the same chain with the merged snap-informed
   share (`apply_snap_informed_target_share`, digest-pinned snap counts).
   Coaching-regime filtering is not loaded; PR #191 measured every
   team-volume estimator variant within 0.0035 MAE of the naive one.
4. **C1**: the challenger above.

## Locked primary success rule (one test)

- Metric: receptions MAE, C1 minus B0, pooled over all locked rows.
- Uncertainty: player-clustered bootstrap (resample players with
  replacement, all of each drawn player's rows), 2,000 resamples, seed
  `20260924`, 95% percentile interval.
- Verdict:
  - `CONFIRMED_IMPROVEMENT_OVER_B0` if the interval's upper bound < 0.
  - `CONFIRMED_WORSE_THAN_B0` if the interval's lower bound > 0.
  - `NO_DEMONSTRATED_DIFFERENCE_FROM_B0` otherwise.

## Secondary measurements (descriptive only, no success claims)

- C1 vs the existing unadjusted control, and C1 vs the full engine (same
  bootstrap).
- Mean signed error (bias) for every model.
- Per-season, per-position (WR/TE/RB), and per-share-tier (existing share
  <0.10, 0.10-0.20, >=0.20) MAE for B0 and C1.
- Probability quality: every model's projection mapped through the SAME
  Poisson(lambda=projection) distribution. Mean Brier over P(receptions >
  L) for L in {1.5, 2.5, 3.5, 4.5, 5.5, 6.5}, and mean log score of the
  realized count.
- Distribution of the C1/unadjusted projection ratio (how much each
  prediction changed).

## What this does NOT establish, whatever the result

- No real historical sportsbook lines exist in this repo for 2019-2022,
  so there is no equal-volume hit-rate or real-price result here. None
  will be fabricated. Real-price evaluation belongs to the frozen forward
  shadow (2026 week 3 onward), joined to real captured offers if and when
  they exist.
- A confirmed MAE improvement is not a promotion. B0, the live selectors,
  and public picks stay unchanged.

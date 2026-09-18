# FULL COUNT NFL game-market B0 reconciliation

Date: 2026-09-17

Status: research-only control; not promoted

## Purpose

This work reconciles the existing draft B0 stack from PRs #110 and #111 with
the stricter historical source contract in draft PRs #115 and #116. It does
not create a second spread/total model path.

B0 predicts home points, away points, home margin, and total from each team's
five most recent explicitly final regular-season scoring results. It requires
at least three prior games for both teams. It has no fitted parameter and no
home-field adjustment.

## Leakage and provenance controls

- Target-game scores never enter target-game features.
- Only an explicit `FINAL` row advances history; scores do not imply finality.
- An unresolved `PREGAME` row blocks later history for either affected team.
- Market lines do not enter prediction features.
- The input is pinned to
  `nflverse/nfldata@8ed09b2fe3ea42332b2249a995737e13dd931ff3`,
  `data/games.csv`, 2,177,838 bytes, SHA-256
  `26332ae5d8d8d0481f0670cf5e3849497a415351d4026ae5bee15a5aab96d188`.
- The evaluator requires every market row to declare
  `NFLVERSE_PFR_CLOSING`,
  `PRO_FOOTBALL_REFERENCE_VIA_NFLVERSE`, `CLOSING`,
  `RETROSPECTIVE_BENCHMARK_CONTROL_ONLY`, and
  `point_in_time_feature_eligible=false`.

The PFR closing lines are a retrospective accuracy control only. They are not
FanDuel observations, prediction-time inputs, book-specific CLV, or
line-movement evidence.

## Reproduced population

The digest-pinned run read 7,548 source rows and retained 6,967 historical
regular-season finals through 2025. All 6,967 had closing spread and total
controls. B0 produced 6,906 eligible predictions and 61
`INSUFFICIENT_HISTORY` rows.

Fixed partitions:

| Partition | Games |
|---|---:|
| Development, 2000-2019 | 5,095 |
| Validation, 2020-2022 | 796 |
| Held out, 2023-2025 | 816 |

## Results

MAE is in points. Delta is B0 MAE minus closing-control MAE, so positive values
favor the closing control.

| Partition | Target | B0 MAE | Closing MAE | Delta |
|---|---|---:|---:|---:|
| Development | Margin | 11.171 | 10.417 | +0.754 |
| Development | Total | 11.234 | 10.688 | +0.546 |
| Validation | Margin | 10.432 | 9.791 | +0.640 |
| Validation | Total | 11.095 | 10.444 | +0.650 |
| Held out | Margin | 10.473 | 9.744 | +0.729 |
| Held out | Total | 10.719 | 10.121 | +0.598 |

The deterministic 2,000-sample paired game bootstrap on the held-out period
placed the B0-minus-close margin MAE delta between +0.429 and +1.010 points at
the 2.5th and 97.5th percentiles. The total delta interval was +0.321 to
+0.860 points. Both intervals remain above zero.

Held-out prediction-minus-actual bias was -2.304 points for margin and -0.113
points for total. The margin bias is diagnostic evidence for a future
development-only home-field challenger; it is not permission to tune on held
data.

## Decision

B0 is a valid falsifiable control and a useful leakage test. It is not a
selector, probability model, or promotion candidate. The closing market beat
B0 on margin and total in development, validation, and held-out periods.

Next research should preserve B0 unchanged as the control and test
predeclared challengers using development data only, beginning with a
home-field term and then opportunity/context features. Validation and held-out
periods must remain evaluation-only. No prospective selection should begin
until a challenger emits calibrated probabilities and clears explicit
out-of-sample gates.

Alligator

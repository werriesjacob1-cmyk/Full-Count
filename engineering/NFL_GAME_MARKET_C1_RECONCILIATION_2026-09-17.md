# FULL COUNT NFL game-market C1 reconciliation

Date: 2026-09-17

Status: research challenger rejected

## Question

C1 tests one predeclared change to B0: additive margin and total corrections
fit only from eligible 2000-2019 development outcomes.

The fit uses no closing line, price, validation outcome, or held-out outcome.
Its outcome population comes directly from the explicit-final scoring
substrate, so missing closing-market data cannot select the fit population.

## Exact fit

Source and B0 inputs are identical to the digest-pinned B0 reconciliation.
The development fit contains 5,095 games.

| Parameter | Fitted value |
|---|---:|
| Home-margin additive correction | +2.570805 |
| Total additive correction | +0.032159 |

## Out-of-sample results

Delta is C1 MAE minus B0 MAE. Negative values favor C1.

| Partition | Target | B0 MAE | C1 MAE | Delta |
|---|---|---:|---:|---:|
| Validation, 2020-2022 | Margin | 10.431648 | 10.373815 | -0.057833 |
| Validation, 2020-2022 | Total | 11.094546 | 11.098020 | +0.003474 |
| Held out, 2023-2025 | Margin | 10.473039 | 10.434690 | -0.038350 |
| Held out, 2023-2025 | Total | 10.718873 | 10.720213 | +0.001340 |

On the 816-game holdout, the deterministic 2,000-sample paired bootstrap
interval for C1-minus-B0 margin MAE was -0.210 to +0.117 points. It crosses
zero. The total interval was -0.001 to +0.004 and its point estimate was
worse.

## Decision

C1 is rejected for promotion. The margin correction removes a visible mean
bias, but it does not produce a stable or material out-of-sample MAE gain.
The total correction is effectively zero and slightly worsens validation and
held-out results.

This negative result narrows the roadmap: retain B0 as the control and test
strictly prior football opportunity/context features from the already-existing
#106-#109 and #113 feature branches. Do not add the C1 correction to a live
selector or treat it as evidence of betting value.

Alligator

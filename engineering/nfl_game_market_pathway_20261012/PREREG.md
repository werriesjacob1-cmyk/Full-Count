# FC-NFL-004 pre-registration — NFL spread/total distribution benchmark

Written and committed **before** any parameter fit or evaluation in this
workstream. The results file must cite this file's commit. Any deviation is
recorded as a deviation, never silently.

## Population

- Source: pinned nflverse/nfldata `data/games.csv` at commit
  `8ed09b2fe3ea42332b2249a995737e13dd931ff3`, which is 2,177,838 bytes with
  sha256 `26332ae5…6d188`. It is the same contract as `game_market_b0_research`.
- REG games, B0-eligible (rolling-5 strictly-prior scoring, at least 3 prior games
  per team). This is the identical 6,906-game matched population used by PR #222.
- Partitions:
  - development 2000–2019: **the only fit population**;
  - validation 2020–2022: reported;
  - held 2023–2025: reported once. *Previously inspected* by PR #222 for B0, close and the B0 blend, so it is not a virgin holdout.
  - fresh 2026 Weeks 1–5: the REG final rows from the nfldata `master` snapshot, sha256 `9380292232901e4321c99992c7efca0c567a0a801dcb292662a3f78a9a101c49` (observed 2026-10-12). It is virgin but small. B0 history = 2025 REG plus strictly earlier 2026 weeks, matching the live runner.
- Closing lines and closing odds are **retrospective controls only**. They are not point-in-time inputs, and they carry no claim about actionable price.

## Configurations (fixed)

| ID | Center for home margin / game total | Fit on dev |
|---|---|---|
| B0 | existing `GAME_MARKET_B0_PRIOR_SCORING_BLEND`, unchanged | nothing |
| F1 | `a + b·B0` (affine recalibration of B0; tests the "B0 bias/scale" hypothesis) | a, b by OLS |
| M0 | the market line (historically the close; live, the capture-time line of the offer being priced) | nothing |
| C1 | `line + k·(F1 − line)` | k by OLS through the origin of (actual − line) on (F1 − line) |

## Distribution (shared by all configs)

- Margin and total are separate discrete distributions over integers. The margin
  excludes 0 for regulation-decided games only through the data: ties are
  modelled as observed.
- Each distribution is a discretised Normal(center, σ_config) multiplied by
  integer key-number weights `w_k`:
  - σ_config is the dev RMSE of that config.
  - `w_k` is fit on dev only, using M0 centers: (empirical count + 1) / (model-expected count + 1), two fixed-point iterations, clipped to [0.2, 5].
  - The same `w` is used for every config.
- Derived values:
  - home win / tie / away win;
  - P(cover / push / lose) at any half or integer line;
  - P(over / push / under);
  - fair spread and fair total: the half-point line closest to equalising both sides' conditional win probability;
  - fair moneyline probability conditional on no tie.
- Known limitation: margin and total are **not** a joint distribution. Same-game correlation is unsupported and is an extension point.

## Metrics

All metrics are computed per partition on identical rows.

1. Point accuracy: MAE and RMSE for margin and total.
2. Probability calibration at the closing spread and total:
   - 3-outcome log loss (win/push/lose), the primary probabilistic metric;
   - binary Brier conditional on no push;
   - 10-bin reliability;
   - predicted vs observed push rate at key numbers (3, 7 margin);
   - home-win log loss excluding ties.
3. Side accuracy vs the close (directional, at equal volume within each config):
   - sides where the conditional cover probability is ≥ 0.5238 (−110 break-even) and ≥ 0.55;
   - hits, misses and pushes reported.
   - A retrospective ROI at the archived **closing** odds, where present, is labelled `RETROSPECTIVE_CLOSING_PRICE_CONTROL`. It is never an operational price claim.
4. Uncertainty: season-week block bootstrap, 2,000 iterations, seed 20261012. It covers config-minus-M0 log loss and hit rates.

## Why-diagnostics (fit/inspected on development only)

- B0 mean residual (missing home-field term) and B0 slope (scale).
- Correlation of B0 with the line, and the dev OLS of actual on [line, B0].
- Subgroups:
  - weeks 1–4 vs 5+;
  - starting-QB change vs the team's previous game. This is *retrospective* starter identity and a leaky diagnostic, never a feature.
  - Subgroups are reported descriptively on every partition. No gate or parameter is fit from validation, held or 2026.
- Information timing (open vs close) **cannot be tested**: the source has no opening or timestamped lines. It is recorded as unknown.

## Prospective research rule (fixed before results)

- The frozen record stores B0, F1, M0 and C1 probabilities for every offered side.
- The research-lean label uses **C1** at the actual captured price. A lean requires all of:
  - identity bound;
  - both sides present (no-vig computable);
  - quote captured ≤ 90 minutes before freeze;
  - freeze strictly before kickoff;
  - the starting QB unchanged from the team's previous game per the schedule source;
  - |C1 center − line| ≤ 6 spread points / 8 total points;
  - EV ≥ +3% per unit staked at the captured price.
- Otherwise the side is `NO_LEAN` or `QUARANTINED` with the exact reason.
- The lean label is a research measurement, never a pick, and never public.
- Gates are not loosened after outcomes.

Alligator

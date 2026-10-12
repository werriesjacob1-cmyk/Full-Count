# FC-NFL-004 — results: NFL spread/total distribution benchmark

Status: RESEARCH ONLY. No model promoted, no pick, nothing public.

- Pre-registration: `PREREG.md` at commit `35a6cb03`, written before any fit.
- Machine-readable report: `benchmark_report.json`.
- Frozen parameters: `nfl/research/game_market_v1_params.json`, canonical params sha256 `c1dbd52e…34fb5`. Fit on 2000–2019 only (5,095 games).

## Deviation D1 (spec defect, found by an invariant check, not by outcomes)

The pre-registration defined the M0 center as `= line`. With key-number weights, the Normal center is not the line's 50/50 point. For example, at −2.5 a heavy mass on 3 makes the favourite "cover" more than half the time. The first run therefore had M0 — a market-only reference — "selecting" 2,577 dev spread sides. That violates the M0 identity: the market cannot disagree with its own line.

The fix: M0 is the distribution *solved* so its line is exactly 50/50 (`solve_center`), and C1 = M0 center + k·(F1 − line). The parameters are unchanged; only the evaluation-time construction changed. After the fix, M0 makes 0 picks on every partition, as it must. The first-run numbers were not used to choose anything.

## Results (3-outcome log loss at the closing line; lower is better)

| Partition (n) | B0 spread / total | F1 spread / total | M0 spread / total | C1 spread / total |
|---|---|---|---|---|
| dev 2000–19 (5,095) | 0.8206 / 0.7852 | 0.8105 / 0.7759 | 0.7801 / 0.7535 | 0.7803 / 0.7536 |
| validation 2020–22 (796) | 0.7839 / 0.7834 | 0.7823 / 0.7715 | 0.7572 / 0.7480 | 0.7580 / 0.7482 |
| held 2023–25 (816)* | 0.7907 / 0.7504 | 0.7878 / 0.7407 | 0.7605 / 0.7195 | 0.7608 / 0.7196 |
| fresh 2026 Wk 1–5 (78) | 0.8676 / 0.7913 | 0.8649 / 0.7828 | 0.8618 / 0.6931 | 0.8631 / 0.6977 |

\*Previously inspected by PR #222.

Paired season-week bootstrap 95% intervals (config − M0 log loss):

| Comparison | validation | held |
|---|---|---|
| F1 spread | [+0.006, +0.046] | [+0.010, +0.046] |
| F1 total | [+0.010, +0.037] | [+0.007, +0.035] |
| C1 spread | [−0.0002, +0.0017] | [−0.0005, +0.0012] |
| C1 total | [−0.0007, +0.0011] | [−0.0007, +0.0010] |

- **Point error, margin MAE.** Validation: M0 9.84, F1 10.35, B0 10.44. Held: 9.79 / 10.40 / 10.49.
- **Calibration.** M0 and C1 non-push Brier ≈ 0.250 everywhere. Push mass at integer lines is predicted at 2.1–2.7% against observed 1.8–2.9%, so the key-number shape is calibrated in aggregate.
- **Side accuracy vs the close** at the −110 break-even threshold:
  - F1: 51.3% validation, 48.9% held (≈680–700 sides per partition).
  - B0: 50.7% validation, 50.0% held.
  - Neither clears 52.4%. The intervals include 50%.
- **Retrospective closing-price ROI** (control only; not an operational price) is negative for every football-only config on validation and held.

## Why PR #222's residual was null (development-only diagnostics)

1. **B0 has a real bias, but bias is not the reason.**
   - B0 omits home field: dev mean (actual − B0) margin = **+2.57 pts**.
   - Its scale is too wide: the F1 slopes are 0.82 for margin and **0.53 for total**, so about half of B0's total variation is noise.
   - F1 fixes both, and is still far worse than the market (+0.025 log loss, with an interval excluding 0).
2. **The market already contains B0's information.**
   - Dev OLS of actual on [close, B0]: the B0 coefficient is **+0.009** for margin and **−0.093** for total; the close coefficient is 1.05 / 0.99.
   - corr(B0, close) = 0.74 / 0.69.
   - The fitted market-anchored weights are k = −0.049 (margin) and +0.065 (total), i.e. ≈ 0. C1 collapses onto M0.
3. **The pre-declared situations do not rescue it.**
   - Football configs trail M0 in every partition, in weeks 1–4 and 5+, and in QB-stable and QB-change games.
   - QB-change games make B0 *worse* relative to the market: dev spread log loss +0.053 vs +0.037. B0 cannot see a starter change; the market can.
   - This supports the live `QB_CHANGE_UNMODELLED` quarantine gate. It is not a source of edge.
4. **The closing line may be too strong a comparator for an earlier decision window.** This **cannot be tested** from this source, which has no timestamped or opening lines.
5. **Timing.** The live pathway now freezes multiple pre-kickoff vintages, and the grader measures closing-line value (CLV) against the latest later vintage. This is the first legitimate test of the "earlier timing" hypothesis, and it starts now. It is prospective only.

## Fresh 2026 Weeks 1–5 (virgin, n=78) — read with caution

- F1 sides went 34-24 against the close on spreads, but 21-45 on totals.
- M0 has the best total log loss.
- At n=78 this is noise-scale and contradicts validation and held. It is recorded, not acted on.

## Negative results preserved

- Football-only F1, a properly recalibrated strictly-prior scoring model, is decisively worse than the closing market on probability and point metrics in every partition.
- A market-anchored combination adds nothing measurable.
- The game-market B0 family should not be pursued further as an edge source against close. Its role is a control and a gate input.

## What remains unknown

- Opening/early-line efficiency, and the size of CLV obtainable at capture time. This is now being measured prospectively.
- Whether independent point-in-time information carries signal the market misses at capture time: confirmed inactives, QB/OL availability, weather changes.
- Cross-book price dispersion: only FanDuel is captured, so M0 = one book's line and its no-vig EV is always ≈ −vig.

Alligator

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
5. **Timing.** The live pathway now freezes multiple pre-kickoff vintages, and the grader measures **later pre-kickoff price movement** against the latest later research vintage (`reference_basis = LATEST_LATER_PRE_KICKOFF_RESEARCH_VINTAGE_NOT_A_VERIFIED_CLOSE`). That is **not** closing-line value: no verified sportsbook closing quote is captured. This is a first, prospective-only probe of the "earlier timing" hypothesis.

## Fresh 2026 Weeks 1–5 (virgin, n=78) — read with caution

- F1 sides went 34-24 against the close on spreads, but 21-45 on totals.
- M0 has the best total log loss.
- At n=78 this is noise-scale and contradicts validation and held. It is recorded, not acted on.

## Negative results preserved

- Football-only F1, a properly recalibrated strictly-prior scoring model, is decisively worse than the closing market on probability and point metrics in every partition.
- A market-anchored combination adds nothing measurable.
- The game-market B0 family should not be pursued further as an edge source against close. Its role is a control and a gate input.

## What remains unknown

- Opening/early-line efficiency, and the size of later pre-kickoff price movement at capture time. This is now being measured prospectively. True closing-line value remains unmeasured until a verified near-kickoff closing quote is captured.
- Whether independent point-in-time information carries signal the market misses at capture time: confirmed inactives, QB/OL availability, weather changes.
- Cross-book price dispersion: only FanDuel is captured, so M0 = one book's line and its no-vig EV is always ≈ −vig.

Alligator

---

## Amendment A1 (2026-10-12, after Codex review 6129120014): D1-consistent fit

**Classification: AMENDED_EXPLORATORY_POST_D1.** This is not a pre-registered test:
- the amendment followed D1;
- validation and held were viewed before it;
- held 2023–2025 was also inspected by PR #222;
- 2026 Weeks 1–5 had also been viewed once.

Nothing here is promotion evidence.

**Defect (Codex P1).** `fit_params` estimated M0 sigma, the key weights and C1's k around the *raw line*, while evaluation used the *solved* M0 center.

**Fix.** The `v1_1_d1_solved_m0` procedure fits on development 2000–2019 only:
- It runs 3 pre-declared outer fixed-point iterations: solve the centers, refit sigma, refit the key weights.
- It then fits C1's k on the final centers, which are exactly the evaluation centers.
- Its normal equation is unit-tested against `market_center`.

**Original params preserved.** The original procedure still reproduces `game_market_v1_params.json` byte-for-byte (sha `c1dbd52e…`). The 2026-10-12T17:01Z freezes remain bound to it.

- Amended params: `game_market_v1_1_params.json`, sha `acc53f4d…`.
- Amended report: `benchmark_report_amended_d1.json`.

| Parameter | Original (line-centered) | Amended (D1) |
|---|---|---|
| C1 k margin / total | −0.0489 / +0.0649 | **+0.0677 / +0.1308** |
| M0 sigma margin / total | 13.356 / 13.494 | 13.371 / 13.525 |
| Key weight at 3 / 7 | 3.10 / 1.96 | 3.19 / 1.97 |

**Convergence (limitation).** Max / mean center shift by iteration:

| Iteration | Max shift (pts) | Mean shift (pts) |
|---|---|---|
| 1 | 2.45 | 0.51 |
| 2 | 0.61 | 0.19 |
| 3 | 0.41 | 0.17 |

The joint fixed point is not converged at the declared count. Centers and key weights are only weakly separately identified. No extra iterations were run after seeing this, to avoid tuning.

**3-outcome log loss at the close, before → after**

| Partition | M0 spread | C1 spread | M0 total | C1 total | F1 spread | F1 total |
|---|---|---|---|---|---|---|
| dev | .7801 → .7801 | .7803 → .7802 | .7535 → .7535 | .7536 → .7539 | .8105 → .8127 | .7759 → .7769 |
| validation | .7572 → .7574 | .7580 → .7567 | .7480 → .7480 | .7482 → .7486 | .7823 → .7824 | .7715 → .7721 |
| held* | .7605 → .7604 | .7608 → .7602 | .7195 → .7195 | .7196 → .7199 | .7878 → .7893 | .7407 → .7412 |
| 2026 Wk 1–5 | .8618 → .8605 | .8631 → .8590 | .6931 → .6931 | .6977 → .7024 | .8649 → .8587 | .7828 → .7834 |

\*Previously inspected.

**Amended C1 − M0 bootstrap intervals** (season-week, 2,000 draws): all span 0.

| Market | Validation | Held |
|---|---|---|
| Spread | [−.0020, +.0006] | [−.0014, +.0009] |
| Total | [−.0012, +.0024] | [−.0013, +.0021] |

**F1 − M0 intervals** still exclude 0: validation spread [+.0047, +.0471], held spread [+.0104, +.0472].

**Amended C1 sides** at the −110 break-even (hits-misses, against the close):

| Market | dev | validation | held | 2026 |
|---|---|---|---|---|
| Spread | 10-18 | 9-1 | 4-4 | — |
| Total | 203-197-6 | 42-43 | 25-30-1 | 2-5 |

These are small, unstable and below break-even overall.

**Conclusion unchanged.** Football-only F1 is decisively worse than the market. Market-anchored C1 is statistically indistinguishable from M0. The B0-family negative result stands and is strengthened by the consistent fit.

Alligator

---

## Erratum E1 (2026-10-12, Codex review 6129802534)

- **Wording correction.** Items 5 and "What remains unknown" previously said "closing-line value (CLV)". Corrected to *later pre-kickoff price movement*. The grader's reference is the latest later research vintage, explicitly `NOT_A_VERIFIED_CLOSE`.
- **Finite-fit limitation, stated explicitly.**
  - The amended D1 parameters (`acc53f4d…`) are a **finite three-iteration exploratory estimator, not a converged calibration**.
  - Codex independently ran a fourth iteration. Mean M0 centers shifted 0.237 pts (margin) and 0.070 (total). Validation/held M0 and C1 log loss changed by < 0.000035, and the negative/inconclusive comparison did not reverse.
  - Nothing was refit or retuned in response. The classification stays **AMENDED_EXPLORATORY_POST_D1**: validation/held were viewed earlier and held was inspected by #222. It is not promotion evidence.

Alligator

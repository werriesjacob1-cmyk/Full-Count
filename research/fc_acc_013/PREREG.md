# FC-ACC-013 — pitcher workload-distribution experiment: frozen design

**Status:** EXPLORATORY, OFFLINE, OUTCOME-ONLY.
- This design was frozen and committed before any evaluation code read an outcome.
- It is not a betting strategy and not a production change.
- 2025 and 2026 are **not** untouched holdouts: earlier FULL COUNT research (FC-MLB-002..006, FC-ACC-004) inspected them.

## 1. Frozen production identity (main `5e6deaee19513a81eb1b46279f58981b76077165`, 2026-10-22)

| Artifact | sha256 |
|---|---|
| `prop_probability.py` | `eafc90ac9ba3dcc14e56cc2222019b9ccf28c67b6c0fcc84df84770a75d097bb` |
| `generate_picks.py` | `d8749d38f92ead4426d43462d2e2321388fac38225314ecb28962e469fba6556` |
| `mlb_sources.py` | `a79f517b8f7d061afe129b50f3864e7ea4093e0b7cbcb81d26e6c829c238754f` |
| `backtest/calibration.py` | `45f578d6e84a0128e9b342393b780cb847cc02d42d562e73fb8d8b0c8dd78aa9` |
| `backtest/calibrators_by_market.json` | `2f15846e40afa0986c86175c08bd86b642ef8d059658e79a222def725d96af3d` |

The calibrator file was last promoted on 2026-10-12 (`34382f8af2`). Its strikeouts curve is `sigmoid(1.374592801173212·p − 0.5084361336087595)`.
- It was fitted on rows dated **2026-09-05..09-22**, so it is an already-fitted artifact that saw 2026 outcomes.
- Applying it to 2026 is therefore partly in-sample, and is reported as such.

**Production strikeout probability path:**
1. **K rate.** `mlb_sources.exp_weighted_pitcher_k_rate` (L1809): K/BF over this season's starts, weights `0.5^(age_days/30)`, requiring ≥3 starts and ≥40 BF. Otherwise it falls back to the L14 Statcast K% (≥15 PA), then season K%, then 22.5%.
2. **Expected BF.** `generate_picks.project_pitcher_workload` (L1317): `n/(n+2.62)·mean BF of starts in the prior 14 days + 2.62/(n+2.62)·23.1`, clamped to [12, 30]; 23.1 when n = 0.
3. **Modelled probability.** `prop_probability.p_at_least_strikeouts` → `_binom_at_least(E[BF], k, t)`. A fractional E[BF] is handled as the 2-point mixture `_mix_fractional`.
4. **League shrink.** `p = 0.5·league_t + 0.5·modelled` (`STRIKEOUT_SHRINK_K`, L6393). `league_t` = the share of league starts with ≥ t K, over starts with BF ≥ 15 in a trailing 30-day window (`league_base_rates`, L1883).
5. **Calibration.** Per-market Platt `sigmoid(A·p+B)`, applied only where p falls in a supported 0.05 bin (strikeouts supported: [0.55, 0.75)). Otherwise p is unchanged.

## 2. Data

- The FC-ACC-004 source, re-retrieved with **identical endpoints and file names**: public MLB StatsAPI season pitching leaderboards and per-pitcher regular-season `gameLog` pitching splits for 2024–2026.
- Every raw file's sha256 is compared with FC-ACC-004's committed manifest (`codex/fc-acc-004-offline-20261016` @ `86a09fc094`, `research/fc_acc_004/report.json`). Matches and mismatches are reported, never assumed.
- **Start filter:** `gamesStarted == 1`, `gameType == "R"`, valid integer BF/outs/K/pitches.
- **Same eligibility as FC-ACC-004:**
  - ≥3 strictly-prior same-season starts;
  - both games of a same-day multi-game pitcher record are excluded;
  - prior means strictly earlier calendar dates;
  - no target start is removed for its own outcome.
- **Information set:** strictly prior dates only, for every feature, league rate and dispersion pool.

## 3. Baseline A (production replica)

- **k:** `exp_weighted` over strictly-prior same-season starts (age measured in days to the target date), requiring ≥3 starts and ≥40 BF. Fallback: season-to-date K/BF; then 0.225.
  - Statcast L14 PA are not available in game logs. The fallback order is therefore season-to-date, and its use is counted. It is the same k for both arms.
- **E[BF]:** exactly `project_pitcher_workload` on strictly-prior starts within 1..14 days.
- **League rate:** `league_t` from strictly-prior league starts with BF ≥ 15 in the trailing 30 days. If fewer than 200 such starts exist, it uses all strictly-prior same-season starts with BF ≥ 15.
- **A_t:** `0.5·league_t + 0.5·_binom_at_least(E[BF], k, t)`, using production's own `_binom_at_least`, imported from `prop_probability.py` at the frozen sha.

## 4. Challenger B (the only challenger)

B is identical to A except that the point mass at E[BF] is replaced by a **pitcher-specific distribution of BF deviations with the same mean**:
- **Pitcher deviations:** the pitcher's last up to 10 strictly-prior same-season starts, `d_j = BF_j − mean(those BF)`, each weighted `1/(n+5)`.
- **League deviation pool**, total weight `5/(n+5)`: the pooled empirical distribution of `BF − that pitcher's strictly-prior same-season mean BF`, over every league start that itself had ≥3 prior same-season starts, strictly before the target date. It is recomputed per date and binned to integers.
- **Mean preservation:** the combined weighted deviations are re-centred to weighted mean exactly 0. Draws are `E[BF] + d`, clamped to [1, 45]. Any mean shift caused by the clamp is reported.
- **Probability:** `B_t = 0.5·league_t + 0.5·Σ w·_binom_at_least(E[BF]+d, k, t)`.
- **Frozen constants:** N = 10, M = 5, clamp [1, 45]. Nothing is tuned, and no second challenger will be fitted.

Because the mean is preserved, any A/B difference comes from **workload dispersion only**. A secondary, separately labelled diagnostic reports the realized BF mean vs E[BF] by season (mean bias); it is not used to modify B.

## 5. Calibration treatments (identical for both arms)

- **C0.** None (the shrunk probability, as production has it before calibration).
- **C1.** The frozen production strikeouts Platt plus the support gate, identical parameters for A and B. On 2026 it is partly in-sample (see §1).
- **C2.** A per-arm Platt fit with the identical procedure (`backtest/calibration._fit_platt`), pooled over t = 4, 5, 6 as production pools by market. It is fit on 2024 only and applied unchanged to 2025 and 2026.

## 6. Evaluation (fixed)

- **Thresholds:** t = 4, 5, 6.
- **Seasons:** 2024 = development (C2 fit only; metrics reported). 2025 and 2026 are reported separately.
- **Metrics:** Brier, log loss (p clipped to [1e-6, 1−1e-6]), and paired differences B − A.
  - 95% CIs from 2,000 fixed-seed (20261022) cluster bootstraps, clustering by **pitcher** and separately by **game**.
  - By month and by season.
- **Classification at p ≥ 0.5:** counts correctly and incorrectly predicted; cases where the arms disagree, with outcomes.
- **Probability change (B − A):** by E[BF] bin, by the pitcher's prior BF SD tercile, by threshold.
- **Coverage:** k fallback use, L14-empty rows, pitcher deviation n distribution, clamp hits.
- **Post-hoc explanation only:** outcomes stratified by the target start's actual outs (< 12 vs ≥ 12). Never an input.

## 7. Selection relevance (fixed diagnostic only)

- At fixed probability floors of 0.55, 0.60 and 0.65 (under C0 and C1): over-decisions added and removed by B vs A, with actual outcomes. Removing losers is **not** reported as equal-volume improvement.
- **Matched-volume winner lift and ROI: NOT MEASURABLE.** There are no authentic historical offers or replacement candidates for 2024–2025.
- The 2026 regular-season starts that coincide with settled public strikeout Top Picks (which carry authentic captured odds) are reported descriptively only.

## 8. Decision rule (fixed)

- **CONTINUE** (to prospective matched-volume validation, not promotion): on 2025, B improves both Brier and log loss with **pitcher-clustered 95% CIs excluding 0** at ≥2 of 3 thresholds; the same sign holds on 2026; and the improvement survives C1 and C2.
- **MODIFY:** consistent improvement, but the CIs include 0, or it does not survive calibration.
- **STOP:** B is neutral or harmful on 2025.

## 9. Pitcher outs

Assessed only after the strikeout result. No recency-weighted or otherwise modified outs model is fitted in this task.

# FC-ACC-014 — market-specific algorithm tournament (2026-10-22)

**Verdicts (frozen rule, `PREREG.md` §7, design committed `d2185cb4` and runner committed `ff03d6a8` before the single evaluation run):**

| Market | Candidate (chosen on fold-1 log loss) | Log loss beats calibration control, both folds | Equal-N extra winners, both folds | Verdict |
|---|---|---|---|---|
| Batter 1+ hits | M2 GLM | yes | yes | **GO** |
| Batter 2+ total bases | M2 GLM | yes | yes | **GO** |
| Pitcher 4+/5+/6+ K | M2 GLM | yes at 3 of 3 | established at 4+ only | **MODIFY** |

**What GO means here:** only *eligible for prospective matched-volume validation against authentic offers*.
- Not promotion, not deployment, not a betting result.
- Every result is a **model/selection diagnostic**: no authentic historical offers exist, so **ROI and price-adjusted edge are NOT MEASURABLE**.
- The features that carry the gain (batting slot, season skill, opponent quality, park) are information the sportsbook also prices. Earlier audits on Issue #91 found the market's probabilities sharper than the model's on published picks, so a probability gain does **not** imply a price edge.

## 1. Data and production fidelity

- **Canonical-v2 research view** (artifact 9776400325, zip `00f04cc4…`, rows `8ca01064…`, manifest `42097deb…`, generation code `bbbe4683`, "CANONICAL CERTIFIED").
  - Universes: 1+ hits 121,554 rows; 2+ TB 121,566 rows; 555 dates, 2024-04-01 → 2026-08-25.
  - `predicted_prob` is production's raw (pre-calibration) probability, replayed point-in-time by `backtest/engine.py`.
  - `prop_probability.py` and `mlb_sources.py` are unchanged from `bbbe4683` to main `99372287`.
  - Disclosed engine gaps: no implied team totals, wRC+, Stuff+ or umpire; constant `cat_environment`; no empirical blend.
- **StatsAPI, retrieved 2026-10-22** (`out/data_manifest.json`, every raw sha256):
  - 203,596 batter game splits; 19,434 starts (2024–26 starts 4,856 / 4,860 / 4,858, identical to FC-ACC-004/013); 9,720 games; 1,738 people.
  - Lineups, probable pitchers and weather cover over 99.9% of games.
- **Strikeout universe:** FC-ACC-013's construction (vendored `fc013_build.py`, sha `3bcdc9aa…` before a one-line `draws` addition). 3,883 / 3,907 / 3,891 eligible starts. Baseline = FC-ACC-013 production replica A.
- **Features:** strictly prior via prefix sums with `bisect_left` on the target date, so no same-date or later game enters any count (tested).
- **Identity/context fields** taken from schedule or target-game identity (disclosed):
  - starting lineups and batting slot (the confirmed lineup production uses);
  - the schedule's "probable" pitcher for past games (in practice the actual starter, a mild hindsight risk);
  - game-time weather;
  - team and home/away.
- **Exposure:** 2025/26 hits were used earlier (canonical hits-PA locked study, FC-MLB-002..006); pitchers 2024–26 by FC-ACC-004/013. 2+ TB is the least exposed. **Nothing here is a confirmatory holdout.**

## 2. Models (fixed hyperparameters)

- **M0:** production raw.
- **M1:** Platt-style recalibration of M0. Ranking is identical to production.
- **M2:** ridge logistic (λ = 1) on BASE plus all feature groups.
- **M3:** market-specific distribution model.
  - Hits: slot-specific empirical PA distribution × log5(batter, opposing SP, league) × park.
  - Total bases: per-PA 0/1B/2B/3B/HR vector, adjusted for SP and park, convolved over the PA distribution.
  - Strikeouts: log5(pitcher K/BF, opposing lineup K/PA, league) × binomial over the FC-ACC-013 BF mixture.
  - Each is then Platt-calibrated on training data.
- **M4:** HistGradientBoosting with fixed parameters (the one nonlinear family).
- **Folds:** fold 1 trains on 2024 and tests 2025; fold 2 trains on 2024–25 and tests 2026. M3 coverage is 100%.

## 3. Results (test folds; Δ = model − M1; 95% date-clustered CI; player-clustered CIs agree)

| Market | Fold | N test | M1 log loss / AUC | M2 log loss / AUC | Δ log loss M2 [CI] | M3 Δ | M4 Δ | M0 vs M1 |
|---|---|---|---|---|---|---|---|---|
| Hits 1+ | 2025 | 43,476 | .66608 / .547 | .66203 / .569 | −.00405 [−.00535, −.00280] | −.00306 | −.00169 | +.00174 |
| Hits 1+ | 2026 | 35,542 | .66899 / .540 | .66408 / .567 | −.00492 [−.00613, −.00375] | −.00280 | −.00414 | +.00280 |
| TB 2+ | 2025 | 43,480 | .64799 / .549 | .64316 / .575 | −.00482 [−.00627, −.00341] | −.00220 | −.00178 | +.00105 |
| TB 2+ | 2026 | 35,545 | .64472 / .545 | .63968 / .573 | −.00504 [−.00632, −.00377] | −.00256 | −.00414 | +.00166 |
| K 4+ | 2025 | 3,907 | .58854 / .645 | .57479 / .670 | −.01376 [−.0222, −.0060] | −.00664 | **+.02099** | +.00081 |
| K 4+ | 2026 | 3,891 | .59288 / .642 | .56360 / .695 | −.02928 [−.0371, −.0214] | −.00672 | −.00879 | +.00252 |
| K 5+ | 2025 | 3,907 | .65517 / .653 | .63032 / .691 | −.02485 [−.0322, −.0167] | −.00933 | +.00370 | +.00054 |
| K 5+ | 2026 | 3,891 | .65731 / .650 | .63022 / .694 | −.02709 [−.0360, −.0189] | −.00601 | −.00877 | +.00163 |
| K 6+ | 2025 | 3,907 | .62094 / .670 | .59608 / .708 | −.02487 [−.0323, −.0172] | −.00813 | +.00928 | +.00198 |
| K 6+ | 2026 | 3,891 | .62038 / .668 | .59940 / .703 | −.02098 [−.0286, −.0128] | −.00611 | −.00638 | +.00227 |

- **Brier** has the same signs everywhere (`report.json`).
- **Calibration:** raw production (M0) is miscalibrated in every market. For hits, calibration-in-the-large is +0.020 / +0.025 (under-predicts) with a logit slope of 0.59 / 0.51 (over-dispersed). M1 fixes only that.
- **M2 calibration:** ECE .015 / .008 (hits) and .013 / .005 (TB). Slopes are 0.81–0.89 for batters and 0.84–1.01 for K.
- **Month stability:** M2 − M1 is negative in every month of both folds for every market, except one 18-row hits cell (2025-11).
- **Family ranking:** the GLM wins every market. The distribution model (M3) is the best-calibrated (slope 0.91–1.04) but discriminates less. Gradient boosting is unstable on the small strikeout training set (worse than M1 at all three 2025 thresholds).

## 4. Equal-N decisions (same per-date count; baseline = production order)

Per date the top 5% of candidates (hits, TB) or top 10% of starts (K) were taken. Hit rates are wins over selected.

| Market | Fold | Selected per arm | Production wins (rate) | M2 wins (rate) | Added (wins) | Removed (wins) | Net [95% date CI] |
|---|---|---|---|---|---|---|---|
| Hits 1+ | 2025 | 2,224 | 1,474 (66.3%) | 1,600 (71.9%) | 1,741 (1,255) | 1,741 (1,129) | **+126 [+72, +177]** |
| Hits 1+ | 2026 | 1,818 | 1,212 (66.7%) | 1,267 (69.7%) | 1,363 (946) | 1,363 (891) | **+55 [+5, +102]** |
| TB 2+ | 2025 | 2,222 | 923 (41.5%) | 1,022 (46.0%) | 1,717 (779) | 1,717 (680) | **+99 [+44, +158]** |
| TB 2+ | 2026 | 1,819 | 699 (38.4%) | 844 (46.4%) | 1,392 (646) | 1,392 (501) | **+145 [+91, +197]** |
| K 4+ | 2025 / 2026 | 402 / 394 | 331 / 330 | 368 / 346 | 187 (173) / 189 (162) | 187 (136) / 189 (146) | +37 [+25, +50] / **+16 [+1, +32]** |
| K 5+ | 2025 / 2026 | 402 / 394 | 282 / 293 | 331 / 310 | 190 (162) / 192 (144) | 190 (113) / 192 (127) | +49 [+35, +64] / +17 [−1, +35] |
| K 6+ | 2025 / 2026 | 402 / 394 | 244 / 249 | 278 / 264 | 192 (127) / 185 (109) | 192 (93) / 185 (94) | +34 [+16, +52] / +15 [−2, +33] |

- Volume is preserved by construction: identical counts per date. These are **diagnostic top-N selections without prices**; they are not the production publication policy.
- The labelled, not-equal-volume p ≥ .60 hits floor: M2 takes fewer (27,904 vs 29,803 in 2025) at 64.6% vs 63.2%. It is reported only for context.

## 5. What carries the information (GLM ablations; Δ log loss vs M1 when added / vs M2 when dropped)

- **Hits and total bases:**
  - **OPP** (slot, home, team runs per game) is the largest addition (−.0026 to −.0029, both folds). Then **SKILL** (season-to-date and prior-season rates, −.0016 to −.0023) and **PARK_WX** (−.0008 to −.0017).
  - MATCH (opposing SP, platoon) adds −.0008 to −.0010.
  - CONTACT (up to −.0014 for TB 2025), LINEUP and RECENCY add small amounts that are mostly redundant given the rest (dropping them from M2 costs about 0).
  - CONTEXT adds nothing.
  - **BASE adds nothing** once the features are present: production's probability is a strict subset of this information.
- **Strikeouts:**
  - **SKILL** (−.008 to −.011): the GLM re-weights the K rate that production shrinks 50% to league.
  - **MATCH** (opponent lineup K%, −.005 to −.012): production uses it only in the 0–100 score.
  - **WORKLOAD** (−.008 to −.012 in fold 2; ≈0 for 4+ and −.003 for 5+/6+ in fold 1).
  - PARK_WX small (−.002 to −.004 in fold 2, none in fold 1); RECENCY adds nothing.
  - Post-hoc only: for early-exit starts M2 lowers 4+ probability from ≈.68 to ≈.61 (realized .29 / .26). It partly sees workload risk, but remains badly over-confident there.
- **Largest standardized weights (fold 2):**
  - Hits: season TB/PA (+0.44); season hits, HR and XBH rates (negative, collinear with TB/PA); slot (−0.15); park hit factor (+0.08).
  - K 4+: season K/BF (+0.41), lineup K% (+0.37), prior-season K/BF (+0.27), mean prior BF (+0.22).
  - Collinear groups mean individual signs should not be over-read.

## 6. Do 1+ hits and 2+ TB need different weights or families?

- **Weights: yes, modestly.**
  - Each market's own M2 was compared with a model restricted to the *other* market's fitted feature direction (intercept and scale refit).
  - Own weights won in 3 of 4 tests: hits fold 2 −.00112 [−.00172, −.00054]; TB fold 1 −.00100 [−.00166, −.00036]; TB fold 2 −.00064 [−.00123, −.00004]. Hits fold 1 was not significant (−.00018).
  - TB's largest fold-2 weights after season TB/PA (+0.20) and slot (−0.15) are opposing-SP TB allowed (+0.08), opposing-SP K (−0.07) and season barrel% (+0.05).
- **Family: no difference.** The GLM is best in both markets, and the same family order holds (M2 > M3 > M4 in 2025; M2 > M4 > M3 in 2026).

## 7. Limitations

- No authentic historical offers: ROI and price edge are NOT MEASURABLE, and the comparison with market information is not possible on these rows.
- Previously exposed seasons; one canonical code regime (`bbbe4683`).
- Identity fields from the schedule (lineup, probable pitcher, weather); the probable pitcher approximates the pregame announcement.
- Strikeout universe: FC-ACC-004 eligibility (≥3 prior starts) includes openers. The top-10% selections may favour pitchers the book prices heavily.
- Fixed λ, so the GLM is not tuned, but the candidate choice used fold-1 outcomes (disclosed).

## 8. Next step (requires authorization; not done here)

A preregistered **prospective, matched-volume shadow** of the frozen M2 coefficients for hits 1+ and TB 2+ (and K 4+ only), refit once on 2024–25, compared head-to-head with production order and a market-only arm, at identical per-slate counts from the same eligible pool, using authentic captured offers. Primary outcome: additional settled winners and ROI at captured prices.
- It depends on the inactive FC-ACC-001 S1 capture path (external anchor still missing) or Codex's market-coverage work.
- **For strikeouts:** MODIFY. Revalidate 5+ and 6+ on more starts, and check opener exclusion before any prospective test.

## Reproduce

```bash
python3 research/fc_acc_014/data.py                       # StatsAPI, cached in raw/
python3 research/fc_acc_014/features.py <canonical rows.jsonl>
python3 research/fc_acc_014/tournament.py                 # out/report.json
python3 research/fc_acc_014/test_fc_acc_014.py
```

Alligator

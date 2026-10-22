# FC-ACC-015 — adversarial validation of FC-ACC-014: frozen audit plan

**Status:** research only. This plan was committed **before** any sensitivity variant below was fitted or scored on an outcome.

**Prior knowledge (disclosed):**
- The auditor also built FC-ACC-014 and has seen all of its results.
- The 2024–2026 seasons are previously exposed.
- Nothing here is independent confirmation; every result is a robustness diagnostic.

**Fixed for every variant:** the FC-ACC-014 frozen experiment is reused unchanged and imported read-only from `research/fc_acc_014` at `c3f3362d`:
- M2 = the same L2 logistic (λ = 1, Newton-IRLS) with the same `Design`;
- same folds (2024→2025, 2024–25→2026), same M1 baseline;
- equal-N fractions 5% (batters) and 10% (strikeouts);
- bootstrap of 2,000 reps, seed 20261022.
- No hyperparameter, λ, fold or feature transformation is tuned. Variants only **remove** inputs, except V4, which substitutes one strictly-prior input.

## 1. Identity and reproduction (gate)

- **Pins:**
  - PR #233 head `c3f3362d67da823ee492ad361017e1e2a4a065a9`, design `d2185cb4`, runner `ff03d6a8`;
  - artifact 9776400325, zip `00f04cc4…`, rows `8ca01064…`;
  - source-file and feature-file sha256.
- **Reproduction:** from a fresh StatsAPI retrieval and the frozen code, rebuild the features and report. They must match the committed shas, or the audit stops and reports the difference.

## 2. Leakage audit

- **Matrix:** each M2 input is classified as
  - **A** strictly pregame,
  - **B** plausibly pregame but without timestamp evidence,
  - **C** retrospectively reconstructed with hindsight risk,
  - **D** confirmed leakage.
- **Empirical checks:**
  - **Outcome-scramble invariance:** for 6 fixed target dates (2025-05-15, 2025-07-20, 2025-09-10, 2026-04-25, 2026-06-15, 2026-08-10), randomise every outcome statistic of every batter game and pitcher start dated on or after the target date, rerun the frozen feature builder, and require every target-date feature (and M3 ingredient) to be **bit-identical**. Any change is a **D**.
  - **Probable-starter hindsight:** agreement of the schedule's `probablePitcher` with the actual starter. Near-total agreement means the field reflects post-game resolution, so it is classed **C**.
  - **Lineup provenance:** how the StatsAPI starting lineup relates to batters with plate appearances, and the slot-source mix (canonical signal vs schedule fallback vs missing).

## 3. Sensitivity variants (challenger = M2 refit on the reduced input set)

**Batter markets (hits 1+, TB 2+):**
- **V0 ORIGINAL:** reproduces FC-ACC-014 M2.
- **V1 NO_WX:** drop `wx_temp, wx_wind_mph, wind_out, wind_in, roof` (game-time observation).
- **V2 NO_SP_IDENTITY:** drop the inputs whose value depends on the identity of the opposing starter: `opp_sp_*`, `match_same_hand`, `sig_platoon`, `sig_sp_era_weak`, `sig_pitch_exploit`.
- **V3 NO_SLOT:** drop `opp_slot`, `sig_woba_behind`, `sig_woba_ahead` (they depend on the retrospective lineup).
- **V4 PRIOR_SLOT:** V3 plus `prior_slot`, the mean of the player's last ≤10 lineup slots strictly before the date (schedule lineups; missing if none). This simulates an unconfirmed lineup.
- **V5 SAMEDAY_STRICT:** V1 ∪ V2 ∪ V3 removed.
- **V6 SAMEDAY_STRICT_NO_BASE:** V5 also without `base_logit`. The challenger then carries no same-day hindsight input at all, while the M1 baseline keeps all of its own.
- **V7 FIDELITY:** drop the run-environment proxies that a live implied team total would partly carry (`opp_team_rpg, lin_team_hit, opp_home, park_*, wx_*`). Production's live path has implied totals; the replay does not.

**Strikeouts (4+/5+/6+):**
- **K0 ORIGINAL**;
- **K1 NO_WX:** drop `wx_temp, wx_roof`;
- **K2 NO_OPP_LINEUP:** drop `match_lineup_k, match_lineup_n, match_same_hand`;
- **K3 SAMEDAY_STRICT:** K1 ∪ K2;
- **K4 SAMEDAY_STRICT_NO_BASE:** K3 without `base_logit{t}`.
- The starter universe itself (actual starters) cannot be removed; this is disclosed.

**Outputs per variant and fold:**
- Δ log loss and Brier vs M1 (date-clustered CI), AUC;
- equal-N at the frozen fraction: added/removed/net, date- and player-clustered CI;
- retained share of the V0 net.

## 4. Equal-volume selection audit (applied to V0 and the forward candidate)

- **Population checks:** candidate population, ungraded exclusions, duplicated (date, player) and (game, player) opportunities, and identical per-date counts per arm (asserted).
- **Ties in the baseline order** (`predicted_prob` has 4 decimals):
  - the count of boundary ties;
  - a **tie-averaged** baseline (expected wins with the boundary tie block allocated fractionally);
  - the net under 200 random tie-breaks (seed 20261022).
- **Selection depth (diagnostic, not a decision rule):** fixed top-k per date for k = 1, 2, 3 (batters) and k = 1, 2 (strikeouts).
- **Floor-matched equal-N (hits, K):** per date, N_d = the number of rows with M1 ≥ 0.60, and the challenger takes its own top N_d. This is the closest count analogue to the uncapped production floor, still without the price, evidence-grade or lineup gates.

## 5. Production-fidelity audit

- **Baseline:** the M0 provenance (`predicted_prob` = canonical replay `raw_hit_probability`, which includes production's empirical blend at the canonical code regime), the calibrator, ordering, and replay gaps.
- **K baseline:** the FC-ACC-013 replica, compared against the production strikeout formula.
- **Decomposition:** probability (log loss) improvement is reported separately from selector (equal-N ranking) improvement.

## 6. Test reconciliation

The 5 failing FC-ACC-014 test files are run on main `99372287` (FC-ACC-014's base), on `c3f3362d`, and on current main, then compared against the GitHub Actions log of PR #233.

## 7. Frozen verdict rules

**Batter market:**
- **GO (forward test of the V1 contract)** requires all of:
  - **V1** meets the FC-ACC-014 GO rule: Δ log loss CI excludes 0 in both folds, equal-N net > 0 in both folds, and the fold-2 date CI excludes 0;
  - **V5** meets the log-loss rule in both folds and has equal-N net > 0 in both folds;
  - V1's tie-averaged net > 0 in both folds;
  - no **D** leakage.
- **MODIFY:** V1 meets GO but V5 fails (the gain is contingent on hindsight-risk inputs and needs decision-time capture first), or the log-loss rule holds without equal-N wins.
- **STOP:** V1 fails the log-loss rule, or a **D** leakage affects the market.

**Strikeouts:**
- remain **MODIFY** unless K3 meets the GO rule at ≥ 2 of 3 thresholds;
- **STOP** if K3 fails the log-loss rule at ≥ 2 thresholds.

No variant result may be used to refit, reselect or tune the forward challenger. The forward contract is fixed by §7 and the availability matrix, not by which variant scores best.

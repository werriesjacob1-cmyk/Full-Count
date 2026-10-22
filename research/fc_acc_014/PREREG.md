# FC-ACC-014 — market-specific algorithm tournament: frozen design

**Status:**
- Research only, retrospective and exploratory.
- Frozen and committed **before** any model in this task was fitted or scored on an outcome.
- Results are model and selection diagnostics, **never betting ROI**: no authentic historical offers exist for these rows.

**Markets:** batter 1+ hits, batter 2+ total bases, pitcher 4+ / 5+ / 6+ strikeouts.

**Ownership:** FC-ACC-012 (H+R+RBI, Codex) and FC-ACC-013 (STOP) are not iterated. Codex's FC-MLB-MARKET-COVERAGE is a read-only audit; there is no overlap.

## 1. Data identity

| Source | Identity | Use |
|---|---|---|
| Canonical-v2 research view, Actions artifact 9776400325 (run 33439627978, branch `superchad/canonical-v2-provenance-runner-01` @ `40d933ca78`) | zip sha256 `00f04cc4…b66c`; `rows.jsonl` sha256 `8ca01064…cb34` (1,186,300 rows); manifest `42097deb…0f55`; generation code `bbbe4683a2f3`; "CANONICAL CERTIFIED", 9 quarantined dates | Batter universes, `predicted_prob` (the production raw, pre-calibration probability replayed point-in-time), production signals, `lineup_slot`, outcome |
| MLB StatsAPI, retrieved 2026-10-22 | per-file sha256 manifest committed in `out/data_manifest.json` | Strictly-prior features: batter game logs 2023–2026, pitcher game logs 2023–2026, schedule (lineups, probable pitchers, venue, weather) 2023–2026, handedness |
| FC-ACC-013 starter population | same construction as FC-ACC-013 / FC-ACC-004 (3,883 / 3,907 / 3,891 eligible starts) | Strikeout universe and production replica |

**Production fidelity:**
- `prop_probability.py` and `mlb_sources.py` are unchanged between `bbbe4683` and main `99372287664738514352b1d304f8ee67bbdde961`. The `generate_picks.py` diff concerns lineup provenance, price fetching and freezing, not probability math.
- Disclosed engine gaps (`backtest/engine.py`): no implied team totals (league-mean run environment), no wRC+ or Stuff+, no umpire, a constant `cat_environment`.
- For strikeouts, the baseline is the FC-ACC-013 production replica A (exp-weighted K/BF × Binom(E[BF]), shrunk 50% to the trailing-30-day league rate).

**Previously exposed periods (not untouched holdouts):**
- 2025 and 2026 batter hits were used by the canonical locked hits-PA-opportunity and disagreement studies and by FC-MLB-002..006.
- Pitchers 2024–26 were used by FC-ACC-004 and FC-ACC-013.
- 2+ total bases is the least exposed market.

## 2. Universes and outcomes

- **Hits:** canonical rows `prop_type=="hits"`, `needs==1`.
- **Total bases:** `prop_type=="total_bases"`, `needs==2`.
- **Strikeouts:** FC-ACC-013 eligible starts at t = 4, 5, 6.
- **Outcome:** the canonical `outcome` field (0/1). All graded rows are included; the `fair_test==True` subset is reported separately.
- **Seasons:** by date year. Quarantined dates are already absent.
- **Joins:** rows that fail to join to StatsAPI features are **kept**. Missing features are median-imputed (training rows only) with a `__missing` indicator, the same rule as `backtest/signals.py`. Join coverage is reported.

## 3. Feature groups (all strictly prior to the game date; no target-game data)

**Batter markets:**
- **BASE:** `logit(predicted_prob)`.
- **SKILL:** season-to-date per-PA hit, TB, K, BB, HR and extra-base-hit rates, each shrunk by adding 100 league-average PA; log(1 + prior PA); prior-season per-PA hit and TB rates (shrunk 100 PA).
- **OPP (opportunity):** batting slot (from `lineup_slot`); home indicator; team season-to-date runs per game.
- **MATCH:**
  - opposing starter (schedule probable pitcher) season-to-date hits/BF, TB-allowed/BF, K/BF and BB/BF (shrunk 100 BF), plus the number of prior starts;
  - platoon class (same hand / opposite hand / switch hitter);
  - production signals `platoon`, `sp_era_weak`.
- **CONTACT:** production signals `l7_avg_ev`, `l7_barrel_pct`, `season_barrel_pct`, `iso`, `platoon_xwoba`, `platoon_barrel_pct`, `bat_speed_trend`, `pitch_exploit`.
- **LINEUP:** production signals `woba_behind`, `woba_ahead`; team season-to-date hits/PA.
- **PARK_WX:**
  - venue hit and TB factors (venue strictly-prior per-PA rate ÷ league, shrunk 3,000 PA);
  - temperature, wind mph, wind-out and wind-in indicators, roof indicator;
  - production signals `park_hand_index`, `pull_park_synergy`;
  - weather is the game-time observation, disclosed.
- **RECENCY:** last 7 and 14 days hits/PA and TB/PA (shrunk 20 PA); games in the last 7 days.
- **CONTEXT:** production signals `days_rest`, `series_game`, `getaway_day`, `bullpen_fatigue`, `opp_catcher_framing`, `bullpen_era_diff`.

**Strikeouts:**
- **BASE:** `logit(A_t)`.
- **SKILL:** exp-weighted K/BF; season-to-date K/BF and BB/BF (shrunk 100 BF); prior-season K/BF (shrunk 100 BF); prior starts.
- **WORKLOAD:** E[BF]; prior BF mean and SD; last start's pitches; days of rest.
- **MATCH:**
  - opponent starting lineup's mean strictly-prior season-to-date K/PA (each batter shrunk 50 PA), with coverage;
  - opponent team strictly-prior K/PA;
  - the lineup's share batting with the same hand as the pitcher.
- **PARK_WX:** venue K factor (shrunk 3,000 PA); temperature; roof.
- **RECENCY:** last-3-starts K/BF.

## 4. Models (hyperparameters fixed now; nothing tuned)

- **M0:** production probability, raw.
- **M1 (calibration control):** logistic on `[1, logit(M0)]`. It is monotone in M0, so its ranking equals production's.
- **M2 (GLM):** L2-penalised logistic on standardized BASE plus all groups. Penalty λ = 1.0 on the coefficient sum of squares (intercept unpenalised), fitted by Newton-IRLS. Strikeouts get one model per threshold.
- **M3 (market-specific distribution):**
  - **Hits:** `P = Σ_n π_slot(n)·[1 − (1 − q)^n]`. π_slot is the strictly-prior league PA-per-game distribution for that batting slot (1..9, from logs plus lineups). `q = log5(batter hit/PA, opposing-SP hits/BF, league hit/PA) × venue hit factor`.
  - **Total bases:** per-PA outcome vector (0, 1B, 2B, 3B, HR). The batter's shrunk rates are multiplied by the opposing SP's hits-allowed ratio (singles, doubles, triples) and HR ratio (HR) to league, times the venue TB factor; the outcome-0 mass is renormalised. Convolved over π_slot; P(TB ≥ 2).
  - **Strikeouts:** per-BF `q = log5(exp-weighted pitcher K/BF, lineup K/PA, league K/PA)`, binomial over the FC-ACC-013 pitcher BF mixture centred at E[BF]. No league shrink.
  - **Calibration:** each M3 then gets Platt `sigmoid(a·logit(P) + b)`, fitted on that fold's training rows only. Strikeouts pool the thresholds, as production does.
- **M4 (the one nonlinear family):** `sklearn.HistGradientBoostingClassifier` with learning_rate 0.05, max_iter 300, max_leaf_nodes 15, min_samples_leaf 200, l2_regularization 1.0, early_stopping False, random_state 20261022. It uses the M2 feature set (raw values, NaN native). One model per market and per threshold.

## 5. Chronological, nested evaluation

- **Fold 1:** train 2024 → evaluate 2025.
- **Fold 2:** train 2024 + 2025 → evaluate 2026.
- No 2025/26 outcome is used to choose any hyperparameter. The only selection made with fold-1 outcomes is the per-market candidate choice in §7.

## 6. Metrics

- **Scores:** Brier, log loss, AUC.
- **Calibration:** calibration-in-the-large, logit calibration slope, ECE (10 equal-width bins), reliability by decile.
- **Uncertainty:** paired differences against M1 and M0, with 2,000-resample bootstraps (seed 20261022) clustered by **date** and, separately, by **player**.
- **Stability:** by month.
- **Coverage:** join and missingness by group.

**Ablation (GLM family):**
- add-one: M1 + G, for each G;
- drop-one: M2 − G;
- Δ log loss with date-clustered CIs in both folds;
- factor influence: standardized M2 coefficients per fold.

**Hits vs total bases:** on each fold, compare the own-market M2 with a cross-direction model, `logit p = a + b·η_other`, where η_other is the other market's fitted M2 linear predictor and a, b are refit on own-market training rows. The test is two-sided both ways. Also report whether M3/M4/M2 rank the same in both markets.

**Decision diagnostics:**
- **Equal-N:** per date, N_d = max(1, round(0.05 × candidates)) for hits and total bases, and max(1, round(0.10 × starts)) for strikeouts at each threshold. The baseline picks the top N_d by M1, which is identical to production's order. Each challenger picks its own top N_d.
  - Report added and removed picks, their winners, and net, with a date-clustered CI.
- **Fixed-floor, labelled NOT equal-volume:** p ≥ 0.60 for hits.
- **Matched-volume ROI: NOT MEASURABLE.**

## 7. Frozen selection and verdict rules (per market; strikeouts judged across t = 4, 5, 6 jointly)

- **Candidate:** the model in {M2, M3, M4} with the lowest fold-1 log loss (strikeouts: the lowest mean over t).
- **GO** (to prospective matched-volume validation, not promotion) requires all of:
  - the candidate beats M1 on log loss with the date-clustered 95% CI excluding 0 in **both** folds;
  - and equal-N net winners > 0 in both folds, with the fold-2 date-clustered CI excluding 0.
  - Strikeouts: at ≥ 2 of 3 thresholds.
- **MODIFY:** the log-loss criterion holds in both folds, but equal-N winners are not established.
- **STOP:** otherwise.

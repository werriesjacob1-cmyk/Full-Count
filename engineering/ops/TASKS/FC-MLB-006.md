# FC-MLB-006 — Coherent plate-appearance event world model (Hits 1+)

## ACCEPTANCE_CRITERIA
Frozen before any FC-MLB-006 model code. Later changes need SUPERCHAD.

**Evidence class: DEVELOPMENT only.** 2026 outcomes have already been studied (FC-MLB-002/004/005). No confirmatory language. IMPROVES would not authorize promotion.

**Question.** Does a coherent per-PA outcome distribution, with the same restrained information as the baseline (batter talent, opposing-pitcher talent, handedness), rank player-games for Hits 1+ better than the strongest simple baseline at identical daily volume?

### 1. Identities (STOP on any mismatch)
| item | identity |
|---|---|
| Raw Statcast | `research/mlb_engine/fc_mlb_002/STATCAST_MANIFEST.jsonl` sha256 `3fb1d0170a3f465ec30625b3deb08e17bae3f4858a468b0cae9f97d608b1d16a` (385 files, 2025-03-18..09-28, 2026-03-25..09-30) |
| PA table | `pa.parquet` sha256 `cb06b8f38c9dd8275194ebe60dc4c24e263b1fa366edd03f42c4b9abf03387a4`, bound by `PA_PROVENANCE.json` to the manifest (`F.verified_provenance()`) |
| Pitch table | `pitches_slim.parquet` sha256 `a87b1c7e560639aa692bfad87ae8a875147db53c3c612e484928fe26de639c76` (needed only by `F.tables`) |
| Batting side | `people_v2.json` sha256 `84b5c960f71a921c3a43c3db66a2a8634c584038cf89302c9eae416415ae3ec7` (biographical) |
| Schedule identity | `schedule_identity.json` sha256 `f7987b83ab0e2ed10a97829f7849af4abb8e832238082c1073ac66737ff3d308` (market matching only) |
| Champion code | main `8b689852342bd49014b86ad637e64002ad6f47b4` (`prop_probability`, `generate_picks`, imported unchanged) |
| Baseline fit | FC-MLB-002 `FROZEN_FIT_2025.json` sha256 `6c0ecde23577659c1e72e18448415208eb4f5d7d2748f24b9631cd01ded7d6aa` |
| Baseline predictions | FC-MLB-004 `out/predictions_2026.csv.gz` @ `46712d8091`, sha256 `3328267f08086cc59213240eaa4fc6edef9f956a9c501da2d58ac35183f5066d` |
| Market props | `data/props/props_<date>.json` at the **pinned** main commit `a8e472272c3cb57884c8a6ccb7cb41beaabb9b26` (no new scraping) |
| Machinery | `fc_mlb_002` @ `7c331c266b` and `fc_mlb_005.home_teams` @ `5e1d6bd8ec`, imported read-only |

### 2. Outcome and populations
- **Outcome:** Hits ≥ 1 settled on the whole player-game, one row per `(game_pk, batter)`. Never fragment rows.
- **Evaluation population:** exactly the FC-MLB-002/004/005 repaired 2026 population (37,125 player-games, 2026-04-15..09-27, 163 dates).
- **Training population:** 2025 regular-season PAs from 2025-04-15 on, with batter season-to-date PA ≥ 30. The pitcher is the actual pitcher, and same_hand is actual. This is identical to the baseline's training rows (FC-MLB-005 refit check reproduced CH1a exactly).
- **No 2026 fitting or tuning.**

### 3. Baseline P0
- P0 = FC-MLB-002 frozen CH1a = FC-MLB-004 P0 = FC-MLB-005 D0, with coefficients from the baseline fit above.
- **Fidelity gate:** recomputed P0 and CH0 must equal the FC-MLB-004 P0/CH0 columns row for row on `(game_pk, batter)`, with |Δ| < 1e-9 and an identical population. Otherwise STOP. No substitute baseline.

### 4. Event categories (mutually exclusive and exhaustive over the PA table)
| category | `events` values |
|---|---|
| `1B` | single |
| `2B` | double |
| `3B` | triple |
| `HR` | home_run |
| `BB` | walk, intent_walk |
| `HBP` | hit_by_pitch |
| `K` | strikeout, strikeout_double_play |
| `OUT` (reference) | every other PA event: field_out, force_out, grounded_into_double_play, double_play, triple_play, sac_fly, sac_fly_double_play, sac_bunt, fielders_choice, fielders_choice_out, field_error, catcher_interf |

- Reaching on an error and catcher interference are non-hit outcomes, so they go in OUT. Hit = {1B, 2B, 3B, HR}, which is exactly `pa.hit`.
- No categories are combined. Triples (≈0.35%) are kept because the softmax plus shrinkage handles sparse classes, and Hits uses only their sum.
- An unmapped event value is an error (STOP).

### 5. Model E1: multinomial logit (softmax) with category-specific talent features
For PA *i* and category *c* ≠ OUT:

`z_c = α_c + β_c·Lb_c + γ_c·Lp_c + δ_c·same_hand`, with `z_OUT = 0`, and `q_c = exp(z_c) / Σ_j exp(z_j)`.

The 8 probabilities sum to 1 by construction.

**Why this form.** It is the simplest coherent multiclass model. With α_c = league log-odds versus OUT and β = γ = 1, δ = 0, it is exactly the generalized log5 (odds-ratio) rule. Fitting β_c and γ_c lets the data regress each channel. A sequential/conditional tree was rejected: it is equivalent in capacity here but adds stage choices.

**Features** (season-to-date through D−1, via FC-MLB-002's `through`/`asof` with `allow_exact_matches=False`):
- League rate `L_c` = league season-to-date frequency over all PAs.
- Batter rate `rb_c = (nb_c + kb_c·L_c) / (Nb + kb_c)`.
- Pitcher rate vs the batter's side `rp_c = (np_c + kp_c·L_c) / (Np + kp_c)`. The bullpen uses the fielding team's relief aggregate vs the batter's side, with the same kp_c.
- `Lb_c = log(rb_c / rb_OUT) − log(L_c / L_OUT)`; `Lp_c` is defined the same way.
- `same_hand` is identical to P0:
  - training: actual;
  - evaluation, starter: from the starter's known hand;
  - evaluation, bullpen: league same-hand share of relief PAs as of D−1;
  - switch hitters: 0.

**Prior strengths k (per category, batter and pitcher separately).** These are fixed by a deterministic method-of-moments beta-binomial estimator on **2025 full-season totals** (training season only; no grid, no split tuning):
- Use players with N ≥ 200 PA (batters) or BF ≥ 200 (pitchers, pooled over hand).
- `var_true = var_obs(x_i) − mean(p̄(1−p̄)/N_i)`.
- `k_c = p̄(1−p̄) / var_true − 1`, clipped to [20, 5000] (and 5000 if var_true ≤ 0).
- The values are written into the frozen fit.

**Fit.** Unpenalised maximum likelihood (28 parameters), L-BFGS with an analytic gradient, on the training population. Convergence must be reported; non-convergence = STOP.

### 6. Player-game aggregation (the challenger probability originates in E1)
- Per-PA hit probability vs the starter is `h_sp = q_1B + q_2B + q_3B + q_HR`; vs the bullpen it is `h_pen` (for switch hitters, the RHP-share mixture of the L and R bullpen sides, exactly as in P0).
- `h = w_sp·h_sp + (1 − w_sp)·h_pen`, with `w_sp` unchanged from FC-MLB-002.
- `E1_raw = pp.p_at_least_hits(1, {0: 1−h, 1: h}, proj_pa)`: the champion's exact binomial, with fractional `proj_pa` as the floor/ceil mixture.
- `proj_pa = gp.project_batter_pa(slot, None)` (projected-PA machinery held fixed).
- The reported challenger is `E1 = 0.5·lg_h1 + 0.5·E1_raw`, the identical league blend P0 uses.
  - `lg_h1` is constant within a date, so **within-date rankings of E1 and E1_raw are identical**.
  - The blend only matters for probability diagnostics, which report both.

### 7. Primary decision (realized hit rate decides; probability metrics never do)
- **Primary:** K = 10, E1 − P0, realized Hits-1+ rate of the top-min(K, eligible) per date. Ties go to the lower batter id (`F.topk_select`). Identical eligible sets.
- **CI:** paired date-block bootstrap with 2,000 reps and seed 7 (`F.boot_ci`).
- **IMPROVES** iff diff ≥ +1.0 pp and the CI lower bound > 0.
- **WORSE** iff the CI upper bound < 0.
- **NO_GAIN** iff the CI includes 0 and |diff| < 0.5 pp.
- Otherwise **INCONCLUSIVE**.
- **Secondary:** K = 5 and K = 20 (same rule, descriptive) and E1 − CH0.
- **Per comparison:**
  - dates; eligible player-games; selections per side; shortfall dates;
  - both rates and the pp difference with its CI;
  - overlap share;
  - hit rate of added (E1-only) and removed (P0-only) selections;
  - concentration: top-10 batters' share of selections, max selections per batter, distinct batters and games.

### 8. Stability (descriptive; nothing tuned)
E1 − P0, P0 − CH0 and E1 − CH0 at K10, plus the log-loss gain, for:
- Apr–Jun and Jul–Sep, and each month;
- starter season-to-date BF bins: <100, 100–299, 300–499, ≥500;
- batter home vs away (home team = fielding team on each game's first PA).

### 9. World-model diagnostics (descriptive)
- **(a) 2026 PA-level, actual pitcher:**
  - 8-class log-loss for E1 vs a league season-to-date frequency reference;
  - binary hit log-loss for E1's hit sum vs P0's PA-level hit model vs league.
- **(b)** Per-category mean predicted vs actual frequency, and decile calibration for hit and K.
- **(c)** Invariants: max |Σq − 1|, min/max q per category, and no NaN/inf.
- **(d)** Player-game log-loss, Brier and calibration deciles for CH0, P0, E1 and E1_raw.
- **(e)** Spearman rank correlation E1~P0, and the daily top-K overlap.
- **(f)** Opponent information check:
  - E1 with Lp_c = 0 (pitcher at league), reported only as a diagnostic: its correlation with E1, and its K10 rate;
  - the share of cross-row variance in z_hit-logit attributable to the pitcher term.
  - This is not a candidate model.

### 10. Market subset (secondary, descriptive)
- FC-MLB-002's repaired `match_market` on props at the pinned main `a8e472272c`. One-sided de-vig is APPROXIMATE.
- K3 and K5 for P0, E1 and the market, with paired date-block CIs for E1 − P0 and E1 − market.
- No market prior; no edge claim unless the CI excludes 0.

### 11. Point-in-time and leakage
- Every feature at date D uses only games strictly before D.
- Same-day games, including doubleheader game 1 for game 2, never update talent.
- No realized current-game information: batting side comes from batSide and the starter's hand; slot is the existing FC-MLB-002 definition.
- Tests must cover:
  - doubleheader/same-day exclusion;
  - invariance to perturbing same-day and future outcomes;
  - event-map exhaustiveness;
  - softmax sum-to-one;
  - the analytic-gradient check;
  - the log5 identity;
  - aggregation equality with `pp.p_at_least_hits`;
  - the method-of-moments estimator on synthetic data;
  - the fidelity gate.

### 12. Chronology and stop
- **Order:**
  1. criteria frozen and pushed;
  2. tests;
  3. 2025 fit plus a descriptive 2025 split check (fit Apr 15–Jul 15, score Jul 16–end; no selection) committed and pushed;
  4. **ONE** 2026 evaluation, with the fidelity gate inside it aborting on failure.
- After that: report and stop. No FC-MLB-007, no re-specification and no reruns on the evaluated outcomes. A crash before results are written may be re-executed unchanged, and that is disclosed.

### 13. Evidence and boundaries
- `research/mlb_engine/fc_mlb_006/` on `claude/mlb-engine-pa-world-model-20261003`: code, tests, `FROZEN_FIT_006.json`, `RESULTS.json`, `REPORT.md` (mechanical), `predictions_2026.csv.gz` and logs.
- No change to V3, production, the selector, picks, the ledger, the site or NFL Week 4, and no change to FC-MLB-002/004/005 evidence.

## BUILDER_NOTES
(owner only)
- Fit 3ede23c1a0 converged (28 params). One 2026 evaluation at a555d4dd59; fidelity gate passed (37,125 rows).
- K10 E1-P0 -0.18 [-2.27, +1.96] -> NO_GAIN. K5 -3.07 [-6.26, -0.12] (WORSE region, secondary). K20 -0.09 NO_GAIN.
- E1 is the better probability model (PA hit logloss 0.52388 vs P0 0.52424; player-game E1_raw logloss best), but the top tail concentrates on fewer batters (K5 top-10 batters 62% of selections vs 44%) and is not more accurate.
- Stability: E1 helps Apr-Jun (+1.17; April +6.25) and loses Jul-Sep (-1.40): complementary to P0's late-season pattern.

## LOG
- 2026-10-03 criteria frozen; claimed.
- 2026-10-03 fit 3ede23c1a0; single evaluation a555d4dd59 -> READY_FOR_CHALLENGE (holding for SUPERCHAD/Jacob).

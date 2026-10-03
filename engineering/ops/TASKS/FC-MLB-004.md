# FC-MLB-004 — Pitcher contact-suppression refinement (Hits 1+)

## ACCEPTANCE_CRITERIA
Frozen before any FC-MLB-004 code. Codex may add criteria at its morning challenge; later changes need SUPERCHAD.

**Evidence class: DEVELOPMENT only, with an extra caveat.** This hypothesis was chosen after seeing FC-MLB-002's 2026 development results (late-season concentration of the pitcher-effect gain). Any 2026 result is therefore development evidence twice over: hypothesis-generating, never confirmatory.

1. **Target and population**
   - Hits Over 0.5, settled on the whole player-game.
   - The population is FC-MLB-002's repaired machinery at `7c331c266b` (import only), unchanged:
     - one row per `(game_pk, batter)`;
     - starters (slot ≤ 9) with an identified opposing starter and ≥ 30 season-to-date PA;
     - 2026-04-15 through the end of the regular season;
     - pregame matchup side from `batSide`, with switch hitters handled exactly as in FC-MLB-002.
   - Never fragment rows.
2. **Baseline P0 = repaired CH1a, exactly as frozen.** Coefficients are taken from FC-MLB-002 `FROZEN_FIT_2025.json` (sha `6c0ecde2…`): Lb (kb=200) + Lp (pitcher H/BF vs hand, kp=150) + same_hand.
   - **Fidelity gate:** P0 and CH0 values recomputed here must equal FC-MLB-002's `predictions_2026.csv.gz` (`b52ac418ba`) row for row (|Δ| < 1e-9). Otherwise STOP.
   - CH0 (production) is reported as a reference row only.
3. **Challenger P1.** Replaces only the pitcher latent term `Lp` with `Lpx`.
   - `Lpx` = EB-shrunk logit of the pitcher's **expected hits per batter faced** vs the batter's hand, season-to-date through D−1.
   - Expected hits per PA = Statcast xBA (`estimated_ba_using_speedangle`) on balls in play, and 0 on PAs without contact (K, BB, HBP).
   - Balls in play missing xBA (about 2%) use that PA's realized hit indicator. These are past PAs only, so no leakage.
   - The bullpen uses the team's relief aggregate, the same way.
   - The PA-level logistic model `Lb + Lpx + same_hand` is fitted on **2025 only**:
     - `kb` = 200 (batter term unchanged);
     - `k_px` from the grid {50, 150, 400, 1000}, chosen by a 2025 time split (fit Apr 15–Jul 15, score Jul 16–end), then refit on all of 2025 and frozen.
   - **Unchanged:** batter latent, projected PA, starter/bullpen mixture and `w_sp`, switch-hitter handling, 0.5 league shrink, top-K, settlement, market handling.
   - Not added: pitch-family interaction, pitch shape, whiff/CSW (their information enters only through xH/BF).
4. **Optional P2 = P1 + team defense conversion.**
   - `Dteam` = the fielding team's shrunk logit(hits on balls in play / BIP) − logit(xH on BIP / BIP), season-to-date through D−1, from all of its pitchers. It includes the park.
   - `k_d` from the grid {500, 1500, 4000}, chosen on the same 2025 split; fitted on 2025 and frozen.
   - **Validity rule:** P2 is evaluated only if Dteam is point-in-time computable for every row. Its 2025 holdout log-likelihood is reported either way.
5. **Same opportunity:** identical eligible player-games and equal top-min(K, eligible) per date for every model, with actual counts reported.
6. **Primary metric:** realized Hits-1+ rate at K = 10, P1 − P0, paired by date, with a 95% date-block bootstrap (2,000 resamples, FC-MLB-002's `boot_ci`).
   - **IMPROVES** iff diff ≥ +1.0 pp and the CI lower bound > 0.
   - **WORSE** iff the CI upper bound < 0.
   - **NO_GAIN** iff the CI includes 0 and |diff| < 0.5 pp.
   - Otherwise **INCONCLUSIVE**.
   - K = 5 and K = 20 are reported. P2 − P0 and P2 − P1 are secondary.
7. **Stability (primary diagnostic, descriptive; nothing tuned on it):**
   - P0/P1/P2 − CH0 and P1 − P0 for Apr–Jun, Jul–Sep and each month.
   - Starter season-to-date BF bins: <100, 100–299, 300–499, ≥500.
   - "Reduces early instability" is reported if (P1 − CH0) − (P0 − CH0) for Apr–Jun is ≥ +1.0 pp. Descriptive only.
8. **Probability quality:** logloss, Brier and calibration deciles for CH0/P0/P1/P2, all with the identical 0.5 league shrink. These explain mechanism; they don't decide.
9. **Market subset:** FC-MLB-002's repaired `match_market` (event identity; last snapshot strictly before first pitch; FanDuel only; one-sided de-vig labelled APPROXIMATE). K = 3 and K = 5, comparing P0, P1, P2 and the market ranking, with paired date-block CIs for P1−P0, P2−P0, P1−market and P2−market. No edge is claimed unless a CI excludes 0.
10. **Leakage / point-in-time:**
    - Every feature at date D uses only games before D. Same-day games, including doubleheader game 1, are excluded.
    - No realized lineup handedness.
    - No 2026 fitting or tuning.
    - **One** frozen 2026 evaluation, run after the frozen fit is committed.
11. **Evidence:** `research/mlb_engine/fc_mlb_004/` on branch `claude/mlb-engine-pitcher-contact-20261003`:
    - FROZEN_FIT_004.json (committed before the evaluation);
    - RESULTS.json and REPORT.md;
    - predictions_2026.csv.gz;
    - tests.
    Raw bytes are verified against FC-MLB-002's committed manifest.
12. **Stop:** one experiment.
    - **NO_GAIN or WORSE:** record the negative result.
    - **IMPROVES or INCONCLUSIVE:** Codex challenge.
    - **Never:** promotion, production, selector, pick or ledger change, or touching V3 / Week 4.

## BUILDER_NOTES
(owner only)
- Frozen fit 6e0e24f1e0 (kpx=150, kd=1500, chosen on the 2025 split); one 2026 evaluation at 46712d8091.
- Fidelity gate passed: 37,125 rows; max |dCH0| 5.6e-17, max |dP0-CH1a| 0.
- Primary K10 P1-P0 -0.18 [-1.96, +1.72] -> NO_GAIN (negative result recorded). K5 -0.12; K20 +0.34. Early instability not reduced.
- Secondary: P2-P1 K10 +1.35 [+0.18, +2.52]; P2-P0 K10 +1.17 [-0.55, +2.94]. P2 is the best logloss (0.66468 vs P0 0.66496). Secondary only; not a verdict.
- Criteria §12 says NO_GAIN -> record the negative result; Jacob's overnight instruction asks for a morning Codex challenge. Moving to READY_FOR_CHALLENGE so the negative result (and the P2 secondary) are verified; this is not a request for iteration.

## LOG
- 2026-10-02 criteria frozen; claimed.
- 2026-10-03 frozen fit 6e0e24f1e0; single evaluation 46712d8091; -> READY_FOR_CHALLENGE.
- 2026-10-03 SUPERCHAD: NO_GAIN accepted as negative development result; DONE; challenge waived; no further P1 work. P2 signal -> FC-MLB-005.

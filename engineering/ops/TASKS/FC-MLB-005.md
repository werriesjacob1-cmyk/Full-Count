# FC-MLB-005 — Team conversion residual isolation (Hits 1+)

## ACCEPTANCE_CRITERIA
Frozen before any FC-MLB-005 code, per SUPERCHAD's FC-MLB-004 adjudication (relayed by Jacob, 2026-10-03). Later changes need SUPERCHAD.

**Evidence class: DEVELOPMENT only. No confirmatory language.**
This hypothesis was generated AFTER inspecting 2026 development evidence: FC-MLB-004's secondary P2 − P1 contrast. Any 2026 result here is hypothesis-generating only. Even IMPROVES does not authorize promotion.

**Terminology.** The term is called the **TEAM CONVERSION RESIDUAL** (`Dteam`), never "defense". It may contain:
- fielding quality and positioning;
- park effects;
- spray-direction structure that xBA ignores;
- scorer or data effects;
- other batted-ball structure that xBA does not capture.

1. **Question.** Does Dteam add equal-volume ranking information to the proven P0 pitcher-main-effect model? This isolates Dteam from FC-MLB-004's failed P1 change.
2. **Population.** Exactly the repaired FC-MLB-002 / FC-MLB-004 machinery, by import only (`fc_mlb_002` @ 7c331c266b; `fc_mlb_004` @ 46712d8091).
   - One row per `(game_pk, batter)`; no fragment rows.
   - Same eligibility, settlement, switch-hitter handling, projected PA, starter/bullpen mixture (`w_sp`), market identity and top-K mechanics.
3. **Baseline D0 = FC-MLB-004 P0 = FC-MLB-002 frozen CH1a**, exactly: Lb (kb=200) + Lp (kp=150) + same_hand, with coefficients from `FROZEN_FIT_2025.json` (sha `6c0ecde2…`).
   - **Fidelity gate:** recomputed D0 and CH0 must equal FC-MLB-004 `out/predictions_2026.csv.gz` @ 46712d8091 (sha256 `3328267f…`), columns P0 and CH0, row for row on `(game_pk, batter)`, with |Δ| < 1e-9 and an identical population. Otherwise STOP.
   - Additional fit-side check: refitting `Lb + Lp + same_hand` on the identical 2025 training rows must reproduce the frozen CH1a coefficients within 1e-4. If it does not, STOP and report.
4. **Challenger D1 = D0 + Dteam.** This is the ONLY model change.
   - **Not included:** the xH/BF pitcher term (P1), pitch-family interaction, pitch shape, or any other feature.
   - **Dteam is FC-MLB-004's frozen construction, unchanged:**
     - fielding team's shrunk `logit(H_bip / BIP) − logit(xH_bip / BIP)`, season-to-date through D−1;
     - shrunk toward the league's season-to-date ratios with **k_d = 1500 reused** (no new degree of freedom; no grid);
     - xH on balls in play = xBA, or the realized hit where xBA is missing (past PAs only).
   - The same Dteam applies to the starter and bullpen components (it is a team quantity) and to both switch-hitter sides.
   - If k_d = 1500 cannot be reused for a mathematical reason: STOP and report; no silent retune.
5. **Fit.** PA-level logistic `Lb + Lp + same_hand + Dteam`, fitted on **2025 only** (Apr 15 → end, rows with ≥ 30 batter PA), all coefficients jointly.
   - Also reported, descriptively: the 2025 split (fit Apr 15–Jul 15, score Jul 16–end) holdout log-loss for D0-refit and D1.
   - `FROZEN_FIT_005.json` is committed and pushed before the 2026 evaluation.
   - No 2026 tuning; no coefficient change after evaluation.
6. **Before evaluation:**
   - (a) these criteria frozen;
   - (b) focused point-in-time tests, including Dteam same-day and doubleheader exclusion and invariance to same-day or future outcome perturbation;
   - (c) frozen fit committed;
   - (d) the D0 fidelity gate runs inside the evaluation and aborts on failure.
   - Then **ONE** 2026 evaluation.
7. **Primary.** K = 10 realized Hits-1+ rate, D1 − D0, top-min(K, eligible) per date with identical opportunities. Paired date-block bootstrap: 2,000 reps, seed 7 (FC-MLB-002's `boot_ci`).
   - **IMPROVES** iff diff ≥ +1.0 pp and the CI lower bound > 0.
   - **WORSE** iff the CI upper bound < 0.
   - **NO_GAIN** iff the CI includes 0 and |diff| < 0.5 pp.
   - Otherwise **INCONCLUSIVE**.
   - Also reported: K5 and K20, plus D0 − CH0 and D1 − CH0.
8. **Stability (descriptive; nothing tuned on it).** D1 − D0, D0 − CH0 and D1 − CH0 at K10 for:
   - Apr–Jun and Jul–Sep;
   - each month;
   - starter season-to-date BF bins: <100, 100–299, 300–499, ≥500.

   Key question: does D1 shrink the Apr–Jun vs Jul–Sep gap in the pitcher-main-effect gain? Report (D1 − CH0) and (D0 − CH0) for both halves.
9. **Mechanism diagnostics.** Descriptive only; no variants are created after seeing them. All from existing data (`pa.parquet` plus the verified contact table); the home team is taken as the fielding team on each game's first PA.
   - Distribution of Dteam over evaluation rows, by season-to-date team BIP sample size.
   - 2025 persistence of the team raw residual: first half (Apr 15–Jul 15) vs second half, across teams; odd vs even date split-half reliability; 2025 full season vs 2026 full season.
   - Team identity: the share of Dteam variance explained by fielding team (eta²) over 2026 evaluation rows.
   - Park/home: team residual on home BIP vs away BIP, across teams; venue residual (all BIP at that park) vs the fielding team's away residual.
   - Predictive association: point-in-time Dteam vs the fielding team's realized rest-of-season raw residual (team-dates, 2025 and 2026).
   - Home vs away performance: D1 − D0 at K10 and the log-loss gain, for batter-away rows (team fielding at home) vs batter-home rows.
10. **Probability quality.** Log-loss, Brier, mean prediction and calibration deciles for CH0, D0 and D1, with the identical 0.5 league shrink. These do not decide the verdict.
11. **Market subset.** FC-MLB-002's repaired `match_market` (FanDuel; last snapshot strictly before first pitch; one-sided de-vig APPROXIMATE).
    - K3 and K5 for D0, D1 and the market ranking.
    - Paired date-block CIs for D1 − D0 and D1 − market.
    - No market-edge claim unless the CI excludes 0.
12. **Leakage / point-in-time.**
    - Every feature at date D uses only games strictly before D; same-day games, including doubleheader game 1, are excluded.
    - No realized current-game data.
    - No new scraping or new data sources.
13. **Evidence.** `research/mlb_engine/fc_mlb_005/` on branch `claude/mlb-engine-team-conversion-20261003`:
    - `FROZEN_FIT_005.json` (committed before evaluation);
    - `RESULTS.json`, `REPORT.md` (rendered mechanically), `predictions_2026.csv.gz`;
    - tests.
14. **Stop.** One experiment.
    - Any verdict is recorded and work stops. No FC-MLB-006 is created automatically; return to SUPERCHAD/Jacob.
    - No Codex tonight.
    - **Never:** promotion, production, selector, pick or ledger change, V3, or NFL Week 4.

## BUILDER_NOTES
(owner only)
- Frozen fit fc3230785a (Dteam coef 0.322; D0 refit = frozen CH1a exactly). One 2026 evaluation at 5e1d6bd8ec. Fidelity gate passed (37,125 rows).
- Primary K10 D1-D0 +0.00 [-1.17, +1.23] -> NO_GAIN. K5 -0.25, K20 -0.06 (both NO_GAIN by the same rule). Negative result recorded; branch stopped.
- Reading: the FC-MLB-004 P2-P1 gain did not transfer to the P0 baseline. Raw pitcher H/BF (Lp) already carries realized BIP conversion; Dteam mostly restored what P1's xH/BF removed (Dteam coef 0.57 next to Lpx vs 0.32 next to Lp).
- Mechanism: team residual persistence r 0.39 (2025 halves), 0.35 (split-half), 0.53 (2025 vs 2026); eta2 by team 0.76; home residual tracks visitors-at-park (0.37) more than own away residual (0.23) -> park/context is material.

## LOG
- 2026-10-03 criteria frozen from SUPERCHAD's adjudication; claimed.
- 2026-10-03 frozen fit fc3230785a; single evaluation 5e1d6bd8ec; -> READY_FOR_CHALLENGE (holding for SUPERCHAD/Jacob).

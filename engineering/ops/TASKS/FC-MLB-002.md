# FC-MLB-002 — First predictive-engine challenger: batter × pitcher interaction for Hits 1+

## ACCEPTANCE_CRITERIA
Frozen at the claim commit (before any challenger code). The challenger (Codex) may ADD criteria; changes after that need SUPERCHAD.
**Evidence class:** 2026 outcomes have already been visible to the project, so every 2026 result here is **DEVELOPMENT evidence**, never confirmatory. Confirmation happens only prospectively, in a separate task.

1. **Target population**
   - **Settlement:** MLB regular season (`game_type R`) batter-games from 2026-04-15 to the end of the 2026 regular season. The prop is **Hits Over 0.5**: YES iff the batter records ≥ 1 hit.
   - **Eligibility:**
     - the batter is in the starting lineup (one of the first 9 distinct batters for his team in that game);
     - the opposing starting pitcher is identified (first pitcher his team faced);
     - the batter has ≥ 30 season-to-date PA before the game (the champion's `min_pa`).
   - **Diagnostics only:** Hits Over 1.5 and Total Bases Over 1.5.
2. **Champion (CH0)**
   - FULL COUNT's production hits path at main `8b68985234` (`generate_picks._batter_options`, hits, needs=1):
     - `modelled = prop_probability.p_at_least_hits(1, pa_outcome_distribution(1B/2B/3B/HR per PA), project_batter_pa(slot, implied_total))`;
     - `prob = 0.5 × league_hits_1plus + 0.5 × modelled`.
   - These functions are **imported unchanged** from main.
   - **Inputs:** season-to-date per-PA rates through D−1, rebuilt from Statcast PA outcomes (the same counts `batter_pa_composition` uses). The production calibrator is monotone: it is omitted from the ranking (no effect) and logloss is reported raw for both models.
   - **Stated deviations, identical for both models:** actual lineup slot instead of projected; implied total = production's league-mean fallback.
3. **Challenger (CH1)**
   - CH1 replaces **only** the per-PA hit probability. PA count, slot, binomial mapping and the 0.5 league shrink are identical to CH0.
   - **Per-PA probability:**
     - `p = w_sp·p(b, SP) + (1−w_sp)·p(b, opposing bullpen)`;
     - `w_sp = min(1, SP's season-to-date mean batters faced per start / league mean team PA per game)`.
   - **`p(b, P)`** comes from a PA-level logistic model **fitted only on 2025 PAs and then frozen**:
     - `logit p = β0 + β1·Lb + β2·Lp + β3·Mbp + β4·same_hand`;
     - `Lb`: empirical-Bayes-shrunk logit of the batter's hits/PA;
     - `Lp`: EB-shrunk logit of the pitcher's hits allowed per batter faced versus the batter's hand;
     - `Mbp = Σ_f û_P(f | batter hand) · d̂_b(f)`, with `f` ∈ {fastball, breaking, offspeed};
     - `û` is the pitcher's shrunk pitch-family usage against that hand;
     - `d̂_b(f)` is the batter's EB-shrunk logit deviation in PAs that end on family `f`, relative to his overall rate;
     - all inputs are season-to-date through D−1, and shrinkage strengths are fitted on 2025.
   - The bullpen enters as a team-level aggregate pitcher.
   - **Ablations reported:**
     - CH0b: fitted batter-only;
     - CH1a: batter + pitcher main effect;
     - CH1: full model.
4. **Same legitimate opportunity:** use the complete-case intersection of the population. On every slate date, both models select their top-K by P(≥1 hit) from the same eligible set, with identical K. Ties break by player id.
5. **Primary metric**
   - Realized Hits-1+ rate of each model's top-K per date, **K = 10 primary**; K = 5 and K = 20 are reported. Comparison is paired by date.
   - Difference CH1 − CH0, with a 95% date-block bootstrap CI (2,000 reps).
   - **Development verdict:**
     - **IMPROVES** iff diff ≥ +2.0 pp and the CI lower bound > 0;
     - **NO_GAIN** iff the CI includes 0 and |diff| < 1.0 pp;
     - otherwise **INCONCLUSIVE**.
6. **Secondary diagnostics**
   - Logloss and Brier on the full population; calibration deciles.
   - Subgroups: batter hand, starter share.
   - **Market subset:** batter-games with a captured FanDuel *Over 0.5 Hits* pregame price (`data/props` snapshots taken before first pitch):
     - logistic `outcome ~ market_logit + (model_logit − market_logit)`;
     - equal-volume top-K within the subset;
     - disagreement deciles.
   - The market is treated as a strong prior; disagreement is not assumed to be edge.
7. **Leakage / point-in-time**
   - Features at date D use only pitches with `game_date < D`.
   - Coefficients and shrinkage come from 2025 only. Nothing is tuned on 2026 outcomes.
   - Usage and arsenal come only from prior games.
   - Lineups and starters are treated as pregame-known (stated as optimistic versus production's projected lineups).
   - Prices are used only from snapshots with `taken_at` < first pitch.
8. **Data identity**
   - Baseball Savant Statcast pitch-level CSVs, one file per day, with a sha256 manifest and fetch timestamps.
   - The experiment code's commit SHA.
   - Champion functions from main `8b68985234`.
   - FanDuel snapshots from `data/props` at the recorded main SHA.
9. **Evidence artifact:** `research/mlb_engine/fc_mlb_002/` on branch `claude/mlb-engine-interaction-20261002`, containing:
   - `RESULTS.json` and `REPORT.md`;
   - gzipped per-row predictions;
   - the data manifest;
   - all labelled `DEVELOPMENT_EVIDENCE`.
10. **Stop condition**
    - **One** evaluation run against these frozen criteria. No iteration on 2026 outcomes.
    - **IMPROVES:** propose a prospective shadow (new task; SUPERCHAD/Jacob).
    - **NO_GAIN or INCONCLUSIVE:** record it as a negative or inconclusive result. The next challenger is chosen from the diagnostics, without refitting on 2026.
    - **Never:** production, selector, pick or ledger change, or promotion.

## BUILDER_NOTES
(owner only — not given to the challenger)

**Champion weakness, verified in code at `8b68985234`:**
- For Hits 1+, `_batter_options` computes `0.5·league + 0.5·Binom(project_batter_pa(slot, total), raw season hits/PA)`.
- The opposing pitcher, his arsenal, handedness, the bullpen and the park never enter the per-PA probability. The `_keep_options` docstring itself says "blind to the opposing starter".
- Matchup facts reach only the generic `score`.
- The calibrator is monotone, so the within-slate ranking is driven only by the batter's own hit rate × slot.

**Data available:**
- Statcast pitch-level CSV from Savant, fetched per day for 2025 and 2026 regular season. It includes pitch_type, velocity, pfx, release, extension, plate location, count, bat speed and swing length, `n_thruorder`, and days of rest.
- `backtest/engine.py` already has a point-in-time Statcast rebuild, but it is not used here, to keep the experiment small.
- FanDuel one-sided prop snapshots in `data/props` (2026-08-06 → 10-02, about 6 per day, with `taken_at` and `start_time`): hits needs=1 rows run to roughly 700 per slate. Two-sided snapshots cover pitcher markets only.
- `data/odds` holds game lines (from 08-05), which could feed implied totals; deliberately not used, identically for both models.
- Grades and picks history covers only the selected board (~95 candidates/day), so it is too thin and selection-biased to serve as the population.

**Data missing / not used in stage 1:**
- Historical pregame lineups: actual lineups are used instead (optimistic, applies to both models).
- Historical implied totals before 08-05.
- Park and weather at PA level.
- Pitch shape clusters: families only in stage 1.
- Count-state usage.
- Two-sided hits prices (one-sided, devigged with production's assumed hold).
- Hits prices before 2026-08-06.

**Builder read of the result (do not give to challenger):**
- The gain comes from the pitcher main effect (CH1a ≥ CH1 at every K). Pitch-family interaction ≈ 0, which is consistent with the 2025 holdout.
- K=5 +5.5 [+1.5, +9.5] and K=20 +2.0 [+0.4, +3.6], but K=10's CI touches 0. Halves: −0.4 (Apr–Jun) vs +4.8 (Jul–Sep), so unstable.
- Logloss is worse than CH0 because the identical 0.5 league shrink compresses CH1 (bottom decile 0.57 predicted vs 0.48 actual).
- Market: FanDuel fair beats both on logloss. Within the market subset, CH1's top-5 ≈ the market's top-5 (72.5 vs 72.1); CH0's top-5 is 68.3.
- The disagreement coefficient is confounded by the market's own under-dispersion (market coefficient ~2).
- **Next candidates (new criteria needed):**
  - (a) CH1a pitcher-main-effect plus removal of the 0.5 league shrink (the champion mapping defect);
  - (b) market-anchored model (market prior + model residual);
  - (c) shape-level interaction only if (a)/(b) leave residual signal.

**Design choices to flag for the challenger:**
- Pitch families FB/BR/OS rather than shape.
- `w_sp` uses the starter's BF/start, shrunk with 3 pseudo-starts (a mechanical expectation, not fitted).
- Bullpen handedness uses the league same-hand share of relief PAs.

## LOG
- 2026-10-02 minimum repair of Codex findings 1-4 (Jacob-authorized); single frozen 2026 rerun; head b52ac418ba; READY_FOR_CHALLENGE (delta-only).
- 2026-10-02 frozen fit committed f48f87dea7 (sha 6c0ecde2…); ONE 2026 evaluation run -> INCONCLUSIVE at K=10 (+2.33pp, CI -0.19..+4.91). Head cc3ff960ccaad178023d4e92fa7940bdfe446ea3.
- 2026-10-02 acceptance criteria written and frozen at claim; FC-MLB-002 claimed (CLAUDE_ACTIVE).

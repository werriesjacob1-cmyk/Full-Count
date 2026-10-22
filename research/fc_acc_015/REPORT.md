# FC-ACC-015 — adversarial validation of the FC-ACC-014 algorithm tournament (2026-10-22)

**Bottom line**
- **Reproduction:** FC-ACC-014 reproduces **byte-for-byte** from a fresh data retrieval.
- **Leakage:** no confirmed leakage was found, and the outcome-scramble test is clean.
- **Gains survive conservative restrictions:** for hits and total bases, most of the matched-count gain survives removing every same-day input with hindsight risk.
- **But three things limit what the result means:**
  1. it is a **deep-board** result (top 5% ≈ 10–15 picks per day), not a result at production's actual Top Pick volume;
  2. production's real publication policy (uncapped, price-gated) cannot be reconstructed;
  3. every season is previously exposed.

**Recommendations**

| Market | Frozen-rule outcome (PLAN §7) | Auditor recommendation |
|---|---|---|
| Batter 1+ hits | GO | **GO, to a forward shadow only.** The 2026 margin is thin, and no gain is established at ~1 pick/day. |
| Batter 2+ total bases | GO | **GO, to a forward shadow.** It is the most robust market, holding under every restriction and at 1 pick/day. |
| Pitcher 4+/5+/6+ K | GO (K3 at 3/3) | **MODIFY.** This explicitly departs from the frozen rule (reasons in §7). The K3 contract is preserved as a research arm. |

Nothing here is ROI, a production improvement, or a promotion. No authentic historical offers exist.

## 1. Identities and reproduction

| Object | Identity |
|---|---|
| PR #233 head | `c3f3362d67da823ee492ad361017e1e2a4a065a9` |
| FC-ACC-014 frozen design / runner | `d2185cb4160e02b3e2feb9a626121c871103cc72` / `ff03d6a8096751c37b0daee3d653d69825a862e6` |
| FC-ACC-015 frozen audit plan (before any variant was scored) | `1650dadfa29e62d06ceada998e63d775eef915ea` |
| Canonical-v2 artifact 9776400325 | zip `00f04cc42273ca820ca8c6acb656690db2608e8e887edcea2d4a08156534b66c`; `rows.jsonl` `8ca010641d08008044c8c3b609162d6e5d69f07bb79be6705b2690a51ab2cb34` (re-verified) |
| FC-ACC-014 code sha256 | `tournament.py` `f2587649…`, `features.py` `52493ed3…`, `data.py` `8d0dba73…`, `fc013_build.py` `c3617153…` |
| StatsAPI re-retrieval 2026-10-22 | all **210/210 raw files byte-identical** to the FC-ACC-014 manifest; normalized `batter_games` `0bc0e364…`, `pitcher_starts` `4c015d5e…`, `schedule` `14900a2f…`, `people` `c158e418…` (identical) |
| Rebuilt features | hits `6f3dd141…`, TB `460c40cd…`, K `2f58ace9…`: **identical** to the committed manifest |
| Rebuilt FC-ACC-014 report | `c9685d7015b4d3b73a2b81a74ae8f24d98c7315184023c700c107a0bc86168aa`: **identical** to the committed report |
| FC-ACC-015 outputs | `out/audit_report.json` `c90c2f5c…`, `out/frozen_models.json` `07115a54…`, both identical across two runs (second run with `PYTHONHASHSEED=7`) |

- **V0 check:** V0 inside the audit runner reproduces FC-ACC-014 M2 exactly:
  - hits: Δ log loss −.00405 / −.00492, equal-N net +126 / +55;
  - total bases: +99 / +145;
  - K: +37/+16, +49/+17, +34/+15.
- **Bootstrap streams:** FC-ACC-015's intervals use a fresh RNG stream (same seed), so CIs differ from FC-ACC-014's in the last digit. This exposed a fragility: **K 4+ fold-2 V0 is [1, 32] in FC-ACC-014 and [0, 31] here**, so FC-ACC-014's "GO at 4+" sat on the bootstrap noise floor.

## 2. Decision-time availability matrix (every M2 input)

A = strictly pregame · B = plausibly pregame, no timestamp evidence · C = retrospectively reconstructed, hindsight risk · D = confirmed leakage.

| Input (batter M2 unless noted) | Source | Class | Evidence / note |
|---|---|---|---|
| `skill_*` season-to-date and prior-season rates, `skill_logpa` | StatsAPI game logs, dates < D | **A** | Outcome-scramble invariant. Same-date doubleheader game 1 is excluded, which is conservative. |
| `rec_*` (7/14-day form, games in the last 7 days) | game logs, dates < D | **A** | Scramble invariant. |
| `opp_team_rpg`, `lin_team_hit` | team game totals, dates < D | **A** | Scramble invariant. |
| `opp_home`, venue, `park_hit`, `park_tb`, K `park_k` | schedule identity plus strictly-prior venue totals | **A** | Home and venue are fixed by the schedule days ahead. |
| `match_switch` (batter handedness) | `/people` current state | **A** | Handedness is effectively static. |
| `opp_slot` | canonical `lineup_slot` (engine `fetch_lineups(date)`, retrospective StatsAPI lineup) | **C** | No historical lineup-confirmation timestamps exist. Slot equals the schedule starting-lineup index for 119,937/119,937 matched rows; 1,617 rows (1.3%) are not in the re-fetched schedule lineup (unexplained). |
| `sig_woba_behind`, `sig_woba_ahead` | engine, from the retrospective lineup neighbours | **C** | Same lineup provenance. |
| `opp_sp_*` (opposing-starter strictly-prior rates) | stats **A**; identity from schedule `probablePitcher` | **C** | The schedule "probable" equals the **actual** starter in **14,549 / 14,565 (99.89%)** decided 2024–26 games (16 mismatches, 9 missing). That is far tighter than the pregame announcement-change rate, which points to post-game resolution. |
| `match_same_hand`, `sig_platoon`, `sig_sp_era_weak`, `sig_pitch_exploit` | depend on the opposing-starter identity | **C** | As above. |
| `wx_temp`, `wx_wind_mph`, `wx_wind_out`, `wx_wind_in`, `wx_roof` | StatsAPI game-time weather; roof from `condition` | **C** | A game-time observation, not a pregame forecast. Roof state is decided near first pitch. |
| `sig_l7_*`, `sig_season_barrel_pct`, `sig_iso`, `sig_platoon_*`, `sig_bat_speed_trend` | engine, Statcast cut-off at D−1 (guarded) | **A** in the replay | Certified by the canonical `--verify` path. Not covered by FC-ACC-015's scramble test, which regenerates only the StatsAPI features. |
| `sig_park_hand_index`, `sig_pull_park_synergy` | engine, Statcast < D | **A** | As above. |
| `sig_days_rest`, `sig_series_game`, `sig_getaway_day`, `sig_bullpen_fatigue`, `sig_bullpen_era_diff`, `sig_opp_catcher_framing` | engine (asof = D−1), schedule | **A**; series game number **B** | The series number can shift after postponements. |
| `base_logit` (production `predicted_prob`) | canonical replay `raw_hit_probability` | **C** | It embeds the retrospective lineup slot, starter identity and **archive-observed** weather (Open-Meteo, documented in `engine.py`). The M1 baseline shares the same inputs. |
| K `skill_*`, `work_*`, `rec_k3`, `base_logit{t}` (replica A_t) | pitcher game logs, dates < D | **A** given the starter identity | Scramble invariant (119 target-date K rows). |
| K `match_lineup_k`, `match_lineup_n`, `match_same_hand` | opponent **actual** starting lineup | **C** | Lineup provenance as above. |
| K `match_team_k` | opponent team, strictly-prior K/PA | **A** | |
| K `wx_temp`, `wx_roof` | game-time weather | **C** | |
| **K universe** | actual starters (`gamesStarted == 1`) with ≥ 3 prior same-season starts | **C** | Hindsight on who started, which includes openers. Pitcher props on a scratched starter are normally voided, which limits the harm. Shared by both arms. |
| **Batter universe** | canonical replay: every batter in the retrospective starting lineup | **C** | Shared by both arms. A late scratch never enters. |

**D (confirmed leakage): none found.**
- **Outcome-scramble test** (`leak_scramble.py`, `out/leak_scramble.json`): on 6 fixed dates, every outcome statistic of every game on or after the target date was randomised (up to 88,909 batter games and 8,422 starts per date). All **2,520 batter-row** and **119 strikeout-row** target-date feature vectors and M3 ingredients stayed **bit-identical**.
- **First-run defect (disclosed):** the first run's date filter silently extracted 0 batter rows; it was caught by the row-count check and fixed before the reported run.

## 3. Leakage-sensitivity results (M2 refit with inputs removed; λ, folds and selection unchanged)

Equal-N = top 5% per date (batters) or top 10% (K), versus the production order. Net = extra winners. The first CI is date-clustered, the second player-clustered.

| Market / variant | Δ log loss 2025 [CI] | Δ log loss 2026 [CI] | Net 2025 [date] [player] | Net 2026 [date] [player] | Retained (2025 / 2026) |
|---|---|---|---|---|---|
| **Hits V0** original | −.00405 [−.0053, −.0028] | −.00492 [−.0061, −.0038] | +126 [74,181] [76,180] | +55 [7,103] [7,104] | 1.00 / 1.00 |
| Hits V1 no weather | −.00420 | −.00472 | +128 [76,182] [78,184] | +52 [3,101] [2,106] | 1.02 / 0.95 |
| Hits V2 no starter identity | −.00394 | −.00459 | +96 [41,150] | +58 [11,104] | 0.76 / 1.05 |
| Hits V3 no slot | −.00272 | −.00374 | +117 [67,172] | +52 [5,103] [−1,109] | 0.93 / 0.95 |
| Hits V4 prior slot | −.00332 | −.00444 | +121 [69,176] | +61 [10,112] | 0.96 / 1.11 |
| **Hits V5 strict same-day** | −.00285 [−.0039, −.0019] | −.00330 [−.0044, −.0022] | **+78 [29,126] [18,141]** | **+55 [4,105] [5,107]** | 0.62 / 1.00 |
| Hits V6 strict, no BASE | −.00293 | −.00325 | +88 [41,138] | +36 [−11,87] | 0.70 / 0.65 |
| Hits V7 fidelity (no run-environment proxies) | −.00368 | −.00411 | +87 [35,141] | +44 [−3,91] | 0.69 / 0.80 |
| **TB V0** original | −.00482 [−.0063, −.0034] | −.00504 [−.0063, −.0037] | +99 [42,153] [42,157] | +145 [94,199] [96,195] | 1.00 / 1.00 |
| TB V1 no weather | −.00469 | −.00472 | +107 [48,167] [53,161] | +119 [68,173] [70,169] | 1.08 / 0.82 |
| TB V2 no starter identity | −.00453 | −.00464 | +74 [14,133] | +132 [81,182] | 0.75 / 0.91 |
| TB V3 no slot | −.00372 | −.00383 | +129 [72,186] | +103 [52,154] | 1.30 / 0.71 |
| TB V4 prior slot | −.00446 | −.00450 | +119 [60,176] | +131 [79,183] | 1.20 / 0.90 |
| **TB V5 strict same-day** | −.00319 [−.0042, −.0022] | −.00313 [−.0043, −.0020] | **+81 [29,137] [30,136]** | **+122 [73,171] [76,172]** | 0.82 / 0.84 |
| TB V6 strict, no BASE | −.00330 | −.00303 | +78 [22,131] | +110 [61,160] | 0.79 / 0.76 |
| TB V7 fidelity | −.00432 | −.00401 | +143 [86,197] | +112 [56,167] | 1.44 / 0.77 |
| K4 K0 original / K3 strict | −.0138 / −.0133 | −.0293 / −.0248 | +37 [24,50] / +37 [25,50] | +16 [0,31] / **+20 [6,34] [6,36]** | — / 1.25 |
| K5 K0 / K3 | −.0249 / −.0217 | −.0271 / −.0234 | +49 [35,64] / +48 [33,63] | +17 [0,35] / **+22 [5,40] [4,41]** | — / 1.29 |
| K6 K0 / K3 | −.0249 / −.0190 | −.0210 / −.0179 | +34 [16,51] / +31 [13,48] | +15 [−2,33] / **+22 [4,41] [2,42]** | — / 1.47 |

All other strikeout variants (K1 no weather, K2 no opponent lineup, K4 strict without BASE) are in `out/audit_report.json`. K1 fails 2026 at 5+ and 6+, and K2 fails 2026 at 6+.

**Reading the table**
- **Probability gain.** Every variant in every market keeps a log-loss improvement with a CI excluding 0. Removing all same-day hindsight inputs (V5) keeps 62–70% of the batter log-loss gain.
- **Hits.** The 2026 matched-count gain survives V5 (+55), but the margin is thin everywhere: date-clustered lower bounds run from +3 to +11, and V3's player-clustered bound is −1.
  - When the challenger is also denied `base_logit` (V6, so no hindsight input at all against a baseline that keeps its own) or the run-environment proxies (V7), the 2026 gain is **no longer established**.
- **Total bases.** TB survives every restriction in both seasons. It is the most robust result.
- **Ingredient that matters most.** For batters, the most important hindsight-risk input is the **opposing-starter identity** (V2: hits 2025 retains 76%, TB 75%). Weather adds nothing (V1 ≈ V0). Slot matters to log loss, not to matched wins, and a strictly-prior slot (V4) recovers most of it.
- **Strikeouts.** Removing hindsight inputs did **not** shrink the matched-count gain. The 2026 net rose from +15–17 to +20–22. That rise is noise-sized (4–7 winners on 394 picks), not evidence that the restricted model is better.

## 4. Equal-volume selection audit

- **Population:**
  - hits 121,554 / TB 121,566 graded rows on 555 dates (18–306 per date, median 270); strikeouts 11,681 starts on 506 dates;
  - 0 ungraded rows were dropped (the canonical view holds only graded rows);
  - 0 duplicate (game, player) rows;
  - 819 (hits) / 847 (TB) same-date second games (doubleheaders), which are legitimate separate opportunities.
- **Arm counts:** identical per date for both arms (asserted); net = challenger − baseline = added − removed (asserted).
- **Baseline order:** M1 is monotone in `predicted_prob`, so it reproduces production's ranking. Production ranks by the calibrated probability (Platt, A > 0), which is also monotone.
- **Missing predictions:** none (every row has `predicted_prob`).
- **Ties (`predicted_prob` has 4 decimals; 3,198 distinct values for hits):**
  - the boundary tie block splits on 17 (hits 2025) / 12 (2026) / 22 and 14 (TB) dates; strikeouts have no ties;
  - tie-averaged nets: hits +122.0 / +56.7 (V0), +124.0 / +53.7 (V1); TB +100 / +146.8;
  - 200 random tie-breaks: hits V0 2025 mean +122.4 (min +111), 2026 mean +56.9 (min +45). **Tie handling does not drive the result.**
- **Uncertainty:** the player-clustered CIs (computed with equal counts preserved) agree with the date-clustered ones.
- **Estimator correction (disclosed):** the first FC-ACC-015 run used a naive player bootstrap of per-player net sums. That lets the added and removed counts drift apart, giving meaningless intervals of about ±300. It was replaced, the replacement is unit-tested, and the run was repeated. Point estimates were unaffected.

**Selection depth (diagnostic; not a decision rule). Nets per arm, date-clustered CI:**

| Market (variant) | Top 1/day 2025 | Top 1/day 2026 | Top 2/day 2025 | Top 2/day 2026 | Top 3/day 2025 | Top 3/day 2026 |
|---|---|---|---|---|---|---|
| Hits V0 | +16 [−1,34] | **−2 [−17,13]** | +32 [8,56] | +16 [−4,37] | +50 [21,81] | +21 [−5,45] |
| Hits V1 | +20 [2,38] | **+1 [−13,16]** | +27 [3,51] | +15 [−6,35] | +48 [15,79] | +25 [0,50] |
| TB V0 | +29 [9,48] | +25 [7,43] | +36 [9,64] | +27 [4,50] | +38 [5,72] | +56 [29,83] |
| TB V1 | +24 [4,43] | +28 [10,46] | +22 [−5,50] | +34 [13,58] | +34 [0,68] | +54 [27,80] |
| K4 K3 | +15 [5,25] | +1 [−9,10] | +28 [16,41] | +11 [−2,25] | — | — |
| K5 K3 | +19 [7,31] | +2 [−9,13] | +39 [24,54] | +21 [5,37] | — | — |
| K6 K3 | +19 [6,33] | +8 [−4,21] | +26 [8,44] | +23 [7,40] | — | — |

- **Floor-matched (hits):** N_d = the number of M1 ≥ 0.60 per day, about 160 per day.
  - V0: +178 [99, 261] / +187 [105, 268]; V1: +177 / +163.
  - This volume is far larger than any publication.

**Gap to the real publication policy (why no "equal-volume production victory" can be claimed)**
- **Production's Top Pick population is uncapped.** Every candidate ships that clears **all** of:
  - calibrated p ≥ 0.60;
  - a real evidence grade;
  - a **confirmed** lineup;
  - a **live price** clearing the value test at the pessimistic end of its interval;
  - fresh data (`recommendation.classify_recommendation`).
- **Realised volume** (24 frozen boards, 2026-09-20 → 10-16):
  - **18** hits 1+ Top Picks, about **0.75 per day**;
  - hits 1+ board rows were 743 in all, of which 373 were priced;
  - **no total-bases 2+ rows** at all;
  - **no strikeout Top Picks** selected (status `top_pick` on 11 K4 rows, but none marked `selected_top_pick`).
- **Why it can't be reconstructed:** the evidence-grade and price gates need data that the historical rows don't have (no authentic offers, no historical reliability/CI state). The tournament's 5%-per-day universe (about 10–15 picks per day from the whole lineup) is a **different population and a different selector**.
- **At production-like depth the 2026 hits gain is not established:** top 1 per day is −2 / +1. The total-bases gain persists, but production does not currently publish 2+ TB.

## 5. Production-fidelity audit

- **M0 provenance.**
  - `predicted_prob` = the canonical replay's `raw_hit_probability` (`engine.to_row`): the production `attach_hit_probabilities` output, **including production's empirical + modelled blend**. `emp_batters`/`emp_pitchers` are fetched with `asof = D−1` at the canonical code regime `bbbe4683`.
  - **Correction to FC-ACC-014:** its REPORT/PREREG listed "no empirical blend" as an engine gap. That was wrong. It understated baseline fidelity and does not affect the challenger comparison.
  - `bbbe4683 → 99372287` changes to `engine.py` concern only Statcast column retention and cache validation; `prop_probability.py` and `mlb_sources.py` are unchanged.
- **Calibration and order.** Production applies a per-market Platt calibrator (current hits A = 5.69, B = −3.02). M1 refits the Platt on the training seasons, which is **more** favourable to the baseline than a stale calibrator. Ranking is unchanged either way.
- **Replay gaps that could exaggerate the challenger.**
  - The live path uses **implied team totals** to shift expected PA (0.1006 PA per run). The replay uses the league mean. **V7** removes the challenger's run-environment proxies: hits keeps 69% / 80% of the net, and its 2026 gain is no longer established; TB keeps 144% / 77%. That bounds the implied-total concern for hits.
  - Also absent from the replay: wRC+, Stuff+ and umpire inputs, a constant `cat_environment`, and the live-only signal-weight adjustment. These are not part of the probability path.
  - Replay weather is archive-*observed*; live is a forecast.
- **Strikeout baseline.**
  - The FC-ACC-013 replica A matches production's shipped formula: `0.5·league + 0.5·Binom(E[BF], k_rate)`, basis `modelled_shrunk`, with no opponent-lineup term and the empirical blend unused for strikeouts.
  - The k_rate source differs: the replica uses exp-weighted game-log K/BF; production uses Statcast-derived `exp_k`.
  - The K baseline is a **reconstruction, not replayed production output**; this is weaker fidelity than for batters.
- **Probability vs selector.**
  - Within a fixed top-N selector, every equal-N gain is pure re-ranking (AUC: hits .547 → .569 / .540 → .567; TB .549 → .575 / .545 → .572).
  - The log-loss gain is beyond calibration, since M1 already recalibrates M0.
  - No result here measures production's real selector: floor, evidence grade, lineup gate, best-of-category, price.

## 6. Test-suite reconciliation (5 files FC-ACC-014 reported failing)

Run with the pinned `requirements.txt` in a venv. Outputs are byte-identical apart from file paths across main `99372287` (FC-ACC-014's base), branch `c3f3362d` and current main `466f7335`. The GitHub Actions run on PR #233 (run 39828749552) fails on exactly these 5 files.

| File | Main 99372287 | Branch c3f3362d | Cause | Class |
|---|---|---|---|---|
| `test_board_first_paint.py` | FAIL | FAIL (identical) | needs a priced prop in `docs/data.json`; the board has **0 props** at 2026-10-22 (172 on 2026-10-01) | inherited; generated-state / off-season dependent |
| `test_fail_closed_surfaces.py` | FAIL (`IndexError`) | FAIL (identical) | `PAYLOAD["props"][0]` on the empty board | inherited; generated-state dependent |
| `test_route_fail_closed.py` | FAIL (`IndexError`) | FAIL (identical) | same | inherited; generated-state dependent |
| `test_h1_ci_calibration_scale_integrity.py` | FAIL | FAIL (identical) | pins strikeouts Platt (0.8433, −0.1259); the weekly `Calibration recheck` bot rewrote it (2026-10-05 `8a590152` → 1.2238/−0.3589; 2026-10-12 `34382f8a` → 1.3746/−0.5084) | inherited; generated-artifact drift against a pinned test |
| `test_strikeout_alternatives_stat_fix.py` | FAIL | FAIL (identical) | pins the strikeouts support region [0.55, 0.80); the same recheck made it [0.55, 0.75) | inherited; generated-artifact drift |

- **Last green run on main:** main's last Test Suite run was 2026-10-01 (`04403c95`, success), when the calibrator still held the pinned values.
- **Why main has no run for these commits:** later commits on main are bot pushes and do not trigger the suite, so main itself never ran on `99372287`.
- **Status:** none of the five is introduced by FC-ACC-014 or FC-ACC-015 (research files only). All five remain **unresolved**. They are production test and generated-state ownership issues, outside this research scope, and are reported rather than fixed.
- **Process failure (disclosed):** FC-ACC-014's draft PR CI was red and was not followed up at the time. Its claim that the 5 failures also fail on main was an inference; it is now demonstrated.

## 7. Verdicts and the strikeout departure

**Hits 1+ (frozen rule GO; recommendation GO to a forward shadow only)**
- V1 meets the FC-ACC-014 GO rule; V5 holds; the tie-averaged net is positive.
- But:
  - the 2026 lower bounds run from −1 (V3, player-clustered) to +11;
  - V6 and V7 lose the 2026 gain;
  - at ~1 pick/day the 2026 gain is −2 / +1.
- The forward test must be judged at a production-like count, not at 5%.

**Total bases 2+ (frozen rule GO; recommendation GO to a forward shadow)**
- It survives every restriction in both seasons, at both clusterings and at top-1 per day.
- Production does not publish 2+ TB today, so the forward test is research-only.

**Strikeouts (frozen rule GO; recommendation MODIFY, an explicit departure)**
- Under PLAN §7, K3 meets the GO rule at all three thresholds. I do not consider that the "compelling evidence" Jacob's standard requires:
  1. The fold-2 margins are 4–6 winners above zero on 394 picks. The original K0 4+ result flips between pass and fail purely with the bootstrap stream.
  2. The pass appears only after removal: K1 fails 5+ and 6+, and K2 fails 6+. K3's rise over K0 is noise-sized.
  3. The K universe is retrospective actual starters, and the K baseline is a reconstruction, not replayed production.
  4. At ~1 pick/day, 2026 gains are +1 / +2 / +8 with CIs including 0.
- This conservative override of my own frozen rule is stated, not hidden. SUPERCHAD or Jacob may apply the frozen rule instead; the K3 artifact is preserved either way.

## 8. Frozen forward contracts (preserve; do not refit)

**Model**
- Frozen M2 family, the FC-ACC-014 fold-2 fit (trained on 2024–2025, λ = 1), stored in `out/frozen_models.json` with keys, train medians, missing-indicator list, mean/SD, coefficients, and the transform.
  - **hits:** V1 contract, 49 inputs;
  - **total_bases:** V1 contract, 49 inputs, with its own weights (FC-ACC-014 showed the hits and TB weights differ);
  - **strikeouts_{4,5,6}_RESEARCH_ONLY:** K3 contract, 14 inputs.
- Unit test: the JSON reproduces the fitted predictions exactly.
- Regenerating the artifact requires the reproduction step plus `audit.py`; its sha is recorded above.

**Permitted pregame feature contract (forward shadow)**
1. Inputs are taken from the same code paths, computed at the decision timestamp, and recorded with that timestamp.
2. Slot comes **only** from a lineup confirmed at decision time. When a lineup is unconfirmed, the row is ineligible, matching production's lineup gate. The schedule's retrospective lineup is never used.
3. The opposing starter is the **announced probable at decision time**, with its timestamp. If the starter changes, the row is voided and logged, never re-scored with the actual starter.
4. Weather is not used (V1). The K3 strikeout contract also excludes the opponent's lineup.
5. **Unavailable inputs** take the frozen train median plus the `__missing` indicator, exactly as fitted, never a later or hindsight value. A row with more than the train-time missingness pattern is flagged, not repaired.
6. The baseline arm is production's own calibrated order on the same eligible pool. Both arms take **identical per-slate counts** at production-like depth (pre-declared k per slate), plus a market-only arm.
7. The outcome is settled winners and ROI at **authentic captured offers**. This depends on Codex's quote-integrity / offer-capture work (PR #226 / #230, not touched here).

No variant result was used to refit or reselect any challenger. V1 was fixed as the forward contract in PLAN §7 before scoring.

## 9. Limitations

- Every season is previously exposed, and the auditor built FC-ACC-014.
- Lineup and starter timestamps are absent.
- Universes are retrospective.
- The production publication population is unreconstructable.
- No ROI.
- The scramble test covers the StatsAPI-derived inputs only. The canonical signals and `base_logit` rely on the engine's own point-in-time certification.
- 1.3% of batter rows are not found in the re-fetched schedule lineup (unexplained, shared by both arms).
- The late-season frozen-board sample (24 boards) is small and includes the postseason.

## Reproduce

```bash
# from a checkout of this branch; canonical rows from artifact 9776400325
python3 research/fc_acc_014/data.py
python3 research/fc_acc_014/features.py <rows.jsonl>
python3 research/fc_acc_014/tournament.py               # -> sha c9685d70...
python3 research/fc_acc_015/leak_scramble.py research/fc_acc_014 <rows.jsonl> <workdir>
python3 research/fc_acc_015/audit.py                    # -> out/audit_report.json c90c2f5c...
python3 research/fc_acc_015/test_fc_acc_015.py
```

Alligator

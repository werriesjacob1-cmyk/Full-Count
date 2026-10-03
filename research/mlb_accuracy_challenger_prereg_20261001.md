# Preregistration: MLB accuracy challenger program, v1 (2026-10-01)

Branch `claude/mlb-accuracy-challenger-prereg-20261001`. This document is committed **before** any evaluation slate exists. The commit that adds it is the evidence boundary.

**Research only.** Nothing here changes the production model, selector, calibration, Top Picks, the public ledger or any customer surface. Promotion of anything requires Jacob's explicit, separate decision.

## 1. Research question
At the **same legitimate usable pick volume**, and drawing from the same eligible candidates in the same price band, does a selector driven by FULL COUNT's information *beyond the posted price* (C2) pick more winners than the current production Top Pick selector (the champion)?

## 2. What is already known (disclosed; this is hypothesis generation, not evidence)
**PR #219 (exploratory).** Forward-chained over the 2026 regular season (2026-08-04 to 09-27; 3,141 out-of-sample rows; 207 games):
- A price-only recalibration beat the raw model by −0.0082 log loss, 95% CI [−0.0152, −0.0019].
- Model + price added +0.0006 over price alone, CI [−0.0008, +0.0020].
- Top Picks: n=75, stated 64.2%, realized 53.3%.
- Pitcher outs was the one family where model + price helped.
- Combined starter strikeouts ran about 0.7 strikeouts high and too narrow.

**Reproduction (this branch, `exploratory/`).**
- #219's four scripts regenerate their reports byte-identically at #219's head `68fa5d0638`.
- An independent reimplementation reproduces every headline number exactly.

**Equal-volume diagnosis (this branch, exploratory, development outcomes).** 75 picks on 23 slates:
- Every selector's hit rate roughly equals the mean implied probability of what it picks. Raw hit rate can be "won" just by choosing favorites. Hence the band and the chalk guard in §9.
- Within the 0.40–0.70 band:

  | Arm | Hit rate | Mean posted q | ROI |
  |---|---|---|---|
  | Residual selector | 61.3% | 0.561 | +10.6% |
  | Champion | 53.3% | 0.549 | −2.7% |

  The CIs overlap heavily. **This informed choosing C2 as the primary challenger.** It is not evidence for it.

**Other prior use of 2026 outcomes:**
- `research/pitcher_outs_shrinkage_prior_experiment.py` (2026-09-20) scored 2026 pitcher-outs starts after a mid-season cutoff, without prices.
- While inventorying data for this program, the aggregate grade counts of the 2026-09-29 postseason frozen board were printed: 30 hit, 81 miss, 32 ungraded. No arm was scored on them. That slate predates the boundary and is excluded anyway.

## 3. Evidence classes (never mixed)
1. **Canonical historical model data:** 2026 regular season. Development only.
2. **Prospective full-candidate data:** frozen full boards (`output/board_freeze_{date}.json` plus the linked graded file), sealed **after the boundary**. This is the only class this program evaluates.
3. **Immutable public Top Pick ledger:** never modified, never re-scored here.

## 4. Clean evaluation availability
**NO CLEAN HISTORICAL CONFIRMATORY HOLDOUT EXISTS.**
- Every 2026 regular-season priced row was used by #219 or earlier work.
- No observed historical prices exist before 2026-08-04; the July backtest regenerated candidates without the prices actually posted.
- Postseason slates before the boundary are excluded.

Evidence must therefore be **prospective**.

## 5. Champion
- The production selector as it actually ran: frozen-board records whose `selector.recommendation_status == "top_pick"`.
- It is not re-run or re-implemented.
- **Model-version rule.** The challengers' coefficients describe production model version `2026.08.15`. Only records with that `provenance.model_version` are eligible.
  - If production is re-versioned, later slates fall out of the universe.
  - If that leaves the minimum unmet, the verdict is INSUFFICIENT_N. Continuing would need a new preregistration.

## 6. Challengers (frozen in commit `ccec326158`)
Per family (hits, hits_runs_rbis, strikeouts, pitcher_outs, other; other includes combined strikeouts), with q = posted implied probability and p0 = production `hit_probability`:
- **C0 market:** p1 = logistic(a + c·logit q).
- **C1 calibrated model:** pcal = logistic(a + b·logit p0).
- **C2 market + model:** p2 = logistic(a + b·logit p0 + c·logit q).
- **C3 pitcher outs:** the C2 model restricted to pitcher_outs, tested on proper score (§12). It is not broadened to other markets.

**Fit details:**
- Fitted once, by `fit_frozen.py`, on all development rows (3,984).
- L2 1.0 on the slopes; clip 1e-4.
- Pooled fallback for any family with fewer than 50 rows.
- Stored in `frozen_coefficients.json`. **Never refitted** during the evaluation.

**Price handling (§11):**
- q is the **raw** posted implied probability. The intercept absorbs the average hold.
- No opposite side is invented.
- Two-sided de-vig is not used, because frozen records do not carry the opposite side consistently.

## 7. Market families
- **Included:** every family present on the frozen board that passes §8.
- **Excluded:** none by rule.
- **Combined starter strikeouts.** This is a red-flag family. Its out-of-sample rows sit in "other" and are reported in the family breakdown.
  - The proposal to exclude it from production Top Picks, pending new evidence, is a **prospective production recommendation for Jacob**. It is not applied here and never retroactively.

## 8. Common eligible universe (per slate)
A frozen-board record is eligible when all of the following hold:
1. `eligibility.qc_status == "kept"`;
2. the lineup is confirmed (`lineup_assumed` is false);
3. a real posted `market_odds` exists (the price observed by the pipeline at board generation);
4. `prediction.hit_probability` exists;
5. a non-null `recommendation_status` exists;
6. `model_version == 2026.08.15`;
7. the posted implied probability is in **[0.40, 0.70]**.

The band is the range of development champion prices: 0.420–0.667, 5th–95th percentile 0.431–0.649.

**Linkage:** the board must rebuild its own canonical hash, and the graded file's `source_board_sha256` must equal it.

**Champion picks outside the band** are counted and reported, but not compared.

## 9. Selector and pick-volume rule (equal volume)
- **N_d** = the number of champion picks inside the slate's universe. Each challenger takes **exactly N_d** picks from the same universe.
- **Primary selector C2:** rank by (p2 − p1), the model's information beyond price.
- **Secondary selectors:**
  - C1: rank by (pcal − q);
  - C0: rank by p1;
  - RAW: rank by p0.
- Ties break by candidate id. No backfill. No outcome is used in selection.
- **Equal volume is impossible** on slates where N_d = 0; those slates are counted.

## 10. Timing and cutoff
- **Boundary:** boards whose `sealed_at` is later than **2026-10-01T18:00:00Z**. Boards are sealed at generation, before first pitch, so no outcome can enter the board.
- **Development window:** 2026-08-04 to 09-27. The harness refuses it, and refuses any board sealed before the boundary.

## 11. Market-price requirements
- Only observed posted prices are used.
- Rows without a posted price are ineligible for every arm, champion included.
- No historical price is inferred or reconstructed.

## 12. Evaluation periods and metrics
**Regime A: POSTSEASON_2026_SHADOW**
- Slates sealed after the boundary, through the end of the 2026 World Series.
- **Descriptive only.** It is a structurally different regime (few games, aces, bullpen use). It is reported once after the Series and never pooled.

**Regime B: CONFIRMATORY_2027_REGULAR**
- 2027 regular-season slates.
- **Count-only check on 2027-07-13**, computed from champion pick counts without reading outcomes. If at least 250 champion picks are in the universe over at least 60 slates, the single analysis runs then. Otherwise the single analysis runs on **2027-09-01** with whatever exists.
- No other looks.

**Primary metric:** realized hit rate (wins / picks settled hit or miss) at equal volume, C2 − champion.

**Secondary metrics (descriptive, no claims):**
- C1, C0 and RAW hit rates;
- hit rate minus mean q;
- 1-unit ROI at the posted price;
- the overlap, champion-only and challenger-only tables;
- family breakdown;
- month-by-month stability;
- log loss and Brier score for p0, q, p1, pcal and p2 on all eligible rows;
- supply: eligible rows, picks per slate, zero-pick slates, family diversity.

**Uncertainty:**
- Game-clustered bootstrap, cluster = (date, game_pk), B=2000, seed 20261001.
- Player-clustered sensitivity, same B and seed.

**C3 (pitcher outs):** the mean paired difference LL(p2) − LL(p1) over eligible pitcher_outs rows, with a game-clustered 95% CI.

## 13. Decision rules (locked; implemented in `harness.verdict` / `c3_pitcher_outs`)
**Primary (Regime B):**

| Verdict | Condition |
|---|---|
| INSUFFICIENT_N | Fewer than 250 champion scored picks, or fewer than 60 slates with champion picks |
| REJECTED | C2 hit rate − champion hit rate ≤ 0 |
| SUPPORTED | The one-sided 95% game-clustered lower bound of the difference is > 0, **and** the chalk guard holds: C2 mean q − champion mean q ≤ 0.03 |
| INCONCLUSIVE | A positive difference that fails the bound |
| INCONCLUSIVE_CHALK_GUARD | A positive difference that passes the bound but fails the chalk guard |

**C3:**

| Verdict | Condition |
|---|---|
| INSUFFICIENT_N | Fewer than 150 rows |
| SUPPORTED | The CI upper bound < 0 |
| REJECTED | Mean ≥ 0 |
| INCONCLUSIVE | Otherwise |

A SUPPORTED primary verdict is a **promotion candidate** only. Promotion needs Jacob's explicit decision, and the ROI and supply results must be reported alongside it.

## 14. Provenance
| Item | Value |
|---|---|
| Harness commit | `ccec3261587a2f0e33cdde64b36e9af9070bfcb2` |
| `harness.py` sha256 | `1ef42f1d8621c4b2d537569447bdec4b66724bc2e1e05d2ae6530351629b67a3` |
| `frozen_coefficients.json` sha256 | `3c9e2c01cf4b7c57261622e829a1cccebd88d12b4950a84d7b7b96ad54672009` |
| `fit_frozen.py` sha256 | `3c326f527f2d3520bdc249f727d71e5fc852f40b9a144f70547dda5d339a9789` |
| `test_harness.py` sha256 | `6268c9077f3984c3a08ceae65545b8d524900fe0295449b302ff96ccaabed8ea` (16 tests, mutation-checked) |
| Development data snapshot | `58abe9f2e5a2b69d0f8bc98c22c8bafe7b00a224` |
| #219 head | `68fa5d06384691e4a32b968a92eefb99820f76e1` |
| Main at mission start | `439f727ffe6f4f5b08f05f8e57100ea34779e526` |

## 15. Leakage prohibitions
1. No refit, re-band, re-threshold or reselection after the boundary.
2. No outcome is read before an analysis date. The 2027-07-13 check counts picks only.
3. No development-window slate and no pre-boundary slate enters the evaluation; the harness enforces both.
4. No inferred, closing or consensus price.
5. The public ledger and settled grades are untouched.
6. Postseason results are never pooled with Regime B.
7. Any deviation needs a new, versioned preregistration committed before the affected data exist.

Alligator.

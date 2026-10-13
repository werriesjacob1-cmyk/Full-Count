# FC-ACC-001 preregistration: equal-volume selector competition (2026-10-13)

Committed **before** any challenger result was computed. This file's commit SHA
and sha256 are recorded in Issue #91 and in `REPORT.md`. Nothing below may be
changed after challenger results exist; any later change is a new, labelled
amendment and the original stands.

## 1. Question

On the same date-specific, legitimately eligible, pregame-sealed candidate
pool, does a challenger selector produce **more realized winning picks at the
same pick volume** as the champion, at the genuine frozen sportsbook price?

## 2. Evidence regimes (never pooled)

| Regime | Source | Role here |
|---|---|---|
| A. Public ledger | `data/public_top_picks/registry.json` + retained grades in `docs/history.json` | Champion baseline and hypothesis generation. Hypotheses are drawn **only** from public picks dated **before 2026-09-20**. |
| B. Prospective freezes | `output/board_freeze_2026-*.json` + `board_freeze_graded_*` (2026-09-20 .. 10-12) | Selector competition (§5). |
| C. Historical research | `backtest/rows.jsonl` (gitignored, absent from this checkout); PR #219/#220 reconstructions | Not used for selection claims. #220's exploratory equal-volume results are cited as prior context only. |

## 3. Disclosure of prior exposure (hindsight protection)

- This author previously read Regime B outcomes, while auditing calibration for
  PR #225: candidate-level Brier for strikeouts/pitcher_outs, and aggregate
  main-board (top-10) selection hit counts under two calibration versions.
- No challenger defined below has been evaluated on Regime B.
- Regime B is therefore **exploratory**, not a virgin holdout. No result here
  can promote anything.
- PR #220's V3 preregistration is unaffected. Its only confirmatory regime is
  the 2027 regular season; 2026 postseason is descriptive only there.

## 4. Hypotheses (from Regime A, public picks before 2026-09-20, n=341 settled)

Champion public record in that window: 189/341 (55.4%), ROI −6.1% at the
published price. Mean stated probability was 0.646, mean market-implied 0.578.

- **H1 (market anchoring).** Realized hits track the market price better than
  the model's stated probability. Larger model-over-market disagreement went
  with lower hit rates: +3–6 pp 59.5% (n=200), +6–10 pp 55.0% (n=80),
  +10–15 pp 42.1% (n=38), ≥15 pp 45.5% (n=22).
- **H2 (price floor).** Near-even and plus-money picks lose: odds > +100 hit
  29% (n=17), −110..−139 hit 47% (n=106), versus 61% at −140..−179 (n=168).
- **H3 (evidence depth).** Picks with sample_n < 40 hit 46% (n=97) versus 59%
  at ≥ 80 (n=175).
- **H4 (concentration).** No support in the window: single-pick games hit 49%
  (n=81), 4+-pick games 59% (n=122). Tested only as a falsification arm.

## 5. Competition design (Regime B)

**Slates.** Each board freeze is one slate. Only boards with a graded file are
scored. 2026-10-12 has none and is excluded and reported.

**Champion.** Records with `selector.recommendation_status == "top_pick"`. This
is the policy that produces public Top Picks; 62/62 freeze top_picks appear in
the public ledger on dates where publication worked. **N_d** is the champion
count on slate d.

**Eligible universe U_d** (identical for every arm). Records with all of:
- `market.market_odds` present (a frozen FanDuel price at the freeze);
- `eligibility.qc_status == "kept"`;
- `eligibility.lineup_assumed` not true;
- `prediction.reliability` in {A, B, C};
- `prediction.hit_probability` present.

Every game in a freeze starts after `sealed_at`, which is verified per board.
Champion picks outside U_d are kept for the champion and counted.

**One line per (player, stat, side) per slate per arm.** Within an arm, only
the arm's best-ranked line of each (player, stat, side) can be taken.

**Equal volume.** Each challenger takes exactly N_d from U_d by its key,
descending, with ties broken by candidate_id. If U_d has fewer eligible
entries than N_d, the arm takes all of them, the shortfall is reported, and
that slate is marked NON_COMPARABLE for that arm.

**Arms.** p = shipped `hit_probability`; q = `market_implied` from the frozen
price.

| Arm | Status | Filter in U_d | Rank key | Hypothesis |
|---|---|---|---|---|
| CH1 BLEND | **primary** | none | (p + q) / 2 | H1 |
| CH2 MARKET | secondary benchmark | none | q | H1, pure chalk benchmark |
| CH3 CAPPED_MODEL | secondary | p − q ≤ 0.10 and q ≥ 0.52 | p | H1 + H2 |
| CH4 DEPTH | secondary | sample_n ≥ 40 | p | H3 |
| CH5 ONE_PER_GAME | secondary (falsification) | champion's own records, at most 1 per game, chosen by the highest p | p | H4 |

CH5 can only re-select within the champion's picks, so it can take fewer than
N_d. That shortfall is reported, and CH5 is volume-NON_COMPARABLE by
construction. It is a descriptive check of H4 only.

## 6. Metrics (all with denominators)

Per arm:
- selected, settled (hit/miss), void/ungraded;
- **hits and hit rate** (primary metric);
- flat 1-unit P&L and **ROI at the frozen price**;
- mean q (the chalk guard);
- market mix and daily volume;
- overlap with the champion;
- **winners retained, winners added, losers removed, losers added**.

Results are reported separately for the 2026 regular season (09-20..09-27) and
the 2026 postseason (09-29..10-11) as a stability check.

## 7. Inference

- **Paired difference.** Arm hits − champion hits over all comparable slates.
- **Uncertainty.** A cluster bootstrap that resamples game_pk clusters within
  each slate, keeping each slate's pairing (B = 2000, seed 20261013). A
  slate-level bootstrap is also reported.
- **Primary verdict (CH1 only).**

  | Verdict | Condition |
  |---|---|
  | NO_DEMONSTRATED_IMPROVEMENT | The difference is ≤ 0, or the one-sided 95% lower bound is ≤ 0. |
  | CHALK_GUARD | The lower bound is > 0, but CH1 mean q − champion mean q > 0.03 **and** CH1 ROI ≤ champion ROI. |
  | EXPLORATORY_POSITIVE | The lower bound is > 0 and the chalk guard holds. Still not promotable (§3); it must go to forward shadow. |

- **Secondary arms.** Holm adjustment over CH2–CH4 (one-sided bootstrap
  p-values). Descriptive only.
- **Power.** About 90 champion settled picks. A ±10 pp difference is roughly
  1 SE. The expected outcome is NO_DEMONSTRATED_IMPROVEMENT unless the effect
  is very large. That is acceptable: the point is a correctly-labelled result,
  plus forward-shadow specification.

## 8. Secondary pool-level test (more data, same regime)

On all graded hit/miss records in U_d (every slate):
- fit `hit ~ logit(q) + d`, where d = logit(p) − logit(q), by logistic
  regression;
- report the coefficient on d with a game-clustered bootstrap 95% CI;
- report AUC(p), AUC(q) and AUC((p + q)/2).

H1 predicts that the coefficient on d is ≤ 0, meaning the model's
disagreement carries no positive information beyond the price.

Sensitivity: core markets only (hits, hits_runs_rbis, total_bases, strikeouts,
pitcher_outs).

## 9. Integrity

- Read-only over committed evidence. No price, eligibility, timestamp or label
  is created. Unknown stays unknown and is counted.
- Every input file's sha256 is written into the output.
- No published pick, grade, ledger entry, production code, calibration or
  selector is changed.
- Nothing here is a promotion. Any positive result goes to forward shadow,
  under PR #220-style sealing, before Jacob decides.

Alligator

# FC-ACC-019 — market-wide predictive edge: preregistration

**When:** frozen before the full-spectrum candidate set was built or any new outcome was read.

**Known going in (prior exposure):**
- FC-ACC-017 and FC-ACC-018 results on the published ledger;
- the hits 1+ candidate replication on 8 snapshot dates;
- FC-ACC-018's capped favorite universe.

**Research only:** no model fitting beyond the diagnostic control in §4.

## 1. Evidence regimes (never pooled)

- **R1, published Top Picks:** `data/public_top_picks/registry.json` plus public grades (`results/grades_*.json`) on main. Pregame fields come from the immutable snapshot.
- **R2, retained pregame candidates:**
  - production dashboard rows (`docs/data.json` history on main);
  - for each regular-season game, the **latest pregame state containing that game's rows**: the latest commit at or before `first_pitch − 10 min`, looking back at most 8 hours, with payload `date` = slate date and at least one row for the game;
  - **gates:** priced, side Over, confirmed lineup, payload `odds_fetched_at` no more than 45 minutes before that commit, game not live or final;
  - **markets:** every market gradable from official StatsAPI regular-season logs (hits, hits_runs_rbis, total_bases, singles, doubles, triples, home_runs, runs, rbis, stolen_base, strikeouts, pitcher_outs);
  - **no price cap**;
  - **graded** against StatsAPI logs (void = batter with 0 PA or no log; pitcher without a start);
  - **window:** 2026-08-18 → 09-27.
  - Prices are production-captured, **not authenticated**.
- **R3, canonical retrospective:** frozen FC-ACC-015 `hits` and `total_bases` models, scored on R2 rows that have reproduced FC-ACC-014 features (dates through 2026-08-25 only). No refit.

## 2. Market probabilities (per row; fixes the FC-ACC-017/018 double-devig)

- **raw** = break-even implied from the American price.
- **no-vig:**
  - if the row's `market_fair_method` is `exact_two_sided`, or `market_implied` is not raw (ratio to raw outside 1 ± 0.002), use `market_implied` as recorded;
  - otherwise use raw / 1.08 (production `ASSUMED_PROP_HOLD`), labelled **approximate**.

## 3. Odds bins (fixed; empty or thin bins are reported, not merged)

| Bin | American odds |
|---|---|
| L+ | +200 or longer |
| P | +100 to +199 |
| E | −100 to −149 |
| M | −150 to −199 |
| F | −200 to −299 |
| X | −300 or shorter |

A segment (market × bin, and bin alone) is **supported** if it has ≥ 300 settled rows. Thinner segments are reported descriptively only.

## 4. Tests (R2; R1 descriptive with the same statistics)

1. **Scoring (no fitting):** log loss and Brier of the production model vs raw vs no-vig; calibration (calibration-in-the-large) per segment.
2. **Diagnostic control, the only fitted object:** logistic regression for each supported segment.
   - **Market-only:** `y ~ a + b·logit(no-vig)`.
   - **Market + model:** add the disagreement term `c·(logit(model) − logit(no-vig))`.
   - **Out-of-sample protocol:** fit on slates before the chronological median slate, evaluate on the rest; report Δ log loss (market+model − market-only) with a game-clustered bootstrap CI (2,000 reps, seed 20261025).
   - **c** is also estimated on all rows, with a game-clustered bootstrap CI.
3. **Disagreement deciles:** realized rate minus no-vig, by model − no-vig band.
4. **ROI** at recorded prices per segment, with a game-clustered CI. Labelled **unauthenticated**.
5. **R3:** frozen challenger log loss vs no-vig on its covered rows, with no fitting.

**Credible incremental information in a segment** requires **both:**
- the CI for c is entirely > 0;
- the out-of-sample Δ log loss CI is entirely < 0.

## 5. Substitution and joint hit/ROI diagnostics (R2)

- **Equal-volume operational substitution** is attempted only where decision-time supply fills the champion's N on every slate. FC-ACC-018 and FC-OPS-003 showed this fails, so it is expected to be **not supported**.
- **Hypothetical ranking diagnostic only** (never an operational claim): per slate, fixed k = 3. One per player, at most 2 per game. Rank by:
  - (i) no-vig probability;
  - (ii) model probability;
  - (iii) model − raw break-even.
- Reported for each ranking: hit rate, ROI, odds distribution, coverage, concentration and game-clustered CIs.

## 6. Verdict

- **GO:** at least one supported segment shows credible incremental information **and** an equal-volume substitution is supported.
- **MODIFY:** credible incremental information exists, but no supported substitution.
- **STOP — NO DEMONSTRATED EDGE:** no supported segment shows credible incremental information.

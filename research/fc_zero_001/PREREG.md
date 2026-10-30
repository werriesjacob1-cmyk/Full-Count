# FC-ZERO-001: preregistration of H1, "ladder gap" (frozen before any outcome is examined)

**Status at freeze**
- No outcome for any row in this population has been fetched, joined or inspected for this relation.
- Prices were inspected only to count supply: 16,724 hits-1 / H+R+RBI-1 player-game pairs across 83 files, 2026-08-06 to 2026-10-27.
- Base: `main` @ `599c85bef`.

## Hypothesis (H1)

**Prices compared.** FanDuel's one-sided "Yes" prices for the same player in the same snapshot:
- **H+R+RBI ≥ 1** (`hits_runs_rbis`, needs 1);
- **hits ≥ 1** (`hits`, needs 1).

**The gap.** Hits ≥ 1 implies H+R+RBI ≥ 1. The gap `g = imp(HRR1) − imp(H1)` (raw American implied probabilities) is therefore the book's price for "no hit, but at least one run or RBI".

**H1 states:** among HRR1 offers at the *identical* HRR1 price, `g` carries outcome information that the HRR1 price does not.
- **Why it could exist:** books may build HRR ladders from the hits ladder plus an average run/RBI uplift. That would under-differentiate player-specific non-hit equity, such as walks, lineup slot and run environment.
- **Direction:** not asserted in advance, so the test is two-sided.
- **What is new:** the decision uses only the book's own prices. There is no FULL COUNT model probability, no fitting and no retraining. No prior experiment (FC-ACC-012 to FC-ACC-019, FC-EDGE-001) tested a within-player cross-rung relation against outcomes.

## Population (frozen)

**Rows**
- Source: `data/props/props_2026-*.json` on `main` @ `599c85bef`. These are production FanDuel captures, **not authenticated**.
- **Decision snapshot** for each (event_id, player_norm): the latest snapshot whose `taken_at ≤ start_time − 30 min`, where both the H1 and HRR1 rows are present with integer `american` and `in_play` false.
- Rows without such a snapshot are excluded and counted.

**Splits, by MLB StatsAPI `gameType` of the matched game**
- **P-RS (primary):** regular season (`R`).
- **P-PS (secondary stability check):** postseason (`F`, `D`, `L`, `W`). Games not final at analysis time are excluded and counted.

**Grading**
- Source: the official StatsAPI boxscore of the game matched by date, team names (from the FanDuel `game` string) and start time within 6 hours.
- Players are matched by `odds_fanduel.normalize_name` of the boxscore full name within the matched game's two rosters.
- **Win:** hits + runs + RBI ≥ 1.
- **Void:** the player is absent from the boxscore or has 0 plate appearances. Voids are excluded and counted.
- **Unmatched:** excluded and counted.

## Decision rule (frozen)

1. **Strata:** exact HRR1 American price. Strata with fewer than 6 graded rows are dropped and counted.
2. **Terciles:** within each stratum (computed separately for P-RS and P-PS), rank `g` with tie-averaged percentile `pct = (rank_avg − 0.5) / n`.
   - **LOW:** `pct ≤ 1/3`;
   - **HIGH:** `pct > 2/3`;
   - **MID:** otherwise.
   Terciles use prices only.

## Primary metric and test (frozen)

**Metric.** `D = Σ_s n_s · (hr_LOW,s − hr_HIGH,s) / Σ_s n_s` over strata containing both LOW and HIGH rows. `hr` is the HRR1 win rate and `n_s` is the stratum's graded size. Every compared row has the same price, so D is also a break-even-free ROI contrast.

**Uncertainty.** 4,000 game-cluster bootstrap resamples, seed 20261027, percentile CIs.

**Multiplicity.** Many earlier tests examined overlapping 2026 games. **H1 passes only if the 99% CI of D on P-RS excludes 0.**

## Profitability, volume and stability requirements (frozen)

**Favoured tercile:** LOW if D > 0, HIGH if D < 0.

| Requirement | Condition |
|---|---|
| **ROI** | P-RS flat-stake ROI of the favoured tercile at recorded HRR1 prices has a game-clustered **95% CI lower bound > 0**. |
| **Stability** | The P-PS point estimate of D has the same sign as on P-RS. P-PS is reported regardless. |
| **Volume** | On every P-RS slate that carries published Top Picks (`data/public_top_picks/registry.json`, champion count `N_s`), arm **B** below must fill at least 95% of `Σ N_s`. |

**Equal-volume arms**, on the same slates. All use at most 1 pick per player and at most 2 per game.

| Arm | Selection | Price used |
|---|---|---|
| **A: champion** | The actual published entries and their recorded outcomes | Recorded price |
| **B: challenger** | `N_s` HRR1 rows from the favoured tercile, shortest HRR1 price first; ties broken by larger \|g\| in the favoured direction, then event_id and player_norm | Recorded HRR1 price |
| **C: market reference** | `N_s` HRR1 rows from all strata, shortest HRR1 price first, same tie-break on event_id and player_norm | Recorded HRR1 price |

**Reported for each arm:** wins, losses, added and removed winners (B vs C, B vs A), hit rate, ROI at recorded price, mean raw break-even, and median and range of odds.

## Verdict rule (frozen)

| Verdict | Condition |
|---|---|
| **STOP** | The P-RS 99% CI of D includes 0. |
| **MODIFY** | The CI excludes 0, but any ROI, stability or volume requirement fails, or B's ROI does not exceed C's. |
| **GO, research continuation only** | All requirements pass. |

**Limits on any verdict**
- No threshold, stratum rule, cutoff or split may change after outcomes are joined.
- ROI figures use unauthenticated observed prices and are **not** executable returns.
- A failure is reported as a failure. No rescue experiment follows.

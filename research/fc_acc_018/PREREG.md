# FC-ACC-018 — market-favorite selection: equal-volume falsification (preregistration)

**When:** frozen before any new evaluation outcome was inspected, and before candidate supply was built. Only data **inventories** were checked: which frozen boards exist, which dashboard commits exist, and which StatsAPI fields exist.

**Status:** research only. Aim: try to **falsify** the FC-ACC-017 favorite hypothesis.

## 1. Policies (frozen)

**Common universe U(slate)**, the same for B and C:
- **Rows:** production dashboard rows (`docs/data.json` on main) for every regular-season game of the slate, each taken from that game's **final pregame dashboard state**: the last `docs/data.json` commit at or before `game_start − 10 min` whose payload `date` equals the slate date.
- **Market and line:**
  - markets `hits_runs_rbis`, `hits`, `strikeouts`, `pitcher_outs` (the champion's Top Pick markets), side Over;
  - the line exactly as offered on the row (`projection.needs`, `market_odds`).
- **Price:**
  - priced (`market_odds` present), and price **≥ −350** (production `USER_MAX_PRICE`, applied to both arms);
  - the payload's `odds_fetched_at` is no more than 45 minutes before the commit (production `MAX_PRICE_AGE_SECONDS`).
- **Lineup:** confirmed (`lineup_assumed` false).
- **Status:** row `game_state`, if present, is not live or final.
- **No other gate.** No reliability, value or model rule enters U.

**The three arms**
- **A — actual champion:** the published Top Picks (`data/public_top_picks/registry.json`), with their **public grades** (`results/grades_*.json`). Per slate, `N` = the number of settled picks (hit or miss).
- **B — market-only favorite:** rank U by sportsbook-implied probability (from `market_odds`), descending; ties → canonical id.
- **C — frozen hybrid favorite (FC-ACC-017, unchanged):**
  - devig market (`market_implied / 1.08`) ≥ 0.60, price ≥ −350, model `hit_probability` ≥ 0.60;
  - ranked by model probability, descending; ties → canonical id.
- **B and C constraints:** one pick per player, at most **2 per game**, filled greedily in rank order to the target count. Shortfalls are reported, never filled by relaxing anything.

**Benchmarks**
- **Original:** A uncapped (actual N) vs B and C at target N.
- **Cap-comparable:** A_cap = the champion's picks after applying one-per-player and ≤ 2-per-game, keeping the champion's own higher-probability pick first (ties → id). `N_cap` = |A_cap|; B and C at target `N_cap`.
- **Disclosure:** A_cap is a constructed comparison, never the public record. Picks removed by the cap are reported.

## 2. Volume gate (measured before any outcome is read)

- **Per slate:** candidate counts after each gate, distinct games and players, eligible count, fill and shortfall for B and C at N and at `N_cap`, and concentration (picks per game; share at price ≤ −250).
- **Gate:** an arm passes a benchmark only if Σ filled / Σ target ≥ **0.95** over the evaluation slates.
- **If it fails:** any equal-volume claim for that arm and benchmark is **STOP**. Thresholds, caps and population are never changed to pass.

## 3. Periods and exposure

- **Grading coverage:** regular-season slates with public Top Picks, 2026-08-18 → 2026-09-27 (StatsAPI regular-season logs end 2026-09-27). Postseason slates are excluded because no gradable logs are retained.
- **P_dev:** dates used in FC-ACC-017 strategy development:
  - frozen-board period 2026-09-20 → 10-16;
  - dashboard snapshot dates 2026-08-20, 08-25, 08-30, 09-04, 09-09, 09-15, 09-20, 09-25.
- **P1 (least-exposed):** every graded slate not in P_dev.
- **P2:** all graded slates.
- **No period is independent.** The champion's outcomes on **every** date were examined in FC-ACC-017's ledger analysis, so every result is **exploratory**, never confirmation. P1 is primary only because its *candidate* outcomes are unseen.

## 4. Outcomes and statistics

**Grading (B, C, and a consistency check of A)**
- Official MLB StatsAPI regular-season game logs (FC-ACC-014 raw cache, retrieved 2026-10-22).
- **H+R+RBI:** hits + runs + rbi ≥ needs. **Hits:** hits ≥ needs. **Strikeouts:** pitcher strikeOuts ≥ needs. **Outs:** pitcher outs ≥ needs.
- **Void:** a batter with no log or 0 PA; a pitcher without a start in that game.
- **Consistency check:** A is also re-graded this way and compared with its public grades. Agreement below 98% → grading is not comparable and the verdict is at most MODIFY.

**Reported per arm and benchmark**
- picks, slates, wins, losses, voids, hit rate;
- added and removed picks and their winners, versus A and B versus C;
- same-count differences;
- **market-expected wins** Σ(devig);
- ROI at the recorded dashboard prices, labelled **unauthenticated**;
- stability by month and by market.

**Uncertainty**
- 10,000 bootstrap reps (seed 20261024) of the hit-rate difference.
- Clustered by **slate** and, separately, by **game**. The game bootstrap recomputes each arm's rate from resampled games, so counts are not summed across games.

## 5. Frozen verdict rules (the primary analysis is P1, cap-comparable, C vs A_cap)

- **STOP** if C fails the volume gate at `N_cap` on P1, **or** the point estimate of the P1 hit-rate difference (C − A_cap) is ≤ 0.
- **GO** requires all of:
  - C passes the volume gate (P1, `N_cap`);
  - P1 hit-rate difference lower bounds > 0 under **both** slate and game clustering;
  - ROI(C) ≥ ROI(A_cap) − 0.02 at recorded prices;
  - P2 has the same sign;
  - grading consistency ≥ 98%.
- **MODIFY:** otherwise.
- **Model alpha** is claimed only if C − B (P1, same target, same constraints) has a slate- **and** game-clustered lower bound > 0. Favorites winning more than the champion is **not** model alpha.

## 6. Not allowed

Tuning any threshold, cap, period or market set after outcomes; re-running variants and reporting the best; inventing offers; using a later snapshot's price for an earlier decision.

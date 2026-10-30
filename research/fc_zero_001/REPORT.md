# FC-ZERO-001: the $0 profitability challenge

## Verdict: **STOP**

**No $0 avenue meets the minimum criteria.**
- The one materially new, testable hypothesis, H1 "ladder gap", was preregistered before any outcome was fetched. It **failed its primary test**.
- Every other free avenue is one of three things:
  - already falsified;
  - already exposed to prior tests;
  - not testable at $0 without live capture, which is not authorized.

**Identities**
- Base: `main` @ `599c85bef`.
- Preregistration: **`96652d3ad30ea1cff110d1a0d503cab876df72f2`** (`PREREG.md`).
- Evaluation: `ladder_gap.py` → `out/ladder_gap.json`.
- Regression check: `test_fc_zero_001_ladder_gap.py`.
- Spend: $0. Prices: production FanDuel captures, **not authenticated**. Outcomes: official StatsAPI boxscores.

## A. What free resources do we actually have?

Columns: **Data** is what is actually available. **Coverage** is how far back it goes. **Timing defensible** asks whether we can show what was known before the game. **Gaps** are missing or unreliable fields. **Free and permissible** covers licensing. **Info beyond price** asks whether it tells us something FanDuel's price does not already reflect.

| Resource | Data | Coverage | Timing defensible | Gaps | Free and permissible | Info beyond price |
|---|---|---|---|---|---|---|
| FanDuel prop snapshots (`data/props/`, 1.7 GB, production capture via `odds_fanduel.py`) | One-sided "Yes" ladders for hits, TB, HR, H+R+RBI, runs, RBI, singles, doubles, triples and SB, plus a two-sided over/under snapshot (`true_over`, `hold`) for K and outs | 83 files, 2026-08-06 → 10-27, about 7 snapshots per day | Yes (`taken_at`) | Not authenticated; one book; no "No" side on ladders; irregular snapshot times | Free. FanDuel terms reportedly prohibit automated access; this is existing production capture, and nothing new is added. | **It is the price.** It can only show the book's internal relations (H1 below). |
| Game lines (`data/odds/`, Action Network `bookIds=15`) | FanDuel moneyline, spread, total and team totals; tickets % and money % | 77 files, 08-05 → 10-27 | Yes | **Single book (FanDuel).** It is not a consensus. | Undocumented web API, already in production | Already consumed: `line_movement`, `implied_total`, and the sharp-money split in `generate_picks.py`. The model using them still loses to the market (FC-ACC-019). |
| Production board history (`docs/data.json` commits) | Every candidate with model p, market odds, `lineup_assumed`, slot and `odds_fetched_at` | 2026-08-18 onward (R2 in FC-ACC-019) | Yes (commit time) | Prices not authenticated | Own data | Fully examined in FC-ACC-017 to 019 and FC-EDGE-001 |
| MLB StatsAPI (schedule, lineups, boxscores, game logs, transactions) | Probable pitchers, posted lineups, official results, IL moves | Multiple seasons | Only our own polling times; there is no posting timestamp | — | Free. Responses carry MLB's notice restricting use to personal, non-commercial purposes. | Public to books at the same moment. Lineups were repriced within our roughly 10-minute cadence (FC-EDGE-001). |
| Statcast / Savant via `pybaseball` | Pitch-level data, xStats, barrels, arsenals, sprint speed | 2015 onward | Yes (game date) | — | Free and public | Already in the model features. The model has no edge over the market. |
| Open-Meteo forecast (`generate_picks.py:302`) | Hourly forecast at the park | Current; free historical-forecast archive | Forecast issue time | We do not store revisions | Free for non-commercial use | Public, and books price weather. Untested. |
| UmpScorecards (`generate_picks.py:344`) | Umpire tendencies | Multiple seasons | Assignment is announced on game day | — | Free | Small effect on K. Untested in isolation. |
| Bullpen and workload (`bullpen_fetch`, StatsAPI logs) | Recent reliever pitch counts and starter workload | Multiple seasons | Yes | — | Free | In the model already; pitcher picks underperformed even the market (FC-ACC-019). |
| Frozen models and prior artifacts | FC-ACC-015 hits and TB frozen models; FC-ACC-019 R1/R2/R3 | — | — | — | Own data | Neither frozen challenger beats the market (FC-ACC-019 R3). |

**What the inventory shows**
- Every feature input FULL COUNT owns is public before the game and is already in the model, and that model loses to FanDuel's price on log loss in every odds bin.
- The only unused structure in our own data is **the relationships between FanDuel's own prices.** The exact-dominance version of that is already closed (FC-EDGE-001: no violations). The *probabilistic* version (H1) has never been tested against outcomes.

## Phase 2: truly free market information

| Source | Multi-book? | Props? | History? | Quota | Payment details | Use terms | Verdict |
|---|---|---|---|---|---|---|---|
| The Odds API "Starter" ([home](https://the-odds-api.com/)) | Yes | Some books | The official [historical page](https://the-odds-api.com/historical-odds-data/) says historical is **paid plans only**; the homepage plan list is ambiguous | **500 credits/month.** Historical event odds would cost 10 × markets × regions per event per timestamp, so about 12 game-snapshots per month. | Not stated | Research use and storage permitted; no redistribution ([terms](https://the-odds-api.com/terms-and-conditions.html)) | **Insufficient** for any historical test. For forward use, about 1 market × 1 region per game per day. |
| SportsGameOdds "Amateur" ([pricing](https://sportsgameodds.com/pricing/), [FAQ](https://sportsgameodds.com/docs/faq)) | 9 books (FanDuel, DraftKings, BetMGM, Caesars, ESPN BET and others) | Listed | **No** | 2,500 objects/month at 10 requests/minute and 10-minute updates. One object is one event, so this is roughly one decision-time pull per game per day. | FAQ: "no credit card required" | "For testing, prototyping, and initial development"; storage terms **not found** | **Forward-only.** Needs live-capture authorization and confirmed terms. Cannot be evaluated historically. |
| fastballapi ([listing](https://mcpservers.org/servers/noahowsh/fastballapi-mcp)) | Uneven | Yes, from 2023 | Yes | 500 one-time signup credits | No card | Third-party; terms unverified | **Insufficient** (one-time credits; provenance unclear) |
| Open game-line datasets (e.g. [mlb-odds-scraper](https://github.com/ArnavSaraogi/mlb-odds-scraper)) | Yes | **No** | 2021 → 2025-08 | — | — | Scraped data; provenance unsuitable | **No props.** Does not overlap our 2026 props. |
| SportsDataIO Vault, OpticOdds | Yes | Yes | Yes | — | **Paid** | — | Excluded ($0) |

**Free multi-book player-prop history: insufficient. That avenue is terminated.** No free source supplies authenticated multi-book prop prices for any population we hold. The only free multi-book option is forward-only (SportsGameOdds Amateur). It requires live capture, which is not authorized, and its usage and storage terms are unconfirmed.

## B. Which potential advantages are materially new? (five hypotheses)

| # | Hypothesis | Why the signal could exist before the game | Why the book might miss it | Data ($0) | Markets | Frequency | Falsified by | Status |
|---|---|---|---|---|---|---|---|---|
| **H1** | **Ladder gap.** At an identical H+R+RBI ≥ 1 price, the gap to that player's hits ≥ 1 price predicts the result | The gap is the book's price for "no hit, but at least one run or RBI" (walks, lineup slot, run environment) | Ladders may be generated from a common hits model plus an average uplift | `data/props` + StatsAPI boxscores | H+R+RBI ≥ 1 (the largest Top Pick market) | About 16.7K player-games in 2026; every slate | 99% CI of the within-price hit-rate difference includes 0 | **Tested (preregistered)** |
| H2 | Free-tier multi-book forward consensus | Different books originate different numbers | Soft-book outliers | SportsGameOdds Amateur (forward only) | All props | Unknown | Consensus does not beat FanDuel no-vig on forward log loss | Not testable historically. It needs a live-capture authorization, so FC-EDGE-002's design would move to forward-only. |
| H3 | Market-anchored selection (FC-ACC-019 A′) | — | — | Production archive | All | Champion volume | A′ does not beat A at equal N | **Already proposed** (FC-ACC-019). It corrects losses and is not a profit source. Not new. |
| H4 | Weather-forecast revisions for HR | Forecasts change during the day | Books may update slowly | Open-Meteo historical-forecast archive (free, non-commercial) | HR, TB | Daily, small effect | No HR rate difference by revision | **Low prior.** HR bins lose about 27% (FC-ACC-019), so it would need a large edge. Books track weather. Not chosen. |
| H5 | Lineup or role instability, late information | — | — | StatsAPI, `lineup_watch` | Batter props | — | — | **Already falsified** at our latency (FC-EDGE-001: FanDuel repriced first) |

**Pitcher workload or restrictions:** no structured free source exists (beat reporters only), so it is not timestamp-verifiable. It is dropped.

## C. Which ONE hypothesis deserves testing first?

**H1.** It is the only candidate that meets all of these at once:
- costs $0;
- uses data that is already captured and timestamped;
- needs no model and no fitting, so it cannot recycle the model's anti-information;
- has never been tested against outcomes;
- targets the market where Top Pick volume actually lives (H+R+RBI);
- can be falsified in one pass.

Preregistered in `PREREG.md`, pinned at `96652d3ad30ea1cff110d1a0d503cab876df72f2`, before any outcome was fetched.

## D. What did the preregistered evidence show?

**Population**
- 15,022 decision-time pairs (H1 and HRR1 in the same pregame snapshot, at least 30 minutes before first pitch).
- **13,322 graded.** Excluded and counted:

| Reason | Rows |
|---|---|
| Void (0 PA or did not play) | 1,211 |
| Game not uniquely matched (doubleheaders or rescheduled games; schedule-based, outcome-blind) | 256 |
| Player unmatched | 160 |
| Game not final | 73 |

**Primary test: P-RS, regular season**
- 12,617 graded rows across 687 games.
- **D = +0.66 points.** 95% CI [−1.48, +2.89]; **99% CI [−2.19, +3.49]. The CI includes 0, so the preregistered verdict is STOP.**

| Tercile of g (within an identical HRR1 price) | n | Wins | Hit rate | Raw break-even | ROI at recorded price [95% CI] |
|---|---|---|---|---|---|
| LOW (smallest gap) | 4,230 | 2,809 | 66.4% | 71.6% | −7.4% [−9.7, −5.2] |
| MID | 4,145 | 2,744 | 66.2% | 72.0% | −8.1% [−10.3, −5.9] |
| HIGH | 4,227 | 2,775 | 65.6% | 71.6% | −8.6% [−10.8, −6.3] |

**Secondary: P-PS, postseason**
- 705 rows, 38 games.
- **D = −8.5 points** [95% CI −17.2, +1.4], the **opposite sign**.
- Every tercile loses.

**Equal-volume arms**
- 35 regular-season slates carrying published Top Picks; champion N = 551 (548 fillable under the one-per-player and two-per-game caps).

| Arm | Wins / picks | Hit rate | ROI at recorded price | Mean raw break-even | Odds, median (range) |
|---|---|---|---|---|---|
| A: champion (published record; FC-ACC-019 R1 reconciliation, 542 regular-season picks) | 289 / 542 | 53.3% | −9.8% | — | about −150 |
| B: H1 favoured tercile (LOW) | 410 / 548 | 74.8% | −4.0% | 77.9% | −350 (−700 to −140) |
| C: market reference (shortest HRR1) | 415 / 548 | 75.7% | −6.1% | 80.5% | −400 (−900 to −230) |

- **B against C:** B added 281 winners and removed 286, so it has **5 fewer wins**. B's slightly better ROI comes only from being pushed toward less-short prices, and it still loses.
- **H1 contains no information beyond the sportsbook price.**
- **On 72%:** B and C exceed 72% only by buying HRR1 at a median of −350 to −400, and both **lose money**. That is exactly the short-favourite trap the North Star excludes. It is not progress.

**Regime:** FanDuel-only prices, unauthenticated, production capture; 2026-08-06 → 10-26. These are not executable historical returns.

## E. Can it plausibly improve realized hit rate and ROI at maintained volume?

**No.**
- **H1 adds no winners.** At identical prices, the gap does not separate winners from losers.
- **ROI:** every tercile loses about 7–9% at FanDuel's HRR1 prices. The ladder's built-in margin is roughly 5–6 points of break-even above realized hit rates.
- **Volume:** volume was never the constraint (99.5% fill). The constraint is that FanDuel's own prices contain everything our free data knows.

## F. What would make us terminate further work on the current strategy?

**Recommendation: freeze it now.** The current strategy is the selection of single-book (FanDuel) props using FULL COUNT's model or FanDuel's own price structure. The evidence chain:
- **FC-ACC-017/018:** losing picks are market disagreements; equal-volume favourites add nothing.
- **FC-ACC-019:** the market beats the model on log loss in every odds bin; model "edges" are anti-informative.
- **FC-EDGE-001:**
  - FanDuel ladders are logically consistent;
  - lineup news is priced before we see it;
  - multi-book data is paid or forward-only.
- **FC-ZERO-001:** FanDuel's internal ladder relation carries no exploitable information.

**What "freeze" means here**
- Stop spending research effort on new features, filters, calibrations or rankings evaluated against FanDuel-only prices.
- This research makes **no production change**. Changing the published product, for example to the market-anchored A′ correction already specified in FC-ACC-019, is Jacob's separate decision.
- No new generic sprint is proposed.

**What would justify reopening.** Any one of these is sufficient; none is in hand.
1. **Authenticated multi-book prop prices at decision time, at $0 or under an authorized budget.** The realistic $0 route: Jacob authorizes forward capture through SportsGameOdds Amateur, after its storage and use terms are confirmed in writing as permitting a research archive. FC-EDGE-002's preregistered STOP criteria would then be applied forward (as an alternative to the paid historical archive). It is not authorized and not started.
2. **A new pre-decision information source** with verifiable timestamps that is demonstrably ahead of the book. For example, the book's price changes *after* our observation of the information. FC-EDGE-001's lineup measurement is the template, and it failed.
3. **An independent forward population** (2027 season) for the already-specified FC-ACC-019 A′ test. That test reduces losses; it does not, by itself, create profit.

Alligator

# FC-EDGE-001: is there a real, executable MLB betting advantage?

Research and feasibility only. No production, selector, display, capture or model change. Nothing has been bought, integrated, scraped or activated.

**Repo identities**
- Base: `main` @ `2f735a9349697042e9aca96389363b0a707c6435`.
- Inspected PR heads: #226 `99dec6f7`, #230 `5c25eb1e`, #234 `6036d29c`, #237 `06b9be89`.
- Prior findings this builds on:
  - FC-ACC-017 (#239 `fa4c465a`);
  - FC-ACC-018 (#240 `1e5d72fe`);
  - FC-ACC-019 (#241 `6496548d`), which found the market beats the model on log loss in every odds bin across 67,820 candidates.

**Artifacts in this directory**
- `dominance.py` and `out/dominance.json`: Phase 3.
- `lineup_timing.py` and `out/lineup_timing.json`: Phase 2.
- Regression check: `test_fc_edge_001_dominance.py` at the repo root.

## Bottom line

FULL COUNT's only price source today is **one book (FanDuel)**, and its model loses to that book's own prices (FC-ACC-019). An edge therefore has to come from somewhere other than the current model. I checked three possible sources:

| # | Opportunity | Verdict |
|---|---|---|
| 1 | **Multi-book price and consensus**: use a licensed odds archive to (a) get a sharper consensus probability than FanDuel alone and (b) take the best available price on the same wager | **GO for one retrospective research test only.** It needs Jacob to authorize a data purchase (about $59–$119 for one month). There is no evidence yet that the edge exists. |
| 2 | **Late information**: confirmed lineups, and lineup slot changes | **STOP at FULL COUNT's current latency.** FanDuel had already repriced by the time FULL COUNT first observed the confirmed lineup. |
| 3 | **Cross-market logical mispricing**: one prop logically implies another, yet the implying prop is priced longer | **STOP.** FanDuel's internal ladder is essentially perfectly consistent. |

**Success criterion.** Only #1 is still a credible mechanism, and it is unproven: FULL COUNT holds **zero timestamped multi-book observations**. Whether #1 is real is decided by the test in Q9, which costs about one month of a data plan and no live capture.

## Q1. What genuinely new advantage might FULL COUNT obtain?

Information it does not have today: **other books' prices for the same wager at the same moment.** This could help in two separate ways, which must not be conflated:

- **(a) Accuracy, meaning more winners at equal volume.**
  - A no-vig consensus across several books may be a better probability than FanDuel's single price. The current model is already worse than FanDuel's price.
  - If the consensus is better, ranking candidates by it selects more winners at the same volume.
  - This is a claim about accuracy and can be falsified on log loss.
- **(b) Price, meaning a lower break-even on the same pick.**
  - The same wager offered at a better price has a lower break-even probability. That alone does **not** make it +EV.
  - Example: say a reference probability is p = 0.55.

    | Price | Break-even | EV per $1 at p = 0.55 |
    |---|---|---|
    | −130 | 56.5% | −2.7% |
    | −115 | 53.5% | +2.8% |

  - The −115 bet is +EV **only if** the trusted reference p is above 53.5%. The better price helps only when a credible reference probability exists.
  - Arbitrage (both sides at different books summing below 100% implied) is a third, separate concept. It is not pursued: it is rare, small, short-lived and a fast route to account limits.

## Q2. Which data sources could provide it?

| Source | MLB props and books | History | Timestamps / identity | Notes |
|---|---|---|---|---|
| **The Odds API** ([docs](https://the-odds-api.com/liveapi/guides/v4/), [historical](https://the-odds-api.com/historical-odds-data/), [markets](https://the-odds-api.com/sports-odds-data/betting-markets.html), [books](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html)) | **Markets:** `batter_hits`, `batter_total_bases`, `batter_home_runs`, `batter_hits_runs_rbis`, `pitcher_strikeouts`, `pitcher_outs`, plus `_alternate` variants. **`us` books:** FanDuel, DraftKings, BetMGM, BetRivers, BetOnline, Bovada, LowVig, MyBookie, plus Caesars and Fanatics (paid plans only). **`us2` books:** Hard Rock, ESPN/theScore, Fliff and others. | **Props from 2023-05-03 at 5-minute snapshots** (paid plans only) | **Timestamps:** snapshot time and market `last_update`. **Identity:** event id, outcome name/description/point/price; `includeLinks` and `includeSids` on `/odds`. **Removal:** suspended markets stop updating and are dropped after about 15 minutes. | **Best fit for an archive-only test.** |
| **SportsGameOdds** ([pricing](https://sportsgameodds.com/pricing/), [MLB](https://sportsgameodds.com/leagues/mlb-odds-api)) | **All tiers:** FanDuel, DraftKings, BetMGM, Caesars, ESPN. **Pro adds:** Pinnacle, Circa, Bet365. | **Historical:** Pro tier only | **Update speed:** 10 minutes (Free), 3 minutes (Rookie), sub-minute (Pro) | Pro includes **sharp books**, which matter for consensus (Q1a). |
| **OpticOdds** ([baseball](https://opticodds.com/sports/baseball)) | 100+ books, props and alternates | Not published | Not published | Quote only. |
| **Sportradar** Odds Comparison Player Props v2 ([overview](https://developer.sportradar.com/odds/reference/oc-player-props-overview)) | Pre-match props only | Contract | Contract | Enterprise contract. |
| FanDuel web endpoint (current production) | One book | Repo retains `data/props/` (83 files) | `taken_at` per snapshot | **Not a licensed feed.** FanDuel's terms reportedly prohibit unauthorized automated access ([ConductAtlas summary](https://conductatlas.com/platform/fanduel/fanduel-terms-of-use/prohibition-on-unauthorized-automated-access/)). |

**The 2–3 most practical sources:**
1. **The Odds API**, for the retrospective test.
2. **SportsGameOdds Pro**, if sharp-book consensus proves decisive.
3. **OpticOdds**, only if both of the above fall short on coverage.

## Q3. What would those sources cost?

**The Odds API** ([pricing](https://the-odds-api.com/)):

| Plan | Price per month | Credits |
|---|---|---|
| Free | $0 | 500 (no historical data) |
| — | $30 | 20K |
| — | $59 | 100K |
| — | $119 | 5M |
| — | $249 | 15M |

- Historical event odds cost **10 × markets × regions** credits per event per timestamp.
- Four prop markets in one region come to 40 credits per game per timestamp, or 80 with the alternate markets.
- One full regular season (about 2,430 games) at one decision-time snapshot is therefore about 97K–194K credits. Adding a second, near-close snapshot doubles that.
- **One month of the $119 plan covers the whole test with ample margin.** The $59 plan is too tight.
- Prices must be re-verified at purchase time.

**SportsGameOdds:**

| Tier | Price per month | Allowance |
|---|---|---|
| Free | $0 | 2,500 objects |
| Rookie | $99 | 100K objects |
| Pro | $299 | Unlimited, includes historical and sharp books |

**OpticOdds and Sportradar:** quote only.

## Q4. What do the sources legally and operationally permit?

- **The Odds API** ([terms](https://the-odds-api.com/terms-and-conditions.html)):
  - permits storing data indefinitely and using it for research and model training;
  - prohibits resale and redistribution;
  - operational limits: 429 rate limiting and the credit quota.
  - A research archive kept inside the repo is consistent with those terms. Republishing the raw vendor odds on the public site would not be. The terms must be re-read before any purchase.
- **FanDuel's own pages:** the current production FanDuel capture already uses an unlicensed web endpoint (`odds_fanduel.py`). This mission adds no scraping, and **no book other than through a licensed vendor should be added.**
- **Execution constraint, not a data constraint:** books routinely limit accounts that beat the closing line, and props are limited quickly ([IBJ](https://www.ibj.com/articles/sportsbooks-say-you-can-win-big-then-they-try-to-limit-winners), [Massachusetts limits review](https://bircheshealth.com/resources/limiting-bettors)).
  - A best-price edge that exists only at small or soft books may not be executable at meaningful stakes.
  - The test must therefore restrict the book set to books Jacob can actually use.

## Q5. What timestamped evidence exists that such an advantage is available?

**For opportunity 1: none in the repo.**
- `data/odds/` holds 77 Action Network scoreboard files with game lines only, no props, from a single feed.
- `data/props/` is FanDuel only.
- Industry sources assert that FanDuel originates its own prop numbers, separately from books that share an outsourced feed ([Establish The Run](https://establishtherun.com/understanding-the-current-ecosystem-of-nfl-player-props/)), and give anecdotal dispersion examples ([Action Network](https://www.actionnetwork.com/nfl/super-bowl-player-props-where-to-find-the-best-odds-and-lines)).
- That is a plausible reason to expect dispersion. It is **not evidence**, and I found no systematic MLB prop dispersion study.

**For opportunity 2: measured, and it is negative at current latency.** See `out/lineup_timing.json`.
- **Sample:** 6 regular-season slates, 268 `docs/data.json` commits between 14:00 and 23:59 UTC, and 6,919 batter-prop series.

| Pair type | n | Share where the FanDuel price changed | Mean absolute change in implied probability | Median gap between observations |
|---|---|---|---|---|
| Last projected → first confirmed observation | 4,375 | **65.7%** | **0.98 pp** | 10.6 min |
| Projected → projected (control) | 76,007 | 7.8% | 0.07 pp | 9.4 min |

- The direction tracks the slot change:

| Slot change at confirmation | n | Mean change in implied probability | Share changed |
|---|---|---|---|
| Moved up | 676 | **+1.04 pp** | 76% |
| Moved down | 393 | **−0.58 pp** | 72% |
| Same slot | 1,951 | −0.03 pp | 61% |

- FanDuel had therefore already moved, in the correct direction, by FULL COUNT's first confirmed observation, usually within the roughly 10-minute observation cadence.
- Pairs that share one odds fetch cannot show a change, so 65.7% understates how often FanDuel repriced.
- **Caveat:** these timestamps are FULL COUNT's acquisition times, not the book's quote times or a bettor's execution times. This rules out an edge at FULL COUNT's current latency. It says nothing about a sub-minute feed.

**For opportunity 3: measured, and it is negative.** See `out/dominance.json`.
- **Sample:** 83 files, 730 snapshots, 4,078,256 pregame rows.
- **Result:** no dominance in any exact implication or monotone threshold rule except HR ≥ 1 ⇒ TB ≥ 4.
- **HR ≥ 1 ⇒ TB ≥ 4:**
  - 157 of 17,555 player-games;
  - 398 observations, of which only 68 were strict;
  - median gap 0 (ties);
  - maximum gap 0.9 pp.
- These are near-identical prices on near-identical events, not a usable mispricing.

## Q6. How could it add winning picks, rather than just improve how probabilities are presented?

- **Only mechanism 1(a) can add winners.** It works if a multi-book consensus ranks candidates more accurately than FanDuel alone, so the top-N at equal volume contain more winners.
  - This is checkable directly: compare log loss and Brier score of the consensus against FanDuel-only on identical rows, then compare equal-N hit rates.
- **Mechanism 1(b) adds no winners.** It changes only how much each win pays and which candidates clear break-even.
- No mechanism here makes an existing pick more likely to win.

## Q7. Can it preserve usable pick volume?

**Unknown.**
- 1(a) is a re-ranking, so volume is preserved by construction: compare at equal N.
- 1(b) volume depends on how often a usable book beats FanDuel by enough to matter. FULL COUNT has no data on that, so I am not estimating it.

## Q8. Can it plausibly produce positive ROI?

**Plausible, not demonstrated.**
- Every FanDuel-only result so far has been negative: the published ledger ROI is −10.6% (FC-ACC-017), and market-only picks at FanDuel prices lose the vig by construction.
- Positive ROI requires both of the following:
  1. a reference probability that beats the price actually taken, on held-out data;
  2. that price being available at a book Jacob can use, at the decision time.
- High hit rate alone does not deliver this. In FC-ACC-018 and FC-ACC-019, 72% was reachable only at prices of roughly −250 or shorter, which lose money.

## Q9. What is the smallest research-only experiment worth doing?

**FC-EDGE-002: a retrospective multi-book archive test.** It requires a separate authorization to buy one month of The Odds API (about $119), and must be preregistered before any vendor data is examined.

**Population**
- Every R2 candidate at the 2026 decision cutoffs, as in FC-ACC-019.
- Champion N recorded before any exclusion (the #230 export).

**Data**
- Vendor snapshots, one region (`us`, plus `us2` if needed for Jacob's books).
- Markets: `batter_hits`, `batter_total_bases`, `batter_home_runs`, `batter_hits_runs_rbis`, plus alternates.
- Snapshot times: (i) the snapshot at or before each candidate's decision time; (ii) the last snapshot before first pitch, for a closing reference.
- Store the raw vendor responses with a sha256 manifest, committed as the archive. Never fabricate a missing price: a missing price means "unavailable".

**Arms**

| Arm | What it tests |
|---|---|
| **A: champion** | Actual Top Picks, at the actual FanDuel price, with actual volume. |
| **B: market-anchored** | Replace model p with FanDuel exact no-vig p (raw/1.08 only where one-sided, labelled as such). Gates unchanged. |
| **B′: multi-book consensus** | p = median no-vig across available books, at least 3 books, excluding FanDuel. Equal-N to A. **This tests 1(a).** |
| **C: best price** | For every A and B′ selection, the best price at an allowed book at decision time. ROI at that price against FanDuel's. **This tests 1(b).** |
| **D: late information** | **Not included.** Phase 2 found no timestamp-verifiable window at FULL COUNT's latency. |

**Requirements, all from the mission list**
- Full event/player/market/side/line/odds identity.
- Vendor timestamp plus `last_update`.
- Availability: absent, suspended or stale beyond 10 minutes means unavailable.
- Authentic response binding: raw vendor body, sha256 and request parameters.
- Same-run manifest.
- Exact grading and void handling, reusing the FC-ACC-019 grading.
- Winners added and removed versus A.
- Equal-volume hit rate.
- Actual-price ROI and odds distribution.
- Bootstrap clustered by game and by slate.
- Results per season and per market.
- Source freshness.
- Book accessibility, i.e. Jacob's book list fixed before the test.
- No tuning after outcomes are seen.
- Incomplete or zero-coverage slates recorded explicitly.

**Reuse (minimal dependency map)**

```
#230 champion export (N before withholding) ─┐
FC-ACC-019 candidates + grading (#241) ──────┼─► FC-EDGE-002 join (event/player/market/line, time ≤ decision)
licensed vendor archive (new, raw + sha256) ─┘            │
                                                          └─► arms A / B / B′ / C report
#226 S1 protocol: reuse its offer-validity rules as the preregistration template
#234 FanDuel parser, #237 bridge: NOT needed (both are FanDuel-raw-response-centric; vendor JSON replaces them)
```

## Q10. What specific result would make us STOP?

Each of the following is preregistered as a termination condition:

1. **Coverage.** Fewer than 50% of A's 2026 selections have at least 3 books, including at least one usable by Jacob, at decision time. If so, STOP the whole opportunity: the archive cannot support it.
2. **Accuracy, 1(a).** B′ fails to beat FanDuel no-vig on held-out log loss, or B′ has no significant equal-N hit-rate gain over A under the clustered bootstrap. If so, STOP 1(a).
3. **Price, 1(b).** The best price at a usable book improves break-even by less than the 95% CI width of the reference probability on fewer than 5% of selections, or C's ROI interval does not exclude the FanDuel-only ROI. If so, STOP 1(b).
4. **Sharpness.** The consensus loses to FanDuel no-vig. That would mean multi-book adds nothing, and no further vendor spend is justified.

**Continue criterion:** 1(a) or 1(b) clears its bar on 2024–2025, and again on 2026 held out. A pass would justify a **forward-only shadow** proposal, which would need its own authorization. It would not justify production use.

## Phase 2 detail: time-sensitive information

"Window?" asks whether a mispriced offer is still available when FULL COUNT can recognize the information.

| Information | Earliest trustworthy source | Timestamp available | Affects | Book adjustment | Window? | Already in model? | Frequency |
|---|---|---|---|---|---|---|---|
| Confirmed lineup and slot | MLB Stats API schedule/boxscore lineups. There is no posting timestamp, so polling is required ([community](https://apify.com/ichigowa/mlb-lineups-alert)). Production also uses RotoWire (`mlb_daily.py:423`) and polls every 10 minutes (`lineup-watch.yml`). | Our acquisition time only | All batter props | **Measured:** FanDuel moved within about 10 minutes, in the right direction | **No, at current latency** | Yes (`lineup_assumed`, slot) | Every game, daily |
| Starting-pitcher change or scratch | MLB Stats API `probablePitcher`; `check_scratches.py` | Our acquisition time only | Opposing batter props; pitcher props (usually void under house rules) | Not measured | Not measured | Partially | Rare, unknown |
| Pitcher limits or pitch counts | No structured source (beat reporters) | None | Pitcher outs and strikeouts | Not measured | Not verifiable | No | Unknown |
| Bullpen fatigue | Prior-day box scores (Stats API) | Public before first pitch | Small effect on batter props | Not measured | Unlikely (public overnight) | Yes (bullpen fetch) | Daily |
| Weather | Open-Meteo forecast (`generate_picks.py`) | Forecast issue time | HR, TB | Not measured | Unlikely (public, continuous) | Yes | Daily |
| Injuries and IL moves | Stats API transactions | Transaction date only | Player availability, usually voids | Not measured | Not verifiable | Yes (IL returns) | Weekly |

**Can FULL COUNT recognize information while a mispriced offer is still available?**
- For lineups, the one source measured: **no.**
- For the others: not timestamp-verifiable from repo data, so Arm D is not supported.

## Phase 3 detail: exact constraints vs probabilistic assumptions

**Exact constraints**
- These follow from set inclusion under identical settlement, same player and same game: hits ≥ 1 ⇒ H+R+RBI ≥ 1; TB ≥ 2 ⇒ hits ≥ 1; hits ≥ 2 ⇒ TB ≥ 2; doubles ≥ 1 ⇒ TB ≥ 2; triples ≥ 1 ⇒ TB ≥ 3; HR ≥ 1 ⇒ TB ≥ 4; HR ≥ 1 ⇒ H+R+RBI ≥ 3; runs ≥ 1 ⇒ H+R+RBI ≥ 1; RBI ≥ 1 ⇒ H+R+RBI ≥ 1; singles ≥ 1 ⇒ hits ≥ 1; X ≥ k+1 ⇒ X ≥ k.
- They hold within one book only if void rules match. At FanDuel they do, so a violation would be a pure dominance.
- A dominance is still not arbitrage and not necessarily +EV.

**Probabilistic assumptions, not exact and not used here**
- correlations between players or markets;
- the "H+R+RBI ≈ hits + something" shape;
- PA-count models;
- cross-book equivalence. Different books can have different void, DNP and pitcher-change rules, so a cross-book "dominance" is not exact.

Alligator

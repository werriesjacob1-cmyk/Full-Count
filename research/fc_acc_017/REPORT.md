# FC-ACC-017 — why FULL COUNT's Top Picks lose (2026-10-23)

**Verdict: GO for one preregistered, research-only test (intervention 1). MODIFY for production: change nothing until prospective evidence exists and Jacob authorizes it.**

## Evidence identities

| Item | Identity |
|---|---|
| main @ | `15d67037d860948f6a37b377757967a8307f0678` |
| Public registry | `data/public_top_picks/registry.json` sha256 `ca408175…9857` (656 entries) |
| Public totals | `results/history.json` sha256 `4d17969e…0079` |
| Public grades | 73 `results/grades_*.json` files, concatenated sha256 `d4be89b9…6ce6` |
| Replacement pool | `output/board_freeze_graded_2026-*.json` (18 slates with public picks); board shas in `out/replacement.json` |
| Replication pool | 8 `docs/data.json` snapshots (commit SHAs in `snapshot_replication.py`), graded against StatsAPI batter logs (`batter_games.json` sha256 `0bc0e364…`, retrieved 2026-10-22) |

## 1. Baseline (public evidence only; `ledger.py`, `forensics.py`)

**Reconciliation**
- 656 published Top Picks, 2026-08-18 → 10-19, over 49 slates: **343 W / 303 L / 9 void / 1 ungraded**.
- Every registry ID is graded exactly once; there are no duplicates and no snapshot mismatches, and the totals equal `history.json`.
- The 342–302 figure does not match any slate boundary. The ledger reads 340–302 through Oct 18 and 343–303 through Oct 19 (the Oct 19 slate went 3–1).

**Performance**
- Hit rate **53.1%**; flat-stake ROI at the published prices **−10.6%** (−68.6 units).
- **By month:** Aug 44.8% (n = 96), Sep 54.8% (476), Oct 52.7% (74); last 14 slates 51.9% (104).
- **By market:**

| Market | n | Hit rate | ROI |
|---|---|---|---|
| H+R+RBI 1+ | 393 | 55.7% | −9.0% |
| Hits 1+ | 111 | 55.0% | −4.9% |
| **K 4+** | **84** | **44.0%** | **−19.3%** |
| All pitcher props | 142 | 44.4% | −19.5% |

**Calibration:** the model said **64.8%** on average. The devigged market (implied ÷ 1.08) said **54.1%**. Realized was 53.1%.

**Volume and correlation**
- Median 14 picks per slate (maximum 29).
- 516 picks shared a game with another pick.
- 49 same-player multi-pick slates; their outcomes were 26 both-won, 19 both-lost, 4 split, so they behave almost as duplicates.

## 2–3. Why picks lose (diagnostic; pregame fields only, postgame fields used only to diagnose)

**The mechanism, in production's own words.**
- **Every one of the 646 settled Top Picks carries production's note that "the market itself disagrees".**
- `recommendation.py` documents that, with an 8% hold and a 5% ROI floor, no favorite can be both market-agreeing and clear the value test.
- So the Top Pick gate selects **only** props where the model sits well above the market.
- The model carries little information beyond the market on these picks:
  - log loss 0.715 (model) vs **0.683** (market); Brier 0.260 vs **0.245**;
  - in a joint logit, the market coefficient is 1.17 (SE 0.54) and the model's is 0.48 (SE 0.71).
- **Losses: 303 observed vs 296.7 expected at the market's own prices vs 227.5 expected under the model.** We lose at the rate the market prices what we pick. The roughly 76 losses above the model's expectation are model overconfidence turned into selection.

**Pregame patterns (exploratory)**

| Pattern | Hit rate | Market's own probability |
|---|---|---|
| Model-minus-market gap 0.08–0.14 | 57% (n = 467) | — |
| Model-minus-market gap ≥ 0.14 | **41% (n = 87)** | 0.45 |
| Devigged market < 0.45 | 37% (n = 38) | — |
| Devigged market 0.60–0.65 | 63% (n = 51) | — |
| Plus-money picks | **30% (n = 27)** | 0.41 |
| Pitcher props | 44% (n = 142) | 0.47 |

- **Hit rate follows the market price.**
- **Batting slot:** no deviation from the market beyond noise; 43% of batter picks came from slots 8 and 9.
- **Reliability A vs B:** 54% vs 50%.
- **Slate volume** and **team implied runs:** no deviation beyond noise.
- **Lineup:** every published pick had a confirmed lineup.

**Loss classes (not exclusive; strength of evidence)**

| Class | Evidence | Strength |
|---|---|---|
| (1) Model estimation | Model 64.8% vs realized 53.1%, uniformly across markets | **Strong** |
| (5) Price/market | Gaps and plus-money picks lose more; every pick contradicts the market | **Strong**; same mechanism as (1) |
| (4) Selection | Market favorites the model also rated ≥ 0.60 were available and skipped (§4) | **Moderate**; exploratory replacement evidence |
| (2) Opportunity | Projected 3.83 PA vs actual 3.54 (n = 501; postgame, diagnostic only) | **Weak**; partly explains the overstatement |
| (3) Matchup | No slot, team-total or reliability pattern beyond the market | **Not supported** |
| (6) Variance | At the market's ~54% for selected props, about 46% losses were inevitable; **individual** losses beyond the price are not pregame-identifiable with this evidence | Dominant at the per-pick level |

## 4. Decision mistakes: what we would have picked instead (`replacement.py`)

**Setup**
- Per slate, N = the actual public Top Pick count.
- Pool = the frozen board universe: priced, QC-kept, lineup-confirmed, champion markets, game not yet started, graded by the production grader.
- The two graders agree on all **120/120** overlapping picks.
- One pick per player; shortfalls are never filled.

| Arm (equal-count slates) | Slates | Picks | Hit rate | ROI | Δ hit-rate vs champion, day-clustered 95% CI |
|---|---|---|---|---|---|
| Actual public Top Picks | 13 | 156 | 52.6% | −12.5% | — |
| **Favorite lane: devig ≥ 0.60, price ≥ −350, ranked by model** | 13 | 156 | **71.8%** | **+1.3%** | **+11.8 to +29.5 pts** |
| Market-only (top N by implied probability) | 15 | 186 | 68.8% (champion 51.6%) | −5.4% (champion −14.2%) | +6.9 to +28.6 pts |

**Cautions**
- **Game concentration:** the favorite-lane picks fell in only **40 games**, with up to 14 picks in one game. The game-clustered CI for its hit rate is still [0.64, 0.80], but a cap of 2 per game leaves too little volume in this narrow pool to test.
- **Exploratory variant:** the favorite-lane variant was specified after the market-only result was seen. Both are reported.

**Independent replication (`snapshot_replication.py`)**
- Population: 833 pregame, priced, confirmed hits 1+ candidates from 8 full-slate dashboard snapshots, graded against official StatsAPI hits.
- **The market predicts better than the model:** log loss 0.658 vs 0.663.
- **Realized rates track the market price:** 55.5%, 64.4% and 73.3% for devig bands 0.50–0.60, 0.60–0.65 and 0.65–0.70.
- **Rows where the model exceeds the market by ≥ 0.05 hit 52% (n = 76);** rows where the model is below the market hit 63.7% (n = 454).
- **The favorite-lane rule hits 67.2% (n = 314).** The champion's own Top Pick rows in those snapshots hit 37.5% (n = 8).
- **Within the published ledger,** picks the market itself priced at ≥ 0.60 hit **64.2% (n = 53)**, against 52.1% for the rest. That is a subset, not a replacement.

**Volume:** on full dashboard slates, 118–156 players satisfy the favorite-lane rule, against a public N of 14–26. Snapshots taken after early games had started fall short.

## 5. Prior models

- **FC-ACC-012 (H+R+RBI plate-appearance conditioning):** relevant only to the weak opportunity signal.
- **FC-ACC-013:** STOP.
- **FC-ACC-014 and FC-ACC-015:** retrospective ranking gains only.
- **FC-ACC-016 E1 (frozen hits challenger):** it keeps production's **value gate**, so it would still be forced into model-over-market disagreements, which is the demonstrated failure mechanism. Only 7 published hits picks fall inside its feature window (through 2026-08-25), and it rated 6 of them below 0.60. That is anecdotal.
- **A1 E2 (TB value lane):** STOP, because it is structurally ineligible.
- **No existing frozen challenger addresses the demonstrated weakness by itself.** The hits model could serve as the ranker *inside* intervention 1; that is untested.

## 6. Ranked interventions (maximum five)

| # | Observed weakness (n) | Pregame correction | Decision change | Replacement established? | Winner direction and uncertainty | Volume, mix and correlation | Evidence regime / limits | Smallest next experiment |
|---|---|---|---|---|---|---|---|---|
| **1** | Every pick contradicts the market; realized 53.1% ≈ market 54.1% (646) | Devigged market probability, already on every row | Select market favorites (devig ≥ 0.60, ≥ −350) the model also rates ≥ 0.60, ranked by model, at the champion's N | **Partly**: frozen pool on 13 of 18 slates, plus 833-row replication | **+**, about +12 to +30 pts on 156 picks; ROI ≈ 0 instead of −12.5% | N maintained on full slates; batter markets only; **high game concentration** | Exploratory; late season; unauthenticated prices; different pipeline from the champion; the variant was chosen after the market-only arm | Preregistered retrospective extension on more snapshots (with a ≤ 2-per-game cap), then a prospective shadow |
| 2 | Pitcher props: 44.4% hit, −19.5% ROI; model 62.8% vs market 47.3% (142) | Market price; the model's pitcher overstatement | Stop pitcher Top Picks; fill the slots via #1 | Via #1 only | **+** if replaced, unquantified alone | Mix shifts to batters | Public evidence; filtering only unless replaced | Folded into #1 |
| 3 | Model-minus-market gap ≥ 0.14: 41% (87) | The gap is known pregame | Exclude large disagreements; replace via #1 | Via #1 only | **+** if replaced | Small volume effect | Exploratory | Folded into #1 |
| 4 | Same-player pairs move together: 45 of 49 identical outcomes (98 picks) | Player identity | One pick per player, with the next candidate replacing | Not tested | ≈ 0 for hit rate; less variance | Neutral volume; less correlation | Public evidence | Apply inside #1 (already applied) |
| 5 | PA overprojected: 3.83 vs 3.54 (501; postgame diagnostic); 43% of batter picks from slots 8–9 | PA calibration by slot (FC-ACC-012 domain) | Estimation only; alone it reduces eligibility | No | Unknown | Would *reduce* volume without #1 | Diagnostic; slot from projection text | Out-of-sample slot-PA calibration check |

## Answers

- **A. Cause.**
  - The Top Pick rule only admits props where the model beats the price by at least a 5% ROI. Given the hold and a model with little information beyond the market, that admits only market-disagreement props, about 54% propositions that the model believes are 65%.
  - We therefore lose at the market's rate, about 46%, and pay the vig (−10.6% ROI).
- **B. Predictable pregame.** A pick's *expected* hit rate is predictable from its own market price, from the size of the model-market gap, and from market type (pitcher props). Individual losses beyond that are variance.
- **C. Correctable at maintained volume.** Moving selection toward market favorites that the model also likes. Supply exists on full slates, and exploratory replacement evidence shows more winners at equal count.
- **D. Test first.** Intervention 1, as a preregistered research test. **It is not a production change:** it redefines Top Pick selection, which needs Jacob's authorization.
- **E. Falsifiers.**
  1. In a preregistered retrospective extension on further dashboard snapshots (hits 1+ graded against StatsAPI, ≤ 2 per game), the favorite lane fails to beat the champion's equal-count hit rate with a day-clustered lower bound above 0.
  2. Prospectively, at equal count, the lane's hit-rate gain has a lower bound ≤ 0.
  3. Its realized hit rate falls below its own devigged market price.
  4. It cannot fill at least 95% of the champion's N under the 2-per-game cap.
  5. ROI falls more than 2 points below the champion's.

## Limits and adversarial checks

- **Leakage:** no postgame field enters selection; the pools are filtered to games not yet started.
- **Population:** the freeze pool is a different pipeline. Only 122 of the 239 public picks on those dates are in it.
- **Prices:** production captures, not FC-OPS-001 authenticated.
- **Grading:** the two graders agree 120/120.
- **Sample:** 13–18 late-season slates, including postseason October.
- **Repeated testing:** two variants were tested; the second was added after the first.
- **Game clustering:** substantial.
- **Ledger:** no duplicate or contradictory public records.
- **No ROI claim:** nothing here establishes profitability.

Alligator

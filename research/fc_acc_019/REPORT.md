# FC-ACC-019 — does FULL COUNT have a predictive edge over the sportsbook? (2026-10-23)

**Verdict (frozen PREREG §6 rule): MODIFY.** Two longshot segments pass the incremental-information test.

**Substantive finding: NO DEMONSTRATED EDGE.**
- No odds range and no market shows information that adds winners or beats the price.
- **The model's own "edges" select fewer winners than the market's prices predict.**
- The two passing segments (stolen bases and triples, all priced +200 or longer) only identify which longshots are *worse*. No band of model-favoured rows beats the market there, ROI is about −37%, and 2 passes out of about 40 tested segments is what chance alone would produce.

## Identities and regimes

| Item | Identity |
|---|---|
| Latest main | `25052101d71fc66ed099cc2afa7daa40116bbc81` (registry, `history.json` and grade files unchanged since FC-ACC-017/018) |
| **Preregistration** | **`32c77d2e26e6afe96925bf7e15de8b67c96633db`** |
| R1, published Top Picks | `registry.json` `ca408175…`: **343 W / 303 L / 9 void / 1 ungraded**. Reconciles exactly with `history.json` `4d17969e…`; no duplicates, no ungraded IDs. |
| R2, retained pregame candidates | Production dashboard rows: for each game, the latest pregame `docs/data.json` state containing that game's rows (≤ 8 h before first pitch; 676 payloads; 473 of 479 games). **67,820** graded rows across 12 markets, with no price cap; regular season 08-18 → 09-27. Graded against official StatsAPI logs. Prices are production-captured, **not authenticated**. Contains 524 of the champion's 542 regular-season picks. |
| R3, canonical retrospective | Frozen FC-ACC-015 hits and TB models on 665 + 665 R2 rows with features (08-18 → 08-25). No refit. |

**Correction to FC-ACC-017/018.**
- Some rows record an exact two-sided no-vig `market_implied` (most strikeouts and outs). FC-ACC-017 and FC-ACC-018 divided those by 1.08 a second time.
- Corrected, published **pitcher** picks had a market probability of **51.1%** (not 47.3%) and hit 44.4%, so they **underperformed even the market**. Batter figures are unchanged (0.560).
- FC-ACC-018's hybrid gate was therefore slightly stricter than intended for strikeouts and outs. Its conclusion does not change, since its picks were about 97% H+R+RBI.

## Odds spectrum

ROI is at recorded prices (unauthenticated); CIs are clustered by game. Empty bins are shown, not merged.

**R1 — published Top Picks**

| Bin | n | Hit rate | Model | Raw break-even | No-vig | ROI [95% CI] | Main markets |
|---|---|---|---|---|---|---|---|
| +200 or longer | 0 | — | — | — | — | — | — |
| +100 to +199 | 27 | **29.6%** | 61.2% | 47.0% | 44.2% | −37.6% [−70, +1] | K, outs |
| −100 to −149 | 307 | 49.5% | 62.6% | 56.6% | 52.6% | −12.4% [−22, −3] | H+R+RBI, hits, K |
| −150 to −199 | 294 | 58.2% | 66.9% | 62.4% | 57.8% | −6.8% [−17, +3] | H+R+RBI |
| −200 to −299 | 18 | 66.7% | 72.0% | 67.7% | 62.7% | −1.7% [−32, +30] | H+R+RBI |
| −300 or shorter | 0 | — | — | — | — | — | — |

**R2 — all retained pregame candidates (67,820)**

| Bin | n | Hit rate | Model | Raw break-even | No-vig | ROI [95% CI] | Log loss, model / market |
|---|---|---|---|---|---|---|---|
| +200 or longer | 27,275 | 12.1% | 13.0% | 15.4% | 14.3% | −26.8% [−31, −22] | 0.3340 / **0.3323** |
| +100 to +199 | 19,321 | 36.9% | 36.4% | 41.8% | 38.8% | −11.8% [−14, −9] | 0.6588 / **0.6547** |
| −100 to −149 | 7,052 | 49.9% | 47.1% | 54.9% | 50.9% | −9.1% [−12, −7] | 0.7032 / **0.6920** |
| −150 to −199 | 5,212 | 58.1% | 57.9% | 63.1% | 58.4% | −7.8% [−10, −6] | 0.6857 / **0.6786** |
| −200 to −299 | 6,250 | 66.9% | 64.1% | 70.5% | 65.3% | −5.1% [−7, −3] | 0.6380 / **0.6341** |
| −300 or shorter | 2,710 | **75.2%** | 67.9% | 77.9% | 72.1% | −3.5% [−6, −1] | 0.5741 / **0.5604** |

- **The market beats the model on log loss in every bin.**
- **Every bin loses at the recorded prices.**
- **The only systematic pattern is the market's favourite–longshot bias:** heavy favourites realize about 3 points above no-vig, and longshots about 2 points below. Even that does not overcome the vig.
- **R1 by period:** August is weaker than September (FC-ACC-017); per-bin month tables are in `out/analysis.json`.

## Model vs market (preregistered diagnostic control)

**Control:** for each segment, `y ~ logit(no-vig) + c·(logit(model) − logit(no-vig))`, fitted on the first half of slates and tested on the second; game-clustered CIs.

**Result:** of 40 supported segments (all rows, each odds bin, each market, and market × bin), **only two** meet both criteria.

| Segment | n | Disagreement coefficient c [CI] | Out-of-sample Δ log loss [CI] | Does model-favoured disagreement beat the market? | ROI |
|---|---|---|---|---|---|
| Stolen base | 4,044 | 0.22 [0.04, 0.50] | −0.00075 [−0.0015, −0.00005] | No: every band realizes below no-vig | −37.5% |
| Triples (all priced +200 or longer) | 7,377 | 0.46 [0.03, 0.87] | −0.00034 [−0.00066, −0.00004] | One band, n = 510, +0.4 points (9 wins); the rest below | −36.8% |

Every other market (hits, H+R+RBI, TB, singles, doubles, HR, runs, RBI, K, outs) and every odds bin fails.
- **Total bases:** **adding** the model makes the out-of-sample log loss *worse* (Δ +0.0004 [+0.0001, +0.0007]).
- **R3:** the frozen hits challenger scores 0.6576 vs market 0.6535, and the frozen TB challenger 0.6306 vs 0.6278. Production scores 0.6598 / 0.6306. **Neither frozen challenger beats the market.**

## Substitution, and hit rate vs ROI

**Operational equal-volume substitution: not supported.** Decision-time prices are unauthenticated, and FC-ACC-018 and FC-OPS-003 showed decision-time supply is insufficient.

**Hypothetical equal-N ranking diagnostic:** champion's actual N on 35 slates; one per player, at most 2 per game; added beyond the preregistered k = 3 diagnostic.

| Selection at champion N | Wins / picks | Hit rate | **Market-expected wins** | ROI | Odds (median, range) |
|---|---|---|---|---|---|
| Champion (actual public picks) | 289 / 542 | 53.3% | 296.3 | −9.8% [−17.5, −2.2] | about −150 |
| Market-only (top no-vig) | 411 / 539 | 76.3% | 398.4 | −4.5% [−9.0, +0.2] | −400 (−900 to −122); **96% at −300 or shorter** |
| Model probability | 371 / 539 | 68.8% | **371.7** | −7.8% [−13.3, −2.2] | −320 |
| Model − break-even ("edge") | 195 / 539 | 36.2% | **208.6** | −14.5% [−25.8, −2.7] | +124 (−220 to +1500) |

**Takeaways**
- **No selection beats the market's own expectation by more than noise**, except the favourite-longshot bias that market-only captures (+12.6 wins). Even that loses about 4.5% at the observed prices.
- **The model's perceived edges are anti-informative:** −13.6 wins against the market's expectation. This is the champion's failure mechanism.
- **The k = 3 per-slate diagnostic agrees:** market-only 85/105 against 81.4 expected; model 70/105 against 68.7; edge 46/105 against 44.1.

**Feasibility of 72% at the same volume with positive ROI**
- In the observed spectrum, a hit rate of 72% or more appears only among props priced −300 or shorter, which lose about 3.5% at recorded prices.
- No populated segment shows both a hit rate near 72% and positive ROI.
- A plus-money edge would be valuable on its own, but none exists: every plus-money bin loses 12–27%.

## Ranked hypotheses (maximum three)

| # | Hypothesis | Pregame decision change | Evidence | Status |
|---|---|---|---|---|
| 1 | **The model's disagreement with the market is anti-information.** The value gate's reliance on model-over-market selects losers. | Stop admitting Top Picks *because* the model exceeds the price: use the market's no-vig probability as the governing probability, and the model only as a bounded tie-breaker. | R1: every pick is a market disagreement; 303 losses ≈ 296.7 market-expected. R2 equal-N: the edge-ranked arm is −13.6 wins vs market. | Strongest. Corrects losing selection; it is not a source of profit. |
| 2 | **Favourite–longshot bias:** heavy favourites (−200 and shorter) realize about 3 points above no-vig. | Choose among priced favourites, and require prices better than those observed (other books or line shopping) before betting. | R2 bins at −200 to −299 and −300 or shorter (n = 8,960); equal-N market-only +12.6 wins vs market | Raises hit rate; ROI still negative at observed prices; no authentic multi-book prices exist. |
| 3 | The model ranks *within* SB and triples longshots slightly better than the market. | At most, avoid model-disfavoured longshots. Never bet them. | 2 of 40 tests; no positive band | Weak; probably chance. |

## Single next move

**A preregistered, archive-only prospective test of hypothesis 1, at the champion's actual N.**
- **Arms:**
  - **A:** the champion;
  - **A′:** the champion's same gates and candidate pool, with the **no-vig market probability** replacing the model probability in the 0.60 floor and the value test, and the model used only to break ties;
  - **B:** market-only.
- **Rules:** identical per-slate counts; at most 2 picks per game and 1 per player; authentic captured offers (the FC-OPS-001/002 path, owned by Codex).
- **Primary outcome:** settled wins, A′ minus A.
- **Falsification:**
  - if A′ does not beat A at equal volume, the model-over-market mechanism is not the cause of the losses;
  - if A′ adds winners only by moving to −300 or shorter prices with ROI worse than A, it does not meet the North Star.
- **Authorization:** this needs Jacob's authorization to archive decision-time candidate sets. Nothing was activated.

Alligator

# FC-ACC-018 — market-favorite selection: equal-volume falsification (2026-10-23)

**Verdict: STOP** (frozen PREREG §5 rule). The equal-volume claim fails its preregistered **supply gate**. On the retained historical dashboard states, the frozen hybrid can fill only **44%** of the champion's cap-comparable volume (32% of its original volume). Market-only fills 47%.

The descriptive results also **falsify the "model" part** of the FC-ACC-017 idea. The hybrid adds nothing measurable over market-only favorites, and every arm wins at about the rate its own market prices imply.

## 1. Identities

| Item | Identity |
|---|---|
| Repository main at evaluation | `8b1a0cdcb7ce79c957eca575f4db1b3e4273e811` |
| **Preregistration** | **`6174a417eb206131920a4a7fbf76357df387db54`**, committed before any candidate supply or new outcome was examined |
| Champion | `data/public_top_picks/registry.json` sha256 `ca408175…`; public grades `results/grades_*.json`; `history.json` `4d17969e…` |
| Candidate universe | 5,005 `docs/data.json` commits on main since 2026-08-16 (blobless partial clone); each game's final pregame state = the last commit ≤ first pitch − 10 min; 224 payloads read; per-candidate commit SHA and odds time in `out/universe.json` |
| Outcomes | Official MLB StatsAPI regular-season game logs (FC-ACC-014 raw cache, retrieved 2026-10-22; includes runs and RBI). The StatsAPI grader agrees with the public grades on **542/542** champion picks. |
| Periods | 35 graded regular-season slates (2026-08-18 → 09-27). **P1** (least-exposed, primary): 22 slates. **P_dev** (used in FC-ACC-017): 13 slates. No independent period exists; everything is **exploratory**. |

## 2. Volume feasibility (measured before any outcome; `out/selection.json`)

| | P1 target | B (market-only) filled | C (hybrid) filled |
|---|---|---|---|
| Original N (champion uncapped) | 305 | 105 (34%) | 99 (32%) |
| Cap-comparable N_cap (champion after ≤ 2 per game, 1 per player) | 213 | 100 (47%) | **94 (44%) → fails the 0.95 gate** |

**Why supply fails (real data, not a threshold choice)**
- **The dashboard's retained history does not preserve the full pregame candidate set at each game's decision time:**
  - the payload `date` rolls over to the next slate during the evening, so late games' final pregame state belongs to the next day;
  - the payload sheds rows through the day: Sept 13 went from about 1,700 rows earlier, to 182 at 22:45, to 13 (Top Picks only) at 23:20.
- **Zero-supply slates:** 4 of the 22 P1 slates have no candidate rows at all.
- **Full fill:** only 6 P1 slates fill N_cap.
- **Champion coverage:** only **25%** of the champion's own P1 picks appear in this universe. The population differs from the one the champion selected from.
- **Champion concentration:** up to 9 picks in one game. The cap would remove 92 of its 305 P1 picks; A_cap is a constructed benchmark, not the public record.
- **The prereg forbids changing the population, thresholds or caps to pass**, so none of those was done.

## 3. Outcomes (descriptive only: unequal volume; P1, cap-comparable)

| Arm | Picks | Wins–losses | Hit rate | Market-expected wins | ROI at recorded prices (**unauthenticated**) |
|---|---|---|---|---|---|
| A_cap, champion | 213 | 113–100 | 53.1% | 114.5 | −11.6% |
| B, market-only favorite | 100 | 70–30 | 70.0% | **70.1** | −7.8% |
| C, frozen hybrid | 94 | 67–27 | 71.3% | 64.2 | −3.4% |

**Hit-rate differences, 95% CIs clustered by slate and by game**
- **C − A:** +18.2 points; slate [+8.7, +27.7], game [+7.7, +28.6].
- **B − A:** +16.9 points; slate [+6.2, +27.6], game [+6.0, +27.8].
- **C − B: +1.3 points; slate [−6.3, +10.0], game [−8.6, +10.8]. No model alpha.**

**Added and removed picks**
- **C vs A:** added 86 (61 wins), removed 205 (107 wins).
- **C vs B:** added 67 (48 wins), removed 73 (51 wins). The model filter neither helps nor hurts.

**Other views**
- **P2 (all 35 slates, cap-comparable):** C 71.3% vs A 52.3% vs B 67.3%; C − B +3.9 [−3.5, +11.8].
- **Same-count subset** (the 9 slates where both B and C filled N_cap exactly; inclusion depends only on supply; post hoc, descriptive):
  - P1 (6 slates, 45 picks): A_cap 24, B **37**, C 34 wins;
  - all 9 slates (78 picks): A_cap 39, B **64**, C 58 wins.
- **Stability:** August and September point the same way. Favorite picks are about 97% H+R+RBI.
- **Price concentration:** 93% of B's picks and 79% of C's are priced at −250 or shorter, where break-even is 71–78%.

## 4. Falsification checks

| Check | Result |
|---|---|
| Equal-volume supply | **Fails** (44% / 47%) |
| Late-season or postseason dependence | Postseason not graded; August and September consistent |
| Missing historical candidate eligibility | **Severe**: retained dashboard states are partial (§2) |
| Quote availability and staleness | Odds fetched no more than 45 minutes before the decision state; prices unauthenticated |
| Dashboard vs frozen-board population | Different; only 25% of the champion's picks are present in the universe |
| Game and player correlation | B and C are capped at 2 per game and 1 per player; the champion is not (cap-comparable benchmark disclosed) |
| Extreme short odds | Yes: most favorite picks sit at −250 to −350 |
| False volume preservation | Confirmed: FC-ACC-017's "supply on full slates" does not hold for decision-time states |
| Does the model condition exclude good favorites? | It is neutral (C vs B added and removed win rates are about equal) |
| Economics | Favorites win about 70%, below the 71–78% break-even of their prices; ROI stays negative (unauthenticated) |
| Model alpha | **None.** Each arm wins about its market-expected total; "favorites win more" is price, not model |

## 5. Answer and the one next action

- **Can the favorite rule be shown to add winners at the same volume?** **No.** The retained history cannot support an equal-volume replacement claim. Its apparent advantage is the market's own higher probability for short-priced props, and it comes with negative economics.
- **The 72% target, read honestly:** a hit rate near 70% is reachable only by selecting heavily favoured props, at a price that loses money. Nothing here shows the model adding winners beyond the price.
- **One next action (the smallest decisive measurement):**
  - persist the complete pre-decision dashboard candidate set (rows, line, price and odds time) for every dashboard run, so that equal-volume comparisons become possible;
  - this is the inactive PR #230 champion decision export (Codex FC-ACC-009 / FC-OPS-002);
  - then run this frozen FC-ACC-018 comparison (A vs B vs C, ≤ 2 per game, ≥ −350) unchanged as a prospective shadow.
  - Activation requires Jacob's authorization and Codex's ownership. Nothing was activated here.

Alligator

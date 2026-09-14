# FULL COUNT NFL passing-yards baseline research

Date: 2026-09-14

Status: research-only negative result. No model, selector, probability method, public pick, grading path, or production workflow was promoted or changed.

## Question and fixed challengers

The current B0 control predicts quarterback passing yards as the mean of the player's last five appearances, with at least three appearances and positive rolling attempts. The full 1999–2025 audit made it possible to test two simple opportunity-aware ideas without tuning them on the result:

- C1 excludes prior zero-attempt appearances, then averages passing yards over the last five passing-role appearances.
- C2 multiplies mean attempts over the last three passing-role appearances by aggregate yards per attempt over up to eight passing-role appearances.

The partitions were fixed as development 2000–2019, validation 2020–2022, and held evaluation 2023–2025. Comparisons use identical rows where both B0 and the challenger have a prediction. The held uncertainty interval resamples player clusters with 2,000 deterministic bootstrap iterations.

## Reproduction

Before evaluating challengers, the script rebuilt the active 2023–2025 history window. It reproduced the frozen workflow checks exactly:

| Season | Rows | B0 MAE |
| --- | ---: | ---: |
| 2024 | 611 | 70.8354337152 |
| 2025 | 617 | 72.4199081578 |

The source loader verifies all 27 cached files against the full-audit byte counts and SHA-256 digests before computing any result. No QB row violated `completions <= attempts`, finite passing fields, or the zero-attempt production invariant.

## Results

Lower MAE is better; positive delta means worse than B0.

| Partition | Challenger | Paired rows | B0 MAE | Challenger MAE | MAE delta |
| --- | --- | ---: | ---: | ---: | ---: |
| Development 2000–2019 | C1 passing-role last 5 | 11,286 | 70.0740 | 70.7176 | +0.6436 |
| Development 2000–2019 | C2 attempts 3 × YPA 8 | 11,286 | 70.0740 | 71.5958 | +1.5219 |
| Validation 2020–2022 | C1 passing-role last 5 | 1,774 | 70.8266 | 71.4437 | +0.6171 |
| Validation 2020–2022 | C2 attempts 3 × YPA 8 | 1,774 | 70.8266 | 72.6412 | +1.8146 |
| Held 2023–2025 | C1 passing-role last 5 | 1,862 | 71.7217 | 73.7419 | +2.0202 |
| Held 2023–2025 | C2 attempts 3 × YPA 8 | 1,862 | 71.7217 | 73.3637 | +1.6421 |

The held player-cluster 95% bootstrap interval for C1's MAE delta was +0.9511 to +3.3360 yards. C2's interval was +0.1560 to +3.2560 yards. Both fixed challengers are rejected: the data does not support replacing B0 with either formulation.

Both challengers also increased positive prediction bias in held data from +3.60 yards for native B0 to about +8.3–8.4 yards. Removing zero-attempt appearances and separating short-window attempts from longer-window efficiency appears to overstate continuing passing role for this broad QB population.

## Limits and next research

- The evaluation population is every regular-season QB row meeting prior-history rules. It is not the smaller population of quarterbacks actually offered a passing-yard line by a sportsbook.
- Filtering targets by their realized attempts would use postgame information and was deliberately avoided.
- The present-day nflverse release is not a point-in-time historical feature store.
- MAE alone does not establish probability calibration, price value, side selection, tail behavior, or profitability.

The next challenger should add facts that are available before kickoff and directly describe opportunity: confirmed starter/role, expected plays, neutral-situation pass rate, opponent pace and pressure, weather, offensive-line availability, and market line/price. Those features need point-in-time provenance and prospective capture. More rearrangements of the same short rolling box score should have low priority.

Machine evidence: `engineering/evidence/nfl_passing_yards_baseline_research_2026-09-14.json`. Reproduction script: `nfl/research/passing_yards_baseline_research.py`.

Alligator

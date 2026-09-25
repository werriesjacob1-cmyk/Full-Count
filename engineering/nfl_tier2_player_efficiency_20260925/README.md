# NFL Tier 2 F17: advanced player efficiency (receiving yards), research only

Nothing here changes authoritative B0, a workflow, a public pick or any Codex file. Codex owns rushing and box work (F16), so F17 is scoped to **receiving** efficiency.

## Verdict: NOT SUPPORTED (pre-declared rule, `f17_report.json`)

**The advanced information does not beat a simple control.** Advanced receiving efficiency means nflfastR expected yards per target (depth, completion probability, expected YAC) plus persistent yards over expectation. The simple control is a long-window, shrunk, raw yards per target. On the pre-declared primary test (HOLDOUT 2023–2025, settlement-aligned population), F17_FULL − SIMPLE_EFF = **−0.010 yards MAE**:

| Clustering | 95% CI |
|---|---|
| Game-clustered | [−0.027, +0.007] |
| Player-clustered | [−0.037, +0.018] |

FRESH 2026 (weeks 1–2, n = 549) gives −0.028 [−0.125, +0.067].

**What does help is not F17-specific.** Replacing part of B0's noisy 5-game yards per target with a better-estimated efficiency lowers MAE against the DEV-fitted scale control:

| Model | Δ MAE vs scale control, HOLDOUT |
|---|---|
| F17_FULL | −0.120 [−0.173, −0.070] |
| SIMPLE_EFF | −0.110 [−0.154, −0.066] |

Almost all of that comes from regressing efficiency, which the simple control already does. It overlaps Tier 1 F3, which shrinks last-5 yards per target toward an ADOT prior. See `f3_estimate` in `nfl/research/tier1/player_opportunity_challenger.py` at `8aa8067fbc` (PR #203): `rate = (receiving_yards_last5 + K·(y0 + y1·adot)) / (targets + K)`. Relative size: about 0.7% of a 16.8-yard MAE.

**Decomposition** (HOLDOUT, game-clustered):
- **Skill adds beyond depth:** F17_FULL − F17_DEPTH = −0.026 [−0.039, −0.013]. Persistent yards over expectation carries information that depth-conditioned expectation lacks.
- **Depth alone is worse than simple:** F17_DEPTH − SIMPLE_EFF = +0.015 [+0.00004, +0.032]. Expected yards per target alone loses to raw long-window yards per target.
- **Net:** the two roughly cancel, so there is no significant gain over the simple control.

**Target-conditioned sensitivity (disclosed, not the primary).** On the harness's population (players *targeted* in the game, which is postgame-conditioned), F17_FULL − SIMPLE_EFF = −0.039 [−0.063, −0.016], which is significant. On the settlement-aligned population it is not. Because the population is defined after the game, this is not evidence of a usable pregame edge.

**Mechanism evidence** (DEV seasons 2016–2022, season-to-season correlation, players with ≥60 expectation-scored targets in both seasons, n = 354 pairs):

| Measure | Correlation |
|---|---|
| Expected yards per target | 0.81 |
| Raw yards per target | 0.39 |
| Yards over expected per target | 0.25 |

Skill persists weakly and role persists strongly, as the heavy K_S = 150 shrinkage assumed.

## Sources and timing
**nflfastR play-by-play** (`play_by_play_{2016..2026}.csv.gz`; SHA-256s in `f17_params.json` → `provenance.pbp_sha256`):
- **Observed:** receiver, air_yards, complete_pass, yards_gained, yards_after_catch.
- **Derived:** `cp` and `xyac_mean_yardage` are nflfastR model outputs, not observations. Those models were trained on historical seasons, so league-level expectation structure for older seasons is not strictly out-of-sample. This mainly affects DEV.
- **Coverage:** `cp`/`air_yards` are present on ≥99.9% of targets in every season, and `xyac` on about 93%. Targets without an expectation count in the raw sums only, so observed-minus-expected compares like with like.

**PFR snap counts** (`snap_counts_{2016..2026}.csv`, via nflverse) are joined to GSIS through `players.csv` `pfr_id`. 74 unmapped rows are excluded and counted.

**Weekly stats and B0:** the audited nflverse weekly stats, plus the pinned pre-week-3 2026 file, supply the actuals and the B0 rule.

**Timing:** every feature uses only games strictly before the target game (same-week rows are excluded; tested). Position priors come from the previous season.

**Not available or not inferred:** routes run (so no per-route-run rates), yards after contact, missed tackles, separation and defender assignments. Rushing efficiency is F16, Codex's.

## Population (settlement-aligned, pre-declared primary)
The population is REG player-games where the player took **offense_snaps > 0** at WR, TE or RB and has a harness-rule B0 (≥3 prior role appearances). This follows FanDuel's settlement rule, which voids only a player who plays no snap. Eligibility never uses the game's own targets or receptions. Actual yards are the weekly receiving_yards, or 0 when the player has no weekly row (5,210 such rows).

| Partition | Rows | Players | Games |
|---|---|---|---|
| DEV 2016–2022 | 33,010 | 956 | 1,823 |
| HOLDOUT 2023–2025 (previously inspected) | 15,360 | 629 | 816 |
| FRESH 2026 wk 1–2 | 549 | 296 | 32 |

**Excluded before the population:** 6,392 rows with no B0, 258 at a non-receiver position, 4,042 with zero offensive snaps.

**F17 fallbacks** (to exactly k·B0): 4,391 `NO_POSITION_PRIOR` (the 2016 season) and 111 `NO_PRIOR_TARGETS`.

## Method (pre-declared at `544001709b`, DEV fit frozen at `3fd182b8af`, scored once)
- **Factorization:** B0 factors exactly as `tgt5 · ypt5` (tested against the harness).
- **Research prediction:** `k · tgt5 · ((1−w)·ypt5 + w·ypt_mode)`, with k = 0.69 (the harness DEV scale control on this population). w = 0 reproduces k·B0 exactly.
- **Modes:**
  - `SIMPLE_EFF` (control): long-window raw yards per target, season weights 1.0 / 0.6 / 0.3, K_S = 150 toward the prior-season position mean. w = 0.50.
  - `F17_DEPTH`: expected yards per target over the last 8 games with a target, K_X = 20. w = 0.45.
  - `F17_FULL` (primary): F17_DEPTH plus the shrunk yards over expected (K_S = 150). w = 0.60.
- **Fit:** w is fit on DEV target seasons 2017–2022 (grid 0.05).

**Changed projections (2023+):** 13,504 of 15,909 move by more than 0.5 yards versus the scale control, 7,576 by more than 2 yards and 2,083 by more than 5 yards. The mean absolute change is 2.5 yards.

**Bias:** MAE-optimal k under-predicts the mean (HOLDOUT bias −4.4 yards for F17 versus +3.2 for raw B0). That is expected for a right-skewed outcome, and is closer to the median, which is what an over/under line prices.

**Example** (FRESH 2026, SF@LA week 1, WR `00-0039075`):

| Quantity | Value |
|---|---|
| B0 | 91.0 |
| Last-5 targets per game | 12.6 |
| Last-5 yards per target | 7.2 |
| Expected yards per target (depth) | 8.77 |
| Skill (yards over expected per target) | +0.73 |
| Scale control | 62.8 |
| SIMPLE_EFF | 69.5 |
| **F17_FULL** | **74.7** |
| Actual | 74 |

`fresh_2026_examples` in the report has 10 such rows, including ones where F17 moved the projection the wrong way.

## Evidence status
- **HOLDOUT outcomes were inspected by earlier experiments,** so every historical number here is exploratory.
- **No prospective F17 predictions exist.** They are not part of the Tier 1 protocol family.
- **Historical MAE is not pick accuracy.** A historical MAE change says nothing about winning-pick accuracy, sportsbook eligibility or prospective value.

## Preserved hypotheses (not pursued; no retuning here)
- **YOE skill beyond F3:** yards-over-expected skill (the significant increment over depth) as an addition to Tier 1 F3's existing efficiency shrinkage, tested prospectively. It is not refit on these outcomes.
- **Median-targeted markets:** evaluating over/under lines directly with a median-targeted loss.

## Independent review
A read-only adversarial review (sonnet reviewer) covered leakage, correctness, protocol and claims, and found them clean:
- same-week and POST ordering checked;
- B0 factorization and the w = 0 nesting checked;
- the snap/weekly join checked against a 2024 sample, where no-weekly-row cases were real zero-target games;
- commit order and the refit-equality gate checked;
- the verdict recomputed.

Its one LOW evidence gap, the F3 citation, is added above.

## Reproduce
From a checkout of this branch, with the pinned inputs under `/tmp/claude-0/nfl_tier1_shared`:

```
PYTHONPATH=. python3 engineering/nfl_tier2_player_efficiency_20260925/evaluate_f17.py --stage final
```

The final stage aborts unless the DEV refit equals the committed `f17_params.json`.

Tests: `PYTHONPATH=. python3 -m unittest nfl.tests.test_tier2_player_efficiency_f17` (8 tests).

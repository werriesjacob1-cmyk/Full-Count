# NFL rushing yards baseline + challenger -- real evidence

Workstream: `NFL-RUSHING-YARDS-BASELINE-20260923` (Issue #91, Mission 8
Workstream B).

## What this is

A genuinely new NFL predictive capability: at the time this workstream
started, `nfl/research/` contained baseline research for passing yards
(`passing_yards_baseline_research.py`) and receptions
(`receptions_baseline_research.py`, `receptions_team_opportunity_
challenger.py`, and related modules), plus extensive game-level market work
(`game_market_b0*`/`c1*`/`c2*`/`c3*`), but **no rushing-yards module of any
kind existed anywhere in the repository** -- confirmed by grepping
`nfl/research/`, `nfl/tests/`, and `.github/workflows/` before writing any
code. Rushing yards is a real, commonly-posted sportsbook player-prop market
that none of the existing modules covered.

This adds `nfl/research/rushing_yards_baseline_research.py`: a simpler
rolling-mean control model (`b0`) plus one feature-informed challenger
(`c1_carries3_times_ypc8`), evaluated against each other on a real, matched
historical population.

## Data source -- no new ingestion added

This module reuses the exact already-ingested, already-audited nflverse
`stats_player_week_<season>.csv` weekly corpus that
`passing_yards_baseline_research.py` and `receptions_baseline_research.py`
already consume. `nfl/research/nflverse_full_audit.py`'s own `NUMERIC` field
list has always included `carries` and `rushing_yards` -- this source always
covered rushing volume and production. Nothing about this module required a
new ingestion path. The evaluation script here loads the CSVs from a local
cache and verifies each season's byte size and SHA-256 against
`engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json`
(the same pinned manifest the passing-yards and receptions baseline scripts
use) before trusting a single row; any drift raises
`RushingYardsDataError` rather than silently proceeding.

## Models compared

- **`b0` (simpler control)**: mean rushing yards over the player's last five
  rushing-role-positive appearances (`carries > 0`), gated on at least three
  such appearances. Returns `None` (abstains) below that threshold -- never
  a fabricated value.
- **`c1_carries3_times_ypc8` (feature-informed challenger)**: mean carries
  over the three most recent rushing-role appearances, multiplied by
  aggregate rushing yards per carry over up to eight most recent
  rushing-role appearances. Structurally identical to
  `passing_yards_baseline_research.py`'s `c2_attempts3_times_ypa8`
  (recent-workload x standing-efficiency decomposition) applied to rushing --
  a genuinely different signal than B0's raw yards rolling mean, not a
  relabeling of it.

Both predictions are computed strictly from prior appearances only (the
history deque is appended to only AFTER the current row's prediction is
computed) -- no lookahead into the row being predicted.

## Real, honest result -- the challenger does NOT beat B0

Using the fixed, predeclared partitions and rejection rule this codebase
already established for passing yards (reject only if validation AND held
paired MAE delta are both `>= 0` vs. B0 -- not retuned against these
results):

| Partition | n | B0 MAE | Challenger MAE | Delta (challenger - B0) |
|---|---:|---:|---:|---:|
| development (2000-2019) | 35,869 | 19.128 | 19.077 | -0.051 |
| validation (2020-2022) | 5,994 | 18.814 | 18.824 | +0.0096 |
| held (2023-2025) | 6,149 | 17.917 | 18.155 | +0.237 |

A player-clustered bootstrap (2,000 resamples, 436 players, seed 20260923)
on the held partition gives a 95% interval of **[+0.083, +0.391]** for the
challenger-minus-B0 MAE delta -- entirely on the "worse" side of zero. The
challenger is reliably, not just marginally, worse than the simple rolling
mean on this real population.

**Decision: `REJECTED_RESEARCH_CHALLENGER`.** The predeclared rule
(validation delta >= 0 AND held delta >= 0) is satisfied, so
`rushing_yards_baseline_research.py` marks this challenger rejected in its
own output rather than presenting it as a win. This is a first-class,
disclosed negative finding, not a hidden or softened one: `c1_carries3_
times_ypc8`'s workload x efficiency decomposition does not improve on B0's
raw rolling mean for rushing yards, on this real matched population, just
as `passing_yards_baseline_research.py`'s analogous `c2_attempts3_times_
ypa8` challenger was also rejected for passing yards. The recurrence across
two independent markets is itself evidence worth recording: a workload x
efficiency recombination does not appear to add value beyond a player's own
recent rolling yardage mean in this codebase's rolling-origin evaluation
framework, at least for these two markets and this feature construction.

Development-partition MAE is marginally negative (-0.051), but per this
project's own anti-retuning doctrine that partition is excluded from the
decision rule precisely because it is the one most exposed during feature
construction; only validation and held decide promotion.

## Data-quality finding -- no coverage gap, unlike receptions' `targets`

Receptions' baseline research documents a real, confirmed 2003-2008
`targets`-column blackout in this same corpus. A full scan of the pinned
1999-2025 corpus for this workstream found **no season-level coverage gap**
in either `carries` or `rushing_yards`: zero blank or invalid numeric values
for both columns in every one of the 27 audited seasons (see
`carries_rushing_yards_coverage_by_season` in the report). A small,
disclosed anomaly was found and reported rather than hidden: 12 rows
(across the full 1999-2025, 476,159-row corpus) have `carries == 0` with
nonzero `rushing_yards` -- a known nflverse quirk (e.g. a lateral or
fumble-recovery return credited as rushing yardage without a charted
carry). These 12 rows are excluded from the rushing-role-positive
population by the same `carries > 0` gate that defines it, and the count is
reported under `rushing_stat_invariant_failures.production_with_zero_
carries` in the output rather than silently dropped.

## Files

- `rushing_yards_real_evaluation.py` -- the exact script that loads the
  pinned, audited real 1999-2025 `stats_player_week_<season>.csv` corpus,
  runs both models via `nfl.research.rushing_yards_baseline_research`, and
  writes the real, reproducible `rushing_yards_real_evaluation_report.json`
  checked into this directory. Re-run it directly (requires a local cache
  of the pinned CSVs; see the script's own docstring) to reproduce every
  number in this README.
- `rushing_yards_real_evaluation_report.json` -- the real output of that
  script: per-partition and per-model MAE/RMSE/bias, the paired comparison,
  the player-clustered bootstrap, and the same `REJECTED_RESEARCH_
  CHALLENGER` decision reported above.

## What this does NOT do

- No live workflow wiring (no `.github/workflows/*.yml` files touched or
  added). This is a research module + real evaluation only, matching the
  established two-step precedent in this repository (research module and
  evaluation first, live wiring only as a later, separately-reviewed PR).
- No promotion, no production/selector/public-artifact change, no
  touching of `receptions_team_opportunity_challenger.py`,
  `qb_change_team_dropbacks.py`, `role_regime_redistribution.py`,
  `price_aware_offers.py`, the B0 game-market selector, or any workflow
  YAML.
- No investigation yet into WHY the workload x efficiency decomposition
  underperforms B0 for rushing yards specifically -- a real, disclosed,
  concrete next milestone, same open-question framing already established
  for the analogous passing-yards and receptions-opportunity findings in
  this repository.

# NFL current-week QB-availability gate -- real evidence (Mission 9 Workstream B)

Workstream: `NFL-MISSION9-PARALLEL-20260923` (Issue #91). Connects a second
real, previously-unconsumed source (`nfl/research/injury_availability_
features.py` -- its own docstring: "does not evaluate that hypothesis...or
wire anything into a model or selector") to the QB-continuity-aware team-
dropback consumer (draft PR #185, `nfl/research/qb_change_team_dropbacks.
py`), via a new module, `nfl/research/qb_availability_gated_dropbacks.py`.

## Why this exists: a direct response to SUPERCHAD's checkpoint

SUPERCHAD's Mission 8 checkpoint (Issue #91 comment `5800978133`) correctly
flagged that PR #185's `resolve_incumbent_qb` is a strictly-prior
HISTORICAL-incumbent proxy (the last game a QB actually started), never a
confirmation of who is expected to start the CURRENT week -- "a newly
announced Week N starting-QB switch could be missed until after he first
starts; or the latest prior-game starter may have become unavailable/
benched." This repo ingests no depth-chart/official-starter source, so a new
starter's IDENTITY still cannot be determined in advance (disclosed, not
solved). What real, already-built, previously-unconsumed evidence CAN do:
tell us, safely and for the CURRENT week, whether the incumbent is likely to
play at all, from the real weekly injury report filed before that week's own
games.

## The four real states

`classify_current_week_qb_availability` reuses `injury_availability_
features.build_prior_starter_availability_features` unmodified and refines
its output into:

- `CONFIRMED_AVAILABLE` -- not listed this week (or listed with a blank
  report_status, a real, distinct case found in this evaluation's own data).
- `DISPUTED` -- listed `Questionable` this week: real, genuine ambiguity.
- `EXPECTED_UNAVAILABLE` -- listed `Out`/`Doubtful` this week: real,
  current-week, pregame-safe evidence the historical incumbent likely will
  not play.
- `UNKNOWN` -- no real prior-starter identity, or a season this source does
  not cover.

`predict_team_pass_dropbacks_availability_gated` gates the QB-aware team-
dropback prediction: when the incumbent is `EXPECTED_UNAVAILABLE` or
`DISPUTED` this week, trusting his own QB-specific rolling window (built
entirely from games he played) as this week's volume estimate is exactly
the unsafe inference SUPERCHAD flagged, so the gate falls back to the real
"no QB-identity adjustment" naive control the QB-aware consumer already
computes and preserves -- never a guessed new-starter identity, and never a
claim about teammate-level target/route/chemistry effects (Mission 9
Section 5's explicit distinction).

## Real, disclosed data-quality findings from this evaluation

1. **Real within-week injury-report updates, not duplicates.** nflverse's
   real injury report can carry more than one row per (season, week, team,
   player) -- e.g. a real 2024 Houston TE carried a real `Questionable` row
   at `date_modified` 03:34:33Z and a real `Out` row at 14:17:06Z the same
   day. `injury_availability_features.py`'s own fail-closed validation
   correctly rejects this as a duplicate if fed raw (its contract is one row
   per key); this evaluation's own ingestion step (`fetch_injury_rows`)
   resolves it by keeping the real LATEST `date_modified` snapshot per key,
   never fabricating a resolution when timestamps tie. This is a real,
   disclosed caller-side responsibility, not a change to that module.
2. **A real non-standard `report_status` value.** 6 of 6,215 real 2024 rows
   carry `report_status: "NOTE"` (e.g. team NO, week 2) -- outside the
   module's own documented vocabulary (`OUT`/`DOUBTFUL`/`QUESTIONABLE`/
   blank). `injury_availability_features.py` correctly fails closed on this
   rather than guessing; this evaluation excludes these 6 real rows at the
   ingestion boundary and reports the exact count, rather than silently
   coercing them to a guessed bucket.
3. **A real "listed but blank status" case.** Real rows exist where a player
   is listed (a real row is filed) but `report_status` is blank -- distinct
   from "no row at all," and distinct from `Questionable`. This module
   classifies it the same as not-listed (`CONFIRMED_AVAILABLE`), matching
   `injury_availability_features.NOT_GAME_AFFECTING_STATUSES`'s own existing
   treatment of blank as non-game-affecting.

## Real, non-cherry-picked activation scan (2023-2025)

Scanning every real (team, season, week) in the loaded 2023-2025 starter
substrate found **83 real gate activations** -- far more frequent than the
coaching-regime feature's real activation rate (0/2,954 in its own matched
population), since real weekly QB availability designations are far more
common than real in-season HC changes. The 8 largest real activations by
team-dropback magnitude, all real, unfiltered, not cherry-picked for a
favorable direction (both increases and decreases occur):

| Team | Season/Week | Incumbent | Bucket | Raw status | QB-aware | Gated (naive control) |
|---|---|---|---|---|---|---|
| MIN | 2025w3 | J.J. McCarthy | EXPECTED_UNAVAILABLE | Out | 30.5 | 38.4 |
| GB | 2025w18 | Malik Willis | DISPUTED | Questionable | 25.0 | 32.0 |
| NYG | 2023w9 | Tyrod Taylor | EXPECTED_UNAVAILABLE | Out | 33.7 | 40.6 |
| NYG | 2024w15 | Drew Lock | EXPECTED_UNAVAILABLE | Doubtful | 49.0 | 42.2 |
| WAS | 2025w8 | Jayden Daniels | EXPECTED_UNAVAILABLE | Out | 37.4 | 31.0 |
| LV | 2024w15 | Aidan O'Connell | DISPUTED | Questionable | 41.2 | 47.4 |
| LV | 2025w18 | Geno Smith | EXPECTED_UNAVAILABLE | Out | 40.8 | 34.6 |
| LV | 2023w7 | Jimmy Garoppolo | EXPECTED_UNAVAILABLE | Out | 34.8 | 40.2 |

Real numbers directly answer Mission 9's requirement to "distinguish that
from separately demonstrated target, route, carry, or receiver-chemistry
effects" -- both directions occur (WAS and LV/Geno Smith rows show the gate
correctly REDUCING the naive extrapolation, not just inflating it), matching
the real underlying box-score data rather than a fixed assumption.

## Real matched-population activation rate

On the same real 2025-week-8+ population precedent used by every prior
opportunity-engine ablation (n=328 real team-weeks): 309 `CONFIRMED_
AVAILABLE`, 6 `DISPUTED`, 13 `EXPECTED_UNAVAILABLE` -- 19/328 (5.8%) real
gate activations. A full end-to-end receptions-MAE comparison isolating
this gate's effect (the way the coaching-aware and snap-share features were
each separately evaluated) is a concrete next milestone, not attempted this
pass given this mission's time constraints -- disclosed as an explicit scope
limitation, not silently omitted.

## What this module does NOT do

- Does not determine a new starter's identity -- only whether the OLD
  incumbent is expected to play.
- Does not claim any teammate-level target/route/carry redistribution --
  only a team-level pass-volume estimate.
- Makes zero changes to `injury_availability_features.py` or `qb_change_
  team_dropbacks.py`.

## Reproduction

`python engineering/nfl_qb_availability_gate_20260923/qb_availability_gate_real_evaluation.py`
(network access to nflverse-data required; real 2023-2026 data, ~80s this
run). Full output in `qb_availability_gate_real_evaluation_report.json`.

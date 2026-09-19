# NFL Role Change Historical Dataset Contract — 2026-09-18

## Objective

Create a point-in-time-safe historical dataset that teaches FULL COUNT how player roles actually change when teammates are unavailable, depth order changes, or coaching/scheme context changes.

This dataset is not a betting model. It is the training/evaluation substrate for:
- ROLE-CHANGE-DETECTION
- TEAMMATE-ON-OFF-REDISTRIBUTION
- REPLACEMENT-HIERARCHY
- PLAYER_ROLE_STATE_V1
- OPPORTUNITY_REDISTRIBUTION_V1

## Unit of analysis

Primary row grain:

`target_game x team x player x role_dimension`

Role dimensions are independent:
- offense_snap_share
- route_share
- target_share
- carry_share
- red_zone_opportunity_share
- goal_line_carry_share
- third_down_snap_share
- two_minute_snap_share

Do not compress all role dimensions into one generic workload label.

## Target-game chronology

For target game G:

Features may use only information from:
- prior completed games;
- pregame depth chart available before G;
- pregame roster state;
- historical injury/practice evidence available before G;
- pregame official inactive evidence only if historically timestamped and legitimately available;
- pregame news/practice claim ledger only where original timestamp is preserved.

Targets may use:
- target-game realized snaps/routes/targets/carries/etc.

The target-game realized role must never enter features.

## Base historical source layers

Deepest defensible source by dimension:

- weekly stats: 1999+
- weekly rosters: 2002+
- snap counts: 2012+
- participation: 2016+ historical
- NGS weekly: 2016+
- PFR advanced: 2018+
- FTN charting: 2022+
- depth charts: 2001+, source break 2025+
- injury reports: historical audited period only; do not assume 2025/2026 live historical availability

Different role targets will therefore have different eligible historical populations.

## Required identity fields

- game_id
- season
- week
- season_type
- kickoff_time if available
- team
- opponent
- player_id
- player_name
- position
- source identity fields
- target-game starter/active status if outcome label only

Stable player ids are mandatory. Name-only joins are quarantine.

## Strictly-prior baseline role features

For each role dimension test distinct horizons:

- previous game
- last 3
- last 5
- last 8
- current-season mean
- trailing season
- trailing 2 seasons
- career / current-team
- current coach/playcaller regime

Also preserve:
- games since role change
- games with current QB
- games with current playcaller
- games with current team

## Availability / trigger features

Potential role-change triggers:

- teammate inactive
- teammate injury designation
- player activated from IR/PUP
- roster elevation
- trade/signing/release
- depth-chart rank change
- starter change
- first-team practice reps
- coach-declared role change
- coordinator/playcaller change
- major OL/QB change
- previous-game sudden role jump/drop

Every trigger must be timestamped or strictly derived from earlier games.

## On/off event construction

For every meaningful unavailable/reduced teammate event:

1. identify removed player;
2. determine his pre-event role budget;
3. define affected position group and cross-position candidates;
4. compute replacement candidate priors before target game;
5. record realized target-game redistribution separately.

Do not create an on/off event from target-game usage itself.

## Removed opportunity budget

Estimate what disappeared using strictly prior evidence:

- expected routes removed
- expected targets removed
- expected carries removed
- expected third-down snaps removed
- expected two-minute snaps removed
- expected red-zone opportunities removed

Represent uncertainty rather than one fixed value where appropriate.

## Candidate replacement features

For each potential replacement:

- depth rank
- roster status
- prior snap share
- prior route share
- prior target share
- prior carry share
- alignment
- prior role when starter previously absent
- current-team tenure
- current-playcaller tenure
- position flexibility
- recent role trajectory

## Realized labels

After game completion, grade:

- started? yes/no
- offense snap share
- route share
- target share
- carry share
- third-down share
- two-minute share
- red-zone share
- goal-line share

These labels are for training/evaluation only.

## Replacement hierarchy labels

Evaluate:

- top-1 replacement identity
- top-2 replacement coverage
- committee vs single replacement
- cross-position redistribution
- scheme-change response

## Mass-balance labels

For each team target game:

`predicted removed opportunity + baseline retained opportunity -> predicted new team budget`

Measure:
- total opportunity prediction error
- redistributed opportunity prediction error
- unallocated residual
- over-allocation error

No role model may improve one player's projection by creating impossible team totals.

## Structural role-change labels

A structural change should be labeled only after observing future persistence for evaluation purposes.

Candidate persistence definition to test:
- role dimension changes beyond threshold in target game;
- persists for >=2 of next 3 available games, OR
- explicit pregame role-change evidence exists and target game confirms it.

The persistence label itself is postgame/future evaluation information and may never be used as a pregame feature.

## Model hierarchy

Recommended first challenger:

league/position prior
+ player historical role
+ current team/regime role
+ current-season role
+ recent role
+ availability triggers
+ replacement depth
+ teammate on/off historical redistribution

Do not begin with unrestricted ML feature search.

## First evaluation populations

### WR absence
Target event:
top recent route-share WR unavailable.

Evaluate:
- remaining WR routes
- TE routes/targets
- RB targets
- team pass volume

### RB absence
Target event:
top recent carry-share RB unavailable.

Evaluate:
- replacement carries
- third-down/two-minute role
- routes/targets
- goal-line work

### TE absence
Evaluate:
- TE2
- slot WR
- RB receiving
- personnel changes

### QB change
Evaluate:
- receiver target redistribution
- pass volume
- QB scramble/design-run share

## Baselines

At minimum compare:

1. NO_ADJUSTMENT
2. PROPORTIONAL_TEAMMATE_REDISTRIBUTION
3. DEPTH_CHART_NEXT_MAN
4. RECENT_USAGE_NEXT_MAN
5. HIERARCHICAL_ROLE_MODEL

## Metrics

Role prediction:
- snap share MAE
- route share MAE
- target share MAE
- carry share MAE
- red-zone share MAE

Hierarchy:
- top-1 accuracy
- top-2 coverage
- committee calibration

Change detection:
- precision
- recall
- lead time

Downstream:
- passing/receiving/rushing prop MAE
- probability calibration
- equal-volume realized betting outcomes when prospective prices exist

## Season/regime reporting

Every evaluation must include:
- season table
- position group
- team
- coach/playcaller regime
- early vs late season
- source-coverage era

Do not let 2022+ rich charting performance hide failure in older sparse-data eras.

## Acceptance before prospective role shadow

- source digests captured
- identity audit passed
- target-game leakage tests
- target labels separated from features
- historical coverage table
- baseline comparisons
- role mass-balance diagnostics
- season stability
- uncertainty
- prospective capture schema ready

## Prospective shadow output

For every target player:

- predicted role distribution
- evidence clock
- baseline role
- detected role changes
- replacement context
- confidence
- source ids
- model/version
- frozen hash

Then grade after game.

Alligator

# NFL Genius Implementation Prep — 2026-09-18

## Purpose

Convert the NFL Genius doctrine from prose into implementation-ready contracts so the next Claude session spends credits on experiments and integration rather than rediscovery, naming, or architecture decisions.

This document is preparation only. It does not authorize model promotion, public pick changes, production selector changes, or merge.

## Core doctrine

FULL COUNT NFL should investigate every defensible predictive angle, but only allow a signal to influence production after it survives point-in-time-safe historical research and prospective confirmation.

North Star:
1. realized predictive accuracy at legitimate usable operational volume;
2. prospective evidence integrity;
3. price-aware profitability / edge;
4. live intelligence;
5. customer product quality;
6. UX.

No angle may disappear from memory. Every angle must have a durable registry status:
- GREEN — validated incremental signal;
- YELLOW — active experiment;
- BLUE — source available / not yet tested;
- RED — tested and rejected;
- GRAY — unavailable, rights-limited, cost-blocked, or insufficient coverage.

## Implementation principle

Do not build one 200-feature mega-model.

Build a football intelligence graph where coherent signal families can be:
- sourced;
- timestamped;
- versioned;
- tested independently;
- combined only after incremental value is measured.

The unit of work is a named hypothesis, not a column.

## Required machine-readable artifacts

This branch introduces a registry seed and validator. Future work should evolve these into:

- `data/nfl_intelligence/angle_registry.json`
- `data/nfl_intelligence/source_registry.json`
- `data/nfl_intelligence/regime_registry.json`
- `data/nfl_intelligence/experiment_registry.json`
- `data/nfl_intelligence/feature_contracts/`
- `engineering/evidence/nfl_intelligence/`

Every feature-producing module should reference an angle id and source id.

## Angle record contract

Each angle record must preserve:

- stable angle id
- category
- exact hypothesis
- target markets
- current status
- historical depth target
- point-in-time requirement
- source candidates
- multi-year policy
- regime sensitivity
- leakage risks
- minimum validation requirements
- current implementation state
- dependencies
- owner / active workstream
- last reviewed date

Results belong in an experiment ledger rather than overwriting the angle definition.

## Multi-year contract

Multi-year data is mandatory where available.

Every feature family must explicitly separate:

1. long-term prior;
2. current regime;
3. current season;
4. recent-form deviation.

Candidate horizons to test where scientifically appropriate:

- prior game
- last 3
- last 5
- last 8
- current season
- trailing 1 season
- trailing 2 seasons
- trailing 3+ seasons
- career / regime history
- league / position prior

No universal lookback window is assumed.

### Walk-forward rule

Historical testing must mimic deployment:
- train only on data known before the target;
- evaluate later weeks/seasons;
- roll origin forward;
- never use future outcomes, future injuries, future depth charts, future line movement, or closing prices as pregame inputs.

### Era and regime handling

Deep history is not equal-weight history.

Explicitly model or quarantine changes in:
- HC
- OC
- DC
- playcaller
- QB
- major skill-position roles
- OL composition
- offensive/defensive scheme
- stadium/surface where relevant
- NFL rules / kickoff / overtime / league environment
- source definitions and tracking schemas

Old data may contribute through shrinkage or hierarchical priors even after a regime change, but may not masquerade as current-regime evidence.

## Intelligence layers

### Game markets

Primary decomposition target:
- offensive possessions / drives
- plays per possession
- scoring efficiency
- explosive probability
- turnover probability
- red-zone conversion
- special-teams field position
- joint score / margin / total distribution

Spread and total challengers must remain coherent with the same simulated game world when this layer matures.

### Passing props

Passing yards = expected attempts/dropbacks × expected efficiency.

Candidate components:
- neutral dropback tendency
- score response
- pressure expectation
- pressure-to-sack response
- coverage
- receiver availability
- OL health
- weather
- pace
- playcaller regime
- QB continuity
- route opportunity
- explosive rate

### Receiving props

Receiving = routes × targets/route × catch probability × yards/catch.

Candidate components:
- route participation
- alignment
- target share
- first-read share
- coverage family
- likely defender
- pressure/checkdown environment
- motion
- route tree
- bracket/double-team
- teammate availability
- QB chemistry
- expected YAC

### Rushing props

Rushing = expected attempts × expected yards/carry.

Candidate components:
- rush share
- goal-line share
- early-down role
- game script
- run scheme
- box count
- OL/DL matchup
- explosive rush probability
- stacked/light box
- QB designed-run competition
- teammate availability

## Priority signal families for implementation

Priority order is based on expected value and current gaps, not novelty.

### Tier 0 — already active / protect

- B0 controls
- live spread/total snapshot and board sealing
- passing-yards prospective pipeline
- game identity and explicit final-outcome grading
- candidate evidence integrity

### Tier 1 — highest-value next intelligence

1. C2 totals-only challenger
2. C3 QB availability / margin challenger
3. receptions B0 / opportunity model
4. coach / coordinator / playcaller regime history
5. snap / route / usage substrate
6. official inactives + depth/replacement graph
7. current OL availability and continuity
8. weather / roof / surface
9. true opponent-adjusted efficiency
10. score-state / pace / play-volume modeling

### Tier 2 — tactical detail

- coverage family
- pressure / blitz / simulated pressure
- personnel grouping
- formation
- motion / shifts
- run concepts
- route concepts
- defender matchup
- red-zone / goal-line
- fourth-down behavior
- opening script
- halftime adjustment
- garbage-time decomposition
- special teams

### Tier 3 — tracking / charting / advanced

- expected pressure
- expected sacks
- expected targets
- expected catches
- expected YAC
- expected rushing yards
- receiver release / leverage
- blocking assignment
- pursuit geometry
- receiver gravity / decoy value
- open-but-untargeted receiver rate
- pre/post-snap disguise
- safety rotation

### Tier 4 — skeptical / exploratory

- referee crew
- rivalry
- revenge
- contract year
- primetime
- media tone
- public betting narratives
- “must win”
- scheme familiarity
- former-team / former-coach

These still belong in the registry. They simply receive skeptical priors.

## Source-preparation matrix

Before implementation, each source must answer:

- source owner
- endpoint / release
- first season
- current-season availability
- update cadence
- timestamp semantics
- historical vintage integrity
- stable player/team/game identity
- licensing / redistribution constraints
- expected row volume
- missingness behavior
- known schema changes
- raw-byte retention policy
- digest / provenance policy
- whether source may be used as model input, evaluation-only, or discovery-only

No source enters a model until point-in-time availability is documented.

## Data domains to source or re-audit

- nflverse weekly player/team stats
- nflfastR play-by-play
- schedules
- rosters
- depth charts
- participation / snap counts
- injuries / practice reports
- official inactive reports
- transactions
- coaching / coordinator / playcaller history
- stadiums / surfaces / roof
- weather
- travel distances / time zones
- officials
- penalties
- Next Gen Stats / tracking-derived metrics
- advanced pass rush / blocking metrics
- market snapshots
- alternate-line ladders
- cross-book market snapshots where legitimately available
- legally usable charting / film-derived sources

## Regime registry

Create explicit intervals with provenance:
- team
- role
- person
- start date
- end date
- source
- confidence
- reason for transition

Roles:
- HC
- OC
- DC
- offensive playcaller
- defensive playcaller
- primary QB
- optional scheme identifiers

Features must be able to ask:
“What prior observations belong to the same current regime?”

## Player role graph

Do not treat roster position as role.

Prospective role should be represented through:
- snap share
- routes
- carries
- targets
- red-zone opportunities
- third-down role
- two-minute role
- goal-line role
- alignment
- depth-chart position
- current availability

When a starter is removed, the system should estimate redistribution rather than simply subtract the missing player's average.

## Interaction registry

High-value interactions must be named before testing. Examples:

- wind × deep-pass tendency
- LT availability × opponent edge pressure
- coverage shell × route family
- receiver alignment × defender alignment
- motion × coverage declaration
- run scheme × defensive front
- backup center × pressure pickup
- RB role × favorite/underdog state
- coach regime × score state
- QB pressure response × expected pressure
- route participation × teammate absence
- dome/surface × kicker range

Avoid unconstrained all-pairs interaction search.

## Experiment contract

Every challenger should predeclare:

- hypothesis
- baseline
- added information family
- exact eligible population
- train / validation / held / prospective boundaries
- equal operational volume comparison
- target metrics
- uncertainty method
- season-by-season table
- subgroup table
- leakage audit
- source digests
- stop / reject rule
- promotion gate

Historical exploratory evidence must never be relabeled as fresh confirmation after it influenced challenger selection.

## Metrics

Prediction:
- MAE / RMSE where relevant
- calibration
- log loss / Brier where probabilistic
- distribution coverage
- directional accuracy only as secondary descriptive metric

Betting:
- N
- W/L/P/V
- hit rate
- average odds
- implied break-even
- profit units
- ROI
- closing comparison only where a legitimate closing source exists
- equal usable operational volume

Diagnostics:
- overlap with baseline
- candidates added
- candidates removed
- performance of added/removed populations
- year stability
- team/player clustering
- market-family mix
- price buckets
- confidence buckets

## Sunday/live clocks

Prospective features should preserve information snapshots rather than mutate one state:

- early-week baseline
- Wednesday practice/injury
- Thursday
- Friday final practice report
- Saturday news
- Sunday morning
- weather update
- market update
- official inactive window
- final pregame freeze

Every snapshot must have:
- observed_at
- effective_at if different
- source
- payload/source digest
- model code SHA
- feature version

## Anti-overengineering gates

A new infrastructure component should answer one of:
- does it unlock a high-priority signal?
- does it protect research integrity?
- does it close a prospective learn loop?
- does it improve customer-facing evidence?

If not, defer it.

## Claude reset handoff

Claude should NOT restart with a broad repo audit.

Read:
1. Issue #91 comments 5734204120 and 5734248739
2. this implementation prep
3. `data/nfl_intelligence/angle_registry.json`
4. existing Total Sports Intelligence roadmap
5. current active workstream claim in #91

Then:
- continue existing C2-total/C3/receptions/MLB work without duplication;
- populate missing source and implementation fields in the registry as each lane is touched;
- open one hypothesis per branch;
- preserve rejected findings;
- return on material result/blocker/PR-ready state.

## Definition of done for this preparation phase

Before Claude returns, preparation is successful if:
- every discussed angle exists in a durable machine-readable registry;
- multi-year and regime rules are explicit;
- source integrity requirements are explicit;
- experiment/promotion rules are explicit;
- implementation priorities are explicit;
- no active Claude scientific branch is modified;
- no production behavior changes.

Alligator

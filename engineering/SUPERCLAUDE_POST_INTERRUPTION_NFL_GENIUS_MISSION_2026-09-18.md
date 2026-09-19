# SuperClaude Post-Interruption Mission Packet — NFL Genius Phase 1

Use this ONLY after the interrupted four-lane work is materially finished or explicitly stopped/reported:

- NFL C2 totals-only
- NFL C3 margin availability
- NFL receptions B0/shadow
- MLB board-freeze grader

Do not restart those lanes if they already have durable results.

## Mission

Implement the first production-grade research substrates for the NFL Genius program without changing public picks or promoting any model.

Priority:
1. coach/playcaller regime substrate
2. role intelligence historical substrate
3. News/Practice/Press Conference prospective capture skeleton
4. 32-team intelligence source population
5. prospective role-change shadow capture

## Read first

- Issue #91 doctrine comments 5734204120, 5734248739, 5734555694
- Draft PR #136
- engineering/NFL_GENIUS_IMPLEMENTATION_PREP_2026-09-18.md
- engineering/NFL_GENIUS_ATOMIC_BACKLOG_2026-09-18.md
- engineering/NFL_GENIUS_NEWS_BRAIN_2026-09-18.md
- engineering/NFL_GENIUS_ROLE_INTELLIGENCE_2026-09-18.md
- engineering/NFL_ROLE_CHANGE_HISTORICAL_DATASET_CONTRACT_2026-09-18.md
- data/nfl_intelligence/angle_registry.json
- data/nfl_intelligence/source_registry.json
- data/nfl_intelligence/feature_contracts.json
- data/nfl_intelligence/team_intelligence_registry.json

Do not broad-audit the repo again unless a concrete dependency is unclear.

## Scientific rules

- no model promotion
- no selector replacement
- no public-pick change
- no fabricated historical news
- no fabricated historical odds
- no current-game role leakage
- no coordinator-title == playcaller assumption
- no postgame explanation used for same-game prediction
- no latest-only overwrite of practice/news state
- preserve negative results
- UNKNOWN is valid
- point-in-time evidence required
- multi-year history must remain regime-aware
- prospectively confirm before any production influence

## Workstream A — coach/playcaller regime substrate

Create isolated branch from current main.

Implement:
- canonical HC/OC/DC/playcaller interval model
- PFR team-season scaffold ingestion
- dated playcaller override mechanism
- deterministic lookup by target timestamp
- source provenance
- confidence
- ambiguous overlap fail-closed
- support shared/partial playcalling
- tests including midseason handoff

Deliver a coverage report:
- teams/seasons resolved
- unresolved playcaller intervals
- midseason changes
- source gaps

Do not wire into model yet.

## Workstream B — historical role intelligence substrate

Use engineering/NFL_ROLE_CHANGE_HISTORICAL_DATASET_CONTRACT_2026-09-18.md exactly as starting contract.

Build in stages:
1. weekly roster identity
2. snap-history substrate
3. depth-state substrate
4. role feature rows
5. absence/trigger events
6. replacement candidate rows
7. realized role labels
8. mass-balance diagnostics

Start with WR and RB.
Do not add unrestricted ML.

Baseline comparisons:
- no adjustment
- proportional redistribution
- depth-chart next man
- recent-usage next man
- hierarchical role model only after substrate is proven

Return historical coverage before fitting anything complicated.

## Workstream C — News Brain prospective skeleton

Do not attempt historical mass scraping first.

Implement schemas/storage for:
- atomic claim ledger
- source registry
- reporter registry
- contradiction graph
- claim type
- evidence class
- published_at/observed_at/effective_at
- player/team/game entity binding
- confidence
- correction/retraction
- source provenance

Create adapters ONLY for stable official sources first:
- NFL.com injury/practice
- official team press-conference/news pages where feasible

Beat writer/social ingestion may begin with registry + manually supplied URL/source adapter interfaces. Do not build brittle scraping against 32 social platforms in one branch.

## Workstream D — populate 32-team source registry

For all 32 teams:
- official team site
- official press conference/video location
- official injury/practice source
- HC media source
- OC/DC media source if consistently published
- >=3 candidate accredited beat reporters
- >=2 local outlets
- >=2 practice-observation sources where publicly available

Each row:
- source
- reporter
- outlet
- team
- source type
- public URL/account
- active status
- practice access indicator
- press access indicator
- claim categories
- last verified date
- rights/paywall notes

Do not assign subjective trust scores yet.
Reliability is earned from resolved claims later.

## Workstream E — prospective role shadow

Once role substrate is deterministic:

For each target player/game:
- baseline role
- role-change events
- replacement hierarchy
- opportunity redistribution
- uncertainty distribution
- evidence IDs
- source IDs
- information clock
- frozen hash

Research-only.

Grade after games:
- starter identity
- snaps
- route share
- target share
- carry share
- red-zone role
- hierarchy top-1/top-2
- mass-balance

## Agent discipline

One hypothesis/substrate per branch.
Claim branch in Issue #91 before editing.
Do not merge without Jacob.
Do not duplicate SUPERCHAD prep branch work.
If PR #136 is not merged, consume it as reference and copy only the minimum required contracts into your branch or branch from an approved reconciled base if directed by Jacob.

## Return conditions

Return only on:
- source contradiction
- point-in-time ambiguity
- licensing/access blocker
- historical coverage report complete
- branch PR-ready
- material scientific result
- Jacob decision required

## Required final report format

For each workstream:
- branch
- head SHA
- exact files
- data sources
- seasons/coverage
- PIT semantics
- tests
- scientific result
- unresolved gaps
- whether merge/promotion is requested (default NO)

End report with Alligator.

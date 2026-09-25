# NFL Genius News / Practice / Press Conference Brain — 2026-09-18

## Mission

FULL COUNT should know as much as legitimately possible about each team between games:

- practice participation and rep allocation;
- first-team / second-team work;
- position competitions;
- role changes;
- expected workloads;
- injuries and recovery;
- OL combinations;
- depth changes;
- scheme installation;
- coaching emphasis;
- player comments;
- coordinator comments;
- pregame press conferences;
- postgame explanations;
- transactions;
- local team reporting;
- beat-writer practice observations;
- contradictions and corrections.

The News Brain is an evidence system, not a sentiment engine.

## Core principle

A public statement or reporter post is not automatically a fact.

Each item becomes an atomic claim/observation with:

- who said/reported it;
- when it was published;
- when FULL COUNT observed it;
- what team/player/game it concerns;
- whether the reporter directly observed it;
- whether the claim is a quote, sourced report, inference, opinion, or official transaction;
- corroborating evidence;
- contradicting evidence;
- confidence;
- eventual outcome / resolution where scoreable.

## Source tiers

### Tier A — official factual events

Highest default confidence for narrow factual claims:

- official transactions;
- roster moves;
- official injury/practice report;
- official inactive list;
- league/team schedule changes;
- team depth-chart publications where applicable.

These can still contain timing/schema ambiguity, but the event itself is official.

### Tier B — official press conferences / direct quotes

Examples:

- head coach press conference;
- OC/DC press conference;
- player press conference;
- postgame media availability;
- official team interview.

NFL.com maintains a league press-conference channel and team sites such as Philadelphia and Kansas City maintain dated press-conference libraries. Current team pages show coach and player media throughout OTAs, training camp, the regular season, and post-practice periods.

Default interpretation:
- exact quote/speaker identity = high provenance;
- truth of strategic claim = not guaranteed.

A coach saying "we want to get Player X more involved" is a verified statement, not verified future usage.

### Tier C — accredited beat writer direct observation

Potentially extremely valuable:

- player took first-team reps;
- OL combination changed;
- player worked individually/on side;
- receiver rotated into slot;
- RB took goal-line reps;
- player left practice;
- player returned to team drills;
- coordinator spent period on a new package.

Must record whether reporter personally observed it.

### Tier D — beat writer sourced reporting

Examples:
- "team expects..."
- "I'm told..."
- "sources indicate..."

Potentially strong, but different from direct observation.

Require:
- reporter identity;
- outlet;
- claim category;
- named/unnamed source status if public;
- corroboration count;
- source-specific historical reliability.

### Tier E — local analysis / film interpretation

Useful for:
- role interpretation;
- scheme changes;
- matchup detail;
- qualitative OL/DB/WR evaluation.

It should generally create a research hypothesis or supporting feature, not directly override structured facts.

### Tier F — aggregation / national repost

Lowest value unless it adds original reporting.

Always resolve to original source where possible.

## Claim taxonomy

Every claim should be one primary type:

- AVAILABILITY
- PRACTICE_PARTICIPATION
- FIRST_TEAM_REPS
- DEPTH_POSITION
- SNAP_LIMIT
- ROLE_INCREASE
- ROLE_DECREASE
- STARTER_CHANGE
- REPLACEMENT
- OL_COMBINATION
- ROUTE_ALIGNMENT
- BACKFIELD_ROLE
- GOAL_LINE_ROLE
- THIRD_DOWN_ROLE
- TWO_MINUTE_ROLE
- RETURN_SPECIAL_TEAMS_ROLE
- SCHEME_CHANGE
- PERSONNEL_PACKAGE
- PLAYCALLING_CHANGE
- COACHING_PHILOSOPHY
- MATCHUP_PLAN
- WEATHER_ROOF
- TRANSACTION
- DISCIPLINE
- REST_MANAGEMENT
- INJURY_RECOVERY
- POSTGAME_ROLE_EXPLANATION
- FUTURE_ROLE_EXPECTATION
- OTHER

## Evidence class

Mandatory:

- OFFICIAL_EVENT
- DIRECT_QUOTE
- DIRECT_PRACTICE_OBSERVATION
- NAMED_SOURCE_REPORT
- UNNAMED_SOURCE_REPORT
- REPORTER_INFERENCE
- FILM_ANALYSIS
- AGGREGATION
- OPINION

Model weighting must not collapse these classes.

## Time model

Preserve:

- event/practice date;
- published_at;
- observed_at;
- corrected_at if applicable;
- effective_from;
- effective_until if stated;
- target game;
- decision clock.

A claim is eligible only if it existed before the relevant FULL COUNT freeze.

Later edits append correction/contradiction events. Never overwrite the historical evidence state.

## Weekly collection clocks

### Monday
- postgame pressers;
- immediate injury explanations;
- coach role explanations;
- transactions;
- snap/usage review.

### Tuesday
- off-day news;
- transactions;
- coach media where available;
- early depth changes.

### Wednesday
- first major practice report;
- direct practice observations;
- first-team reps;
- OL combinations;
- coach/player pressers.

### Thursday
- practice progression;
- coordinator media;
- role refinement;
- market reaction.

### Friday
- final practice status;
- coach final-week media;
- game-status designations;
- workload comments.

### Saturday
- travel;
- elevations/transactions;
- late injury news;
- weather/roof;
- beat-reporter final notes.

### Sunday
- early-morning updates;
- warmup observations where legitimately public;
- official inactives;
- final depth/replacement confirmation;
- final market freeze.

### Postgame
- role explanation;
- injury explanation;
- coach decision explanation;
- player comments;
- immediate future-role clues.

Postgame evidence may influence later games only.

## Reporter registry

Create a durable team-by-team source registry.

Each reporter/source row:

- reporter_id
- name
- outlet
- team(s)
- role/title
- source URLs/accounts
- credential/accreditation evidence if public
- active_from / active_to
- original_reporting capability
- practice_access indicator
- press_conference_access indicator
- claim categories frequently covered
- paywall/licensing notes
- last verified date

Do not hard-code "trusted reporter" as a permanent binary.

## Reliability model

Reliability must be claim-type specific.

A reporter may be excellent at:
- injuries;
- depth-chart changes;
- transactions;

and mediocre at:
- projecting snap shares;
- forecasting game plans.

For scoreable claims, accumulate:

- claim count;
- resolved count;
- correct / partially correct / incorrect;
- timing lead;
- confidence calibration;
- category;
- team;
- source type.

Use hierarchical shrinkage:
league/reporting baseline
-> evidence class
-> outlet
-> reporter
-> reporter × claim type.

Never punish a reporter for a claim that was not actually testable.

## Contradiction graph

Claims can:

- corroborate;
- refine;
- supersede;
- contradict;
- retract.

Example:

Wednesday beat writer:
"Player X took most first-team slot reps."

Thursday coach:
"We're rotating several players."

Friday official depth chart:
Player X still second string.

Sunday:
Player X inactive.

The system should retain all four pieces and their timestamps, not pick one and erase the others.

## Coach/player language ontology

Track recurring language carefully:

Strong explicit:
- "will start"
- "won't play"
- "will be limited"
- "he is our starter"
- "X will call plays"

Moderate:
- "expect him to play"
- "plan is..."
- "we'd like to..."
- "he'll have a role"

Weak/strategic:
- "we need to get him involved"
- "we have confidence in him"
- "we'll see"
- "day to day"
- "everyone has to be ready"

The words themselves should not become arbitrary numeric weights.
Prospectively learn how often each speaker/claim type predicts actual role.

## Press conference extraction

For each official presser:

1. bind team/date/speaker;
2. preserve source;
3. transcribe/capture captions where permitted;
4. segment by question/answer;
5. extract atomic claims;
6. classify claim type/evidence;
7. bind named players/units;
8. preserve exact quoted reference/timecode;
9. infer no hidden facts;
10. score later against outcomes where possible.

Important distinction:
Questioner's premise is not the speaker's claim.

## Practice observation extraction

Examples of useful structured observations:

- first-team QB/RB/WR/TE/OL group;
- slot/outside alignment;
- offensive line combination;
- returner reps;
- goal-line reps;
- red-zone package;
- individual vs team drill participation;
- limited workload;
- position group rotation;
- player leaving/returning;
- new motion/personnel package.

One observation does not equal a locked Sunday role.
The Role Brain aggregates evidence.

## Market-reaction join

Each claim can optionally bind the nearest market snapshots:

- price immediately before;
- price after;
- 15m / 1h / 4h movement;
- cross-book movement if available.

Research question:
Does the market move on this source/claim category?
Does FULL COUNT interpret the claim better/worse than the market?

Do not define source quality solely by market reaction.

## Alerts

Eventually the live system should surface high-value deltas:

- probable starter change;
- new first-team reps;
- OL reshuffle;
- unexpected absence;
- snap-limit statement;
- role expansion;
- goal-line role change;
- playcaller change;
- new scheme/package;
- contradiction in injury status;
- beat-report consensus shift.

The alert should show:
- what changed;
- sources;
- timestamp;
- confidence;
- affected players/markets;
- whether model refresh occurred.

## Anti-noise rules

Do not model:
- generic motivational quotes;
- headlines without source details;
- fan speculation;
- engagement metrics;
- arbitrary sentiment;
- viral rumors without provenance.

They may remain discoverable but should not contaminate predictive state.

## Historical strategy

The News Brain should be prospective-first.

Historical backfill is allowed only when:
- original timestamp is recoverable;
- source identity is clear;
- content is available legitimately;
- the historical information state is not reconstructed from later summaries.

A smaller honest news ledger beats a giant contaminated archive.

## Success criteria

The News Brain succeeds when FULL COUNT can answer:

- What changed since our last model freeze?
- Who reported/said it?
- How trustworthy is that type of claim from that source?
- Is it corroborated?
- Is it contradicted?
- Which players/roles/markets are affected?
- Did the market already react?
- Did the eventual role/outcome validate the claim?

Alligator

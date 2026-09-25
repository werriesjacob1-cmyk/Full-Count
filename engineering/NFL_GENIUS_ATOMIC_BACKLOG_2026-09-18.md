# NFL Genius Atomic Implementation Backlog — 2026-09-18

This backlog operationalizes the NFL Genius doctrine. It is ordered by dependency and expected predictive value, not by novelty. One scientific hypothesis per branch. No promotion/merge implied.

## P0 — preserve the evidence loop

### P0.1 Live game-market bridge
Status: PR #135 prepared by SUPERCHAD.
Acceptance:
- every discovered event represented;
- real FanDuel line/price/selection identity;
- B0 remains control;
- explicit NO_PLAY;
- deterministic seal;
- no rejected challenger promotion.

### P0.2 Postgame game-market report
Status: SUPERCHAD stacked prep branch.
Acceptance:
- source manifest hash verified;
- exact frozen line/price graded;
- explicit ESPN finality;
- W/L/P, units and ROI;
- full-slate denominator;
- no survivor-only reporting.

### P0.3 Angle/source control plane
Status: this branch.
Acceptance:
- registry validates;
- every angle references defined sources;
- multi-year/PIT/prospective rules fail closed;
- no production model behavior changes.

## P1 — clean current scientific queue

### P1.1 C2 totals-only
Owner branch already reserved: `claude/nfl-c2-totals-only-20260918`
Hypothesis:
C2 total features provide repeatable total-error improvement beyond B0.
Restrictions:
- do not add new availability features;
- old 2020-2025 result is exploratory because it influenced this follow-up;
- freeze prospective confirmation gate before new outcome evidence.
Deliver:
- historical robustness table by season;
- exact spec/gate;
- prospective shadow adapter only after spec freeze.

### P1.2 C3 margin availability
Reserved branch: `claude/nfl-c3-margin-availability-20260918`
Hypothesis:
QB continuity/availability explains margin error omitted by C2.
Restrictions:
- targeted features only;
- no broad feature fishing;
- old C2 held years are exploratory.
Deliver:
- PIT-safe join;
- historical characterization;
- frozen prospective gate;
- separate challenger label.

### P1.3 Receptions B0
Reserved branch: `claude/nfl-receptions-b0-20260918`
Hypothesis:
A simple strictly-prior receptions baseline is a valid control for a richer opportunity model.
Deliver:
- historical B0;
- role/identity safety;
- residual distribution;
- prospective shadow scorer;
- tests.

### P1.4 MLB board-freeze grader
Reserved branch: `claude/mlb-board-freeze-grader-20260918`
Keep separate from NFL work.

## P2 — data foundations with highest expected value

### P2.1 Coach/coordinator/playcaller regime registry
Angles:
COACH-REGIME, COACH-PROE, COACH-PACE, COACH-SCORE-RESPONSE.
Deliver:
- dated responsibility intervals;
- HC/OC/DC plus actual playcaller;
- source citation/provenance/confidence;
- no assumption that title=playcaller;
- regime-change test fixtures.
Acceptance:
- target game resolves exactly one applicable regime per role;
- ambiguous intervals fail closed.

### P2.2 Participation / role substrate
Angles:
WR-ROUTE-OPPORTUNITY, RB-ROLE, REPLACEMENT-GRAPH, TEAM-CONTINUITY.
Deliver:
- multi-year player-team-game snap/participation table;
- strictly-prior rolling and season/career summaries;
- stable identity;
- role change flags;
- missingness ledger.
Acceptance:
- no current-game participation used pregame;
- season-to-season coverage report.

### P2.3 Depth/replacement graph
Deliver:
- depth chart and recent usage merged into prospective role graph;
- expected replacement player(s);
- redistribution candidates;
- confidence.
Acceptance:
- removal of starter produces explicit replacement candidates rather than subtract-only adjustment;
- uncertain depth fails to UNKNOWN, not fabricated certainty.

### P2.4 Current OL state
Angles:
OL-CONTINUITY, OL-AVAILABILITY, OL-BLOCKING-MATCHUP.
Deliver:
- expected starting five;
- injury/inactive state;
- recent snaps together;
- replacement identities;
- prior continuity.
Acceptance:
- upcoming starters must not be inferred from current-game snaps.

### P2.5 Weather/stadium
Angles:
WEATHER, SURFACE-ALTITUDE.
Deliver:
- stadium/date binding;
- roof/surface/altitude;
- timestamped forecasts;
- wind/gust/precip/temp/humidity.
Acceptance:
- historical backtests use forecast vintage if available, not postgame observed weather disguised as pregame.

## P3 — true multi-year football brain

### P3.1 Opponent-adjusted team strength
Angles:
DRIVE-EFFICIENCY, EXPLOSIVE-PROCESS, TURNOVER-PROCESS, FIELD-POSITION.
Build:
- offense and defense EPA/play or analogous;
- drive efficiency;
- explosive probability;
- turnover expectation;
- opponent-strength adjustment.
Multi-year:
- rolling-origin seasons;
- era-normalized league baseline;
- current-regime + recent form.

### P3.2 Score-state/play-volume model
Angles:
GAME-STATE, GARBAGE-TIME, COACH-SCORE-RESPONSE, COACH-PACE.
Build:
- neutral pace;
- pass tendency by score state;
- no-huddle;
- expected plays;
- leading/trailing response.
Acceptance:
- garbage time explicitly separated;
- no use of target game's realized score state before prediction.

### P3.3 Opportunity × efficiency prop decomposition
Passing:
dropbacks/attempts × efficiency.
Receiving:
routes × targets/route × catch rate × yards/catch.
Rushing:
carries × yards/carry.
Acceptance:
- component errors reported independently;
- final distribution built from components;
- compare against naive B0 at equal eligible population.

## P4 — tactical scheme detail

### P4.1 Coverage brain
Angles:
DEF-COVERAGE, QB-COVERAGE-RESPONSE, WR-COVERAGE.
Need:
- man/zone;
- shell family;
- press/off;
- bracket/double team;
- coverage-to-route response.
First experiment:
Does opponent coverage mix improve receiving opportunity/efficiency beyond role + market baseline?

### P4.2 Pressure brain
Angles:
DEF-PRESSURE, QB-PRESSURE-RESPONSE, EXPECTED-PRESSURE-SACK.
Need:
- blitz;
- non-blitz pressure;
- simulated pressure if available;
- pressure-to-sack;
- OL matchup.
First experiment:
Does expected pressure improve QB attempts/yards and game total residuals?

### P4.3 Personnel/formation/motion
Angles:
PERSONNEL-GROUP, FORMATION, MOTION, PRE-SNAP-DISGUISE.
First experiments:
- personnel vs play family;
- motion vs target/rush efficiency;
- defensive response to motion.

### P4.4 Run-concept brain
Angles:
RB-RUN-SCHEME, DEF-RUN-FRONT, EXPECTED-RUSHING.
Need:
- zone/gap/duo/power/counter/etc.;
- intended gap;
- box;
- OL/DL matchup.

## P5 — contextual detail

### P5.1 Injury progression
Angle: INJURY-PROGRESSION.
Critical prerequisite:
re-audit historical/current injury source before use.
Deliver:
DNP/LP/FP sequence, injury category, days since injury, prior return workload.

### P5.2 Travel/rest/circadian
Angles:
TRAVEL-CIRCADIAN, SHORT-REST.
Test:
distance, time zones, local kickoff body-clock proxy, rest differential, prior OT, international travel, consecutive road games.

### P5.3 Officials and penalties
Angles:
OFFICIALS, TEAM-PENALTIES.
Skeptical priors.
Require multi-year shrinkage to avoid crew small-sample overfit.

### P5.4 Special teams
Angle: SPECIAL-TEAMS.
Build:
field position, kicker, punter, returns, ST EPA proxies.

## P6 — advanced tracking/charting

Do not start before access/right/source contracts are settled.

### P6.1 Expected target/catch/YAC
Angle: EXPECTED-TARGET-CATCH-YAC.

### P6.2 Expected pressure/sack
Angle: EXPECTED-PRESSURE-SACK.

### P6.3 Expected rushing / blocking geometry
Angle: EXPECTED-RUSHING.

### P6.4 Gravity/open-but-untargeted/release
Angles:
WR-GRAVITY, WR-OPEN-UNTARGETED.

Promotion requirement:
must add residual predictive value beyond role + scheme + market baselines.

## P7 — market intelligence

### P7.1 Market movement
Angle: MARKET-MOVEMENT.
Prospective first.
Capture repeated price/line snapshots without rewriting previous state.

### P7.2 Cross-market consistency
Angle: CROSS-MARKET.
Examples:
game total vs team total vs QB/WR/RB derivative coherence.

### P7.3 Cross-book
Angle: CROSS-BOOK.
Blocked on source/access decision.
No purchase implied.

## P8 — film

### P8.1 Structured charting proxy
Angle: FILM-STRUCTURED.
Use only after source rights/access are known.

### P8.2 Visual film agent
Angle: FILM-VISION.
Do not begin until:
- legal video access solved;
- play identity solved;
- ontology defined;
- structured data residual-value gap established;
- inter-reviewer/vision reliability can be measured.

## P9 — skeptical angles

Angle: NARRATIVE plus SCHEME-FAMILIARITY and similar.
Research, do not dismiss.
But require:
- clear timestamped definition;
- enough multi-year N;
- controls for team/player quality and market expectation;
- out-of-time repeatability.
Expect many RED results.

## Cross-cutting acceptance checklist for every branch

Before PR-ready:
- [ ] named angle_id
- [ ] source_id(s)
- [ ] source coverage documented
- [ ] PIT availability documented
- [ ] multi-year depth documented
- [ ] regime sensitivity documented
- [ ] raw/source digest policy
- [ ] stable identity
- [ ] missingness/quarantine behavior
- [ ] explicit leakage audit
- [ ] baseline frozen
- [ ] challenger frozen before confirmatory evidence
- [ ] season-by-season table
- [ ] equal-volume comparison
- [ ] clustered uncertainty
- [ ] negative result preserved
- [ ] prospective confirmation path
- [ ] no production promotion without Jacob

## “Detail edge” checklist

For every target game/player, mature FULL COUNT should eventually know or explicitly mark UNKNOWN:

- who is expected to play;
- expected role;
- replacement hierarchy;
- coach/playcaller regime;
- offensive/defensive scheme;
- recent and multi-year opportunity;
- recent and multi-year efficiency;
- opponent-adjusted efficiency;
- score-state behavior;
- pace/play volume;
- coverage/pressure/front;
- OL condition;
- weather/roof/surface;
- rest/travel;
- officials/penalties;
- market price/line and timestamp;
- movement/cross-market context;
- uncertainty caused by missing/ambiguous information.

UNKNOWN is a valid state. Fabricated certainty is not.

Alligator

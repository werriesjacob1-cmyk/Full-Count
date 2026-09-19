# NFL Genius Role Intelligence Brain — 2026-09-18

## Mission

Predict who will actually receive offensive opportunity before kickoff.

The system should not merely know:
- who is on the roster;
- who is listed first on a depth chart;
- who played last week.

It should estimate:
- who will be active;
- who will start;
- who will rotate;
- who will run routes;
- who will receive targets;
- who will receive carries;
- who owns third-down / two-minute / goal-line roles;
- how those roles change when a teammate is absent or limited.

## Core decomposition

Team opportunity is a budget.

Passing opportunity:
- team dropbacks
- player routes
- target probability per route

Rushing opportunity:
- team designed rushes
- player rush share
- situational rush share

Scoring opportunity:
- red-zone
- inside-10
- inside-5
- end-zone targets
- goal-line carries

The role model predicts the distribution of those budgets before predicting final player production.

## 1. Baseline role state

For each player before each target game, preserve:

### Long-term prior
- career position/role
- historical snap share
- historical route/carry/target behavior
- historical situational role

### Current regime
- current coach/playcaller
- current team
- current position group
- current QB
- current scheme

### Current season
- snap share
- routes
- carries
- targets
- alignment
- red-zone work
- third-down work
- two-minute work
- goal-line work

### Recent role
- last 1
- last 3
- last 5
- exponentially weighted recent role

Do not assume one horizon is universally best.

## 2. Role-change detection

A role change is a latent structural event, not simply a large box-score number.

Candidate evidence:

- depth chart movement
- starter injury
- teammate injury
- roster transaction
- first-team practice reps
- coach declaration
- beat-writer direct observation
- snap-share jump/drop
- route-share jump/drop
- carry-share jump/drop
- target-share jump/drop
- alignment change
- special package usage
- scheme/playcaller change

### Change categories

- NEW_STARTER
- DEMOTION
- PROMOTION
- INJURY_REPLACEMENT
- COMMITTEE_EXPANSION
- COMMITTEE_CONTRACTION
- POSITION_CHANGE
- ALIGNMENT_CHANGE
- THIRD_DOWN_ROLE_CHANGE
- TWO_MINUTE_ROLE_CHANGE
- GOAL_LINE_ROLE_CHANGE
- RED_ZONE_ROLE_CHANGE
- RETURN_ROLE_CHANGE
- SNAP_LIMIT
- SCHEME_DRIVEN
- UNKNOWN_STRUCTURAL_CHANGE

### Detection logic

Do not trigger from one noisy game automatically.

Use:
- magnitude
- persistence
- corroboration
- explicit coach/news evidence
- depth/availability evidence
- regime context

A two-game 70% snap role is more convincing than one anomalous 70% game, unless a trusted pregame source explicitly documents the role change.

## 3. Replacement hierarchy

When Player A is unavailable, do NOT assign his role wholesale to Player B.

Construct candidate replacement set from:

1. current timestamped depth chart;
2. weekly roster;
3. prior snaps/routes/carries;
4. position flexibility;
5. practice first-team observations;
6. coach comments;
7. beat-writer observations;
8. official inactive list;
9. recent game packages;
10. scheme/personnel usage.

Output:

- primary replacement probability
- secondary replacement probability
- committee probability
- cross-position redistribution probability
- scheme-change probability
- confidence

Example:
WR1 absent could redistribute to:
- WR2 outside routes
- slot WR targets
- TE seam targets
- RB checkdowns
- more 12 personnel
- lower pass rate

The model should allow all of these.

## 4. On/off redistribution

Historical teammate on/off events are one of the highest-value role datasets.

For each meaningful absence/reduction event, measure pregame-predictable changes in:

- team pass rate
- routes
- targets
- carries
- red-zone work
- first-read opportunities where available
- alignment
- personnel
- pace

### Hierarchical structure

Player-specific on/off sample sizes are often tiny.

Use shrinkage:

league position group
-> team
-> playcaller/regime
-> position-room
-> individual player pair

Examples:

- when WR1 is out, WR2 target share does not always increase by the same amount;
- some coaches funnel to TE;
- some increase RB targets;
- some reduce total passing;
- some use deeper WR rotation.

## 5. Mass balance

Redistribution must reconcile.

If 25% of team routes disappear, predicted replacement route shares should approximately allocate those routes.

If six carries disappear, they must be:
- reassigned;
- removed because expected team rush volume changes;
- converted to QB attempts/scrambles;
- or explicitly left as uncertainty.

Do not independently boost three teammates by 15% each without reconciling the team budget.

Required diagnostics:
- removed opportunity
- redistributed opportunity
- change in total team opportunity
- mass-balance residual

## 6. Position-specific role models

### WR

Predict:
- snap share
- route share
- slot/wide alignment
- target share
- first-read share
- red-zone/end-zone share

### TE

Predict:
- routes vs blocking snaps
- inline/slot/wide alignment
- target share
- red-zone role

### RB

Predict:
- early-down share
- carry share
- third-down snaps
- two-minute snaps
- routes
- target share
- inside-10 / inside-5 carries

### QB

Predict:
- starter probability
- dropback share
- designed run rate
- scramble rate
- short-yardage/goal-line role

### OL

Predict:
- expected starting five
- position assignment
- continuity
- replacement probability
- rotation probability

## 7. Role priors for new players

Rookies/new signings may have little NFL data.

Use:
- draft capital
- college usage
- age
- athletic profile
- training camp depth position
- preseason role
- coach comments
- practice reps

But shrink heavily toward league/position priors until NFL role evidence accumulates.

## 8. Role uncertainty

Output distributions, not one deterministic role when uncertainty is high.

Example:
RB workload:
- 20% chance 70% carries
- 50% chance 55%
- 30% chance committee ~40%

Prop pricing can integrate over role uncertainty.

This matters especially:
- injury return
- rookie debut
- new starter
- committee backfield
- snap-limit risk

## 9. Practice/news integration

The News Brain feeds structured claims into role state.

Examples:

Beat observation:
"RB2 took first-team reps."

Effect:
increase primary-replacement probability.

Coach:
"We'll use both backs."

Effect:
increase committee probability, not automatically reduce either player to a fixed share.

Official inactive:
RB1 OUT.

Effect:
availability becomes known; replacement distribution updates sharply.

Warmup:
Player working normally.

Effect:
may reduce snap-limit uncertainty if source is strong, but does not erase prior injury evidence.

## 10. Evaluation

Every predicted role should later be graded.

Role grading:
- starter identity accuracy
- snap-share MAE
- route-share MAE
- target-share MAE
- carry-share MAE
- red-zone share MAE
- hierarchy top-1 accuracy
- hierarchy top-2 coverage
- committee calibration

Then test downstream:
Does better role prediction improve prop projection accuracy and betting results?

## 11. Change-point evaluation

Role-change detector itself must be evaluated.

Precision:
How often a detected structural change persists?

Recall:
How often did a real structural change occur before the model recognized it?

Lead time:
Did practice/news/depth information identify it before box scores did?

This is a key edge metric.

## 12. Multi-year use

Role behavior changes quickly, but history still matters.

Preferred architecture:

career role prior
+ position archetype prior
+ coach/playcaller redistribution prior
+ current team/regime
+ current season
+ recent usage
+ current practice/news/availability

Old data informs how players/coaches redistribute roles.
Recent information determines current state.

## 13. First implementation experiments

### Experiment A — WR absence redistribution
Population:
games where a team's top recent-route WR was inactive.

Predict:
- routes
- targets
- receiving yards for remaining WR/TE/RB.

Compare:
naive proportional redistribution vs hierarchical role model.

### Experiment B — RB absence redistribution
Predict:
- carries
- routes
- targets
- inside-10 work.

### Experiment C — first-team practice reps
Prospective only initially.

Question:
Do beat-reported first-team reps improve starter/snap-share prediction beyond depth chart + prior snaps?

### Experiment D — coach role statements
Prospective claim ledger.

Question:
Which claim classes actually predict workload changes?

### Experiment E — OL replacement
Does correctly identifying replacement OL materially improve expected pressure and QB efficiency?

## 14. Prop integration

Role model should feed opportunity models:

Receiving:
predicted routes × target probability × catch probability × yards/catch.

Rushing:
predicted carries × expected yards/carry.

Passing:
predicted dropbacks × attempts/dropback × yards/attempt.

Do not let final box-score rolling averages bypass the role model once a mature opportunity model exists.

## 15. Acceptance conditions before production influence

- PIT-safe role features;
- exact source provenance;
- multi-year history;
- regime-aware priors;
- explicit uncertainty;
- mass-balanced redistribution;
- role-prediction grading;
- prospective evidence;
- downstream prop improvement at equal operational volume.

A role system that sounds intelligent but does not improve realized future predictions remains research-only.

Alligator

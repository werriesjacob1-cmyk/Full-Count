# NFL COMPETITOR + SOCIAL INTELLIGENCE MATRIX

**Status:** living research artifact.  
**Purpose:** learn what serious NFL prop/projection products do well, where their
publicly visible architecture appears incomplete, and what FULL COUNT should
measure/build to be materially better.

This document records PUBLIC product evidence only. It does not claim access to
competitors' proprietary models, internal data, or private performance.

## Research rule

Competitor features are inspiration and benchmarks, NOT scientific evidence.

A competitor saying “our model has an edge” does not establish that edge.
A social post saying a prop is “90%” does not establish predictive validity.

FULL COUNT should copy neither claims nor proprietary content. It should learn:
- which user problems products solve
- which data surfaces users value
- which workflow steps are fast
- where evidence/provenance is weak
- where football context is missing
- where customer trust can be better

---

## PFF

Public evidence:
- Player Prop Tool displays projection, cover probability, edge, hit rate and
  sportsbook price.
- Supports core NFL player markets including passing/rushing/receiving and TD.
- Public product material emphasizes matchup context, opponent tendencies,
  scheme/usage, similar-defense hit rates and line shopping.
- PFF marketing says its underlying data is used by NFL teams.

Strengths to learn from:
- football-specific matchup context
- projection + probability + price on one surface
- line shopping
- fast filtering
- football data depth

FULL COUNT opportunity:
- preserve exact pregame evidence/provenance
- distinguish fundamental model from market-informed model
- publish immutable realized results by market
- expose information completeness/contradictions
- make coaching/play-caller and live media intelligence explicit rather than
  simply presenting “matchup context”

Sources:
- https://www.pff.com/betting/player-props
- https://www.pff.com/lp/betting
- PFF YouTube: “The Smarter Way to Bet Player Props” (2025-12-10)

---

## SūmerSports / SūmerLive

Public evidence:
- generates pressures, routes, coverages, formations and outcomes from licensed
  player-tracking data using deep-learning models
- validates machine charting against independent football-expert charting
- provides real-time charting during games
- exposes advanced matchup analysis around tendencies, formations, schemes and
  coverages
- publicly describes 20+ charted data points per snap and hundreds of tracking
  observations over time

Strengths to learn from:
- football is modeled at the role/alignment/play level rather than box-score only
- machine charting is validated against expert agreement, not assumed accurate
- route/coverage/pressure context is treated as fundamental data

FULL COUNT opportunity:
- pregame prop-specific application rather than primarily live football analysis
- combine charting/film state with sportsbook price and live injury/coaching state
- test whether each charted concept adds information BEYOND market price
- build week-over-week film/change signals linked to prop distributions

Sources:
- https://sumersports.com/the-zone/how-sumerlive-tracks-the-game/
- https://sumersports.com/features/sumerlive/

---

## Props.Cash

Public evidence from official 2025 NFL app tutorial:
- player pages
- sportsbook + odds selection
- live roster activity
- receiver charts
- offensive distribution charts
- games played
- matchup pages
- pinning
- shareable NFL research

Public/social ecosystem evidence:
- creators routinely use Props.Cash graphics/data to publish betting theses
- public posts emphasize recent hit rates, matchup splits, opportunity metrics,
  scheme/play-type context, injury context and analogous-player results

Strengths to learn from:
- extremely fast visual research workflow
- opportunity information is visible, not buried
- social sharing is native to the product loop
- customer can move from player → usage → matchup → decision quickly

FULL COUNT opportunity:
- avoid over-reliance on retrospective hit-rate filters
- distinguish causal opportunity from cherry-picked historical splits
- probability distributions rather than pattern matching
- immutable/public result verification
- point-in-time evidence and market comparison built into every recommendation

Sources:
- official Props.Cash YouTube tutorial:
  https://www.youtube.com/watch?v=pBWg_mPnCsk
- public indexed X posts using Props.Cash research/graphics

---

## Outlier

Public evidence:
- multi-book prop discovery
- historical hit-rate filters (recent, season, head-to-head)
- line movement
- public bet percentages
- market-detail views
- saved filters / research workflow
- EV-oriented discovery

Strengths to learn from:
- market state is first-class
- historical research is fast and filterable
- line-movement context is accessible at decision time

FULL COUNT opportunity:
- focus on predictive distribution rather than retrospective hit-rate filtering
- source/provenance discipline
- coaching/film/intelligence layers
- distinguish public sentiment from actual predictive value
- verify whether line movement contains incremental information before weighting it

Sources:
- https://help.outlier.bet/en/articles/6712923-tracking-line-movement-and-public-bet-percentages-on-outlier
- https://help.outlier.bet/en/articles/9828814-researching-nfl-player-props-with-high-hit-rates

---

## RotoWire

Public evidence:
- continuously updated projections
- explicitly incorporates injury news, depth-chart changes, real usage and
  matchup information
- weekly projections cite usage trends, specific matchup context, game script,
  venue/weather and real-time practice/depth updates
- depth charts are updated around injuries/inactives and coaching decisions

Strengths to learn from:
- live depth/role maintenance
- injury impact is modeled as opportunity redistribution, not simply player OUT
- human analysts supplement models with game/context knowledge

FULL COUNT opportunity:
- preserve every revision with observed_at instead of only latest projection
- learn staff/coach reliability
- quantify whether human/context adjustments improve OOS performance
- expose model revision provenance

Sources:
- https://www.rotowire.com/football/projections.php
- https://www.rotowire.com/football/projections-weekly.php
- https://www.rotowire.com/football/nfl-depth-charts/

---

## OddsJam / market-tool class

Publicly visible product category:
- broad sportsbook line/price coverage
- alternate props
- line movement
- alerts
- market comparison

Strengths to learn from:
- market coverage and speed
- price-shopping workflow

FULL COUNT opportunity:
- we do not need to become a generic odds terminal
- our differentiator should be better football probability + evidence state,
  then identify the best FanDuel expression
- however, exact market history and alternate ladders are scientifically useful
  even if customer product remains FanDuel-first

---

## Establish The Run / analyst-model hybrid class

Public product philosophy emphasizes:
- projections
- player usage
- coaching tendencies
- matchup knowledge
- rapid response to news
- expert football analysis layered on quantitative models

Strengths to learn from:
- acknowledges information that simple box-score models miss
- analyst judgment focuses on role/context changes

FULL COUNT opportunity:
- turn analyst-style qualitative information into immutable structured evidence
- measure whether each adjustment class improves results
- preserve the original source behind any LLM/human interpretation
- build coach/speaker reliability instead of treating all news equally

---

# SOCIAL DISCOVERY — WHAT WE LEARN FROM PUBLIC CONTENT

Indexed public X/YouTube evidence already shows a common workflow among betting
creators:

1. recent hit rate
2. minutes/snaps/opportunity threshold
3. opponent rank or defense-vs-position
4. analogous-player results against opponent
5. game spread/total
6. injury/role context
7. public graphic
8. direct “tail” CTA

This workflow is commercially effective because it is understandable and
shareable.

Scientifically it has weaknesses:
- selection/cherry-picking risk
- arbitrary recent windows
- defense-vs-position confounding
- no price-aware break-even comparison
- no proof the selected analogous players were pre-specified
- no immutable record of every recommendation
- survivorship bias in social posting

FULL COUNT should keep the clarity while removing those weaknesses.

---

# FULL COUNT TARGET EXPERIENCE

A customer should eventually be able to open an NFL Top Pick and understand:

### MARKET
- FanDuel line
- price
- implied break-even probability
- movement since first capture
- available alternate expressions

### FULL COUNT DISTRIBUTION
- median / mean projection
- cover probability
- fair price
- uncertainty interval
- difference from market

### OPPORTUNITY
- projected team plays
- projected dropbacks/rush volume
- player routes/carries/targets
- role trend/change
- red-zone / goal-line opportunity

### COACHING
- HC / OC / DC
- actual play-caller if established
- relevant situational tendencies
- regime change
- relevant coach statement, source and reliability

### MATCHUP / FILM
- relevant alignment
- route/run concept
- coverage/front/pressure interaction
- meaningful week-over-week film/charting change
- evidence source and recency

### LIVE INTELLIGENCE
- practice trajectory
- injury status
- inactive effect
- OL/personnel changes
- credible local/beat intelligence
- contradictions
- completeness state

### PROOF
- current model/version
- prediction timestamp
- public exposure timestamp
- historical record for this market
- sample-size caveat
- eventually: similar prior model situations selected WITHOUT hindsight

The customer-facing view should remain simple. The complexity belongs behind an
expandable “Why / Evidence” layer, not on the first screen.

---

# COMPETITIVE MOAT HYPOTHESIS

FULL COUNT should NOT attempt to win by having the largest generic stat table.

Potential moat:

**Point-in-time football world state
+ causal opportunity distributions
+ coaching/play-caller intelligence
+ film/charting change detection
+ market state
+ evidence-completeness risk
+ immutable prospective/public proof.**

Each component still has to earn predictive value. The moat is the architecture
that lets us measure the combination honestly.

---

# SOCIAL ACQUISITION POLICY

Use, in priority order:
1. official/public APIs
2. public/indexed pages permitted for automated access
3. web/search discovery
4. approved social-listening/analytics integrations
5. manual review when automation is not permitted

Never:
- bypass login walls
- defeat anti-bot systems
- scrape a source whose terms prohibit it
- treat a social claim as football ground truth
- ingest private content
- fabricate unavailable post text/video/transcript

Store social intelligence separately from football evidence with:
- platform
- account
- URL
- posted_at
- observed_at
- product/topic
- exact public claim or feature observed
- evidence type
- reliability
- whether independently verified

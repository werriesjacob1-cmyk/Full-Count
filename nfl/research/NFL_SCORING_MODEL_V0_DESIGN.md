# NFL SCORING MODEL V0 — RESEARCH ARCHITECTURE

**Status:** design contract for research implementation.  
**No production scorer. No public picks. No promotion authority.**

## 1. Objective

FULL COUNT NFL should estimate the underlying distribution of football outcomes
first, then price sportsbook expressions from that distribution.

The model is NOT:

`signal_A * weight_A + signal_B * weight_B + ...`

and it is NOT one independent binary classifier per prop line.

The central research object is:

`P(player_game_outcome | information available at cutoff T)`

A single outcome distribution should support multiple lines and alternate
expressions when mathematically legitimate.

---

## 2. Separate four questions

### A. Football world model

What happens in the game, independent of the sportsbook price?

Examples:
- team offensive plays
- dropbacks
- rush attempts
- player routes
- targets
- carries
- receptions
- yards
- touchdowns

### B. Market model

What probability does the sportsbook price imply?

This is separate from the football model. Market information must not leak into
the “fundamental” model and then later be presented as independent evidence.

### C. Residual-information model

Given the market price, does FULL COUNT possess incremental information that
predicts market errors?

This is where live intelligence, role changes, coaching information, film-derived
change signals and evidence completeness may eventually matter most.

### D. Selector / portfolio

Which wager expression should be exposed?

This is NOT the same problem as predicting the outcome. It is downstream and
must remain separately measurable.

---

## 3. Generative causal families

### QB passing

`team plays`
→ `dropbacks`
→ `pass attempts`
→ `completions`
→ `passing yards`

Parallel branch:

`team scoring opportunities`
→ `passing-TD opportunities`
→ `QB passing TD count`

Research outputs:
- pass attempts
- completions
- passing yards
- passing TDs
- interceptions later as a separate turnover process

### RB rushing

`team offensive plays`
→ `team rush opportunities`
→ `player carry share`
→ `player rush attempts`
→ `yards per opportunity / rush-yards distribution`

Research outputs:
- rush attempts
- rushing yards

### WR / TE / RB receiving

`team dropbacks`
→ `route participation`
→ `routes`
→ `target probability`
→ `targets`
→ `catch probability`
→ `receptions`
→ `receiving yards`

When current-season route participation is unavailable, V0 MUST NOT manufacture
routes from postseason-only participation data. A lower-information model can
use target/opportunity history and snap proxies, explicitly labeled as such.

Research outputs:
- receptions
- receiving yards
- longest reception only after a tail model is separately validated

### Touchdowns

`team scoring environment`
→ `red-zone / goal-to-go opportunities`
→ `play-type scoring opportunity`
→ `player share of TD opportunity`
→ `conversion process`
→ `player TD count`

Research outputs:
- passing TDs
- rushing TDs
- receiving TDs
- anytime TD
- 2+ TD

The model must never reduce TD prediction to:

`season TD rate × generic projection`

---

## 4. Coaching is causal state, not a score weight

Future coaching features should modify opportunity-process parameters, not be
added as an arbitrary “coaching score.”

Examples:

- neutral pass tendency → expected dropback share
- pace → team play distribution
- RB committee philosophy → carry-share concentration
- two-minute back choice → receiving opportunity
- goal-line package → inside-5 carry share
- target-manufacturing tendency → target-share prior
- new play caller → regime/change state
- defensive man/zone/blitz/front tendency → matchup interaction

A coaching variable earns production use only if its historical/PIT version
improves held-out distribution or selection performance.

---

## 5. Benchmark ladder — every layer must earn its place

For each market family, build models in this order and retain all results.

### B0 — naive football baseline

Examples:
- rolling player/team opportunity averages
- opponent-neutral
- no market input
- no news/intelligence

Purpose: prove the research harness can beat trivial persistence.

### B1 — game environment

Add:
- home/away
- expected game pace
- rest/short week
- opponent
- basic spread/total ONLY in a separately labeled market-informed variant

Football-only B1 must remain price-free.

### B2 — team opportunity

Add measurable team-level processes:
- plays
- dropback tendency
- rush tendency
- score-dependent tendencies using only prior games
- red-zone / goal-to-go rates

### B3 — player role / opportunity

Add:
- snap share
- carry share
- target share
- air-yard share
- third-down/two-minute role where reconstructable
- recent role-change features

### B4 — coaching regime

Add only reconstructable:
- HC / OC / DC
- play-caller evidence where known
- regime tenure/change
- historical situational tendency estimates

Never treat unknown play caller as the coordinator by default.

### B5 — matchup / charting / film

Add only data available by that historical cutoff:
- pressure/blitz
- motion
- play action/RPO/screen
- aDOT/depth profile
- alignment/personnel/coverage only where legitimately available

Same-season postseason-only participation is forbidden.

### B6 — live intelligence

Prospective-first:
- practice trajectory
- inactive role redistribution
- coach/player role statements
- OL configuration changes
- credible practice observation
- contradiction state
- evidence completeness

This layer may initially be measurable only in prospective shadow.

### B7 — scoring/TD opportunity

Explicit red-zone, goal-line and player-share process.

### M0 — sportsbook-only baseline

Using captured market state:
- line
- price
- de-vigged implied probability where both sides exist
- alternate-ladder shape where available

This is the “what the market knows” baseline.

### MR1 — market-informed residual challenger

Inputs:
- fundamental football distribution
- sportsbook baseline
- FULL COUNT information-state features

Question:

**Does FULL COUNT predict residual sportsbook error?**

It is not a win if MR1 merely copies M0.

---

## 6. Distribution families are candidates, not doctrine

Do not predeclare one distribution correct.

Research candidates per process:

### Counts / opportunity
- Poisson baseline
- Negative Binomial when overdispersion is real
- Binomial / Beta-Binomial for player share conditional on team opportunities
- empirical conditional distributions where parametric assumptions fail

### Yards
Prefer compound models:
- opportunity-count distribution
×
- per-opportunity efficiency distribution

Candidate efficiency models may include:
- empirical/bootstrap residuals
- robust Gaussian / Student-like residual approximations where supported
- positive-tail families only if negative outcomes are handled honestly

Do not force rushing/receiving/passing yards into the same family because all
are measured in yards.

### Touchdowns
Candidates:
- team TD opportunity process
- player share conditional on scoring opportunity
- count distribution for player TDs
- calibrated Bernoulli `P(TD >= 1)` derived from the count model

Zero inflation / overdispersion must be tested, not assumed.

---

## 7. Point-in-time feature contract

Every training row must identify:

- season
- week
- game_id
- player_id
- team
- opponent
- kickoff timestamp
- prediction cutoff timestamp
- feature-observed-through timestamp
- source vintages used
- feature-set version
- model version

A feature builder must refuse a source observation with:

`observed_at > cutoff`

No silent fallback to final/current data.

If a feature is unavailable:
- it is missing
- it may have an explicit availability/fired indicator
- it is NEVER silently imputed to a favorable football state

---

## 8. Evaluation hierarchy

### Distribution quality

For the full outcome distribution:
- proper scoring rules where implementable
- calibration of derived thresholds
- MAE/RMSE only as secondary point-estimate diagnostics
- tail calibration for touchdown and alternate-line use

### Prop probability quality

At real lines:
- Brier score
- log loss
- calibration bins
- calibration slope/intercept
- coverage by market and price band

### Market-relative skill

- probability residual vs de-vigged market probability
- does disagreement predict actual market error?
- closing-line movement relationship where captured
- ROI at actual usable captured price

### Selector quality — North Star

At equal legitimate operational volume:
- realized hit rate
- ROI
- overlap with champion
- added candidates
- removed candidates
- performance of added/removed populations
- week/season stability
- market mix
- confidence intervals / clustered uncertainty

Do not permit a challenger to “win” by selecting only one easy prop every three
weeks when the champion produced a usable board.

---

## 9. Dependence / uncertainty

NFL rows are NOT independent.

Evaluation must account for clustering/dependence at minimum by:
- week/slate
- game
- team
- repeated player process

Research:
- block bootstrap by week
- game-level resampling
- repeated-player/team effects where model class supports them

Correlated prop expressions from one football thesis must not be counted as
independent discoveries.

---

## 10. Historical split design

Use strictly chronological evaluation.

The exact years should be selected after availability audit, but acceptable
patterns include:

- expanding-window walk-forward by week/season
- train on earlier seasons, validate on later season(s), final untouched
  season as test
- 2026 retained for prospective shadow where live-intelligence features cannot
  be reconstructed honestly

No random train/test split across games from the same time period.

Any tuning decision made after seeing a “test” season converts that season into
validation and requires a newer untouched test.

---

## 11. Required baselines

Every model family must compare against:

1. player rolling mean / opportunity persistence
2. team/opponent-neutral baseline
3. market-implied probability where real historical market evidence exists
4. consensus/simple public-style baseline when legally/reproducibly obtainable

A sophisticated model that cannot beat a rolling average or sportsbook baseline
does not earn complexity.

---

## 12. Model promotion gates

No layer is promoted merely because:
- coefficient sign “makes football sense”
- feature importance is high
- in-sample likelihood improves
- pooled AUC improves
- one week looks good

Minimum evidence:
- held-out improvement
- within-market improvement
- stable direction across multiple folds/seasons where sample allows
- no point-in-time leakage
- exact dataset identity
- feature/source provenance
- same-volume selector test before any pick-policy promotion

Live-intelligence layers may require prospective shadow before a production
claim is even possible.

---

## 13. First implementation target

Build a RESEARCH-ONLY historical dataset and benchmark harness for:

### First wave
1. pass attempts
2. rush attempts
3. receptions
4. passing yards
5. rushing yards
6. receiving yards

These establish the opportunity architecture.

### Second wave
7. passing TDs
8. anytime TD
9. 2+ TD

TD architecture begins from day one in data collection, but the first scoring
prototype should prove the opportunity chain before layering the rarer scoring
event.

This sequencing is a research-order decision, NOT a statement that touchdowns
matter less.

---

## 14. V0 success condition

V0 succeeds when we can answer, reproducibly:

- Which opportunity process is most predictable?
- Which football-only model is best out of sample by market?
- How much does coaching-regime information add?
- How much does matchup/charting add?
- Which missing live data families are worth paying for?
- How much information does the sportsbook add?
- After conditioning on the sportsbook, does FULL COUNT retain residual skill?
- At equal usable volume, does the selector convert that skill into more
  realized winners?

Until those answers exist, there is no scientifically defensible NFL “score.”

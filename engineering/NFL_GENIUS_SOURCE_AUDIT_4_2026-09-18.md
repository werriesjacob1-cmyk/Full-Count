# NFL Genius Source Audit Packet #4 — Coach / Coordinator / Playcaller Regimes

Date: 2026-09-18

Purpose: define a defensible multi-year coaching-regime reconstruction strategy that distinguishes formal titles from actual playcalling responsibility.

## 1. Broad historical scaffold

Primary public source candidate:
- Pro-Football-Reference team-season pages.

Verified examples show team-season pages commonly expose:
- head coach;
- offensive coordinator;
- defensive coordinator;
- notable assistants;
- offensive scheme label;
- defensive alignment;
- stadium and team-season context.

This is enough to construct a broad year-by-year HC/OC/DC/scheme scaffold across teams.

### Important limitation

A team-season page is a coarse season-level record. It does not, by itself, prove:
- who actually called offensive plays;
- who actually called defensive plays;
- whether responsibility changed midseason;
- whether playcalling was shared;
- the exact effective date of a change.

Therefore title history and playcaller history must be stored separately.

## 2. Dated playcaller evidence

Contemporary official/NFL reporting can explicitly identify playcalling responsibility.

Verified example:
NFL.com reported Sean Payton handing primary offensive playcalling to Davis Webb for 2026, while noting Payton would remain involved.

This demonstrates why the schema must support:
- primary playcaller;
- supporting/shared responsibility;
- effective date;
- source publication time;
- confidence;
- source reference.

Do not reduce shared/partial structures to a false single-person certainty.

## 3. Canonical regime model

For every team and target game, FULL COUNT should resolve separate intervals for:

- HC
- OC
- DC
- OFFENSIVE_PLAYCALLER
- DEFENSIVE_PLAYCALLER

Each interval:
- team
- role
- person
- effective_start
- effective_end
- source_id
- source_reference
- source_observed_at
- confidence
- responsibility_note

If intervals overlap ambiguously for the same role and target timestamp, fail closed.

## 4. Multi-year feature architecture

Coaching features should not be just "coach name."

For each target game, build distinct priors:

### Career prior
What has this playcaller/coach historically done across teams?

### Current team/regime prior
What has this specific coach + coordinator + roster context done since the regime began?

### Current-season prior
What has this offense/defense done this year?

### Recent deviation
How has the last 3/5/8 games changed relative to the regime baseline?

These are separate signals.

## 5. Candidate coaching features

Offense:
- neutral pass rate
- pass rate over expectation
- early-down pass rate
- pace
- no-huddle
- shotgun/pistol/under-center
- play action
- RPO
- motion
- screen rate
- first-read concentration
- RB committee concentration
- target concentration
- TE usage
- red-zone pass/run
- goal-line tendencies
- fourth-down aggression
- trailing response
- leading response
- two-minute behavior
- opening-script behavior
- post-bye tendency shift

Defense:
- blitz rate
- rush-count distribution
- pressure style
- man/zone mix where historical charting allows
- coverage family where historical participation allows
- box-count response
- personnel
- rotation
- red-zone pressure/coverage
- leading/trailing defensive behavior

## 6. Regime-change rules

A coaching change is not the only regime break.

Potential breakpoints:
- HC change
- OC/DC change
- playcaller handoff
- starting QB change
- major OL turnover
- scheme change
- midseason philosophical shift

The regime layer should record the administrative coaching structure, while model features separately detect whether statistical tendencies actually changed.

Do not assume a title change automatically produces a football change.
Do not assume no title change means no football change.

## 7. Portability research

One of the most valuable multi-year questions:

"Which coach/playcaller tendencies travel with the coach, and which are roster/QB/team-specific?"

Required held evaluations:
- same coach, different team
- same coach, different QB
- same team, different coordinator/playcaller
- same coordinator, promoted to playcaller
- HC retaining vs delegating playcalling

Candidate hierarchical model:
league prior
-> coaching-tree / role prior
-> coach career prior
-> current team/regime
-> current season
-> recent deviation.

## 8. Source priority

Phase 1:
Use PFR team-season pages to reconstruct HC/OC/DC/scheme scaffold.

Phase 2:
Overlay dated playcaller changes from official team/NFL reporting.

Phase 3:
Use play-by-play + FTN charting to infer and test whether the observed behavior actually changes at the documented breakpoints.

Phase 4:
Only then consider more labor-intensive manual historical reporting reconstruction if it adds material value.

## 9. Acceptance conditions for Claude implementation

Before `COACH_REGIME_V1` is PR-ready:
- every current team resolves HC/OC/DC;
- actual playcaller is independent from title;
- midseason intervals supported;
- ambiguity is explicit;
- source citations/provenance retained;
- interval lookup is deterministic;
- historical source coverage report produced;
- no model wiring yet;
- tests include a known HC-to-OC playcaller handoff case.

Alligator

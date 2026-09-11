# FULL COUNT — SUPERCHAD MASTER CONTROL PLAN

**Owner/final authority:** Jacob Werries  
**Control plane:** SUPERCHAD  
**Repository:** `werriesjacob1-cmyk/Full-Count`  
**Planning snapshot:** 2026-09-11  
**Planning base:** `eea386c4dd4085266146d395b0eda5b6bb8cda91`

This document is the durable cross-session operating plan for the MLB + NFL system.
It does **not** authorize merge, deploy, publication, model promotion, or production
activation. Jacob remains final authority for those actions.

## North Star

1. Realized prop hit rate / predictive accuracy.
2. At the same legitimate usable operational pick volume.
3. Fresh/live intelligence.
4. Customer product quality.
5. UX.

The system must not trade away usable volume merely to manufacture a prettier hit
rate, and it must not increase volume by lowering evidence standards.

## Operating model

FULL COUNT is now managed as three parallel lanes:

### Lane A — NFL evidence foundation
Preserve the 2026 season correctly before building a prediction system.

### Lane B — MLB predictive improvement
Keep improving the existing live product, especially selector skill, market-relative
accuracy, and plus-money performance.

### Lane C — Shared integrity / product truth
Protect immutable evidence, CI, point-in-time semantics, and customer-facing truth.
Extract shared infrastructure only when both sports prove it is sport-neutral.

# NOW — P0 / CURRENT EXECUTION

## NFL-01 foundation completion

Working branch:
`superchad/nfl-foundation-continuation-20260911`

Current takeover base:
Claude head `7f21e77ea1376f2ab321178c880aafd977dd0542`

Current SUPERCHAD head at planning time:
`2448b47941b2e722d9650d56cf82e245f1f7a958`

Completed during takeover:
- Added failing regression tests for FanDuel ambiguous-success state.
- Observed those tests fail before implementation.
- Added `PARTIAL` as a non-conclusive capture outcome.
- FanDuel silent default payloads no longer masquerade as successful tab coverage.
- Added live layout-title tab discovery unioned with empirically verified fallback slugs.
- NFL test suite passed after the fix.
- Reconciled coaching documentation with the 32-team batched acquisition architecture.

Remaining before NFL foundation is merge-ready:
- Re-run final NFL CI after documentation/capture warning changes.
- Confirm only pre-existing MLB/root-suite failures remain outside NFL scope.
- Add/verify a real post-fix capture proving PARTIAL semantics against live FanDuel.
- Verify discovered tabs do not create duplicate/redundant archives or miss player markets.
- Resolve/record the `nfl-raw-archive` branch/ruleset protection requirement.
- Close the official inactives acquisition gap or document a fail-honest interim mechanism.
- Finish weather venue-coordinate source design without inventing coordinates.
- Re-audit press-conference/media acquisition boundaries and permitted transcription paths.
- Preserve the play-caller gap honestly: coordinator title is solved; actual play-caller duty is not.
- Produce final NFL-01 acceptance matrix and cold-session handoff.

**Critical activation fact:** scheduled NFL capture cannot preserve future slates from the default
branch until Jacob explicitly authorizes the required merge/activation path.

## MLB customer truth — Top Pick record by prop

Working branch:
`superchad/mlb-top-pick-market-hit-rate-20260911`

Current head:
`aaaed8f04e19aa1e8b93f1abd9e0a858ca0be39d`

Implemented:
- Per-prop public Top Pick hit rate from immutable `public_top_picks` only.
- Top Pick card history line.
- Performance-page breakdown.
- Tests exclude Leans/Value/Neutral and ungraded/void rows.

Current branch-caused test failure: none known after fixing miss counting.
Root suite still has pre-existing first-paint/live-overlay failures.

Before merge-ready:
- Rebase/replay onto current moving main if needed.
- Re-run branch-specific tests against newest main.
- Verify UI copy/labels and mobile rendering.
- Verify numbers reconcile exactly with immutable ledger.
- Do not merge without Jacob.

## MLB plus-money evidence

Working branch:
`superchad/mlb-plus-money-evidence-20260911`

Current head:
`a82fead58bde8cac05bc0d8bb768780b96aa2f64`

Test written first and observed failing because `plus_money_breakdown` does not yet exist.

Implement next:
- +100–149
- +150–199
- +200–299
- +300+
- by prop market
- actual hit rate
- actual flat-stake ROI at captured prices
- average implied break-even probability
- observed hit rate minus price-implied break-even rate
- sample size
- no pooling across incompatible markets when drawing skill conclusions

Do **not** create a longshot heuristic or promote plus-money picks from small samples.

# NEXT — P1

## NFL source completeness before modeling

Build an auditable coverage matrix for every game/team around:
- sportsbook/player props
- official injuries/practice progression
- official inactives
- transactions
- roster/depth changes
- HC/OC/DC identity
- actual play-caller evidence
- coach/coordinator press conferences
- relevant player media
- contract/holdout/signing/trade information
- credible beat/local reporting
- weather forecast vintages
- film/charting availability
- market movement

Every expected source must distinguish:
- CHECKED_AND_FOUND
- CHECKED_AND_NONE_FOUND
- PARTIAL
- NOT_CHECKED
- SOURCE_FAILED
- STALE
- UNRESOLVED_CONTRADICTION
- UNAVAILABLE_BY_POLICY

## NFL play-caller registry

Coordinator title != play caller.

Design an append-only evidence registry:
- team
- offense/defense
- claimed play caller
- effective date
- observed date
- authoritative source
- quote/span
- confidence
- supersedes
- contradicted_by

Populate historical known changes only from auditable evidence.
Do not infer unknown play callers from title alone.

## NFL Film Intelligence research

Before modeling:
- identify legally automatable film/charting/tracking sources
- measure FTN/nflverse/NGS-like structured coverage
- map what is only available post-season
- map what is available in-season
- define structured film observation contract
- preserve exact play provenance
- investigate week-over-week change detection
- never let “AI liked the tape” become a signal without measurement

## NFL canonical world-state design

No scorer yet.

Define:
Raw Observation
→ Normalized Fact
→ Point-in-Time World State
→ Derived Feature

Use bitemporal semantics:
- `effective_at`
- `observed_at`

Do not overwrite Wednesday/Thursday/Friday/Sunday state into one latest value.

## MLB first-paint / live-overlay baseline defect

Separately investigate the root-suite failure:
- overlay price not visible on first paint
- board-age/staleness behavior where currently failing

Do not bundle this into unrelated MLB/NFL feature branches.
Create a regression branch only after reproducing against current main.

# P2 — MEASUREMENT BEFORE NFL MODELING

NFL does not get a scorer until the evidence substrate is sufficient.

Build historical research datasets and measure signal families:

1. Coaching / scheme / play-calling
2. Team opportunity
3. Player role/opportunity
4. Defensive matchup
5. Film/charting
6. Injury/personnel redistribution
7. Game environment / game script
8. TD/scoring opportunity
9. Market state

Required scientific discipline:
- time-based OOS
- point-in-time reconstruction
- game/week/team/player dependence
- market-relative evaluation
- multiple seasons where required
- no row-independence fantasy
- no arbitrary signal weights
- no pooled-sport metrics

The question is not “does it predict?”
The question is “does it add information beyond the market at usable volume?”

# P3 — NFL WORLD MODELS

Only after P2.

Conceptual causal stack:

Sources
→ Immutable Observations
→ Point-in-Time World State
→ Game Environment
→ Coaching / Scheme / Play Calling
→ Team Opportunity
→ Player Role / Opportunity
→ Film / Matchup State
→ Efficiency / Execution
→ Scoring / TD Opportunity
→ Outcome Distributions
→ Wager Expressions
→ Market Comparison
→ Information Risk
→ Selector
→ Portfolio

Initial model families should estimate underlying distributions, not isolated over/under labels.

Examples:
- pass attempts/completions/yards/TDs
- rush attempts/yards
- receptions/receiving yards
- anytime/2+ TD opportunity

One underlying distribution should price multiple lines/alternates where mathematically valid.

# P4 — NFL SELECTOR / PROSPECTIVE SHADOW

Only after world-model historical OOS evidence exists.

Preserve:
- every eligible candidate
- every rejected candidate
- exact rejection reason
- rank
- probability
- market price
- information-risk state
- model/code identity
- thesis/correlation identity

Shadow before public exposure.

Promotion standard:
more realized winners at the same legitimate operational opportunity/volume,
with no evidence leakage and no dependence on impossible historical information.

# P5 — NFL PUBLIC PRODUCT

Only after human promotion authorization.

Need:
- sport-scoped UX
- NFL board
- performance page
- immutable NFL publication ledger
- settlement
- per-market performance
- transparent sample-size warnings
- no pooled MLB/NFL customer performance number

# MLB CONTINUING ACCURACY PROGRAM

MLB does NOT freeze while NFL is built.

Priority research:
1. Plus-money skill by price band × market.
2. Per-market selector skill at equal usable volume.
3. Why Hits outperform current Ks/Pitcher Outs in public Top Picks.
4. Market-relative calibration/edge rather than raw confidence alone.
5. Added/removed candidate analysis for any selector challenger.
6. Preserve prospective rejected populations.
7. Fix live-data integrity bugs separately from scientific promotion.
8. Reconcile current production weighting documentation with what actually ships.
9. Never promote on pooled AUC/base-rate separation.
10. Continue public-ledger truth and immutable exposure discipline.

# DO NOT BUILD YET

- NFL production scorer.
- NFL public picks.
- Arbitrary coaching weight.
- Arbitrary film weight.
- Generic “longshot score.”
- Multi-sport mega-framework.
- Cross-sport model.
- NFL frontend polish before scientific foundation.
- Any model trained on final information unavailable at prediction cutoff.
- Any source pipeline that silently converts missing coverage into “no news.”

# DAILY CONTROL LOOP

For each active lane:

1. Re-resolve current main.
2. Verify branch head.
3. Inspect diff from branch base.
4. Run/inspect authoritative CI.
5. Separate branch-caused failures from baseline failures.
6. Record real bug with failing test first.
7. Implement.
8. Observe pass.
9. Update this control plan/handoff when state materially changes.
10. Never infer merge/deploy/promotion permission.

# JACOB DECISION GATES

Only Jacob authorizes:
- merge to main
- production activation
- publication
- model/scientific promotion
- irreversible identity/evidence-estate choice when multiple sound options remain

SUPERCHAD may independently code, research, test, push branches, and prepare merge-ready work,
but should stop at those authority gates.

# CURRENT ORDER OF EXECUTION

1. Finish MLB plus-money evaluator red→green.
2. Complete NFL FanDuel/capture post-fix live verification.
3. Make NFL-01 foundation merge-ready.
4. Prepare NFL activation/ruleset decision package for Jacob.
5. Make MLB per-prop Top Pick UI merge-ready.
6. Isolate/fix root first-paint/live-overlay baseline defect.
7. Build NFL source-completeness/play-caller plan.
8. Deep competitor/source research and Film Intelligence feasibility.
9. Design NFL canonical bitemporal world-state contract.
10. Begin historical measurement only after the foundation above is stable.

This order may change only when new evidence shows a higher-value or time-critical dependency.

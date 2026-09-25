# FULL COUNT — NFL Intelligence: Permanent Requirements and Completeness Register (v1)

**Owner:** Jacob (final authority); SUPERCHAD maintains strategic audit; Claude Code and Codex implement in separately claimed, non-overlapping workstreams.  
**Captured:** 2026-09-23. **Status:** Permanent REQUIREMENTS BASELINE, **not** evidence that every capability is implemented or scientifically useful.  
**Applies to:** NFL prediction, research, source acquisition, actual film, market selection, operations, prospective learning, customer product.  
**Bridge:** https://github.com/werriesjacob1-cmyk/Full-Count/issues/91  
**Authoritative rule:** Do not remove or silently collapse a requirement when writing a shorter prompt or delivering an incremental PR. Carry it forward until explicit validated implementation, evidence-based rejection, or a documented blocker. Amend versioned history; preserve negative experiments.

## 0. The purpose and non-negotiable epistemic rules

FULL COUNT must explain *why* an NFL event/player may exceed or fall below an offered sportsbook line this week, evaluate the actual line/price, freeze the prediction before the information cutoff, and grade it honestly after the game. Early leans and final eligible picks are distinct. Aim for stronger **realized prediction accuracy at comparable legitimate usable volume** and, separately, **realized return at actual captured prices**. High hit rate alone is not value: plus-money picks can be good with lower hit rates. Do not game apparent accuracy by selecting only expensive favorites.

Every required intelligence family must move through: **legal SOURCE → real CAPTURE → canonical PLAYER/TEAM/GAME/PLAY identity → point-in-time FEATURE → model/opportunity/distribution CONSUMER → genuine observed MARKET/LINED PRICE evaluation → FROZEN prospective B0/challenger evidence → authoritative POSTGAME GRADE → matched incremental-value assessment → human-reviewed promotion**. Explicitly record missing steps. No source registry, design document, synthetic fixture, standalone code module, test passing, historical-only improvement, or code merge equals live predictive usefulness. An absence of evidence is not a zero-valued feature; preserve UNKNOWN and abstain appropriately.

A feature that is built, unit-tested, and even accepted as a parameter by a consumer function is still NOT a live factor unless that function's real output structurally depends on the parameter's value -- a subtler and easier-to-miss failure mode than "no consumer exists at all." This happened for real in this repository (PR #179's coaching-regime filter existed and was tested but the evaluated projection never depended on it, caught by independent review and fixed in PR #181) -- treat "consumed" as "provably changes the real computed output," not "present in a function signature or output dict."

Do not fabricate historical sportsbook odds, source availability, film observations, coach motives, practice repetitions, injury severity, route actions outside camera view, or exact historical production eligibility. Never backdate captures or use information released after the target prediction cutoff. No unauthorized footage acquisition, restricted scraping, paid source purchase, or rights assumption. An observed third-party charting label is NOT film independently watched by us. No production promotion, public-pick policy change, merge or deployment inferred from this requirements document.

NFL must protect MLB's roughly 25% meaningful engineering/research allocation. MLB North Star: more realized winning props at the *same legitimately usable operational pick volume*, separating canonical historical model data, prospective full candidates, and immutable published Top Picks. Keep MLB grading/History and customer reliability sound without mistaking maintenance for accuracy research.

### Status contract — one status per factor/market/season, not one misleading project-wide checkbox

`NOT_STARTED | SOURCE_IDENTIFIED | RIGHTS_VERIFIED | REAL_CAPTURED | IDENTITY_VERIFIED | PIT_FEATURE_BUILT | HISTORICAL_RESEARCH | LIVE_RESEARCH_CONNECTED | PROSPECTIVE_FROZEN | VALUE_EVALUATED | SPECIFIC_MARKET_VALIDATED | PRODUCTION_INTEGRATED | BLOCKED(reason) | REJECTED(evidence)`.

Attach separately: legal source/license URL or entitlement, observation/publication/capture time and available-at vintage, covered seasons/teams/markets, named owner + Issue #91 claim + branch/PR, consumer file/function, feature semantics/units/unknown policy, tests, prospective artifact IDs, grading identity, B0 comparator and experiment, incremental result/uncertainty, next unblocker. Multiple states can be useful as separate *stage fields*, but never promote a factor to a later stage because an earlier stage exists. Every row below is an **accountable candidate requirement**, NOT an instruction to inject every available variable into production. If unusable, explicitly BLOCK or REJECT with evidence, do not delete it.

## 1. Player history, skill and individual outcomes (P)

- P01 multi-year, season, regime, game and recent-form passing attempts/completions/yards/TDs/interceptions; scramble rate, designed QB runs and pressure response.
- P02 rushing attempts/yards/TDs, designed vs scramble volume, explosive runs, short-yardage/goal-line workload and efficiency.
- P03 receiving targets/receptions/yards/TDs, air yards, target depth, YAC, drops, catchability, contested catches, broken tackles and role-specific consistency.
- P04 age/career stage, role changes, rookie/returning-veteran priors, trades/new teams and roster transitions; no ungrounded age penalties.
- P05 home/away, opponent history, demonstrated role changes, multi-year trend, recency weighting, regression to the mean and sample/strength-of-opponent adjustments.
- P06 snap expectation, routes/route share, pass-blocking assignments, carries, targets, QB dropbacks/attempts and substitution probability; distinct snaps/routes/targets denominators.
- P07 route-specific **target probability per route** and **catch probability per target** by route/depth/alignment/coverage; un-targeted routes must be in the denominator.
- P08 receiver separation vs catch-point performance, open-but-not-targeted cases, target quality and actual QB reads/decision time when observable.
- P09 QB progression/read preferences, time to throw, pressure susceptibility, checkdown tendency, hot reads, scramble vs pass decisions.
- P10 QB–receiver chemistry and continuity measured as an interaction against simpler baselines; account for QB change effects on ALL teammates, not storytelling.
- P11 recent player workload, fatigue proxies, recovery/rest/injury restrictions, prior usage sustainability, practice reps where legitimately observed, medical speculation prohibited.
- P12 player-specific outcome distribution with opportunity and efficiency uncertainty, count zeros/tails, over/under/alternate/TD count probabilities.

## 2. Team opportunities, roles and redistribution (R)

- R01 offensive plays/drives/possessions, neutral pace, situation-specific pass/run rate, team attempts/carries/route opportunities.
- R02 expected offensive snap share, route share, target share, carry share, red-zone/goal-line share, short-yardage packages, third-down/two-minute use.
- R03 personnel groupings (11/12/21 etc.), formations, motion, alignments, starters, rotations, substitution patterns and pass-protection vs route assignments.
- R04 teammate OUT/limited/DNP, replacements, depth chart, QB changes and team-switch impact; validate **mass balance** across total team targets/carries/routes/snaps.
- R05 distinguish genuine pregame announced absence, probable game-time limitation, questionable status, in-game injury and hindsight; conditional availability scenarios.
- R06 prior team workload vs projected game plan; usage after a bye/short week/extra prep; source-confirmed elevated/lowered workload.
- R07 RB rush/route/block splits, backfield committee, short-yardage and receiving roles; TE route-block split; skill-player subpackages.
- R08 backup/refill hierarchy by formation, situation and opponent; replacement effectiveness vs vacated opportunity, not mechanically transferring all missed targets.
- R09 pregame role visibility, role uncertainty and probability of unexpected benching/rotation; distinguish deterministic decisions from tentative leans.

## 3. Coaching, scheme and actual game-plan intelligence (C)

- C01 actual HC, OC, DC, offensive/defensive PLAYCALLER identities and dated intervals, tenure/regime transitions and uncertainty; job title is not proof of calling plays.
- C02 neutral-situation pass/run rates, pace, down/distance, play-action, RPO, motion, formation, personnel-package and tempo preferences.
- C03 red-zone/goal-line, third-down/short-yardage, opening-drive/scripted plays, two-minute/late-game and fourth-down tendencies.
- C04 coach response to specific defensive weaknesses, coverage/pressure looks, similar opponents and personnel changes; opponent-specific plans rather than blanket coach bonuses.
- C05 halftime/in-game tactical adjustment tendencies, defensive adjustment, score/time/timeout-dependent decisions; avoid postgame leakage.
- C06 playbook/system continuity after coach/coordinator/QB turnover, new regime sample uncertainty, nonstationarity and carryover from documented earlier roles.
- C07 game-plan uncertainty with multiple plausible, coherent tactical scenarios and estimated scenario probabilities; uncertain quotes are NOT planned play counts.
- C08 documented milestone/contract incentives only when verifiable, point-in-time known and empirically tested; no motive-as-fact.
- C09 coach statements, press conferences, coordinator/player interviews and observed practice cues: corroboration, reliability, corrections and delayed reporting; no automatic fact promotion.

## 4. Defense, fronts and individual matchups (D)

- D01 strictly-prior pass/rush EPA allowed, success, plays/pace allowed, efficiency by opponent strength, personnel and game state.
- D02 man/zone rates, shells, coverages, match/quarters/brackets/doubles, disguised coverages and post-snap rotations; field confidence/UNKNOWN.
- D03 alignment-specific WR/CB, slot/outside, TE/LB/S, RB coverage and backfield coverage assignment tendencies, including personnel-specific switches.
- D04 route/coverage matchup, funnel tendencies, receiver opportunity by alignment, and defensive pressure impact on first/second/late reads; avoid ecological fallacy from team-level stats.
- D05 blitz/pressure/stunt/rush-lane tendencies, individual pass rush, OL/DL matchups, protection and blocking, sacks, QB hit timing; separate pressure caused vs pressure credited.
- D06 run fronts, box count, fit, gap assignments, run concepts, OL continuity, tackle/edge injuries and short-yardage matchup.
- D07 defensive replacement hierarchy, altered coverage assignment/communication/speed, in-game rotation, opponent-specific exploitable weaknesses.
- D08 formation/motion/personnel-specific defensive reaction, disguised alignments, halftime defensive adjustment and uncertainty.

## 5. Actual film and legally accessible tactical charting (F)

- F01 lawful access and **commercial/automated analysis rights** for each footage source; record actual license/terms, duration, cost, retention, redistribution and permitted derived-feature use.
- F02 true clip/game/play/time provenance; canonical player/team IDs; game clock, camera type/angle, timestamps, source URL/digest and observation/capture/availability times.
- F03 observed offensive/defensive personnel, formation, alignment, pre-snap motion, substitutions, route tree/depth/combinations, coverage and post-snap changes.
- F04 observed QB progression if camera permits, pocket/pressure, separation, blocking assignments/execution, individual WR/CB and OL/DL matchups, run concepts and fits.
- F05 distinguish **direct video observation**, **licensed third-party charting**, **play-by-play inference**, **model inference**, **synthetic fixture**, and **not visible**; NEVER conflate them.
- F06 independent real-play annotator agreement, ambiguous-field quarantine, identity/timing confidence, cross-angle limitations, false-positive/false-negative audits.
- F07 film-derived conditional opportunity/efficiency feature with actual model consumer and paired incremental-value test; third-party labels may not support all-route target probability if only targeted passes are charted.
- F08 partial-data pathway when full All-22 unavailable: legitimately usable public footage or licensed structured charting, preserving no-footage/unknown and source rights.
- F09 film-induced opponent-specific game-plan hypotheses must remain uncertain and be tested against pregame-only baselines, not explained retrospectively.

## 6. Injury, practice, roster and human intelligence (N)

- N01 official injury reports, practice participation, specific reps/workload limits where observable, IR/PUP/activation, inactives, transactions and updated depth-chart identity.
- N02 game-day warmups, weather/roof announcements, credible local reporters for all 32 teams, official team reporting, beat writers, team/local radio-TV, player/coach pressers and authenticated public communications.
- N03 news provenance: what was OBSERVED, CLAIMED, PUBLISHED, INGESTED, CORRECTED and AVAILABLE at the freeze cutoff; timezones and corrections separately retained.
- N04 reliability/corroboration, reporter expertise, independent-source overlap, stale rumors, contradictory reports and likelihood of role outcome; no social post automatically becomes truth.
- N05 probable/minutes-limited/questionable player availability mixtures, scratch/DNP/early exit probability and teammate/opponent conditional scenarios.
- N06 positional/injury impact on OL/front/secondary coverage communication and replacement effects; not just star skill-player inactives.
- N07 pregame **early lean** vs final **actionable selection** and candidate update/freeze rules; do not silently rewrite previously exposed picks.
- N08 social-media/competitor observation only through permitted public/authorized routes; commercial terms, copyright, privacy, provenance, adversarial/inaccurate claims and signal-value audit.

## 7. Game, environment and clock/score mechanisms (G)

- G01 offensive/defensive efficiency, neutral-situation pace, projected total drives/possessions, field position, projected points and margin with uncertainty.
- G02 lead/trail/tie and time-remaining conditional playcalling, hurry-up, kneel-down, no-huddle, intentional clock burn, comeback/pass volume and opponent response.
- G03 weather actual forecast vintage/uncertainty, wind/gust direction, temperature, precipitation, roof open/close, stadium altitude, turf/grass and field-condition changes.
- G04 travel, time-zone shifts, international games, rest, Thursday/Monday short weeks, byes, schedule congestion, travel anomalies, home-field and preparation time.
- G05 special teams field position, turnovers, penalties/automatic first downs, two-point attempts, fourth-down aggression and their effect on possessions and scoring.
- G06 officiating crew identification and tendencies only when reliably measured, adequately sampled and not confounded by team/opponent.
- G07 game delays, postponements, weather interruption, overtime/tie rules, season phase (regular/postseason) and changed motivation/rotation only from documented role evidence.

## 8. Markets, prices, decisions, settlement and correlation (M)

- M01 all genuinely offered QB passing/completions/attempts/yards/TDs/INTs; rushing attempts/yards/TDs; receiving targets/receptions/yards/TDs; defensive/kicking/return props and other supported player/team outcomes.
- M02 game spreads/totals/moneylines, team totals, period/quarter/half markets, standard/alternate thresholds, plus-money and other legitimately offered price bands; avoid fake coverage.
- M03 exact book/event/player/team/market/selection IDs, line, side, offer timestamp, quote age, suspended/limited/not-posted/fetch-failed distinctions, usable timing and book/jurisdiction limits.
- M04 observed cross-book differences and best actually accessible offer, implied and two-sided no-vig market probability, break-even price, real odds and price-sensitive expected value.
- M05 line/price movement relative to documented arrival of news, market open/close, stale quotes, delayed sportsbook response, quote availability and price-impact half-life.
- M06 discrete/integer/continuous outcome PMFs and tails, zeros, push/void/DNP/action rules, threshold inclusion, integer-vs-half-integer coherence and distribution support.
- M07 standard and alternate lines derive from one coherent distribution; cross-market consistency (targets ≥ receptions, target/receptions/yardage/TD and game/team/player opportunities), accounting for statistical exceptions and market definitions.
- M08 joint player/game distributions with shared game script, team opportunity budget, weather and injury shocks; correlated bets and same-game portfolio limits; never assume independent SGP legs.
- M09 uncertainty-aware probability/EV and price-specific NO_PLAY; early vs final decisions, public selector gate separate from research board, published snapshot immutable.
- M10 sportsbook-specific settlement rules, game-shortening, stats corrections, partial appearance, pushes, voids, DNP, injuries and official grading vs book contract.
- M11 realized hit rate at matched usable operational volume **separately** from actual-price realized ROI/profit, by market, price band, year, season, kickoff wave and eligible population. CLV is a separate descriptive diagnostic, not realized winnings.
- M12 player/game clustering, multiple-testing/research degrees of freedom, uncertainty, drift by season/coach regime, calibration by predicted confidence and observed reliability.
- M13 full-candidate and rejected-candidate records: why no pick, counterpart market quotes, candidate availability and cutoff, missed prices, excluded markets, so selectors can be fairly replayed.
- M14 coherent joint scoring/margin and touchdown-count distributions with goalline/drive/possession model, rather than independent per-market estimates.

## 9. Evidence, reliability, product and research controls (E)

- E01 champion B0 vs frozen challengers with same actual candidate universe, timing, market, eligible slate/date allocation and legitimate usable count; report overlap/added/removed, not just aggregate-N.
- E02 proper scores/calibration AND realized selected hit rate and price-specific realized return; uncertainty/significance with game/player correlation, coverage, missingness and selection bias.
- E03 source rights, exact raw bytes/digests, reproducible feature/model/experiment identities, fit/held-out separation, hyperparameter preregistration, model freshness and training-serving parity.
- E04 point-in-time source vintages and data revisions, knowledge vs publication vs observation clocks, no closing-line/settlement leak, no retrospective rewrite of prospective evidence.
- E05 market-eligible grade completeness, official final vs provisional states, delayed/cancelled/void outcomes, settlement correction, first public exposure proof and immutable customer History.
- E06 model fallback, stale/missing feed gate, identity conflicts, atomic artifacts, concurrency-safe updates, retries, incident recovery, controlled real end-to-end workflows vs CI-only success.
- E07 source coverage by team/season/game/position/market, MNAR missingness/survivorship, prospective shadow capture completeness and cost/supply feasibility.
- E08 first-class early leans, final eligible picks, customer explanations with explicit source/time/uncertainty, meaningful alerts on changed prices/news, website grading/history reliability.
- E09 cost/rate limits, request load, no unauthorized terms bypass, privacy and source security, legal/consumer-facing compliance by jurisdiction, model change governance and Jacob-specific approvals.
- E10 separate historical model research, prospective full-candidate shadow, and immutable public-pick scoreboard; never blend their results or infer historical publication.

## 10. Further angles to investigate — SUPERCHAD NEW CANDIDATES, NOT ESTABLISHED GAPS OR PROVEN ADVANTAGES (X)

Claude must independently verify which already exist or lack usable sources, and propose additional hypotheses *without* copying this list as its own brainstorming.

- X01 **Model causal chain:** team plays → route/attempt opportunities → target/carry share → efficiency → player outcome; explicit uncertainty propagation and team-level budget reconciliation.
- X02 **Availability-aware predictive mixtures:** probabilities of player active/limited/early exit, alternate depth-chart/player lineup states; separate sportsbook action/void rules.
- X03 **Game-clock/drive simulator:** penalties, sacks, turnovers, possessions, fourth downs, special teams, two-minute and overtime; calibrate against simpler team-volume baselines.
- X04 **Coverage/pressure counterfactuals:** offense adaptation to opponent pressure/coverage conditioned on personnel vs observational confounding; guard against over-interpreting small charting cells.
- X05 **Route tree opportunity:** all eligible routes including no-target routes, blocker/decoy role, motion-created coverage shift, protection-to-route conversion and QB read order as observable.
- X06 **Offensive-line communication and replacement chemistry:** starter combinations, center/QB continuity, pressure from blitz pickup, run-block cohesion; measure prior to kickoff.
- X07 **Defensive matchup reallocation:** shadow corner assignment and double/bracket scenarios conditional on formations, receiver alignment and game score.
- X08 **Data quality as a modeled signal:** observation camera visibility, charting disagreement, source publication lag, news source independence, uncertainty/missingness as an explicit reason to abstain.
- X09 **Market microstructure:** quote delays, alternative book lines, liquidity/limits, stale-but-displayed quotes, suspension, playable window and transaction feasibility; do not imply guaranteed execution.
- X10 **Outlier tail mechanisms:** low-volume players, TD rarity, multi-TD and large-yardage games, overdispersed opportunities, shared/team scoring shock, adverse injury tail.
- X11 **Selection opportunity cost:** one thesis/one best wagerable expression vs duplicate correlated props; exact-N refill, same-day opportunity and legitimate prices.
- X12 **Continuous validation and drift:** new coach/QB and depth-chart turnover, roster vintage changes, data schema changes, model-training vs live-feature mismatch, calendar/season transitions.
- X13 **Market-based blind-spot diagnosis:** analyze when our predictions disagree with market and whether discordance follows actual player news, role or scheme; do not treat movement as automatic truth.
- X14 **Observation-to-decision latency:** news published vs fetched vs scored vs book repriced vs customer surfaced; stale inference/price gate at every step.
- X15 **Negatives and censoring:** DNP/snap-zero, called-back plays, penalties and stat corrections, suspended games, unobservable film fields, missing historical prices, no-offer censoring.
- X16 **Special teams/kicker/defense model families:** field-goal attempt distance, kicking weather and holder/long-snapper changes, punt/return chances, defensive sacks/turnovers/TDs with event-specific market rules.
- X17 **Unpriced risk:** correlated entries, opportunity concentration, book access differences, user eligibility and cumulative exposure; assess as portfolio constraints, not a claim of risk-free EV.
- X18 **Research governance:** feature ablation/negative-control exposures, no double counting across correlated factors, multiple-testing control, experiment stop criteria and team/season holdouts.

## 11. Twenty previously identified extras — explicitly retained (crosswalk)

These must remain individually auditable even where grouped above:

| Exact requirement | Primary requirement ID(s) |
|---|---|
| 1. Route-specific target probability | P07, F07 |
| 2. QB progression/read preferences | P09, F04 |
| 3. Receiver separation vs catch-point | P08, F04 |
| 4. Player substitutions | R03, F03 |
| 5. QB–receiver chemistry | P10 |
| 6. Fatigue/workload sustainability | P11, R06 |
| 7. Defensive replacement effects | D07, N06 |
| 8. Defensive coverage disguises | D02, F03 |
| 9. Defensive funnels | D04 |
| 10. Defensive adjustment tendencies | D08, C05 |
| 11. Opening-drive/scripted plays | C03 |
| 12. Coaching exploitation of defensive weaknesses | C04 |
| 13. Post-bye player usage | R06, C06 |
| 14. Documented milestones/incentives | C08 |
| 15. Market movement vs information arrival | M05, X14 |
| 16. Cross-market probability consistency | M07, M08 |
| 17. Game-script-conditional performance | G02, C07 |
| 18. Game-plan uncertainty | C07, X02 |
| 19. Confidence vs actual historical reliability | M12, E02 |
| 20. Sportsbook-specific differences | M03–M05 |

## 12. Current verified integration caution (a dated checkpoint, not static truth)

- PR #176 merged (2026-09-23) as research-only role-adjusted receptions connector. Documented historical 2022-2025 held-out target-share MAE 0.06196 (n=449) vs no-adjustment MAE 0.06050 (n=441): not a matched-population win.
- PR #178 merged: connects PR #176's role-adjusted challenger to the scheduled research workflow. Research-only, not a public-pick change.
- PR #179 merged: team-opportunity engine (team pass-volume x target share x catch rate). Real matched 2025 evaluation, n=2954: B0 MAE=1.299 vs challenger MAE=1.412 -- a real, disclosed negative finding. Not promoted.
- PR #180 merged: fixes a real operational gap (no way to target a non-Sunday research capture date).
- PR #181 merged: connects the previously-built-but-unconsumed coaching-regime filter to the actual team-opportunity projection, and fixes a real temporal-leakage defect the fix surfaced. Real activation confirmed on three real 2023 in-season HC changes; zero real activations found in the main matched 2025 population (no in-season change fell inside any evaluated player's rolling window there) -- improved accuracy remains unestablished either way.
- PR #182 merged: applies PR #178's already-reviewed living-roster validation pattern to a second workflow that had drifted on an exact byte pin.
- PR #183 merged: current-season snap-share role-change signal, actually consumed by the team-opportunity projection. Real matched 2025 evaluation, n=3059: unadjusted MAE=1.386 vs snap-adjusted MAE=1.478 -- a second real, disclosed negative finding, not retuned against.
- PR #184 (draft, unmerged as of this checkpoint): component-level ablation of the same opportunity/coaching/snap-share adjustments on a fresh 2024-season-weeks-8+ holdout. Real matched population n=2907: B0 MAE=1.3532 vs team-volume-only MAE=1.5044 vs full engine MAE=1.4555 -- both real challengers worse than B0, an independent confirmation of the PR #179 finding on a different cohort. Coaching: 0/3175 activated, same honest null as 2025. Snap-share: every tested gate variant remained worse than unadjusted, though tighter gating reduced the damage relative to universal application.
- PR #185 (draft, unmerged as of this checkpoint): QB-change-aware team-dropback consumer, connecting the previously-unconsumed `qb_continuity_features.py` substrate to the same opportunity chain. Real matched 2025 evaluation, n=3059: baseline (coaching-aware) MAE=1.386444868319182 vs QB-aware MAE=1.38511789320672 -- a negligible, inconclusive difference, not a demonstrated win, though the feature is far more active (1027/3059 real projections changed) than the coaching feature.
- PR #172 merged frozen receptions research-challenger connector. First real forward paired capture/settlement must be verified independently rather than inferred from the merge.
- PR #175 (exact-byte roster re-pin for the receptions workflow) was closed, not merged (2026-09-23T05:43:16Z) -- superseded by PR #178's schema/row-count/coverage sanity-check pattern, which replaced the exact-byte pin this PR would have refreshed rather than re-pinning it again.
- PR #174 real FTN charting adapter remains draft/research-only; no independent footage watching, no live predictive consumer. PR #170 film prototype is synthetic-only and unmerged.
- MLB PR #173 and NFL PR #176 merges and the 463-pick MLB public History recovery were reported and independently corroborated at the repository/PR layer; do not rerun a closed incident without new evidence.
- As of this checkpoint, no NFL receptions challenger (role-adjusted, team-opportunity, coaching-aware, snap-share-adjusted, or QB-change-aware) has demonstrated a real matched-population accuracy improvement over B0's own rolling-mean baseline. `nfl/research/receptions_shadow.py` B0 itself still relies on recent player receptions and historical residuals, not the full expected-team-plays x routes x target-probability x catch-probability mechanism. Other football research modules are **not automatically live consumers** merely because a connector PR merged -- check the actual consumer function, not the PR title.

## 13. Claude's independent adversarial brainstorming obligation

**Claude must brainstorm *before reading Section 10* if possible in its own environment, or explicitly label independent findings vs items reproduced from this spec.** It must not merely produce more attractive headings. Require: (a) at least 15 concrete additional testable angles across player, coach, opponent, film, game/clock, odds, data acquisition, grading, and customer usability; (b) remove duplicates after crosswalk; (c) distinguish observable evidence from speculative storytelling; (d) for each genuine novel factor, provide hypothesis, legal source/coverage, unit/denominator, point-in-time cutoff, intended live consumer, target market, negative control, likely confounder, blockers/cost and falsification experiment; (e) actually implement the most tractable high-value missing *consumer* under separate scoped mission ownership, not just another document; (f) preserve rejected ideas with reason rather than deleting.

Inspect `AGENTS.md`, `CLAUDE.md`, `engineering/PROJECT_STATE.md`, `engineering/ENGINEERING_HANDOFF.md`, Issue #91 and the actual current live NFL workflow before assigning statuses. Account for existing Codex claims and do not edit their files without bridge handoff. This permanent requirements document is a starting obligation, not a claim of comprehensive discovery. When Claude proposes additions, append them in a versioned new section with source/hypothesis and supersession history, and update the existing single authoritative factor-status matrix rather than create another competing inventory.

**Authorization:** Recording requirements, source investigation, branch implementation, tests, independent reviews and draft PRs may proceed within existing authorization. Any merge/deploy/model or public-pick promotion, paid footage source, new paid service or policy change still requires Jacob's explicit applicable approval.


## 14. Permanent SuperClaude execution doctrine — Jacob's explicit 2026-09-23 correction

**SuperClaude must be an empowered, resource-aware multi-agent engineering and research operation, not a single serial implementer or a report-writing persona.** Maximize useful, independently verified source-to-prediction implementation and thoughtful discovery, subject to legitimate tool/platform limits, finite context, cost/access and research integrity. The previous sections define the completeness target; this section defines **how** Claude is expected to execute it.

### 14.1 Autonomous agency and engineering freedom

Claude leads architecture and implementation within the current mission and existing authority. It may challenge previous prompts, modify sequencing, select an alternate implementation, reuse or retire redundant modules, write real code, run genuine research experiments, create disjoint worktrees/branches/draft PRs, perform CI and targeted runtime checks, and independently investigate unexpected opportunities. Explain evidence-based material design changes, but do not stop for routine tactical decisions, repeat authorization requests for already permitted development work, or ask Jacob to manage a queue of trivial subtasks. Prefer actual working prediction consumers over additional scaffolds. A PR/spec can be a useful artifact but cannot substitute for execution.

### 14.2 Agent delegation and parallelism

Plan at most one shared integration owner per actively edited file/path; use specialized **bounded** agents or subagents for independent, nonconflicting workstreams such as (1) sources/rights/PIT/identity, (2) player/opponent/coaching features and model integration, (3) probability/odds/selection math, (4) independent adversarial reviewer and reproducibility, and (5) MLB predictive work. Select only the agents that have useful independent work NOW, give each exact branch/worktree, base SHA, paths, contracts, acceptance tests, explicit exclusions and stop conditions, then compose their artifacts through a single integration owner. Claude/Codex claims in Issue #91 govern active ownership. Existing 2–3 bounded concurrent agents is the default *anti-collision operating guard*, not a mandate to leave otherwise authorized useful capability idle: if more independent lanes have genuinely disjoint work and platform capacity permits, propose a bounded expansion through Issue #91 and obtain Jacob's approval if it would supersede his previous explicit concurrency limit. Do not imply that a GitHub comment launches or wakes an agent. Escalate conflicting edits, access rights or native permission gates rather than bypassing them.

### 14.3 Model routing and available resources

Use the highest-capability reasoning/model configuration **available within the actual environment and authorization** for architecture choices, causal/quantitative modeling, hard debugging, novel hypothesis generation, scientific audit and decisions with substantial research risk. Route bounded extraction, schema review, simple test scaffolding, documentation formatting and low-risk routine edits to cheaper/faster available models or agents when quality can be independently checked. Escalate ambiguous/high-stakes results to stronger reasoning and independent review. Do not repeatedly send the same full repository/context to several agents or make unverified claims about actual model availability, paid entitlements, API keys, limits, cost or future capacity. Claude may choose the best available model mix and dynamically revise it with measured outcomes; user retains authority over NEW paid services or higher paid spend.

### 14.4 Context and token utilization — maximize valuable work, not token burn

Use sufficient reasoning depth and context for hard scientific/code decisions; do not artificially truncate work, deliver ceremonial summaries, or stop at a scaffolding deliverable while a real implementation is feasible. Conversely, maximize **useful verified output per token**, not total token usage: use narrow file reads, delta summaries, reusable pinned context, targeted tests before broad CI, bounded subagent context and durable handoff artifacts. Retain a compact authoritative state/ownership ledger so sessions can resume without reorientation. Use strong models and more tokens when they change the solution or independently validate a risky claim. Document actual capacity/limits without promising unlimited tokens or uninterrupted background execution.

### 14.5 Independent creativity and challenge

Claude MUST independently brainstorm blind spots *before* reading this specification's added candidate list where practical, rather than treating Jacob/SUPERCHAD's list as exhaustive. Explore alternative causal models and genuine opponent-specific hypotheses, source accessibility/rights, film limitations, route-level denominators, player-opportunity mass balance, conditional game-script distributions, market execution and data-quality failure modes. Challenge methodological drift, duplicated features, overfitting, invalid data vintage and hidden assumptions. For novel ideas, provide falsifiable source→feature→consumer→prospective test; implement the best tractable missing predictive pathway within an owned bounded workstream. Negative findings are first-class results; no feature is compulsory for promotion merely because this specification enumerates it.

### 14.6 Full mission delivery and meaningful checkpoints

At mission start: refresh main/Issue #91, identify owned and already-completed work, delegate disjoint work immediately and choose the shortest path to a real integrated prediction. At meaningful checkpoints: report actual commits, tests, source/date/market evidence, live-consumer proof, negative findings, review and blockers. At completion: present the exact operational delta and reproducible artifacts, not a task list disguised as progress. Continue available authorized work if an unrelated PR is awaiting review or merge approval. No unauthorized merge/deploy/model promotion/public-pick policy change, no native permission circumvention, no new paid-source contract without Jacob's explicit approval.

**This execution doctrine and the factor-completeness requirements are equally permanent. Neither may be silently omitted from future Claude handoffs.**

Alligator.

## 15. 2026-09-25 Jacob directive reconciliation — same register, no promotion

The permanent [Issue #91 directive](https://github.com/werriesjacob1-cmyk/Full-Count/issues/91#issuecomment-5835759702) refines the requirements above. The IDs below remain the accountable units; this crosswalk does not mark the broader capability complete because a narrow proxy was tested. `NOT YET CERTIFIED` means this register has not established a complete source → strictly-prior feature → actual consumer → matched and prospective evaluation chain. It is not a claim that no related code exists. Each ID's eventual status must include source/rights, vintage, coverage/identity, consumer, comparison, owner, evidence and exact blocker as specified in §0.

| Directive family | Existing requirement IDs and explicit scope to retain | Current evidence and boundary |
|---|---|---|
| 1. Current opportunity and redistribution | P06–P07, R01–R09, N01, X01–X02: forecast snaps, **routes per team dropback**, first-read/route target probability, carries, first-team practice, designed touches/screens, third-down/two-minute/short-yardage/goal-line use, replacements and limited-player workloads before kickoff. | Tier 1 #203 and earlier opportunity work are research, not a certified current-week role forecast. Keep target/carry/route mass balance and role uncertainty open. |
| 2. Coaching and adaptation | C01–C09, R06, N03–N04: verify actual HC/OC/DC/**playcaller** intervals; scripted drives, package and down-distance preferences, injury/QB/bye responses, opponent-specific and halftime changes, and scenario uncertainty. | Historical regime consumer #181 exists; genuine current playcaller identity and game-plan prediction remain NOT YET CERTIFIED. Quotes need provenance, not automatic deterministic use. |
| 3. Individual defense and coverage | D02–D08, P07–P08, F03–F07, X07: defender identity/position, slot/outside alignment, actual receiver assignment, targets/receptions/yards allowed with **valid coverage-route exposure denominators**, bracket/shadow probabilities and replacement effects. | #207 F11 rejected and F12 not supported in their tested family proxies. #214 F18 has partial defender-attributed target data only: no receiver–defender assignment, all-route denominator or validated predictive-use rights. F26 full matchup remains SOURCE/RIGHTS BLOCKED pending a legitimate assignment source; on-field co-presence and team coverage are not assignments. |
| 4. QB decisions and chemistry | P01, P09–P10, D04–D05, F04, X04: read order, time to throw, pressure-to-sack, deep/checkdown/scramble/play-action decisions and QB–receiver interaction conditional on protection, coverage and teammate availability. | #206 F13 pressure/blitz challenger had a negative matched evaluation; that narrow result does not close reads or chemistry. |
| 5. OL, rushing scheme and fronts | P02, P06, D05–D08, F03–F04, X06: projected OL starters/continuity, individual blocking and rushers, actual run concepts/location/fit, runner contact/explosive outcomes and context-specific box counts. | #186 rushing B0 remains unmerged. #212 F16's box proxy did not beat its scale control; it does not establish run concept, front assignment or runner-specific matchup. |
| 6. Game script and joint outcomes | G01–G07, P12, M06–M08, M14, X01–X03: score/time-conditioned usage, possessions, attempts, margin, correlation and coherent player/team/game distributions through alternate thresholds. | #204 is Tier 1 research; mean-yardage accuracy alone cannot establish line-hit or price value. Coherent joint prospective validation remains NOT YET CERTIFIED. |
| 7. All 32 current-week news | N01–N08, R04–R05: official practice/inactives/transactions, local reporting and warmups for all teams with identity, publication/ingest/correction clocks, conflict resolution and explicit role/availability effect. | Source collection or an article feed alone is partial. Complete 32-team, time-safe predictive coverage remains NOT YET CERTIFIED. |
| 8. Development, fatigue and aging | P04–P05, P11, R05–R06, X12: rookie/college priors, age/career trajectory, injury return, workload/recovery, new-team and QB–WR adaptation, small-sample uncertainty. | No blanket health, intent or age penalty may be inferred. A measured current-role/efficiency consumer and held/prospective evidence remain NOT YET CERTIFIED. |
| 9. Genuine tracking, charting and film | F01–F09, P08, D02–D06, X05, X08: rights-cleared footage or tracking, game/play/video synchronization, visibility limits, personnel/routes/depth/separation, coverage/blocking/reads where observable and independent annotation checks. | #174 is structured FTN charting; #170 is a synthetic film prototype. Neither is an operational independent film watcher. #214 identifies a separate defender-charting rights/assignment gap. No unauthorized source access is implied. |
| 10. Markets, grading and learning | M01–M14, E01–E10, X09–X15: authentic multi-book standard/alternate/plus-money offers and timestamps, movement/news chronology, coherent threshold probabilities, rules/push/void, immutable full-candidate/pick evidence, postgame grades and matched-volume hit rate **separate from** actual-price return. | #196/#199 remain research-only price/eligibility work. A captured offer, a graded board and a profitable-looking historical probability are distinct evidentiary stages. |
| 11. Cross-factor integration | P12, R01–R09, C01–C07, D01–D08, G01–G07, M06–M08, E01–E04, X01–X04, X18: opportunity × coaching × injury × QB × OL × defense × game script × efficiency in coherent predictions without double counting. | Tier 1 #202–#205 prospective H1–H3 is frozen and active. Do not alter its Saturday seal, interim-peek, backfill, or infer promotion from a module/test or a favorable subgroup. |

### Dated research-stage distinctions

- **Implemented or source-captured is not validated:** #202–#205 are an active prospective research protocol; their frozen parameters, cutoffs and seal remain authoritative. #174 and #170 supply charting/prototype evidence at different stages, not operational film-derived predictive value.
- **Negative or near-zero narrow tests remain preserved:** #206 F13, #209 F14, #211 F15 and #212 F16 are not promoted; #207 F11 is rejected and F12 not supported; #213 F17 is not supported against its simple-efficiency control. These findings reject the tested formulations, not every broader football mechanism.
- **Source/rights blocked is different from a failed model:** #214 F18 documents real defender-attributed target statistics but lacks verified receiver–defender assignments, coverage exposure and established downstream predictive-use rights. No individual matchup model was fit or promoted.
- **Operational completion requires a separate decision:** a later legitimately sourced factor must demonstrate incremental value against matched B0 and a suitable simple control, with uncertainty, point-in-time-safe identity and prospective pregame evidence. Promotion then requires Jacob's explicit authorization and proof of more correct legitimately usable selections at comparable declared volume; actual-price returns remain separate.

## 16. Per-angle accountability status (directive §"Accountability"; added 2026-09-25 by Claude Code, continuing Codex's §15)

This section uses the directive's five statuses, one per listed angle:
- **COMPLETE:** existing complete capability.
- **PARTIAL:** partial or source-only.
- **TESTED:** research tested. The row states whether the result was negative, near zero or positive, and whether it was historical or prospective.
- **BLOCKED:** source or rights blocked.
- **NOT YET:** not yet investigated.

**No angle is COMPLETE today.** Nothing has passed matched historical evaluation, prospective pregame evidence and Jacob's promotion.

**Other conventions:**
- **IDs:** the IDs in §1–§10 and the §11 crosswalk still apply; this is a ledger, not a new register.
- **Owner:** the agent that last held it. "Unassigned" means Jacob or SUPERCHAD assigns it.
- **Scope of a TESTED result:** a TESTED result closes only the tested formulation (§15).
- **Candidate sources:** "Candidate source" means named but not verified in this register.
- **Two "F" namespaces:** the register's film requirement IDs (F01–F09, §5) appear only in the IDs column. Research factor numbers (F1–F18, F26, e.g. "F16 #212") appear only in Evidence and Next action, and refer to Tier 1 and Tier 2 workstreams, not to register IDs.

### 1. Opportunity, roles and redistribution

| Angle | IDs | Status | Evidence | Owner | Next action |
|---|---|---|---|---|---|
| Expected snaps and snap-share role | P06, R02 | TESTED (historical; BUILT) | Tier 1 F2, #203 | Claude | Prospective exploratory rows in the protocol seal |
| Target share × team targets | P06, R02 | TESTED (positive historical, narrow; prospective H1 active) | Tier 1 F3, #203; H1 seal | Claude | Week-8 protocol analysis |
| Carries and carry share | P02, R07 | PARTIAL | #186 rushing B0 (unmerged); F16 #212 negative | Codex / Claude | Settle #186 disposition |
| Routes run / routes per dropback | P06–P07 | BLOCKED | Participation `route` is target-only, and there is no in-season participation (#207 README) | Unassigned | A licensed all-route source |
| Target probability per supported route | P07 | BLOCKED | No un-targeted route denominator (#174, #207) | Unassigned | Same as above |
| First-read targets | P09, X04 | PARTIAL (source only) | FTN 2026 charting exposes a thrown-read field (#174 evidence); no consumer | Unassigned | Feasibility: coverage, vintage, rights |
| First-team practice | N01 | BLOCKED | Not in official reports; beat reports only, unverified | Unassigned | News provenance pipeline (row 7) |
| Designed touches and scripted screens | R02, C03 | NOT YET | — | Unassigned | Source check (play-by-play has no screen flag; FTN candidate) |
| Third-down and two-minute roles | R02, C03 | NOT YET | Play-by-play supports it | Unassigned | Feature feasibility |
| Short-yardage and goal-line roles | R02, P02 | TESTED (positive historical, narrow; prospective H3 active) | Tier 1 F4, #205 | Claude | Week-8 analysis |
| Substitutions and rotations | R03, R07 | PARTIAL | Snap counts only (F2) | Claude | — |
| Injury replacement hierarchy and absence redistribution | R04, R08 | TESTED (historical; BUILT) | Tier 1 F9, #203 | Claude | Prospective rows |
| Limited-player workloads | N05, R05 | PARTIAL | F8 injury/practice, PARTIAL | Claude | Final-report timing |
| Personnel-conditioned opportunity | R03 | TESTED (negative) | F15, #211 | Codex | None (preserved) |

### 2. Coaching, playcallers and adaptation

| Angle | IDs | Status | Evidence | Owner | Next action |
|---|---|---|---|---|---|
| HC identity and regimes | C01 | PARTIAL (HC complete; used in F12 regime rule) | #181 registry | — | — |
| OC/DC and actual playcaller identity | C01 | BLOCKED | Registry DC/playcaller `NO_INTERVALS_INGESTED`; PFR returns a 403 bot challenge; nflverse has no staff data | Unassigned | A legitimate staff source |
| Pass rate / pace tendencies | C02, R01 | TESTED (REJECTED) | Tier 1 F5, #204 | Claude | None |
| Offensive concepts, play-action and RPO | C02 | TESTED (null) | F14, #209 | Codex | None |
| Scripted drives, RB rotation, red-zone decisions, 2-minute | C03 | NOT YET (except red-zone via F4) | — | Unassigned | Feasibility |
| Post-injury, bye and QB-change usage | C06, R06 | PARTIAL | F9 redistribution only | Claude | — |
| Opponent-specific and halftime adjustment | C04–C05 | NOT YET | — | Unassigned | — |
| Coordinator tendency transfer across teams | C06 | BLOCKED | Needs coordinator identity | Unassigned | — |
| Coach and player statements with provenance | C09, N03 | NOT YET | — | Unassigned | Row 7 pipeline |

### 3. Defense, coverage and individual matchups

| Angle | IDs | Status | Evidence | Owner | Next action |
|---|---|---|---|---|---|
| Man/zone and coverage family, team level | D02 | TESTED (F11 REJECTED; F12 NOT SUPPORTED) | #207 | Claude | Exploratory prospective rows in the protocol seal |
| Opponent allowed-by-position | D01 | TESTED (historical; BUILT) | Tier 1 F10, #204 | Claude | — |
| Individual defender stats | D03 | PARTIAL (source only) | F18, #214: PFR advanced defense, defender-attributed targets | Codex | Rights for predictive use |
| Verified defender→receiver assignment and shadow | D03–D04 | BLOCKED | #214: no assignment source; co-presence is not an assignment | Unassigned | F26 source search |
| Brackets, doubles, rotations and disguise | D02, D08 | BLOCKED | No public label | Unassigned | — |
| Defensive substitutions and replacement effects | D07 | NOT YET | — | Unassigned | — |

### 4. QB decisions and chemistry

| Angle | IDs | Status | Evidence | Owner | Next action |
|---|---|---|---|---|---|
| Pressure/blitz response | D05, P09 | TESTED (negative) | F13, #206 | Codex | None |
| Read progression | P09 | PARTIAL (source only) | FTN thrown-read (#174) | Unassigned | Feasibility |
| Time to throw, deep/checkdown/scramble, play-action response | P09, P01 | NOT YET | Candidate source: NGS passing aggregates | Unassigned | Source verification |
| QB–receiver chemistry and QB-change effects on teammates | P10 | NOT YET | — | Unassigned | Interaction test vs simple baseline |

### 5. OL, rushing and fronts

| Angle | IDs | Status | Evidence | Owner | Next action |
|---|---|---|---|---|---|
| Rushing B0 / runner baseline | P02 | PARTIAL | #186, unmerged research | Claude | Disposition |
| Box count / front proxy | D06 | TESTED (negative) | F16, #212 | Codex | None |
| OL starters and continuity | D05–D06 | NOT YET | Snap counts support it | Unassigned | Feasibility |
| Individual blocking; rushers vs OL matchups | D05, F04 | BLOCKED | No public assignment data | Unassigned | — |
| Run concept, location and fit | D06, F03 | BLOCKED (concept); NOT YET (run location in play-by-play) | — | Unassigned | — |
| Yards before/after contact, missed tackles forced, explosives | P02 | NOT YET | Candidate source: PFR advanced rushing via nflverse. F17 was scoped to receiving to avoid F16 overlap | Unassigned | Feasibility |

### 6. Game script and joint outcomes

| Angle | IDs | Status | Evidence | Owner | Next action |
|---|---|---|---|---|---|
| Game lines and implied totals | G01 | TESTED (historical; BUILT; uses market input) | Tier 1 F1, #204 | Claude | — |
| Weather, roof and surface | G03 | TESTED (positive historical for receiving yards; prospective H2 active) | Tier 1 F6, #204 | Claude | Week-8 analysis |
| Rest and travel | G04 | TESTED (REJECTED) | Tier 1 F7, #204 | Claude | None |
| Score/time-conditioned workload, possessions, 4th-down, overtime | G02, G05 | NOT YET | — | Unassigned | — |
| Coherent joint and alternate-line distributions, correlation | M06–M08, M14 | PARTIAL | #190 passing-yards alternate ladder (research) | Claude | — |

### 7. 32-team current-week information

| Angle | IDs | Status | Evidence | Owner | Next action |
|---|---|---|---|---|---|
| Official injuries, practice and inactives | N01 | PARTIAL | F8; inactives capture used in the ATL@GB cycle (`engineering/nfl_atl_gb_20260924`) | Claude | Final-report automation |
| Transactions and depth charts | N01 | NOT YET | — | Unassigned | — |
| Pressers, beat/local reporting, warmups | N02–N04 | NOT YET (source audits only; see NFL_GENIUS_NEWS_BRAIN_2026-09-18) | — | Unassigned | Provenance pipeline |
| Converting verified news to role/availability changes | N05, N07 | NOT YET | — | Unassigned | — |

### 8. Development and changing performance

| Angle | IDs | Status | Evidence | Owner | Next action |
|---|---|---|---|---|---|
| Efficiency persistence and regression | P05 | TESTED (F17 NOT SUPPORTED vs simple control; skill persistence r ≈ 0.25) | #213 | Claude | None (preserved) |
| Rookie/college priors, aging, injury return, fatigue, new-team adaptation | P04, P11, X12 | NOT YET | — | Unassigned | — |

### 9. Tracking, charting and film

| Angle | IDs | Status | Evidence | Owner | Next action |
|---|---|---|---|---|---|
| Structured third-party charting | F05, F08 | PARTIAL | #174 FTN charting (descriptive) | Codex | — |
| Film observation pipeline | F02–F06 | PARTIAL (synthetic prototype) | #170 | Codex | Rights-cleared footage |
| Rights-cleared All-22 / footage | F01 | BLOCKED | No licensed source | Unassigned | — |
| Tracking speed, separation, cushion | F04, P08 | NOT YET | Candidate source: NGS receiving aggregates | Unassigned | Source verification |

### 10. Market, grading and learning

| Angle | IDs | Status | Evidence | Owner | Next action |
|---|---|---|---|---|---|
| Authentic offer capture and exact B0 join | M01–M03 | PARTIAL | #196/#199: 9/9 exact joins on ATL@GB; 0 eligible behind the 3 gates | Codex | Certify the gates |
| Multiple books | M04 | NOT YET | FanDuel only | Unassigned | — |
| Postgame grading path | M10, E05 | PARTIAL (exercised on ATL@GB research rows) | `engineering/nfl_atl_gb_20260924` | Claude | — |
| Line movement vs news | M05, X13–X14 | NOT YET | — | Unassigned | — |
| Matched-volume hit rate, separate from price return | M11, E01–E02 | PARTIAL | Protocol §4 equal-volume comparison: NOT AVAILABLE until eligible offers exist | Claude / Codex | — |

### 11. Integration

| Angle | IDs | Status | Evidence | Owner | Next action |
|---|---|---|---|---|---|
| Coherent multi-factor player/game predictions | P12, X01–X04, X18 | PARTIAL | Tier 1 challengers are single-family consumers; no validated combined model; prospective H1–H3 frozen for weeks 3–8 | Claude | Week-8 analysis, then a pre-registered combination |

Alligator.

# FULL COUNT NFL MNF final execution handoff — 2026-09-14

Status: execution-ready research packet. No merge, deployment, public pick, model promotion, grading activation, or immutable-ledger mutation is authorized by this document.

Current moving `main` observed before this handoff: `52b66539797e8f4f03132555a7b758f7b0ea46a2` (MLB pipeline dashboard refresh). Re-fetch before any execution because `main` moves automatically.

Preparation branch: `superchad/nfl-mnf-execution-packet-20260914`.
Draft PR: #94.

## 1. Mission

Before the 2026-09-14 Denver at Kansas City kickoff at 7:15 PM America/Chicago, obtain an honest FULL COUNT research PICK/PASS decision for the existing primary two-sided quarterback passing-yards market. Do not weaken identity, availability, timing, source-digest, model-reproduction, or prospective-seal gates to manufacture action.

Passing yards is the only NFL market currently close enough to the complete chain for tonight. Full-game spread and total are observed but not yet normalized/modelled/graded prospectively; they must not distract from the final passing-yards capture.

## 2. Important correction: this is Week 1

DEN-KC on 2026-09-14 is 2026 regular-season Week 1. Therefore no 2026 regular-season player-stat row can be strictly prior to tonight's target for Bo Nix or Patrick Mahomes.

For **tonight only**, the existing current-projection history sourced from 2025, including legitimate 2025 postseason appearances, is correct. Do not wire current-2026 player rows into tonight's B0 just because a 2026 weekly source now exists.

For Week 2+, `nfl/research/live_prior_history.py` on this preparation branch provides the strict target-week cutoff contract. The official nflverse `stats_player_week_2026.csv` asset was observed/pinned in `engineering/evidence/NFL_2026_WEEKLY_SOURCE_PIN_2026-09-14.json` at SHA-256 `7bf9b616d9f86a5357093b5af6bf1fa7138c1d4cb46cab4c2765fef852c30bc4`. It must be re-digested whenever used because the upstream season file is mutable.

The historical B0 calibration substrate remains frozen: 2024 n=611 / MAE 70.83543371522094, 2025 n=617 / MAE 72.41990815775257, pooled residual n=1228. Current-season live history must never alter that calibration population silently.

## 3. The only workflow repair required for tonight

The current workflow `.github/workflows/nfl-live-passing-yards-shadow-board.yml` cannot target Monday manually: `workflow_dispatch` has no input, `TARGET_LOCAL_DATE` is blank, and runtime falls forward to the next Sunday.

The exact minimal intended patch is retained at:

`engineering/NFL_MNF_WORKFLOW_PATCH_2026-09-14.diff`

It does only this:

1. adds optional manual `target_local_date` input,
2. passes it into `TARGET_LOCAL_DATE`,
3. uses tested `nfl/prospective/slate_target.py` for strict YYYY-MM-DD validation / Sunday fallback.

It must **not** alter B0, probabilities, source pins, identity binding, official-inactive logic, snapshot schema, capture/seal timing, or publication semantics.

## 4. Exact candidate/quarantine chain

The operating workflow currently performs these gates in order:

1. normalize primary passing-yards market,
2. exact roster/event/team/GSIS binding; unbound candidates are excluded,
3. official availability evaluation,
4. multiple-primary-QB-per-team ambiguity check,
5. same-team role-continuity gate,
6. minimum/positive prior-history B0 projection,
7. empirical residual probability + two-sided market de-vig,
8. `SHADOW_ONLY` only if no quarantine reason remains; otherwise `QUARANTINED`,
9. validate every observation and seal before kickoff,
10. deterministic snapshot seal.

Availability itself fails closed unless a current official NFL inactive report:

- was published no later than observed,
- was observed before kickoff,
- was published on the game's Chicago-local date,
- covers BOTH teams in the event,
- binds inactive identities cleanly.

Availability outcomes that block: `LISTED_INACTIVE`, `UNKNOWN_PLAYER_BINDING`, `UNKNOWN_GAME_COVERAGE`, or invalid candidate identity. Positive status is only `NOT_LISTED_INACTIVE`; the code does not infer "healthy" or "starter" from absence on the list.

Other possible blockers: `MULTIPLE_PRIMARY_QBS_SAME_TEAM`, `TEAM_CHANGE_ROLE_UNCERTAINTY`, `UNKNOWN_NO_HISTORY`, `UNKNOWN_PRIOR_TEAM`, `INSUFFICIENT_HISTORY`.

## 5. Why the Sunday launch quarantined all 26 — resolved

Retained live artifact from run `34715686590`, artifact `10304778237`, digest `sha256:8dab23ffda6c3b9b3be358624d6aefba3901545f0e84eb679e811fbe3365f6ce` was downloaded and audited.

It showed:

- 13 pregame events,
- 26 normalized primary passing candidates,
- 26/26 exact BOUND identities,
- zero market failures,
- 25 B0-history eligible / 1 insufficient,
- exact frozen benchmark reproduction,
- 26/26 `QUARANTINED`,
- **all 26 had `UNKNOWN_GAME_COVERAGE`**,
- five also had team-change uncertainty,
- one also lacked B0 history.

That capture occurred before valid same-day inactive-report coverage. Therefore the universal quarantine was an expected availability-timing result, **not** a market normalization, identity, scorer, or B0-reproduction defect.

This materially narrows tonight's final risk: after Monday targeting is repaired, the key time-dependent gate is valid official same-day inactive coverage.

## 6. Preliminary B0 reproduction for tonight

Machine-readable evidence is in `engineering/evidence/NFL_MNF_PRELIMINARY_B0_2026-09-14.json`.

Using the exact retained/pinned 2023–2025 source bytes from the successful Sunday artifact, the B0 residual population was independently reproduced exactly before scoring tonight's reference lines.

### Bo Nix — preliminary

GSIS `00-0039732`, DEN.
Last five legitimate 2025 appearances used by B0: 302, 352, 182, 141, 279 passing yards.

- B0 projection: **251.2**
- reference line: **227.5** (not final authoritative live capture)
- gap: **+23.7**
- B0 P(OVER 227.5): **59.8374%**
- P(OVER 232.5): **57.2358%**
- P(OVER 237.5): **54.8780%**

Current independent numberFire Week 1 projection is about **234.6**, still above the 227.5 reference neighborhood. Thus B0 and the independent contextual projection currently agree on direction, though not magnitude.

Preliminary status: **OVER signal; not final until live FanDuel capture + official inactive coverage + final seal.**

### Patrick Mahomes — preliminary

GSIS `00-0033873`, KC.
Last five legitimate 2025 appearances used by B0: 276, 352, 261, 160, 189 passing yards.

- B0 projection: **247.6**
- reference line: **223.5** (not final authoritative live capture)
- gap: **+24.1**
- B0 P(OVER 223.5): **59.8374%**
- P(OVER 228.5): **57.2358%**
- P(OVER 233.5): **55.0407%**

However, current independent numberFire Week 1 projection is about **219.5**, below the market neighborhood. Mahomes is returning from ACL/LCL repair with no preseason game reps, and Chiefs LT Josh Simmons is officially out. This is a genuine regime conflict that pooled historical B0 does not model.

Preliminary status: **PASS / unresolved regime conflict unless live evidence materially resolves it. Do not force the B0 OVER.**

## 7. Current contextual state to preserve as diagnostics, not manual yard adjustments

- Official NFL Week 1 injury report lists Patrick Mahomes as full practice participation without a game-status designation; Josh Simmons and Chamarri Conner are OUT for KC.
- Denver reports all 53 rostered players expected available; Marvin Mims was full all week and has no game status.
- Bo Nix is also returning from a prior ankle fracture, but he was cleared and had preseason game action; treat this as context/risk, not an ad-hoc projection adjustment.
- Kansas City forecast around kickoff is hot/clear with light wind; no obvious weather-based passing quarantine is indicated in the current forecast. Recheck if conditions change.
- Current public market references cluster Nix passing around 227.5–228.5. Mahomes references are around 223.5–225.5. These are context only: the FULL COUNT live FanDuel payload must be authoritative at decision time.

## 8. Codex execution order at reset

Do this sequentially. Do not spend the pre-kickoff window rediscovering architecture.

1. **Re-fetch moving `main`; record exact SHA.** Compare against this packet and PR #94. Report only material drift.
2. Create/reuse a reversible execution branch from current main. Do not merge.
3. Apply the minimal date-target change represented by `engineering/NFL_MNF_WORKFLOW_PATCH_2026-09-14.diff`, adapting only if moving-main syntax requires it.
4. Bring over/implement the tested `nfl/prospective/slate_target.py` helper and its tests if the execution branch does not already contain them.
5. Do **not** change tonight's 2025 live-history semantics because this is Week 1.
6. Run focused slate-target tests, full NFL Test Suite, and root Test Suite on the exact execution head. Distinguish inherited unrelated MLB fixture failure from any new regression; NFL regressions are blockers.
7. Only after exact-head NFL correctness is green, manually run the live passing-yards workflow with `target_local_date=2026-09-14`.
8. Preserve the full research artifact. No publication.
9. Return a candidate table for every DEN-KC primary passing-yard row with:
   - player / GSIS / team,
   - event ID and kickoff,
   - market ID,
   - current line,
   - over/under price,
   - source payload SHA + captured_at,
   - B0 projection / history n / line gap,
   - model P(over) / P(under),
   - de-vig fair market P(over) / P(under),
   - raw research edge and direction,
   - binding status,
   - team-candidate count,
   - role-continuity status,
   - availability status / covered report count,
   - decision status,
   - every quarantine reason.
10. Compare Nix/Mahomes output against `engineering/evidence/NFL_MNF_PRELIMINARY_B0_2026-09-14.json`. Any B0 difference is a stop/report condition unless source/model semantics are proven different.
11. Preliminary run may remain `UNKNOWN_GAME_COVERAGE` if official inactive reports are not yet valid. That is expected; do not bypass it.
12. Near the official inactive-report window, run again against fresh live FanDuel state. The final decision must use the latest valid pregame line/price, not a stale preliminary line.
13. Final seal must satisfy capture/seal < kickoff and current bound inactive coverage for both teams.
14. Sensitivity: score the candidate at roughly ±5 passing yards around the final line or the nearest captured alternate-line neighborhood if legitimate alternate data is available. Do not invent alternate prices.
15. Context audit: confirm no new QB/OL/skill-player/weather information creates an unmodeled regime warning.
16. Return **research PICK or PASS**. PASS is correct if evidence remains conflicted or a gate fails. Do not invent a universal edge threshold today.
17. No merge, deploy, public NFL pick, model promotion, grading activation, or immutable ledger mutation without Jacob's explicit authorization.
18. If a native/elevated permission is required, use the existing Issue #91 remote approval relay and continue unrelated reversible work while waiting.

## 9. Tonight's expected decision logic

### Nix

If the final live line remains near 227.5–228.5, B0 remains materially above it, the exact live probability stays positive after de-vig, role/identity/availability gates are all clean, sensitivity remains positive, and no new injury/context warning appears, Nix OVER is the leading research candidate.

Do not convert this expectation into a final pick before the final live seal.

### Mahomes

Because B0 OVER conflicts with the current independent contextual projection and a major return-from-injury/OL regime is present, default to PASS unless the live/full evidence offers a defensible reason that survives the predeclared gates. Do not hand-adjust B0 to make it agree.

## 10. Spread / total lane after final passing capture

Repository truth is frozen in `engineering/NFL_SPREAD_TOTAL_READINESS_2026-09-14.md`.

Current full-game spread and total capability: feed observed YES; normalized NO; historical market dataset NO; model NO; prospective capture NO; grader NO; public selector NO.

After the final MNF passing capture is secure:

1. normalize full-game moneyline/spread/total preserving every native ID/runner/handicap/price/timestamp/source SHA;
2. build authoritative final-score grader with explicit pushes/overtime contract;
3. establish market-line residual controls without fabricating historical price vintages;
4. build leakage-safe rolling team baseline using prior-only team strength/scoring/pace/QB/injury/rest/weather features;
5. move toward one coherent margin/total distribution pricing ML/spread/total/alternates/team totals;
6. evaluate same-population / equal-volume realized accuracy, calibration, season stability, uncertainty, clustering, provenance, and leakage before promotion.

Claude's Tuesday lane should focus on this predictive/game-distribution research and contextual passing challengers. Codex should retain live ingestion/workflows/grading/CI plumbing.

## 11. Stop/report conditions

Stop rather than improvise if:

- current main materially changes the NFL workflow beneath this packet,
- exact-head NFL CI fails,
- FanDuel target event/market capture is inconclusive,
- pinned roster or historical player source unexpectedly drifts,
- exact player/event/team identity cannot be bound,
- B0 benchmark/reproduction changes,
- official inactive report is stale, missing either team, ambiguous, or post-kickoff,
- multiple primary QBs appear for a team,
- role continuity fails,
- capture or seal reaches/passes kickoff,
- any required action would merge/deploy/publish/promote/activate grading/change immutable evidence without Jacob approval.

The goal is not "produce a bet at all costs." The goal is the strongest honest pregame decision the current system can earn.

Alligator

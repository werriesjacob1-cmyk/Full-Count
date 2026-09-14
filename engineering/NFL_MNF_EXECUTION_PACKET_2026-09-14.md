# FULL COUNT NFL MNF Execution Packet — 2026-09-14

Status: planning-only, reversible branch artifact. No merge, deploy, public pick, grading activation, model promotion, or immutable ledger mutation is authorized by this document.

Branch base at creation: `9a82e72a4136d22dd3d0103f5b180144e338d8ed` from `main`.

## Objective

Produce an honest pregame FULL COUNT PICK/PASS decision for the 2026-09-14 Denver at Kansas City game using the existing NFL passing-yards research stack, without weakening pregame timing, identity, availability, provenance, or model-evidence gates.

## Repository truth already established

The existing `main` workflow `.github/workflows/nfl-live-passing-yards-shadow-board.yml` already performs:

1. live FanDuel NFL event discovery,
2. strict primary passing-yards normalization,
3. exact 2026 roster / GSIS identity binding,
4. frozen B0 projection,
5. pooled historical B0 residual scoring,
6. two-sided American-odds de-vig,
7. model OVER/UNDER probability and research edge,
8. official-inactives fail-closed availability gating,
9. deterministic pregame timing validation,
10. sealed prospective shadow snapshot output.

`nfl/research/passing_yards_shadow.py` is research-only and hard-codes `SHADOW_ONLY`; it must not be silently converted into a public selector.

PR #93 records that two simple rolling-box-score challengers were rejected against B0 in development, validation, and held evaluation. Do not spend the MNF window tuning more rearrangements of the same recent passing-yard/attempt windows.

PR #89 contains an artifact-only exact-identity prospective grading bridge. It remains separate and must not be merged or activated without explicit Jacob authorization.

## Blocker 1 — Monday target date

Current workflow behavior is Sunday-specific:

- `workflow_dispatch` exposes no date input.
- `TARGET_LOCAL_DATE` is hard-coded to an empty string.
- when no override exists, runtime chooses the next Sunday in America/Chicago.

Therefore a manual run on Monday 2026-09-14 targets Sunday 2026-09-20 instead of tonight.

### Required repair

Add a validated optional manual `target_local_date` workflow-dispatch input and wire it to `TARGET_LOCAL_DATE` only for manual runs. Scheduled Sunday behavior must remain unchanged.

Acceptance contract:

- manual `target_local_date=2026-09-14` selects only events whose Chicago-local date is 2026-09-14;
- malformed/non-ISO input fails closed before sportsbook capture;
- a date with no remaining pregame events fails closed;
- scheduled runs with no input preserve current next-Sunday behavior exactly;
- no relaxation of `captured_at <= sealed_at < kickoff`;
- no change to model math, identity binding, inactive logic, snapshot schema, publication logic, MLB paths, or immutable evidence;
- tests cover manual Monday date, default Sunday behavior, invalid date, no-event date, and post-kickoff rejection.

Prefer extracting date resolution into a small dependency-free helper that is independently testable instead of embedding more YAML-only logic.

## Blocker 2 — 2026 current-history freshness

The current live workflow shown on `main` builds present-game player prior history from the pinned 2025 player file. For Week 1 / early-season 2026 this may be intentional, but before tonight Codex must verify whether the workflow includes any strictly-prior 2026 appearances elsewhere in the current head.

For the 2026-09-14 game, if either QB has a completed 2026 appearance before tonight, excluding it from the live projection would make B0 inconsistent with its own last-five prior-appearance doctrine.

Required check:

- inspect the exact current workflow and `nflverse_history` path;
- determine whether strictly-prior 2026 Week 1 data is already included;
- if absent and the source is available pregame, add a source-vintage-preserved 2026 prior-only feed with explicit hash/provenance and no postgame leakage;
- never read tonight's outcome row or any data timestamped after decision seal;
- if 2026 source completeness cannot be proven point-in-time safe, quarantine or preserve the older B0 control rather than silently mixing uncertain data.

## Blocker 3 — availability timing

Do not delete or bypass the official-inactives gate to create action earlier in the day.

Use two stages:

### Stage A — preliminary research run

As soon as Monday targeting works:

- capture the game and primary passing-yards markets,
- bind QB identities,
- reproduce B0 benchmark invariants,
- calculate projections, lines, de-vigged prices, model probabilities, and research edges,
- report every quarantine reason,
- keep status research-only.

### Stage B — final pre-kickoff sealed run

After official inactive reports are valid for the game:

- recapture current sportsbook state,
- apply the unchanged availability gate,
- confirm all capture/seal timestamps precede kickoff,
- seal a new deterministic snapshot,
- treat the latest valid pregame line/price as the decision input.

Never carry an earlier edge forward if the market moved materially.

## PICK/PASS research gate for tonight

Tonight is not a model-promotion event. B0 remains the control and the output must be labeled research-only unless Jacob separately authorizes official publication/promotion.

A candidate can advance from raw research direction to an assistant-recommended research PICK only if all are true:

1. exact player/team/game identity is BOUND;
2. no multiple-primary-QB ambiguity for the team;
3. role-continuity gate passes or a separately validated replacement rule exists;
4. official availability gate passes at the final run;
5. capture and seal are strictly pre-kickoff;
6. current two-sided line and prices are present and de-vig successfully;
7. B0 benchmark reproduction remains exact;
8. no source-digest drift is unexplained;
9. model direction is non-neutral;
10. edge is not solely an artifact of a stale line;
11. contextual risk review does not identify a regime the pooled residual model clearly fails to represent;
12. the result survives a sensitivity check using nearby plausible line movement (at minimum ±5 passing yards or the nearest available alternate-line neighborhood if available);
13. there is no unresolved integrity or data-quality warning.

If these cannot be established, output PASS rather than lowering the gate.

Do not invent a universal edge threshold today without historical selector evidence. Report the raw model edge and sensitivity. A future selector threshold must be earned from prospective/historical equal-volume hit-rate evidence.

## Context checks — diagnostic, not ad hoc model overrides

For tonight, collect and preserve only facts known before kickoff that can explain regime risk:

- confirmed starting QB / role,
- offensive-line availability,
- material skill-position absences,
- opponent pass-defense / pressure environment using strictly prior information,
- weather and wind,
- market line movement between preliminary and final capture,
- any major return-from-injury or new-team/new-system condition.

These facts may trigger quarantine or confidence reduction. Do not manually add/subtract yards from B0 without a predeclared and historically evaluated rule.

## Spread / total lane

Jacob wants spreads and game totals to become first-class NFL markets. Do not block tonight's passing-yard path on building them, but begin a separate research lane after the MNF capture is secure.

First deliverable is a repository truth audit for spread/total:

- live FanDuel capture availability,
- normalized market representation,
- historical outcome source,
- point-in-time feature availability,
- baseline predictor existence,
- probability/calibration method,
- grader existence,
- prospective capture path.

Do not claim spread/total readiness until all eight are explicitly answered.

Preferred first baselines should be simple, reproducible controls that can be beaten later, not an opaque large model. Candidate feature families for subsequent challengers: team strength, QB value, expected plays/pace, neutral pass rate, offensive/defensive efficiency, injuries, rest/travel, weather, and market prior. Historical evaluation must be rolling-origin and leakage-safe.

## 4 PM Codex execution order

1. Fetch/reconcile moving `main`; record exact starting SHA.
2. Verify this packet against current head and report any stale assumption before coding.
3. Implement the minimal Monday/date-target repair with dependency-free tests.
4. Run exact-head NFL tests and root tests; distinguish inherited MLB fixture failures from new NFL regressions.
5. Verify 2026 prior-history freshness and repair only if point-in-time safety can be established.
6. Run a manual `2026-09-14` preliminary capture for DEN-KC.
7. Return the complete candidate table: player, line, prices, projection, line gap, model probabilities, market fair probabilities, edge, availability status, role-continuity status, quarantine reasons, source hashes, captured_at, kickoff.
8. Audit line sensitivity and regime/context warnings.
9. Preserve evidence artifact; no public pick or production promotion.
10. Near the official-inactive window, recapture and seal the final pregame snapshot.
11. Return research PICK/PASS recommendation(s) with all evidence and explicit limitations.
12. After the MNF path is safe, begin the spread/total readiness audit; do not interrupt the final pregame capture to do so.

## Stop / escalation conditions

Stop and report instead of improvising if any of the following occurs:

- FanDuel event/market source becomes inconclusive;
- roster/player source digest drifts from a pin without an explicit repin audit;
- exact identity cannot be established;
- official inactive source is stale/ambiguous/post-kickoff;
- current 2026 history source cannot be made prior-only safely;
- B0 reproduction changes;
- test regression touches NFL logic;
- capture or seal crosses kickoff;
- required change would merge/deploy/publish/promote/activate grading or mutate immutable evidence.

Those last actions require explicit Jacob authorization.

## Tuesday Claude lane

Claude should not duplicate Codex's live-pipeline work. Give Claude the predictive research lane:

- passing-yards challenger design using point-in-time opportunity/context features rather than rolling-box-score rearrangements;
- historical starter/role and expected-play feature construction;
- opponent/pass-rush/weather/injury feature provenance research;
- first leakage-safe spread and total baseline/challengers;
- equal-population champion-vs-challenger evaluation with season stability and uncertainty.

Codex retains live ingestion, workflow, grading, CI, and production plumbing. SUPERCHAD remains control plane and independent evidence auditor.

Alligator

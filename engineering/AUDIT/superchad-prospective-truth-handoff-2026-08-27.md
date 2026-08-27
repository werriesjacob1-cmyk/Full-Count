# SUPERCHAD Prospective Truth Handoff — 2026-08-27

Status: **LIVING HANDOFF — QUARANTINE BRANCH — STOP BEFORE MERGE**

## Authority / scope

- Repository: `werriesjacob1-cmyk/Full-Count`
- Branch: `superchad/prospective-truth-safe-01`
- Exact base: `a3017bce8a9dd41919f546a9e011818c3bf68c15`
- Base branch: `claude/canonical-rebuild-and-accuracy-foundation-01`
- This branch is prospective research measurement only.
- No merge, deploy, production-promotion, public-ledger mutation, or scheduler
  activation is authorized.
- The active canonical run and `canonical-durable-checkpoints` were not
  modified by this branch.

## Why this branch exists

The existing prospective candidate-funnel logger was valuable but still lost
the most important operational evidence:

- the real live test documented 969 candidates;
- 297 had multiple alternate lines;
- every market field was null because the logger did not attach FanDuel prices;
- the JSONL files were gitignored/ephemeral and could disappear with a
  container;
- deduplication proved candidate content changed, but not that an unchanged
  candidate was observed again at a later time;
- selector reporting did not yet enforce same legitimate usable volume or lock
  the observation/challenger before outcomes.

This branch closes those gaps without changing the production recommender.

# Implemented units

## 1. Stronger point-in-time candidate schema

Key commits:
- `96df9b690b6ab6ad01692dad88dcb2e4990862ec`
- `5d8530dd44a14bc6e6a28e33844709f8cb59bcbe`

Candidate records now preserve:

- sportsbook/feed family;
- actual posted odds;
- posted implied;
- fair-market probability;
- exact-two-sided vs assumed-hold fair method;
- edge vs fair;
- measured hold where available;
- price-clear state;
- market observation time and family fetch state;
- signal-weight adjustment;
- model / selection / calibration / feature versions;
- exact code SHA and run timestamps.

Conservative market-state semantics:

- MATCHED
- NOT_MATCHED
- FETCH_FAILED
- UNKNOWN

A successful feed fetch plus no matching player/threshold is NOT_MATCHED, not
the stronger and usually unprovable claim NOT_POSTED.

## 2. Observation manifests

Key commits:
- `96df9b690b6ab6ad01692dad88dcb2e4990862ec`
- `b95486db67838f866a85560e71baf006d27b86d3`

Candidate rows remain a deduplicated changelog, but every actual observation can
write an immutable snapshot manifest containing:

- snapshot id;
- observed_at;
- exact candidate count;
- candidate-universe fingerprint;
- candidate_id -> substantive content hash for every candidate;
- market-fetch context;
- code/model version identity.

This preserves the fact that the same unchanged candidate was observed at two
different times without duplicating its full payload.

Candidate substantive state is now canonicalized separately from observation
timestamps, so one immutable candidate blob can be referenced by many real
observation events.

## 3. Isolated live FanDuel attachment

Key commits:
- `205c6678dafa7cb6cb3a512c8ddf61b45902907a`
- `85fbbb0e61f5842a9607dbd300151db4b78675cd`
- `f95e9ce9ca616010eaacc9bbcf9eb9b246625344`
- `57fa8b82776ddd47e7b0e277b66bd4fffc4819c8`

The prospective pass now captures/reuses the same market families production
uses:

- generic batter props;
- pitcher strikeouts;
- pitcher outs;
- first inning;
- combined starter strikeouts.

Important mutation boundary:

- the scoring-pass candidate universe is deep-copied first;
- quality_control(), live signal weighting, and attach_market_prices() mutate
  only the research copy;
- the source candidate objects returned by the isolated scoring pass remain
  unchanged.

The copied confirmed-QC candidates receive the same live-only signal-trust
adjustment production applies after QC. Rejected/assumed candidates retain
their pre-QC score because production does not advance them through that step.

## 4. Exact snapshot reconstruction

Key commits:
- `8113033fd529f1ccaba0568d30b9c6004df441c8`
- `b8235b32891f8be406f295122448d25dc6389967`

`prospective_reporting.resolve_snapshot()` reconstructs the exact universe
named by a snapshot manifest and fails closed on:

- missing candidate state;
- duplicate/missing candidate IDs;
- candidate-universe fingerprint mismatch;
- ambiguous same-id/same-hash state.

## 5. Legitimate operational eligibility + equal-volume selector comparison

Key commits:
- `8113033fd529f1ccaba0568d30b9c6004df441c8`
- `b8235b32891f8be406f295122448d25dc6389967`

Operational eligibility currently requires:

- confirmed QC/lineup state;
- real model probability;
- matched market;
- real posted odds;
- pregame clock when observation time is supplied;
- candidate identity.

Champion Top Picks outside that population cause an integrity error rather
than being silently dropped.

One declared challenger is then forced to the champion's exact selection
volume. Reports include:

- champion vs challenger realized hit rates;
- exact overlap;
- added / removed candidates and their realized outcomes;
- market mix;
- unique game/player concentration;
- incomplete-settlement state;
- no hit-rate delta until both selections are fully settled.

## 6. Multi-slate aggregation with date-cluster uncertainty

Key commits:
- `d0d3c06f6f2ef5f399c88b8d1caac975964c787f`
- `d242fd3ee94b867b503d32f1510e39d334e988d5`

Aggregate reporting:

- preserves exact equal volume across every nonzero slate;
- refuses incomplete settlement;
- refuses multiple challenger definitions in one aggregate;
- refuses duplicate slate dates, preventing multiple snapshots of the same
  games from being treated as independent observations;
- reports slate wins/losses/ties, overlap, added/removed performance, market
  mix, and aggregate realized hit-rate delta;
- computes a deterministic bootstrap 95% interval by resampling whole slate
  dates, preserving within-slate dependence rather than pretending every prop
  is independent.

This is measurement only, not a promotion verdict.

## 7. Content-addressed prospective durability

Key commits:
- `c315a4c75f03280bf22a49c967b3768d8b4e245f`
- `012665bbf50fc6a24cfdf0754f5881019867b817`
- `591e73f188cbc05ae84c33750fc5282e72f5abbb`
- `baca86c7b5c4065ff5295b8d8ab1a5633dfc3252`

New module:
`backtest/prospective_durability.py`

Storage design:

`prospective/v1/candidates/<sha-prefix>/<content_sha>.json.gz`
`prospective/v1/snapshots/YYYY-MM-DD/<snapshot_id>.json`

Properties:

- candidate payloads are immutable/content-addressed;
- unchanged candidates are stored once even across many observations;
- gzip is deterministic;
- existing blobs are checksum-verified before reuse;
- conflicting immutable snapshot IDs fail closed;
- a durable snapshot can be reconstructed without the original JSONL spool.

## 8. Durable prospective outcomes

Key commits:
- `007a33cff4f7230de6158ffc7ad0283136a601b1`
- `15d755f08bff35fa12cb21949125bb392945c40d`
- `43fb8138d834e96157b3c17ee2b7168b216a263b`
- `be6f1f98d0bf211470a56aac8bed91635110ff61`
- `fcfcc8ba80229e6c0fad5da4f8afe93076d7a4e1`

The grader can now:

- grade an explicit record mapping;
- grade the union of every candidate identity ever observed across durable
  snapshots for a slate;
- operate without the ephemeral candidate_funnel JSONL file;
- fail closed if one candidate_id changes settlement-defining identity fields
  across observations.

Substantive grading states are stored append-only under:

`prospective/v1/outcomes/YYYY-MM-DD/<candidate-hash>/<outcome-hash>.json`

Repeated identical grading is idempotent.

Transitions such as ungraded -> miss/hit are preserved.

Contradictory final settlements for one candidate fail closed.

## 9. Manual-only capture workflow

Key commits:
- `82a9f38adb969533943f579138f6b909d67ac70c`
- `748e5de2d65e8429ab05cfb4e443c7d65ec04e11`
- `7ea50671047cb66766db5d0248d59022436e516b`

Workflow:
`.github/workflows/prospective-shadow.yml`

Important rollout boundaries:

- workflow_dispatch only;
- NO schedule;
- capture job has contents:read;
- persistence is an explicit boolean opt-in defaulting false;
- persistence targets only `prospective-candidate-ledger`;
- capture and grading use one shared non-cancelling concurrency group;
- persistence re-runs durability verification against the actual ledger
  worktree instead of blindly copying the capture artifact;
- staged git diff must be additions only before push.

At handoff-writing time, the dedicated
`prospective-candidate-ledger` branch does not exist yet. The workflow is
designed to create it on the first separately authorized persisted run.

## 10. Manual-only durable grading workflow

Key commit:
- `163ae7b340e21b6f4d0a22b05c30d0b4d717550c`

Workflow:
`.github/workflows/prospective-grade.yml`

Properties:

- manual only;
- explicit slate date;
- persistence default false;
- reads candidate evidence from the durable ledger, not ephemeral spool;
- production grade_results.grade_pick() remains the grading authority;
- persistence targets the same append-only prospective research branch;
- capture and grade writes serialize on the same concurrency group.

## 11. Pre-outcome locked selector plans

Key commits:
- `a0c8540b53a846ad992eb3accec3f67165640ebb`
- `5be226582bb142acf3feaa4e3abb63dee18cd7aa`

New module:
`backtest/prospective_selector_eval.py`

Purpose: prevent post-outcome snapshot/challenger shopping.

Locking a plan:

- requires explicit snapshot IDs;
- allows one observation per slate date;
- binds candidate-universe fingerprint and observation metadata;
- binds one challenger definition;
- fails if ANY durable outcomes already exist for a selected date;
- hashes the whole locked protocol.

Evaluation:

- reloads only those exact locked snapshots;
- verifies the plan fingerprint and bound snapshot evidence;
- joins durable outcomes;
- runs equal-volume comparison;
- aggregates by slate/date;
- deliberately emits no promotion verdict.

Current allowed simple challenger rankings are:
- edge_vs_fair
- hit_probability
- score

These are measurement probes, not promoted models.

# Workflow / activation state

No schedule has been added.

No prospective ledger branch has been created.

No real prospective capture has been persisted by this branch.

No production recommendation behavior was changed.

No public Top Pick ledger behavior was changed.

No merge or deployment has been authorized.

# Important methodological caveats

1. **Independent shadow pass != exact production-publication replay.**
   The logger runs a separate live scoring pass. Network/source state can move
   between production and shadow executions. Treat this as prospective
   full-candidate research, not proof of the exact board state customers saw.

2. **One-sided market fair values remain assumed-hold.**
   edge_vs_fair is more comparable than raw posted implied, but one-sided
   FanDuel markets still do not expose an exact opposite-side hold. Do not call
   assumed-hold fair probabilities exact.

3. **One snapshot per slate is the first anti-double-counting protocol, not
   necessarily the final operational timing design.**
   Staggered game starts may eventually justify a prospectively locked
   per-game evaluation window. Do not retrofit that rule after observing
   results.

4. **No challenger has proven accuracy superiority.**
   The code can measure challengers. It has not produced real prospective
   evidence yet.

5. **No schedule should be activated just because the workflow exists.**
   Capture cadence, compute cost, book-request load, data retention, and
   rollout permissions need explicit review.

## 12. Production-style per-market opportunity expansion + exact wager identity

Key commits:
- `e2a06650d8da13b2f12adea46065404c7a484612`
- `9d573bf6fc762a5576d775f2e3e4e54d595bec38`
- `a064fd7ef583e21839ace9a19fbf99fb612cdfd6`
- `735873c866dc60dbe69b4009c1d09c8ff6c070d8`
- `135df4d7334714da7dc3c1098601992d4b98077c`
- `cf498d84d3ea8d47ff3773ec4869c3960cfb30e9`
- `3a733e43faaecce227e4d11f5c081b7083311879`
- `e15b971558de5f7e2fb3f9f1ce9228c920486c84`

Critical parity finding:

`_build_and_score()` is not itself the full live betting-opportunity universe.
For batters it keeps one primary projection while alternate market families
remain inside `line_options`. The dashboard expands those through
`select_best_by_category(..., n_per_category=9999, min_score=0)` before the
recommendation layer.

Changes:

- the prospective research path now reuses that production expansion seam;
- confirmed + assumed-lineup candidates advance through the operational
  opportunity expansion;
- QC-rejected candidates are expanded separately only as counterfactual regret
  evidence and cannot masquerade as operational Top Picks;
- settlement/ranking provenance dropped by the compact dashboard-row builder
  (game_start, bet_side, category inputs, signal adjustment, reliability note)
  is restored from the exact source subject rather than recomputed;
- duplicate expanded wager identity fails closed;
- already-started games are removed from the point-in-time operational universe
  before recommendation analysis, matching the production pregame boundary.

Exact wager identity was also corrected:

- `candidate_id` now includes date/game/subject/stat/side/threshold/needs;
- line movement or opposite side therefore becomes a different wager and can be
  settled unambiguously;
- a separate `candidate_series_id` intentionally remains stable across line
  movement for longitudinal market analysis.

This closes a contradiction in the earlier logger design: one candidate ID
could previously span changing thresholds while the durable grader correctly
required settlement-defining fields to remain stable.

Adversarial tests cover:

- one batter expanding into multiple market families;
- settlement/provenance restoration;
- separate rejected counterfactual expansion;
- duplicate expanded identity rejection;
- line movement exact-id separation with stable series id;
- opposite-side identity separation;
- started-game exclusion;
- unknown game-start retention as evidence rather than fabricated commencement.

## SUPERCHAD bounded finish line

SUPERCHAD should STOP implementation on this branch once all of the following
are true:

1. the cumulative prospective branch is exact-head green in the repository's
   full Test Suite;
2. all new focused prospective tests pass inside that exact run;
3. cumulative diff remains limited to prospective research/evaluation,
   dedicated manual-only workflows, tests, and this audit handoff;
4. no schedule, ledger branch, production/public mutation, merge, deployment,
   or real persisted prospective run was activated;
5. Claude has a complete independent-review handoff.

At that point, additional infrastructure is methodological drift. The next work
belongs to Claude/user review and then, if separately authorized, a single
persist=false real capture proof followed by a separately authorized durable
proof. The purpose of the system from there is direct equal-operational-volume
realized-winner measurement, not more framework construction.

# Claude mandatory independent review

Before any merge or activation, Claude should:

1. Fetch this branch fresh and verify exact merge-base.
2. Inspect every cumulative diff, especially both workflow files.
3. Run the exact branch test suite to completion on GitHub and locally where
   practical.
4. Specifically execute:
   - test_candidate_funnel_logger.py
   - test_candidate_funnel_grader.py
   - test_prospective_reporting.py
   - test_prospective_durability.py
   - test_prospective_workflow_contract.py
   - test_prospective_selector_eval.py
5. Red-team the two-workflow shared-branch concurrency and first-branch
   creation path.
6. Verify the capture workflow cannot write main, docs/data.json, docs/live.json,
   public registry, production picks, or canonical durable data.
7. Verify market attachment parity against dashboard/build_dashboard.py and
   generate_picks.main(), including fetch failure semantics.
8. Run one DRY/manual capture with persist=false and inspect:
   - candidate count;
   - actual FanDuel price coverage by market;
   - snapshot manifest;
   - code/model versions;
   - QC distribution;
   - no production-file mutations.
9. Only after that, separately request authorization for one persisted
   prospective-ledger proof run.
10. For the proof run, verify durable reconstruction from a fresh checkout
    with the original spool deleted.
11. Run durable grading only after games settle and verify outcomes against
    production grade_results on a sample.
12. Do not choose or lock a challenger snapshot after outcomes exist.
13. Do not merge or schedule without explicit user authorization.

# North Star / stop condition

This branch is an **ACCURACY ENABLER moving into DIRECT ACCURACY measurement**.

Once the prospective path can reliably capture actual pregame candidates,
prices, exact observation identity, and realized outcomes, stop adding general
infrastructure and use it to answer the real question:

**At the same legitimate usable operational pick volume, does a predeclared
challenger produce more realized MLB prop winners than the champion?**

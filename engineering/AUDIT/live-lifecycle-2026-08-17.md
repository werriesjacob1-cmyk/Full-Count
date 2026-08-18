# Live lifecycle, publication, grading, and Pages delivery

- Date verified: 2026-08-17; post-merge incident addendum added 2026-08-18
- Agent: Codex (original); Claude (2026-08-18 addendum)
- Scope: Pre-Phase-V live lifecycle hardening, including the adversarial correction pass
- Status: PR #51 merged (`9275b5bdd7d955a7a2e2f149b4814dad69ec95ea`) 2026-08-17/18;
  see "Post-merge addendum" below — the merge was followed by a real
  production outage, since corrected. The line below is left as originally
  written and is no longer current; it is not evidence the rollout was clean.
- ~~Status: **remediation implemented on draft PR #51; unmerged**~~ (superseded, see addendum)
- Model/recommendation policy impact: **none**

This audit records the evidence and lifecycle contract implemented on PR #51.
It supersedes the first-pass claim that live hits were permanently terminal.
The correct invariant is result authority: a live observation is provisional
and may yield to authoritative final settlement.

## Original diagnosis

### HIGH — Repository live-state commits did not update the active Pages artifact

**CONFIRMED.** The former price and grade workflows committed repository JSON
but did not upload a Pages artifact. Refresh run `32049619252` included a
Pages deployment; later price run `32057441977` updated the repository without
one. An uploaded Pages artifact does not mutate when `docs/live.json` changes.

### HIGH — The reduced live-grading environment could not import the grader

**CONFIRMED.** `refresh_grades.py -> grade_results.py -> mlb_daily.py` eagerly
required `pybaseball`, which the reduced workflow did not install. Run
`32056821159`, job `95468790326`, failed on that chain; the 20 most recent
former grading runs inspected had failed. The new live boundary avoids that
eager import and has a subprocess import/execution regression.

### HIGH — A full rebuild removed a published Top Pick after first pitch

**CONFIRMED.** Pregame filtering correctly omitted started games, then the old
full build replaced `data.json` and cleared `live.json`. No durable exposure
source authorized carrying the exact wager forward.

### HIGH — Whole-file writers could regress independent facts

**CONFIRMED / QUALIFIED.** Three workflows wrote overlapping generated JSON
under different concurrency groups. Git push rejection prevented some silent
last-writer wins, but it did not merge independent price/grade facts, compare
result authority, or distinguish unreadable state from empty state.

### HIGH — Exact market disappearance could look freshly priced

**CONFIRMED.** The former refresher attached into a row that retained the old
quote, then freshness-stamped and reclassified it even when the exact line was
absent from a successful board.

### MEDIUM — Frontend lifecycle rendering and overlay merge were incomplete

**QUALIFIED.** Polling and live/hit/miss chips existed. Void/ungraded,
provisional authority, lifecycle card colors, recency comparison, immutable
published snapshots, and overlay reapplication after a full-board swap did not.

## Adversarial correction findings

The numbers correspond to the correction request for PR #51.

| # | Severity | Conclusion | Evidence and disposition |
|---|---|---|---|
| 1 | HIGH | **CONFIRMED** | First-pass `hit` was terminal and tests prevented a final correction. Live threshold hits now use `provisional_hit`; official Final may confirm or replace the entire settlement fact. |
| 2 | HIGH | **CONFIRMED** | The first-pass status mapper treated most non-Preview/non-Final states as live. Explicit MLB abstract/detailed mappings now distinguish pregame, live, delayed, suspended, postponed, final, cancelled, and unknown; unknown preserves prior state and blocks new bets. |
| 3 | HIGH | **CONFIRMED** | Status was fetched before slow sportsbook/build work, leaving a first-pitch race. Both price and full-build finalizers refetch state and apply scheduled `game_start` as an absolute cutoff. Deployment staging also reserves 15 minutes so a candidate cannot first become public after first pitch. |
| 4 | HIGH | **CONFIRMED** | A shared non-cancelling concurrency group did not prove retention of every pending important run. Full/lineup rebuilds now use their own non-cancelling true queue and finalize against current `main`; live updates cannot displace them. |
| 4B | MEDIUM | **QUALIFIED** | True queuing every five-minute observation would create stale backlog. Live observations deliberately retain GitHub's single-pending coalescing semantics, checkout current `main`, and make a fresh observation when they start. The workflow installs only `requests` and `mlb-statsapi`; post-merge runtime remains an operational check. |
| 5 | HIGH | **CONFIRMED** | Successful exact absence and source failure were conflated. The final hardening review also proved that a 200-OK but empty/malformed response still masqueraded as absence. General, strikeout, pitcher-outs, first-inning, and combo-K families now produce event-scoped `MATCHED`, `NOT_POSTED`, `FETCH_FAILED`, or `IN_PLAY` evidence independently; absence requires a structurally complete relevant-event observation. |
| 6 | CRITICAL | **CONFIRMED** | Canonical daily picks are overwritten and timestamp archives do not prove public exposure. A minimal deployment-proven registry now stores an immutable first-exposure snapshot. It is lifecycle infrastructure, not the future event ledger. |
| 6B | CRITICAL | **CONFIRMED** | A visually carried wager absent from final daily picks could evade official Top Pick history. Durable grading now consumes registry snapshots separately, idempotently, without changing legacy/canonical population semantics. |
| 7 | HIGH | **CONFIRMED** | First-pass visual rollover worked, but grade status lookup could still use a single payload/slate date. Public grading now requests the actual feed by `game_pk`; prior-slate games remain gradeable after UTC midnight. |
| 8 | HIGH | **CONFIRMED** | `_proven_void()` depended partly on reason text and did not encode market action requirements. `settlement_rules.py` separates action eligibility from threshold grading and fails ungraded where the official rule cannot be established. |
| 9 | HIGH | **QUALIFIED** | First pass added full combo participants, but participant order, inconsistent supplied IDs, duplicate settlement identities, and bounded migration remained open. Identity schema v2 canonicalizes commutative combo-K participants and rejects inconsistencies/duplicates. |
| 10 | HIGH | **CONFIRMED** | A single lifecycle/grade field mixed game progress and wager outcome. Recommendation, game, and settlement states are now independent canonical facts; no generated compatibility `grade` field is authoritative. |
| 11 | CRITICAL | **CONFIRMED** | Local qualification, repository commit, and successful public deployment were conflated. Only post-`deploy-pages` confirmation establishes exposure; the staged public manifest provides recoverable provenance. |
| 11B | HIGH | **QUALIFIED** | Rollout needs existing legitimate public picks, but legacy status alone is not permanent proof. A versioned one-time migration reads only the verified currently deployed artifact and its HTTP deployment provenance; arbitrary archives are excluded. |
| 12 | MEDIUM | **CONFIRMED** | Preventing rebuild reset left the overlay unbounded. Compaction is now conditional on official terminal settlement being durable and no current/suspended/postponed/recovery dependency remaining. |
| 13 | HIGH | **CONFIRMED** | New-state parsing formerly accepted naive timestamps as UTC. New lifecycle state accepts only `Z` or zero-offset `+00:00`; a bounded schema-v1/v2 migration path alone accepts legacy naive values. |
| 14 | HIGH | **CONFIRMED** | The first verifier mostly checked file existence and shallow JSON shape. It now validates schema/identity uniqueness, strict timestamps, enums, state combinations, publication proof, retention legality, hashes, and frontend overlay consumption. |

## Final high-blocker review

### HIGH — Structurally empty sportsbook responses still failed open

**CONFIRMED.** Before this pass, patch-head `odds_fanduel.list_games()` parsed
`attachments.events` and could return `[]` for `{}`, a missing/empty events
object, or no usable events. `fetch_prop_prices(strict=True)` then returned
`{}` without failing. `refresh_prices.py` cleared quote fields, called the
unchanged classifier, stamped `market_observed_at`, and labeled the result
`NOT_POSTED`. A direct reproduction returned `{}` from strict mode for an
HTTP-success `{}` root payload.

The corrected fetch boundary records root transport failure, malformed root,
structurally empty root, and usable event discovery separately. Each family
then records required-tab structural completeness per FanDuel event. An exact
parsed market is positive `MATCHED` evidence even if another tab failed, but
`NOT_POSTED` requires exactly one relevant event (matched by scheduled UTC
start and/or normalized matchup) with every required family tab structurally
valid. Otherwise the row is `FETCH_FAILED`; prior quote, recommendation, and
successful observation timestamp remain intact.

### HIGH — Lineup state could acknowledge a change before rebuild dispatch

**CONFIRMED.** `lineup-watch.yml` committed
`dashboard/lineup_watch_state.json` before invoking
`gh workflow run dashboard-refresh.yml --ref main`. A successful state push
followed by a failed dispatch made the next poll see no change and therefore
lose the important lineup rebuild.

The workflow now dispatches first and only acknowledges state after the
dispatch step succeeds. Dispatch rejection/failure leaves remote state old so
the next poll retries. Dispatch success followed by state-push failure can
enqueue a duplicate on the next poll; that is intentionally safe at-least-once
behavior because full rebuilds are idempotent, use `queue: max`, and finalize
against current `main`.

### MEDIUM — A new public Top Pick could lack a structured settlement path

**CONFIRMED / QUALIFIED.** `grade_results.py` can calculate singles, doubles,
and triples, and FanDuel prices them, but `settlement_rules.py` has no verified
cross-jurisdiction action rule for those exact markets. Their current
probability floors make public exposure unlikely, so immediate production
frequency is bounded; the integrity defect remains real.

Pages publication now requires `supports_public_settlement()` for a new Top
Pick. Unsupported local Top Picks are omitted only from the staged public
artifact; the classifier and source board are unchanged. A bounded rollout
still records any unsupported Top Pick proven to have already been public,
because historical exposure must not be erased; it remains ungraded. The Pages
verifier rejects a new unproven Top Pick or candidate without this capability.

## Canonical lifecycle contract

Three independent concepts are persisted:

| Concept | Values |
|---|---|
| Recommendation | `top_pick`, `lean`, `value`, `neutral` |
| Game | `pregame`, `live`, `delayed`, `suspended`, `postponed`, `final`, `cancelled`, `unknown` |
| Settlement | `open`, `provisional_hit`, `hit`, `miss`, `void`, `ungraded` |

Result authority is strictly:

1. `none`
2. `live_observation`
3. `official_final`

Settlement state, authority, actual, reason, source, and observation timestamp
are one atomic fact. An older or lower-authority observation cannot partially
replace it. Equal-time conflicting incoming state loses to already-persisted
current-main state. Repeating an identical final settlement is idempotent.

The game-state parser maps only supported MLB abstract/detailed states.
Delayed before play is not live. Suspended preserves the last legitimate
settlement. Postponed/cancelled never imply sportsbook void by themselves.
Unknown source state preserves last-known-good state and blocks a new wager.

## First-pitch and immutable recommendation boundary

Scheduled `game_start` is an absolute conservative cutoff even when MLB still
reports Preview. New Top Pick publication requires explicit pregame state,
current UTC before scheduled start, and a final status refetch at the last
writer boundary. The Pages artifact admits a new candidate only when start is
at least 15 minutes after preparation, while the deploy job has a 10-minute
timeout. A candidate deployed at/after first pitch cannot be confirmed.

After the boundary, only game and settlement facts advance. The registry's
immutable snapshot preserves the exact wager identity, displayed definition,
publication probability/price/implied probability/edge, recommendation status
and reasons, available versions, and available board/prediction timestamps.
No missing version value is fabricated. Live repricing cannot alter that
snapshot. A never-public started prop cannot appear as a new public bet.

## Public exposure and ownership

`published_top_pick_at` means the first successful Pages deployment containing
the exact Top Pick. Qualification and repository persistence are not exposure.

Ownership is:

- full-build workflow: only complete `docs/data.json` owner;
- consolidated live workflow: only `docs/live.json` owner;
- Pages deploy workflow: stages/verifies/deploys the exact artifact and is the
  only first-exposure confirmer;
- `data/public_top_picks/registry.json`: authoritative public population;
- intraday grader: provisional/live display settlement;
- daily grader: durable registry-backed public history;
- merge/finalization tools: compaction only after durable proof.

The deployment manifest records artifact ID, hashes for staged data/live,
source commit, prepared time, cutoff, candidate immutable snapshots, and known
publication proofs. Confirmation adds workflow/deployment IDs and URL when
GitHub exposes them. A failed deploy creates no public entry. If deployment
succeeds but the registry push fails, the publicly deployed manifest and its
HTTP `Last-Modified` allow an idempotent next-run recovery. The registry-only
commit does not trigger deployment because deployment listens to completed
dashboard state-owner workflows, not arbitrary pushes.

## Odds observation semantics

| Observation | Meaning | Action |
|---|---|---|
| `MATCHED` | Relevant family succeeded and exact market exists | Replace quote and advance that family's successful observation time. |
| `NOT_POSTED` | A unique relevant event was discovered, every required family tab was structurally valid, and the exact market is absent | Clear current bettable quote and fail closed through unchanged policy. |
| `FETCH_FAILED` | Root transport/structure/discovery is indeterminate, a required relevant-event tab failed/malformed, or event identity is ambiguous | Preserve prior quote; record failure separately; do not freshness-stamp or reclassify because of false absence. |
| `IN_PLAY` | Wager boundary crossed | Freeze published snapshot; do not reprice or reclassify. |

Families and events are independent: a successful general board or unrelated
game cannot make a failed strikeout, pitcher-outs, first-inning, combo-K, or
other-game quote fresh. When there are no relevant pregame rows (including an
all-in-play slate), the refresher does not manufacture a source failure.

## FanDuel settlement eligibility

Verified 2026-08-17 against current official FanDuel sportsbook rules:

- Illinois: https://www.fanduel.com/fanduel-sportsbook-house-rules-il
- Pennsylvania: https://www.fanduel.com/fanduel-sportsbook-house-rules-pa
- Tennessee: https://www.fanduel.com/fanduel-sportsbook-house-rules-tn

The Tennessee page was current with an effective date of 2026-07-30 when
verified. It differs materially from Illinois/Pennsylvania for core batter
props and H+R+RBI. The implementation does not select Tennessee semantics;
there is no configured Full Count jurisdiction.

The repository does not configure an operating jurisdiction, so the
implementation settles only cases where the inspected rules agree and the
official MLB feed proves the requirement:

- hits: Illinois/Tennessee require a start while Pennsylvania requires a plate
  appearance. Start+PA is eligible and neither is void; the two mixed cases
  are jurisdiction-dependent and remain ungraded;
- home run: Illinois/Tennessee require a start while Pennsylvania requires
  start+PA. Start+PA is eligible, non-starters are unanimously void, and
  starter-without-PA remains ungraded;
- total bases, runs, RBIs, stolen bases: Illinois/Pennsylvania allow a start or
  PA; Tennessee requires a start. A starter is eligible, neither is void, and
  a non-starting pinch hitter with a PA remains ungraded;
- hits+runs+RBIs: Illinois/Pennsylvania require a PA; Tennessee requires a
  start. Both is eligible, neither is void, and either mixed case remains
  ungraded;
- pitcher strikeouts/outs: listed pitcher must start; later relief work does
  not make a non-starting listed pitcher eligible;
- combined starter strikeouts: both listed pitchers must start;
- NRFI/YRFI: an official first-inning result must be present;
- postponed, cancelled, or suspended status alone: ungraded pending verified
  sportsbook settlement;
- shortened final: settle only an unequivocally determined result; otherwise
  ungraded. Scheduled seven-inning games use their official scheduled length;
- singles/doubles/triples and unsupported specialty-market action rules:
  ungraded, never invented, and ineligible for first official Top Pick
  publication until a structured rule exists.

FanDuel recognizes MLB's official result and applicable stat corrections.
Therefore early threshold hits remain provisional until official Final.

## Durable grading and retention

Public Top Picks are graded from immutable registry snapshots even when absent
from the later canonical picks file. They are keyed by canonical ID, written
once logically, retryable while ungraded, and correctable in place by a later
official result. Canonical and registry copies never double-count. Voids do not
enter the public hit/miss denominator. Pre-rollout dates are not guessed.

Live overlay compaction may remove an old entry only when all are true:

- official terminal hit/miss/void is present;
- the same or newer settlement is durable in public history;
- the entry is not needed by the current board;
- it is not unresolved, provisional, suspended, or postponed;
- no publication/recovery persistence depends on it.

Compaction is atomic and idempotent. UTC date change alone never prunes state.

## Workflow and failure-safety contract

- Full/lineup builds use the separate non-cancelling full-rebuild queue and
  always finalize against current `main` after any queue/build delay.
- Lineup changes are dispatched into that queue before watcher state is
  acknowledged. Failure before dispatch is retryable; a duplicate after
  dispatch-but-before-ack is safe.
- Five-minute live observations coalesce obsolete pending runs, checkout
  current `main`, and perform a current observation rather than replaying an
  old dispatch snapshot.
- Push retries fetch current `main` and semantically merge facts. Grade then
  price, price then grade, stale writers, provisional/final, and rebuild/live
  overlaps preserve the newest authoritative independent fields.
- JSON reads fail closed. Corrupt persistent state is never converted to empty
  state. Writes use a temp file, flush/fsync, and `os.replace`.
- Total MLB status failure preserves prior recommendation/game/settlement and
  logs the source failure; it blocks a new wager.
- Deployment verifies staged state before upload. Invalid staging cannot
  replace the last successfully deployed Pages artifact.

## Artifact validation contract

`verify_pages_artifact.py` requires all static/data/live/manifest files,
lifecycle schema 3, identity schema 2, a props list and live-delta object,
nonempty unique canonical IDs, unique settlement identities, strict UTC,
supported enums, internally consistent result authority, legal publication
proof, valid candidate tokens, prospective Top Pick settlement capability,
legal retained orphans, exact manifest hashes, and a frontend that polls and
consumes the separated live overlay. Corrupt, duplicate, impossible,
unsupported/unproven Top Pick, stale-authority, or partially proven state fails
deployment.

## Post-merge operational checklist

1. Observe or trigger the first Dashboard Live Update.
2. Verify it checks out current `main` at runtime.
3. Verify live state writes successfully.
4. Verify Dashboard Pages Deploy follows.
5. Verify deploy checks out newest `main`.
6. Verify the deployed artifact contains expected data/live state.
7. Compare public `live.json` with the deployed manifest/hash.
8. Confirm the frontend fetches and applies it.
9. Observe one public Top Pick cross first pitch.
10. Confirm its immutable recommendation snapshot freezes.
11. Confirm it becomes live/yellow.
12. Observe a mathematically definitive live over hit if available.
13. Confirm it becomes green as `provisional_hit`.
14. Confirm Final verifies or corrects it.
15. Observe a full rebuild while a public pick is live.
16. Confirm that pick survives and no new started prop appears.
17. Confirm exposure is recorded exactly once after successful deployment.
18. Confirm durable history retains it if a later board omits it.
19. Verify a lineup rebuild is not displaced.
20. Verify stale live observations do not create a backlog or regress state.
21. Verify independent price/grade facts survive retries.
22. Deliberately observe a failed deployment leaving last-good Pages intact.
23. Confirm no registry-commit deployment loop occurs.
24. Confirm compaction retains every unresolved/provisional/suspended item.
25. Record actual live/deploy durations against the five-minute cadence and
    revisit cadence only if execution approaches overlap persistently.
26. Exercise or observe a structurally empty/malformed FanDuel family response
    and confirm the last quote/timestamp/recommendation remain intact.
27. Exercise a failed lineup rebuild dispatch and confirm the next watcher poll
    retries without having acknowledged the changed lineup.

## Remaining limitations

- Scheduled workflows and a real public deployment cannot execute from this
  unmerged branch; every operational checklist item remains required.
- This is not the future full recommendation-event ledger.
- Early unders deliberately wait for Final. The shortened-game conservative
  rule can still leave a sportsbook-settled wager ungraded until exact
  settlement is independently known.
- The repository has no configured FanDuel jurisdiction. Rules that differ by
  jurisdiction or are unavailable for a specialty market fail ungraded.
- Durable correction polling is automatic for a bounded recent window; an
  older correction can be rerun explicitly by date.
- One workflow already executing under a deleted pre-rollout definition may
  finish once during rollout.

## Post-merge addendum (2026-08-18, Claude)

PR #51 merged at `9275b5bdd7d955a7a2e2f149b4814dad69ec95ea` (reviewed head
`87db8cd7a340caf6dfeb0d431746f437ee40f4a3`), with a passing post-merge CI run
(`32088820525`). The rollout was **not** clean: shortly after merge, real
`docs/live.json` content accumulated an id — `824077-686930-strikeouts-4` —
for a game/prop no longer on any board this repository could reconstruct.
`prepare_pages_artifact.normalize_live()` unconditionally raised on any such
unmappable id, with no distinction between stale reproducible content and
durable settlement/publication facts. Every production caller of that
function shared it, so `dashboard-live.yml`, `dashboard-refresh.yml`, and
`dashboard-deploy.yml` all began failing. The public site was stuck roughly
17 hours stale, publicly showing 0 Top Picks, while a real rebuild run during
investigation independently computed 3 legitimate Top Picks and 53 Value
picks that never reached `docs/data.json` or Pages. This is a real gap in
the checklist above item 20 ("verify stale live observations do not create a
backlog or regress state") — the prior verification did not anticipate an
orphaned, fully-stale legacy id bricking normalization outright, as opposed
to merely regressing state. Checklist item 20 should be considered
re-opened until the canary rollout below is executed and observed.

The correction (branch `pre-phase-v/live-artifact-orphan-migration-fix`,
scoped strictly to lifecycle publication restoration, no
`recommendation.py`/model/calibration change) added
`carries_durable_state()` to `dashboard/live_state.py` and bounded
`normalize_live()`'s leniency to genuinely legacy-schema input carrying no
`SETTLEMENT_FIELDS`/`PUBLICATION_FIELDS` content — mirroring the
legacy/current-schema boundary `normalize_payload()` already enforced. An
orphan carrying durable settlement or publication content, or any orphan in
a current-schema document, still fails closed exactly as before. Full detail,
regression evidence, and remaining required post-merge proof are recorded in
`engineering/ENGINEERING_HANDOFF.md`'s 2026-08-18 entry; that account is the
authoritative record of this incident and should be read alongside this
addendum. The sequential canary rollout (canary live-writer run, confirmed
Pages deploy, full `dashboard-refresh.yml`, confirmed deploy, live writer
again, independent repo + public-artifact verification) remains required and
had not been executed as of this addendum, pending explicit merge
authorization for the correction above.

**Update, same day, post-merge:** the correction merged (`5916e3549af1bc09
6dd5b80107ec1e2f18c9ccf8`) and the full sequential rollout above was executed
and independently verified against both repository state and the live public
Pages site at every step -- see `engineering/ENGINEERING_HANDOFF.md`'s
"sequential post-merge incident-recovery rollout" entry for the run ids,
timestamps, and independent-fetch evidence. Checklist item 20 is CLOSED for
the specific orphan-migration failure mode that reopened it (stale/orphaned
legacy live observations no longer brick normalization or regress state);
it remains open in the broader ordinary-staleness sense the original PR #51
audit intended. Six real Top Picks were published during the rollout with
correct provenance -- genuine, non-manufactured lifecycle evidence that
"pipeline repaired and publishing again" is CONFIRMED. This is a narrower
claim than "every PR #51 lifecycle invariant has been observed on a real
public Top Pick," which remains NOT YET PROVEN pending real game progression
(survival across first pitch, live yellow, provisional hit, official-final
confirmation, durable next-day grading). One naturally scheduled (non-manual)
`dashboard-live.yml` tick had not yet fired as of this update despite the
5-minute cron -- recorded as a pending operational observation rather than
assumed proven, consistent with GitHub Actions' own known scheduling latency
rather than a defect in this correction.

Phase V has **not** begun.

# Mission 1.1 — pre-implementation independent findings

Three read-only lanes, driven by the real `fc-*` role definitions read out of
`tooling/superclaude-activation-01` (the `fc-*` runtime itself is not active;
see `00_PREFLIGHT.md`). None had write access to the writer branch.

Verdicts as returned:

| lane | role | verdict |
|------|------|---------|
| A | prospective ledger auditor | **DEFECTS FOUND** — 12 |
| B | methodology red team | **DOES NOT SURVIVE** — 4 decisive |
| C | live/workflow SRE | integration spec delivered + 1 pre-existing production bug found |

Lanes A and B ran independently and **converged on the same decisive defect**
(champion definition) by different routes. That agreement is the single most
important result of this pass.

---

## THE DECISIVE FINDING — the champion arm is not what the site exposed

`prospective_selection.champion_hits_picks()` reads `manifest["candidates"]`.
That list is built by `dashboard/publication_registry.py:250-251`, whose FIRST
filter is:

```python
if prop_id in registry["entries"]:
    continue
```

`registry["entries"]` is a **permanent, cumulative, cross-date** store — 108
entries spanning 2026-08-18 → 2026-09-01. So `candidates` is the set of props
achieving **first public exposure at that exact artifact**, not the set the
artifact displays.

Protocol §7 then mandates the **latest** converged refresh deployment as
decisive. Latest artifact ⇒ almost everything already registered ⇒ the champion
list is near-empty.

**Empirically confirmed against real committed state.** `docs/data.json`
(2026-09-01, `generated_at 20:30:24Z`) publicly displays 2 Hits Top Picks. Both
are already in `registry.json` (first published 20:08:03 and 20:27:45). The
manifest would therefore emit **zero** Hits candidates, `N(date) == 0`, and
`build_epoch_selection()` returns `None` — while the site showed two Hits Top
Picks all evening.

Across real dates the Hits Top Picks of a single day are spread over 10–12
distinct publishing artifacts:

```
2026-09-01  hits=2  distinct publishing artifacts=10
2026-08-30  hits=5  distinct publishing artifacts=11
2026-08-29  hits=4  distinct publishing artifacts=12
2026-08-25  hits=5  distinct publishing artifacts=10
```

Consequences: unequal opportunity set at nominally equal volume (PA-v1 ranks the
whole gated pool; the champion is confined to first-exposure residue), a
denominator that collapses for reasons unrelated to pick quality, and a
non-random missing-epoch bias. All silent — `resolve_champions()` raises only
for a champion *in the manifest* missing from the universe; a published champion
absent from the manifest never enters and never trips anything.

Worse: `dashboard-deploy.yml` triggers on **both** Dashboard Refresh and
Dashboard Live Update. A Hits Top Pick first published by a live-update deploy
is registered and can then **never** appear in any later refresh manifest —
permanently invisible to the champion arm while remaining a real public wager
and remaining fully selectable by PA-v1.

**Resolution adopted:** the champion set stops being `manifest["candidates"]`.
It becomes every row in the decisive artifact's served `data.json` with
`recommendation_status == "top_pick"`, `stat == "hits"`, pregame, and
`before_betting_cutoff(row, publication_cutoff_at)` — i.e. the manifest's own
gates **minus** the first-exposure registry filter. That filter is lifecycle
bookkeeping, not an exposure predicate.

---

## Lane A — prospective ledger auditor: DEFECTS FOUND (12)

| # | defect | status |
|---|--------|--------|
| A1 | **No state machine.** `build_epoch_selection` has NO caller anywhere, tests included. `build_receipt`, `select_decisive_epoch`, `regate_pool`, `settle` are called only from tests. The only runtime entry into the whole system is `build_dashboard.py` → `capture()`. No prospective module has a `__main__`/`main()`/argparse. No workflow references the shadow at all. | confirmed |
| A2 | **24 receipt fields destroyed at process exit.** `build_snapshot()` emits 17 keys; `build_receipt()` reads 28 off `row`, 6 off `verdict`, 6 off `meta`. AST-extracted and verified by live reconstruction. **`stat` resolves to `None`, which breaks settlement** (`reconstruct_pick` feeds it to `grade_public_pick`, which dispatches on it) — and the value physically exists in the snapshot under `expression["stat"]` but `build_receipt` reads `row["projection"]["stat"]` and cannot reach it. Receipt content SHA is therefore not reproducible. | confirmed |
| A3 | **Nothing is persisted in production.** `FULLCOUNT_SHADOW_PERSIST` is set in no workflow, script or env file. Every production capture takes the `if not persist:` branch. The only trace is a stdout line in an Actions log with expiring retention — exactly what §10 forbids as a sole evidence copy. | confirmed |
| A4 | **The ledger cannot represent the two-stage lifecycle.** `bind_deployment` sets `decisive_epoch_id = candidate["epoch_candidate_id"]`, so stage 1 and stage 2 collide on `(event_type, idempotent_key)`. Demonstrated empirically: the stage-2 append is refused. The ledger is behaving correctly; capture spent the key on a non-event. `EVENT_TYPES` also has no member for a regate drop, an `EpochFailedClosed`, or a `NoPrimaryEpoch` date, so §12's required missing-epoch reasons have nowhere to live. | confirmed |
| A5 | **`EVENT_EPOCH_BOUND` written on an unbound candidate** whose body carries `publicly_converged: False` and no `decisive_epoch_id`. | confirmed |
| A6 | **`select_decisive_epoch` has no input source.** Nothing anywhere produces a deployment record. The convergence proof in `dashboard-deploy.yml` is an inline heredoc that prints and exits, persisting nothing. | confirmed |
| A7 | Champion arm — see THE DECISIVE FINDING. Plus: **the decisive manifest is not retained.** `publication_manifest.json` is copied to `$RUNNER_TEMP` and uploaded into the Pages artifact; never committed; each deploy overwrites the public copy. | confirmed |
| A8 | **Version provenance is populated then thrown away.** `build_metadata()` returns real values (`model_version 2026.08.15`, `git_sha 41369064`, …) and `build_dashboard.py` correctly passes them into `capture(board_metadata=…)` — but `capture()` never reads the parameter. Also `build_receipt` ignores `meta["git_sha"]` and calls `git_sha()`, which in a later job would stamp the **closure job's** HEAD. `lineups_observed_at` is never passed and is always `None`. | confirmed |
| A9 | **Funnel gaps.** Eligible rows carry no positive gate trace (only rejected rows carry `failed_gates`). `regate_pool`'s drops are computed and discarded. Field-name mismatch: `rank_pa_v1` reads `row["hit_probability"]`, the snapshot stores `champion_probability`; `_pool_index` needs `(row, verdict)` tuples, the snapshot stores flat dicts. Every downstream function needs an adapter that does not exist. | confirmed |
| A10 | **§12 reporting does not exist.** Grep for `overlap` / `pa_only` / `champion_only` across `backtest/prospective_*.py` returns zero. `backtest/prospective_reporting.py` is NOT this reporter — it is the 2026-08-25 candidate-funnel track and wiring it in would be an estate blend. | confirmed |
| A11 | **No late-information path exists today** — no prospective module opens `docs/data.json` or any board file, and the estates are not blended. But A2 creates the pressure: the only way to fill 24 lost fields is to re-open a later board. The fix must widen the snapshot, never add a reader. | confirmed |
| A12 | Mission 1 report's `VERIFIED-RUNTIME` labels on steps D/F/G/H are unsupportable — those functions never executed outside a test. | accepted |

**Verified sound, do not churn:** all 15 gates including the absent-is-not-False
lineup trap and the strict `<` cutoff; `assert_no_outcome` and its exemptions;
ledger crash-safety; `receipt_id` keyed on (epoch, prop) not arm; the PA-v1
artifact hash pin; `select_decisive_epoch`'s outcome-blindness.

---

## Lane B — methodology red team: DOES NOT SURVIVE

Four independent structural defects, any one sufficient.

- **B1** = the decisive finding above.
- **B2** — every safeguard downstream of capture is an uncalled helper.
  Specifically: **`regate_pool()` — presented in the Mission 1 report §5.3 as the
  fix for the two-clock asymmetry — has no caller, and `build_epoch_selection()`
  does not call it.** So even on the intended path the capture-time pool is what
  PA-v1 would rank over: the strictly-larger opportunity set the module's own
  docstring warns about.
- **B3** — the sealed snapshot cannot produce a §9 receipt, so any receipt is
  necessarily a post-outcome reconstruction; and `evaluate_row(row, *, now, …)`
  takes `now` from the caller with no binding to the epoch. `now` drives three
  gates. Moving `now` moves the pool; moving the pool moves PA-v1's top-N.
  **Nothing pins `now` to `board_generated_at` or `deployment_prepared_at`.**
- **B4** — `bind_deployment()` can never bind a real deployment.
  `build_source_commit` is the refresh run's `GITHUB_SHA` (main HEAD at trigger);
  `deployment.source_commit` is main HEAD at deploy checkout, several commits
  later, because Dashboard Refresh itself pushes a commit in between. Guaranteed
  mismatch ⇒ `NoPrimaryEpoch` on every date. Confirmed independently by Lane C
  against real registry data: all 12 recorded deployments carry a
  `Dashboard live update` head commit.

Attack-list results worth carrying forward:

- **Exact N — SURVIVES structurally.** Per-epoch equality raises; no global budget.
- **Two clocks — FAILS** (uncalled `regate_pool`).
- **Publication timing — challenger advantage.** The cutoff itself is sound
  (900 s lead, 10-min job timeout, bounded poll). But because the champion arm is
  first-exposure-only, it is by construction composed of the props with the
  **shortest public notice on the slate**, while PA-v1 selects freely from props
  public for hours.
- **Missing-epoch bias — FAILS.** The "latest refresh" rule lands on the 23:00
  UTC build, by which time most of the slate has commenced and the pool is
  empty. Surviving evidence would be restricted to late West-Coast games. **My
  Mission 1 report claimed §7 "already selects for this"; that was asserted,
  unverified, and wrong in direction** — latest maximises lineup confirmation
  *and* commencement, and the second effect dominates.
- **Denominators — SURVIVES.** Decided-only hit rate, selected-denominator void
  and ungraded rates.
- **Clustering — WEAKENED.** Right primary unit (date), but `SECONDARY_UNITS` is
  declared and **computed nowhere**; and **player is a crossed cluster the date
  bootstrap absorbs none of** — PA-v1 repeatedly selects the same top-of-order
  hitters, so its effective independent sample is smaller and the understatement
  is asymmetric in the challenger's favour. At §13's 30-date floor a percentile
  cluster bootstrap is anti-conservative.
- **Optional stopping — FAILS.** §13 sets a floor but no ceiling, no
  alpha-spending rule and no preregistered analysis schedule, while §12 requires
  a CI at every checkpoint.
- **Seed — SURVIVES at the API** (no seed/replicate/CI argument exists), but
  `prospective_bootstrap.py` is **not pinned by hash** anywhere, unlike the PA-v1
  artifact. A one-line edit would be undetectable in the evidence record.
- **Outcome leakage — one gap.** `make_event()` applies `assert_no_outcome` only
  to `EVENT_PREGAME_RECEIPT`. The one event type capture actually writes is not
  checked.
- **Evidence-regime separation — SURVIVES.** No pooling found.

### Escalated question, resolved: champion-public-but-ineligible

`resolve_champions()` currently drops a published champion that fails a
policy-independent gate from `N`, with **no backfill**, while PA-v1 still takes
its own best `N−1`. The red team's finding: this is asymmetric replacement, and
the deletion is **caller-controlled** through two unpinned inputs
(`source_integrity_holds`, injectable and empty by default; and `now`). That is a
post-outcome lever pointed at the champion's measured set.

**Adopted resolution (red team's primary recommendation):** change the treatment
from *drop it from N* to **fail the epoch closed**, exactly as an unresolvable
champion already does. A pick the site exposed for public wagering, which the
shadow's own usability gates say a human could not have placed, is a
contradiction between two claims that both purport to describe operational
usability. Silently deleting it resolves the contradiction in the direction that
shrinks the champion, while the experimenter holds a lever over which picks
disappear. Failing closed makes it visible and un-exploitable, and MISSING
EVIDENCE is already the protocol's accepted answer for a date that cannot
produce a sound comparison.

Required under either choice, and adopted: `source_integrity_holds` becomes a
preregistered, timestamped, append-only state computed **before first pitch**
from existing durable signals; and `now` is pinned to the epoch's own clock
rather than accepted from the caller.

---

## Lane C — live/workflow SRE: integration specification

**The hook.** `.github/workflows/dashboard-deploy.yml`, job `deploy`, a new step
placed **after** `Confirm durable public exposure`, i.e. last in the job, with
`continue-on-error: true`. Because `Verify public site converges…` has no
`continue-on-error`, **any later step running at all is itself proof convergence
succeeded** — no new detection logic is needed. It must not be placed earlier:
research code upstream of the registry write could cost a real Top Pick's
exposure record inside a 10-minute job timeout.

**Convergence is not available as an output.** That step has no `id:` and writes
nothing to `$GITHUB_OUTPUT`. Both edits (adding `id: converge` + emitting
outputs, and the new final step) **touch the live publication workflow and
require Jacob's authorization.**

**Convergence is not durably recorded anywhere.** Grep-verified. The only durable
side effect is a registry entry, and it is lossy: written only when the deploy
admitted ≥1 genuinely new Top Pick (12 distinct deployments across ~2 weeks
against dozens of deploys per day), and it records no triggering workflow name,
no public `generated_at`, and no convergence timestamp. **A post-hoc binder is
therefore impossible with today's artifacts.**

**Timing.** Real measured refresh-push → exposure-commit deltas on 2026-09-01:
3m25s, 5m00s, 5m57s, 6m10s, and one 20m48s. Refresh *start* → convergence is
that plus a multi-minute build: **8–15 min typical, 25–35 min tail.** With a
15-minute publication lead this puts ~25 minutes of first-pitch proximity
between "captured" and "publishable" — a materially different cohort, which is
exactly why the re-gate is not optional.

**Cadence.** The capture tap fires **~20–30× per slate date**, not 8×: `reconcile.py`
dispatches `dashboard-refresh.yml` from `dashboard-live.yml` every 5 minutes, and
`lineup-watch.yml` and `mlb-daily.yml` also dispatch it.

**Concurrency risk to §7.** `dashboard-deploy.yml`'s single concurrency group is
shared with the ~288 live-update-triggered deploys/day, and with
`cancel-in-progress: false` a *pending* run is cancelled by a newer one. A
refresh-originated deploy sitting pending is therefore likely to be cancelled by
the next live-update deploy. Whether refresh-originated deployments converge
often enough is answerable **only from the Actions run history**, and should be
queried before enabling persistence. If they are rare, the honest outcome is many
NO PRIMARY EPOCH dates — not a relaxed rule.

**Source-health inventory.** Delivered as a 12-row mapping to CLEAR/HOLD/UNKNOWN
over signals that are already durably written by existing sole writers. Adopted
in `07_SOURCE_INTEGRITY_CONTRACT.md`. Explicitly flagged as WRONG to treat as a
HOLD: FanGraphs 403 (happens on essentially every real run with documented
graceful degradation — holding on it would zero the experiment while looking
rigorous), any optional enrichment failure, `lineup-watch` not having run (9% of
declared cadence; its silence proves nothing), and `NOT_POSTED`/`LINE_MOVED`
(real, successful observations).

**Ledger concurrency.** The current retry loop never re-fetches, so a
non-fast-forward rejection is rejected identically on every attempt; after 4
attempts it returns `{"committed": True, "pushed": False}` — a silent local-only
ledger on a container about to be destroyed. At least two, plausibly three,
independent Actions concurrency groups can push to the research branch
simultaneously. Full fetch-replay-push specification adopted in
`02_EVENT_ONTOLOGY_AND_CONCURRENCY.md`.

**Ledger size.** One file, rewritten whole on every append, with each
`epoch_bound` body embedding the entire snapshot: ~160 KB per capture × 20–30
captures/day ≈ 3–5 MB/slate-date, ~100 MB over the §13 minimum. Partition by
slate date.

### Pre-existing production bug found (NOT this mission's, reported separately)

`dashboard/confirm_publication.py` appends a hash-chained event per new registry
entry to `data/prediction_ledger/events.jsonl`, but
`.github/workflows/dashboard-deploy.yml:173` stages **only** `registry.json`, and
the next retry's `git checkout --detach origin/main` discards the rest.

**Proof:** `registry.json` has 108 entries; `events.jsonl` has 6 events, and
`git log -- data/prediction_ledger/events.jsonl` shows only two development
commits and **not one workflow-produced commit**, while `registry.json` has a
long run of `Record deployed Top Pick exposure` commits.

The immutable Prediction Ledger has silently recorded nothing in production. The
fix is one line, in the live publication workflow. **Not made here** — it is
outside this mission's scope, requires deploy authority, and deserves its own
change on its own merit. Raised in the final report for Jacob.

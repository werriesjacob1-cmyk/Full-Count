# Mission 1 — Prospective Hits PA-v1 shadow path: completion report

Branch `claude/prospective-hits-pa-shadow-v1-01`, HEAD `c705dd7e`.
Protocol sha256 `5ce1ae95c4d3034d7948eb0ad7bc2441efcf2cabb234944e36bc315b2b355de7` (verified in-worktree).

**Note on format.** The exact section headings you specified were lost with a
container reclamation and were not recoverable from the repository. Rather than
guess at them, this report is structured against the locked protocol's own §12
reporting requirements, mapped to mission steps A–K. Everything you asked to be
reported is here; the ordering is mine.

Evidence labels: **VERIFIED-RUNTIME** = executed and observed here.
**VERIFIED-REPO** = read at the cited line. **OPEN GAP** = not satisfied.

---

## 1. Standing constraints — all honored

No merge. No deploy. No PA promotion. No HR execution. No public-ledger
mutation. No production recommendation policy changed. No prospective outcome
used for fitting. The 2026 HR holdout was not opened.

`data/public_top_picks/registry.json` was not written. The shadow ledger is a
separate orphan research branch (`research-ledger/prospective-hits-pa-v1`).

---

## 2. What was built (steps A–K)

| step | artifact | state |
|------|----------|-------|
| A | `backtest/pa_v1_fit.py`, frozen artifact | complete (prior turn) |
| B | `backtest/prospective_capture.py` + tap in `build_dashboard.py` | VERIFIED-RUNTIME |
| C | `engineering/PROSPECTIVE_LIVE_UNIVERSE_AUDIT.md` (rewritten) | complete |
| C | `backtest/prospective_eligibility.py` — 15 gates | VERIFIED-RUNTIME |
| D | `backtest/prospective_receipt.py` | VERIFIED-RUNTIME |
| E | `backtest/prospective_ledger.py` | VERIFIED-RUNTIME |
| F | `backtest/prospective_epoch.py` | VERIFIED-RUNTIME |
| G | `backtest/prospective_selection.py` | VERIFIED-RUNTIME |
| H | `backtest/prospective_settlement.py` | VERIFIED-RUNTIME |
| I | pregame integrity — gates 5–8 | VERIFIED-RUNTIME |
| J | `backtest/prospective_bootstrap.py` + 268 checks | VERIFIED-RUNTIME |
| K | two real live builds | VERIFIED-RUNTIME |

3,315 lines added across 15 files. 268 new checks in 5 test files, all passing.

---

## 3. The A-artifact binding

The shadow scores only against the authoritative freeze:

- `scientific_content_sha256` `a4f598bd4138305d8da4d85767eb873781b10e918dd1e402d536d9cd13fadf4a`
- `serialized_file_sha256` `112517321e562ee25f46140cf8ce52e2ef48b40447235cf9b22e50dec9870750`
- `effective_from` `2026-09-02T00:00:00+00:00`

`load_artifact()` verifies both that the artifact recomputes its own hash (it
has not been edited) and that the hash equals the pinned value (it is not some
other fit). Either failure aborts capture. A shadow scored by an unknown model
is worse than no shadow.

---

## 4. RAW CAPTURE SOURCE vs PRIMARY ELIGIBLE POOL — the 1C correction

`by_category_full["hits"]` is the raw capture source, **not** the eligible pool.
It is built with `n_per_category=9999, min_score=0` to satisfy a verbatim
product requirement that every market always renders something, so it contains
assumed-lineup rows, unpriced rows, reliability C/D rows and zero-evidence rows
by design.

Eligibility is earned against 15 predeclared, policy-independent gates. No
`predicted_prob >= 0.60` and no ROI/value gate: both are conditional on the
champion's own probability and would hand the champion its own selection rule
as the challenger's entry ticket.

---

## 5. Three real defects found and fixed

**5.1 The absent-is-not-False trap.** `quality_control()` sets
`lineup_assumed = True` on assumed rows and leaves the key unset on confirmed
ones — it never writes `False` (`generate_picks.py:6993`, VERIFIED-REPO).
Transcribing the protocol's prose literally as `== False` compares
`None == False` and **rejects every confirmed candidate**, emptying the pool
while looking like a correct reading. Four cases locked by test.

**5.2 Publication cutoff strictness and clock — found in my own step-C gate.**
Production is `prepared_at + 900s < game_start`, STRICT, evaluated at Pages
artifact preparation. My first gate used `now <= game_start - 900` at build
time: inclusive where production is strict, and against an earlier clock. Both
errors were more permissive, and both favoured the challenger.
`admits_new_top_pick()` now mirrors production exactly.

**5.3 The capture/preparation asymmetry this exposed.** Because preparation is
strictly later than capture, the capture-time pool can contain rows the real
artifact excluded — wagers the site was structurally unable to publish.
Leaving them in would let PA-v1 select from a strictly larger opportunity set
than the champion ever had, at identical headline volume.
`prospective_epoch.regate_pool()` re-applies the cutoff against the bound
deployment's real `prepared_at` before any selection.

---

## 6. Identity — four facts, never conflated

| fact | source |
|------|--------|
| team side (`home`/`away`) | `row["side"]` (`generate_picks.py:2692`) |
| wager direction | `live_state.market_side_token(row)` |
| line | `projection.value` |
| threshold to win | `projection.needs` |

Misreading `row["side"]` as a wager direction would be wrong for 100% of rows
while remaining perfectly self-consistent — which is why a naive round-trip
test would not catch it. All four are recorded separately on every receipt and
asserted pairwise distinct.

`prop_identity_key()` is reused verbatim as the core expression identity and is
**not** overloaded into the whole receipt.

---

## 7. The receipt and the ledger

**No outcome field, enforced not assumed.** `assert_no_outcome()` walks
recursively and refuses any outcome-shaped key. Deliberately aggressive: a
false positive costs one renamed field, a false negative costs the experiment.
Two exemptions, each individually justified (`settlement_identity_key` and the
`settlement_supported` gate name are pregame facts). `build_receipt` is
additionally an allow-list projection, so a stray outcome field on a source row
cannot reach the receipt at all.

**A later price is a different receipt state, not an edit.** The content SHA
covers exact odds and odds timestamp, so a re-observed price cannot round-trip
to the same hash.

**`receipt_id = sha256(epoch_id, canonical_prop_id)`**, deliberately NOT keyed
by arm — three keys for one wager at one epoch would let a single observation
be counted more than once. Arm membership is two flags inside the one receipt.

**Ledger:** orphan research branch, idempotent keys, identical re-append is a
reported no-op, differing content under the same key raises, settlement is a
separate event type, crash-safe via temp-file + fsync + atomic rename, corrupt
lines raise rather than being skipped.

---

## 8. Decisive epoch and equal volume

One decisive epoch per slate date, chosen after the date is over, mechanically
and outcome-blind. The binding is exact, not a timestamp correlation: the
build's `board_generated_at` travels into the artifact as `data.json`'s
`generated_at`, and `dashboard-deploy.yml`'s existing convergence check polls
the **public** URL until both `publication_manifest.source_commit` and
`data.json.generated_at` match. That makes `generated_at` a join key proving
the build went public.

Live-update-originated deployments never qualify. No qualifying deployment
raises `NoPrimaryEpoch` — missing evidence, not a loss, and not a licence to
fall back to a friendlier snapshot.

The champion arm is `build_publication_manifest()`'s own candidate list, which
has already applied production's real exposure gates. Every champion must
resolve against the frozen raw universe or the epoch fails closed. `N(date)`
counts champions also in the gated pool. Equal volume is enforced **per epoch**
and raises — a global top-N budget would let a challenger win by moving picks
onto easier days at identical headline volume.

---

## 9. Settlement

The wager is reconstructed from the receipt and only from the receipt.
`candidate_funnel_grader.load_latest_records()` is unusable here and is not
imported: it resolves a prop to its latest candidate state, so a line or side
that moved after the decisive epoch would settle a **different** wager
invisibly, producing a number that looks like a hit rate and is not one.

`market_side` is carried under its own key because
`grade_results._is_under_pick()` reads exactly that and refuses to infer
direction from a display string — a test proves the sealed direction wins even
against a contradictory label.

Four outcomes stay distinct. `hit_rate` uses the decided denominator;
`void_rate` and `ungraded_rate` use the selected denominator, so a challenger
cannot appear superior merely by deciding less often.

---

## 10. The frozen bootstrap contract

Unit = slate-date cluster. With replacement, carrying all selections with
multiplicity. 5,000 replicates. Seed 20260901. `random.Random`. Statistic =
PA-v1 minus champion at exact matched volume on the decided denominator. 95%
percentile interval. Game/player clustering secondary only.

`run()` takes **only** the settlements — no seed, replicate or CI parameter
exists, so there is no argument through which to redraw a seed or trim
replicates to move an interval. Undefined replicates are skipped and counted,
never coerced to zero.

A test proves the clustering is real: the same picks concentrated into one date
collapse the interval to a point, where pick-level resampling would have shown
a falsely narrow spread.

---

## 11. Step K — real live dry runs, zero production promotion

**Run 1, 2026-09-01 23:48 UTC.** The tap fired at the correct boundary and
**skipped with a stated reason**: `board_generated_at` preceded the artifact's
`effective_from` by twelve minutes. Rather than fake the clock, I waited for
the real boundary and re-ran.

**Run 2, 2026-09-02 ~00:05 UTC**, full build against live MLB/FanDuel:

```
270 raw hits rows in by_category_full["hits"]
  0 eligible
270 rejected: lineup_confirmed
185 rejected: real_current_price
 47 rejected: reliability_a_or_b
 16 rejected: evidence_sample_nonzero
  0 champion Top Picks on the same board
```

Correct and **symmetric** on both arms. The build ran ~18 hours before first
pitch, so no lineup is posted and every row is an assumed-lineup research row.
`N(date) = 0` → no comparison is created and nothing is manufactured.

It also demonstrates the 1C correction on real data: 270 rows present in the
raw capture source, zero wagerable.

Both builds exited 0 and produced complete customer output (2,568 props).

**Operational consequence worth recording:** the decisive epoch will in
practice always be a late-day build (13:00–23:00 UTC), never the 01:00/03:00
UTC ones, because early builds structurally cannot clear the confirmed-lineup
gate. §7's "latest converged refresh-originated deployment" already selects for
this — it is not a new rule, but it is why that rule lands where it does.

---

## 12. Open items, stated plainly

1. **OPEN GAP — no source-integrity hold mechanism exists.** A repo-wide search
   for `source_integrity` / `integrity_hold` across `*.py` returns nothing. The
   §5 gate is a real, injectable check over a registry that is **empty by
   default**. A functioning gate over an empty set, not a satisfied
   requirement. Filling it requires deciding what a hold *is* — a policy
   question, not an implementation one.

2. **No decisive epoch has been bound end to end against a real Pages
   deployment.** Steps F/G are unit-tested against constructed deployment
   records, not against a real converged deployment, because binding one would
   require a real refresh-originated deploy with a persisted shadow snapshot —
   which needs `FULLCOUNT_SHADOW_PERSIST=1` in CI, and that is a deploy-adjacent
   change I did not make without authorization.

3. **Pre-existing, not mine: `test_board_first_paint.py` fails.** It fails
   identically at `14e7b403` with all my changes stashed. Fixture-clock aging:
   the stored timestamps drift with wall time, so at 11h the price-age reason
   now trips before the board-age one the test asserts. Same defect class as
   the browser-E2E nested-clock issue. Reported, not absorbed. Full suite
   otherwise 134 passed.

4. **Deviation from your step-A instruction, still standing.** Three fields
   (`created_at`, `repo_worktree_fully_clean`, `authoritative_run`) sit in an
   unhashed `run_provenance` sibling rather than inside the hashed body,
   because wall-clock and worktree state made the artifact fail its own
   reproducibility check. You said "hash the complete canonical body excluding
   only the self-referential hash field"; this excludes three more. Offered for
   reversal if you prefer the literal reading.

5. **Not started: nothing.** Steps B–K are complete to the extent possible
   without authorization to persist in CI.

---

## 13. What I did not do

- Did not persist anything to the research ledger (`FULLCOUNT_SHADOW_PERSIST`
  is unset; both live runs were dry).
- Did not create the `research-ledger/prospective-hits-pa-v1` branch on the
  remote. `ensure_ledger_worktree()` will create it as an orphan on first real
  persist.
- Did not open a pull request.
- Did not modify any scoring, calibration, threshold, selector or settlement
  logic. The only production file touched is `dashboard/build_dashboard.py`,
  and only to add an observational tap and two additive schedule keys.

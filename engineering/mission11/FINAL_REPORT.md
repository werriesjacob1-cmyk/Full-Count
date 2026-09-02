# Mission 1.1 — Prospective PA-v1 lifecycle closure: final report

Branch `claude/prospective-hits-pa-lifecycle-closure-01`. Draft PR **#85**, DO NOT MERGE.

---

## A. RUNTIME / SUPERCLAUDE STATUS

**The `fc-*` runtime is NOT active.** Established by evidence, not assumed:

1. The agent types available are `claude`, `claude-code-guide`, `Explore`, `general-purpose`, `Plan`, `statusline-setup`. No `fc-*` agent exists.
2. `ListSkills` filtered on `fc / prospective / audit / red-team / release` returns exactly one skill, `mlb-betting-analyst`.
3. The project root `.claude/` contains only `settings.local.json`, `worktree-autosave.sh` and `worktrees/` — **no `agents/` and no `skills/` directory at all**, so nothing could load at session start.
4. The definitions exist only on the unmerged `tooling/superclaude-activation-01` (9 agents, 10 skills).

**Fallback used, as the brief directs.** PR #73 was not merged and no time was spent installing tooling. The real role definitions were read out of `tooling/superclaude-activation-01` and used verbatim to drive independent generic subagents, preserving the read-only boundary manually. Six reviewer lanes total: three pre-implementation, three post-implementation, all fresh.

Recorded from the definitions themselves because it bounds what "read-only" means: each `fc-*` auditor file states that `Bash` is granted and a shell is a superset of Write/Edit, so read-only is enforced at the tool layer and conventional at the shell layer. That caveat was restated verbatim in every reviewer prompt.

---

## B. LIVE REPO IDENTITY

| | |
|---|---|
| base (claimed and verified ancestor) | `41369064aedd4b81ac36f0f05cda2bc0fa587c46` |
| branch | `claude/prospective-hits-pa-lifecycle-closure-01` |
| **final head** | **`32ede6a7bcc5a156ddf1b5da9ead57e67e4ec00f`** |
| `main` at preflight | `627b8bff…` (75 commits since merge base) |
| merge base with `main` | `a301f25c005ef1de1ad45e17a96fa16d564f1a86` |

**Divergence classification: 100% generated/data.** `git diff --name-only <base>..origin/main | grep -E '\.py$|^\.github/' | wc -l` → **0**. The single `dashboard/` path is `lineup_watch_state.json`, which `lineup-watch.yml:80` commits as its own runtime state. Main was therefore deliberately not rebased in.

**Disclosure from the release auditor, which I accept:** `41369064` is **not an ancestor of `main`** — it is the tip of the unmerged Mission 1 branch, which has no PR of its own. So PR #85, judged against `main`, would land **both** Mission 1 and Mission 1.1 (32 files, +7738/−1). The `41369064..head` diff is a review convenience, not what would merge. Also, main is now ~143 commits ahead, not the 75 the PR body states — still 0 `.py`, 0 `.github/`.

---

## C. PRE-IMPLEMENTATION INDEPENDENT FINDINGS

| lane | verdict |
|---|---|
| prospective ledger auditor | **DEFECTS FOUND — 12** |
| methodology red team | **DOES NOT SURVIVE — 4 decisive** |
| live/workflow SRE | integration spec + 1 pre-existing production bug |

Two lanes ran independently and **converged on the same decisive defect**: the champion arm read `manifest["candidates"]`, whose first filter is a cumulative cross-date registry membership test, making it **first-exposure-only** while §7 mandates the *latest* deployment as decisive. Measured on real state: `docs/data.json` for 2026-09-01 displayed 2 Hits Top Picks, both already registered, so the manifest would emit **zero** — N(date)=0 while the site showed two all evening.

Full detail in `01_REVIEWER_FINDINGS.md`. **Three of my own Mission 1 claims were withdrawn** there: that `regate_pool()` closed the two-clock asymmetry (it had no caller); that §7's rule "already selects for" lineup confirmation (unverified and wrong in direction); and "correct and symmetric on both arms" (drawn from a 0-eligible, 0-champion run).

---

## D. LIFECYCLE STATE MACHINE

Nine immutable event types replace three:

`snapshot_captured` → `deployment_observed` → `public_exposure_bound` → `epoch_selection_sealed` → `decisive_epoch_designated` → `pregame_receipt` → `settlement`, plus `epoch_failed_closed` and `no_primary_epoch`.

Transitions and why each is immutable:

| transition | immutable because |
|---|---|
| capture → `snapshot_captured` | a snapshot existing asserts nothing about exposure; Mission 1 wrote this under `epoch_bound` with `publicly_converged: False`, so the real binding later collided on that key and was refused |
| convergence → `public_exposure_bound` | a deployment going public is a different fact from which epoch is primary |
| binding → `epoch_selection_sealed` | sealed **before outcomes exist**; a later re-run cannot alter it, only add under a new key |
| after the date → `decisive_epoch_designated` | designation points at an already-sealed set; it cannot modify one |
| selection → `pregame_receipt` | a later price is a new receipt state, never an edit |
| later → `settlement` | a separate event carrying the receipt id **and** its content hash |

`backtest/prospective_lifecycle.py` is executable: `bind-exposure`, `designate`, `settle`, `report`. Mission 1 had **no module with a `__main__`**, `build_epoch_selection` had **no caller anywhere**, and `regate_pool` was never invoked. AST tests now assert those calls exist.

**Selections are sealed at convergence, not after the date.** Waiting until the games are over would reconstruct pool, verdicts and receipts after outcomes are visible. Every bound epoch seals its complete comparison before any game is decided; designation is a separate mechanical read that can only point at one of them.

---

## E. REMOTE DURABILITY / CONCURRENCY

Mission 1 retried `git push` with backoff. A non-fast-forward rejection is rejected identically every attempt — the local ref never advances — and after four attempts it returned `{"committed": True, "pushed": False}`: a silent local-only ledger on a container about to be reclaimed.

`append_events()` is a pure function of (remote content, pending events), so the loop re-derives against fresh remote state every attempt: **fetch → reset --hard onto remote → replay → commit → push**, looping on fast-forward rejection with jittered backoff. Never rebase, never force-push. Same-key/different-content is a hard failure, never a retry. Orphan creation is re-resolved every attempt.

**Deliberate race test, against a real bare git remote** (`test_prospective_ledger_concurrency.py`): a competing push is injected in the window between writer B resolving and writer B pushing. B is genuinely rejected (`attempts > 1`), recovers, and **all three non-conflicting events survive**. Also proven: identical re-append is a durable no-op; same key + different content raises and leaves the remote unchanged; a local-only commit reports `durable: False` and never reaches the remote; two writers both starting with no remote branch both survive.

The release auditor flagged this suite as **timing-sensitive under load** — it failed once locally then passed 5/5 and passes in CI. Real flakiness risk, disclosed.

Ledger is partitioned per slate date (was ~160 KB/capture × 20–30 captures/day in one rewritten file).

---

## F. SNAPSHOT / RECEIPT COMPLETENESS

Mission 1's snapshot stored 17 projected fields; `build_receipt()` reads **28 row + 6 verdict + 7 meta**. **24 fields were destroyed at process exit**, one of which was `stat` — which silently **broke settlement**, since `reconstruct_pick()` feeds it to `grade_public_pick()`, which dispatches on it.

Fixed by widening what is sealed, never by adding a reader. `RECEIPT_ROW_FIELDS` / `_VERDICT_` / `_META_` are one allow-listed source of truth, and a test **AST-derives what `build_receipt` actually reads** and asserts coverage, so the projection and its consumer cannot drift again.

**Proof:** a receipt rebuilt from *only* the sealed basis — through a real JSON round-trip, with the live candidate deleted — is **byte-for-byte identical** to the live one. The post-implementation auditor reproduced this independently in `/tmp`: 2805 bytes, sha `c9a83a95…`, and `reconstruct_pick` off the snapshot-only receipt has zero `None` fields.

**Real latent defect found and fixed:** `prop_identity_key()` returns a tuple whose subject element is itself a tuple, and `list(identity)` flattened only the outer level. `canonical_json` serializes tuples and lists identically, so every hash check passed — but a receipt **read back** from the ledger did not compare equal to the one written. A silent asymmetry in permanent evidence no hash could catch.

---

## G. SOURCE-INTEGRITY CONTRACT

`CLEAR` / `HOLD` / `UNKNOWN`, versioned, composed only from signals existing sole writers already produce.

| state | meaning |
|---|---|
| UNKNOWN | live.json unreadable/absent, or a required freshness channel has **no timestamp at all**. **Fails closed.** |
| HOLD | empty schedule (whole-slate fetch failed); a required channel stale past `check_live_freshness`'s own SLA; or, when present, a board-age or lineup reconciliation mismatch |
| CLEAR | an evaluation genuinely ran over the available signals and none raised a hold |

Every verdict records `evaluated_signals` and `unevaluated_signals`.

**Deliberately NOT holds**, each because holding on it would zero the experiment while looking rigorous: FanGraphs 403 (happens on essentially every real run, documented graceful degradation), any optional enrichment failure, `lineup-watch` not having run (its own workflow retires it at 9% of declared cadence), and `NOT_POSTED`/`LINE_MOVED` (real successful observations, already the price gate's job).

**Reconciliation is an enhancer, not a precondition** — see §R for the production finding that forced this.

---

## H. PUBLIC EXPOSURE BINDING

Hook specified (not applied): `dashboard-deploy.yml`, job `deploy`, a new **final** step after `Confirm durable public exposure`, `continue-on-error: true`. Since `Verify public site converges…` has no `continue-on-error`, any later step running is itself proof convergence succeeded.

The binder requires: refresh origination, `converged`, and `public_generated_at == board_generated_at` — the value the deploy workflow polls the **public URL** until it matches.

**The `source_commit` trap, removed.** Requiring `build_source_commit == deployment.source_commit` was provably unsatisfiable: build is main HEAD when the refresh *started*, deployment is main HEAD at the deploy checkout, several commits later. All 12 real recorded deployments carry a `Dashboard live update` head commit. §8 requires both **recorded**, not equal.

**The question Mission 1 did not ask.** `prepared_at` clearing the 15-minute rule does not make a prop wagerable — being *publicly visible* does. Measured latency is 3–7 min typical, ~21 min tail. `publicly_usable()` drops any pick whose game had started by public convergence, applied symmetrically to both arms.

Live-update-originated deployments never qualify as primary.

---

## I. EQUAL-VOLUME SELECTION

The champion is read from the artifact's **served payload** with production's real gates (top_pick, hits, pregame, strict `before_betting_cutoff`) minus the lifecycle-bookkeeping registry filter. An AST test with docstrings stripped asserts no registry-membership filter and no `0.60` proxy survive in executable code.

`build_epoch_selection` now: verifies the payload binding → re-gates against the bound deployment's real clocks → reads the champion → fails closed on any shadow-gate drop → resolves → ranks PA-v1 over the **same** re-gated pool → takes exactly N → asserts per-epoch equal volume.

Fail-closed cases: unresolvable champion; unidentifiable exposed pick; **published-but-ineligible champion**; any exposed Hits row removed by a shadow gate; missing `publication_cutoff_at`; unbound payload.

---

## J. DECISIVE EPOCH

Every bound epoch seals its own comparison. Designation reads only `public_exposure_bound` bodies and `deployment_prepared_at` — an AST check with the docstring stripped asserts no outcome/hit_rate/settlement/grade/actual token appears.

**Fixed:** it sorted `prepared_at` **lexicographically**, ordering `+00:00` before `Z` at the same instant. Now parsed, with ties broken deterministically on epoch id rather than ledger append order (a function of the concurrent-push race).

No valid epoch → `NO PRIMARY EPOCH` recorded as an immutable event with its reason.

---

## K. SETTLEMENT

The wager is reconstructed from the receipt and only the receipt. `candidate_funnel_grader.load_latest_records()` is not imported — a test asserts this against the code with the docstring stripped. `market_side` is carried under its own key because `grade_results._is_under_pick()` reads exactly that and refuses to infer direction from a display string.

Retry appends only identical missing events; a **changed** settlement under the same key raises. Settlements carry the receipt id **and** its content hash; the scoreboard **excludes** pairing mismatches from counting.

---

## L. LIVE FEATURE PARITY — **ESTABLISHED**

Structural: both regimes read `signals[name]` written by the same `generate_picks._sig()` call sites and decode through the same `pa_v1_fit` group functions. `backtest/engine.py:1355` calls the same `gp.build_candidates()` the live pipeline uses and copies the bag verbatim at `:1111`. No separate historical feature builder exists to drift.

Measured on 270 real live Hits candidates: `lineup_slot` 270/270, decoding to exactly 30 in each of orders 1–9; `getaway_day` 270/270, domain `{-2.0, 0.0}` **identical** to historical; `days_rest` 268/270, domain a subset; 267 joint cell / 1 cell-absent / 2 no-key, **270 scored**.

**Permanent caveat, recorded and deliberately not fixed:** `days_rest_group()` buckets on `0/1/<=3/else` as if the value were a raw day count, but `_sig` stores the *scaled* `clamp((n-1)*2,-3,4)`, whose integer range is only `{-2,0,2,4}`. **`1_day_rest` is structurally unreachable** — confirmed exhaustively and empirically (zero such cells among the artifact's 41). The labels do not describe their contents. Not a parity defect (the mapping is identical in both regimes), and not fixed because PA-v1 is frozen.

**Downgrade I accept from the red team:** *distributional* parity is asserted from a single slate. `getaway_day` is 60.0% live versus 31.2% of training mass. One date proves nothing either way; the forward window must re-check it.

---

## M. REPORTING (§12)

`backtest/prospective_scoreboard.py`, read-only. §12 had **no implementation** — grep for `overlap`/`pa_only`/`champion_only` returned zero.

Covers: primary slate dates; missing-primary dates with reasons; epochs failed closed; per-arm selected/decided/hit/miss/void/ungraded; hit rate on the **decided** denominator with void/ungraded on the **selected** denominator; exact per-date volume equality with mismatches listed; overlap, PA-only and champion-only; per-date contribution direction; date-cluster bootstrap CI; lineup/fallback/integrity/odds distributions; version strata; settlement pairing mismatches (excluded from counting); and the promotion floor (30 dates AND 100 decided per arm) reporting **INCONCLUSIVE / NOT YET PROMOTABLE** below threshold, explicitly never a PA-v1 failure.

Secondary game/player clustering is reported as **NOT COMPUTED** rather than silently omitted — see §R.

---

## N. TESTS

8 prospective suites. The end-to-end test drives the whole machine with **N = 4** against a real bare git remote, plus ~20 adversarial cases.

**Full suite at final head: 136 passed, 2 failed.**

Both failures — `test_board_first_paint.py`, `test_browser_e2e.py` — **fail identically at the base `41369064`**, independently reproduced by the release auditor in a separate worktree, and `test_board_first_paint.py` also fails at the tip of `origin/main`. Same pre-existing fixture-clock-aging class. `superchad/fix-board-first-paint-clock-fixture-01` already exists; **not cherry-picked**, per §18.

Wording correction I owe: in the PR-merge CI context the failing check inside `test_board_first_paint.py` is a *different* assertion than in the branch-content run. Both are pre-existing; my "fail identically" phrasing was true only of the branch-content run.

---

## O. EXACT-HEAD GITHUB CI

**Head `bc9c8ce28aeab65338230b8f4a6fdf55b6af923c`. Both runs COMPLETE. Both RED.**

| run | event | status | conclusion |
|---|---|---|---|
| 33595832763 | `push` | completed | **failure** |
| 33595836502 | `pull_request` | completed | **failure** |

**I am not calling this green.** No required-status contexts are configured on this repo, so nothing is "required-green" either.

Verified locally at this exact head — inference from the previous head was not accepted as evidence, even though the only delta is one markdown file:

```
AT bc9c8ce2: PASSED=136 FAILED=2
failing: test_board_first_paint.py test_browser_e2e.py
```

All **8 prospective suites pass** at this head, individually re-run:
`capture`, `eligibility`, `epoch`, `ledger_concurrency`, `lifecycle_e2e`,
`receipt`, `reporting`, `settlement`.

Both failures are the **pre-existing** fixture-clock-aging pair, independently
reproduced by the release auditor at the base `41369064` in a separate
worktree, and `test_board_first_paint.py` additionally at the tip of
`origin/main`. Not introduced by this branch, and deliberately not fixed here
(§18) — `superchad/fix-board-first-paint-clock-fixture-01` already exists and
was not cherry-picked.

Wording correction I owe: in the PR-merge CI context the failing assertion
inside `test_board_first_paint.py` is a *different* one than in the
branch-content run. Both are pre-existing; my earlier "fail identically"
phrasing was true only of the branch-content run.

Final safety re-verification at this head:

```
protocol sha256           5ce1ae95…b2b355de7   MATCHES
PA-v1 scientific hash     VERIFIED (recomputed)
PA-v1 file sha256         112517321e56…70750   MATCHES
.github/ files changed    0
FULLCOUNT_SHADOW_PERSIST executable occurrences  0
```

## P. INDEPENDENT POST-IMPLEMENTATION VERDICTS

| reviewer | verdict |
|---|---|
| prospective ledger auditor | **DEFECTS FOUND** |
| methodology red team | **DOES NOT SURVIVE** |
| release auditor | **RELEASE AUDIT PASSED — with two disclosures** |

**Both critical reviewers were right, and I fixed what they found** (units 7–8) — but their verdicts stand as issued, against the head they reviewed. They have **not** re-reviewed the fixes. Do not read the fixes as a passing verdict.

What they found and what I did:

1. **FATAL — the production call site never invoked the contract.** `build_dashboard.py` called `capture()` without `source_integrity`, which defaults to UNKNOWN and blocked every row on every date forever. `psi.evaluate()` had no caller outside tests. **This is my own repeat of the exact "uncalled code" defect I criticised Mission 1 for.** Fixed; the auditor independently verified it closed.
2. **The champion arm was unauthenticated.** The red team *executed* the attack: bump the free-string `artifact_id` to mint a fresh key space, bump `prepared_at` to win designation, hand in a chosen payload — and it sealed a different champion set (N=4 → N=2) and won. Ledger immutability protects a fixed key; the key was attacker-controlled. Fixed with `verify_payload_binding()`; their attack is now a regression test that fails closed.
3. **Silent champion shrinkage reintroduced one function earlier** — four gates used a bare `continue` before `resolve_champions` could fail closed. Fixed; every drop is recorded and fails the epoch.
4. **Cutoff fallback looser than production** — fixed, fails closed.
5. **Lexicographic sort in `designate()`** — fixed.
6. **Bootstrap hash recorded but never verified** — now a pinned baseline, proven to detect a one-line seed edit, and a modified contract blocks the promotion verdict.

---

## Q. REAL LIVE ENABLEMENT STATUS

**LIVE PERSISTENCE / REAL PAGES BINDING AWAITS HUMAN ENABLEMENT AUTHORIZATION.**

Shadow persistence is **disabled**. `FULLCOUNT_SHADOW_PERSIST` is read in exactly one place and set to `"1"` in no workflow, script, env file or Dockerfile — the only `: "1"` occurrence is inside a fenced code block in an unapplied Markdown spec. Independently verified by the release auditor. **Zero files under `.github/` are modified.**

---

## R. OPEN GAPS

1. **Optional stopping is unaddressed.** §13 sets a floor but no ceiling, no alpha-spending rule and no preregistered analysis schedule, while §12 requires a CI at every checkpoint. The report is callable at any moment and records nothing about how many times it has been run. Adding a stopping rule would amend the frozen protocol — **Jacob's call**.
2. **Player-crossed clustering is declared, not computed.** PA-v1 ranks on batting order and repeatedly selects the same top-of-order hitters, so player is a *crossed* cluster the date-level resample absorbs none of. The understatement is asymmetric **in the challenger's favour**. `SECONDARY_UNITS` exists as a constant with no code path.
3. **The fail-closed rule's date-selection effect is reported by reason but not by composition** — not which gate, which arm, which stratum. The red team's specific concern: late lineup confirmation concentrates on getaway days, and `getaway_day` is one of PA-v1's three frozen features, so fail-closed dates may be deleted preferentially from the stratum where the challenger's edge is defined. Their recommended alternative — report both strict-N and full-N — is **not implemented**.
4. **Whether refresh-originated deployments converge often enough is unmeasured.** The deploy concurrency group is shared with ~288 live-update deploys/day and pending runs are cancelled by newer ones. Answerable only from Actions run history. **Query a week before enabling.** If they are rare, the honest outcome is many NO PRIMARY EPOCH dates; loosening §7 is not available.
5. **Distributional feature parity rests on one slate** (§L).
6. **`test_prospective_ledger_concurrency.py` is timing-sensitive** and will produce occasional spurious CI reds.
7. **PR #85's base is not on `main`** (§B) — merging it would land Mission 1 as well.
8. **`select_decisive_epoch` is now dead code**, superseded by `lifecycle.designate`.

### Production defects found, NOT fixed here (each needs its own change and deploy authority)

- **The Prediction Ledger has silently recorded nothing.** `confirm_publication.py` appends hash-chained events to `data/prediction_ledger/events.jsonl`, but `dashboard-deploy.yml` stages only `registry.json`. `registry.json` holds 108 entries; `events.jsonl` holds 6, **none workflow-produced**.
- **Reconciliation never populates `live.json`.** Null across twelve consecutive commits on `main` spanning an hour. `run_reconciliation.py` runs every 5 minutes from `dashboard-live.yml`. This directly weakens the source-integrity gate, which can currently evaluate freshness and schedule but not reconciliation-derived holds.

---

## S. HUMAN DECISIONS NEEDED

1. **Enable shadow persistence?** Three workflow edits, two of them to the live publication path. Specified in `WORKFLOW_INTEGRATION_SPEC.md`.
2. **Optional stopping** — adopt a preregistered analysis schedule, or accept unlimited looks and say so. Amends the frozen protocol.
3. **Fail-closed vs strict-N/full-N dual reporting** (§R.3).
4. **The two production defects above** — both are one-line fixes in the live publication workflow, both deserve their own PR.
5. **PR #85's base.** It cannot merge cleanly as scoped without Mission 1 also being reviewed.

---

## T. FINAL SAFETY STATEMENT

- **NOT MERGED.**
- **NOT DEPLOYED.**
- **SHADOW PERSISTENCE NOT ENABLED.**
- **PA-v1 NOT PROMOTED.** Both frozen hashes verify byte-for-byte: protocol `5ce1ae95…`, artifact scientific `a4f598bd…`, artifact file `11251732…`. No refit, no new feature.
- **PUBLIC PICKS UNCHANGED.** Zero changes to `generate_picks.py`, `recommendation.py`, `prop_probability.py`, `value_board.py`, `grade_results.py`, `settlement_rules.py`, `refresh_grades.py`, `calibrators/`, `docs/`, `data/`. The one production file touched is `dashboard/build_dashboard.py`, changed only to add an exception-guarded observational tap and two additive schedule keys that never reach the public payload.
- **NO UGE / TEAM-vs-TEAM WORK PERFORMED. NO HR EXPERIMENT.**

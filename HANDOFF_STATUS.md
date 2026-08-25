# Full Count — session handoff status

Last updated: 2026-08-25 ~15:05 UTC by Claude (this session).
Purpose: let a fresh session resume with zero hidden chat context.

## RESTART-SAFETY HARDENING DONE (2026-08-25 ~14:30-15:05 UTC)

Per the continuation directive after the two restarts documented below,
audited `backtest/engine.py`'s existing backfill write/resume semantics
BEFORE assuming anything needed building from scratch. Real finding:
**`run_backtest()` was already genuinely interruption-resumable** --
per-date state-file checkpointing (`load_state`/`save_state`), and
critically `dates_already_in_output()` trusts the REAL output file over
the state-file bookkeeping (so even a missing/corrupted state file can't
cause duplicate work), no_games and failed dates both checkpointed
distinctly (failed dates auto-retry on resume, no_games/ok dates are
skipped). **Re-running the exact same command after a crash already
resumes correctly -- this was true before this session's edits too.**
Proved this with 7 new tests
(`test_backfill_resume.py::FreshRunTests`/`InterruptionResumeTests`/
`ForceFlagTests`, all mocking `simulate_date` for deterministic,
network-free testing) -- all passed against the UNMODIFIED code.

One real, narrow gap found and fixed (`backtest/engine.py`'s
`run_backtest()`): a date's rows were written via a per-row loop of
`f.write()` calls -- if the process died mid-loop, that date could be
left PARTIALLY in the output file yet still get treated as "already
done" on resume (since `dates_already_in_output()` only checks date
PRESENCE, not completeness). Fixed: build the whole date's blob in memory
and issue ONE `f.write()` call instead -- narrows the crash window from
"however many rows this date has" to a single syscall. Locked in by
`AtomicWritePerDateTests` (failed against the old per-row-loop code,
passes against the fix).

Also added `check_regime_consistency(out_path, current_sha=None)` --
surfaces (WARN-level, not a hard gate; `provenance.require_single_regime()`
remains the hard enforcement point at canonical-build time) whether a
resumed file already contains more than one `code_git_sha`, using the
code_git_sha every row already carries (no new tracking invented). Wired
as a startup warning in `run_backtest()`'s resume path. 5 new tests
(`RegimeConsistencyTests`).

**Practical implication**: PID 3304 (the current backfill attempt) uses
this exact `run_backtest()` path. If it dies again, re-running the SAME
command (`--start 2024-04-01 --end 2026-06-30 --out
backtest/rows_canonical_rebuild.jsonl --no-weather`, no `--force`) will
resume from the first incomplete date, not restart from date 1 -- this
was already true, and is now slightly more crash-safe on top.

**Durable persistence (Priority 4) -- evaluated, not yet executed**:
full option comparison in
`backtest/durable_artifact_persistence_2026-08-25.md`. Checked directly,
not assumed: no GitHub Release create/upload-asset tool exists in this
session's MCP toolset (only read tools), no `gh`/`hub` CLI available,
`git-lfs` is not installed in this container. **Recommended and ready to
execute the moment the backfill finishes**: gzip-compress
`rows_canonical.jsonl`, split into <100MB chunks, commit directly to git
(`git add -f`, overriding the `backtest/*.jsonl` gitignore rule for just
those specific compressed snapshot files) -- durable indefinitely, free,
needs no new tools/credentials. A GitHub-Actions-based, date-sharded
parallel backfill (so the computation itself survives this session
entirely) is flagged as the better long-term fix but was NOT attempted
this pass -- too large a change to risk half-finishing across another
restart; see that doc's own "what was not done" section.

## IMPORTANT: session worker restart + data recovery in progress (2026-08-25 ~10:50 UTC, SECOND restart ~11:05 UTC)

This session's container/worker was restarted mid-turn -- TWICE. Both
times, git-committed state was completely unaffected (confirmed via
`git fetch` + fast-forward merge each time -- nothing has ever been lost
from git). Both times, the same casualty: a long-running background
backfill process (started via `nohup ... &`) was killed outright, along
with its local monitor process and the local working directory being
reset to a stale `main` checkout.

**Restart 1** (~10:50 UTC): killed the original canonical dataset in
progress reconstruction. Recovery: relaunched backfill as PID 8145,
committed `7dcfdb4a`/`ef4ef292`.

**Restart 2** (~11:05-11:10 UTC, noticed when the user asked "is backfill
done"): killed PID 8145 before meaningful progress (checked at ~03:11
elapsed the one time before it died). Recovery: relaunched AGAIN as a new
PID (check `ps aux | grep backtest/engine.py` for the current one --
don't trust any PID number written here, it will be stale by the time you
read it). Local stale `backtest/rows_backfill.jsonl`/`.state.json` files
(leftover from main's old 2026-08-20 state) were deleted again before
relaunching.

**Lesson learned, applied going forward**: a local `nohup`'d background
monitor does NOT survive a worker restart (verified twice) -- do not trust
one to notify reliably for a multi-hour job in this environment. Prefer
checking `ps aux | grep backtest/engine.py` and the row count of
`backtest/rows_canonical_rebuild.jsonl` directly when resuming, and/or a
server-side scheduled check-in (`send_later`/routine), not a local
background watcher, for anything that needs to survive a restart.

**What this means for resuming, if you land here mid-backfill AGAIN**:
1. `git fetch origin claude/gridiron-continuation-dvaljm` then
   `git checkout claude/gridiron-continuation-dvaljm && git merge --ff-only
   origin/claude/gridiron-continuation-dvaljm` -- this ALWAYS recovers full
   git state instantly, has never failed across two restarts so far.
2. Check `ps aux | grep "[b]acktest/engine.py"` -- if nothing is running,
   the backfill died with the restart (again) and needs relaunching:
   `rm -f backtest/rows_backfill.jsonl backtest/rows_backfill.jsonl.state.json`
   (clean up any stale main-branch leftover file first), then
   `nohup /tmp/mlbvenv/bin/python3 backtest/engine.py --start 2024-04-01
   --end 2026-06-30 --out backtest/rows_canonical_rebuild.jsonl --no-weather
   > backtest/_backfill_rebuild.log 2>&1 &`.
3. Do NOT assume progress carries over between relaunches -- `engine.py`
   writes to a fresh output file each invocation named here
   (`rows_canonical_rebuild.jsonl`); check its row count / the state JSON
   next to it if one exists to see how far a given attempt got before
   dying, but each relaunch starts the date range over from scratch unless
   `engine.py` itself has resume support (check its own `--help`/state-file
   handling before assuming either way).
4. `backtest/disagreement_decomposition.py`, `disagreement_challenger_model.py`,
   and their tests are safely committed now (unlike after restart 1) --
   no need to recreate them again.

## Original restart-1 recovery notes (superseded in relevance by the above, kept for the full timeline)

Data recovery in progress (2026-08-25 ~10:50 UTC)

This session's container/worker was restarted mid-turn. The git-committed
state (branch/commits/all `.md` findings) was NOT affected -- everything
already committed and pushed is fully intact, confirmed via
`git fetch origin claude/gridiron-continuation-dvaljm` after the restart.
**What WAS lost**: the large, gitignored (`backtest/*.jsonl`) raw data
files -- `backtest/rows_canonical.jsonl` (1,027,462 rows, the whole
canonical dataset the backfill took ~7 hours to build) and
`backtest/rows_backfill_repair.jsonl` -- were on local disk only, never
in git by design, and did not survive the restart. A stale, wrong
`backtest/rows_backfill.jsonl` from 2026-08-20 (a much earlier, unrelated
run) was also found on disk and has been DELETED to avoid confusion.

**Recovery action taken, already in progress**: relaunched the full
backfill fresh -- `backtest/engine.py --start 2024-04-01 --end
2026-06-30 --out backtest/rows_canonical_rebuild.jsonl --no-weather`,
PID 8145, started ~10:55 UTC. Verified before launching: no commit since
`f7c120a3` has touched `generate_picks.py`/`backtest/engine.py`/
`backtest/signals.py`/`mlb_sources.py` (confirmed via
`git log -1 -- <those paths>`), and this session will not touch them
while the backfill runs -- so this run will be a SINGLE consistent
`code_git_sha` throughout, meaning **the old repair-file/main-file
reconciliation dance (`build_canonical_backtest.py`) is no longer
necessary** -- `rows_canonical_rebuild.jsonl` can become
`rows_canonical.jsonl` directly once it finishes and passes
`provenance.require_single_regime()`, no merge step needed. A background
monitor (nohup'd, PID 8518, survives detached from this session) is
watching PID 8145 and will report completion.

**What this means for resuming**: every `.md` finding in this repo
(canonical baseline, opportunity-decomposition, residual-opportunity,
disagreement-decomposition) represents REAL, already-verified conclusions
from the FIRST successful canonical build -- they are not invalidated by
this data loss, since nothing about the scoring code changed. But no NEW
live query against canonical history is possible until the rebuild
finishes. **Two source files (`backtest/disagreement_decomposition.py`,
`backtest/disagreement_challenger_model.py`, and their tests) were
recreated from this session's own prior context after the restart wiped
them from disk before they'd been committed -- their content is
byte-for-byte what was tested and run against real data before the
restart, but they have NOT been re-verified against live data since
recovery. Re-run them once `rows_canonical.jsonl` exists again and
confirm the numbers in `backtest/disagreement_priority1_2_3_2026-08-25.md`
reproduce before extending that work further.**

If you resume and PID 8145 is gone with no completion message in
`backtest/_backfill_rebuild.log`, check `ps -p 8145` first, then check for
a fresher `backtest/rows_canonical_rebuild.jsonl` -- the process may have
finished (or died) without this file being updated with that news.

## Branch / HEAD

- Branch: `claude/gridiron-continuation-dvaljm` at `69f86520` (this
  session's HEAD before the restart -- confirmed intact via
  `git fetch`/fast-forward after the restart). `f7c120a3` is an earlier
  commit in this same branch's history (an earlier segment of this
  session) -- it's the most recent commit anywhere in this branch's
  ancestry that touched scoring code, used above only to confirm scoring
  logic hasn't changed since. Fully pushed once this HANDOFF_STATUS.md
  update + the recreated disagreement files are committed. Clean working
  tree otherwise.
- PR #64 (Weston fix + provenance validator) MERGED, sha `1ead2fb1`.
- PR #65 (event-targeted observer upgrade + on_1b fix) MERGED, sha `610cfe17`.
- No open PRs. Everything on this branch this update is already on `main`.
- Repo has moved to `werriesjacob1-cmyk/Full-Count` (GitHub redirects
  PROJECT-GRIDIRON pushes there).

## CANONICAL HISTORY IS NOW LIVE — the main blocker is CLEARED

- **Main backfill FINISHED**: PID 3663 completed 2026-08-25T05:00:47Z. All
  821 intended calendar dates processed (578 real game dates "ok", 243
  correctly `no_games` -- offseason/All-Star break), zero gaps, zero
  unexplained failures. Console leakage-check did NOT fire (all per-market
  hit rates in the sane 50-65%-ish band).
- **Canonical reconciliation RUN**: `backtest/build_canonical_backtest.py`
  produced `backtest/rows_canonical.jsonl` -- 1,027,462 rows, 578 dates,
  2024-04-01..2026-06-30, **single-regime PROVENANCE CHECK: PASS**
  (`code_git_sha=6b748538` throughout; the known-bad pre-919456e5 portion
  of the main backfill was correctly dropped in favor of the repair file
  for 2024-04-01..2025-02-26). `rows_canonical.jsonl` itself is gitignored
  (`backtest/*.jsonl`) -- regenerate any time via that script, fully
  deterministic and idempotent.
- **Control baseline RUN**: `backtest/canonical_baseline_report.py` against
  real `rows_canonical.jsonl` for the first time. Full real numbers
  persisted at `backtest/canonical_baseline_2026-08-25.md` (NOT
  gitignored, read this for the real headline numbers). Key result: at
  `predicted_prob >= 0.60` (generate_picks.py's own MIN_LINE_PROB floor,
  a RECONSTRUCTED proxy for board eligibility, not real eligibility),
  141,998 rows realize a **66.39% hit rate** -- inside the board's
  intended 60-80% band, at real multi-year scale, single clean regime.
  Probability-bucket calibration is close to monotonic across 1M+ rows.
- **Priority 6 DONE** (previous directive's numbering): same-nominal-
  probability subgroup trustworthiness analysis.
  `backtest/prob_subgroup_trust_report.py` (11 tests). Persisted:
  `backtest/priority6_subgroup_trust_2026-08-25.md`. Headline: opportunity
  shortfall (`fair_test=False`, low `actual_pa`) is the dominant source of
  within-probability-bucket variance. Both fields are POSTGAME-only,
  motivating the opportunity-modeling phase below.
- **Opportunity-modeling phase (new directive, 2026-08-25 continuation)
  Priority 1 DONE**: full decomposition of the opportunity finding before
  modeling. `backtest/opportunity_decomposition.py` (21 tests). Persisted:
  `backtest/priority1_opportunity_decomposition_2026-08-25.md`. KEY
  DISCOVERY: `signals.lineup_slot` (89% of rows) is an invertible,
  PREGAME-KNOWABLE encoding of real batting order
  (`generate_picks.py:1379`). Load-bearing result: within nearly every
  0.05 probability bucket 0.05-0.80, batting order STILL separates
  realized hit rate (top_1_3 > mid_4_6 > bottom_7_9), pooled and
  per-market, stable across all 3 years (~8pp gap each year) --
  `predicted_prob` does not fully absorb what batting order carries.
- **Priorities 2/3/4 DONE**: PA distribution model + challenger
  probability + the equal-volume test.
  `backtest/pa_opportunity_model.py` (19 tests). Persisted:
  `backtest/priority2_3_4_pa_opportunity_model_2026-08-25.md`. Empirical
  `P(actual_pa|order)` fit on 2024-2025, evaluated STRICTLY on 2026
  holdout. Two results: (a) within-bucket discrimination is real (+4-7pp
  across every populated bucket 0.40-0.70) but (b) **the equal-volume test
  -- the one that actually matters -- shows only +0.22pp net gain at fixed
  volume, and added-pick hit rate (62.50%) is statistically
  indistinguishable from removed-pick hit rate (62.04%). Does NOT meet the
  promotion bar.** Root cause identified: `generate_picks.py:1379/1386`
  already feeds batting order into `score_batter`'s CONTEXT component --
  order is not new information to the current model, so an order-only
  challenger mostly rediscovers what's already priced in. Reported
  honestly as marginal, not spun as a win. **This challenger does NOT earn
  shadow testing.**
- **Residual-opportunity phase (new continuation directive, 2026-08-25)
  Priority 1/2 DONE**: clean target definition
  (`is_shortfall = (actual_pa - E[actual_pa|order]) <= -1.0`) +
  decomposition. Persisted: `backtest/residual_priority1_2_2026-08-25.md`.
  Real bug found and fixed BEFORE publishing any conclusion:
  `generate_picks.py:1891` stores `getaway_day` as -2 (true)/0 (false),
  not a 0/1 flag -- an initial check silently matched zero real rows;
  fixed, regression test added. **Two real, independent, year-stable
  residual predictors found beyond order**: `days_rest` (0 days rest 8.56%
  shortfall -> 4+ days rest 13.73%) and `getaway_day` (12.52% vs 8.86%),
  both holding in every order slot and (mostly) every probability bucket.
  `consecutive_games` (10+ streak) is real but in the OPPOSITE direction
  (a role-stability/reliability signal, not fatigue risk).
- **Priority 3/4/5 DONE -- thread CLOSED**:
  `backtest/residual_challenger_model.py` (11 tests). Joint (order +
  days_rest + getaway_day) empirical PA distribution, same strict
  train(2024-2025)/holdout(2026) discipline. Persisted:
  `backtest/priority3_4_5_residual_challenger_closure_2026-08-25.md`.
  Result: directionally slightly better than the order-only challenger
  (equal-volume net gain +0.34pp vs +0.22pp) but **NOT statistically
  distinguishable from noise** (two-proportion z-test on added-vs-removed
  picks: z=0.80, p≈0.42). Per the directive's own explicit closure
  criteria: **"OPPORTUNITY SHORTFALL IS A REAL OUTCOME MECHANISM BUT IS
  ALREADY SUFFICIENTLY PRICED INTO CURRENT SELECTION FOR PRACTICAL
  PURPOSES." The opportunity-selection thread is CLOSED.** Do not reopen
  without materially new evidence (a pitcher-workload analogue is a
  distinct mechanism, not covered by this closure).
- **NEXT**: per the directive's own instruction on closure, move directly
  to model/context disagreement (does disagreement add independent
  trustworthiness signal now that opportunity is ruled out as the
  dominant explanation), market specialization (extend beyond `hits`),
  and shrinkage-strength audit. Fragility (as originally scoped, tied to
  opportunity) is effectively superseded by this closure -- a future
  fragility metric would need to be grounded in whatever DOES survive as
  a real trustworthiness dimension, not re-derived from the now-closed
  opportunity mechanism.

## CLOSED work this session — do not redo

1. **H+R+RBI stable-lift** merged, PR #62, sha `6c2ce0d6`. Scope:
   hits_runs_rbis Lean gate ONLY.
2. **Live-freshness watchdog + dashboard-live.yml timeout bump** merged,
   PR #63, sha `d4aaec8e`.
3. **Fail-fast provenance/regime validator**: `backtest/provenance.py`,
   wired into `build_canonical_backtest.py` as a real gate.
4. **Weston Wilson stale-explanation bug fixed, MERGED, live-verified**
   (`1ead2fb1`). Fixed via `FROZEN_PUBLICATION_FIELDS` allowlist. Honest,
   PERMANENT boundary case documented: the original Weston registry entry
   still shows pre-fix text because `bettable_games(allow_started=False)`
   means no rebuild ever re-scores an already-started game — not a bug,
   not something to patch with a historical-registry rewrite (forbidden).
5. **FanDuel passive observer CLOSED** — zero real odds/line changes,
   ~85 min, 96-100 markets.
6. **Event-targeted observer built, run to completion, upgraded, MERGED**
   (`610cfe17`). Full run: 27 triggers, 233 polls, 0 endpoint failures, 58
   status_change events (30 suspend/28 reopen) bound tightly to inning
   transitions specifically — reads as routine market rollover, not a
   leverage reaction. **Still zero confirmed odds/line changes across 3
   runs, ~130 min total observation.** Full writeup:
   `backtest/fanduel_observer_final_report.md`.
7. **Priority D runtime bottleneck FOUND + FIXED + forward-verified twice**:
   `47a75920` (parallelized `fetch_game_contexts()`). Forward proof #1: a
   manually-recovered run completed in 35s during a later, UNRELATED
   scheduling incident (see #8). Runtime is not the open question anymore.
8. **Scheduling-delivery incident investigated, partially resolved, real
   architecture built for the rest**: `dashboard-live.yml` AND its
   independent watchdog both went silent on their own 5-minute crons
   simultaneously (~03:29-04:19 UTC, ~50 min). Zero queued/in-progress
   runs during the gap (ruled out capacity — this was trigger-DELIVERY).
   Manual dispatch restored freshness (35s) but did NOT restore the
   recurring schedule for ~50 minutes; **a real scheduled run (`event:
   schedule`) DID resume on its own at 04:19:16 UTC** (10 min after the
   manual dispatch) — the incident appears to have cleared on its own by
   then, though the exact mechanism (GitHub-side congestion vs. something
   the manual dispatch indirectly triggered) was never conclusively
   identified and is NOT re-investigatable now (evidence window has
   passed). Full timeline: `backtest/live_incident_2026-08-25_0329.md`.
   Architecture recommendation: `backtest/independent_recovery_design_2026-08-25.md`
   (Option A: external heartbeat). **Built the actual Option A
   implementation**: `ops/external_heartbeat/` — a Cloudflare Worker
   (`worker.js`), 9 passing tests on synthetic timestamps
   (`test_worker.mjs`, zero network/credentials), deployment config
   (`wrangler.toml`), and a README documenting the exact 7 one-time user
   actions needed to deploy (Cloudflare account, wrangler auth, KV
   namespace, least-privilege GitHub token, deploy, dry-run verify,
   go-live). **NOT deployed** — stops at the credential/account boundary
   per the standing instruction; needs the user to actually create the
   Cloudflare account and GitHub token, since those are real credentials
   this session cannot safely create on its own.
9. **Candidate-level decision dataset: feasibility analysis + reusable
   builder**: `backtest/candidate_dataset_feasibility_2026-08-25.md` +
   `backtest/candidate_dataset.py` (14 tests). Key finding: Priority 5-style
   within-slate pairwise selection can't be answered from
   `rows_canonical.jsonl` as currently produced (one candidate per batter).
10. **Prospective full-candidate research funnel logger — built AND
    LIVE-VALIDATED**: `backtest/candidate_funnel_logger.py` (19 tests).
    Discovered `generate_picks.py` already computes and discards the full
    alternate-line curve per batter via `_keep_options()`/`line_options`
    ("THE MODEL ALREADY COMPUTES THIS AND THREW IT AWAY" — that function's
    own docstring) — this logger persists what's already computed, no new
    scoring logic. Mirrors `dashboard/build_dashboard.py`'s
    `run_live_fetch()` isolation pattern exactly (own scratch OUTPUT_DIR,
    cannot touch production). **Ran for real against tonight's actual
    slate**: 969 real candidates captured, 297 with 2+ alternate lines, 906
    correctly flagged `assumed_lineup` (exact match to generate_picks.py's
    own console output), gate traces on all 969. Confirmed live that
    `output/` was untouched and the new `.jsonl` file correctly matches the
    `backtest/*.jsonl` gitignore rule. **Honest gap**: market prices aren't
    attached yet (every record's `market` section is null this run) — a
    real, scoped, documented follow-up, not hidden.
11. **Candidate funnel lifecycle CLOSED — outcome-join grader built**:
    `backtest/candidate_funnel_grader.py` (13 tests). Reduces the append-only
    funnel changelog to the latest record per `candidate_id`, reconstructs a
    `grade_results.grade_pick()`-compatible pick from each record's identity
    section, grades EVERY candidate (kept/rejected/assumed_lineup alike, not
    just selected ones) with the real `grade_results.fetch_game_contexts()`/
    `grade_pick()` (never reimplemented), writes outcomes to a SEPARATE
    `candidate_funnel_outcomes_{date}.jsonl` keyed by `candidate_id` — the
    pregame file is never touched. All 6 lifecycle sub-items now complete
    (identity, snapshot semantics, outcome join, decision trace, storage
    discipline, non-mutation). Also added `"type"` to the logger's identity
    section (needed since `grade_pick()` reads `pick["type"]` via direct
    bracket access). Not yet run against a real graded slate (built same day
    the funnel logger's first live data was still same-day/ungradeable) —
    next live slate is the first real end-to-end validation opportunity.
12. **CANONICAL HISTORY BUILT + CONTROL BASELINE RUN — see the new section
    above.** This is the single biggest state change this session: the
    long-standing blocker is gone and Track A accuracy research is
    unblocked for the first time.

## OPEN THREADS

### Track A historical accuracy research — UNBLOCKED, now the top priority
Canonical history + control baseline are both done (see section above).
Next: same-nominal-probability subgroup trustworthiness analysis (Priority
6 — "which 65% predictions are actually trustworthy?", controlling for the
probability the model already believes, segmented by
market/support-bucket/probability-basis/etc.), then fragility (Priority 7,
empirical forecast-error perturbations, never invented values), model
disagreement (Priority 8), market specialization (Priority 9), shrinkage
audit (Priority 10, ONLY where current math has a tunable shrinkage —
do not revisit the closed H+R+RBI stable-lift decision), opportunity/PA
modeling (Priority 11), then prospective policy research design (Priority
12, Champion vs Shadows A-F, frozen pregame selections, never touching the
public board). Full question design already exists in this session's own
instruction history (the directive that opened this segment). If you resume
and find partial analysis code/output with no persisted `.md` writeup,
finish persisting it before trusting or extending it — an unpersisted
terminal-only finding does not count as done per this project's own
interruption-safety rule.

Track B (prospective, using `candidate_funnel_logger.py` +
`candidate_funnel_grader.py`) can keep accumulating independently of Track
A — not blocked, just needs many days of real logged-and-graded slates
before it has enough volume to analyze on its own. Run the logger again on
every live slate with real pregame runway; the grader can be run the
morning after once games are final.

### NOT blocked, but correctly deferred tonight (do not manufacture)
- **Priority 12 (narrower FanDuel player-prop experiment)**: checked for a
  live game at ~04:53 UTC — only ONE live game exists (the same
  Pirates@Padres game already tracked extensively), and it's genuinely in
  the 9th inning, tied 2-2, likely ending soon or heading to extras. Not
  enough runway to properly set up a deliberate, well-targeted player-prop
  experiment (confirm the target prop is posted, identify its stable
  market/runner id, THEN watch a real trigger window) per the priority's
  own explicit setup requirement. **Next actionable step**: run this the
  moment a fresh slate's games are live with real time to prepare (ideally
  a few hours before first pitch on the next slate) — target actual
  player-prop markets (hit/TB/HR, K/outs), not the inning-special market
  that dominated the last run.
- **Candidate funnel logger's market-price gap**: scoped, documented,
  not built tonight — see item 10 above.
- **`dashboard-live.yml`'s scheduling-gap root mechanism**: cleared on its
  own; the evidence window to diagnose WHY has passed. If it recurs, the
  external heartbeat (once deployed) will both recover it faster AND
  produce independent KV-logged evidence of exactly when/how often it
  happens — a much better position than trying to diagnose retroactively
  from GitHub's own run history again.

## How to resume

1. `git status` / `git log --oneline -5` to confirm you're at `5f714294`
   or later, clean, matching `origin/main`.
2. Canonical history is DONE (see section above) — `rows_canonical.jsonl`
   is gitignored so it won't exist on a fresh clone; regenerate via
   `python3 backtest/build_canonical_backtest.py` (fast, deterministic,
   reads the also-gitignored `rows_backfill.jsonl`/`rows_backfill_repair.jsonl`
   — if THOSE are also missing on a fresh checkout, the backfill would need
   re-running, which is slow; check before assuming it's a quick regenerate).
   The real numbers from the one real run are permanently captured in
   `backtest/canonical_baseline_2026-08-25.md` regardless.
3. Track A accuracy research (Priority 6 onward) is the top priority now —
   see OPEN THREADS above for the full sequence. Do not skip ahead to
   fragility/disagreement/market-specialization before Priority 6's
   subgroup trustworthiness analysis exists, per the standing priority
   ordering.
4. If a fresh MLB slate is live with real pregame runway, also: (a) run
   `backtest/candidate_funnel_logger.py` again to keep building Track B's
   prospective dataset, (b) the morning after, run
   `backtest/candidate_funnel_grader.py <date>` to grade that day's funnel,
   and (c) consider Priority 12's targeted player-prop FanDuel experiment
   (deferred every night so far — see OPEN THREADS above).
5. If ops/external_heartbeat/ hasn't been deployed yet and the user wants
   to proceed, walk them through README.md's 7 steps (needs their own
   Cloudflare account + GitHub token — cannot be done by an agent alone).
6. Run `for f in test_*.py; do /tmp/mlbvenv/bin/python3 "$f" || echo "FAIL: $f"; done`
   before every commit. Fully green as of `5f714294`. (The
   `ops/external_heartbeat/` JS tests are separate: `node
   ops/external_heartbeat/test_worker.mjs`, also passing.)

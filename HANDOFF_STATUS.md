# Full Count — session handoff status

Last updated: 2026-08-25 ~03:30 UTC by Claude (this session).
Purpose: let a fresh session resume with zero hidden chat context.

## Branch / HEAD

- Branch: `claude/gridiron-continuation-dvaljm`
- HEAD at last checkpoint: `6b344276` "Fix Weston Wilson stale-explanation bug: stop freezing why/watchouts"
- `main` is at merge SHA `d4aaec8e` (live-freshness watchdog + timeout bump) as of last sync;
  `claude/gridiron-continuation-dvaljm` has since added the Weston fix and repair-vs-main
  reconciliation tooling not yet merged to `main`.
- Repo has moved to `werriesjacob1-cmyk/Full-Count` (GitHub redirects PROJECT-GRIDIRON pushes there).

## Long-running background job (DO NOT kill/restart)

- **Main backfill**: PID 3663, `backtest/engine.py --start 2024-04-01 --end 2026-06-30
  --out backtest/rows_backfill.jsonl --no-weather`. Started 2026-08-24 21:53:13 UTC.
  Check with `ps -p 3663 -o pid,etime,cmd` and `tail backtest/_backfill_run.log`.
  At last check: ~5.5h elapsed, healthy, no action needed until it completes.
  **When it completes**: verify 821/821 intended dates, no gaps, inspect failures,
  then run `backtest/build_canonical_backtest.py` (already written, see below) to
  produce `backtest/rows_canonical.jsonl`. Do NOT launch another giant backfill after.

- **Repair backfill**: already completed earlier this session. 400,207 rows,
  211/211 dates, 2024-04-01..2025-02-26, no gaps. File: `backtest/rows_backfill_repair.jsonl`.

## CLOSED work this session — do not redo

1. **H+R+RBI stable-lift** merged to main, PR #62, sha `6c2ce0d6`. Real replay:
   current 64.6% (n=12,779) vs challenger 69.9% (n=8,305); added-picks 73.5% vs
   removed-picks 61.2%. Positive in every year/season-phase bucket with data.
   Scope: hits_runs_rbis Lean gate ONLY. Do not extend to runs/rbis (shadow-only,
   history too thin) or add a Top Pick lift gate. Do not touch the shrinkage prior.
2. **Live-freshness watchdog + dashboard-live.yml timeout bump** merged to main,
   PR #63, sha `d4aaec8e`. `.github/workflows/live-freshness-watchdog.yml` (new,
   independent 5-min cron, dispatches `dashboard-live.yml` via `workflow_dispatch`
   if `docs/live.json`'s `updated_at` exceeds 15 min, never writes live.json itself).
   `dashboard/check_live_freshness.py` is the tested SLA logic.
   **STATUS: shipped but NOT YET live-proven** — as of last check, zero real
   scheduled runs of `live-freshness-watchdog.yml` had fired yet (check via
   `mcp__github__actions_list` `list_workflow_runs` on `live-freshness-watchdog.yml`).
   Check opportunistically, don't block on it.
3. **12 repair-vs-main row-count discrepancies root-caused** (not yet merged to
   main — currently only on this branch). Real cause: `code_git_sha` proves the
   main backfill's early portion (2024-04-01..2025-02-26, tag `c182b186`) ran
   BEFORE commit `919456e5` fixed a real `backtest/engine.py` bug that silently
   wrote `predicted_prob=None` for 7 of 13 markets (home_run/total_bases/singles/
   doubles/triples/runs/rbis). The repair backfill and the main backfill's later
   portion (`6b748538`, 2025-02-27 onward) both postdate that fix. Tools written:
   `backtest/reconcile_repair_vs_main.py` (the diagnosis script) and
   `backtest/build_canonical_backtest.py` (stitches repair ≤2025-02-26 + main
   >2025-02-26 into `backtest/rows_canonical.jsonl`, verifies every kept row's
   `code_git_sha` before writing, aborts on an unexpected regime). **Confirmed
   NOT to affect** `backtest/rows.jsonl` (used for the stable-lift merge above) —
   that file predates `code_git_sha` tracking entirely and empirically has zero
   nulled rows for hits_runs_rbis/runs/rbis.
4. **Weston Wilson stale-explanation bug fixed**, commit `6b344276` (this branch,
   not yet merged to main). Root cause: `dashboard/build_dashboard.py`'s
   `reconcile_public_lifecycle()` did a wholesale `row = frozen` / `{**frozen,...}`
   once a game started, and `dashboard/static/app.js`'s `freezePublishedSnapshot()`
   did the same client-side — both blindly copied the ENTIRE first-publication
   registry snapshot onto the live row, including `why`/`watchouts`, which are
   pure presentation and should always reflect the CURRENT generator. Fixed via
   a new explicit allowlist, `dashboard/live_state.py`'s `FROZEN_PUBLICATION_FIELDS`
   (audit/settlement-critical fields only — probability, CI, odds, edge, status,
   reliability, lineup state, identity — deliberately excludes why/watchouts).
   Both freeze sites now overlay only that allowlist onto the current row instead
   of replacing it wholesale. Registry's own immutable snapshot is untouched
   (why/watchouts stay there for real audit history). Verified via sign-reversal:
   both new tests (`test_live_lifecycle.py::test_why_watchouts_reflect_current_generator_not_stale_first_publication`,
   `test_frontend_lifecycle.py::test_why_watchouts_do_not_regress_to_stale_first_publication_snapshot`)
   fail against the pre-fix code and pass against the fix. Full suite green.
   **NEXT STEP: open a PR from this branch to main and merge once CI is green**
   (this qualifies for standing merge authorization — presentation-only fix,
   no probability/ranking/recommendation math touched, real regression tests).

## OPEN / IN-PROGRESS threads (priority order per user's last message)

### A. Canonical backtest dataset (blocked on main backfill completion)
- Reconciliation rule and tooling done (see #3 above). NOT YET RUN to completion
  because main backfill isn't done. Once it is: run
  `/tmp/mlbvenv/bin/python3 backtest/build_canonical_backtest.py`, verify output,
  then use `rows_canonical.jsonl` (not `rows_backfill.jsonl` alone) for any future
  accuracy experiment on this date range.
- **Still needed, not yet built**: a permanent fail-fast provenance/regime
  validator for backtest tooling in general (not just this one-off script) —
  should inspect `code_git_sha` (exists on every row already) plus
  `model_version`/`feature_version`/`calibration_version`/`recommendation policy
  version`/data-source version where available, report discovered regimes +
  row counts + date ranges per regime, and fail closed on an unannounced mix
  unless the caller explicitly opts into a regime-comparison analysis. This was
  the next task queued when this handoff was written — not started yet.

### B. Weston/publication-snapshot bug — DONE, needs merge (see #4 above)

### C. Live freshness / scheduler architecture
- Watchdog shipped (see #2), not yet live-proven (see #2).
- **NOT YET DONE**: full inventory of all cron-triggered workflows (name,
  cadence, avg duration, concurrency group, writer targets, whether it commits/
  deploys) to test the "repo-wide scheduled-workflow congestion" hypothesis
  (real evidence already gathered this session: Lineup Watch's own 10-min cron
  showed the same 20-90 min irregular gaps ALL DAY on 2026-08-24, not just during
  the Dashboard Live Update incident window — strongly suggests total scheduled-
  trigger volume across this account is the real mechanism, not a bug isolated
  to one workflow). Workflows known to exist (from `mcp__github__actions_list`
  `list_workflows`): Calibration Recheck, Dashboard Pages Deploy, Dashboard Live
  Update (`*/5`), Dashboard Refresh, Lineup Watch (`*/10`), MLB Daily Pipeline,
  Odds Snapshot, Test Suite, plus the new Live Freshness Watchdog (`2-59/5`).
  Exact cadences for Calibration Recheck/Dashboard Refresh/MLB Daily
  Pipeline/Odds Snapshot not yet pulled from their YAML files.
- **NOT YET DONE**: a measured consolidation/reduction proposal (fewer
  independent 5-10 min crons, one orchestrator, shared fetch work) — explicitly
  do NOT consolidate blindly; measure first.

### D. dashboard-live.yml runtime growth (15min -> 25min was a patch, not a fix)
- **NOT YET DONE AT ALL.** Real evidence already in hand: run #267's own job log
  (2026-08-24 22:59 UTC) showed "Grade published Top Picks" taking 7m34s and
  "Reprice pregame candidates" cancelled mid-step at 7m31s in — vs. 2m38s +
  2m41s = 5m19s measured 5 days earlier (2026-08-20). Need: a real timing
  profile (grading vs repricing vs MLB boxscore calls vs FanDuel requests vs
  merge/serialization vs git ops), broken out with percentages, to find why
  runtime roughly tripled in 5 days. Do NOT fix by bumping the timeout again.

### E. FanDuel observer — redesign, not another passive run
- Two full passive runs this session (a ~25min run and an ~82.6min run) produced
  **zero real odds/line changes** across 96 unique (event, market) pairs, 3
  games, ~5989 open-market-minutes, 346 polls, 154 suspends/132 reopens, 43
  migrations. Both runs' logs are at `backtest/fanduel_observer_log.jsonl` (first
  run) and `backtest/fanduel_observer_log2.jsonl` (second, longer run — the one
  with the full stats above). **NOT YET WRITTEN**: a persisted final honest
  report file with these exposure numbers (currently only stated in chat, per
  the interruption-safety instruction this needs to live in the repo).
  Conclusion to state explicitly: these specific observed windows produced no
  confirmed repricing — NOT "FanDuel never reprices."
- **NOT YET STARTED**: the event-targeted redesign (detect MLB high-leverage
  state transitions — pitcher change, scoring play, HR, inning transition —
  then temporarily raise polling cadence only for that game/market set,
  tracking stable market/runner IDs across tab migrations). This was the
  explicit next task queued when this handoff was written — not started yet.
  Existing building blocks to reuse: `backtest/fanduel_live_observer.py` (the
  passive observer, has the migration-tracking/suspend-reopen logic already),
  `backtest/alive_brain_prototype.py` (has `props_for_current_matchup()` for
  matching current batter/pitcher to FanDuel runner names).

### F. Alive-brain direction
- No new work needed beyond what's already documented in
  `backtest/alive_brain_design.md`. Next real proof requires the event-targeted
  observer from (E) to produce one real captured price/line change with full
  before/after detail. Do not deploy Cloudflare. Do not overstate current
  latency numbers as production-readiness.

### G. Forward validation of stable-lift
- No action needed right now — just don't touch the policy again on tiny
  samples. Registry already captures `base_rate`/`lift`/`lift_reference_rate`/
  `stable_lift` going forward (additive, does not rewrite old entries).

## How to resume

1. `git status` / `git log --oneline -5` to confirm you're where this file says.
2. `ps -p 3663` to check the main backfill (see PID above); if it's gone, check
   whether it completed (`tail backtest/_backfill_run.log`) or died unexpectedly.
3. Check whether commit `6b344276` (Weston fix) has been merged to `main` yet
   (`git log origin/main --oneline -5` after `git fetch origin main`). If not,
   open a PR (`claude/gridiron-continuation-dvaljm` -> `main`) and merge once
   CI is green — it already qualifies under standing authorization.
4. Pick up Priority A's provenance/regime validator (not started) or Priority
   E's event-targeted FanDuel observer redesign (not started) — both were
   explicitly queued as "use the wait time productively" while the backfill runs.
5. Run `for f in test_*.py; do /tmp/mlbvenv/bin/python3 "$f" || echo "FAIL: $f"; done`
   before every commit. It was fully green as of `6b344276`.

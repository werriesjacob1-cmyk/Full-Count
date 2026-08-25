# Full Count — session handoff status

Last updated: 2026-08-25 ~03:55 UTC by Claude (this session).
Purpose: let a fresh session resume with zero hidden chat context.

## Branch / HEAD

- Branch: `claude/gridiron-continuation-dvaljm`
- HEAD at last checkpoint: `47a75920` "Fix Priority D bottleneck: parallelize per-game MLB feed fetches"
- `main` is at merge SHA `d4aaec8e` (live-freshness watchdog + timeout bump) as of last sync.
  This branch has since added, on top of `main`: Weston stale-explanation fix + provenance
  validator (PR #64, open, CI in progress as of this update), FanDuel passive-observer final
  report, event-targeted FanDuel observer (redesign), and the Priority D runtime-bottleneck
  fix (this update). Only PR #64 exists so far — the rest can ride a follow-up PR once ready,
  or be added to #64 before merge.
- Repo has moved to `werriesjacob1-cmyk/Full-Count` (GitHub redirects PROJECT-GRIDIRON pushes there).

## Long-running background jobs (DO NOT kill/restart)

- **Event-targeted FanDuel observer**: PID 1720, `backtest/event_targeted_observer.py
  --game-pk 823260 --event-id 35973137 --minutes 45 --log
  backtest/event_targeted_observer_log.jsonl`. Started 2026-08-25 ~03:35 UTC against a REAL
  live game (Pirates @ Padres). As of ~03:48 UTC (13 min in): 7 real triggers detected, 4
  status_change (suspend/reopen) events on one "Specials Top 7th" market, but **zero
  odds_change/line_change events yet** — check with `ps -p 1720` and
  `grep '"kind": "\(odds_change\|line_change\)"' backtest/event_targeted_observer_log.jsonl`.
  When it finishes: if still zero, update `backtest/fanduel_observer_final_report.md` with
  these numbers too (still an honest null, third run in a row) rather than discarding them.
  If a real odds/line change ever lands inside a burst window, report it with full before/after
  detail (old/new odds, old/new line, previousWinRunnerOdds, suspend/reopen timestamps, bounded
  open-duration) — that would be the first confirmed capture this whole investigation has
  been trying to get.

- **Main backfill**: PID 3663, `backtest/engine.py --start 2024-04-01 --end 2026-06-30
  --out backtest/rows_backfill.jsonl --no-weather`. Started 2026-08-24 21:53:13 UTC.
  Check with `ps -p 3663 -o pid,etime,cmd` and `tail backtest/_backfill_run.log`.
  At last check: ~6h elapsed, healthy, no action needed until it completes.
  **When it completes**: verify 821/821 intended dates, no gaps, inspect failures,
  then run `backtest/build_canonical_backtest.py` (already written and tested — see below)
  to produce `backtest/rows_canonical.jsonl`. It already calls `provenance.require_single_regime()`
  internally and will fail loudly (not silently) if anything unexpected is mixed in.
  Do NOT launch another giant backfill after.

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
   **STATUS: shipped, still NOT confirmed to have fired yet** — check opportunistically
   via `mcp__github__actions_list` `list_workflow_runs` on `live-freshness-watchdog.yml`.
   Note: dashboard-live.yml's own 23-run cancellation streak on 2026-08-24 (see item 5
   below) self-recovered via a MANUAL `workflow_dispatch` at 23:29 UTC, not via this
   watchdog — worth checking whether the watchdog ever actually tried during that window.
3. **12 repair-vs-main row-count discrepancies root-caused**, tooling built (not yet
   run to completion — waiting on main backfill). Real cause: `code_git_sha` proves the
   main backfill's early portion (tag `c182b186`) predates commit `919456e5`, which fixed
   a real `backtest/engine.py` bug nulling `predicted_prob` for 7/13 markets. Tools:
   `backtest/reconcile_repair_vs_main.py` (diagnosis), `backtest/build_canonical_backtest.py`
   (stitches + verifies via provenance.py, writes `rows_canonical.jsonl`). Confirmed NOT
   to affect `backtest/rows.jsonl` (used for the #1 stable-lift merge).
4. **Fail-fast provenance/regime validator built**: `backtest/provenance.py`
   (`inspect_regimes`, `require_single_regime`, `MixedRegimeError`, CLI). Detects
   `code_git_sha` (+ any future `model_version`/`feature_version`/etc.) mixing in a
   backtest dataset, fails closed unless `allow_multi=True`. 8 tests in
   `test_backtest_provenance.py`, all passing. Wired into `build_canonical_backtest.py`
   as a real gate (proven against real data: fails on the known-mixed file, passes on
   clean ones). This is the "foundational integrity guardrail" the user asked for.
5. **Weston Wilson stale-explanation bug fixed**, commit `6b344276` (this branch,
   PR #64 open, not yet merged). Root cause: `dashboard/build_dashboard.py`'s
   `reconcile_public_lifecycle()` and `dashboard/static/app.js`'s
   `freezePublishedSnapshot()` both did a wholesale copy of the ENTIRE first-publication
   registry snapshot onto the live row once a game started, including `why`/`watchouts`
   (pure presentation, should always reflect the CURRENT generator). Fixed via a new
   explicit allowlist, `dashboard/live_state.py`'s `FROZEN_PUBLICATION_FIELDS`
   (audit/settlement-critical fields only — deliberately excludes why/watchouts).
   Registry's own immutable snapshot is untouched (why/watchouts stay there for audit).
   Verified via sign-reversal (2 new tests, server + client side). Full suite green.
   **NEXT STEP: merge PR #64 once CI is green** (qualifies under standing authorization).
6. **FanDuel passive observer wrapped with a persisted final report**:
   `backtest/fanduel_observer_final_report.md`. Two runs (2.1min smoke test + 82.6min
   real run), **zero real odds/line changes** across 96-100 unique (event, market)
   pairs, 3-4 games, ~5,989 open-market-minutes. Honest conclusion: these specific
   windows produced no confirmed repricing — NOT "FanDuel never reprices." Includes an
   explicit caveat that the first run's original ~25min log no longer exists on disk
   under that filename (overwritten by a later smoke test); only what's actually on
   disk is reported.
7. **Event-targeted FanDuel observer built** (the redesign): `backtest/event_targeted_observer.py`.
   Detects real MLB triggers (pitcher change, scoring play, inning transition, batter
   change) via `fetch_mlb_state()`, then bursts FanDuel polling only around a detected
   trigger (8s cadence, bounded 10 polls, aborts on failure), else holds a slow 25s
   baseline. 17 tests in `test_event_targeted_observer.py`, all passing, no network calls.
   Live-smoke-tested against a real game. A real 45-min live run is in progress (see
   background jobs above) — first live evidence: 7 real triggers, 4 status_change
   (suspend/reopen) events on one market, still zero odds/line changes as of this update.
8. **Priority D runtime-growth root cause found + fixed**: see `backtest/runtime_profile_2026-08-25.md`
   for full evidence. Real data pulled directly from `list_workflow_jobs`/`list_workflow_runs`,
   not estimated: dashboard-live.yml runs #245-267 (2026-08-24 07:41-22:59 UTC) were **23
   CONSECUTIVE CANCELLED SCHEDULED RUNS** — not a gradual creep, a sustained ~15h binary
   failure (each ~20min, hitting timeout). Root cause: `grade_results.fetch_game_contexts()`
   fetched every distinct game's MLB feed SEQUENTIALLY (with its own 20s-timeout/2-retry
   backoff), called 3x per 5-min cycle (once from grading, twice from repricing) — real MLB
   Stats API slowness that day got multiplied by N games x 3 calls instead of absorbed
   (population is small, only 7 distinct game_pks — this is NOT a prop-count-growth story).
   Fixed by parallelizing that fetch with a bounded ThreadPoolExecutor (8 workers) — same
   requests/cache/return-shape, just concurrent. Verified via sign-reversal
   (`test_fetch_game_contexts_concurrency.py`: fails against old sequential code with real
   timing, passes against the fix). Full suite green. Committed `47a75920`, pushed. Also
   updated `dashboard-live.yml`'s own timeout-minutes comment to record the real root cause
   (left `timeout-minutes: 25` unchanged as headroom — the fix removes amplification, not
   MLB's own latency, so keep the margin until a future slow-MLB window proves fast recovery).
   Recovery for that specific incident was a MANUAL `workflow_dispatch` at 23:29 UTC — after
   that, every scheduled run (#269-273) has been healthy (27s-65s).

## OPEN / IN-PROGRESS threads (priority order per user's last message)

### A. Canonical backtest dataset (blocked on main backfill completion)
- Reconciliation rule, tooling, AND provenance validator all done (see items 3-4 above).
  NOT YET RUN to completion because main backfill isn't done. Once it is: run
  `/tmp/mlbvenv/bin/python3 backtest/build_canonical_backtest.py`, verify output
  (it self-verifies via provenance.py and will print PASS/FAIL), then use
  `rows_canonical.jsonl` (not `rows_backfill.jsonl` alone) for any future accuracy
  experiment on this date range. Do NOT launch another giant backfill after.

### B. Weston/publication-snapshot bug — DONE, needs merge (see item 5 above)

### C. Live freshness / scheduler architecture
- Watchdog shipped (item 2), still not confirmed to have fired live.
- **NOT YET DONE**: full inventory of all cron-triggered workflows (name, cadence, avg
  duration, concurrency group, writer targets) to test the "repo-wide scheduled-workflow
  congestion" hypothesis. Real evidence in hand supporting it: Lineup Watch's own 10-min
  cron showed similar irregular gaps the same day. Workflows known to exist (from
  `list_workflows`): Calibration Recheck, Dashboard Pages Deploy, Dashboard Live Update
  (`*/5`), Dashboard Refresh, Lineup Watch (`*/10`), MLB Daily Pipeline, Odds Snapshot,
  Test Suite, Live Freshness Watchdog (`2-59/5`). Exact cadences for the others not yet
  pulled from their YAML files.
- **NOT YET DONE**: a measured consolidation/reduction proposal. Do NOT consolidate blindly.

### D. dashboard-live.yml runtime growth — ROOT CAUSE FOUND + FIXED (see item 8 above)
- Real fix shipped this update (`47a75920`). Remaining: watch for a live confirmation
  the next time MLB's API has a slow window (can't force this on demand) — a fast
  per-step recovery instead of a repeat of the 2026-08-24 cancellation streak is the
  live proof. Nothing else queued here unless a NEW regrowth is observed.

### E. FanDuel observer — redesign DONE, live validation in progress (see items 6-7 above)
- Passive observer closed with a persisted report. Event-targeted redesign built,
  tested, and running live (PID 1720, see background jobs). Real triggers ARE being
  detected (pitcher/scoring/inning/batter changes, and market suspend/reopen cycles
  around them) — genuinely more signal than the passive runs ever produced — but no
  confirmed odds/line change yet. Let the run finish, report the final numbers.

### F. Alive-brain direction
- No new work needed. Next real proof requires (E) to produce one real captured
  price/line change with full before/after detail. Do not deploy Cloudflare.

### G. Forward validation of stable-lift
- No action needed right now — just don't touch the policy again on tiny samples.

## How to resume

1. `git status` / `git log --oneline -5` to confirm you're at `47a75920` or later.
2. `ps -p 3663` / `ps -p 1720` to check both background jobs (see above for what to do
   when each finishes).
3. Check whether commit `6b344276`+ (PR #64) has merged to `main` yet
   (`git fetch origin main && git log origin/main --oneline -5`). If CI is green and
   it hasn't merged, merge it — qualifies under standing authorization. Consider folding
   this update's runtime-profile commit (`47a75920`) into the same PR before merging,
   or opening a quick follow-up PR for it — it's an independent, low-risk infra fix.
4. Priority C (scheduled-workflow inventory + consolidation proposal) is the largest
   fully-unstarted item left from the "use the wait time productively" list.
5. Run `for f in test_*.py; do /tmp/mlbvenv/bin/python3 "$f" || echo "FAIL: $f"; done`
   before every commit. Fully green as of `47a75920`.

# Full Count — session handoff status

Last updated: 2026-08-25 ~03:55 UTC by Claude (this session).
Purpose: let a fresh session resume with zero hidden chat context.

## Branch / HEAD

- Branch: `claude/gridiron-continuation-dvaljm`, now fast-forwarded to equal `main`
  (no divergence — PR #64 merged, branch ff'd onto the merge commit, both pushed).
- `main` is at `a1e8e8b6` "Dashboard refresh 2026-08-25 03:59 UTC" (the manually-triggered
  post-merge rebuild that live-verified the Weston fix — see item 5 below) as of last sync,
  plus this branch has ONE further unmerged commit on top: `c2064996` (event-targeted
  observer tiered-trigger upgrade — pure research tooling, no PR needed/opened yet, not
  required on main). PR #64 (Weston fix + provenance validator) is MERGED, sha `1ead2fb1`.
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
   **STATUS: fired for real the first time at 2026-08-25 03:36:09 UTC** (run #1,
   completed successfully in 9s) — "Check docs/live.json freshness" ran and passed,
   correctly SKIPPED both the recovery-dispatch and fail-visibly steps because
   live.json was genuinely fresh at that moment. Real live proof the plumbing works
   end to end on a healthy tick. NOT yet observed: an actual recovery dispatch (only
   happens if live.json goes stale again — hasn't recurred since the Priority D fix).
   Full detail in `backtest/scheduled_workflow_inventory_2026-08-25.md`. Note: the
   2026-08-24 cancellation streak (item 8) self-recovered via a MANUAL
   `workflow_dispatch`, not this watchdog — the watchdog didn't exist yet at that time
   (it was part of the same-day PR #63 that merged after the incident).
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
5. **Weston Wilson stale-explanation bug fixed and MERGED** (`1ead2fb1`, PR #64).
   **Live verification done (2026-08-25 ~04:00 UTC), with an important honest
   caveat found along the way** — see below. Root cause: `dashboard/build_dashboard.py`'s
   `reconcile_public_lifecycle()` and `dashboard/static/app.js`'s
   `freezePublishedSnapshot()` both did a wholesale copy of the ENTIRE first-publication
   registry snapshot onto the live row once a game started, including `why`/`watchouts`
   (pure presentation, should always reflect the CURRENT generator). Fixed via a new
   explicit allowlist, `dashboard/live_state.py`'s `FROZEN_PUBLICATION_FIELDS`
   (audit/settlement-critical fields only — deliberately excludes why/watchouts).
   Registry's own immutable snapshot is untouched (why/watchouts stay there for audit).
   Verified via sign-reversal (2 new tests, server + client side). Full suite green.
   **MERGED, verified via a real production reproduction, with an honest
   finding**: manually dispatched Dashboard Refresh (run #222,
   `32807093880`) after merge to force an immediate rebuild rather than
   waiting ~9h for the next scheduled slot; deployed `docs/data.json`
   confirmed clean and diff-reviewed before merge (no probability/ranking/
   recommendation logic touched -- verified via `git diff origin/main...HEAD`
   on `dashboard/build_dashboard.py`/`dashboard/live_state.py`/
   `grade_results.py` line by line). BUT: pulling the actual deployed
   Weston Wilson row post-merge still shows the exact original stale why
   text ("Platoon: R bat vs RHP (unfavorable)", bare "Opposing SP ERA 2.92"
   with no directional qualifier, bare "L7 avg EV 82.8mph" with no
   judgment). Root-caused this precisely, NOT a failure of the merged fix:
   registry `first_published_at` for this exact entry
   (`fc2:823097:player-642215:hits_runs_rbis:1:over`) is `2026-08-24T23:28:18Z`
   -- the why text's own format (bare facts, no directional suffix) proves
   it was captured by a version of `generate_picks.py` that predates the
   "2026-08-24 explanation-quality fix" comment block now in
   `generate_picks.py` (~line 1579-1619) that routes unfavorable
   platoon/elite-SP-ERA/cold-EV to watchouts with a qualifier suffix.
   Structural reason this can't retroactively self-heal: `run_live_fetch()`
   calls `gp._build_and_score()` fresh every rebuild, but
   `bettable_games(game_meta, allow_started=False)` (its default) excludes
   any game already in progress from being scored at all -- so once a game
   goes live, NO rebuild (before or after this fix) ever produces a fresh
   `row` for it again; `reconcile_public_lifecycle()`'s carry-forward path
   has nothing fresher than `registered` to draw from, so the fix's
   allowlist has no effect for that specific case (this is exactly the
   "accepted, unavoidable limitation" already noted in that function's own
   new comment). **What the fix DOES prove**: the regression tests (sign-
   reversal verified) show the mechanism is correct going forward -- any
   Top Pick published under the ALREADY-fixed generator, whose game starts
   after this point, will carry forward a CORRECT frozen snapshot (since
   the bug that produced wrong routing is gone), and any future
   presentation-only improvement to why/watchouts routing will apply live
   right up until a game's own bettable-window closes, not regress after.
   Tonight's slate had zero pregame Top Picks left to observe that
   transition live (checked -- 0 rows). **Not a new task queued** -- this
   is a structural fact about the pipeline (no live re-scoring of
   in-progress games), correctly out of scope for tonight; flagged here so
   a future session doesn't re-diagnose it from scratch, and doesn't
   mistake it for the fix having failed.
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

### B. Weston/publication-snapshot bug — DONE, merged, live-verified (see item 5 above)

### C. Live freshness / scheduler architecture — DONE for now, one open question flagged
- Watchdog shipped and live-proven on a healthy tick (item 2 above).
- **NEW real live incident observed and resolved this update** (2026-08-25
  ~03:29-04:09 UTC) — see `backtest/live_incident_2026-08-25_0329.md` for full
  detail. Dashboard Live Update AND its own watchdog both went quiet for
  30-40+ minutes on their 5-minute crons (Lineup Watch showed a similar,
  independently-timed 47.5-min gap), well AFTER the Priority D runtime fix
  was already merged — confirms this is a SEPARATE incident class from
  2026-08-24's (a scheduling/trigger-delivery gap, not a runtime bottleneck).
  Proof: manually dispatched `dashboard-live.yml` as recovery; it ran in
  **35 seconds** once it actually started — the pipeline itself was never
  slow, only the trigger delivery was delayed. `docs/live.json` freshness
  restored. **Open question, NOT answered, flagged for a future session**:
  why did the watchdog itself (cheap, 3-min timeout, check-only) also go
  quiet on its own cron for 30+ minutes? Don't guess at this — investigate
  with real data if it recurs.
- **Full inventory DONE**: `backtest/scheduled_workflow_inventory_2026-08-25.md`.
  ≈1,050 workflow invocations/day total; ≥720/day are independent `git push
  origin HEAD:main` attempts from 4 workflows effectively on the same ~5-minute
  rhythm (Dashboard Live Update, Dashboard Pages Deploy which chains off it,
  Live Freshness Watchdog, Lineup Watch). Real support for the congestion
  hypothesis as a secondary/background cost — but NOT the cause of the
  2026-08-24 incident specifically (that was the sequential MLB-fetch loop,
  already fixed — see item 8).
- **Consolidation proposal DONE (measured, not implemented)**: one tempting
  idea (fold the watchdog's check into Dashboard Live Update itself) is
  recorded as explicitly WRONG — it would defeat the watchdog's purpose of
  detecting Dashboard Live Update when THAT workflow is the one stuck. One
  more defensible candidate (merging Dashboard Pages Deploy's own commit into
  Dashboard Live Update's) is recorded but deliberately NOT implemented —
  needs its own measurement pass. Nothing further queued here unless new
  evidence surfaces.

### D. dashboard-live.yml runtime growth — ROOT CAUSE FOUND + FIXED (see item 8 above)
- Real fix shipped this update (`47a75920`). Remaining: watch for a live confirmation
  the next time MLB's API has a slow window (can't force this on demand) — a fast
  per-step recovery instead of a repeat of the 2026-08-24 cancellation streak is the
  live proof. Nothing else queued here unless a NEW regrowth is observed.

### E. FanDuel observer — redesign DONE, TARGETING UPGRADED, live validation in progress
- Passive observer closed with a persisted report. Event-targeted redesign built,
  tested, and running live (PID 1720, see background jobs). First ~28 min: 7+ real
  triggers detected (pitcher/scoring/inning/batter changes), 4 status_change
  (suspend/reopen) events on one market — genuinely more signal than the passive
  runs ever produced — but still no confirmed odds/line change through that point.
  Per the explicit "better targeting, not longer duration" directive: upgraded
  `detect_triggers()` from 4 to 8 kinds ranked into 5 priority tiers (commit
  `c2064996`, not yet on main, pure research tooling) — real home-run detection via
  MLB's own `result.eventType` (not score-delta inference), real bases-loaded
  detection (fixed a genuine bug: `on_1b` was unconditionally True, never actually
  read baserunner state), batting-order-turnover detection. Burst cadence/poll-count
  now scale per tier (pitcher changes burst hardest, routine batter changes
  lightest) instead of one flat plan for every trigger. This upgrade landed AFTER
  the PID 1720 run already started, so that run is still using the OLD flat-plan
  code (in-memory, unaffected by the file edit) — its results are still valid data
  for the "passive vs event-targeted" comparison, just not a test of the new tiering.
  **Next**: let PID 1720 finish (see background jobs), report its final numbers
  honestly (confirmed repricing count, or another honest null), then the tiered
  version is ready for the NEXT live test whenever a next live game is available.

### F. Alive-brain direction
- No new work needed. Next real proof requires (E) to produce one real captured
  price/line change with full before/after detail. Do not deploy Cloudflare.

### G. Forward validation of stable-lift
- No action needed right now — just don't touch the policy again on tiny samples.

## How to resume

1. `git status` / `git log --oneline -5` to confirm you're at `c2064996` or later, and
   that it's a clean fast-forward from `origin/main` (PR #64 already merged — do NOT
   look for it as still-open).
2. `ps -p 3663` / `ps -p 1720` to check both background jobs. If PID 1720 (event-targeted
   observer) is gone: read the tail of `backtest/event_targeted_observer_log.jsonl`,
   grep for `"kind": "odds_change"` / `"kind": "line_change"` to see if it caught a real
   repricing, and either report that in full detail or update
   `backtest/fanduel_observer_final_report.md` with this run's numbers if still null.
   Then consider a next live run using the NOW-tiered trigger code (commit `c2064996`) —
   not yet live-tested — whenever a next live game is available. If PID 3663 (main
   backfill) is gone: this becomes the top priority — see "PRIORITY A" in the still-open
   threads below (verify date coverage, then run `build_canonical_backtest.py`).
3. Priority C (scheduled-workflow inventory + consolidation proposal) is DONE — see
   `backtest/scheduled_workflow_inventory_2026-08-25.md`. Priority D (runtime bottleneck)
   is DONE and merged. Both closed unless new evidence surfaces.
4. Once the main backfill completes and `rows_canonical.jsonl` is built + validated,
   the next real work is the accuracy-experiment queue (probability-first vs edge-first
   within the safe pool, robust-CI lower-bound ranking, shrinkage-strength audit,
   realistic PA-distribution modeling, market specialization, fallback-source value) —
   equal-volume realized hit rate is the primary metric for every one of them. Do not
   promote any challenger on Brier/logloss/calibration alone or on reduced volume.
5. Run `for f in test_*.py; do /tmp/mlbvenv/bin/python3 "$f" || echo "FAIL: $f"; done`
   before every commit. Fully green as of `c2064996`.

# Full Count — session handoff status

Last updated: 2026-08-25 ~04:35 UTC by Claude (this session).
Purpose: let a fresh session resume with zero hidden chat context.

## Branch / HEAD

- Branch: `claude/gridiron-continuation-dvaljm` at `c72883ef`, fully pushed.
- `main` is at `a652eccb` "Dashboard live update 2026-08-25 04:09 UTC".
- PR #64 (Weston fix + provenance validator) MERGED, sha `1ead2fb1`.
- **PR #65 OPEN** (https://github.com/werriesjacob1-cmyk/Full-Count/pull/65):
  event-targeted observer tiered-trigger upgrade + real `on_1b` bug fix +
  this session's incident docs. Pure research tooling, no production code.
  CI was `unstable`/in-progress as of last check (just pushed more commits
  onto the same branch/PR) — **check CI and merge once green, qualifies
  under standing authorization** (diff-reviewable: only
  `backtest/alive_brain_prototype.py`, `backtest/event_targeted_observer.py`,
  `backtest/candidate_dataset.py`, test files, and markdown docs).
- Repo has moved to `werriesjacob1-cmyk/Full-Count` (GitHub redirects
  PROJECT-GRIDIRON pushes there).

## Long-running background jobs

- **Main backfill**: PID 3663, `backtest/engine.py --start 2024-04-01 --end
  2026-06-30 --out backtest/rows_backfill.jsonl --no-weather`. Started
  2026-08-24 21:53:13 UTC. At last check: ~6.5h elapsed, healthy, still the
  long pole for everything in "OPEN THREADS" below. Check with
  `ps -p 3663 -o pid,etime,cmd`. **When it completes**: verify 821/821
  intended dates, no gaps, inspect failures, then run
  `backtest/build_canonical_backtest.py` (already written, tested, and wired
  to `provenance.require_single_regime()` as a hard gate) to produce
  `backtest/rows_canonical.jsonl`. Do NOT launch another giant backfill.
- **Event-targeted FanDuel observer**: FINISHED (PID 1720 completed
  2026-08-25T04:20:52Z). Results fully written up in
  `backtest/fanduel_observer_final_report.md`'s "Event-targeted observer —
  first full run" section — 27 real triggers (15 batter/8 inning/4 pitcher),
  233 FanDuel polls, 0 endpoint failures, 58 status_change events (30
  suspend/28 reopen) tightly bound to inning transitions specifically, still
  **zero confirmed odds/line changes** across all 3 runs (~130 min total).
  Real finding: suspend/reopen on the tracked market looks like routine
  per-inning market rollover, not a leverage reaction — future targeting
  should track actual PLAYER PROP markets during a trigger window, not
  inning-special markets (not yet tried). This run used the PRE-tiering
  observer code (upgrade landed mid-run); the tiered version (PR #65) is
  ready for the next live test whenever a next live game is available.

## CLOSED work this session — do not redo

1. **H+R+RBI stable-lift** merged to main, PR #62, sha `6c2ce0d6`. Scope:
   hits_runs_rbis Lean gate ONLY. Do not extend to runs/rbis or add a Top
   Pick lift gate. Do not touch the shrinkage prior.
2. **Live-freshness watchdog + dashboard-live.yml timeout bump** merged,
   PR #63, sha `d4aaec8e`. Watchdog fired successfully once (03:36:09 UTC,
   correct no-op on a fresh state) — see item 9 below for what happened
   after that.
3. **12 repair-vs-main row-count discrepancies root-caused**, tooling built,
   NOT yet run to completion (waiting on main backfill). Real cause:
   `code_git_sha` proves the main backfill's early portion predates commit
   `919456e5` (fixed a real `predicted_prob` nulling bug for 7/13 markets).
4. **Fail-fast provenance/regime validator**: `backtest/provenance.py`,
   8 tests, wired into `build_canonical_backtest.py` as a real gate.
5. **Weston Wilson stale-explanation bug fixed, MERGED, live-verified**
   (`1ead2fb1`). Root cause: wholesale-copy of the immutable registry
   snapshot onto the live row once a game started, including `why`/
   `watchouts` (pure presentation). Fixed via `FROZEN_PUBLICATION_FIELDS`
   allowlist (audit/settlement-critical fields only). Verified via
   sign-reversal regression tests AND a real production reproduction
   (manually dispatched `dashboard-refresh.yml`, diff-reviewed the merge —
   no probability/ranking logic touched). **Honest boundary case found**:
   the ORIGINAL Weston Wilson registry entry still shows pre-fix text,
   because (a) its snapshot was captured by a generator version that
   predates the explanation-quality routing fix (proven by the text's own
   bare/unqualified format — no directional suffix), and (b)
   `bettable_games(allow_started=False)` means NO rebuild, before or after
   this fix, ever re-scores an already-started game, so there's no fresher
   `row` for `reconcile_public_lifecycle()`'s carry-forward path to draw
   from. This is a real, structural, PERMANENT limitation of the
   pregame-only recomputation architecture — not a bug, not something to
   patch with a one-off historical-registry rewrite (explicitly forbidden).
   The fix DOES prevent this exact regression for every future publication.
6. **FanDuel passive observer CLOSED** with a persisted final report —
   zero real odds/line changes across ~85 min, 3-4 games, 96-100 markets.
7. **Event-targeted observer built AND run to completion AND upgraded**
   — see background jobs above and PR #65.
8. **Priority D runtime bottleneck FOUND + FIXED + forward-verified**:
   `grade_results.fetch_game_contexts()` was sequential (one MLB feed fetch
   fully awaited per distinct game, called 3x/cycle) — real MLB API
   slowness got multiplied into the 2026-08-24 23-run cancellation streak.
   Fixed via bounded `ThreadPoolExecutor` (`47a75920`, merged). **Forward
   proof**: when a completely SEPARATE scheduling incident hit on
   2026-08-25 (item 9), the manually-recovered run completed in **35
   seconds** — proves the runtime fix holds; that incident was NOT a
   runtime regression.
9. **NEW scheduling-delivery incident investigated + partially resolved**:
   `dashboard-live.yml` AND `live-freshness-watchdog.yml` (a SEPARATE,
   independently-scheduled workflow) both went silent on their own 5-minute
   crons simultaneously, ~03:29-04:16+ UTC. Zero queued/in-progress runs
   during the gap (ruled out capacity issues — this was trigger-DELIVERY,
   not job-execution). Manual `workflow_dispatch` of `dashboard-live.yml`
   restored freshness (35s run) but did **NOT** restore either workflow's
   recurring schedule — as of last check (~04:16 UTC) both were STILL
   silent on their own cron, 47+ min for Dashboard Live Update, 40+ min for
   the watchdog. **A fresh session should re-check whether the cron has
   resumed on its own.** Full timeline:
   `backtest/live_incident_2026-08-25_0329.md`. This is real, strong,
   first-party evidence that a GitHub-schedule-based watchdog cannot serve
   as an independent recovery domain for a GitHub-schedule-based primary
   workflow — produced the requested architecture comparison:
   `backtest/independent_recovery_design_2026-08-25.md` (recommends Option
   A: an external heartbeat + `workflow_dispatch` trigger, reusing
   `dashboard/check_live_freshness.py`'s existing logic; explicitly does
   NOT recommend Option B, moving primary orchestration off GitHub, without
   more evidence — "do not deploy a large migration yet"). **Not
   implemented** — this is the requested recommendation, not authorization
   to build it; needs the user to weigh in on which external scheduler/
   credential to use before it's built.
10. **Candidate-level decision dataset: feasibility analysis + reusable
    builder** (Priority 3, the accuracy-research prep). See
    `backtest/candidate_dataset_feasibility_2026-08-25.md` for the full
    gap analysis (IDENTITY/PREDICTION/OUTCOME/PROVENANCE: buildable today;
    MARKET: permanently absent from backtest rows per SCHEMA.md; DECISION:
    the hard part — `recommendation_funnel.gate_trace()` — already exists,
    just never persisted). **Most important finding**: Priority 5
    (within-slate pairwise selection — "why did the better candidate win")
    CANNOT be answered from `rows_canonical.jsonl` as currently produced,
    because `build_candidates()` keeps only ONE candidate per batter,
    discarding the losing alternatives. Flagged now so it isn't a late
    discovery once canonical history lands. Built
    `backtest/candidate_dataset.py` (a pure, point-in-time-safe overlay
    builder — no new fetches, no new lookahead risk) + 14 tests against
    synthetic fixtures. Deliberately NOT a full historical build yet.

## OPEN THREADS (in the order the user's last message prioritized)

### 1. Finish current moving work
- Event-targeted observer: DONE (see background jobs above).
- PR #65: review CI, merge once green (see Branch/HEAD above).
- Main backfill: still the long pole, untouched, healthy.

### 2. Scheduler failure domain — investigated, architecture recommended, NOT implemented
- See item 9 above. Real open question still unresolved: does the cron
  resume on its own eventually, or does it need another manual kick? Check
  opportunistically, don't poll idly. If Option A (external heartbeat) is
  ever authorized to build, it needs: a concrete external scheduler choice
  (user decision), a scoped-down GitHub PAT/App token, and wiring the
  existing `dashboard/check_live_freshness.py` logic to run there instead.

### 3. Accuracy research lab prep — DONE for what could be done pre-canonical-history
- See item 10 above. Ready the moment `rows_canonical.jsonl` exists.
- **Remaining real gap, scoped but not built**: capturing ALL within-slate
  competing candidates (not just the one `_pick_line` winner) is needed for
  Priority 5 — requires a scoped change to `backtest/engine.py`'s candidate
  generation call, itself production-adjacent code needing careful review
  even though it wouldn't touch scoring/probability math. Not started.

### 4. Canonical history — BLOCKED on main backfill (item above)
Once PID 3663 finishes: verify date coverage/gaps/failures/row counts,
reconcile via `backtest/build_canonical_backtest.py` (already gated by
`provenance.py`), produce `rows_canonical.jsonl`, persist the reconciliation
report. Do NOT launch another giant backfill after.

### 5-10. All deep accuracy questions (pairwise selection, fragility, source/
role certainty, market specialization, disagreement archetypes, shadow
tournament) — correctly BLOCKED on canonical history, per the user's own
instruction not to manufacture premature analysis. Full question design
already exists in this session's own instruction history (not re-copied
here — see the governing instruction set) plus the feasibility groundwork
in item 10 above.

## How to resume

1. `git status` / `git log --oneline -5` to confirm you're at `c72883ef`
   or later.
2. Check PR #65's CI (`mcp__github__pull_request_read` method `get_check_runs`
   on PR 65) and merge if green — qualifies under standing authorization.
3. `ps -p 3663` for the main backfill. If it's gone, this becomes the
   immediate top priority (see OPEN THREADS #4 above) — do NOT start any
   accuracy analysis before canonical history is validated.
4. Re-check whether `dashboard-live.yml`/`live-freshness-watchdog.yml`
   scheduled (not manual) runs have resumed on their own
   (`list_workflow_runs` with `event: schedule` filter) — if still silent
   after a long gap, that's worth a fresh look; if resumed, note when and
   close the open question in item 9 above.
5. Run `for f in test_*.py; do /tmp/mlbvenv/bin/python3 "$f" || echo "FAIL: $f"; done`
   before every commit. Fully green as of `c72883ef`.

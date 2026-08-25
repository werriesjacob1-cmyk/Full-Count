# Scheduled/triggered workflow inventory — 2026-08-25

Priority C.2 from the governing instruction set: "inventory every cron-triggered
workflow: name, cadence, average duration, concurrency group, writer targets,
overlapping responsibilities, whether it commits/pushes, whether it deploys
Pages, whether jobs can overlap." Pulled directly from each workflow's own YAML
(`.github/workflows/*.yml`), not estimated.

## The inventory

| workflow | trigger | cadence | timeout | concurrency group | commits to `main`? | deploys Pages? |
|---|---|---|---|---|---|---|
| **Dashboard Live Update** | `schedule` | `*/5 * * * *` (every 5 min, 288/day) | 25 min | `dashboard-live-observation` | **yes** — sole writer of `docs/live.json` | no |
| **Live Freshness Watchdog** | `schedule` | `2-59/5 * * * *` (every 5 min, offset +2, 288/day) | 3 min | `live-freshness-watchdog` | no — only dispatches Dashboard Live Update via `workflow_dispatch` if stale | no |
| **Dashboard Pages Deploy** | `workflow_run` (fires on EVERY completion of Dashboard Refresh or Dashboard Live Update) | effectively every 5 min (~288-296/day, riding on Dashboard Live Update's own cadence) | 10 min | `dashboard-pages-deployment` | **yes** — "Record deployed Top Pick exposure" commit, separate from the Pages artifact upload | **yes** — `actions/upload-pages-artifact` + `actions/deploy-pages` |
| **Lineup Watch** | `schedule` | `*/10 * * * *` (every 10 min, 144/day) | 5 min | `lineup-watch` | yes (own lineup-state commits) | no |
| **Odds Snapshot** | `schedule` | `0 * * * *` (hourly, 24/day) | 6 min | `odds-snapshot` | yes | no |
| **Dashboard Refresh** | `schedule` | 8x/day (13/15/17/19/21/23/1/3 UTC) | 25 min | `dashboard-full-rebuild` | yes (full board rebuild) | no directly (triggers Dashboard Pages Deploy) |
| **MLB Daily Pipeline** | `schedule` | 6x/day (14:30/15:30/17:00/20:00/22:30/23:30 UTC) | 35 min | `mlb-daily-pipeline` | yes | no |
| **Calibration Recheck** | `schedule` | weekly, Monday 09:00 UTC | 90 min | `calibration-recheck` | yes (calibrator refit output) | no |
| **Test Suite** | `push`/`pull_request` (not cron) | on every push | 15 min | `test-${{ github.ref }}`, **cancel-in-progress: true** | no | no |

Every scheduled workflow uses `cancel-in-progress: false` (deliberate — the
existing comments explain this coalesces stale 5-minute observations into a
queue rather than dropping them). Only Test Suite cancels in-progress runs,
and only within the same ref (a superseded push on the same branch/PR).

## Total daily invocation volume

Summing the cadences above: **≈1,050 workflow invocations/day**, heavily
concentrated in three workflows that effectively all fire on the same ~5-minute
rhythm and independently attempt a `git push origin HEAD:main`:

- Dashboard Live Update: 288/day
- Dashboard Pages Deploy (chained off Dashboard Live Update's completion): ~288-296/day
- Live Freshness Watchdog: 288/day (usually a no-op push — only dispatches, doesn't commit)
- Lineup Watch: 144/day

That's **≥720 independent attempts per day to push to `main`** from just these
four, before counting Odds Snapshot, Dashboard Refresh, and MLB Daily Pipeline.
This is real, quantified support for part of the "repo-wide scheduled-workflow
congestion" hypothesis from earlier in this investigation — Dashboard Live
Update's own long-standing workflow comment already flagged "how many OTHER
workflows commit to main every few minutes" as the reason its git-push retry
loop needed headroom.

## Relationship to the 2026-08-24 incident (Priority D)

Important scoping correction: this inventory is real and the push-contention
volume is real, but the **actual measured root cause of the 23-run cancellation
streak** (see `backtest/runtime_profile_2026-08-25.md`) was NOT git-push
contention — it was `grade_results.fetch_game_contexts()` looping sequentially
over MLB feed fetches, independently reproducing 4-8 minute step durations even
before the commit step ever ran. Git-push contention is a real, secondary cost
(and the *reason* the commit step's own retry loop exists), not the dominant
one for that specific incident. That fix already shipped and does not depend
on anything in this section.

## Consolidation: measured, not blind

The instruction was explicit: "do not consolidate blindly, measure first." One
tempting idea — fold Live Freshness Watchdog's stale-check directly into the
tail of Dashboard Live Update itself, eliminating an independent 288/day cron
— **does not actually work** and should not be done: the watchdog's entire
purpose is to detect and recover Dashboard Live Update when *that workflow
itself* is the one stuck, cancelled, or not firing (exactly what happened on
2026-08-24). A check embedded inside the workflow it's meant to rescue cannot
run when that workflow fails to run at all. This is a real example of why
"consolidate for fewer crons" can silently remove the actual safety property —
worth recording so a future session doesn't propose the same thing without
re-deriving why it's wrong.

A more defensible candidate, NOT implemented here (would need its own
measurement pass and explicit sign-off given it touches multiple workflows'
write semantics): Dashboard Pages Deploy's own `git commit`/`git push` (the
"Record deployed Top Pick exposure" step) could potentially be folded into
Dashboard Live Update's own existing commit-and-push step, since both already
run on virtually the same cadence and both write to `main` — this would remove
one of the four ~5-minute-cadence pushers without removing any workflow's
actual observation/detection responsibility (unlike the watchdog case above).
This is a real candidate for a future, carefully-measured change — NOT
something to implement speculatively now, and explicitly deferred per "measure
first."

## Watchdog live-execution proof (Priority C.1)

`live-freshness-watchdog.yml` fired for the real first time at 2026-08-25
03:36:09 UTC (run #1, `list_workflow_jobs` on run id `32805793628`) — completed
successfully in 9s. Step detail: "Check docs/live.json freshness" ran and
passed; "Dispatch Dashboard Live Update (recovery attempt)" and "Fail visibly
on a stale finding" both correctly SKIPPED, because `docs/live.json` was
genuinely fresh at that moment (Dashboard Live Update run #273 had committed
7 minutes earlier, well inside the 15-minute SLA). This is real, live proof
the watchdog is wired up and behaving correctly on a real tick — it correctly
identified a healthy state and took no action, rather than either firing a
needless recovery or silently doing nothing. **Not yet observed**: an actual
recovery dispatch (the "Dispatch Dashboard Live Update" step actually running,
not skipping) — that only happens if `docs/live.json` goes stale again while
the watchdog is checking, which hasn't recurred since the Priority D fix
shipped. Continue checking opportunistically; the no-op case above is already
meaningful confirmation the plumbing works end to end.

## Bottom line

The repo-wide congestion hypothesis has real, quantified support (≥720
same-branch pushes/day from four independently-scheduled workflows), but it is
NOT what caused the specific 2026-08-24 incident — that was the sequential
MLB-fetch bottleneck, already fixed. Congestion remains a legitimate,
lower-priority target for a future measured consolidation pass, with one
candidate idea recorded above and one previously-plausible idea now recorded
as ruled out with its reasoning, so neither needs to be re-investigated from
scratch.

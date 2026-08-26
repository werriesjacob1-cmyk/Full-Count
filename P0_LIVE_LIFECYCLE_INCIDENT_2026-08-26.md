# P0 live-lifecycle incident: forensics, SLA, and what was actually fixed

Written on `claude/live-lifecycle-p0-01` (separate worktree/branch from the
in-flight research rebuild and the frozen UX branch). Every claim below is
backed by a specific artifact -- a real GitHub Actions run ID, a real
`docs/live.json`/`docs/data.json` field read directly from the repo at
investigation time, or a real test file -- not inferred from "cron has had
problems before."

## A. The two customer-facing symptoms, reconciled

1. Colt Keith (`game_pk=824234`, `player_id=690993`,
   `fc2:824234:player-690993:hits:1:over`, Over 0.5 Hits, Top Pick):
   customer reported the game over, the prop missed, but the card still
   showed **LIVE** + **LIVE STATUS UNKNOWN**, ungraded.
2. **LIVE DATA STALE** shown prominently, sitewide.

Both trace to the **same single root cause**: GitHub's `schedule` trigger
for `dashboard-live.yml` (the sole writer of `docs/live.json`, intended
cadence every 5 minutes) is not actually firing anywhere near that cadence
in this repository.

## B. Proof, not inference -- real Actions run history

Pulled directly via the GitHub API (`workflow_runs` for
`dashboard-live.yml` and `live-freshness-watchdog.yml`, 2026-08-26).
Real gaps between consecutive **`schedule`-triggered** run creations
today (`workflow_dispatch` runs, which fire immediately on request, are
excluded from this list on purpose -- they are the *recovery* action, not
the baseline cadence):

| From (UTC) | To (UTC) | Gap |
|---|---|---|
| 12:25:17 | 13:35:18 | 70 min |
| 13:35:18 | 14:24:59 | 50 min |
| 14:24:59 | 16:01:40 | **97 min** |
| 16:01:40 | 16:56:02 | 54 min |
| 16:56:02 | 19:11:50 | **136 min** |

Configured cadence: 5 minutes. Observed cadence: 34-136 minutes, all day,
not one anomalous window. `live-freshness-watchdog.yml` -- deliberately
built with its *own*, differently-offset `schedule` trigger
(`2-59/5 * * * *` vs the main job's `*/5 * * * *`) specifically so a
scheduler gap affecting one "doesn't necessarily affect both" (its own
header comment's stated design goal) -- shows the **identical** gap
pattern on the same day (12:02→13:07, 13:07→14:01, 14:01→14:50,
14:50→16:06, 16:06→16:55, 16:55→18:49: 55-115 min gaps). **The
"independent" 2-minute offset does not produce real independence** -- both
schedules are being throttled together, consistent with GitHub delaying
`schedule`-triggered runs repo-wide (this repo runs at least 8 other
5-15-minute cron workflows competing for the same allocation), not
per-workflow.

Pulled one real failed watchdog run's job log
(run `33001822158`, 18:49:34Z) directly: it is **not crashing** -- it
correctly measured `docs/live.json` as `112.9 minutes old` against its own
15-minute SLA, correctly dispatched a `workflow_dispatch` recovery (which
is `run 33001834183`, confirmed succeeded), and deliberately `exit 1`s
*by design* so the staleness is visible on the Actions badge. The
watchdog's logic is working exactly as built; it just isn't being
*invoked* often enough to matter, because it rides the same throttled
scheduler.

## C. Colt Keith: the exact mechanism

Read directly from the live repo during investigation (not reconstructed
after the fact):

- `docs/data.json` (written by the periodic **full rebuild**, "Dashboard
  refresh", a separate, slower pipeline from the 5-minute live updater):
  `game_state: "final"`, observed `19:26:39Z`. This pipeline had already
  correctly seen the game end.
- `docs/live.json` (written by the fast channel, `dashboard-live.yml` via
  `refresh_grades.py`): for the **same prop**, `game_state: "live"`,
  observed `19:12:10Z` -- its last successful check, which simply predated
  the game actually going final. `settlement_state: "open"`,
  `settlement_observed_at: "16:55:48Z"` (a genuine mid-game box-score
  snapshot, correctly still "open" at the time it was taken).

`refresh_grades.py` (`dashboard/refresh_grades.py:264-346`) re-checks
MLB's live game feed for **every row on every run** and computes final
settlement in the *same pass* the instant it observes `final` -- so there
is no separate "settlement lags game-state" application bug. The only
reason settlement hadn't advanced is that `refresh_grades.py` (and the
workflow that hosts it) simply hadn't run again since 19:12, for the exact
scheduling reason in section B.

A customer loading the site in that window sees: `gradeChip()` render
**"Live"** (because `live.json`'s `game_state`, last refreshed at 19:12,
still says `"live"`) and, once `grades_checked_at` ages past the 15-minute
threshold, `liveStaleChip()` render **"Live Status Unknown"/"Live Data
Stale"** on top of it -- two separately-computed, individually-honest
signals that read as contradictory side by side. Not a lie, not a crash --
stale-but-honestly-labeled data, compounded by a scheduling gap far
outside any reasonable customer SLA.

## D. A real bug found and fixed while verifying the contract

Verifying Phase 5's race conditions directly (not assuming the existing
code was correct) surfaced one genuine gap: `applyCachedLive()`
(`dashboard/static/app.js`) already correctly refuses to let a stale
`live.json` poll un-final a game or downgrade a settlement (`regressesFinal`
+ `acceptSettlement`'s authority/recency ranking -- both already
implemented and already covered by `test_state_races.py`,
`test_live_lifecycle.py`, `test_lifecycle_contract_v3.py`, all passing
before this investigation touched anything). But the **generic field
loop** (odds, edge, `recommendation_status`, etc.) only checked an
incoming price delta against one board-wide `boardOddsAt` timestamp --
never against what *this same browser session* had already applied from
an earlier, newer poll. An older snapshot (a CDN/proxy cache hit, or a
slow request landing after a faster later one) could silently move a
price backwards. Fixed: `applyCachedLive()` now records
`p._field_updated_at` per field and rejects a strictly-older incoming
stamp for the same field -- the identical discipline
`ingestLiveDocument()` already applied when accumulating documents into
`LIVE_CACHE`, now also enforced at the point those cached deltas land on
the displayed prop. See `test_frontend_lifecycle.py`'s new
`test_colt_keith_style_final_state_never_regresses_to_a_stale_live_poll`
for the regression test (reconstructs the exact Colt Keith field values
and both the stale-poll and legitimate-newer-correction cases, plus the
price race explicitly).

## E. Freshness architecture: what already exists vs. what's separated now

Already correctly separate, confirmed by direct code read:
- **Board-generation freshness**: `DATA.generated_at` (full rebuild).
- **Game-state + settlement freshness**: `grades_checked_at`/
  `grades_updated_at` in `live.json`.
- **Sportsbook price freshness**: `prices_checked_at`/`prices_updated_at`
  in `live.json`, and per-prop `market_fetch_state`
  (MATCHED/NOT_POSTED/FETCH_FAILED/IN_PLAY -- already surfaced via
  `staleChip()`/`priceFreshnessState()`).

These are genuinely independent *data* channels already -- a price-fetch
failure does not block grading, and vice versa (`continue-on-error: true`
on both steps, verified in `dashboard-live.yml`). What was **not**
separated: the watchdog's own reporting, which only ever surfaced one
combined `updated_at` blob. `dashboard/check_live_freshness.py` now also
reports `channel_staleness()` for the game-state/settlement channel and
the pricing channel independently (printed in the workflow's own log
output on every run; the exit-code/recovery-dispatch behavior is
unchanged, since one recovery action -- redispatching `dashboard-live.yml`
-- fixes both channels together, and today's evidence does not support
that they fail *independently* often enough to need separate recovery
paths). Tests: `test_live_freshness_watchdog.py` checks 10-11.

## F. Was Phase 3's "split the lightweight lane from pricing" needed?

Investigated, not assumed. Every real run today (`updated_at` -
`created_at` on the successful runs pulled in section B) completed in
well under a minute -- the 2026-08-25 timeout/cancellation problem
(grading+repricing regrowing past the 25-minute budget) that the
workflow's own header comment documents is **not** reproducing today.
Today's incident is 100% a *scheduling-frequency* problem, not a
*job-runtime* problem. Splitting lifecycle from pricing into two workflows
would not have prevented a single minute of today's staleness -- both
would still ride the same throttled `schedule` trigger. Recommendation:
**do not split it now** on unproven-today grounds; revisit if job runtime
regrows past the timeout budget again (that failure mode is real and
already documented, just not what happened this time).

## G. Reliable triggering -- what's available, and the real boundary

Compared per Phase 4:

1. **GitHub cron alone**: demonstrated unreliable today (34-136 min gaps).
2. **GitHub cron + watchdog**: demonstrated *also* unreliable today, for
   the reason in section B -- the watchdog rides the same throttled
   scheduler, so it is not the independent backstop its own design intent
   describes.
3. **Canonical workflow triggered by an independent external heartbeat**:
   would work (`workflow_dispatch` fires immediately, not subject to the
   `schedule` delay) but requires something outside GitHub's own cron to
   call it on a real 5-minute cadence.
4. **A lightweight externally-scheduled worker**: same requirement as (3).
5. **This session's own scheduling tool** (Claude Code Remote Routines)
   was evaluated as a possible stopgap for (3)/(4). Its own documented
   floor is "normally hourly" -- an hourly external heartbeat would not
   reliably beat the *best* gaps already observed today (34-54 min) and
   would still miss the worst ones by a wide margin, while adding a
   production dependency on this chat session's account that has no
   business owning site uptime. **Rejected as not a real improvement**,
   not attempted.

**No option compared above closes the gap without an external
scheduler-of-record that Jacob sets up and authorizes.** Everything
possible without that has been implemented (per-channel observability,
the price-race fix, the regression tests). The concrete, minimal one-time
action that would fix this for real: point any external "hit this URL/run
this command every 5 minutes" service (a free tier of something like
cron-job.org, a GitHub App with its own scheduler, or a small always-on
box Jacob already controls) at
`gh workflow run dashboard-live.yml --repo <owner>/<repo>` using a token
scoped to `actions:write` on this repo. That single call, made reliably
every 5 minutes from outside GitHub's own throttled scheduler, replaces
both `schedule` triggers' unreliable cadence with a real one; the existing
watchdog and its recovery-dispatch logic keep working unchanged as a
second line of defense.

## H. SLA (proposed, pending Jacob sign-off)

- **Game-state/settlement channel**: target re-verification at least every
  5 minutes during any game in `pregame`/`live`/`delayed`/`suspended`
  state; customer-facing "stale" framing only past 15 minutes (3x
  cadence, unchanged from the existing, already-reasoned SLA in
  `check_live_freshness.py`).
- **Final settlement**: target within 5 minutes of MLB reporting a game
  final, *contingent on the channel above actually running that often* --
  which today's evidence shows is not reliably true without the external
  trigger in section G. Until that trigger exists, the honest SLA is "as
  fast as the next successful scheduled or dispatched run," typically
  seen today within 30-140 minutes, not 5.
- **Sportsbook price channel**: independent SLA, same 15-minute stale
  threshold, explicitly never gates game-state/settlement advancement
  (already true today, confirmed by `continue-on-error` isolation).
- **Board-generation channel**: separate again -- `DATA.generated_at`
  being old is a "when did the last full rebuild run" fact, not a claim
  about whether any individual game's live state was rechecked.

## I. What was deliberately NOT touched

Zero changes to `generate_picks.py`, `recommendation.py`,
`settlement_rules.py`'s grading rules, any score/weight/threshold/gate,
calibration, or the locked disagreement experiment. Zero changes to
`.github/workflows/*.yml` (the demonstrated root cause is a GitHub
platform-level scheduling behavior, not a misconfigured cron expression --
editing the cron string would not fix it). The customer-facing freshness
*copy* (the alarming "LIVE DATA STALE" bar wording) and the
"⚠ Market Disagrees" badge are explicitly UX-branch changes (Phase 9 of
the governing directive) and are made there, not here, to keep this
branch's diff strictly about the lifecycle/reliability contract.

# P0 Hardening — reconciliation, fail-closed, derived surfaces

Branch `p0/live-board-integrity` · PR #72 · **not merged, not deployed**
No model, calibration, scoring or threshold changes.

---

## A. The two shapes the first pass got wrong

**Scheduling.** The first fix added another GitHub-cron watchdog. Measured
over 29 consecutive Lineup Watch runs on 2026-08-28:

| declared | `*/10 * * * *` (144/day) |
|---|---|
| delivered | **12.4/day — 9%** |
| median gap | 51 min |
| worst gap | **11.0 h** |
| intervals near 10 min | **0 of 29** |
| runs during the incident's 9.6 h | **0** |

A recovery mechanism on that queue cannot bound anything.
`infra/live-heartbeat` already dispatches `dashboard-live.yml` every 5
minutes from Cloudflare. Reconciliation runs there; the new watchdog is
**deleted**, not kept as a weaker second path.

**Acknowledgment.** A watchdog that dispatches a rebuild and considers
itself done acknowledges an *event*. The question is whether publication
*matches reality*, and those come apart exactly when it counts — the
dispatch can be dropped, the rebuild can fail, or it can succeed and still
not fix the mismatch.

`reconcile.py` therefore never acknowledges. It re-derives the entire
mismatch set every cycle; `mark_rebuild_requested()` increments a counter
and changes nothing else. A mismatch clears **only** by re-observation.

## B. Three checks, one resolution

`board age` · `confirmed MLB lineup vs published` · `LINE_MOVED`

All three request a full rebuild, because only a rebuild re-derives
lineups, thresholds and probabilities together. A price refresh can fix
none of them — which is exactly why the live overlay kept looking healthy
while the board rotted underneath it.

Against the real production board and live MLB:

```
15 games, 11 with a confirmed lineup posted
12 open mismatches: 1 board_age (824 min old) + 11 lineup
needs_rebuild: True
dispatch: False — cannot determine if a rebuild is running (fails closed)
```

## C. Stampede guard

A rebuild takes 10–15 min; reconciliation runs every 5. Without a guard,
every cycle during a long mismatch dispatches another. An **unknown** run
state fails closed: a duplicate rebuild is worse than a delayed one, and
the next tick is five minutes away.

## D. Fail closed, for real

A banner is a description. The first pass added a warning strip above cards
that still rendered identically — same prices, same edges, same Top Pick
chips. A customer scanning cards does not re-read a banner before each one.

Best Bets and More Picks are now **replaced**, not annotated. Research stays
reachable behind an explicit *"show anyway (research only)"* choice that
keeps the unverified label on screen. All Props keeps listing rows —
browsing is its purpose — but declares up front that nothing in it is
current.

Four independent ways currency can fail to be proven, all treated alike:
model basis past limit · prices past limit or unknown · this browser never
applied a live overlay · reconciliation found a provable mismatch.

## E. Derived surfaces — the audit's real find

`suggested_parlay` is built once during full generation and frozen into the
payload. The live overlay then corrects the props underneath it and never
touches that copy, and its legs carried **no id**, so nothing could even
tell they had gone stale.

Legs now carry one. The frontend resolves each leg back to the live prop
(exact name+prop fallback for older boards) and anything unmatched, moved,
unpriced or no-longer-confirmed **suppresses the whole parlay** — a parlay
is a single wager, so one bad leg invalidates it rather than degrading it.

Writing that fix surfaced a second bug its own test caught: leg prices came
from the live board while the combined figure was still frozen, so the two
could disagree on screen — prices that no longer multiply out to the total
beneath them. The combined figure is recomputed from the same live prices.

## F. Compact cards

`market_odds == null` no longer falls through to *"Not yet posted on
FanDuel."* On the day of the incident that copy showed for **17 of 23**
pitcher-outs props while FanDuel was actively posting every one at a
different number. On a compact card that is worse than on the detail sheet:
the compact card is what people scan, and "not posted yet" reads as "check
back" rather than "this cannot be bet."

The new-line price is shown as information about the market and never
paired with this row's probability — that probability was computed for the
old threshold.

## G. Thresholds unified, and the recovery number justified

Three conflicting numbers became two, each with one owner:

| 180 min | start recovering (`reconcile.py`) |
|---|---|
| 4 h board / 45 m price | stop being actionable (`recommendation.py`, unchanged) |

The first pass used **90 minutes**, on the reasoning that 90 < 240. That is
not a justification. `dashboard-refresh.yml` declares eight windows at
13/15/17/19/21/23/01/03 UTC — a 120-minute cadence — so a 90-minute
threshold fires *before the next scheduled rebuild is even due*:

| threshold | fires early? | spurious/day | headroom | attempts |
|---|---|---|---|---|
| 90 min | **yes** | 8 | 150 min | 7 |
| 120 min | no (equal) | 8 | 120 min | 6 |
| 150 min | no | needs a real miss | 90 min | 4 |
| **180 min** | no | needs a real miss | 60 min | 3 |

120 equals the cadence, so any jitter trips it. 150 trips on 30 minutes of
ordinary scheduler lateness. 180 fires only once a window has genuinely
been missed and still leaves three full observe+rebuild cycles.

Checking that claim turned up something worth recording: **the windows are
not uniform.** They are 2-hourly through the evening and leave a **10-hour
overnight gap (03:00–13:00)** — which is exactly where the incident
happened. Recovery will fire a few times overnight when no cron is due.
That is correct rather than spurious: a board untouched for three hours is
stale regardless of why.

The conflicting 6-hour hard-fail concept and its module are deleted.

## H. Lineup Watch retired

Kept as an accelerator, since it can still start a rebuild sooner when it
does fire. No longer load-bearing, and its "within 10 minutes" claim is
removed from both the workflow and `check_lineups.py`.

## I. What the test failures taught us

Two browser tests failed in CI and the local sweep. Both were worth keeping.

`test_fail_closed_surfaces.py` asserted *"Walker Jenkins is absent"*. MLB
has since added him to the roster (`player_id 805805`), so his id resolves
and his rows are legitimately published.

**This changes how the incident reads: the outage ended because the DATA
changed, not because anything was fixed.** Production rebuilt at 19:34 UTC
for exactly that reason. The same failure recurs the next time a
Rotowire-projected player is not on the roster — the whole argument for the
quarantine. The assertion is now the real invariant: no published row may
lack a settleable subject, and every id is canonical rather than
synthesized.

`test_board_first_paint.py` waited for card selectors a fail-closed board
deliberately never renders — it renders the panel instead. `.fail-closed`
is now a legitimate terminal render state.

## J. Preserved unchanged

Walker Jenkins quarantine and the systemic identity guard · no synthetic
ids · first-paint live overlay · the four clocks · same-line price refresh ·
immutable published wagers · `first_inning_run` deliberately out of scope.

## K. Detection and recovery — precisely

The earlier version of this report said reconciliation "runs on Cloudflare,
so detection is reliable." That is too loose, and the loose version reuses
a statistic outside its evidence.

The real path:

```
Cloudflare cron → external heartbeat → workflow_dispatch of dashboard-live.yml
  → GitHub Actions EXECUTES it → reconciliation runs
  → possible dashboard-refresh.yml dispatch
  → GitHub Actions EXECUTES the full rebuild
```

Four distinct things, which must not be collapsed:

| trigger / dispatch reliability | **improved** by Cloudflare |
|---|---|
| observer execution latency | still GitHub Actions |
| rebuild execution latency | still GitHub Actions |
| publication reconciliation | the only thing that proves recovery |

The **9%** figure is evidence about GitHub's `schedule` trigger for Lineup
Watch. It is **not** evidence that externally dispatched runs execute 9% of
the time, and this report does not use it that way. Cloudflare materially
improves trigger reliability; it does not remove GitHub Actions from the
execution path. Under heavy queueing even a reliably dispatched
`dashboard-live` run can start late, and the rebuild it requests is a
second, separate execution with its own latency.

No hard detection bound is claimed here, because no measured execution
evidence supports one.

> **OBSERVATION IS NOT RECOVERY. DISPATCH IS NOT RECOVERY. RECOVERY IS
> PROVEN ONLY WHEN THE PUBLISHED CUSTOMER STATE RECONCILES TO THE
> AUTHORITATIVE CURRENT STATE.**

That invariant is why nothing in `reconcile.py` treats a dispatch as
closure.

## L. What the source now proves that it did not before

| Area | Evidence |
|---|---|
| Clock masking | `test_live_health_channels.py` — 13 tests, all **failing on `2ee82ed7`** (verified against a worktree at that commit), passing here |
| Lineup basis | `test_lineup_basis.py` — 17 tests over the twelve required cases, against the snapshot contract rather than candidate reconstruction |
| Games / routes | `test_route_fail_closed.py` — 60 checks in real Chromium, every route entered **directly**, under all four unverifiable conditions, plus a healthy control |
| Identity | `test_fail_closed_surfaces.py` calls the production `validate_payload_identities`, and proves it rejects a forged `fc2:` id |

## M. Corrections to the previous report

Two claims in the earlier version were not supported by the source and are
withdrawn:

- *"all ten items delivered"* — reconciliation was masking the global
  health clock, lineup reconciliation was candidate-derived, Games was a
  second frozen-copy surface, and four routes had no fail-closed path.
- *"secondary surfaces fully audited"* — the audit had covered the
  suggested parlay and missed Games entirely.

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

## G. Thresholds unified, and recovery keyed on a missed window

Three conflicting numbers became two, each with one owner:

| a missed scheduled window (+60 min grace) | start recovering (`reconcile.py`) |
|---|---|
| 4 h board / 45 m price | stop being actionable (`recommendation.py`, unchanged) |

Two earlier passes both keyed recovery on **raw board age**. The first used
90 minutes, on the reasoning that 90 < 240 — not a justification, and it
fires before the next scheduled rebuild is even due. The second used 180
minutes, justified as *"fires only once a scheduled window has genuinely
been MISSED."*

**That second justification was not true, and this section previously
recorded the disproof and then kept the number anyway.**
`dashboard-refresh.yml` runs at 13/15/17/19/21/23/01/03 UTC: 2-hourly
through the active window, then a deliberate **10-hour gap from 03:00 to
13:00** with no window at all. Any raw-age threshold under 600 minutes
trips inside that gap with nothing missed and nothing failing. Simulated
over a perfectly healthy day (every window building on time, 15 minutes to
complete, observer every 5 minutes):

| policy | full rebuilds on a healthy day | fires on a real miss |
|---|---|---|
| raw age 90 min | 8 scheduled + 8 recovery = **16** | yes |
| raw age 180 min | 8 scheduled + 2 recovery = **10** (06:20, 09:50 UTC) | yes |
| **missed window + 60 min grace** | 8 scheduled + 0 recovery = **8** | yes |

Two extra FanGraphs/Statcast/FanDuel pulls every healthy night is not
recovery. It is a supplemental overnight refresh schedule, implemented
inside a module labelled recovery — the wrong place for a scheduling
decision to live, and mislabelled where it did live.

So recovery is now keyed on the thing it claims to detect. A scheduled
window becomes *due* once it has had `REBUILD_GRACE_MINUTES = 60` to
produce a board; if the published board still predates that window, the
window was missed and recovery fires. Grace is 60 minutes because a full
rebuild takes 10–15 minutes plus GitHub Actions queueing and this repo's
scheduler runs routinely ~30 minutes late. Inside the active window this
reproduces the old 180-minute lead time exactly — previous build at T, next
window at T+120, recovery at T+180 — with 60 minutes of headroom to the
4-hour limit, three observe+rebuild cycles. Inside the gap it dispatches
nothing.

`reconcile.py` now declares `SCHEDULED_REBUILD_HOURS_UTC` and
`test_reconciliation.py::test_declared_windows_match_the_workflow_exactly`
asserts it equals the workflow's cron hours, so the policy cannot silently
drift from the schedule it is derived from.

### The gap itself, recorded rather than fixed

The 10-hour gap does conflict with the 4-hour actionability contract.
From roughly **07:00 UTC until the 13:00 build lands**, the board is
necessarily older than 4 hours, so `recommendation.py` suppresses it and
the front end fails closed. About six hours a night with no actionable
board.

That is a property of the **refresh schedule**, not of recovery, and it is
left unchanged here for two reasons. First, 03:00–13:00 UTC is 11pm–9am ET:
no games are in progress and the next slate's lineups are not posted, so a
rebuild in that window produces a fresh-but-near-empty board —
`quality_control` rejects candidates without a confirmed lineup. Fresh and
empty is not more actionable than stale and suppressed. Second, changing
the schedule is a product decision, and making it covertly by leaving a
raw-age recovery rule in place is exactly the failure this section is
fixing. If a continuously actionable overnight board is wanted, the fix is
to add cron windows to `dashboard-refresh.yml` deliberately.
`test_reconciliation.py::TestOvernightGap` pins both halves: the gap
exceeds the limit, and the board fails closed instead of being
"recovered".

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

Two more, added when the policy pass re-ran everything:

- *"180 minutes fires only once a scheduled window has genuinely been
  MISSED"* — false across the real schedule. Section G now carries the
  disproof and the replacement policy. This report had already recorded the
  10-hour gap that disproves it, in the same paragraph that kept the claim.
- *"full local suite, 131 files, zero failures"* — true when it was run,
  and not true an hour later. See section N.

## N. The suite that passed because it ran early

`test_browser_e2e.py` went from 98/98 to 91/98 with **no code change** —
the same bytes, the same commit, 50 minutes later. Verified by running it
against an unmodified checkout of the pre-policy HEAD, which failed the
same seven checks.

Cause: the P0 fail-closed work. `docs/live.json` is a committed fixture
carrying real timestamps, and once its `prices_updated_at` passes 45
minutes the board correctly fails closed, every card disappears, and the
seven checks that need a card to click hard-fail. It passed in CI at 21:05
UTC against a 20:50 fixture and failed at 21:57 against the same one.

This is the sharper version of a lesson already in this repo: **a suite
whose result depends on the data rather than the code will go green and red
on its own.** Left alone it would have failed most PR runs for reasons
unrelated to the diff, then gone green on a re-run after the next scheduled
build refreshed `docs/` — the worst possible signal.

The fix is in the suite's static server: `live.json` and `data.json` are
served with their `*_at` clocks rebased to test time. Nothing on disk is
mutated and no assertion is relaxed. This suite tests the interaction
contracts, which need a reachable board; the fail-closed contract is owned
by `test_fail_closed_surfaces.py` and `test_route_fail_closed.py`, which
build explicit stale fixtures and assert suppression directly. With the
rebase, 127/127 (more checks run, because the card-dependent branches are
reachable again).

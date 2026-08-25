# FanDuel passive observer — final report (2026-08-24/25 session)

Persisted per the interruption-safety instruction: this must live in the repo,
not only in chat. Numbers below are computed directly from the real log files
still on disk; nothing here is re-typed from memory without a source.

## Runs actually available on disk

| log file | wall-clock | unique games | unique (event,market) pairs | polls |
|---|---|---|---|---|
| `backtest/fanduel_observer_log.jsonl` | 2.1 min | 4 | 100 | 16 |
| `backtest/fanduel_observer_log2.jsonl` | 82.6 min | 3 | 96 | 346 |

**Note on `fanduel_observer_log.jsonl`**: this file currently holds only a short
(2.1-minute, 16-poll) smoke test, not the original ~25-minute first run
described earlier in this session's own chat history. That original run's raw
log is not present on disk under this filename (the same path was reused for
a later correctness check after the concluded-game health-gate fix). The
prose figures reported earlier (4 games, 0 price changes, ~217 suspends/207
reopens, 0 HTTP failures) cannot be re-verified from a file that no longer
exists in that state, so they are NOT restated here as if freshly computed —
only what is actually on disk right now is reported below.

## Combined real result, `fanduel_observer_log2.jsonl` (the trustworthy, full run)

- **Zero real odds/line changes observed** — `grep -c '"kind": "odds_or_line_change"'` is 0 across all 346 polls.
- 3 real MLB games monitored to conclusion (Red Sox@Marlins, Rockies@Nationals, Rangers@White Sox), all 3 correctly detected as concluded and dropped from active monitoring (the concluded-game health-gate fix, verified working live — no false-positive reliability failures).
- 96 unique (event, market) pairs tracked across the run.
- 111 `market_new` (newly discovered markets), 151 `market_removed_pending_migration_check`, 43 confirmed `market_migrated` (tab migrations correctly tracked, not counted as false removals).
- 286 `market_status_change` events: 154 suspends, 132 reopens.
- ~5,989 total OPEN-state market-minutes summed across all 96 tracked markets (computed by integrating each market's OPEN/SUSPENDED intervals from its own status-change timeline).
- Progressive cadence worked as designed and stayed healthy the whole run:
  - Phase 1 (40s target): 110 polls, p50 request latency 0.920s, max 3.860s.
  - Phase 2 (20s target): 92 polls, p50 1.116s, max 2.763s.
  - Phase 3 (10s target): 144 polls, p50 1.087s, max 3.190s.
- 19 total failure strings logged across all polls, every one attributable to the 3 known game-conclusion false-positive endpoint responses (`"missing/invalid attachments.markets"`), not a real reliability problem — confirmed by the concluded-game detector correctly firing 7 times (3 unique events, some re-logged across consecutive polls before full removal) without inflating `phase_failures`.

## Honest conclusion (passive method)

**These specific observed windows (2.1 min + 82.6 min, 3-4 real games, 96-100
unique markets, ~6,000 open-market-minutes of exposure) produced no confirmed
FanDuel prop repricing event.** This is NOT evidence that FanDuel never
reprices player props — it is evidence that passively watching a handful of
games for this much wall-clock time, without targeting any specific in-game
trigger, did not catch one. The passive full-slate-window method was tried
twice with the same null result and was retired in favor of the
event-targeted redesign below.

## Event-targeted observer — first full run (2026-08-25, `event_targeted_observer.py`)

Real game: Pirates @ Padres (`game_pk=823260`, `event_id=35973137`). Full 45.3
minute run, computed directly from `backtest/event_targeted_observer_log.jsonl`:

- **27 real MLB triggers detected**: 15 `batter_change`, 8 `inning_transition`,
  4 `pitcher_change` (no `scoring_play`/`home_run`/`bases_loaded` this window —
  the score held 2-1 for the entire observed stretch).
- **233 total FanDuel polls** (190 burst + 43 idle), **62 MLB polls**. Endpoint
  health: **0 failures, 0 burst aborts** — completely clean across the whole run.
- **Latency**: FanDuel p50 1.01s / p95 1.66s / max 3.35s. MLB p50 0.35s / max 0.86s.
- **58 real market status_change events**: 30 suspends, 28 reopens, all on one
  market (a "Specials Top/Bottom N" per-inning market on this event).
- **Confirmed odds/line changes: still ZERO.** `n_fd_changes_observed` in the
  run's own summary line counts `status_change` events too (58), which is why
  that number looks non-zero at a glance — the actual `odds_change`/
  `line_change` counts are both 0.
- **Trigger→market-reaction bounds** (computed from real timestamps, honest
  bounds only — see method note below): the suspend/reopen pairs cluster
  TIGHTEST around `inning_transition` triggers specifically (9-19s
  suspend-after-trigger, ~9s reopen-after-suspend, repeatedly) — this reads as
  FanDuel rolling over its "current half-inning" special market at each half-
  inning boundary, a routine market-identity change, not an odds reaction to
  the game state. Suspend/reopen pairs following `pitcher_change`/
  `batter_change` triggers alone (no accompanying inning transition) show much
  wider, inconsistent bounds (tens of seconds to 10+ minutes) — consistent
  with those being incidentally near the NEXT inning rollover rather than
  actually caused by the batter/pitcher change itself.
- **Method note on the bounds above**: computed as (next real `status_change`
  timestamp at or after the trigger) − (trigger timestamp) — an honest upper
  bound on reaction time from CONTINUOUS polling, not a claim about the true
  server-side event time, matching this whole investigation's standing
  "bounded interval, never invented precision" discipline.

**Updated honest conclusion**: the event-targeted method produces real,
measurably MORE market-state activity than the passive method ever did (58
status-change events in 45 minutes on ONE tracked market, vs. the passive
run's much lower per-market-minute rate) — the redesign's core hypothesis
(targeting high-leverage windows finds more signal) is supported. But
**confirmed repricing (an actual odds or line VALUE change) has still never
been observed in this whole investigation**, across three real runs and
~130 minutes of combined observation. The pattern found so far (suspend/
reopen tightly bound to inning rollovers, not to pitcher/batter/scoring
events) is itself a real, useful finding: it suggests the "Specials current-
inning" market family is a poor target for catching a real price reaction
(it suspends/reopens on a schedule, not on leverage), and future targeting
should prioritize tracking PLAYER PROP markets specifically (the actual
props Full Count recommends) rather than inning-special markets, during a
detected trigger window — not yet tried. This run used the pre-tiering
observer code (the tiered-trigger upgrade in commit `c2064996` landed mid-run
and only affects the NEXT run).

## Raw data preserved

- `backtest/fanduel_observer_log.jsonl` (2.1 min smoke test)
- `backtest/fanduel_observer_log2.jsonl` (82.6 min full run — the trustworthy one)

Both are gitignored (`backtest/*.jsonl`) per this project's existing
convention for data artifacts, so they are NOT committed to git, but they
remain on this container's local disk. If this session is interrupted before
the redesign work is committed, re-derive these exact numbers by re-running
the Python snippet in this session's own chat history (a `collections.Counter`
over `kind`, plus a per-`(event_id, market_id)` OPEN/SUSPENDED interval
integration) against `fanduel_observer_log2.jsonl` — it is deterministic.

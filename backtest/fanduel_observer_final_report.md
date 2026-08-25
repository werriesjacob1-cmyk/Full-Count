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

## Honest conclusion

**These specific observed windows (2.1 min + 82.6 min, 3-4 real games, 96-100
unique markets, ~6,000 open-market-minutes of exposure) produced no confirmed
FanDuel prop repricing event.** This is NOT evidence that FanDuel never
reprices player props — it is evidence that passively watching a handful of
games for this much wall-clock time, without targeting any specific in-game
trigger, did not catch one. The passive full-slate-window method has now been
tried twice with the same null result; continuing to run it unmodified on
future slates is low-expected-information-density. The next real test should
be the event-targeted redesign (see `backtest/event_targeted_observer_design.md`,
if that file exists — check for the newest design doc in `backtest/` covering
this): detect a real MLB high-leverage state transition (pitcher change,
scoring play, HR, inning turnover) via the MLB live feed, then temporarily
raise polling cadence only for that specific game/market set, holding
endpoint health as the guardrail on how aggressive that cadence can get.

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

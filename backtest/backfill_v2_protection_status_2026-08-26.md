# Backfill v2 protection status -- Part 1 (2026-08-26)

Written while the long canonical rebuild is still running, per the sprint
directive's Part 1 ("protect the long backfill first, before anything
destructive"). Every fact below was checked directly against the live
process and its output files at 2026-08-26 16:32 UTC, not assumed.

## Process

- PID 1817, elapsed 03:19:18 at check time, cmd:
  `/tmp/mlbvenv/bin/python3 backtest/engine.py --start 2024-04-01 --end
  2026-08-25 --out backtest/rows_backfill_v2.jsonl --no-weather --sleep 1.0`
- Started under HEAD `b0cca4972c996554bdafb97aa6bdd8f0be9108b9` (confirmed:
  this is the current branch HEAD on `claude/realized-hit-rate-sprint-01`,
  the exact merge commit of PR #67 -- single, integrity-fixed code regime,
  no drift since launch since nothing has been committed to this branch
  since).
- Requested date range: 2024-04-01 -> 2026-08-25 inclusive = 877 calendar
  days.
- Progress at check time: 111/877 dates iterated, 110 completed
  (107 `ok` + 3 legitimate `no_games` -- 2024-07-15/17/18, the MLB
  All-Star break window, consistent with the real 2024 schedule).
- A background watcher (separate from this process, polls `kill -0 1817`
  every 60s) is armed and will fire a notification the moment this process
  exits -- confirmed still alive and attached to PID 1817 at check time.

## Output artifact

- `backtest/rows_backfill_v2.jsonl`: 219,860 rows, 215,494,586 bytes at
  check time.
- `backtest/rows_backfill_v2.jsonl.state.json`: per-date checkpoint index,
  110 date entries, sum of per-date `rows` fields (219,860) matches the
  file's actual line count exactly -- no drift between bookkeeping and
  data.
- Both paths are covered by `.gitignore` (`backtest/*.jsonl` and
  `backtest/*.jsonl.state.json`) -- **this output lives only in this
  session's local container filesystem.** It has never been committed and
  was never intended to be (multi-hundred-MB raw research data, consistent
  with how the project already treats `backtest/rows.jsonl` /
  `backtest/rows_canonical.jsonl`).

## Safety contract (read directly from `backtest/engine.py`, not assumed)

- **Per-date atomicity** (`run_backtest()`, engine.py:1781-1799): a date's
  full row blob is built in memory and written with a single `f.write()`
  call inside one `open(..., "a")`, only after `simulate_date()` fully
  succeeds for that date. A process killed mid-date can never leave a
  partially-written date in the file -- confirmed by the function's own
  comment: "Building the full blob first and issuing ONE write() call...
  narrows that crash window... down to a single syscall."
- **State file atomicity** (`save_state()`, engine.py:1662-1666): written
  via `tmp` file + `os.replace()`, so a crash mid-save cannot corrupt or
  truncate `state.json`.
- **Resume correctness is double-checked, not just state-file-trusted**
  (`dates_already_in_output()`, engine.py:1669-1689): on resume, the actual
  JSONL is scanned for which dates are already present, and that is
  trusted OVER the state file specifically to guard against a crash
  landing between "rows appended" and "state saved." Re-running the exact
  same command would safely resume with no duplication.
- **Regime consistency is checked on resume** (`check_regime_consistency()`,
  engine.py:1692-1719): every row carries its own `code_git_sha`; a resume
  into a file with a different SHA already present prints a hard warning.
  Not exercised this run (single continuous process, no resume yet), but
  confirmed present and correct by direct read.
- **Duplicate-identity check** on the 219,860 rows written so far, keyed
  on `(date, game_pk, player_id, prop_type, line, side)` per the sprint
  directive's canonical identity definition: **0 duplicate keys, 219,860
  distinct keys for 219,860 rows.** Alternate lines are not being
  collapsed (verified: distinct `line` values for the same
  date/game_pk/player_id/prop_type/side are counted as distinct keys and
  none collided).

**Conclusion: the running process is healthy, safely resumable if
interrupted, and has produced zero identity violations so far. It was not
touched, killed, or restarted.**

## Durability improvement added (non-destructive)

The raw output exists only in this container's disk (confirmed above), and
this container is reclaimed at session end. Real off-container durability
(e.g. pushing 1-2GB of raw JSONL to git or external storage) is neither
appropriate (violates the project's own convention of never committing
these files) nor available with the tools in this session. What *was*
added, without touching PID 1817 or its files in any writing sense:

A read-only background snapshot loop
(`/home/user/backfill_checkpoints/snapshot_loop.sh`, started 16:32 UTC,
independent of PID 1817) that every 20 minutes:
- gzip-copies the current `rows_backfill_v2.jsonl` and its `.state.json`
  to `/home/user/backfill_checkpoints/` with a UTC timestamp,
- writes a `sha256sum` manifest for each snapshot pair,
- logs `<timestamp> rows=<count>` to `snapshot_log.txt`,
- prunes to the 3 most recent snapshot sets so disk usage stays bounded
  (disk headroom at start: 20GB available; final file is projected at
  roughly 1.7GB based on the observed 215MB/110-date rate scaled to 877
  dates, well within budget even with 3 gzip'd snapshots retained).

This protects against corruption of the single live copy (e.g. a bad write,
a bug, an accidental edit) by keeping recent point-in-time, checksum-
verified copies, and gives an audit trail of row-count growth over time.
It does **not** provide true off-container durability -- that limitation
is inherent to this environment and is reported here honestly rather than
overstated.

## What was NOT done

- The process was not killed, signaled, or restarted.
- No production file, workflow, or committed code was touched.
- No research (Part 2 onward) was started -- Part 8 explicitly reserves
  that until the rebuild is validated.

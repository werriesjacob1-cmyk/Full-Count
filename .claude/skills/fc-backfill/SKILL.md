---
name: fc-backfill
description: Check on or resume a long-running canonical backtest backfill (backtest/engine.py) safely. Use to check whether a backfill is still running, whether it finished, or whether it's safe to resume -- never to casually restart one.
---

# fc-backfill

A long backfill runs as an ordinary OS process, not a Claude agent --
`ListAgents` cannot see it and answers a different question. Checking
"is Claude still working on this" is not the same check as "is the backfill
still running." This skill exists because that exact mixup already happened
once on this project.

## Steps

1. OS-level check, not agent-level:
   - `kill -0 <pid>` -- exit 0 means the process exists.
   - `ps -p <pid> -o pid,ppid,etime,pcpu,pmem,cmd` -- confirms it's the
     right command, and elapsed time + %CPU distinguish "actively working"
     from "hung."
2. Check the canonical state/progress file next to the output
   (`<output>.state.json` for `backtest/engine.py` runs): completed date
   count, most recent completed date, any `status` other than `ok`/`no_games`.
3. Check the output file itself: row count (`wc -l`), size, and mtime
   compared to "now" -- a file whose mtime is seconds old with an alive,
   CPU-active PID is strong corroborating evidence of real progress, not
   just a claim.
4. Confirm the worktree the backfill runs in is otherwise clean (read-only
   `git status --short`) -- do not modify it as part of this check.
5. Classify exactly one:
   - **RUNNING HEALTHY** -- PID alive, real CPU activity, output/state
     file fresh and growing.
   - **COMPLETED SUCCESSFULLY** -- PID gone, state file covers the full
     intended date range with no unexpected error statuses.
   - **DEAD BUT RESUMABLE** -- PID gone, state file stops partway with no
     corruption; the run can restart from the last completed date.
   - **DEAD / INTEGRITY UNCERTAIN** -- PID gone, state file missing,
     truncated, or shows an error pattern that needs investigation before
     trusting a resume.
6. Do NOT restart or modify anything merely because a PID looks gone --
   confirm the classification first. A resumable backfill resumes from its
   last completed date; it does not restart from scratch.
7. If completed, validate the canonical artifact contract (single verified
   `code_git_sha`, `provenance.require_single_regime()` passes) before any
   downstream experiment treats it as canonical.

## When NOT to use

Short/interactive backtest runs that finish in the same turn -- this is for
the long unattended kind that outlives a single session.

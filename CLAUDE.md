# FULL COUNT — Claude operating instructions

@AGENTS.md

Full Count is an MLB player-prop betting analytics platform. It fetches real
slates, scores candidates, prices them against FanDuel, publishes a board, and
grades itself against real box scores the next morning. Real money follows the
public board, so the standard for a claim here is higher than "the tests pass."

Path-scoped rules live in `.claude/rules/` and load automatically for the files
they cover: `research.md`, `live.md`, `frontend.md`, `tooling.md`.

---

## The objective, in priority order

1. **Realized MLB prop hit rate at the same usable pick volume.** This is the
   North Star. Everything else is instrumental.
2. Fresh, live intelligence.
3. Customer-facing product quality.
4. UX polish.

Calibration work is worth doing only when it moves (1). A prettier reliability
curve that does not change realized hit rate at equal N has not earned its place.

---

## Three evidence regimes — never pool them

| Regime | What it is | What it can support |
|---|---|---|
| **Canonical backtest** | Historical reconstruction under `backtest/` | What the model *would have* computed |
| **Prospective capture** | Full candidate set recorded live, before selection | What the product *could have* offered |
| **Public ledger** | Hash-chained published Top Picks | What was actually offered as a wager |

Only the third supports a claim about deployed performance. A number quoted
without naming its regime is not a measurement. Historical reconstruction is
**confirmed-starting-lineup historical evidence** — it is *not* an exact replay of
what Full Count knew at a specific historical publication timestamp, because some
market and live context inputs are unavailable historically. Never fabricate the
missing inputs to close that gap.

---

## The production-science freeze

These are frozen and require **explicit human authorization** before any change:

- probability formulas, scoring formulas, signal weights
- calibrators and calibration parameters
- recommendation thresholds, Top Pick policy, selector behavior
- market rules, odds/de-vig semantics
- settlement and grading (`dashboard/settlement_rules.py`,
  `dashboard/refresh_grades.py`, `grade_results.py`)
- public lifecycle semantics
- the LOCKED disagreement experiment

Propose freely. Do not merge, deploy, or quietly "improve" any of them.

---

## Durability — the rule written in blood

**On 2026-08-27 an idle container was reclaimed and roughly 90 minutes of
uncommitted tooling work plus an in-progress canonical row artifact were
destroyed.** The local git object store went with the filesystem, so a
local-only autosave snapshot rescued nothing.

The invariant that follows:

> No meaningful work product may exist only inside the ephemeral container for
> longer than one small logical unit of work.

In practice:

- Commit and push after each coherent unit — not at the end of the session.
- Push **before** any long command, idle period, context-heavy investigation, or
  multi-hour run.
- A local ref is not durability. A pushed remote branch is.
- Long-running generators must persist resumable state *remotely* on a bounded
  cadence, so container loss costs a bounded amount of work.
- Never rely on the Claude conversation as the recovery mechanism.

---

## Merge discipline

- Never merge or deploy without explicit authorization, even when CI is green.
- Verify the *real* base with a fresh `git fetch` and `git merge-base`. Never
  quote a SHA you have not just resolved; a stale local ref has already produced
  one factually wrong report on this project.
- Check CI at the **exact head SHA**, not at an earlier commit.
- Read the actual diff. Never the commit messages, never an author's self-report.
- Determine "generated-only" by inspecting changed *paths*, never by reading a
  commit message that says "Dashboard live update".

---

## Worktree isolation

- One worktree, one branch, one job. Never mutate another worktree.
- Never advance a pinned detached HEAD — it is pinning a run's code identity.
- A long backfill is an OS process, not a Claude agent: `ListAgents` answers a
  different question. Check `ps -p <pid>`, and prefer `/proc/<pid>/stat` field 22
  (`starttime`) plus `/proc/sys/kernel/random/boot_id` — a bare PID is recycled,
  and a changed `boot_id` means the container restarted and everything local died.

---

## Honesty rules

- Absent is not zero and not neutral. A missing signal must degrade confidence,
  never silently become favorable evidence.
- Never fabricate a historical sportsbook price, and never claim exact historical
  production eligibility from data that carried no price.
- Self-certification is not evidence. Independent review is.
- Report what actually happened — failed tests, skipped steps, partial results —
  including your own errors, plainly and without burying them.
- A bug found deserves a test that fails against the old code first.

## Running the tests

```
for f in test_*.py; do python3 "$f" || echo "FAIL: $f"; done
```

`.github/workflows/test.yml` runs this by glob, so a new test file is never
forgotten.

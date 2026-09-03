# FULL COUNT — Claude operating instructions

@AGENTS.md

Full Count is an MLB player-prop betting analytics platform. It fetches real
slates, scores candidates, prices them against FanDuel, publishes a board, and
grades itself against real box scores the next morning. Real money follows the
public board, so the standard for a claim here is higher than "the tests pass."

Path-scoped rules live in `.claude/rules/` and load automatically for the files
they cover: `research.md`, `live.md`, `frontend.md`, `tooling.md`.

---

## The control plane — configuration intent, not proven runtime

`.claude/` carries 9 subagents, 10 skills, 4 path-scoped rules and
`.claude/settings.json`. `.claude/CAPABILITY_MATRIX.md` is the index: what each
one is for, what it may write, and how to verify it is actually working.

Read it as **configuration intent**. It states what a compliant client would do
with these files; it is not a record of behaviour observed in a running session.
`bash .claude/tests/test_superclaude_acceptance.sh` checks that the
configuration is internally consistent — names resolve, fork targets exist,
read-only reviewers hold no mutating tool — and it deliberately marks runtime
enforcement `INFO`, never `PASS`, because a shell script cannot prove a session
honoured a permission rule.

Two limits worth knowing before you rely on any of it:

- **`permissions.ask` is a prompt, not a boundary.** It is prefix matched and
  hooks bypass the permission system entirely. It cannot substitute for
  server-side GitHub branch protection, which is **not** configured on this
  repository.
- **The five read-only reviewers are read-only by declaration.** They are
  granted no `Write`/`Edit` and no mutating MCP tool, and the acceptance suite
  fails if that changes — but the enforcement lives in the client, not here.

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
- **Push only to this session's own working branch.** Never to `main`, never to
  `gh-pages`, never to an evidence branch (`prediction-ledger/*`,
  `prospective/*`, `evidence/*`, `canonical/*`), and never to another session's
  branch. This standing instruction authorises a routine backup of your own
  work; it is not authority over shared refs, and a security review was right
  to note that without this sentence the destination was bounded only by
  convention. Merging, tagging, releasing and deploying are Jacob's, always.
- A local ref is not durability. A pushed remote branch is.
- **`Bash(git push:*)` in `ask` is a prompt, not a boundary.** It is prefix
  matched, so `git -c x=y push`, `env git push` or a wrapper script evades it,
  and hooks bypass the permission system entirely. The only real control over
  `main`, `gh-pages` and the evidence branches is server-side branch
  protection, which is NOT currently configured on this repository.
- Long-running generators must persist resumable state *remotely* on a bounded
  cadence, so container loss costs a bounded amount of work.
- Never rely on the Claude conversation as the recovery mechanism.

### There is no automatic autosave

`.claude/worktree-autosave.sh` exists in the repository but **nothing invokes
it**. No hook launches it, and this configuration deliberately does not add one.
A hardened variant was written and reviewed three times; each round found real
defects faster than they could be closed, so it was dropped rather than shipped
on the strength of a story about what it would have prevented.

The consequence is the point: **nothing is backing your work up.** The bullets
above are the whole mechanism. Commit and push, or lose it.

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

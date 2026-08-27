---
name: fc-context-keeper
description: Write or refresh the compact handoff checkpoint for the current worktree. Use before ending a long session, before /clear, before handing work to another Claude session, after a major milestone, or when context has gotten noisy. Does not change production science.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# fc-context-keeper

Full Count sessions run long and span several worktrees, a multi-hour backfill,
and frozen research artifacts. The failure this prevents is not "forgetting" —
it is **remembering something that stopped being true** and then acting on it. A
checkpoint claiming a PID is alive is worse than no checkpoint at all.

The checkpoint lives at `.claude/context/<branch-slug>.md`. It is **gitignored
and volatile by design**: it describes one worktree at one moment, and a
committed copy would be a stale claim about a different moment.

## Steps

1. **Collect live state first**, never from the previous checkpoint:

   ```
   bash .claude/skills/fc-context-keeper/checkpoint.sh
   ```

   It prints the real worktree state and, for every PID recorded in the previous
   checkpoint, **re-checks whether that process is alive right now**. Its answer
   replaces the old claim unconditionally.

2. **Fill in the narrative sections yourself** — the collector cannot know the
   mission, the blockers, or the next action.

3. **Replace, do not append.** Overwrite the file. A checkpoint that grows is a
   checkpoint nobody reads. A fact that stopped being true gets deleted, not
   annotated with a correction.

4. **Verify** every SHA, run ID and path is copied exactly, character for
   character.

## The three fact classes — label every line

| Class | Means | Example |
|---|---|---|
| `DURABLE` | True regardless of when it is read; content-addressed or historical | a commit SHA, a dataset fingerprint, a merged PR, a recorded result |
| `VOLATILE` | Was true when written; must be re-verified before use | branch HEAD, dirty state, a PID, job progress, CI status |
| `UNVERIFIED` | A hypothesis or an unchecked claim | "probably an upstream revision" before the 2×2 was run |

An unlabeled line is a bug. If you cannot decide, it is `UNVERIFIED`.

## What must survive verbatim — never paraphrase

- **Provenance**: pinned code SHA, generation-regime fingerprint, repository identity.
- **Experiment identity**: run IDs, holdout lock state, whether an experiment is
  LOCKED and NOT YET RUN.
- **Dataset fingerprints**: manifest sha256, checkpoint counts, logical fingerprints.
- **Safety gates**: which passed, which are still open.
- **Merge state**: what is merged, what is STOP-BEFORE-MERGE, what authorization
  is outstanding.
- **Durability state**: what is pushed to a remote branch versus what exists only
  in this container. Anything in the second category is one idle timeout from
  being destroyed.

Compressing these into prose ("provenance verified", "the backfill is healthy")
destroys the only thing that made them useful. Keep the number.

## Hard rules

- **A checkpoint is never evidence.** Re-derive facts from the repo, the artifact,
  or the process. Citing the checkpoint back as proof is circular.
- **Never claim a process is alive from a checkpoint.** PID 1633 was recorded as
  running for hours after it died; PID 3988 was recorded alive minutes before the
  container that hosted it was reclaimed. Re-check with `ps -p <pid>`, and prefer
  `/proc/<pid>/stat` field 22 plus `/proc/sys/kernel/random/boot_id` — a bare PID
  is recycled, and a changed `boot_id` means everything local is gone.
- **Exact identifiers only.** `022c8829`, not "the pinned SHA".
- **One worktree per checkpoint.** Never describe or write into another worktree.
- **No production science.** This skill records state; it changes nothing.

## Template

```markdown
# FULL COUNT — checkpoint: <branch>
_Written <UTC>. VOLATILE unless a line says DURABLE._

## Objective
<what this worktree is for, and the authorizing mission>

## Position
- DURABLE  base main: <sha>
- VOLATILE worktree/branch/HEAD/dirty
- DURABLE  pushed through: <sha on origin>   <- what actually survives container loss
- DURABLE  merge state: STOP BEFORE MERGE / merged as <pr> / n/a

## Live processes (re-verified at write time; re-verify again before use)
- VOLATILE <run id> pid <pid> — <alive|dead as of ts> — boot_id <id>

## Done
- DURABLE <thing> — evidence: <exact path or sha>

## Open
- <blocker, and what specifically unblocks it>

## Next action
<one concrete action, precise enough to start without re-reading anything>

## Artifacts
- <label>: <exact path> (<fingerprint>)
```

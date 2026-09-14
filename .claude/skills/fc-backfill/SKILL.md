---
name: fc-backfill
description: Inspect, start, resume or monitor a canonical backtest backfill safely. Use to check whether a run is alive, how much is durably safe, or whether resuming is warranted — never to casually restart one. Read-only by default.
allowed-tools: Read, Grep, Glob, Bash
---

# fc-backfill

A backfill is an **ordinary OS process**, not a Claude agent. `ListAgents`
answers a different question and has already caused this exact confusion once.
Everything below is read-only unless a step says otherwise.

## The distinction this skill exists to protect

> **Durability succeeding is not scientific certification.**

A run can push every date to the durable branch, with zero failures, and still
produce an artifact that is **NOT CANONICAL**. Durability says the bytes
survive. Certification says the bytes mean what you think. They are separate
verdicts from separate workflows — this skill never issues the second one. That
is `fc-canonical-certify`, via `fc-canonical-certifier`.

**Current standing example:** the live run's durable index records
`source_lineage: []` and `source_lineage_fingerprint: null`. Durability is
working perfectly. Certification is BLOCKED on source lineage.

## Health check — read-only, always safe

Requires an **explicit run id**. "The newest run" is not an identity.

```bash
RUN=<run_id>; WT=<pinned worktree>; R=$WT/backtest/canonical_runs/$RUN
# Count REAL interpreters, not every process whose command string happens to
# contain the text. A naive grep also matches the parent shell, and -- the way
# this actually bit -- the health-check command itself, reporting 2 generators
# when there was 1. Match on comm (the executable), not on the cmdline.
ps -eo pid,comm,cmd --no-headers \
  | awk '$2 ~ /^python/ && /canonical_run\.py/ {print $1}'   # MUST be exactly one
awk '{print $22}' /proc/<pid>/stat                        # starttime
cat /proc/sys/kernel/random/boot_id
python3 -c "import json;print(json.load(open('$R/lock.json')))"   # owner pid + run id
ls $R/checkpoints | grep -c meta.json
git -C $WT rev-parse HEAD          # must equal the manifest's code_git_sha
git -C $WT symbolic-ref --quiet HEAD && echo 'NOT DETACHED — problem'
```

Then the durable side:

```python
import backtest.canonical_durability as cd
cd.fetch_durable_branch()
for r in cd.discover_durable_runs():
    print(r["run_id"], r["dates"], r["code_git_sha"], r["updated_at"])
```

If that prints more than one PID, confirm each is a real interpreter
(`readlink /proc/<pid>/exe`, `cat /proc/<pid>/comm`) and that they are not
parent/child of one another (`awk '{print $4}' /proc/<pid>/stat`) before
declaring a duplicate. A wrapper shell is not a second generator.

**Identity, not names.** `lock.json`'s `pid` + `run_id` is what proves which
process owns which run — the run id is often absent from the cmdline, because
the first invocation creates the run rather than passing `--run-id`. A bare PID
proves nothing: the kernel recycles numbers, so compare `starttime`, and a
changed `boot_id` means every process from the old container is dead and every
local-only file is gone.

## STOP immediately and report, do not improvise

- **More than one generator** for the same run id — duplicate writers corrupt.
- **Any `DURABLE PUSH FAILED`** — the run has silently lost its protection.
- **Pinned HEAD changed**, or the pinned worktree is no longer detached.
- **Any `error`-status checkpoint**, `CodeIdentityDrift`, `IdentityMismatch`,
  `DurableIntegrityError`, or `CacheIntegrityError`.

## Starting a run

```bash
python3 backtest/canonical_run.py --start <D1> --end <D2> \
    --no-weather --sleep 1.0 --cache-mode frozen_cache
```

Durable push is **on by default here and only here** — `run()` defaults it OFF
for library callers, because with it on the ordinary test suite pushed 40
synthetic run ids to the real durable branch in about three minutes.

Run from a **pinned detached worktree** at the SHA you intend to be the
scientific identity. Never from a branch that will move under you.

## Resuming after a container loss

```bash
bash backtest/resume_canonical.sh <run_id>
FC_RESUME_DRY_RUN=1 bash backtest/resume_canonical.sh <run_id>   # inspect first
```

It reads the pinned SHA off the durable index, checks it out detached, restores
checksum-verified checkpoints **from the current checkout** (restoring bytes is
regime-neutral; only generation must run at the pinned SHA), then generates.

### Known limitations — state these, do not paper over them

The recovery path is directionally right and **not yet hardened for unattended
use**. Still outstanding:

- targets the newest durable run when no run id is given, rather than requiring one
- no healthy-owner detection, so it does not cleanly NO-OP against a live run
- `pgrep` can report success for a process this invocation did not launch
- run contract comes from CLI arguments, not from the durable manifest
- concurrent invocations are not proven to yield exactly one owner
- a completed run's NO-OP path is not proven clean

**Do not create a scheduled auto-resume Routine.** Not until those are closed
and a human authorizes it separately.

## Never

Restart, stop, or signal a healthy run. Mutate another worktree. Advance a
pinned detached HEAD. Rewrite rows. Launch a second generator for one run id.
Rewrite history on `canonical-durable-checkpoints`. Call anything
canonical-certified.

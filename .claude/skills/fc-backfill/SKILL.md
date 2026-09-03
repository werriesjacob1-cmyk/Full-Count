---
name: fc-backfill
description: Inspect, start, resume or monitor a canonical backtest backfill safely. Use to check whether a run is alive, how much is durably safe, or whether resuming is warranted — never to casually restart one. Read-only by default.
allowed-tools: Read, Grep, Glob, Bash
---

# fc-backfill

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

**Never copy a cache mode from an example.** Source vintage is part of the run's
scientific identity.

For a BRAND-NEW run, the operator must explicitly choose and record the source
contract before generation:

- run id / date range
- pinned detached code SHA
- `cache_mode` (`fresh_source` or `frozen_cache`)
- exact source-cache path / source artifact identity
- weather mode
- sleep / durability cadence
- environment identity

Only then launch from the pinned detached worktree, using the exact recorded
values. A schematic command is intentionally parameterized:

```bash
python3 backtest/canonical_run.py --start <D1> --end <D2> \
    --no-weather --sleep 1.0 --cache-mode <RECORDED_CACHE_MODE>
```

Durable push is **on by default in the CLI path and only there** — library
callers keep it off so ordinary tests cannot push synthetic scientific state.

## Resuming after a container loss

Recovery is **restore-then-generate**, never "run the helper and trust its
defaults."

The helper may be inspected first:

```bash
FC_RESUME_DRY_RUN=1 bash backtest/resume_canonical.sh <EXPLICIT_RUN_ID>
```

but do **not** execute it unmodified until its generated command has been checked
against the durable manifest/source contract.

Standing real example, 2026-08-29:

- `canonical-20260828T153143Z-2b79304f` records `cache_mode=fresh_source`.
- the current `resume_canonical.sh` appends `--cache-mode frozen_cache` when
  the pinned runner exposes that flag.
- running that helper unmodified would therefore change source semantics while
  preserving the same run id — an identity violation, not a harmless default.

The safe recovery sequence is:

1. require the explicit run id;
2. fetch the durable branch and verify its run identity;
3. prove no live owner already exists (PID + starttime + boot id, not `pgrep`
   text matching alone);
4. fetch/checkout the exact pinned SHA detached. For the current canonical run,
   the protected remote ref is
   `claude/canonical-source-identity-01 -> fc589447ec157bff9a96071edc3ceb6c7dc734eb`;
5. restore the durable manifest/checkpoints/source material into the run
   directory **before** invoking the pinned generator;
6. verify the exact source checksum/fingerprint and environment identity;
7. derive every generation flag from the restored run contract — especially
   cache mode. Never substitute a neighboring cache or generic default;
8. preserve the original full date range. Do not use `--max-dates` or alter
   `--end` merely to chunk a resume;
9. launch exactly one generator;
10. call recovery proven only after at least two new remote durable checkpoints
    advance consecutively.

On the pinned runner, `load_manifest()` happens before any
`--resume-from-remote` behavior can bootstrap an empty run directory, so a
generator flag alone is not a recovery mechanism. Restore first.

### Known limitations — state these, do not paper over them

The repository helper is useful scaffolding and **not safe as an unattended
one-command recovery authority**. At minimum:

- if no run id is supplied it can choose the newest durable run; this skill
  forbids that and always requires an explicit id;
- healthy-owner/no-op behavior is not proven strongly enough for blind retries;
- `pgrep` can mistake a pre-existing process for one this invocation launched;
- its generated cache mode is not derived from the durable manifest;
- concurrent invocations are not proven to yield exactly one owner;
- a completed run's NO-OP path is not proven clean.

**Do not create a scheduled auto-resume Routine.** Not until those are closed
and a human authorizes it separately.

## Never

Restart, stop, or signal a healthy run. Mutate another worktree. Advance a
pinned detached HEAD. Rewrite rows. Launch a second generator for one run id.
Rewrite history on `canonical-durable-checkpoints`. Call anything
canonical-certified.

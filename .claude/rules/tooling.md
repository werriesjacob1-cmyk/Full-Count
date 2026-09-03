---
paths: [".claude/**", "CLAUDE.md", "AGENTS.md", "engineering/**"]
---

# Tooling rule

Tooling changes are infrastructure for the whole project, so they get the same
adversarial standard as production code — with one extra hazard: a broken tool
fails *silently* and keeps looking healthy.

## Durability is the point

The failure this tooling exists to prevent is: **important work existing only
inside an ephemeral container.** An idle container is reclaimed and re-cloned;
the filesystem, the git object store, and any local-only ref go with it.

- Remote-durable by default for research/tooling branches. A local ref is not a
  backup.
- `refs/fc-autosave/*` and similar custom namespaces are **rejected by this
  host's git credentials with HTTP 403** at the RPC (a `--dry-run` misleadingly
  succeeds). Use `refs/heads/*`.
- Bound the loss: persist remotely on a stated cadence, and document the bound.

## Least privilege

- Read-only reviewers get no `Write` and no `Edit`. An auditor that can edit the
  thing under audit is not an auditor.
- Domain agents get their domain only. No `Write`/`Edit` "just in case."
- Never grant a subagent authority the main thread would not exercise itself.

## Autosave / hook safety

Any autosave must: operate on the **current worktree only**; refuse `main`,
`master`, and detached HEAD; use a scratch `GIT_INDEX_FILE`; leave HEAD, the
index, and `git status --porcelain` byte-identical; filter sensitive filenames
and oversized files and *report* what it skipped; and report a failed push
honestly rather than masking it.

Prove it in a disposable sandbox before enabling it. Hooks compose across
settings sources — the harness owns `SessionStart` and a `Stop` hook that exits 2
on uncommitted/untracked files, so volatile tooling state must be gitignored or
it wedges the end of every session.

## Honesty

- Deny rules on `Read`/`Edit` do **not** constrain Bash subprocesses. Say so
  rather than implying a security boundary that does not exist.
- Verify a runtime capability against the runtime before depending on it. Do not
  copy a frozen model name, tool name, or MCP identifier forward blindly.
- A checkpoint is never evidence. Re-derive facts from the repo, the artifact, or
  the process — never from a summary of them.

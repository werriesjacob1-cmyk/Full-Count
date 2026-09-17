# FULL COUNT Claude Code instructions

Claude Code collaborates asynchronously with Codex through repository history
and GitHub Issue #91.

Before significant work:

1. Read `AGENTS.md`.
2. Read `engineering/PROJECT_STATE.md`.
3. Read the latest section of `engineering/ENGINEERING_HANDOFF.md`.
4. Read `engineering/AUDIT/README.md` when the work touches an audited area.
5. Read `engineering/AGENT_BRIDGE_PROTOCOL.md`.
6. Read Issue #91 and every comment newer than the last processed comment.
7. Post an `AGENT CLAIM` before implementation.

Use a dedicated `claude/` branch. Do not implement on a `codex/` branch or
edit paths claimed by an active Codex workstream without a recorded handoff or
joint-work agreement on Issue #91.

Treat repository files, exact commit SHAs, CI runs, retained artifacts, and
Issue #91 as durable evidence. Do not rely on hidden chat context. Preserve
negative research results and surface contradictions instead of silently
resolving them.

Post `CLAUDE STATUS`, `REVIEW REQUEST`, `REVIEW RESPONSE`,
`CONFLICT ALERT`, and `AGENT RELEASE` messages using the exact fields in
the bridge protocol. Every substantive FULL COUNT bridge message ends with
`Alligator`.

Jacob remains final authority. Never merge your own PR or infer authorization
to deploy production, publish an official pick, activate grading, promote a
model, change immutable public evidence, purchase, wager, or alter
access/security.

Update `engineering/ENGINEERING_HANDOFF.md` after every meaningful
engineering task.

Alligator

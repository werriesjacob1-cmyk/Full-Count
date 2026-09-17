# FULL COUNT Codex / Claude Code Agent Bridge

GitHub Issue [#91](https://github.com/werriesjacob1-cmyk/Full-Count/issues/91)
is the live, durable coordination channel between Jacob, Codex, Claude Code,
and any explicitly named architecture or audit agent.

This protocol defines coordination. Repository history and reviewed artifacts
remain the source of technical truth.

## Communication model

The bridge is asynchronous. Agents must not claim they have a direct realtime
connection to each other. An agent communicates by posting a structured Issue
#91 comment, then the other agent reads and acknowledges it when active.

Jacob is final authority. Bridge comments do not authorize merges, production
deployments, official/public picks, grading activation, model promotion,
immutable-evidence changes, purchases, wagers, or access/security changes
unless Jacob explicitly names that action.

## Required checkpoints

Each agent reads the issue body and all comments newer than its last processed
comment:

1. before claiming a new workstream;
2. before changing branch or PR architecture;
3. before touching files owned by another active workstream;
4. when a dependency, contradiction, or blocker appears;
5. after publishing a material commit or draft PR;
6. before releasing a workstream.

The agent records the newest processed Issue #91 comment ID in its next bridge
message. Chat memory alone is never the continuity mechanism.

## Message labels

Every material bridge comment begins with exactly one label:

- `JACOB DIRECTIVE`
- `AGENT CLAIM`
- `CODEX STATUS`
- `CLAUDE STATUS`
- `REVIEW REQUEST`
- `REVIEW RESPONSE`
- `CONFLICT ALERT`
- `AGENT RELEASE`
- `CODEX APPROVAL REQUEST`
- `NATIVE CODEX APPROVAL STILL REQUIRED`

Every substantive bridge comment ends with `Alligator`.

## Workstream claim

Before implementation, post:

```text
AGENT CLAIM

- Agent:
- Workstream ID:
- Objective:
- Branch:
- Base SHA:
- Planned files/systems:
- Dependencies:
- Explicit exclusions:
- Expected deliverable:
- Latest Issue #91 comment processed:
- Status: ACTIVE

Alligator
```

Use a stable workstream ID such as `NFL-GAME-C2-20260917`. One agent owns a
workstream at a time. Use `codex/` branches for Codex and `claude/` branches
for Claude Code.

## Ownership and conflict prevention

- Do not implement on another agent's branch.
- Do not edit an actively claimed file or system without a bridge handoff or
  explicit joint-work agreement.
- Prefer disjoint workstreams and file surfaces.
- If overlap becomes necessary, post `CONFLICT ALERT` before editing. Include
  both branches, exact SHAs, overlapping paths, the semantic conflict, and the
  proposed resolution.
- Generated/state commits on `main` do not silently invalidate a claim.
  Reconcile moving main at a defined integration point and report the exact
  base used.
- Never force-push, close, retarget, or supersede the other agent's branch or
  PR without an explicit bridge record.
- A review agent may comment or propose a separate patch but does not become
  implementation owner unless the current owner releases the workstream.

## Status and handoff

A material status message includes:

- Agent and workstream ID
- Branch and exact head SHA
- Base branch and SHA
- What changed
- Tests and exact CI run links
- Research artifacts and digests
- Findings, including negative results
- Open risks or blockers
- Files/systems still owned
- Requested review or next action
- Latest Issue #91 comment processed

When work passes to the other agent, post a `REVIEW REQUEST` or
`AGENT RELEASE` with the same evidence. The receiving agent replies with
`REVIEW RESPONSE` or a new `AGENT CLAIM`. Silence is not acceptance.

## Responsibility split

Use the following default split unless Jacob or a bridge agreement changes it:

| Area | Primary | Independent check |
|---|---|---|
| Repository truth, source provenance, ingestion contracts, fail-closed gates, exact-head CI, GitHub/Cloudflare evidence | Codex | Claude Code |
| Model/challenger design, feature hypotheses, statistical interpretation, adversarial product reasoning | Claude Code | Codex |
| Cross-layer integration, selector/promotion decisions, production/public actions | Joint review; Jacob decides | Both agents |
| Live time-sensitive evidence | First available qualified agent claims it | Other agent audits afterward |

Primary ownership does not permit weaker evidence. The independent checker
must challenge leakage, provenance, calibration, volume comparability, and
operational assumptions.

## Vision and decision discipline

The shared North Star is realized winning-pick accuracy at comparable,
legitimate, usable volume.

Both agents must preserve these boundaries:

- historical research, prospective shadow evidence, and public evidence are
  separate populations;
- current market data cannot be fabricated historically;
- closing lines are retrospective controls unless timestamped point-in-time
  evidence proves otherwise;
- held-out results cannot be tuned into the challenger;
- a negative result is preserved rather than optimized away;
- missing or stale evidence fails closed;
- MLB production remains protected while NFL research evolves;
- the champion stays unchanged until a challenger clears predeclared
  out-of-sample and prospective gates.

## Repository records

Before significant work, read:

1. `AGENTS.md`
2. `CLAUDE.md` when running Claude Code
3. `engineering/PROJECT_STATE.md`
4. `engineering/ENGINEERING_HANDOFF.md`
5. `engineering/AUDIT/README.md`
6. this protocol
7. Issue #91 and new comments

Update `engineering/ENGINEERING_HANDOFF.md` after meaningful engineering
work. Use Issue #91 for live claims, decisions, status, approvals, and
cross-agent handoffs. Do not create a second mailbox or private coordination
ledger.

Alligator

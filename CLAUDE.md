# FULL COUNT — Claude Code core instructions

**Roles:** Jacob is final authority. SUPERCHAD is the strategy, audit and adjudication control plane. Codex is the independent challenger. Claude and Codex coordinate through the ops-state branch and compact Issue #91 checkpoints.

## Orient (default: one call, about 2k tokens)
```
git fetch -q --depth 1 origin claude/full-count-ops-state && git show FETCH_HEAD:engineering/ops/bin/fc.py | python3 - orient [TASK_ID]
```
- This prints CURRENT_STATE, the work queue and your task capsule. Then read only the code and scoped instructions your task touches.
- The operating contract (lanes, claims, challenge, checkpoints, batching, polling, subagents, session ceiling, model routing) is `engineering/ops/OPERATING_CONTRACT.md` on that branch. Read it for queue, bridge or challenge work.
- `engineering/ENGINEERING_HANDOFF.md` is **frozen history**. Issue #91 history, `PROJECT_STATE.md` and `AUDIT/README.md` are reference material. Read them only when the task needs them (history reconstruction, system map, audited area), and say so.

## Invariants (never relaxed)
- **Jacob's explicit, exact-SHA authorization is required for:**
  - merge, deploy, model promotion;
  - production model, selector, pick or public-ledger change;
  - official/public picks; activation or grading activation;
  - prereg or immutable-evidence change;
  - purchase or wager;
  - access or security change.
- Never merge your own PR. Never self-approve: consequential work gets an independent challenge.
- **Scientific integrity:**
  - keep historical, prospective and public-ledger evidence separate;
  - exact code and data identity, with point-in-time provenance;
  - no outcome leakage; no invented historical prices;
  - equal legitimate opportunity in comparisons;
  - never backfill prospective evidence;
  - preserve negative results; surface contradictions instead of resolving them silently.
- Durable evidence means repository files, exact SHAs, CI runs, retained artifacts and Issue #91. Never rely on hidden chat context.
- **Acceptance criteria come before any consequential build** (contract §3). Claim work in the queue before implementing.
- **Blind challenge:** a challenger receives objective state only (`fc.py challenge-capsule`), never the builder's conclusions.
- Work on a dedicated `claude/` branch. Never implement on `codex/` branches or on paths claimed by an active Codex task without a recorded handoff.
- **MLB V3 is activated.** Its implementation, activation record, runner, evidence ref and prereg are untouchable without Jacob (`.claude/rules/mlb-v3-evidence.md`).
- **Bridge:**
  - Issue #91 carries compact checkpoints (`fc.py checkpoint`). Substantive bridge messages end with `Alligator`.
  - Jacob authorization comments and V3 seal receipts keep their exact existing formats.
- **After meaningful work:** update the task in the ops-state branch (queue entry, capsule LOG, CURRENT_STATE if project state changed). Do not append to the frozen handoff.
- **Efficiency defaults** (contract §7): batch independent commands; never poll with model turns (`fc.py status`); subagents only when isolation or parallel research pays; rotate sessions at about 150–200k context.

Alligator

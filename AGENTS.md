# Full Count Engineering Rules

Orientation (default, one call):
`git fetch -q --depth 1 origin claude/full-count-ops-state && git show FETCH_HEAD:engineering/ops/bin/fc.py | python3 - orient [TASK_ID]`
prints CURRENT_STATE, the work queue and your task capsule. As challenger, use `orient TASK_ID --challenger` (objective state only; never the builder's conclusions).
The Claude/Codex operating contract is `engineering/ops/OPERATING_CONTRACT.md` on that branch.

Reference only when the task needs it: `engineering/PROJECT_STATE.md` (system map), `engineering/AUDIT/README.md` (audited areas), `engineering/AGENT_BRIDGE_PROTOCOL.md` (exceptional bridge messages), and `engineering/ENGINEERING_HANDOFF.md` (FROZEN history; reconstruction only).

1. Full Count is an MLB betting analytics/research system.
2. Current project stage is PRE-PHASE-V hardening. Phase V has NOT begun.
3. Claude built/reviewed substantial portions of Phases 1–4.
4. Codex and Claude Code collaborate asynchronously through the ops-state work queue, repository history, and compact GitHub Issue #91 checkpoints. Each must claim, report, hand off, and release work per the operating contract. Acceptance criteria precede consequential builds; challenges are blind (objective state only).
5. ChatGPT may act as architecture/adversarial reviewer.
6. Any engineer may challenge prior decisions with evidence.
7. Orient from CURRENT_STATE plus your task capsule before significant work; read reference documents only when the task needs them.
8. `engineering/ENGINEERING_HANDOFF.md` is frozen history (2026-10-02); do not append to it.
9. Update your task's queue entry, capsule LOG, and (if project state changed) CURRENT_STATE on `claude/full-count-ops-state` after every meaningful engineering task.
10. Never silently alter prediction history.
11. Probability and betting value are different concepts.
12. Market-category rank does not equal Top Pick status.
13. User-facing claims must map to reproducible underlying data.
14. Missing/stale data must reduce confidence or availability, never silently become favorable evidence.
15. Model/calibration/feature changes require held-out evidence and explicit versioning.
16. Backtest performance and real forward performance must never be conflated.
17. Generated data and source code are different classes of artifact.
18. Prefer one source of truth over duplicated state.
19. Add regression tests for every material bug.
20. Full existing test suite must run before a production PR unless technically impossible; document exceptions.
21. Do not knowingly implement an inferior workaround just because the clean solution touches more files.
22. Stay inside the OBJECTIVE of the task, but modify any architecture/files genuinely required to solve that objective correctly.
23. Never merge your own PR unless explicitly instructed by the user.

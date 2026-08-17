# Full Count Engineering Rules

Repository memory:

- Read `engineering/PROJECT_STATE.md` for the current technical map.
- Read `engineering/ENGINEERING_HANDOFF.md` for chronological decisions and work.
- Use `engineering/AUDIT/README.md` for the Pre-Phase-V audit index and finding rules.

1. Full Count is an MLB betting analytics/research system.
2. Current project stage is PRE-PHASE-V hardening. Phase V has NOT begun.
3. Claude built/reviewed substantial portions of Phases 1–4.
4. Codex and Claude are collaborating asynchronously through repository documentation and git history.
5. ChatGPT may act as architecture/adversarial reviewer.
6. Any engineer may challenge prior decisions with evidence.
7. Read `engineering/PROJECT_STATE.md` before significant work.
8. Read `engineering/ENGINEERING_HANDOFF.md` before significant work.
9. Update `engineering/ENGINEERING_HANDOFF.md` after every meaningful engineering task.
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

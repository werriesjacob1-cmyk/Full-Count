# FC-OPS-001 — Balanced Max Lean Phase 1

## ACCEPTANCE_CRITERIA
1. Fresh session orients from core doctrine + CURRENT_STATE + one capsule, without the handoff or #91 history.
2. One MLB and one NFL task coexist in WORK_QUEUE.json; `fc.py queue` exits 0; WIP and path-conflict checks work.
3. `fc.py challenge-capsule` excludes BUILDER_NOTES/LOG/BLOCKERS/NEXT_ACTION and fails closed on leakage.
4. Default orientation payload is materially smaller than the old path (measured bytes).
5. At least one status workflow is deterministic and change-only (`fc.py status`).
6. One navigator pilot run; retained only if net-beneficial.
7. No change to V3, PR #220 scientific state, production, selectors, ledger; no merge/deploy/promotion.

## BUILDER_NOTES
Builder self-assessment lives in engineering/ops/PHASE1_VERIFICATION.md (owner view).

## LOG
- 2026-10-02 ops branch created; contract, queue, facts, fc.py.

# PR #85 — what a clean integration would require

**PR #85 is a draft and stays unmerged.** This is the strategy, not a request.

## Where the branch stands

* head `2fe604db`, base `main`
* **347 commits behind `main`, 37 ahead.** Mission 1 branched before a long run
  of main-line work. Any integration starts with merging `main` in, not with a
  merge button.
* Production-path diff vs the merge base is **two files, 94 lines**:

| file | change |
|---|---|
| `generate_picks.py` | +8 lines: carries `extras["rest"]` through `_build_and_score`'s ctx dict. Read by nothing in scoring. |
| `dashboard/build_dashboard.py` | the shadow capture tap, plus the source-integrity evaluation it needs. Persistence is gated on `FULLCOUNT_SHADOW_PERSIST`, which nothing sets. |

Everything else is new modules under `backtest/`, new `test_*.py`, and
`engineering/` documents. Nothing changes probabilities, weights, calibrators,
recommendation thresholds, Top Pick policy or settlement semantics.

## The order a clean integration would go in

1. **Land the unrelated fixture repair separately.** `test_board_first_paint.py`
   and `test_browser_e2e.py` fail on the Mission 1 base for reasons this branch
   did not cause (proved three ways: Mission-1 base, current main, this head).
   PR #75 is the repair. It must not be absorbed here.
2. **Merge `main` into the branch** and re-run the full suite. With 347 commits
   of drift, the shadow modules' assumptions about `dashboard/live_state.py`,
   `publication_registry.py` and the served payload shape need re-verification,
   not just a green suite — those three are exactly what the champion arm and
   the capture tap read.
3. **Re-run the two critical reviewers against the merged head.** Neither
   verdict on this branch transfers across 347 commits of production change.
4. **Only then** is the enablement decision (§14 / `FULLCOUNT_SHADOW_PERSIST`)
   even on the table, and that is Jacob's alone.

## What must not happen

* No merge, and no removal of the DO-NOT-MERGE marker, without Jacob's word.
* No cherry-picking of the `backtest/` modules onto main ahead of steps 1–3:
  the capture tap and the champion arm are one mechanism, and splitting them
  gives a system that records snapshots nothing can bind.
* No enabling persistence in a workflow as part of "integration". Merging the
  code and turning the experiment on are two decisions, and only the first is
  a code review.

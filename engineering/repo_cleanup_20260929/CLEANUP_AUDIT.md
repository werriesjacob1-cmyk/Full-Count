# FULL COUNT repository cleanup audit — 2026-09-29

## Scope and truth

Exclusive cleanup branch: `codex/repo-cleanup-20260929`, forked from main `dd252867f8b2fe74a7bfcfaa902c8ebd152ff0f7`. Inventory is point-in-time; main advances frequently through the MLB automation. This branch changes only `engineering/repo_cleanup_20260929/`. No model, selector, grading, publication, workflow, seal, or production file is changed.

**Actual cleanup:** PR [#130](https://github.com/werriesjacob1-cmyk/Full-Count/pull/130) closed unmerged after comparing its sole functional `.gitignore` addition, `.claude/worktrees/`, against main, where that same ignore rule already exists. Its head is `342f69e71ea0711bb38ab594f165ad93f0626d33`. No research or runtime content was removed. Open PRs: **45 → 44**. Issue #91 records the disposition in comment 5895479331.

Three old autosave refs are proved safe to delete, but **not deleted**: the connected GitHub tool has no delete-ref operation and the signed-in GitHub browser's delete control did not change the page or the ref. Their exact names and tips are below. All three are ancestors of main with zero unique commits, unprotected, unused by every open PR head/base and the 18 main workflow files, absent from local worktrees, and recoverable by exact SHA. Remote branches were **251 before creating this audit branch; 252 after; 252 remain**. Do not count proposed deletion as achieved.

| Safe deletion candidate | Exact preserved tip | Unique commits vs main |
|---|---|---:|
| `worktree-agent-a86ae4110b03c7f17` | `fda7111c6593ad912d62fe9ef475df1a776aa549` | 0 |
| `worktree-agent-ae0bbf867acdfd4a2` | `fe815c49a0b6da2ee4c16650d524e690dc7738a5` | 0 |
| `worktree-agent-ae81d329067af391a` | `c91471b9a99ee018bf15ec641e07322f664a7a59` | 0 |

For an authorized operator with a functioning authenticated Git client, delete **only** these exact refs after rechecking the tips: `git push origin --delete worktree-agent-a86ae4110b03c7f17 worktree-agent-ae0bbf867acdfd4a2 worktree-agent-ae81d329067af391a`. A changed tip voids this recommendation. Do not use a wildcard. The current environment's Git HTTPS helper is unavailable; no credential workaround was attempted.

## Branch and PR inventory

The [branch manifest](branch_manifest.json) classifies all **252** branch refs at the audit snapshot: **59 KEEP, 82 ARCHIVE FIRST, 108 REVIEW REQUIRED, 3 DELETE SAFE**. The original 251 consisted of 114 tips already reachable from main, 133 with unique commits, and four compare failures from unrelated-history refs; the latter remain preserved. Eight GitHub-protected refs are KEEP. A reachable tip alone is insufficient to delete a branch when it may be an active base, seal/recovery marker, or workflow trigger. Current NFL seal, pricing, integration, and evidence refs are preserved.

The [PR manifest](pr_manifest.json) classifies all **45** initially open PRs, with #130's executed closure annotated. Only #130 had a proved no-loss closure. #110 and #75 look superseded but have unique history or dependent branches, so remain open. #215/#210, #177, Tier 1/rushing research, #196/#199, #193, and #216 remain for their owners and Jacob. No PR was merged or retargeted.

## Tracked weight and preservation

At main `dd252867f8b2fe74a7bfcfaa902c8ebd152ff0f7`, the recursive tree has **3,060 tracked blobs** totaling **2,198,713,712 uncompressed bytes** (~2.05 GiB). This is current-tree blob content, not packed Git history or checkout disk allocation. `data/` accounts for 1,813,647,748 bytes; `output/` 344,909,435; `results/` 20,435,758; `docs/` 11,779,016. The [large-file inventory](large_files.json) lists the top 100 blobs and exact duplicate-blob groups.

The largest class, 55 `data/props/` files totaling **1,735,570,868 bytes**, is dated raw sportsbook evidence, not a disposable cache. `output/mlb_daily_*.txt` totals 156,021,803 bytes across 56 daily source transcripts; archive design is needed before removing any. `output/picks_*.json` (89,740,045 bytes), `output/board_freeze*` (15,108,227), and `results/` (20,435,758) preserve prospective/public prediction and grading evidence. Exact duplicate blobs could at most save 16,373,859 current-tree bytes by retaining one path per group, but the duplicate paths carry distinct publication/provenance roles; none was removed. No tracked `.pyc`, cache, temporary screenshot/video, or obvious scratch file was found. **Tracked files/bytes removed: 0/0.**

The expensive output class deserves a *separate* archival design: immutable object identity, complete per-date index, retrievable raw bytes, migration verification, and a consumer audit before switching tracking. No such migration is attempted here.

## Actions and CI

The [CI inventory](ci_waste.json) samples the newest 1,000 of 1,103 main workflow runs from 2026-09-28 03:41 to 2026-09-29 17:26 UTC: 471 Pages deploy, 462 live update, 30 refresh, and small numbers of other jobs. Those are lower-bound sample counts. No root or NFL suite appears in these newest 1,000 bot-churn main runs, so there is **no demonstrated daily test waste** to remove with broad path filters. Pages deploy already coalesces pending runs and checks public convergence/durable exposure; slowing it risks freshness.

A single PR #199 SHA `9c19be…` had three root and three NFL runs near 16:32 UTC from push/PR events and branches. Deduplicating by content might save two root and two NFL jobs in that one case, but required checks and branch-only coverage need a separate safe design. Root CI also downloads Playwright Chromium each run; caching gain and storage cost have not been measured. **Workflows changed: 0. Measured runtime savings: 0.** Five sampled Actions artifacts are classified in the inventory; NFL shadow artifacts are evidence-critical. Repository-wide artifacts/caches cannot be listed or deleted with the connected tool. **Actions bytes deleted: 0.**

## Code, dependencies, and hygiene

No source module met the full dead-code proof standard (no imports, CLI, workflow, tests, or documentation consumer plus an exact replacement), so **dead modules removed: 0**. Root/NFL dependency isolation stays intact; no broad package removal or upgrade is justified without a caller/runtime check.

Permanent small policy: use `agent/<workstream>-<date>` or owner-scoped branches for temporary work; push a recoverable checkpoint before long tests; put durable evidence in a named research/engineering location with exact SHA; close or retire merged disposable branches only after checking open PR bases and preserved evidence; never infer that an old seal, preregistration, raw capture, negative research result, or public ledger is disposable from age alone. Issue #91 is the handoff record. This policy is guidance within this audit, not a new process framework.

## Handoff for Claude and Jacob

**Deleted:** no refs, tracked files, artifacts, or evidence. **Closed:** PR #130 only, with duplicate functionality already in main. **Preserved:** all research/preregistration/seal/recovery branches, NFL shadow artifacts, raw odds/props, public picks/grades, and every uncertain PR. **Changed:** this audit branch and the #130 administrative state. Claude need not reconsider #130; it can use the manifests to avoid re-inventorying 252 refs. The three autosave refs are the only branch deletion candidates with complete proof, and their deletion remains an explicit operator action because the browser control did not execute.

This audit does **not** claim a smaller Git tree or faster CI. Exact-head root and NFL checks for the documentation-only audit branch are recorded separately in Issue #91 when complete.

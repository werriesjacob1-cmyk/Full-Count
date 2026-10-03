import json, collections
rows = json.load(open("inventory.json"))
full = {b: x["sha"] for b, x in json.load(open("branch_facts.json")).items()}
cnt = collections.Counter(r["bucket"] for r in rows)
NAMES = {"A": "KEEP ACTIVE", "B": "CLOSE PR ONLY — KEEP BRANCH", "C": "CLOSE PR + SAFE TO DELETE BRANCH", "D": "NEEDS MANUAL REVIEW"}
L = ["# FULL COUNT cleanup inventory — 2026-10-03 (PROPOSAL ONLY; nothing deleted or closed)", "",
     "Prepared by Claude for SUPERCHAD/Jacob review. **No branch was deleted, no PR closed, nothing merged.** Execution needs Jacob's explicit authorization of the exact batch below.", "",
     "## Method (evidence, not age)",
     "- Snapshot: 270 remote branches, 21 open PRs, 200 closed PRs, 436 Issue #91 comments, enabled routines, main workflows, and the ops queue, at 2026-10-03 ~15:00Z.",
     "- Ancestry from a blobless mirror: `in_main` = head is an ancestor of main; `unique` = `git rev-list --count main..head`; `contained_in` = `git branch --contains head`.",
     "- Automation: every branch name was grepped across main's code and docs tree (raw data/output/results excluded). The only automation hits are 5 workflow push filters.",
     "- The enabled routines are V3 dispatch and the NFL Week 4 seal + backstop. Their prompts and recovery scripts (`recover_w04.sh`, `recover_environment.sh`) pin branches and SHAs.",
     "- Reconciled against Codex's 2026-09-29 audit (`codex/repo-cleanup-20260929`, `branch_manifest.json`). Most of its REVIEW REQUIRED items are now proven (they are ancestors of main with no consumer).",
     "- **C requires all of:** zero unique commits vs main (or an ancestor of a retained evidence branch); not protected; not the head or base of an open PR; not named by any routine, workflow or recovery script; not an archive/incident/evidence marker.",
     "- #91 and doc mentions do not block C, because the commits stay reachable in main.",
     "- **D** = unique commits with no proven durable copy, a Codex/owner decision, or an automation/config reference. Never guessed.", "",
     f"## Totals: A {cnt['A']} · B {cnt['B']} · C {cnt['C']} · D {cnt['D']} (270 branches; 21 open PRs inside these)", ""]
for k in "ABCD":
    L += [f"## {k} — {NAMES[k]} ({cnt[k]})", "", "| PR | branch | head | date | in main / unique | purpose | durable evidence | dependencies | rationale |", "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted((r for r in rows if r["bucket"] == k), key=lambda r: (r["pr"] is None, r["pr"] or 0, r["branch"])):
        deps = "; ".join(r["dependencies"]) or "none found"
        if r["closed_prs"]: deps += "; prior PRs " + ", ".join(r["closed_prs"][:3])
        if r["issue91_mentions"]: deps += f"; #91 mentions {r['issue91_mentions']}"
        L.append(f"| {('#' + str(r['pr'])) if r['pr'] else '—'} | `{r['branch']}` | `{r['sha']}` | {r['date']} | {'yes' if r['in_main'] else 'no / ' + str(r['unique_vs_main'])} | "
                 f"{(r['purpose'] or '').replace('|', '/')} | {r['durable']} | {deps.replace('|', '/')} | {r['rationale'].replace('|', '/')} |")
    L.append("")
C = sorted(r["branch"] for r in rows if r["bucket"] == "C")
L += ["## Exact proposed batch (only if Jacob authorizes; recheck first)", "",
      "**Batch 1 — close PRs only, keep branches (B).** Close children before bases. Each closing comment points to the retained branch and head SHA:",
      "#81, #84 → #79, #82 → #75, then #85, #217, #219.", "",
      f"**Batch 2 — delete {len(C)} branches (C).** Zero unique content: 112 have every commit in main; 2 (FC-MLB-004/005) are ancestors of the retained FC-MLB-006 branch.",
      "- Each delete is guarded by the exact expected tip, so a moved branch refuses rather than deletes. No wildcards.",
      "- Same batch: update the FC-MLB-004/005 EVIDENCE_POINTERS in the ops queue to the FC-MLB-006 branch (same SHAs).", "", "```"]
for b in C:
    L.append(f"git push --force-with-lease=refs/heads/{b}:{full[b]} origin :refs/heads/{b}")
L += ["```", "",
      "**Not proposed now:**",
      "- A (30) and D (118).",
      "- The D branches with closed, unmerged PRs (negative research) should be considered only via an *archive-tag-then-delete* step, after owner review: create `refs/tags/archive/<branch>` at the exact SHA, verify, then delete. This preserves every commit while decluttering branches.",
      "- The 5 workflow-filter branches need their push filters removed from main first.", "",
      "Alligator."]
open("CLEANUP_INVENTORY_20261003.md", "w").write("\n".join(L) + "\n")
print(len(L), cnt)

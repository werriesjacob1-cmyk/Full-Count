"""Deletion-safe cleanup inventory (2026-10-03). Evidence-driven; conservative: C only with full proof."""
import json

f = json.load(open("branch_facts.json"))
op = {p["head"]["ref"]: p for p in json.load(open("open_prs.json"))}
open_bases = {p["base"]["ref"] for p in op.values()}
r91 = json.load(open("i91_refs.json"))
cb = {x["branch"]: x for x in json.load(open("codex_branch_manifest.json"))}
raw = open("closed_prs.json").read().replace("][", ",")
closed = {}
for p in json.loads(raw):
    closed.setdefault(p["head"]["ref"], []).append(f"#{p['number']} {'merged' if p['merged_at'] else 'closed'}")
ops_txt = open("ops_text.txt").read()
main_refs = json.load(open("main_refs.json"))

PROTECTED = {"canonical/2025-source-boundary-blocker", "canonical-durable-checkpoints", "canonical-run-manifests",
             "incident/2026-09-03-broken-main", "incident/2026-09-03-pre-rewrite", "incident/2026-09-03-superclaude-head",
             "main", "prediction-ledger/publication-events"}
TRIGGER = {  # named or pinned by an ENABLED routine (V3 dispatch, NFL Week 4 seal + backstop), incl. recovery scripts
    "claude/mlb-v3-ops": "V3 hourly dispatch trigger trig_011u98uX… pulls this branch",
    "claude/mlb-challenger-v3-evidence": "V3 append-only evidence ref (dispatcher EVIDENCE_REF)",
    "claude/nfl-tier1-seal-2026w04": "Week 4 seal target; Saturday trigger + backstop push/verify here",
    "claude/nfl-seal-inputs-2026w04": "Week 4 seal runner checkout; recover_w04.sh",
    "claude/nfl-tier1-seal-2026w03": "recover_environment.sh clones tier1 @ e05e02c592 from this branch; named immutable",
    "claude/nfl-seal-inputs-20260925": "Week 3 inputs, named immutable in the seal trigger",
    "claude/nfl-seal-drill-2026w04": "DRILL_REPORT.md cited by the seal trigger",
    "claude/nfl-tier2-coverage-20260925": "recover_environment.sh clones cov @ f4fb17aa0b from this branch (PR #207)",
    "claude/nfl-atl-gb-evidence-20260924": "ATL@GB evidence, named immutable in the seal trigger",
}
WORKFLOW = {  # named in push filters of workflow files on main
    "superchad/nfl-live-game-market-shadow-20260918": "nfl-live-game-market-shadow.yml push filter",
    "superchad/nfl-foundation-continuation-20260911": "nfl-live-passing-yards-shadow-board.yml push filter",
    "superchad/nfl-sunday-shadow-launch-20260912": "nfl-live-passing-yards-shadow-board.yml push filter",
    "claude/nfl-live-player-prop-board-20260917": "nfl-live-player-prop-board-manual.yml push filter",
    "claude/nfl-receptions-live-shadow-20260919": "nfl-live-receptions-shadow-board.yml push filter",
}
EXPLICIT = {  # branch -> (bucket, purpose, rationale)
    "claude/full-count-ops-state": ("A", "Ops state/queue (fc.py)", "Live operating state for all agents; never merged to main by design."),
    "claude/practical-maxwell-wgs4l6": ("A", "This session's designated development branch", "Assigned branch of the active builder session."),
    "claude/mlb-engine-pa-world-model-20261003": ("A", "FC-MLB-006 evidence; ancestry holds FC-MLB-002/004/005/006 evidence",
                                                  "Retain as the single durable research-evidence branch for the MLB engine series (negative results preserved)."),
    "claude/mlb-engine-interaction-20261002": ("A", "FC-MLB-002 evidence (repaired) + zero-match fix",
                                               "Ops task FC-MLB-002 is still READY_FOR_CHALLENGE (not closed); its head 7c331c266b is also contained in the FC-MLB-006 branch, so it becomes C once FC-MLB-002 is closed."),
    "claude/mlb-engine-pitcher-contact-20261003": ("C", "FC-MLB-004 evidence (NO_GAIN, DONE)",
                                                   "Head 46712d8091 is an ancestor of the retained FC-MLB-006 branch, so every FC-MLB-004 artifact stays reachable; task DONE; no PR, trigger or workflow uses it."),
    "claude/mlb-engine-team-conversion-20261003": ("C", "FC-MLB-005 evidence (NO_GAIN, DONE)",
                                                   "Head 5e1d6bd8ec is an ancestor of the retained FC-MLB-006 branch; task DONE; no PR, trigger or workflow uses it."),
    "claude/efficiency-investigation-20261002": ("A", "FC-OPS-001 efficiency report", "Cited by FC-OPS-001, which awaits SUPERCHAD."),
}
OPEN_PR = {  # PR -> (bucket, purpose, rationale)
    75: ("B", "Test-fixture repair; base of stacked SUPERCHAD PRs #79/#82",
         "Aug-29 draft superseded in practice by the merged CI fixture repairs (#208, #215), but it is the base of a stack with unique commits; close after #79/#81/#82/#84 and keep the branch."),
    79: ("B", "Research experiment-integrity primitives (stacked on #75)", "Explicitly NOT AUTHORIZED TO MERGE; 7 unique commits; keep branch as history, close PR after #81/#84."),
    81: ("B", "PA opportunity runner scaffold (stacked on #79)", "Not authorized to merge; 7 unique commits; no consumer; keep branch."),
    82: ("B", "Canonical artifact certifier (stacked on #75)", "Not authorized to merge; 10 unique commits; keep branch for the certifier design."),
    84: ("B", "HR contact-state Stage-1 readiness stack (stacked on #79)",
         "Prereg records were preserved on main via #216, but 44 unique code commits exist only here; close PR, keep branch."),
    85: ("B", "Prospective PA-v1 lifecycle closure — DO NOT MERGE", "Evidence PR by design; no workflow or trigger uses it; 39 unique commits must remain recoverable."),
    174: ("D", "Codex FTN tactical-source research", "Codex-owned research with unique commits; owner disposition required."),
    193: ("A", "NFL receptions scale-bias fix (Mission 10)", "Production-chain change still awaiting a Jacob merge decision; forward-shadow grading cited on #91."),
    196: ("D", "Codex price-aware NFL offers research", "Codex-owned ('do not edit'); base of #199 and of codex/nfl-pricing-eligibility-20260925."),
    198: ("D", "MLB slate-date contract audit (doc + replay)", "Unresolved engineering question (UTC vs Central slate day); needs owner/Jacob decision whether to merge the audit or close."),
    199: ("D", "Codex B0-join pricing research (stacked on #196)", "Codex-owned; base of codex/nfl-pricing-eligibility-20260925."),
    202: ("A", "NFL Tier 1 foundation", "Head e05e02c592 is the pinned tier1 commit of the active Week 4 seal recovery and is the base of #203/#204/#205/#207."),
    203: ("A", "NFL Tier 1 WS-B (frozen B 8aa8067fbc)", "Frozen commit B is verified by the active Week 4 seal."),
    204: ("A", "NFL Tier 1 WS-C (frozen C 40d842c9a9)", "Frozen commit C is verified by the active Week 4 seal."),
    205: ("A", "NFL Tier 1 WS-D (frozen D 76b42547c3)", "Frozen commit D is verified by the active Week 4 seal."),
    207: ("A", "NFL Tier 2 coverage (cov @ f4fb17aa0b)", "Cloned by recover_environment.sh for the active Week 4 seal; DO-NOT-MERGE evidence."),
    217: ("B", "MLB market-anchor pre-registered analysis (insufficient n)",
          "Pre-registered analysis already ran and was answered more fully by #219/#220; not a merge candidate; the prereg must stay durable, so keep the branch."),
    219: ("B", "MLB forward-chain accuracy study (exploratory)", "Completed research (price beats model; pitcher-outs lead) that seeded V3; not a merge candidate; keep branch as the only artifact copy."),
    220: ("A", "V3 activated implementation 504ca9cdb9", "Activated prospective challenger; never merge without Jacob."),
    221: ("A", "Slim orientation (FC-OPS-001)", "Awaiting SUPERCHAD/Jacob decision."),
    222: ("A", "FC-NFL-003 Codex research", "NFL disposition not yet settled (READY_FOR_CHALLENGE)."),
}

rows = []
for b, x in sorted(f.items()):
    pr = op.get(b)
    num = pr["number"] if pr else None
    dep = []
    if b in open_bases:
        dep.append("base of open PR(s) " + ",".join(f"#{p['number']}" for p in op.values() if p["base"]["ref"] == b))
    if b in TRIGGER: dep.append("ACTIVE ROUTINE: " + TRIGGER[b])
    if b in WORKFLOW: dep.append("WORKFLOW CONFIG: " + WORKFLOW[b])
    if b in PROTECTED: dep.append("GitHub-protected")
    if b in ops_txt and b != "main": dep.append("named in ops state")
    if b in main_refs and b not in WORKFLOW: dep.append("doc mention on main: " + ", ".join(main_refs[b][:2]))
    if x["contained_in"]: dep.append("head contained in: " + ", ".join(x["contained_in"][:4]) + (" …" if len(x["contained_in"]) > 4 else ""))
    hist = closed.get(b, [])
    durable = ("main (head is an ancestor of main)" if x["in_main"] else
               "only this branch" + (f" (+ contained in {len(x['contained_in'])} other ref(s))" if x["contained_in"] else ""))
    purpose, why = (pr["title"][:90] if pr else (cb.get(b, {}).get("evidence_category") or "")), ""
    if num in OPEN_PR:
        bucket, purpose, why = OPEN_PR[num]
    elif b in EXPLICIT:
        bucket, purpose, why = EXPLICIT[b]
    elif b in PROTECTED:
        bucket, why = "A", "GitHub-protected history/evidence ref."
    elif b in TRIGGER:
        bucket, why = "A", "Used or pinned by an enabled routine."
    elif b.startswith(("archive/", "incident/", "evidence/")):
        bucket, why = "D", "Intentional archive/evidence marker; retention is an owner decision, not a mechanical one."
    elif b in WORKFLOW:
        bucket, why = "D", "Named in a main workflow push filter; remove the filter first, then re-evaluate."
    elif b in open_bases:
        bucket, why = "D", "Base of an open PR."
    elif x["in_main"] and "named in ops state" not in dep:
        bucket = "C"
        why = "Every commit is already in main (0 unique); not protected; no open PR head/base; no routine or workflow names it."
    else:
        bucket = "D"
        why = (f"{x['ahead_of_main']} unique commit(s) not in main" + (f"; prior PR {', '.join(hist)}" if hist else "; no PR")
               + "; deleting would remove the only reachable copy unless archived (e.g. tag) first.")
    rows.append({"branch": b, "pr": num, "sha": x["sha"][:10], "date": x["date"], "in_main": x["in_main"],
                 "unique_vs_main": x["ahead_of_main"], "closed_prs": hist, "issue91_mentions": r91.get(b, 0),
                 "codex_0929": cb.get(b, {}).get("recommended_action"), "purpose": purpose, "durable": durable,
                 "dependencies": dep, "bucket": bucket, "rationale": why})
json.dump(rows, open("inventory.json", "w"), indent=1)
from collections import Counter
print(Counter(r["bucket"] for r in rows))
print("C with #91 mentions:", sum(1 for r in rows if r["bucket"] == "C" and r["issue91_mentions"]))

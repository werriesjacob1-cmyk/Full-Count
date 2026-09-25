import json, subprocess
prs = {214:"codex/nfl-tier2-defender-coverage-f18-20260925",211:"codex/nfl-tier2-personnel-f15-20260925",209:"codex/nfl-tier2-concepts-f14-20260925",206:"codex/nfl-tier2-pressure-f13-20260925",201:"claude/mlb-selection-overconfidence-20260924",199:"codex/nfl-price-b0-join-20260924",198:"claude/central-slate-contract-20260924",196:"codex/nfl-price-aware-offers-20260923",193:"claude/nfl-pregame-target-share-20260923",192:"claude/mlb-mission10-toppick-calibration-holdout-20260924",191:"claude/nfl-opportunity-error-decomposition-20260923",190:"claude/nfl-passing-yards-alt-ladder-20260923",188:"claude/mlb-fullboard-calibration-analysis-20260923",187:"claude/mlb-fullboard-snapshot-20260923",184:"claude/nfl-opportunity-ablation-2024-holdout-20260923",174:"codex/nfl-real-tactical-source-20260922",170:"codex/nfl-film-intelligence-prototype-20260922",131:"claude/hits-runs-rbis-mechanism-20260918",130:"claude/gitignore-agent-worktrees-20260918",110:"superchad/nfl-scoring-prior-features-20260914",85:"claude/prospective-hits-pa-lifecycle-closure-01",84:"superchad/hr-contact-state-integration-01",83:"superchad/hr-offset-estimator-scaffold-01",82:"superchad/canonical-certification-readiness-01",81:"superchad/pa-opportunity-runner-scaffold-01",80:"superchad/hr-contact-state-feature-scaffold-01",79:"superchad/experiment-primitives-01",78:"superchad/pa-opportunity-decisive-prereg-01",77:"superchad/hr-execution-prereg-v2-01",76:"superchad/superclaude-activation-corrections-01",75:"superchad/fix-board-first-paint-clock-fixture-01",74:"accuracy/hr-execution-prereg-01",73:"tooling/superclaude-activation-01"}
def g(*a):
    r = subprocess.run(["git",*a],capture_output=True,text=True); return r.returncode, r.stdout.strip()
out={}
for n,b in sorted(prs.items(), reverse=True):
    ref="origin/"+b
    rc,_=g("rev-parse","--verify",ref)
    if rc: out[n]={"branch":b,"missing":True}; continue
    mb=g("merge-base","origin/main",ref)[1]
    ahead=int(g("rev-list","--count","origin/main.."+ref)[1]); behind=int(g("rev-list","--count",ref+"..origin/main")[1])
    files=g("diff","--name-only",mb,ref)[1].split("\n") if ahead else []
    files=[f for f in files if f]
    # files whose content at PR head already equals main (superseded check)
    same=[f for f in files if g("diff","--quiet",ref,"origin/main","--",f)[0]==0]
    rc,mt=g("merge-tree","--write-tree","--name-only","origin/main",ref)
    conflicts=[l for l in mt.split("\n")[1:] if l and not l.startswith(("Auto-merging","CONFLICT"))] if rc==1 else []
    out[n]={"branch":b,"head":g("rev-parse","--short",ref)[1],"ahead":ahead,"behind":behind,"files":len(files),
            "files_equal_to_main":len(same),"conflict_files":conflicts[:6],"top_files":files[:4]}
json.dump(out,open("/tmp/claude-0/pr_scan.json","w"),indent=1)
for n,v in out.items():
    print(n, v.get("head"), "ahead",v.get("ahead"),"behind",v.get("behind"),"files",v.get("files"),"eq_main",v.get("files_equal_to_main"),"CONFLICT" if v.get("conflict_files") else "", v.get("conflict_files") or "", v.get("top_files"))

import json
C=json.load(open("/tmp/claude-0/pr_collect.json")); F=json.load(open("/tmp/claude-0/fail_prs.json")); O=json.load(open("/tmp/claude-0/old_prs.json"))
FIX="#208 fixes `fixture needs four top picks`"
D={ # n: (disposition, dependencies, scientific status, blocker, owner, next action)
208:("**READY FOR APPROVAL**","none","test-infrastructure repair","none","SUPERCHAD→Claude","Jacob: merge 1st (tree `6f282f2d80`)"),
210:("**READY FOR APPROVAL**","#208 (root CI)","NFL-CI-only dependency","none","SUPERCHAD→Claude","Jacob: merge 2nd (tree `0933a89b3a`)"),
177:("**READY FOR APPROVAL** (via integration tree `8dfbac39ce`)","#208","permanent requirements register (docs only)","PR head (`superchad/…`) lacks §15/§16; certified content is the integration branch","SUPERCHAD→Claude","Jacob: merge the integration content 3rd"),
202:("**READY FOR APPROVAL** (research-only)","#208, #210","Tier 1 contract/harness/protocol; seal runs from exact SHAs on a separate branch","head's e2e failure is its stale Sept 24 `docs/data.json` snapshot (the PR touches no docs); integration tree green","Claude","Jacob: merge 4th"),
203:("READY (research-only)","#202","F2/F3/F8/F9 historical; H1 prospective active","none","Claude","retarget to main after #202; merge"),
204:("READY (research-only)","#202, #210","F1/F5/F6/F7/F10; H2 prospective active","head NFL CI needs numpy (`No module named 'numpy'`) → #210","Claude","after #202/#210"),
205:("READY (research-only)","#202, #210","F4 narrow historical; H3 prospective active","head NFL CI needs numpy → #210","Claude","after #202/#210"),
207:("READY (research-only)","#202","F11 REJECTED / F12 NOT SUPPORTED (preserved)","head e2e stale-data snapshot only","Claude","after #202"),
213:("READY (research-only)","#202","F17 NOT SUPPORTED (preserved)","head e2e stale-data snapshot only","Claude","after #202"),
186:("READY (research-only)","#208, #210 (combined tree)","rushing B0 research, not authoritative","none (handoff union applied to head `efb40423ff`)","Claude","after Tier 1"),
212:("READY (research-only)","#186","F16 negative (preserved)","none","Codex","after #186"),
211:("READY AFTER #208 exact-tree CI","#208","F15 held evaluation, independent-review corrections recorded","root: "+FIX,"Codex","CI refresh after #208"),
214:("READY AFTER #208 exact-tree CI","#208","F18 source-gate docs (rights/assignment blocked)","root: "+FIX,"Codex","CI refresh after #208"),
206:("READY AFTER #208 exact-tree CI","#208","F13 NEGATIVE; independently reviewed CLEAN 2026-09-25","root: "+FIX+"; LOW: tests write temp files in repo path","Codex","CI refresh after #208"),
209:("READY AFTER #208 exact-tree CI","#208","F14 NULL; independently reviewed CLEAN 2026-09-25","root: "+FIX+"; LOW: same temp-file pattern","Codex","CI refresh after #208"),
174:("READY AFTER #208 exact-tree CI","none","FTN descriptive charting; independently reviewed CLEAN","LOW: README says 10 tests (14)","Codex","CI refresh on new main"),
170:("READY AFTER #208 exact-tree CI","none","synthetic film prototype; real source BLOCKED; reviewed CLEAN","none","Codex","CI refresh on new main"),
196:("ACTIVE WORK","none","price-aware offer research","3 gates: BOOK_ACTION_RULES_NOT_CERTIFIED, CURRENT_ROLE_NOT_VERIFIED, QUOTE_TIMESTAMP_NOT_PROVIDED","Codex","certify gates"),
199:("ACTIVE WORK","#196","offer→sealed-B0 join research","same gates","Codex","after #196"),
193:("ACTIVE WORK","none","Mission 10 pre-registered forward shadow","handoff conflict deliberately unrepaired until grading","Claude","Sep 30 trigger grades; then union-repair"),
201:("READY AFTER #208 exact-tree CI","#208","MLB overconfidence, pre-registered, inconclusive","root: "+FIX+" (handoff repaired `025fdf63ed`)","Claude","CI refresh after #208"),
198:("READY AFTER #208 exact-tree CI","#208","MLB slate-date audit, no code change","root: "+FIX+" (repaired `d21ba2f5fc`)","Claude","CI refresh after #208"),
192:("READY AFTER #208 exact-tree CI","#208","MLB Top Pick calibration holdout","root: "+FIX+" (repaired `c30c1c3a37`)","Claude","CI refresh after #208"),
191:("READY AFTER #208 exact-tree CI","#208","NFL error-decomposition diagnostic","root: "+FIX+" (repaired `0d85251d7e`)","Claude","CI refresh after #208"),
190:("READY AFTER #208 exact-tree CI","#208","NFL passing-yards alt ladder research","head CI green on its older base; needs exact-tree run on new main","Claude","CI refresh after #208"),
188:("READY AFTER #208 exact-tree CI","#208","MLB full-board calibration research","head CI green on older base","Claude","CI refresh after #208"),
187:("READY AFTER #208 exact-tree CI","#208","MLB test-coverage (adds a test)","root: "+FIX+" (repaired `8e413cc072`)","Claude","CI refresh after #208"),
184:("READY AFTER #208 exact-tree CI","#208","NFL ablation diagnostic","root: "+FIX+" (repaired `ba757f1fdf`)","Claude","CI refresh after #208"),
131:("READY AFTER #208 exact-tree CI","#208","MLB selector diagnosis (handoff doc)","root: "+FIX+" (repaired `c7dc7fdfd1`)","Claude","CI refresh after #208"),
130:("**SUPERSEDED**","none","—","identical `.claude/worktrees/` rule + comment already on main (.gitignore 28–32)","Claude","Jacob: close"),
110:("**SUPERSEDED**","none","—","both files byte-identical on main","SUPERCHAD","Jacob: close"),
75:("**SUPERSEDED** (verified)","none","—","all 3 commits' fixes present on main in equivalent/evolved form (5b67: 28/28 lines; 26d37: 19/20; c001 superseded by main's model_basis_at+market_prices_at stale fixture)","SUPERCHAD","Jacob: close"),
}
for n in (73,76): D[n]=("DEPENDENCY BLOCKED (re-rooted history)","—","SuperClaude activation tooling","no merge base with main; unique `.claude/` files + CLAUDE.md edits","SUPERCHAD / Jacob","decide: port agent/skill definitions via a new docs PR, or archive")
for n in (74,77,78): D[n]=("DEPENDENCY BLOCKED (re-rooted history)","—","MLB pre-registration documents","no merge base; unique prereg docs only (rest is old generated data)","SUPERCHAD","smallest action: port the prereg .md files into engineering/ in one docs PR")
for n in (79,80,81,82,83,84): D[n]=("DEPENDENCY BLOCKED (re-rooted history)","stacked superchad chain","MLB research code scaffolds (unvalidated)","no merge base; unique backtest/*.py + tests written against the old tree","SUPERCHAD","port with fresh review + CI on current main, or archive")
D[85]=("DEPENDENCY BLOCKED (re-rooted history)","—","PA-v1 lifecycle closure, marked DO NOT MERGE","no merge base; 48 unique files","Claude / Jacob","archive (preserve branch) unless PA-v1 is revived")
rows=[]
for p in C["prs"]:
    n=p["number"]; d=D[n]; ci=p["ci"]
    cis=" ".join(f"[{('✅' if v['conclusion']=='success' else '✗' if v['conclusion']=='failure' else v['conclusion'])} {k.replace(' Test Suite','').replace('Test Suite','root').replace('NFL Web Integration Tests','web')}]({v['url']})" for k,v in ci.items()) or "—"
    uniq=""
    if str(n) in O: uniq=f" Unique files absent on main: {len(O[str(n)]['absent_on_main'])}."
    rows.append(f"| #{n} | `{p['head_sha']}` | {p['base_ref']} | {d[0]} | {cis} | {d[1]} | {d[2]} | {d[3]}{uniq} | {d[4]} | {d[5]} |")
open("/tmp/claude-0/matrix_rows.md","w").write("\n".join(rows)+"\n")
print(len(rows), "rows;", sum(1 for p in C["prs"] if p["number"] in D), "classified")

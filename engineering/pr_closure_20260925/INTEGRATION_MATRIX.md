# PR closure and merge readiness: final integration matrix (2026-09-25)

- **Workstream:** PR-CLOSURE-MERGE-READINESS-20260925. Codex started it, then ran out of usage; Claude Code completed it (#91 claim 5836256690).
- **Main:** `800596c78d` at the final collection. The certified integration trees were built on `e48bb2ac21`; since then main has only received automated dashboard-data commits, which don't trigger CI.
- **Open PRs:** 44, every one with a disposition.
- **Authority:** nothing was merged, closed, deployed or promoted. Every step still needs Jacob's explicit approval.
- **Seal isolation:** the Saturday Tier 1 seal (`claude/nfl-tier1-seal-2026w03`, `claude/nfl-seal-inputs-20260925`, `trig_01FiiD9MknrMxbB1zbeS4d5N`) was not touched.

**Readiness standard.** A PR is READY FOR APPROVAL only when all of these hold:
- a complete implementation for its scope;
- green exact-integration-tree CI (root `test.yml` and `nfl-tests.yml`);
- an independent review of the actual diff;
- no conflict;
- documented dependencies;
- no research-integrity defect;
- no production or customer change;
- no interference with frozen evidence.

"Research-only" means safe to integrate as research code. It is **not** production readiness and **not** model promotion.

Every number below comes from the scripts and JSON in this directory:

| Script | Output |
|---|---|
| `collect_prs.py` | `pr_heads_and_ci.json` |
| `fail_files.py` | `ci_failure_attribution.json` (failing test and message, parsed from each failed job's log) |
| `old_prs.py` | `rerooted_pr_unique_files.json` |
| `gen_matrix.py` | the table below |

## 1. Certified integration trees (exact-head CI)

| Tree | SHA | Contents | Root CI | NFL CI | Applies to |
|---|---|---|---|---|---|
| `codex/pr208-integration-20260925` | `6f282f2d80` | main + #208 | ✅ [36164562893](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36164562893) | ✅ [36164562999](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36164562999) | #208 |
| `codex/pr210-integration-20260925` | `0933a89b3a` | + #210 | ✅ [36164601803](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36164601803) | ✅ [36164601793](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36164601793) | #210 |
| `codex/pr177-integration-20260925` | `8dfbac39ce` | main + #177 + §15/§16 | ✗ only `test_browser_today_central.py: fixture needs four top picks`, the known #208 defect; the diff vs main is one .md file | ✅ | #177 content; certified in the sequence tree |
| `claude/tier1-research-integration-20260925` | `59492822f8` | #210 tree + #202, #203, #204, #205, #207, #213 | ✅ [36164939177](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36164939177) | ✅ [36164938965](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36164938965) | Tier 1 stack |
| `claude/pr186-integration-20260925` | `6f549c9d0a` | main + #186 + #212 + #208/#210 | ✅ [36165038521](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36165038521) | ✅ 36165038564; web ✅ | #186/#212 |
| **`claude/merge-sequence-integration-20260925`** | **`9abf854193`** | #208 → #210 → #177 → Tier 1 stack → #186 → #212 | ✅ [36165557380](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36165557380) | ✅ [36165557531](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36165557531) | Steps 1–5 of §4 |

**Local corroboration:**
- The #208 fixture passes 12/12.
- Renaming the "Still open from" label in `docs/app.js` makes it fail (11/12), so the real dashboard assets are still exercised.
- A clean CI-equivalent venv (NFL requirements + requests, no pandas) passes all 93–95 NFL test files.
- Without numpy, the Tier 1 WS-C/WS-D tests fail. So #210 is both necessary and sufficient.
- The frozen harness, protocol, seal builder and drivers are byte-identical to `e05e02c592`.
- No workflow, dashboard or root script references the research modules.

**Independent reviews** (read-only, against the actual diffs), none with HIGH or MEDIUM findings:
- #208, #210, #177 (its LOW F-namespace note is fixed in `8dfbac39ce`), the #186 union and the combined tree;
- #174, #170, #206, #209: all CLEAN, with LOW items listed in their rows.

The Tier 1 research PRs and #212 carry their own recorded independent reviews.

## 2. Every CI failure on a current PR head, with its demonstrated cause
From `ci_failure_attribution.json`. Failures on the 13 re-rooted PRs (#73–#85) come from their pre-rewrite trees and are not integration candidates.

| Failing test / message | PRs | Cause | Repair |
|---|---|---|---|
| `test_browser_today_central.py` — `AssertionError: fixture needs four top picks` | #214, #211, #210, #209, #206, #201, #198, #192, #191, #187, #186, #184, #131, INT177, report branch | The fixture copies live Top Picks. Current main data has 1; the fixture needs 4. It reproduces on plain main locally | **#208**, verified green in every tree that contains it |
| `nfl/tests/test_tier1_team_context.py` / `test_tier1_touchdown.py` — `ModuleNotFoundError: No module named 'numpy'` | #204, #205 | NFL CI installs only `nfl/requirements-nfl.txt`, which lacks numpy | **#210**, which the negative control shows is necessary and sufficient |
| `test_browser_e2e.py` — `at least one clickable pick-card/prop-row exists on Today…` | #202, #207, #213 | The branches carry a stale Sept 24 `docs/data.json` snapshot; they change no `docs/` files. The e2e test rebases timestamps but Today filters by Central slate date, so a day-old snapshot shows no Today cards | None needed for these PRs: the Tier 1 tree with current main data is green |

**Latent defect found (not introduced by any PR; recorded, not fixed here).** `test_browser_e2e.py` needs at least one visible Today card in `docs/data.json`, so main's own root CI will fail on any day when no Top Pick is visible. That is the same live-data-volume class of defect #208 fixed in the Central-midnight test.
- **Proposed narrow repair:** give the e2e detail-sheet and My Board checks a deterministic synthetic Top Pick, following #208's pattern, without weakening any assertion.
- **Owner:** unassigned (proposed Claude, as one small test-only PR once Jacob approves).

## 3. Disposition of every open PR (44)

| PR | Head | Base | Disposition | CI (latest on head) | Dependencies | Scientific status | Remaining blocker | Owner | Next action |
|---|---|---|---|---|---|---|---|---|---|
| #214 | `0d324fd961` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36160147508) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36160147384) | #208 | F18 source-gate docs (rights/assignment blocked) | root: #208 fixes `fixture needs four top picks` | Codex | CI refresh after #208 |
| #213 | `edddb9ab2b` | claude/nfl-tier1-foundation-20260924 | READY (research-only) | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36159569976) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36159570083) | #202 | F17 NOT SUPPORTED (preserved) | head e2e stale-data snapshot only | Claude | after #202 |
| #212 | `0a303be087` | claude/nfl-rushing-yards-baseline-20260923 | READY (research-only) | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36158323581) [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36158323547) | #186 | F16 negative (preserved) | none | Codex | after #186 |
| #211 | `2930030da4` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36156586107) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36156586048) | #208 | F15 held evaluation, independent-review corrections recorded | root: #208 fixes `fixture needs four top picks` | Codex | CI refresh after #208 |
| #210 | `3f5368d924` | main | **READY FOR APPROVAL** | [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36155296333) [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36155296323) | #208 (root CI) | NFL-CI-only dependency | none | SUPERCHAD→Claude | Jacob: merge 2nd (tree `0933a89b3a`) |
| #209 | `c5403efa9a` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36154570736) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36154570569) | #208 | F14 NULL; independently reviewed CLEAN 2026-09-25 | root: #208 fixes `fixture needs four top picks`; LOW: same temp-file pattern | Codex | CI refresh after #208 |
| #208 | `d68b2d6073` | main | **READY FOR APPROVAL** | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36153963241) [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36153963351) | none | test-infrastructure repair | none | SUPERCHAD→Claude | Jacob: merge 1st (tree `6f282f2d80`) |
| #207 | `f4fb17aa0b` | claude/nfl-tier1-foundation-20260924 | READY (research-only) | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36157035013) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36157034994) | #202 | F11 REJECTED / F12 NOT SUPPORTED (preserved) | head e2e stale-data snapshot only | Claude | after #202 |
| #206 | `556bc26e6e` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36148185695) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36148185837) | #208 | F13 NEGATIVE; independently reviewed CLEAN 2026-09-25 | root: #208 fixes `fixture needs four top picks`; LOW: tests write temp files in repo path | Codex | CI refresh after #208 |
| #205 | `76b42547c3` | claude/nfl-tier1-foundation-20260924 | READY (research-only) | [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36062920159) [✗ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36062920098) | #202, #210 | F4 narrow historical; H3 prospective active | head NFL CI needs numpy → #210 | Claude | after #202/#210 |
| #204 | `40d842c9a9` | claude/nfl-tier1-foundation-20260924 | READY (research-only) | [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36062903514) [✗ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36062903531) | #202, #210 | F1/F5/F6/F7/F10; H2 prospective active | head NFL CI needs numpy (`No module named 'numpy'`) → #210 | Claude | after #202/#210 |
| #203 | `8aa8067fbc` | claude/nfl-tier1-foundation-20260924 | READY (research-only) | [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36062890834) [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36062890696) | #202 | F2/F3/F8/F9 historical; H1 prospective active | none | Claude | retarget to main after #202; merge |
| #202 | `e05e02c592` | main | **READY FOR APPROVAL** (research-only) | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36162946955) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36162946809) | #208, #210 | Tier 1 contract/harness/protocol; seal runs from exact SHAs on a separate branch | head's e2e failure is its stale Sept 24 `docs/data.json` snapshot (the PR touches no docs); integration tree green | Claude | Jacob: merge 4th |
| #201 | `025fdf63ed` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166966118) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166966127) | #208 | MLB overconfidence, pre-registered, inconclusive | root: #208 fixes `fixture needs four top picks` (handoff repaired `025fdf63ed`) | Claude | CI refresh after #208 |
| #199 | `285252d313` | codex/nfl-price-aware-offers-20260923 | ACTIVE WORK | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36017944942) [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36017944941) | #196 | offer→sealed-B0 join research | same gates | Codex | after #196 |
| #198 | `d21ba2f5fc` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166973509) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166973536) [✅ web](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166973498) | #208 | MLB slate-date audit, no code change | root: #208 fixes `fixture needs four top picks` (repaired `d21ba2f5fc`) | Claude | CI refresh after #208 |
| #196 | `7626a0424f` | main | ACTIVE WORK | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36015414843) [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36015414637) | none | price-aware offer research | 3 gates: BOOK_ACTION_RULES_NOT_CERTIFIED, CURRENT_ROLE_NOT_VERIFIED, QUOTE_TIMESTAMP_NOT_PROVIDED | Codex | certify gates |
| #193 | `675d84f087` | main | ACTIVE WORK | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35943683950) [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35943683937) | none | Mission 10 pre-registered forward shadow | handoff conflict deliberately unrepaired until grading | Claude | Sep 30 trigger grades; then union-repair |
| #192 | `c30c1c3a37` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166977687) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166979086) [✅ web](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166978958) | #208 | MLB Top Pick calibration holdout | root: #208 fixes `fixture needs four top picks` (repaired `c30c1c3a37`) | Claude | CI refresh after #208 |
| #191 | `0d85251d7e` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166996467) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166996402) [✅ web](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166996347) | #208 | NFL error-decomposition diagnostic | root: #208 fixes `fixture needs four top picks` (repaired `0d85251d7e`) | Claude | CI refresh after #208 |
| #190 | `fddbed763a` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35929799012) [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35929798958) | #208 | NFL passing-yards alt ladder research | head CI green on its older base; needs exact-tree run on new main | Claude | CI refresh after #208 |
| #188 | `9266b78980` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35927980289) [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35927980278) | #208 | MLB full-board calibration research | head CI green on older base | Claude | CI refresh after #208 |
| #187 | `8e413cc072` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166997810) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166998097) [✅ web](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166997949) | #208 | MLB test-coverage (adds a test) | root: #208 fixes `fixture needs four top picks` (repaired `8e413cc072`) | Claude | CI refresh after #208 |
| #186 | `efb40423ff` | main | READY (research-only) | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36167027099) [✅ web](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36167027068) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36167027103) | #208, #210 (combined tree) | rushing B0 research, not authoritative | none (handoff union applied to head `efb40423ff`) | Claude | after Tier 1 |
| #184 | `ba757f1fdf` | main | READY AFTER #208 exact-tree CI | [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166998425) [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166998209) [✅ web](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36166998252) | #208 | NFL ablation diagnostic | root: #208 fixes `fixture needs four top picks` (repaired `ba757f1fdf`) | Claude | CI refresh after #208 |
| #177 | `fcd9094dba` | main | **READY FOR APPROVAL** (via integration tree `8dfbac39ce`) | [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35905406576) [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35905406694) | #208 | permanent requirements register (docs only) | PR head (`superchad/…`) lacks §15/§16; certified content is the integration branch | SUPERCHAD→Claude | Jacob: merge the integration content 3rd |
| #174 | `db54f1f6dc` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35889072017) [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35889072031) | none | FTN descriptive charting; independently reviewed CLEAN | LOW: README says 10 tests (14) | Codex | CI refresh on new main |
| #170 | `a898e3660d` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35763446798) [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35763446598) | none | synthetic film prototype; real source BLOCKED; reviewed CLEAN | none | Codex | CI refresh on new main |
| #131 | `c7dc7fdfd1` | main | READY AFTER #208 exact-tree CI | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36167006908) [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36167006895) [✅ web](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36167006844) | #208 | MLB selector diagnosis (handoff doc) | root: #208 fixes `fixture needs four top picks` (repaired `c7dc7fdfd1`) | Claude | CI refresh after #208 |
| #130 | `342f69e71e` | main | **SUPERSEDED** | [✅ NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35365047137) [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/35365047176) | none | — | identical `.claude/worktrees/` rule + comment already on main (.gitignore 28–32) | Claude | Jacob: close |
| #110 | `390e04d3fd` | main | **SUPERSEDED** | [cancelled NFL](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/34911668831) [cancelled root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/34911668842) | none | — | both files byte-identical on main | SUPERCHAD | Jacob: close |
| #85 | `4744ad2fc7` | main | DEPENDENCY BLOCKED (re-rooted history) | [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33667172866) | — | PA-v1 lifecycle closure, marked DO NOT MERGE | no merge base; 48 unique files Unique files absent on main: 48. | Claude / Jacob | archive (preserve branch) unless PA-v1 is revived |
| #84 | `d9d40fa175` | superchad/experiment-primitives-01 | DEPENDENCY BLOCKED (re-rooted history) | [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33261367760) | stacked superchad chain | MLB research code scaffolds (unvalidated) | no merge base; unique backtest/*.py + tests written against the old tree Unique files absent on main: 15. | SUPERCHAD | port with fresh review + CI on current main, or archive |
| #83 | `d9e6021b7b` | superchad/hr-contact-state-feature-scaffold-01 | DEPENDENCY BLOCKED (re-rooted history) | [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33259948724) | stacked superchad chain | MLB research code scaffolds (unvalidated) | no merge base; unique backtest/*.py + tests written against the old tree Unique files absent on main: 2. | SUPERCHAD | port with fresh review + CI on current main, or archive |
| #82 | `926cf3f814` | superchad/fix-board-first-paint-clock-fixture-01 | DEPENDENCY BLOCKED (re-rooted history) | [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33261504428) | stacked superchad chain | MLB research code scaffolds (unvalidated) | no merge base; unique backtest/*.py + tests written against the old tree Unique files absent on main: 4. | SUPERCHAD | port with fresh review + CI on current main, or archive |
| #81 | `95a4b80521` | superchad/experiment-primitives-01 | DEPENDENCY BLOCKED (re-rooted history) | [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33259481528) | stacked superchad chain | MLB research code scaffolds (unvalidated) | no merge base; unique backtest/*.py + tests written against the old tree Unique files absent on main: 2. | SUPERCHAD | port with fresh review + CI on current main, or archive |
| #80 | `77442dabfe` | superchad/hr-execution-prereg-v2-01 | DEPENDENCY BLOCKED (re-rooted history) | [cancelled root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33259890063) | stacked superchad chain | MLB research code scaffolds (unvalidated) | no merge base; unique backtest/*.py + tests written against the old tree Unique files absent on main: 2. | SUPERCHAD | port with fresh review + CI on current main, or archive |
| #79 | `7794220733` | superchad/fix-board-first-paint-clock-fixture-01 | DEPENDENCY BLOCKED (re-rooted history) | [✅ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33260060751) | stacked superchad chain | MLB research code scaffolds (unvalidated) | no merge base; unique backtest/*.py + tests written against the old tree Unique files absent on main: 2. | SUPERCHAD | port with fresh review + CI on current main, or archive |
| #78 | `b019e49981` | main | DEPENDENCY BLOCKED (re-rooted history) | [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33258381531) | — | MLB pre-registration documents | no merge base; unique prereg docs only (rest is old generated data) Unique files absent on main: 1. | SUPERCHAD | smallest action: port the prereg .md files into engineering/ in one docs PR |
| #77 | `5869216a73` | main | DEPENDENCY BLOCKED (re-rooted history) | [cancelled root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33259352845) | — | MLB pre-registration documents | no merge base; unique prereg docs only (rest is old generated data) Unique files absent on main: 1. | SUPERCHAD | smallest action: port the prereg .md files into engineering/ in one docs PR |
| #76 | `26dbe36714` | tooling/superclaude-activation-01 | DEPENDENCY BLOCKED (re-rooted history) | [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33256810680) | — | SuperClaude activation tooling | no merge base with main; unique `.claude/` files + CLAUDE.md edits Unique files absent on main: 3. | SUPERCHAD / Jacob | decide: port agent/skill definitions via a new docs PR, or archive |
| #75 | `26d37fd475` | main | **SUPERSEDED** (verified) | [cancelled root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33259722404) | none | — | all 3 commits' fixes present on main in equivalent/evolved form (5b67: 28/28 lines; 26d37: 19/20; c001 superseded by main's model_basis_at+market_prices_at stale fixture) Unique files absent on main: 0. | SUPERCHAD | Jacob: close |
| #74 | `0ae4535d5a` | main | DEPENDENCY BLOCKED (re-rooted history) | [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33548879722) | — | MLB pre-registration documents | no merge base; unique prereg docs only (rest is old generated data) Unique files absent on main: 2. | SUPERCHAD | smallest action: port the prereg .md files into engineering/ in one docs PR |
| #73 | `79f1109fc1` | main | DEPENDENCY BLOCKED (re-rooted history) | [✗ root](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/33548167737) | — | SuperClaude activation tooling | no merge base with main; unique `.claude/` files + CLAUDE.md edits Unique files absent on main: 20. | SUPERCHAD / Jacob | decide: port agent/skill definitions via a new docs PR, or archive |

**Totals:**

| Category | Count | PRs |
|---|---|---|
| READY FOR APPROVAL | 4 | #208, #210, #177, #202 |
| READY (research-only) | 7 | #203, #204, #205, #207, #213, #186, #212 |
| READY AFTER #208 exact-tree CI | 15 | #211, #214, #206, #209, #174, #170, #201, #198, #192, #191, #190, #188, #187, #184, #131 |
| ACTIVE WORK | 3 | #196, #199, #193 |
| SUPERSEDED | 3 | #130, #110, #75 |
| DEPENDENCY BLOCKED (re-rooted history) | 12 | #73, #74, #76–#85 |

**The re-rooted PRs.** Their unique content is listed file by file in `rerooted_pr_unique_files.json`. The "differs on main" entries there are almost entirely old generated `output/`, `data/` and dashboard-state files; that material is obsolete.

What is worth preserving:
- **Prereg documents** (#74, #77, #78): the smallest action is one docs-only port PR.
- **MLB research code** (#79–#84, #85): port only with fresh review and CI on current main, or archive.
- **`.claude/` agent and skill definitions** (#73, #76): Jacob decides.

**Every branch stays in place, and nothing is closed or deleted.**

## 4. Dependency-aware merge sequence (each step needs Jacob's explicit approval)
1. **#208**, which fixes the shared root-CI failure.
2. **#210**, the NFL-only numpy pin. It must land before #204/#205.
3. **#177**, using the integration content `8dfbac39ce`.
4. **#202**, then #203, #204, #205, #207, #213, retargeted to main. Research-only; no production activation; the seal is unaffected.
5. **#186** (head `efb40423ff`, union-repaired), then **#212**.
6. **Exact-tree CI refresh on the new main**, then the 15 READY-AFTER-#208 PRs.
7. **#193** after its Sep 30 pre-registered grading. **#196/#199** after Codex's gate certification.
8. **Jacob's decisions** on #130, #110 and #75 (superseded) and #73–#85 (port or archive).

The combined tree for steps 1–5 is `9abf854193`, green on both suites.

## 5. Genuinely unresolved (owner, then next action)
- **Pricing gates:** Codex must certify `BOOK_ACTION_RULES_NOT_CERTIFIED`, `CURRENT_ROLE_NOT_VERIFIED` and `QUOTE_TIMESTAMP_NOT_PROVIDED` (#196/#199).
- **Re-rooted PRs:** Jacob or SUPERCHAD chooses port or archive for #73–#85.
- **Latent e2e defect:** unassigned; the proposed repair is in §2.
- **Optional LOW cleanups in Codex PRs:** Codex (#174 README test count; #206/#209 temporary-file location).
- **Merge authorization** for each step in §4: Jacob.
- **Saturday seal:** Claude runs the prospective capture at 2026-09-26 18:30Z.

Alligator.

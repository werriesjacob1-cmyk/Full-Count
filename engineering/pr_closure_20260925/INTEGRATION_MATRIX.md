# PR closure and merge readiness: integration matrix (2026-09-25)

- **Workstream:** PR-CLOSURE-MERGE-READINESS-20260925. Codex started it (it claimed the work at 16:34Z and then ran out of usage). Claude Code continued it on #91, comment 5836256690.
- **Base:** main at `e48bb2ac21` when refreshed.
- **Open PRs:** 44.
- **Authority:** nothing here merges, closes, deploys or promotes anything. Every READY item still needs Jacob's explicit approval.
- **Seal isolation:** the Saturday Tier 1 seal is on its own branch and trigger and is outside this work: `claude/nfl-tier1-seal-2026w03`, `claude/nfl-seal-inputs-20260925`, `trig_01FiiD9MknrMxbB1zbeS4d5N`.

**Certification standard.** A PR is READY only when all of these hold:
- a complete implementation for its declared scope;
- exact-integration-tree CI passing (root `test.yml` and `nfl-tests.yml`);
- an independent review of the actual diff;
- no conflict;
- no undocumented dependency;
- no research-integrity defect;
- no production or customer change;
- no interference with frozen evidence.

"READY (research-only)" means safe to integrate as research code. **It is not production readiness, and it is not model promotion.**

## Certified integration trees

| Tree | SHA | Contents | Root CI | NFL CI | Local evidence |
|---|---|---|---|---|---|
| `codex/pr208-integration-20260925` | `6f282f2d80` | main + #208 | ✅ [36164562893](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36164562893) | ✅ 36164562999 | Main reproduces the failure: 1 live Top Pick → `AssertionError: fixture needs four top picks`. The tree passes 12/12. A mutation (renaming the "Still open from" label in `docs/app.js`) is caught (11/12). |
| `codex/pr210-integration-20260925` | `0933a89b3a` | + #210 | ✅ [36164601803](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36164601803) | ✅ 36164601793 | In a clean CI-equivalent venv with only `nfl/requirements-nfl.txt` and requests, and with no pandas, all 93 NFL test files pass. Without numpy, the Tier 1 team-context and touchdown tests fail with "No module named 'numpy'". |
| `codex/pr177-integration-20260925` | `8dfbac39ce` | main + #177 + Codex §15 + Claude §16 | ✗ only because #208 is missing (docs-only change; main fails the same fixture) | ✅ | Certified inside the sequence tree below |
| `claude/tier1-research-integration-20260925` | `59492822f8` | #210 tree + #202, #203, #204, #205, #207, #213 | ✅ [36164939177](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36164939177) | ✅ [36164938965](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36164938965) | Merges are conflict-free. The frozen harness, protocol, seal builder and drivers are byte-identical to `e05e02c592`. There is no production reference. |
| `claude/pr186-integration-20260925` | `6f549c9d0a` | main + #186 (append-only handoff union) + #212 + #208/#210 | ✅ [36165038521](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36165038521) | ✅ 36165038564; web ✅ 36165038510 | Main's handoff is preserved as a byte prefix. Rushing and F16 tests pass. |
| `claude/merge-sequence-integration-20260925` | `9abf854193` | Everything above, in the proposed order | ✅ [36165557380](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36165557380) | ✅ [36165557531](https://github.com/werriesjacob1-cmyk/Full-Count/actions/runs/36165557531) | 95 NFL test files pass, and the browser fixture passes 12/12 (on `0b39dc7ad0`, before the §16 wording fix) |

**Independent review** (sonnet reviewer, read-only, against the actual diffs): #208, #210, #177, the #186 union resolution and the combined tree are all clean. There were no HIGH or MEDIUM findings; the one LOW finding (the F-namespace wording in #177 §16) is fixed in `8dfbac39ce`. The Tier 1 research PRs (#202–#205, #207, #213) each had their own independent reviews, recorded in their READMEs and PRs.

## Disposition of all 44 open PRs

| PR | Owner | Disposition | Evidence / blocker | Next action |
|---|---|---|---|---|
| #208 fixture | SUPERCHAD → Claude | **READY FOR APPROVAL** | Tree `6f282f2d80`, both suites green, reviewed | Jacob: approve and merge first |
| #210 NumPy (NFL only) | SUPERCHAD → Claude | **READY FOR APPROVAL** | Tree `0933a89b3a`, green; necessity and sufficiency shown | Merge second (required by #204/#205) |
| #177 requirements register | SUPERCHAD → Claude | **READY FOR APPROVAL**, via the integration tree | The PR head (`superchad/…`) lacks §15/§16; the certified content is `8dfbac39ce` | Merge the integration branch, or update the PR head to it, after #208 |
| #202 Tier 1 foundation | Claude | **READY FOR APPROVAL (research-only)** | Part of the Tier 1 tree; no production activation. The seal runs from exact SHAs and is unaffected by merging | After #208/#210 |
| #203 WS-B | Claude | READY (research-only) | Based on foundation; merges clean in the tree | Retarget to main after #202, or merge into foundation first |
| #204 WS-C | Claude | READY (research-only) | Needs #210 (numpy) | Same as #203 |
| #205 WS-D | Claude | READY (research-only) | Needs #210 | Same as #203 |
| #207 F11/F12 | Claude | READY (research-only; F11 REJECTED, F12 NOT SUPPORTED; results preserved) | Reviewed | Same as #203 |
| #213 F17 | Claude | READY (research-only; NOT SUPPORTED preserved) | Reviewed | Same as #203 |
| #186 rushing B0 research | Claude | **REPAIR DONE → READY (research-only)** via `claude/pr186-integration-20260925` | The PR head still conflicts on the handoff; the resolution is on the integration branch | Merge the integration branch, or apply the union to the PR head |
| #212 F16 | Codex | READY (research-only; negative result preserved), stacked on #186 | Codex recorded an identity-review correction; merges clean | After #186 |
| #196 price-aware offers | Codex | **ACTIVE WORK** | The 3 eligibility gates are uncertified (rules, current role, quote timestamp) | Codex: certify the gates |
| #199 offer→B0 join | Codex | **ACTIVE WORK** (stacked on #196) | Same | Same |
| #174 FTN tactical charting | Codex | SCIENTIFIC REVIEW REQUIRED | Descriptive prototype; merges clean; the diff has not been independently reviewed this round | Independent diff review, then an exact-tree CI run after #208 |
| #170 film prototype | Codex | SCIENTIFIC REVIEW REQUIRED | Synthetic prototype, not an operational film source | Same |
| #206 F13 | Codex | SCIENTIFIC REVIEW REQUIRED | Negative result; no independent review recorded | Review, then CI after #208 |
| #209 F14 | Codex | SCIENTIFIC REVIEW REQUIRED | Null result; no independent review recorded | Same |
| #211 F15 | Codex | READY AFTER #208 CI refresh (research-only) | Independent-review corrections recorded; merges clean | Exact-tree CI after #208 |
| #214 F18 source gate | Codex | READY AFTER #208 CI refresh (documentation) | Revised after independent review; merges clean | Same |
| #193 target-share forward shadow | Claude | **ACTIVE WORK** | Pre-registered grading is scheduled for Sep 30 (`trig_017aueLwd923cGSr4ZVdkKGQ`); conflicts only on the handoff | Grade, then union-resolve the handoff |
| #201 MLB overconfidence | Claude | REPAIR REQUIRED | Conflicts only on the handoff (append-only union, as done for #186) | Union-resolve, then CI after #208 |
| #198 MLB slate-date audit | Claude | REPAIR REQUIRED | Same, handoff only | Same |
| #192 MLB Top Pick calibration | Claude | REPAIR REQUIRED | Same, handoff only | Same |
| #191 NFL error decomposition | Claude | REPAIR REQUIRED | Same, handoff only | Same |
| #187 MLB full-board snapshot test | Claude | REPAIR REQUIRED | Same, handoff only | Same |
| #184 NFL 2024 ablation | Claude | REPAIR REQUIRED | Same, handoff only | Same |
| #131 MLB selector diagnosis | Claude | REPAIR REQUIRED | Handoff-only PR; its section is not on main | Union-resolve |
| #190 passing-yards alternate ladder | Claude | READY AFTER #208 CI refresh (research-only) | Merges clean; reviewed in its mission | Exact-tree CI after #208 |
| #188 MLB full-board calibration | Claude | READY AFTER #208 CI refresh (research-only) | Merges clean | Same |
| #130 gitignore worktrees | Claude | **SUPERSEDED** | Main already contains the identical `.claude/worktrees/` rule and comment (`.gitignore` lines 28–32) | Jacob: close; nothing unique is lost |
| #110 scoring prior features | SUPERCHAD | **SUPERSEDED** | Both files are byte-identical on main | Jacob: close |
| #73 / #76 SuperClaude activation | SUPERCHAD | DEPENDENCY BLOCKED (history re-rooted) | No merge base with main (different root commit); 29 unique `.claude/` agent files; marked "DO NOT MERGE — review only" | Jacob or SUPERCHAD: port the wanted files to current main, or archive |
| #74 / #77 HR execution prereg v1/v2 | SUPERCHAD | DEPENDENCY BLOCKED (re-rooted) | Unique `engineering/PREREG_HR_EXECUTION_V1/V2*.md` | Same |
| #78 PA opportunity prereg | SUPERCHAD | DEPENDENCY BLOCKED (re-rooted) | Unique `PREREG_PA_OPPORTUNITY_DECISIVE_V1.md` | Same |
| #79 / #81 experiment primitives, PA runner | SUPERCHAD | DEPENDENCY BLOCKED (re-rooted) | Unique `backtest/experiment_primitives.py`, `pa_opportunity_decisive.py` and their tests | Same |
| #80 / #83 / #84 HR contact-state stack | SUPERCHAD | DEPENDENCY BLOCKED (re-rooted) | Unique `backtest/hr_contact_state_*`, `hr_offset_estimator.py` (17 files in #84) | Same |
| #82 canonical certifier | SUPERCHAD | DEPENDENCY BLOCKED (re-rooted) | Unique `backtest/canonical_certification.py`, `generation_regime.py` and their tests | Same |
| #85 PA-v1 lifecycle closure | Claude | DEPENDENCY BLOCKED (re-rooted; "DO NOT MERGE") | 48 unique files, including `backtest/pa_v1_*` | Same |
| #75 four-clock fixture | SUPERCHAD | SUPERSEDED candidate (re-rooted) | No files unique to the PR | Jacob: confirm and close |

Count: 11 certified items (#208, #210, #177, #202–#205, #207, #213, #186, #212) + 2 active pricing (#196, #199) + 4 review-required (#174, #170, #206, #209) + 2 Codex after-refresh (#211, #214) + 1 active shadow (#193) + 7 handoff repairs + 2 clean Claude (#190, #188) + 2 superseded (#130, #110) + 13 re-rooted (#73–#85, including the #75 superseded candidate) = **44**.

## Proposed dependency-aware merge sequence (every step needs Jacob's explicit approval)

1. **#208**, which fixes the shared root-CI failure.
2. **#210**, the NFL-only numpy pin, required by #204/#205.
3. **#177**, using the integration content (`8dfbac39ce`).
4. **#202**, then #203, #204, #205, #207, #213: research-only; retarget to main after #202.
5. **#186**, using the union resolution, then **#212**.
6. #211, #214, #190, #188: after an exact-tree CI refresh on the new main.
7. The handoff-only repairs (#201, #198, #192, #191, #187, #184, #131), each union-resolved and CI-refreshed. #193 after its Sep 30 grading.
8. Reviews for #174, #170, #206, #209. #196/#199 wait for Codex's gate certification.
9. Jacob decides #110 and #130 (superseded) and #73–#85 (port or archive).

The combined tree for steps 1–5 is `claude/merge-sequence-integration-20260925` (see CI above).

## Genuinely unresolved
- **Pricing gates:** Codex owns certifying `BOOK_ACTION_RULES_NOT_CERTIFIED`, `CURRENT_ROLE_NOT_VERIFIED` and `QUOTE_TIMESTAMP_NOT_PROVIDED` (#196/#199).
- **Unreviewed research:** #174, #170, #206 and #209 lack an independent diff review this round (owner Codex; any reviewer).
- **Pre-rewrite history:** 13 PRs sit on a history with no common base with main. Jacob or SUPERCHAD must choose port or archive; this workstream does not port them.
- **Main's root CI** fails until #208 lands, because live Top Pick volume varies. Every PR's root CI inherits that failure until then.
- **Seal:** the Saturday seal remains the priority; the trigger fires 2026-09-26 18:30Z.

Alligator.

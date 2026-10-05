# V3 prospective grading and champion-vs-V3 comparison: audit

**Scope:**
- What happens after a legitimate prospective V3 unit is sealed, and whether future evidence can answer the North-Star question: *at the same legitimate, usable operational pick volume, does V3 realize a higher MLB prop hit rate than the champion?*
- The audit is read-only, with synthetic fixtures for every new test.
- No outcome of any real unit was read. The Oct 3–4 units are not scored.

**Code audited:**
- implementation `dac663c0a2` (FC-MLB-001B branch);
- evaluator `v3/evaluate_v3.py`, harness `harness.py` and `harness_v2.py`, manifest `v3/manifest_v3.py`, regimes `v3/regimes.py`;
- pinned grader `board_freeze_grader.py` → `grade_results.grade_pick` at shadow pin `7d3ebacd55`.

## Proven by the existing implementation
| Question | Answer and where |
|---|---|
| **When are outcomes read?** | Only in `evaluate_from_evidence`, after `one_look_check`, mandatory chain and unit verification, and `publish_lock`, which is committed and pushed **before** any grade (`evaluate_v3.py` 223–262). A second look is refused (`ONE_LOOK_ALREADY_TAKEN`). Collection (`runner.py`) never reads outcomes. |
| **Settlement source** | The pinned grader only (`grade_shadow_board`, 105): `board_freeze_grader.grade_frozen_board` at the pin. It verifies the sealed board's own seal, then `grade_results.grade_pick` (pin line 527) uses statsapi game status and box scores. No production graded file is ever opened. |
| **Settlement timing** | Postseason (descriptive): not before 2026-11-16. 2027 confirmatory: on or after 2027-10-05, and only if every covered game is final under the pin's `is_final` (pin line 267); otherwise not before the later of 2027-10-12 and 7 days after the last unit (`ANALYSIS_RULES`, `one_look_check` 125). |
| **Stat corrections** | Box scores are fetched once, at the single look, at least 7 days after the slate in the grace path, so normal official corrections are included. That fetch is final, because the look cannot be repeated. **Gap G1** (fixed here): the graded outputs were not persisted. |
| **Postponed / suspended / cancelled** | Not final under the pin, so the pick is `ungraded`. The evaluator maps it to `unresolved` (`GRADE_CLASS`) and excludes it from both arms' hit-rate denominators. It never unlocks the early analysis path. |
| **Void / push** | The pin never emits `void` or `push` (half-point lines). The `GRADE_CLASS` mapping for them is inert. |
| **Player non-start** | A player missing from the box score gives `get_box_line` → no row → `ungraded`, which is unresolved and excluded symmetrically. A late sub who appears is graded on the actual line. |
| **Paired eligible population** | The sealed manifest decides eligibility pregame (`manifest_v3.py` 300–389), as eligible PRIMARY rows: family, one-to-one event mapping, an exact quote at the cutoff, board price = captured price, implied probability in [0.40, 0.70], and a covered, timed game. There are no outcome inputs. Champion top picks excluded by eligibility are counted by reason. |
| **Equal-volume N_d** | Per slate, `harness.select` (140): N_d = the count of sealed champion rows in the eligible universe. V3 (`C2_RESIDUAL`) takes exactly N_d rows of the **same** sealed eligible set by its frozen key, ties broken by candidate id. There is no outcome, no cross-slate carryforward, and slates with N_d = 0 contribute nothing. `_compute` asserts every arm stays inside the sealed universe. |
| **Champion selections** | `shadow_champion = recommendation_status == "top_pick"` on the sealed shadow board of the frozen pin (`manifest_v3.py` 363). |
| **Overlap / champion-only / V3-only** | `harness.overlap_table` (199), with hit rates for each disjoint part. |
| **Paired uncertainty** | `harness_v2.clustered_diff` (51): a cluster bootstrap of the hit-rate difference by game, player or ISO week; B=2000, seed 20261001. A one-sided lower 95% bound drives the confirmatory verdict. |
| **No backfill** | The dispatcher reports `MISSED_NO_BACKFILL`; the runner never seals late; the evaluator reads only the anchored, chained units. |
| **No outcome-conditioned exclusion** | Exclusions are only (a) pregame eligibility, sealed; (b) chain or verification failure, decided before the lock; (c) settlement status (`ungraded`), which depends on game and player participation, never on hit or miss. |
| **No leakage** | Selection reads only sealed pregame rows. The grader runs on the sealed board after the lock. The shadow board is replayed from sealed inputs (001B) during verification, before the lock. |
| **Regime separation** | `regimes.check_regime` rejects any unit outside the regime's calendar or game types; one call is one regime. A descriptive regime's verdict is forced to `NOT_APPLICABLE_DESCRIPTIVE_REGIME`. |
| **Oct 3–4 units** | They already fail `verify_unit` (shadow not reproducible), so they are invalid before the lock. **Gap G3** (hardened here): they are now also excluded by name. |

## Gaps found and their status
| Id | Gap | Severity | Status |
|---|---|---|---|
| **G1** | `evaluate_from_evidence` returns the single look in memory only. There is no entrypoint, and neither the result nor the pinned-grader outputs are persisted. Because the look cannot be repeated (`ONE_LOOK_ALREADY_TAKEN`), a caller that does not save it loses the confirmatory result. | high, at evaluation time | **Fixed on the prep branch:** `evaluate_v3.main()` → `write_result` (written locally first: `FINAL_RESULT_<regime>.json`, `SCORECARD_<regime>.md`, `GRADED/<unit>.json`, with each graded sha256 bound in the result) → `publish_result` (append-only to the evidence ref; refuses to rewrite). Tests in `test_scorecard.py`. |
| **G2** | The pinned grader runs on the **host** interpreter (`grade_shadow_board`: `python3 -c` in a pin worktree), so its packages (pandas and others) are unpinned at evaluation time. | medium, at evaluation time | **Open, not a collection blocker.** Proposed: run `grade_frozen_board` inside the 001B sandbox with the hash lock, with network allowed for statsapi and the graded output persisted (G1). Decide before 2026-11-16. |
| **G3** | The Oct 3–4 units were excluded only because their replay fails. | low (defence in depth) | **Hardened on the prep branch:** `evaluate_v3.QUARANTINED_UNITS` excludes them by name before verification. Test: `test_postseason_is_descriptive_and_quarantined_units_never_scored`. |
| **G4** | The output has no consolidated North-Star scorecard: no running series and no per-family, phase or concentration view. | reporting | **Built:** `scorecard_v3.py`, computed only inside the post-lock path from the same `pairs` with the frozen selection. It is labelled DESCRIPTIVE SHADOW or CONFIRMATORY, and regimes are never mixed. |

**Not changed:** the challenger model, coefficients, selector, rankings, projected PA, hypotheses, equal-volume procedure, 2027 regime, prereg, pinned grader and verdict logic. The scorecard adds no new statistic to the verdict.

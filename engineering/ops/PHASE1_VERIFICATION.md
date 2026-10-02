# FC-OPS-001 — Phase 1 focused verification (owner notes; 2026-10-02)

| Check | Result | Evidence |
|---|---|---|
| A. Fresh-session recovery | **PASS after 1 repair** | Session `session_01KfyMEbPYUcsNvnrWZ1Wrnv` (Sonnet, depth-1 clone of PR #221) read CLAUDE.md plus the `fc.py orient` output only; no handoff and no #91. Total context 69k (≈45k platform baseline). Answered authority, V3/runner and FC-NFL-002 questions. **Gap:** checkpoint fields were missing from the default path. Repaired by adding a "Working rules" summary to CURRENT_STATE; re-checked deterministically (orient output contains `CHECKPOINT`) |
| B. Queue | **PASS after repair** | MLB-002 and NFL-002 active concurrently (queue rc 0); a second active MLB build is refused (WIP); `PATH_CONFLICT` detected. **Defects found and fixed:** build/research could skip the challenge to DONE or READY_FOR_SUPERCHAD; "TBD" counted as acceptance criteria; cross-agent claims were possible. Now refused: `NO_SELF_APPROVAL`, challenger-only pass, SUPERCHAD/Jacob-only DONE, owner-matched claims, `ACTIVE_WITHOUT_ACCEPTANCE_CRITERIA` |
| C. Audit isolation | **PASS** | A sentinel conclusion in BUILDER_NOTES appears in the owner capsule (1) and not in the challenge capsule (0). If copied into ACCEPTANCE, the result is `ISOLATION_FAILURE` (fail closed) |
| D. Token path | **PASS** | Mandated orientation was 66,547 B (≈16.6k tokens, ≥7 reads); now 11,480 B (≈2.9k tokens, 1 call): **−83%**. Against agents that read the whole 439 KB handoff: −97% |
| E. Polling | **PASS** | `fc.py status --until-change 10` ran in the background with no model turns and woke with a single line: `branch:claude/full-count-ops-state: e2425cee3a -> 4d197e91c7` (rc 3). The baseline covers 8 targets: 5 branches, PR #220 + CI, #91 count, V3 chain length |
| F. Navigator pilot | **Serena not retained** | See below |
| G. Safety | **PASS** | No change to V3 (`504ca9cdb9`, evidence `968c0e550e`, runner and trigger), the PR #220 head, production code, selectors, the ledger or main. No merge, deploy or promotion |

## F. Code-navigation pilot
**Tool:** Serena 1.7.0 (MIT). GitNexus was excluded: PolyForm-Noncommercial-1.0.0 license. **Task:** trace the pitcher-strikeout projection from inputs to board field.

| | Baseline (grep/sed, batched) | Serena (LSP symbols via MCP) |
|---|---|---|
| Model turns | 3 | 1 (scripted); ≥5 tool calls if used interactively |
| Result bytes | ~6.5 KB | 11.5 KB (reference context includes tests/other files) |
| Completeness | Full: K%/L14 inputs, workload, `build_candidates`, `attach_hit_probabilities` → `p_at_least_strikeouts` (`generate_picks.py:6374`), `classify_recommendation`, `recommendation_status` field (`:5154`) | Call graph only; inputs and board field still needed grep |
| Fixed overhead | 0 | 21 tool schemas = 28.7 KB (≈7k tokens) once loaded; LSP start ≈5 s per container; nested uvx/pyright fetch failed until pre-warmed (environment workaround) |
| Precision | Grep also matches comments | Semantic references (better) |

**Decision:** net benefit is marginal or negative for this repository and workflow. **Not retained.**
- Nothing was registered as an MCP server.
- The uv cache (`serena-agent`, `pyright`), `~/.serena` and the pilot worktree were removed.

**Revisit only for** large cross-file refactors where reference precision matters.

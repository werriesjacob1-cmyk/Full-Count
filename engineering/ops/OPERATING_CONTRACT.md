# FULL COUNT — Claude/Codex operating contract (Balanced Max, Phase 1)
Binding for Claude Code and Codex. Authority rules here never weaken `CLAUDE.md`, `AGENTS.md`, or any prereg.

## 1. Orientation (default path)
1. Read the core instructions (`CLAUDE.md` / `AGENTS.md`), which load automatically.
2. Run one call:
   `git fetch -q --depth 1 origin claude/full-count-ops-state && git show FETCH_HEAD:engineering/ops/bin/fc.py | python3 - orient [TASK_ID]`
   This prints CURRENT_STATE, the queue board and the task capsule.
3. Read only the code and scoped instructions your task touches.

Read history (handoff, #91 threads, old PR bodies) **only** for historical reconstruction, and say so in the checkpoint.

## 2. Roles, lanes, ownership
- **Lane A:** Claude builds or researches → Codex challenges → PASS or BLOCK → Claude minimum repair → Codex delta-only check → READY_FOR_SUPERCHAD.
- **Lane B:** Codex researches independently → Claude challenges → READY_FOR_SUPERCHAD.
- Roles may swap per task: `OWNER` and `CHALLENGER` in `WORK_QUEUE.json` are authoritative.
- **Claim** by setting `STATUS` to `CLAUDE_ACTIVE` or `CODEX_ACTIVE` and pushing the ops branch.
  - Claims expire 12 h after `UPDATED` unless renewed. An expired task returns to READY, and the other agent may take it with a one-line checkpoint.
  - Never edit files listed in another active task's `FILES_OR_AREAS` without a recorded handoff.
- **WIP limits** (enforced by `fc.py queue`): 1 active build/research per sport and 1 active challenge. Scheduled automation is `WIP_EXEMPT`.
- **States:** READY → CLAUDE_ACTIVE | CODEX_ACTIVE → READY_FOR_CHALLENGE → (PASS) READY_FOR_SUPERCHAD → DONE.
  - BLOCK → MINIMUM_REPAIR → READY_FOR_CHALLENGE (delta-only).
  - BLOCKED (external), from any state.

## 3. Acceptance criteria before build
A consequential task gets `ACCEPTANCE_CRITERIA` in its capsule **before** coding. The challenger may add to them before the build starts; after that they are frozen except by SUPERCHAD.

| Kind | Must state |
|---|---|
| Predictive/model | Target population; exact comparison (champion vs challenger, same code/data identity); same legitimate opportunity; metrics and decision threshold; expected evidence artifact; leakage constraints (point-in-time, no outcome contact, no invented prices); stop condition |
| Engineering | Exact behaviour; failure modes (fail-closed?); required tests; authority boundary |

Trivial tasks (typo, one-line fix, formatting) need none.

## 4. Challenge and blind-audit boundaries
- The challenger orients with `fc.py challenge-capsule TASK_ID`. It contains only objective fields: IDs, SHAs, branch, objective, acceptance criteria, areas, objective evidence pointers and authority.
- It **excludes** the builder's `## BUILDER_NOTES`, the log, blockers, next actions and any `AUDITS/` file.
- **Shared freely (objective):** SHAs, file locations, commands, test locations, repo and CI state, data-source identity, run IDs, hashes.
- **Never preloaded to a challenger:** the builder's conclusions, model judgments, audit verdicts, promotion opinions, or hypotheses presented as facts.
  - Builders keep their reasoning and claimed results in `BUILDER_NOTES`.
  - `EVIDENCE_POINTERS` list raw artifacts (code, data, test output), never the builder's prose report.
- The challenger writes `AUDITS/<TASK_ID>/<agent>.md` with verdict PASS or BLOCK and numbered findings. The builder does not read another agent's audit of the same task until it is posted.
- SUPERCHAD adjudicates disputes from both files.

## 5. Minimum repair and delta-only re-audit
- After a BLOCK, repair **only** the numbered findings. Do not refactor or widen scope.
- The re-audit covers `git diff <audited_sha>..<new_sha>` plus the findings. It does not reopen passed areas.
- Reopening a PASSED area needs a stated trigger: the diff touches it, a new evidence class appears, or SUPERCHAD orders it.
- Only these block completion: broken correctness, scientific-isolation failure, unsafe authority behaviour, or a failing required test. Cosmetic issues become follow-ups.

## 6. Issue #91 = checkpoint channel
Default status message (`fc.py checkpoint TASK_ID` prints it):
```
CHECKPOINT
TASK=FC-MLB-###  HEAD=<sha>  STATUS=<state>
TESTS=<n passed / n failed | n/a>  BLOCKERS=<count>
EVIDENCE=<repo path or URL>
NEXT=<action>  AUTHORITY=<none|Jacob:merge|...>
Alligator
```
- Long evidence belongs in repo artifacts, linked by path.
- Jacob authorization comments, V3 seal receipts and prereg anchors keep their existing exact formats.
- `CLAIM` / `RELEASE` are one-line checkpoints. The older bridge labels remain valid for exceptional messages (CONFLICT ALERT, disputes).

## 7. Efficiency rules
- **Batching:**
  - Put independent checks (git state, file greps, test runs, API reads) in **one** shell call or in parallel tool calls in one turn.
  - Make a separate turn only when the next command depends on reading the previous result.
  - Trim output at the source: `-q`, `| tail -n 30`, `--jq`, `minimal_output`. Every 1 KB kept in context is re-read on every later call.
- **Polling:**
  - Never poll PRs, CI, #91, branches or evidence refs with model turns. Run `fc.py status` (deterministic; prints only changes).
  - To wait, run `fc.py status --until-change 300 --max-hours 6` as a background shell job; the session wakes only when something changed.
  - Prefer `subscribe_pr_activity` events where available.
- **Subagents:**
  - Never use one for simple search, reading a few files, one-file edits, routine tests, log summarisation, formatting or git state.
  - Use one only for independent parallel research, audit isolation, a large exploration whose ≤2k-token result saves main-context cost, or a genuinely different specialisation.
  - State a budget and a return format.
  - Prefer parallel deterministic tool calls.
- **Session ceiling:**
  - At about 150–200k effective context, prefer rotating:
    1. update the task capsule and CURRENT_STATE;
    2. push the ops branch;
    3. post a checkpoint;
    4. start fresh from `fc.py orient TASK_ID`.
  - Stay only while an active debug loop's accumulated state *is* the work and fewer than about 10 calls remain.
  - New sessions recover from state artifacts, never from transcripts.
- **Model routing:**
  - **Strongest model:** predictive architecture, statistics, scientific method, adversarial review, complex debugging, consequential design.
  - **Cheaper model or a script:** formatting, extraction, status, inventories, test-log summaries, mechanical transforms, repo checks.
  - Never trade scientific quality for tokens.
- **Measurement:** `engineering/ops/bin/fc_usage.sh` (ccusage 20.0.26, offline, read-only).

## 8. State upkeep
- After meaningful work, update in the ops branch:
  - the task's `WORK_QUEUE.json` entry (STATUS, HEAD, NEXT_ACTION, UPDATED);
  - its capsule `LOG` (≤ 3 lines per session);
  - `CURRENT_STATE.md` if the project-level state changed.
- Run `fc.py queue` (it must exit 0), then push.
- `ENGINEERING_HANDOFF.md` is frozen history; do not append.
- One task per capsule. Keep capsules short; put detail in repo artifacts and link them.

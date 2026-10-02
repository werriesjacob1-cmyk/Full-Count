# FULL COUNT — SuperClaude MAX efficiency investigation

**Status:** recommendations only. Nothing was installed, configured, connected or activated.
- No changes to settings, hooks, MCP servers, plugins, CLAUDE.md, AGENTS.md, project memory or scientific code.
- **Author:** Claude Code. **Date:** 2026-10-02. **Pending:** SUPERCHAD/Jacob review.

**How to read this.** Every number is marked as one of:
- **[M] measured** from local transcripts, the repo or GitHub on 2026-10-02;
- **[E] estimated**, with the reasoning shown;
- **[U] upstream claim**, not verified here.

Measurement script: `engineering/efficiency/tools/transcript_usage.py`. It reads local transcripts and sends nothing anywhere.

---

## 1. Executive findings

1. **The dominant cost is re-reading context, not generating work.**
   - The main FULL COUNT Claude session (2026-09-10 → 10-02) made **5,628 model calls** and processed **2.36 B input tokens**. **98.7%** were cache reads; **output was 4.65 M (0.2%)**. [M]
   - With 101 subagents (**688 M** tokens), the total is about **3.05 B tokens processed to generate about 4.9 M** — roughly **620:1**. [M]
   - At list-price weights (cache read 0.1×, 1-hour cache write 2×, output 5× input), about **75% of the main session's cost is context re-reading**, about 17% cache writes and about 7.5% output. [E]
2. **Context size per call is the single biggest lever.**
   - Median context per main-session call was **415k tokens** (p90 713k). It rode the 1M window up to about **783k** before each of **15 compactions**, which dropped it to 65–92k. [M]
   - Every tool round trip therefore cost about 40k input-equivalent tokens, the price of writing about 8k output tokens. [E]
   - Capping working context at about 150–200k, by work-item-scoped sessions or an earlier compaction point, cuts per-call cost about **3×** with no change to the work. [E]
3. **Round trips are the multiplier.**
   - 54% of main-session context tokens were spent on calls that issued a single Bash command (3,055 calls). [M]
   - Batching, scripts and parallel tool calls in one message reduce call count directly.
4. **PR and CI babysitting inside the fat session cost 13.7%** of main-session tokens: 760 polling calls (PR reads, issue reads, notifications, Actions lists, job logs, wakeups), about **325 M tokens**. [M]
   - This should be deterministic scripts or event digests run in a tiny context, not an Opus/Sonnet session carrying 400k of history.
5. **Orientation and handoff are prose-based and unbounded.**
   - `ENGINEERING_HANDOFF.md` is **6,738 lines, 406 KB (~100k tokens)**, append-only. The main session read it 24×; subagents read it 15×. [M]
   - Issue #91 has **424 comments, ~318k tokens** of narrative. [M]
   - Subagents re-read the same orientation set over and over: AGENT_BRIDGE_PROTOCOL 34×, AGENTS.md 33×, PROJECT_STATE 25×. [M]
   - Each subagent starts at a **~45k-token fixed baseline** (platform prompt and tools) before any work. [M]
6. **The always-loaded root context is NOT the problem.** CLAUDE.md is 1.5 KB and AGENTS.md is 2.2 KB (~1k tokens together). [M]
   - The waste is that CLAUDE.md *instructs* every significant task to read six sources, including #91 history, which turns cheap root context into an expensive orientation ritual.
7. **"SuperClaude" is not installed.**
   - There is no SuperClaude framework, no `/sc:` commands, no plugin and no pip package in this environment. [M]
   - In this project, SuperClaude and SUPERCHAD are **roles** (mission docs, the ChatGPT control plane).
   - Installing the SuperClaude Framework (v4.3: 30 commands, 20 agents, 7 modes, 8 optional MCPs) [U] would *add* persona and instruction surface. **Do not install it.**
8. **No named tool attacks the measured top-3 sinks** (context size, round trips, fat-context polling). Those are fixed by operating discipline plus three small deterministic scripts.
   - Tools are second-order. The two worth benchmarking are symbol/graph code navigation (Serena or GitNexus) and usage instrumentation (ccusage / token-xray).
   - Auto-memory and auto-skill tools (agent-memory hooks, AutoHarness) are **rejected in auto mode**: they inject one agent's distilled conclusions into every future session, which breaks audit independence.
9. **The biggest non-token waste is serial audit rounds.**
   - MLB V3 took about six audit/repair rounds: v1, v2, v3, repair, final-delta, activation.
   - Several blockers would have been caught by an **acceptance checklist written by the auditor before the build**.
   - Front-loading the challenger's checklist and auditing deltas only should cut rounds by about half. [E]
10. **Recommendation: Architecture B, "Balanced Max"** (§18).
    - It has no new always-on tools, a hard context ceiling, a durable in-repo state and queue on a dedicated ops branch, compact checkpoint schemas, two-lane Claude/Codex pipelining, deterministic monitors, and model routing.
    - One code-navigation tool is trialled only behind the benchmark gate.
    - Expected: **55–75% fewer tokens per validated task** and **1.5–2.5× validated throughput** [E], confirmed or refuted by the §19 benchmark before anything is adopted.

---

## 2. Current efficiency bottlenecks (non-token)

| # | Bottleneck | Evidence | Effect |
|---|---|---|---|
| 1 | Serial build → audit → repair → re-audit loops with late-discovered acceptance criteria | MLB V3: 6 rounds over ~30 h; each round re-states whole-system context | Wall-clock; idle agents |
| 2 | One mega "conductor" session doing build, merge, monitoring, bridge posting and scheduling | 22-day session, 537 user turns, 15 compactions [M] | Everything is slow and expensive; compaction loses detail and forces re-reads |
| 3 | Narrative status as the inter-agent interface | #91: 137 CLAUDE STATUS, 82 AGENT CLAIM, 40 REVIEW RESPONSE; median 2.4 KB, p90 5.6 KB, max 15.5 KB [M] | Each reader must parse prose to recover ~10 facts |
| 4 | Integration overhead from branch and PR sprawl | 253 remote branches; multi-day convergence sprints over ~44 open PRs (earlier sessions) [M] | Days spent reconciling instead of improving models |
| 5 | Polling instead of events | 760 polling calls [M] | Session busy waiting, not working |
| 6 | Re-audit of already-passed areas | Audits restate full-system verdict tables each round | Reviewer tokens and rounds |
| 7 | Stopping early on reversible work, or stopping to ask | Repeated "STOP after X" plus re-prompting cost per turn | Idle time between Jacob/SUPERCHAD messages |
| 8 | Platform friction discovered late | Authorization-footer blocker (Oct 2); fresh-session repo access (NFL W4) | Hours lost; extra rounds |

---

## 3. Token-use bottlenecks (quantified)

### 3.1 Main session (2026-09-10 → 2026-10-02) [M]
| Metric | Value |
|---|---|
| Model calls | 5,628 (Sonnet-5 3,725; Opus-5.5 1,653; Opus-5 179) |
| Input processed | 2.36 B (cache read 2.33 B, cache write 26.5 M, uncached 11k) |
| Output | 4.65 M |
| Context per call | median 415k, p90 713k, max 784k |
| Compactions | 15, each at ~783k → 65–92k |

**Share of context tokens by what the call did:**

| Activity | Calls | Tokens | Share |
|---|---|---|---|
| Bash | 3,055 | 1.27 B | 53.8% |
| Monitoring/polling | 760 | 325 M | 13.7% |
| Text-only replies | 508 | 211 M | 8.9% |
| GitHub writes | 350 | 166 M | 7.0% |
| Edit/Write | 338 | 153 M | 6.5% |
| Read/Grep/Glob | 257 | 90 M | 3.8% |
| Task/ToolSearch meta | 208 | 76 M | 3.2% |
| Subagent launches | 71 | 33 M | 1.4% |

**Tool-result volume** (stays in context until compaction):

| Source | Volume |
|---|---|
| Bash | 3.9 MB |
| `issue_read` | 1.6 MB |
| `pull_request_read` | 1.2 MB |
| Read | 1.2 MB |
| `actions_list` | 0.6 MB |
| `get_job_logs` | 0.6 MB |
| Notifications | 0.56 MB |

**Repeat reads:**
- 224 Read calls on 105 unique files; 119 were repeats.
- A workflow YAML was read 25×; the handoff 24×.

### 3.2 Subagents (101 agents) [M]
- **Volume:** 5,026 calls; 688 M tokens. Median per subagent 5.0 M; p90 13.5 M; max 36.6 M.
- **First-call baseline:** median **44.9k** tokens (platform prompt plus tool schemas), before any task content.
- **Orientation re-reads:**
  - bridge protocol 34×, AGENTS.md 33×, PROJECT_STATE 25×, handoff 15×, AUDIT README 11×;
  - 230 repeat reads overall;
  - orientation plus #91 reads carried through later calls ≈ **6.6%** of subagent tokens.
- **Models:** Sonnet-5 3,975 calls; Opus-5.5 895; Haiku 51.

### 3.3 The three numbers every agent should know [E]
1. **One tool round trip at 400k context costs about as much as writing 8k tokens.** Fewer, bigger, batched calls win.
2. **Every 1 KB of tool output kept in context costs about 44k cache-read tokens** before the next compaction, because the median result is carried about 175 later calls (350 calls per compaction cycle ÷ 2). Suppress test, CI and JSON noise at the source.
3. **A fresh session (≈50k with capsule) pays for itself after about 3 calls** when the current context is 400k:
   - savings per call = (400k − 50k) × 0.1 = 35k input-equivalent;
   - restart cost = 50k × 2 (1-hour cache write) = 100k.

   At 150k context break-even is about 10 calls. **Rule: restart or compact when context exceeds about 150–200k and more than about 10 calls of work remain.**

### 3.4 Not measured
- Codex usage. There is no access to Codex logs from here; instrument it with `ccusage` on the Codex host (§19).
- ChatGPT/SUPERCHAD usage.

---

## 4. Current SuperClaude inventory: what exists today [M]

| Layer | Present |
|---|---|
| SuperClaude Framework (`/sc:*`, personas, modes) | **Not installed.** No `~/.claude/commands`, no pip `superclaude`, no plugin |
| "SuperClaude" in repo | Mission/persona label only (e.g. `engineering/SUPERCLAUDE_POST_INTERRUPTION_NFL_GENIUS_MISSION_2026-09-18.md`). SUPERCHAD = ChatGPT control plane |
| Root instructions | `CLAUDE.md` 1.5 KB; `AGENTS.md` 2.2 KB; no nested CLAUDE.md or AGENTS.md; no `.claude/rules/` |
| Orientation docs | `PROJECT_STATE.md` 23 KB (~6k tokens); `ENGINEERING_HANDOFF.md` 406 KB (~100k tokens); `AGENT_BRIDGE_PROTOCOL.md` 6.6 KB; `AUDIT/README.md` 2.3 KB |
| Hooks | User-level Stop hook (`stop-hook-git-check.sh`, platform-provided); repo `.claude/worktree-autosave.sh` (autosave of agent worktrees) |
| Skills | Account-synced general skills (docx, xlsx, pdf, pptx, docs, morning, trading-desk, health-fitness, mlb-betting-analyst, google-workspace, skill-creator, import-memory) plus built-ins. None are FULL COUNT engineering skills |
| MCP servers in this session | github (56 tools), Claude_Code_Remote (24), Claude_Docs, Google_Drive, Cloudflare ×2 (unauthenticated). Mostly **deferred** (loaded on search), so schema cost is limited, but tool lists still appear in reminders |
| Auto memory | Claude Code auto memory is **machine-local and not shared across cloud environments** [U, docs]. It is useless for our ephemeral cloud containers |
| Scheduling | CCR Routines (persistent-session and send_later); runner sessions (NFL W4, MLB V3) |

---

## 5. Tool-by-tool investigation

Scale used below: **+++** strong, **++** good, **+** minor, **0** none, **−** negative.

### 5.1 SuperClaude Framework v4.3 (SuperClaude-Org)
- **What it is:** 30 `/sc:` commands, 20 agents, 7 behavioural modes, 8 optional MCPs (Tavily, Context7, Serena, Playwright, Magic, Morphllm, Chrome DevTools, Sequential). Installed with `pipx install superclaude && superclaude install` into `~/.claude`. Claims "30–50% fewer tokens" in its token-efficiency mode. [U]
- **Bottleneck removed:** none of ours. It adds instruction surface and personas; our root context is already about 1k tokens.
- **Audit independence:** neutral. **Codex:** no support documented.
- **Failure mode:** generic workflows overriding FULL COUNT doctrine; command sprawl.
- **Verdict: REJECT.** Its useful sub-pieces (Serena, a terse output mode) can be evaluated individually.

### 5.2 AutoHarness (tigerless-labs/autoharness)
- **What it is:** a Claude Code plugin with Python hooks and an MCP server (`stage_skill`). It captures tool calls, reflects on episodes, auto-promotes and merges skills, and injects a skill index at session start. MIT, about 7.2k stars, no third-party Python dependencies, Claude-only. [U]
- **Bottleneck removed:** in principle, repeated procedures.
- **Risk:**
  - It **auto-generates doctrine from small episode windows** and injects it into every session.
  - Skills distilled from Claude's sessions would shape later Claude audits, and its errors would propagate silently.
  - Skill merges are irreversible (snapshots mitigate). [U]
- **Verdict: REJECT for now.** Write the 4–6 FULL COUNT procedural skills by hand (§9). Revisit only for mechanical ops procedures in a sandbox, with human review.

### 5.3 agent-memory (tigerless-labs/agent-memory)
- **What it is:** Markdown files as the source of truth, an SQLite FTS5/BM25 index and optional vectors. MCP tools `memory_recall/read/record/correct/supersede/...`. Hooks: SessionStart injects `MEMORY.md`, Stop/SessionEnd distils, PreCompact evicts. A **shared store across Claude Code, Codex CLI and Hermes**. MIT, about 2.3k stars, Python 3.12, `uv`. [U]
- **The good ideas:** markdown as truth, pointers instead of paste, explicit supersede/correct.
- **The bad fit:**
  1. Automatic distil-and-inject shares conclusions across agents, which **contaminates blind audits**.
  2. The local store at `~/agent-memory-store` dies with each ephemeral cloud container unless it is git-backed.
  3. Codex runs in a different environment (ChatGPT/Codex cloud) and cannot read a Claude container's local store.
- **Verdict: REJECT the tool; adopt the pattern in-repo** (§8), with namespaces and no auto-injection.

### 5.4 GitNexus
- **What it is:** a Tree-sitter AST knowledge graph (calls, imports, inheritance, execution flow), queried through an MCP server ("what depends on X" in one query). 13 languages, local, `npx gitnexus analyze`. Works with Claude Code, Codex and Cursor. [U] License and maintenance status need checking before any trial.
- **Bottleneck removed:** "find all files affected by an NFL change" and grep chains. Our 125k LOC of Python in 439 files [M] is large enough to benefit.
- **Costs:**
  - a Node toolchain;
  - an index rebuild per ephemeral container (or a committed index that goes stale);
  - MCP tool schemas;
  - the risk of trusting an incomplete graph: dynamic Python, `importlib` and string-built paths are common in our pipelines.
- **Verdict: BENCHMARK** against Serena and against a deterministic generated `REPO_MAP` (§6). On-demand only.

### 5.5 Context7 (Upstash)
- **What it is:** an MCP server (`resolve-library-id`, `query-docs`) and CLI. Queries go to a private Upstash backend. [U]
- **Fit:**
  - Our dependency surface is small and **pinned** (pandas 3.0, numpy 2.4, sklearn 1.9, statsapi, pybaseball, nflreadpy).
  - Errors come from data semantics, not API hallucination.
  - Every query leaks project intent to a third party.
- **Verdict: REJECT always-on; optional on-demand** for a new-library integration task. Better: pin a short `engineering/efficiency/DEPS_NOTES.md` per dependency quirk.

### 5.6 Ralph / Ralph Wiggum loop (anthropics/claude-code plugin)
- **What it is:** a Stop hook blocks exit and re-feeds the same prompt in the **same session** until an exact `<promise>` string appears or `--max-iterations` is reached. [U]
- **Token profile:** the worst kind for us. Each iteration re-reads the ever-growing context (§3.3).
- **Science risk:** "keep going until the promise" pressures toward making checks pass, which is unacceptable near evidence, preregs or tests guarding scientific invariants.
- **Verdict: REJECT the plugin. ADOPT a "fresh-session loop" pattern** for mechanical tasks with hard verifiers (CI green, lint, formatting):
  - each iteration is a **new** small session seeded with a ≤3k capsule;
  - a hard budget;
  - excluded paths (`research/**prereg*`, evidence refs, frozen pins, test assertions).

### 5.7 Cost/token instrumentation
- **Tools:** `claude-code-token-xray` (local, Apache-2.0, reads `~/.claude/projects/*.jsonl`; its own finding: "you pay to re-read", 150× [U]) and **ccusage** (local; reads Claude Code **and Codex CLI** logs; daily, session and JSON). [U]
- **Bottleneck removed:** we currently fly blind on Codex and only learned §3 by hand-parsing.
- **Verdict: ADOPT, as a read-only CLI that never enters agent context.** Our own `transcript_usage.py` already covers Claude. Use `npx ccusage@<pinned>` on the Codex host for comparability.

### 5.8 Claude Code native features (official docs [U])
| Feature | What it does | Use |
|---|---|---|
| Nested CLAUDE.md in subdirectories | Load **on demand** when files there are read | `research/mlb_*`, `nfl/`, `engineering/` scoped doctrine |
| `.claude/rules/*.md` with `paths:` frontmatter | Load only when matching files are touched | Evidence-ref rules, prereg rules, CI rules |
| `@imports` | Do **not** save tokens (they load at launch) | Avoid for big docs |
| HTML comments in CLAUDE.md | Stripped before injection | Maintainer notes cost 0 tokens |
| Skills | Only name and description load until invoked | Orientation, audit capsule, handoff, CI triage |
| Subagents | Own context; can pin `model:`; can skip project instructions | Cheap Explore/Haiku for search fan-out |
| Auto memory | First 200 lines / 25 KB of MEMORY.md; **machine-local** | Not useful in our cloud containers |

**Verdict:** use nested CLAUDE.md, path-scoped rules and project skills; do not use auto memory.

### 5.9 Codex-native equivalents (docs [U])
- Codex concatenates AGENTS.md from the git root down to the **cwd**, at most one file per directory, with `AGENTS.override.md` precedence. The cap is **32 KiB** (`project_doc_max_bytes`), and the chain is rebuilt every run.
- **Implication:** nested `AGENTS.md` in `nfl/` and `research/` gives Codex scoped doctrine *only when its cwd is there*. Keep root AGENTS.md ≤ 2 KB. Codex reads the same in-repo `ops/` state as Claude (§8).

---

## 6. Other tools and methods discovered

| Method | Bottleneck | Verdict |
|---|---|---|
| **Serena** (MCP, LSP symbol-level find/read/edit; Claude Code and Codex; MIT) [U] | Whole-file reads; grep chains. Reported break-even above ~20k LOC [U]; we have 125k | **Benchmark** vs GitNexus vs REPO_MAP; on-demand only |
| **Deterministic `REPO_MAP.md` generator** (ast-based: module → public symbols → imports, ≤ 25 KB, regenerated by script, committed to the ops branch) | First-hop navigation for both agents with zero runtime | **Adopt** (cheap, auditable, Codex-compatible) |
| **`fc-status` digest script** (PR/CI/#91 deltas since last checkpoint as ≤ 40 lines of JSON) | 13.7% polling cost | **Adopt** |
| **`fc-test` wrapper** (runs the scoped pytest set and prints only counts plus failing test ids with first error lines) | Test and log noise carried in context | **Adopt** |
| **`fc-ci-triage`** (fetches failed job logs and greps failure signatures to ≤ 30 lines) | `get_job_logs` 0.6 MB | **Adopt** |
| Event-driven monitoring (`subscribe_pr_activity`, Routines into a *small* runner session, GitHub Actions summaries) | Polling | **Adopt** |
| Codex `exec`/cloud tasks from a written capsule (non-interactive audits) | Audit orientation | **Adopt** (owned by Codex-side config) |
| Vector or semantic memory servers (Mem0, Letta, vector DBs) | — | **Reject** (overkill for a few hundred notes; BM25/rg suffices) |
| Multi-agent frameworks (CrewAI, AutoGen, LangGraph swarms) | — | **Reject** (duplicates what CCR Routines plus a repo queue do; adds a runtime) |

---

## 7. Claude/Codex parallel operating model

### 7.1 Principles
1. **Two lanes per agent, always.** Each agent holds at most 1 BUILD item and 1 AUDIT item (WIP limit 2+1). While waiting on an audit, it pulls the next READY item. Nobody waits.
2. **The challenger writes acceptance criteria first.**
   - Before a build starts, the challenger posts an `ACCEPTANCE` file: falsifiable checks, required tests and adversarial cases.
   - The builder builds to it.
   - The audit checks the acceptance list plus a bounded "anything else critical" sweep.
   - This turns most audit rounds from discovery into verification.
3. **Delta audits by default.**
   - A PASS records `{area, pass_sha, scope_paths, checklist_hash}` in the audit ledger.
   - A later audit covers only `git diff pass_sha..head -- scope_paths` plus the checks the change could plausibly affect.
   - Reopening a PASSED area requires a stated trigger: a diff touching it, a new evidence class, or a SUPERCHAD order.
4. **Blind where it matters.**
   - Scientific audits (preregs, evidence, promotion inputs) get the **audit capsule only**: exact SHA range, the claims as checkable statements, acceptance criteria, and paths.
   - They do not get the builder's narrative, reasoning, or self-assessment.
   - Mechanical audits (CI fixes, refactors with tests) may see everything.
5. **No self-approval; no conclusion leakage.** An agent never reads the other's in-progress audit file for the same item. SUPERCHAD adjudicates disagreements from both files.

### 7.2 Work-item state machine
```
PROPOSED -(SUPERCHAD/Jacob prioritises)-> READY
READY -(owner claims; paths locked)-> CLAIMED
CLAIMED -(challenger posts ACCEPTANCE; may run in parallel with early build)-> BUILDING
BUILDING -(head_sha, tests)-> BUILT
BUILT -(audit capsule)-> AUDITING
AUDITING -> PASS | BLOCK(findings[])
BLOCK -(minimum repair)-> BUILT -> DELTA_AUDIT -> PASS | BLOCK
PASS -> DONE                (no merge/activation needed)
PASS -> PENDING_JACOB       (merge/deploy/activation/promotion) -(Jacob exact-SHA approval)-> MERGED/ACTIVATED
any  -> DISPUTED -(SUPERCHAD adjudication)-> previous state
any  -> ABANDONED (reason)
```
**Rules:**
- Claims carry a **TTL of 12 h** and are renewed by any commit or checkpoint. On expiry the item returns to READY with the last capsule; this is the usage-exhaustion recovery.
- Items lock **paths**, not whole areas. Overlap needs a `CONFLICT` entry before editing (the existing bridge rule, made machine-checkable).

### 7.3 Lane assignment (default; SUPERCHAD may swap)
| Lane | Claude | Codex |
|---|---|---|
| Build | Model/challenger implementation, research pipelines, evidence tooling | Ingestion contracts, source provenance, CI gates, fail-closed checks |
| Audit/challenge | Statistical interpretation, adversarial product reasoning | Exact-SHA audits, repository truth, independent replication |
| Research rival | Hypothesis B for the same question, run independently | Hypothesis A |

Sport split by queue weight: MLB 60 / NFL 40, with NFL ≥ 60 inside T−48 h of an NFL lock (a queue rule, not a conversation).

### 7.4 Avoiding the "both read the same 50 files" problem
- The **builder** writes `ops/items/<id>/MAP.md` (≤ 1 page): files touched, entry points, invariants, commands.
- That is objective navigation, not a judgement. The auditor may use it for navigation but must verify claims from code.
- `REPO_MAP.md` and `ops/facts/` give both agents first-hop navigation without re-exploration.
- Research fan-out (for example "find every consumer of the board schema") runs once, as a cheap Explore/Haiku subagent. Its output is filed under `ops/facts/` with SHA and paths.

### 7.5 SUPERCHAD checkpoints (≤ 25 lines, fixed schema)
```
CHECKPOINT <date> <agent>
items: <id> <state> <head_sha> <next action> <eta>        # one line per active item
blocked: <id> <blocker> <needs: Jacob|SUPERCHAD|Codex|Claude>
disputes: <id> <claude_file> <codex_file>
spend: tokens_24h=<n> validated_items_24h=<n> tokens/validated=<n>
risks: <one line each>
links: <ops branch sha>
Alligator
```
Narrative detail lives in the item folder; the checkpoint points to it.

---

## 8. Shared memory design

**Store:** a dedicated branch `ops/state` (no CI, no PRs; pushed by both agents; append-mostly; small) in this repository. It is git-backed, so it survives ephemeral containers, is readable by Codex and SUPERCHAD, and is reviewable.

```
ops/
  STATE.md                 # generated, ≤150 lines: SHAs, active items, deadlines, open PRs, regimes
  queue/<id>.yaml          # work items (schema below)
  items/<id>/
    CAPSULE.md             # ≤3k tokens: goal, base/head SHA, paths, commands, acceptance link, status
    ACCEPTANCE.md          # written by the challenger before the build
    MAP.md                 # builder's navigation map (objective)
    audit_<agent>.md       # ISOLATED: never read by the other agent before adjudication
    log.md                 # append-only, terse
  facts/<topic>.md         # objective facts: each has source pointer (path:line@sha | run id | API url) + verified_at_sha
  research/<topic>.md      # notes typed HYPOTHESIS | RESULT(sha, artifact) | NEGATIVE_RESULT(sha, artifact)
  audit_ledger.yaml        # {area, pass_sha, scope_paths, checklist_hash, auditor, date}
  REPO_MAP.md              # generated
```

**Queue item schema (YAML):**

| Field | Contents |
|---|---|
| `id` | Item identifier |
| `sport` | MLB or NFL |
| `kind` | build, research, audit, ops |
| `owner`, `challenger` | Agents |
| `state` | State-machine value (§7.2) |
| `base_sha`, `head_sha` | Exact identities |
| `paths_claimed` | Path locks |
| `acceptance` | Path to ACCEPTANCE.md |
| `budget_tokens`, `budget_hours` | Hard limits |
| `jacob_gate` | none, merge, deploy, activate or promote |
| `claim_expires` | TTL timestamp |
| `evidence` | Pointers |
| `blockers` | Open blockers |

**What may be shared:**
- **Shared (objective):** SHAs, branch and PR state, file locations, commands, data-source identities, schema facts, run ids, hashes, deadlines, negative results with artifacts.
- **Isolated until adjudication:** audit verdicts, scientific interpretations, promotion judgements, untested hypotheses. These live in `items/<id>/audit_<agent>.md` or `research/` typed as HYPOTHESIS. **Nothing is auto-injected.** Retrieval is pull-only (`rg` over `ops/`).
- **Never stored:** outcomes after a prospective boundary, credentials, anything that would let an auditor see the builder's private reasoning before auditing.

**What happens to the existing files:**
- `ENGINEERING_HANDOFF.md` is frozen as history. New entries become per-item `log.md` lines plus a rolling index of ≤ 100 lines.
- `PROJECT_STATE.md` becomes the human-readable twin of the generated `STATE.md`.
- Issue #91 carries only claims, checkpoints, Jacob authorizations and SUPERCHAD orders, each ≤ 25 lines with pointers to the ops branch.

---

## 9. Context architecture

| Layer | Budget | Contents |
|---|---|---|
| Root CLAUDE.md / AGENTS.md (always) | ≤ 1.5k tokens each (today ~0.4k / 0.55k) | Invariants (Jacob gates, no outcome leakage, no self-approval, exact-SHA identity), the ops-branch pointer, "start from `ops/STATE.md` + your item CAPSULE". **Remove** the instruction to read handoff, PROJECT_STATE, the AUDIT README and all of #91 before significant work |
| Nested CLAUDE.md / AGENTS.md (on demand by path) | ≤ 1k each | `research/mlb_accuracy_challenger*/` (V3 doctrine pointers); `nfl/` (NFL invariants); `engineering/` (bridge formats) |
| `.claude/rules/` with `paths:` | ≤ 0.5k each | Evidence refs and seals (append-only, never force); prereg files (immutable); `.github/workflows` (CI conventions) |
| Project skills (name + description only until invoked) | — | `fc-orient` (build a capsule from STATE + item); `fc-audit-capsule` (assemble a blind audit input); `fc-checkpoint` (emit the §7.5 schema); `fc-ci-triage`; `fc-test` |
| Retrieval (pull) | — | `ops/facts`, `ops/research`, `REPO_MAP`, symbol tool if adopted |
| Never injected | — | Other agents' audit conclusions; outcome data; long narratives |

**Platform baseline.** Each call carries about 45k of platform and tool schema [M]. FULL COUNT sessions should run with the **minimum connector set**: github and Claude_Code_Remote. Google Drive, Claude Docs and Cloudflare should be off unless a task needs them. Measure the delta in the benchmark; deferred loading may already make it small.

---

## 10. Session strategy

1. **One work item per session.** A session starts from CAPSULE plus STATE (≈ 50–60k total including platform) and ends by updating CAPSULE/log, pushing, and posting one checkpoint.
2. **Hard context ceiling of about 150–200k.** Above it, write the capsule and restart (break-even ≈ 3 calls at 400k, ≈ 10 at 150k; §3.3). If the platform exposes an earlier auto-compact threshold, set it near 200k (check the current Claude Code setting; do not assume).
3. **No mega-conductor.** Jacob/SUPERCHAD conversations run in a thin "control" session that reads `STATE.md` and dispatches items to work sessions or Routines. It never builds or polls itself.
4. **Long-running jobs** (seals, collection, CI watches) go in **dedicated small runner sessions** (pattern proven by NFL W4 and MLB V3) or as GitHub Actions. They are woken by Routines or events.
5. **When to stay in a long session:** an active debugging loop where the accumulated state is the work product, the context is under 200k, and fewer than about 10 calls remain.

---

## 11. Model-routing strategy

| Work | Model / engine | Why |
|---|---|---|
| Prereg design, statistical inference, scientific audit, adjudication drafts, adversarial review | Strongest (Opus-class / Codex high) | Errors here are expensive and silent |
| Implementation against acceptance tests, refactors, repo surgery | Sonnet-class | 66% of main calls already; adequate quality |
| Search fan-out, file location, CI log triage, checkpoint formatting, monitoring digests | Haiku-class subagent or **deterministic script** | Mechanical |
| Status/PR/CI polling, hash verification, test running, coverage counts | **Scripts, not models** | Zero tokens |

The routing saving is real but **second-order**: context size × calls dominates price per token (§3). A Haiku call in a 400k context still costs more than an Opus call in a 40k context.

**Subagent rule:**
- Spawn when the task needs more than about 5 exploratory calls **and** returns at most about 2k tokens of conclusions.
- Do not spawn for 1–3 calls, because the ~45k baseline dominates.
- Subagents median **5 M tokens** each [M]; every spawn needs a stated budget and a stated return format.

---

## 12. Research-reuse strategy

- **One retrieval, one note.** Every web or document fetch that informs a decision is filed in `ops/research/<topic>.md` with URL, retrieval date, a ≤ 15-line digest and the decision it informed. Later agents grep before fetching.
- **Negative results are first-class:** `NEGATIVE_RESULT(sha, artifact)` entries with the exact config. This preserves CLAUDE.md's "preserve negative research results" at near-zero cost.
- **Hypotheses are labelled** and cannot be cited as facts. Promotion from HYPOTHESIS to RESULT requires an artifact SHA.
- **Data-source identity cards** live in `ops/facts/sources/*.md`: endpoint, auth, schema hash, point-in-time semantics and known quirks. This stops re-deriving statsapi, FanDuel and nflreadpy behaviour each session.

---

## 13. Autonomous-work strategy

1. **Autonomy classes on each queue item:**

   | Class | Scope |
   |---|---|
   | `AUTO_REVERSIBLE` | Tests, CI, refactor with green suite, ingestion behind flags: continue until acceptance passes or budget is hit |
   | `AUTO_RESEARCH` | Historical backtests on frozen data, no prospective or outcome contact: continue within budget |
   | `GATED` | Prereg, evidence, activation, merge, deploy, promotion: stop at the gate |

2. **Fresh-session loop** (replaces Ralph):
   - a Routine fires a new session with the CAPSULE;
   - each iteration ends by updating the CAPSULE;
   - limits: max iterations, max tokens, max wall-clock.
3. **Continuation contract.** For `AUTO_*` items Claude does not stop to report partial progress. It stops only at acceptance, budget, a GATED boundary or a genuine blocker. This addresses "Claude stopping too early".
4. **Never automated:** merge, deploy, activation, promotion, prereg change, authorization posting, evidence rewrite, outcome reads after a boundary.

---

## 14. Token-saving methods ranked by expected impact [E]

| Rank | Method | Expected saving | Basis |
|---|---|---|---|
| 1 | Context ceiling ~150–200k (work-item sessions / earlier compaction) | **50–65%** of session tokens | Mean context 419k → ~130–150k at the same call count |
| 2 | Move polling to scripts/events in tiny contexts | **10–13%** | 325 M of 2.36 B measured |
| 3 | Fewer round trips (batch commands, scripts, parallel calls) | **15–30%** of remaining | 3,055 single-Bash calls |
| 4 | Suppress tool output at source (`fc-test`, `fc-ci-triage`, `--jq`/`minimal_output`) | **5–10%** | 1 KB ≈ 44k carried tokens |
| 5 | Capsule orientation instead of reading 6 sources | **5–8%** (more after rank 1) | 6.6% measured in subagents; 24 handoff reads in main |
| 6 | Delta audits + acceptance-first | **30–50% of audit tokens**; fewer rounds | V3 rounds |
| 7 | Subagent spawn rule and budgets | **5–10%** of subagent tokens | Median 5 M per subagent |
| 8 | Model routing for mechanical work | Price −; tokens ≈ | Second-order |
| 9 | Compact checkpoint schema on #91 | **2–4%** | 318k-token channel |
| 10 | Minimum connector set | **≤ 5%** | Deferred schemas; to be measured |

These compound, but not linearly. Combined realistic target: **55–75% fewer tokens per validated task.**

## 15. Speed-increasing methods ranked by expected impact [E]
1. **Two-lane pipelining with WIP limits.** Neither agent idles while the other audits: about 1.5–2× throughput.
2. **Acceptance-first plus delta audits:** audit rounds 5–6 → about 2–3 on large items.
3. **Thin control session plus dispatch to work sessions and Routines:** parallel items instead of one serialised conductor.
4. **Deterministic monitors:** no session is blocked babysitting CI.
5. **Item capsules:** fresh-session start in minutes, not a long orientation.
6. **Fewer, larger PRs per item, plus branch hygiene:** cuts convergence-sprint days. Retire stale branches by policy after Jacob approves a list; deletion stays manual per standing instruction.
7. **Code-navigation tool** (if the benchmark proves it): faster impact analysis on NFL changes.

---

## 16. Risks and failure modes

| Risk | Mitigation |
|---|---|
| Capsules drift from truth (stale SHAs) | `STATE.md` is **generated** from git/GitHub, never hand-written. Capsules carry `as_of_sha`; the skill refuses a capsule whose SHA is not an ancestor of the current head |
| Shared facts smuggle in conclusions | Typed entries; facts require a source pointer; reviewers reject untyped claims; `audit_*` files are excluded from facts by path |
| Smaller contexts lose subtle state, so more mistakes | Work-item scoping; acceptance tests; the benchmark measures defects introduced |
| Ops branch conflicts between agents | One file per item; append-only logs; `STATE.md` regenerated, never merged by hand |
| Autonomy overreach | Autonomy classes; GATED boundaries enforced by path rules plus existing verifiers; no merge, deploy or activation tools in autonomous prompts |
| Tool supply chain (plugins, MCP) | Nothing new always-on; any trial is pinned by version and hash, in a disposable branch or session, with network egress reviewed |
| Codex blind spots (no telemetry) | ccusage on the Codex host; SUPERCHAD requests Codex token totals per item |
| Over-instrumentation | The benchmark itself must be cheap (§19 caps it) |

---

## 17. Three SuperClaude MAX architectures

### A. MINIMAL LEAN
- **Components:**
  - context ceiling and work-item sessions;
  - root CLAUDE.md slimmed to invariants plus a pointer;
  - `ops/STATE.md` generator plus item CAPSULE;
  - `fc-status`, `fc-test` and `fc-ci-triage` scripts;
  - event-driven monitoring in small runner sessions;
  - the §7.5 checkpoint schema;
  - no queue automation and no new tools.
- **Advantages:** near-zero install risk; captures the top three sinks; reversible in minutes.
- **Disadvantages:** Claude and Codex still coordinate through #91; no WIP or pipeline discipline; manual dispatch.
- **Token profile:** −45–60% per task. **Speed:** +20–40%.
- **Risks:** low; mostly habit drift.

### B. BALANCED MAX (recommended)
- **Components:** everything in A, plus:
  - the `ops/state` branch with queue, items, facts, research and audit ledger;
  - the two-lane Claude/Codex pipeline with acceptance-first and delta audits;
  - nested CLAUDE.md/AGENTS.md and `paths:` rules;
  - 4–6 hand-written project skills;
  - a thin control session;
  - model routing and the subagent spawn rule;
  - a generated `REPO_MAP.md`;
  - instrumentation (`transcript_usage.py` + ccusage);
  - **one** code-navigation tool (Serena *or* GitNexus) trialled on-demand behind the benchmark;
  - fresh-session loops for `AUTO_REVERSIBLE` items.
- **Advantages:** attacks both the token and the throughput bottlenecks; keeps audits blind; durable across containers; Codex-compatible because it is all in the repo.
- **Disadvantages:** one-time setup (≈ 2–3 agent-days); queue hygiene discipline.
- **Token profile:** −55–75% per validated task. **Speed:** 1.5–2.5× validated items per day.
- **Risks:** moderate; mainly state drift, mitigated by generated STATE and SHA-checked capsules.

### C. AGGRESSIVE MAX
- **Components:** everything in B, plus:
  - GitNexus **and** Serena always-on;
  - agent-memory shared store with hooks;
  - AutoHarness auto-skills;
  - Ralph-style loops;
  - 3–5 parallel worktree subagents per agent;
  - SuperClaude framework commands.
- **Advantages:** maximum parallel activity; maximum automation.
- **Disadvantages:** higher baseline per call (more schemas and injected memory); auto-shared conclusions break blind audits; more supply-chain surface; harder recovery when an injected "fact" is wrong.
- **Token profile:** likely **+20–100% total burn**, with uncertain per-task efficiency. **Speed:** higher raw activity, with a lower validated fraction.
- **Risks:** high, especially audit contamination and silent doctrine drift.

---

## 18. Recommended FULL COUNT architecture: B, Balanced Max

**Why B:**
- the measured waste is structural (context size, round trips, polling, prose interfaces, serial audits);
- B fixes each with deterministic, git-backed and Codex-readable mechanisms;
- it adds no always-on third-party surface;
- it keeps the scientific guarantees intact.

A captures most token savings but not the throughput gains. C spends tokens to buy activity and puts audit independence at risk.

**Target operating picture (steady state):**
1. **Control session (thin).** Jacob/SUPERCHAD dialogue; reads STATE; dispatches; posts checkpoints. No building, no polling.
2. **Claude work sessions, one per item.** At most 1 build and 1 audit active; start from a capsule; ceiling 200k; end with a pushed capsule, log and checkpoint line.
3. **Codex sessions.** The same queue, reading the same ops branch; blind audit capsules for scientific items.
4. **Runner sessions and Actions:** seals, V3 collection, CI digests, `fc-status` into STATE.
5. **SUPERCHAD:** consumes ≤ 25-line checkpoints and the queue; adjudicates DISPUTED items from two isolated audit files.
6. **Jacob:** sees PENDING_JACOB items with exact SHAs; nothing else needs him.

---

## 19. Before/after benchmark plan

**Run it before adopting anything beyond scripts** (the scripts are needed to measure).

### Tasks (fixed prompts, fixed base SHA, run in fresh sessions)
| Id | Task | Validation |
|---|---|---|
| A | Fresh-session orientation: "state the current MLB V3 and NFL W4 status with SHAs and next actions" | Checklist of 12 facts |
| B | Trace one MLB prediction feature end-to-end (e.g. pitcher-K projection inputs → board field) | Path list vs reference trace |
| C | List all files affected by an NFL model change (a given function signature change) | Precision/recall vs `git grep` + import-graph reference |
| D | Narrow code review of a seeded diff with 3 planted defects | Defects caught, false positives |
| E | Run the correct tests for a given change and report | Correct test set, correct counts |
| F | Produce a handoff for a half-done item | A fresh agent resumes using only the handoff (time and tokens) |
| G | Independent audit from an exact SHA with 2 planted scientific defects (e.g. post-cutoff row, wrong coefficient hash) | Defects caught; no contamination (auditor not given builder notes) |
| H | Research a new predictive idea (e.g. NFL target-share regime shift) to a ≤ 1-page plan with citations | SUPERCHAD rubric (novelty, testability, leakage safety) |

### Configurations
- **Baseline (today):** current CLAUDE.md, full connectors, no capsules, a long-session start (resume a large session) for tasks A, B, D.
- **Candidate A**, then **Candidate B.** Then B with a code-navigation tool for tasks B and C only.

### Protocol
- 2 repetitions per task × config. Claude tasks run here; Codex runs the same tasks where applicable.
- The other agent grades blind, using only the validation column.

### Metrics
- **Tokens:** input, output, cache read, cache write.
- **Activity:** tool calls, subagents, files opened, unique files, repeat reads, searches.
- **Time and reliability:** wall-clock, retries, failed tool calls.
- **Outcomes:** completion (pass/fail vs validation), defects caught, defects introduced, human interventions.

### Sources
- `transcript_usage.py` (Claude, per session and per subagent);
- `ccusage --json` (Claude and Codex);
- `git log` for wall-clock.

### Scores
- **PRODUCTIVITY** = validated tasks / wall-clock hour.
- **TOKEN EFFICIENCY** = validated tasks / (input-equivalent tokens), where input-eq = cache_read × 0.1 + cache_write × w + uncached + output × 5 (w = 1.25 for 5-minute TTL, 2 for 1-hour TTL).

### Adoption gate
- Adopt a component only if, with no increase in defects introduced or in contamination incidents:
  - TOKEN EFFICIENCY improves ≥ 25% and PRODUCTIVITY does not drop; **or**
  - PRODUCTIVITY improves ≥ 25% and TOKEN EFFICIENCY does not drop.
- **Benchmark budget:** about 8 tasks × 3–4 configs × 2 reps × ~1–3 M tokens per run in candidates. That is far below one week of current burn (~1 B/week).

---

## 20. Implementation plan in phases (each phase needs Jacob/SUPERCHAD approval)

**Phase 0 — Instrument (½ day).**
- Commit `transcript_usage.py` (done on this branch; read-only).
- Codex host runs ccusage.
- Record the baseline from §3 and a Codex baseline.

**Phase 1 — Lean wins, no tools (1–2 days).**
- Generated `STATE.md`.
- `fc-status`, `fc-test`, `fc-ci-triage`.
- Checkpoint schema.
- Context-ceiling and spawn rules written as a ≤ 1-page `engineering/OPERATING_RULES.md`.
- **Proposed** (not done) slim CLAUDE.md/AGENTS.md diff for Jacob review.
- Thin control-session practice.
- Run the benchmark on A.

**Phase 2 — Queue and two-lane pipeline (2–3 days).**
- `ops/state` branch, item schema, capsule/acceptance/audit files, audit ledger.
- Nested CLAUDE.md/AGENTS.md and `paths:` rules.
- 4–6 skills.
- Codex adopts the same queue.
- First 5 items run through it; benchmark on B.

**Phase 3 — Navigation trial (1 day).** Serena vs GitNexus vs REPO_MAP on tasks B and C. Adopt the winner on-demand only, or none.

**Phase 4 — Bounded autonomy (ongoing).**
- Fresh-session loops for `AUTO_REVERSIBLE` items, with budgets.
- Review weekly on tokens/validated item.

**Rollback.** Every phase is files on branches plus prompt practice, so revert the branch. Nothing touches production, evidence refs or scientific code.

---

## 21. What NOT to install

| Tool | Reason |
|---|---|
| **SuperClaude Framework** | Adds surface; no measured sink addressed; no Codex parity |
| **AutoHarness** | Auto-generated doctrine injected every session; audit contamination; Claude-only |
| **agent-memory with hooks** | Auto-shared conclusions; ephemeral local store; Codex lives elsewhere |
| **Ralph Wiggum plugin** | Same-session re-feed maximises context re-reads; pushes toward "make it pass" near scientific invariants |
| **Context7 always-on** | Pinned, small dependency surface; third-party query egress |
| **Any always-on code-graph MCP** | Before the benchmark proves it |
| **Vector memory DBs / multi-agent frameworks** | Overkill; duplicate Routines and git |
| **More connectors in FULL COUNT sessions** | Each adds baseline context |

---

## 22. Open questions for SUPERCHAD/Jacob

1. Approve **Architecture B** and the **§19 benchmark gate** as the adoption rule?
2. Approve a dedicated **`ops/state` branch** as shared objective memory, with the isolation rules in §8? Should SUPERCHAD write to it directly or only via #91?
3. May CLAUDE.md/AGENTS.md be slimmed (as a reviewed PR) to replace the "read 6 sources incl. all new #91 comments" ritual with "STATE + CAPSULE"?
4. Freeze `ENGINEERING_HANDOFF.md` as history and move to per-item logs plus a rolling index?
5. Should #91 be restricted to claims, checkpoints, authorizations and SUPERCHAD orders (≤ 25 lines), with all narrative in the ops branch?
6. Codex token telemetry: who runs ccusage on the Codex host, and how is it reported?
7. Which acceptance-first template should Codex use as challenger, and should SUPERCHAD own the template?
8. Autonomy classes: confirm which item kinds may be `AUTO_REVERSIBLE` and `AUTO_RESEARCH`.
9. Connector minimisation for FULL COUNT sessions: may Google Drive, Claude Docs and Cloudflare be detached for engineering sessions?
10. Branch hygiene: approve a policy (not an action) for retiring stale branches, given the standing no-deletion instruction?

---

### Sources (retrieved 2026-10-02)
- [tigerless-labs/autoharness](https://github.com/tigerless-labs/autoharness)
- [tigerless-labs/agent-memory](https://github.com/tigerless-labs/agent-memory)
- [GitNexus (MarkTechPost overview)](https://www.marktechpost.com/2026/04/24/meet-gitnexus-an-open-source-mcp-native-knowledge-graph-engine-that-gives-claude-code-and-cursor-full-codebase-structural-awareness/)
- [GitNexus explained](https://hoangyell.com/gitnexus-explained/)
- [upstash/context7](https://github.com/upstash/context7)
- [Ralph Wiggum plugin](https://github.com/anthropics/claude-code/tree/main/plugins/ralph-wiggum)
- [claude-code-token-xray](https://github.com/Coral-Bricks-AI/coral-ai/tree/main/claude-code-token-xray)
- [ccusage](https://github.com/ccusage/ccusage)
- [SuperClaude Framework](https://github.com/SuperClaude-Org/SuperClaude_Framework)
- [Serena guide](https://mcp.directory/blog/serena-mcp-complete-guide-2026)
- [Claude Code memory docs](https://code.claude.com/docs/en/memory)
- [Codex AGENTS.md docs](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

SUPERCLAUDE MAX EFFICIENCY INVESTIGATION COMPLETE — PENDING SUPERCHAD/JACOB REVIEW

Alligator.

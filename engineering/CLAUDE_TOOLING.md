# FULL COUNT Claude Tooling Manifest

Every tool Claude sessions on this project may use, its install status, and
the rules for using it. Update this file whenever a tool's status changes --
this is the single source of truth for "is X installed, and am I allowed to
reach for it." `scripts/claude-bootstrap.sh` reports live status for most
rows below; this file is the policy layer on top of that report.

Status legend: **INSTALLED** (verified present) / **ON-DEMAND** (not
installed by default; install only when the specific task needs it) /
**DEFERRED** (deliberately not installed yet, see project rules).

---

## Serena (MCP)

- MODE: MCP server (stdio), configured in `.mcp.json`
- PURPOSE: symbol-aware code navigation (find callers, trace a value's
  serialization path, list consumers of a function) without reading whole
  files into context
- ALLOWED AGENT: all agents
- WHEN TO USE: a targeted structural question about the codebase that grep
  can't answer precisely (e.g. "every caller of `freezePublishedSnapshot`")
- WHEN NOT TO USE: as a substitute for actually reading a file you're about
  to edit; Serena is a compass, not a replacement for reading the code you
  change
- TOKEN COST: low per query once warmed; avoid re-indexing repeatedly
- CREDENTIALS: none
- PINNED VERSION: 1.7.0 (`uv tool install --from git+https://github.com/oraios/serena serena-agent`)
- INSTALL STATUS: **INSTALLED; benchmark pending fresh session.** `serena
  project health-check` passes against this repo (Pyright LSP up in
  1.55s, 215 files indexed, `FindSymbolTool`/`FindReferencingSymbolsTool`
  both functional). No `mcp__serena__*` tools are attached to THIS
  session's tool surface despite `.mcp.json` declaring it, so the actual
  precision/time/token comparison against grep for real navigation
  questions has not been run and should not be forced here -- verify live
  tool attachment on the next clean session after this branch is merged
  or otherwise available, not by further detour from P0.

## Hypothesis (property-based testing)

- MODE: Python library, project venv
- PURPOSE: property-based tests for invariants that hold across a whole
  input space, not just hand-picked examples
- ALLOWED AGENT: fc-scientist, fc-live-sre (their own domains only)
- WHEN TO USE: lifecycle monotonicity, settlement-authority ranking,
  live-merge field-level ordering, idempotency, candidate identity,
  publication-snapshot immutability -- invariants worth modeling precisely
- WHEN NOT TO USE: trivial modules with no real invariant to state; don't
  force property testing onto everything (see `test_check_live_freshness_hypothesis.py`'s
  own docstring for this exact judgment call already made once)
- TOKEN COST: n/a (runtime tool, not a Claude-token cost)
- CREDENTIALS: none
- PINNED VERSION: 6.165.10 (`requirements-dev.txt`)
- INSTALL STATUS: **INSTALLED**

## Ruff

- MODE: CLI, project venv + global uv tool
- PURPOSE: lint + format
- ALLOWED AGENT: all agents
- WHEN TO USE: before any commit touching Python
- WHEN NOT TO USE: mass-reformatting unrelated files as a side effect of an
  unrelated change (avoid formatting churn per governing instructions)
- TOKEN COST: n/a
- CREDENTIALS: none
- PINNED VERSION: 0.16.4 (`requirements-dev.txt`); 0.15.8 also present as a
  global uv tool -- prefer the project-pinned version for this repo
- INSTALL STATUS: **INSTALLED**

## Type checker: mypy (DECIDED -- pyright rejected on real evidence)

- MODE: CLI, global uv tool
- PURPOSE: static type checking
- ALLOWED AGENT: all agents, on-demand
- WHEN TO USE: after a change to a module with type annotations, to catch a
  class of bug tests may not cover
- WHEN NOT TO USE: as a blocking gate on a codebase that isn't fully
  annotated yet -- report findings, don't force annotation-completeness as
  a side quest
- TOKEN COST: n/a
- CREDENTIALS: none
- PINNED VERSION: mypy 1.19.1
- INSTALL STATUS: **INSTALLED, DECIDED**. Tested both against this
  codebase directly rather than picking by reputation: `pyright
  generate_picks.py` **crashed with an internal stack trace** (a real
  TypeScript-side exception in its reachability analyzer) on the repo's
  single largest and most central file (421KB); `mypy --ignore-missing-imports
  generate_picks.py` completed cleanly in under a minute with no crash,
  and along the way surfaced a real, worth-investigating bug pattern
  (`generate_picks.py:4830-4845`: assigning to an exception variable `e`
  and reading it after the `except:` block ends -- Python 3 deletes that
  binding at block exit, so this is either dead code or a latent
  NameError depending on the exact control flow; not fixed here, flagged
  for a real look, out of scope for a tooling-selection pass and
  secondary to P0). mypy is the project standard going forward. Do not
  mass-annotate the codebase to satisfy it -- gradual typing only, where
  a real change already touches a function worth annotating.

## jq

- MODE: CLI
- PURPOSE: filtered JSON extraction -- read the 3 fields you need from a
  large JSON artifact instead of loading the whole file into context
- ALLOWED AGENT: all agents
- WHEN TO USE: inspecting `docs/data.json`, `docs/live.json`,
  `results/history.json`, hook-schema validation (see the update-config
  skill's own `jq -e` verification step)
- WHEN NOT TO USE: n/a -- essentially free, prefer it over `cat`+eyeballing
  for any JSON file above a few KB
- TOKEN COST: n/a
- CREDENTIALS: none
- PINNED VERSION: 1.7
- INSTALL STATUS: **INSTALLED**

## Context7 (external library/API documentation)

- MODE: CLI + on-demand skill (`npx ctx7 setup --claude`), NOT MCP
- PURPOSE: current, version-specific documentation for EXTERNAL
  libraries/APIs (Cloudflare, Playwright, MLflow, GitHub API, etc.).
  Serena handles this repo's own code; Context7 handles everything
  outside it. Never use it for normal source navigation.
- ALLOWED AGENT: all agents, on-demand only
- WHEN TO USE: "what's the current signature/behavior of external library
  X" -- exactly the kind of check this session did manually via WebFetch
  for Cloudflare's Workers Builds docs and the `@cloudflare/vitest-plugin`
  API this turn; Context7 would be the more direct tool for that once set
  up.
- WHEN NOT TO USE: this repo's own code (Serena's job); do not permanently
  load it as an always-on MCP connector.
- TOKEN COST: **CLI + Skills mode chosen over MCP mode specifically for
  token cost** -- MCP mode would add a permanently-loaded tool schema to
  every session regardless of whether a given task needs it; CLI + Skills
  loads only on demand. (Context7's own docs present both modes as
  equally valid without a token-efficiency recommendation either way --
  this preference is FULL COUNT's own reasoning, not upstream's.)
- CREDENTIALS: OAuth device-auth flow during `npx ctx7 setup`, generates
  an API key afterward (`context7.com/dashboard`)
- PINNED VERSION: n/a
- INSTALL STATUS: **IDENTIFIED, NOT INSTALLED** -- setup requires an
  interactive OAuth flow only Jacob can complete (phone browser is fine:
  the flow is a normal OAuth redirect). Stop-here boundary; not attempted
  this session.

## Playwright CLI (exploratory QA, project-local)

- MODE: CLI via `npx @playwright/cli` (verified resolvable this session,
  v0.1.18 -- confirmed real and working, though still an early version
  number, worth treating as young/less battle-tested than Playwright Test
  itself), NOT the always-loaded browser MCP server
- PURPOSE: exploratory browser QA -- screenshots, mobile viewport checks,
  console-error checks, light/dark mode
- ALLOWED AGENT: fc-ux only
- WHEN TO USE: visual/interaction verification for a UX change, per
  fc-ux.md's own testing-discipline rule
- WHEN NOT TO USE: as a replacement for the deterministic
  `test_browser_e2e.py` suite -- a real behavior change still needs a real
  regression check added there, not just a screenshot
- TOKEN COST: **confirmed lower than the browser MCP server by
  Playwright's own current docs** -- "concise CLI output, skills loaded
  on demand" (CLI) vs. "tool schemas + snapshots in context" (MCP),
  stated directly on `playwright.dev/agent-cli/introduction`. Skills
  install via `<the CLI> install --skills`.
- CREDENTIALS: none
- PINNED VERSION: `@playwright/cli` 0.1.18 (via npx, project-local --
  Playwright's own docs default to a global `npm install -g`, overridden
  here per the governing instruction to prefer project-local/npx)
- INSTALL STATUS: **ON-DEMAND** (verified resolvable via npx; not wired
  always-run path)

## Lighthouse

- MODE: CLI, on-demand
- PURPOSE: performance/accessibility/SEO scoring for the dashboard
- ALLOWED AGENT: fc-ux only
- WHEN TO USE: preparing a UX validation pass, never as a blocking gate
- WHEN NOT TO USE: routine/automatic -- this is a manual, occasional check
- TOKEN COST: n/a
- CREDENTIALS: none
- PINNED VERSION: n/a
- INSTALL STATUS: **DEFERRED** -- not installed; install on-demand the
  first time an fc-ux task actually needs it

## Context7

- MODE: MCP/connector, on-demand
- PURPOSE: current external API/library documentation lookup -- NOT a
  substitute for repo navigation (Serena/grep own that)
- ALLOWED AGENT: any, scoped to the specific external-doc question
- WHEN TO USE: "what's the current signature of library X's function Y"
- WHEN NOT TO USE: never load as an always-on connector; never use it to
  answer a question about THIS repo's own code
- TOKEN COST: query-scoped, avoid loading whole doc trees into context
- CREDENTIALS: `CONTEXT7_API_KEY` -- not currently set (see bootstrap report)
- PINNED VERSION: n/a
- INSTALL STATUS: **DEFERRED** -- not configured; requires credential setup
  Jacob has not yet provided

## n8n-MCP (reclassified 2026-08-26: no longer P0-blocking)

- MODE: MCP, isolated to FC Live SRE
- PURPOSE: future workflow orchestration/automation (notifications, admin
  automations, analytics jobs, operational integrations). **Not required
  for the live-freshness heartbeat** -- that problem is solved by
  `infra/live-heartbeat/` (a Cloudflare Worker), chosen specifically
  because it needs no server Full Count maintains, has a free tier, and
  the job is exactly one authenticated REST call every 5 minutes. n8n
  remains valuable for future, broader automation needs.
- ALLOWED AGENT: fc-live-sre only
- WHEN TO USE: a genuine future automation need where BUILD/INSPECT/VALIDATE
  via n8n-MCP adds real value beyond a single scheduled dispatch. Claude
  is never the production runtime of anything built this way -- zero
  Claude tokens at runtime is the hard requirement regardless of which
  automation tool is used.
- WHEN NOT TO USE: as a Claude Routine or a second GitHub cron; not for
  the live-freshness heartbeat specifically now that the Worker exists
- TOKEN COST: n/a at any automation's runtime by design; MCP calls during
  build/inspect are normal tool-call cost
- CREDENTIALS: n8n API key/instance URL -- not currently set
- PINNED VERSION: n/a
- INSTALL STATUS: **ON-DEMAND / FUTURE AUTOMATION** -- do not install or
  connect now. Revisit only when a concrete future automation task
  actually needs it.

## UI/UX design-critique skill ("UI/UX Pro Max")

- MODE: skill, project-local, isolated to FC UX
- PURPOSE: a generic UI-pattern ADVISER for fc-ux -- never authoritative
  over Full Count's own product rules (no casino/neon/urgency aesthetic;
  see fc-ux.md's "Product identity" section, which always overrides a
  conflicting generic suggestion)
- ALLOWED AGENT: fc-ux only
- WHEN TO USE: a second opinion on a layout/interaction pattern
- WHEN NOT TO USE: never let it justify a product-identity violation
- TOKEN COST: skill-invocation cost only, on-demand
- CREDENTIALS: none expected
- PINNED VERSION: v2.15.0 confirmed current as of 2026-08-26 (repo:
  `nextlevelbuilder/ui-ux-pro-max-skill`, releases nearly daily)
- INSTALL STATUS: **IDENTIFIED, VETTED, NOT YET INSTALLED**. Inspected
  the actual repository (not old cached documentation): the historical
  Claude Code install defect the governing prompt warned about ("Zip file
  contains a symbolic link") is documented as fixed in v2.5.1+, and the
  current release (v2.15.0) is well past that. `SECURITY.md` is a real,
  specific policy -- explicitly scopes "arbitrary code execution via the
  CLI installer or search scripts" and "path traversal/unsafe file writes"
  as in-scope vulnerability categories (a transparency signal, not
  itself a red flag), but does not address telemetry/network-access
  practices (a real gap, not confirmed either way). Two install paths
  exist: `/plugin marketplace add` + `/plugin install` (a Claude Code
  slash command, not Bash-scriptable from a session, and installs at
  user scope by default -- would need the plugin enabled via THIS
  project's `.claude/settings.json` `enabledPlugins` specifically to
  satisfy "isolated, project-local only, fc-ux only," not user-level
  settings), or `npm install -g ui-ux-pro-max-cli` (global by design,
  conflicts with "project-local only" as written). Neither path was
  executed this pass -- doing so blindly would either require an
  interactive `/plugin` command this session can't issue, or a global
  install that violates the isolation requirement, or trusting the CLI
  installer's actual script contents (flagged by the project's own
  SECURITY.md as an in-scope risk surface) without having read them.
  Recommend: Jacob runs `/plugin marketplace add nextlevelbuilder/ui-ux-pro-max-skill`
  + `/plugin install ui-ux-pro-max@ui-ux-pro-max-skill` directly (this is
  a one-time interactive step only a human driving the CLI can do
  cleanly), after which a Claude session can verify it landed in this
  project's `enabledPlugins` and is reachable only from fc-ux.

## Mutation testing (mutmut or equivalent)

- MODE: CLI, on-demand
- PURPOSE: verify the existing test suite actually fails when scoring logic
  is deliberately broken -- a coverage number alone doesn't prove that
- ALLOWED AGENT: any, on-demand, never in default CI
- WHEN TO USE: spot-checking a specific high-value module's real test
  strength (e.g. `recommendation.py`, `settlement_rules.py`)
- WHEN NOT TO USE: repo-wide, on every CI run -- expensive and noisy at
  this codebase's size
- TOKEN COST: n/a (runtime tool)
- CREDENTIALS: none
- PINNED VERSION: n/a
- INSTALL STATUS: **DEFERRED** -- `mutmut` confirmed not installed; install
  on-demand the first time a specific module needs this check

## pip-audit / Bandit / Semgrep

- MODE: CLI, on-demand
- PURPOSE: dependency vulnerability scan (pip-audit) / static security
  lint (Bandit, or targeted Semgrep rules)
- ALLOWED AGENT: any, on-demand
- WHEN TO USE: before a dependency bump, or a targeted security review of
  a specific module
- WHEN NOT TO USE: routine every-commit gate until evaluated for
  false-positive rate on this codebase
- TOKEN COST: n/a
- CREDENTIALS: none
- PINNED VERSION: n/a
- INSTALL STATUS: **DEFERRED** -- confirmed not installed; install
  on-demand

## pre-commit / coverage / pytest-timeout / pytest-xdist

- MODE: CLI/pytest plugins, on-demand
- PURPOSE: pre-commit (git hook framework, not yet adopted -- this repo's
  test discipline is currently "run the full suite," not pre-commit hooks);
  coverage (measure); pytest-timeout (fail hung tests instead of hanging
  CI); pytest-xdist (parallelize -- only if test isolation is verified safe
  first, since some tests here touch shared fixture files)
- ALLOWED AGENT: any, on-demand
- WHEN TO USE: pytest-timeout is the most immediately valuable of these
  (cheap, catches a hung test outright) -- consider first
- WHEN NOT TO USE: pytest-xdist without first confirming no test relies on
  serial execution or shared mutable fixtures
- TOKEN COST: n/a
- CREDENTIALS: none
- PINNED VERSION: n/a
- INSTALL STATUS: **DEFERRED** -- confirmed not installed; install
  on-demand, pytest-timeout first if any is added

## MLflow (evaluate, do not force)

- MODE: n/a -- not standing up a server
- PURPOSE: experiment parameters/metrics/artifacts/model-version tracking
  with a remote artifact store backend
- ALLOWED AGENT: n/a (not adopted)
- WHEN TO USE: n/a yet
- WHEN NOT TO USE: as a permanent standing server "because it exists" --
  the governing instruction is explicit that this must reduce actual
  ambiguity/overhead to be worth adopting
- TOKEN COST: n/a
- CREDENTIALS: n/a
- PINNED VERSION: n/a
- INSTALL STATUS: **DEFERRED, STAGED DECISION**. NOW: the manifest +
  checksum + R2 design in `engineering/RESEARCH_ARTIFACT_DURABILITY.md`
  already covers artifact durability without a server. LATER: if research
  overhead from tracking multiple challenger experiments by hand actually
  becomes the bottleneck (not hypothetically), MLflow's tracking metadata
  could layer on top of the same R2 artifact store as the backend --
  revisit then, with a concrete pain point named, not preemptively.

## Observability: Grafana Cloud / OpenTelemetry / k6 (planned option, not adopted)

- MODE: n/a -- not instrumented yet
- PURPOSE: future SLI/SLO tracking once the Live system has enough moving
  parts to justify it -- heartbeat success/failure, GitHub dispatch
  latency, workflow start latency, live/settlement/pricing freshness age,
  error rates, traces (Grafana Cloud); load/latency testing for a future
  customer-facing delta endpoint or transport (k6, part of the same
  ecosystem)
- ALLOWED AGENT: n/a (not adopted)
- WHEN TO USE: n/a yet -- for the P0 soak specifically, simple structured
  logs (already how `infra/live-heartbeat/src/index.js` logs) are
  sufficient; don't add a metrics backend to prove out one heartbeat
- WHEN NOT TO USE: as a production dependency before there are enough
  real SLIs/SLOs defined to make it worth the operational overhead
- TOKEN COST: n/a
- CREDENTIALS: Grafana Cloud has a real free-forever tier (moved up from
  "random future tool" to "planned option" per this decision pass, not
  independently re-verified this session beyond that classification)
- PINNED VERSION: n/a
- INSTALL STATUS: **PLANNED OPTION, NOT ADOPTED**. Define the actual few
  SLIs/SLOs that matter first (this session's own P0 soak-test contract
  in `infra/live-heartbeat/README.md` is a starting list); instrument
  only what those require, prefer OpenTelemetry/Prometheus-style open
  standards over a vendor-specific agent, and do not make Grafana itself
  a production dependency.

---

## Deliberately NOT installed (project decision, see governing prompt)

Agent Reach (full stack), Claude Mem, LightRAG, Obsidian, GSD. Not needed
for the immediate foundation/P0 work. Revisit only if a concrete task
requires one specifically. (Grafana moved to "planned option" above,
2026-08-26 -- no longer in this deferred-indefinitely list.)

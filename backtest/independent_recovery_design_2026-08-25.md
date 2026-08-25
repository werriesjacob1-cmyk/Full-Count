# Independent recovery-path design comparison — 2026-08-25

Priority 2 from the governing instruction set. Core rule stated by the user:
**"the recovery mechanism should not share the same failure domain as the
system it is supposed to recover."** Grounded in the real incident just
observed (`backtest/live_incident_2026-08-25_0329.md`): `dashboard-live.yml`
went silent on its `*/5` cron for 47+ minutes; `live-freshness-watchdog.yml`
(a second, independently-scheduled `2-59/5` cron whose sole job is to detect
exactly this and recover it) ALSO went silent for 40+ minutes over the same
window and has not resumed on its own even after a manual dispatch of the
workflow it's meant to protect. That is direct, first-party evidence that a
watchdog built as another GitHub Actions `schedule` trigger shares its
recovery target's failure domain rather than standing outside it.

## Incident-derived answers to the 9 investigation questions

1. **Which scheduled workflows failed to receive executions during the
   window (~03:29-04:16 UTC)?** `dashboard-live.yml` (0 scheduled runs in 47+
   min vs. an expected ~9) and `live-freshness-watchdog.yml` (0 scheduled runs
   in 40+ min vs. an expected ~8, after firing exactly once at 03:36).
2. **Which continued?** `Lineup Watch` (`*/10`) — though it also showed one
   47.5-min gap (03:08→03:55) inside the same broad window, then resumed on
   its own. `Odds Snapshot` (hourly) showed no anomaly (its own cadence is
   coarse enough that a ~55 min gap between runs is unremarkable).
   `Dashboard Pages Deploy` is not independently scheduled — it fires via
   `workflow_run` off `dashboard-live.yml`/`dashboard-refresh.yml`
   completions, so its own gap is a direct downstream reflection of
   `dashboard-live.yml`'s gap, not new information.
3. **Were runs queued, skipped, delayed, cancelled, or never created?**
   Checked directly: zero `queued` and zero `in_progress` runs existed for
   `dashboard-live.yml` during the gap (confirmed via `list_workflow_runs`
   with `status: queued` / `status: in_progress` filters, both returned
   `total_count: 0`). This rules out "queued but not yet started" — the runs
   were simply never created. That is real evidence of a **trigger-delivery**
   failure, not a **runner-capacity** failure (a capacity problem would show
   queued-but-not-started runs).
4. **Repo-wide Actions activity during the window?** Not fully saturating —
   `Lineup Watch` and `Odds Snapshot` both had real runs land inside the same
   broad window, so this was not a total repo-wide Actions outage.
5. **Were manual `workflow_dispatch` runs accepted immediately?** Yes, both
   times this session (`dashboard-refresh.yml` at 03:57, `dashboard-live.yml`
   at 04:09) — each queued and started within seconds. This is significant:
   the runner/execution layer itself was never impaired; only the `schedule`
   trigger's own delivery was affected.
6. **Did concurrency groups play a role?** No evidence they did — each
   affected workflow has its own independent concurrency group
   (`dashboard-live-observation`, `live-freshness-watchdog`), and manual
   dispatches into those same groups ran immediately with no visible queuing
   or blocking.
7. **Did pushes/workflow-file updates correlate with missing deliveries?**
   PR #64 merged at 03:57:57 (`1ead2fb1`), i.e. roughly in the middle of the
   gap window, not at its start (~03:29) — the gap began before that merge
   and has continued after it, so this specific merge does not explain the
   onset. Not ruled out as a contributing factor to the whole window, but not
   a clean single-cause correlation either.
8. **Evidence of GitHub schedule-delivery unreliability vs. job-execution
   failure?** Strong evidence for delivery unreliability specifically: zero
   queued/in-progress runs (not stuck runs — never-created runs), manual
   dispatch working instantly and completing fast (35s), and — the strongest
   single data point — the watchdog itself, a completely different, much
   cheaper (3-minute timeout, single lightweight step) workflow on its own
   independent cron, exhibiting the identical symptom at almost the same
   time. Two unrelated workflows both losing scheduled delivery together is
   much better explained by something at the trigger-delivery layer (GitHub's
   own cron scheduler for this repo/account) than by anything inside either
   workflow's own job definition.
9. **Does ~1,050 scheduled invocations/day increase exposure even if it
   wasn't the 2026-08-24 runtime root cause?** Plausibly yes, as a general
   "more scheduled triggers competing for the same account/repo-level
   delivery mechanism" exposure story — but this incident's own evidence
   (a period where the two highest-frequency workflows both went silent while
   two others kept running) is more specific than pure volume: it looks like
   an intermittent, occasionally sustained gap in GitHub's own cron delivery
   for this repo, and this session found no way to rule that in or out from
   inside the repo alone. **Flagged as open, not resolved** — GitHub-side
   scheduled-trigger reliability is outside what repo-level evidence can
   conclusively diagnose; this is the practical justification for Priority
   2's design comparison below, independent of settling this specific
   mechanism question.

## Design comparison: independent recovery path

### Option A — External heartbeat service
An external, always-on (or scheduled-outside-GitHub) service periodically
fetches `docs/live.json` (a public GitHub Pages URL, or via the GitHub API)
and checks `updated_at` staleness; if stale beyond the SLA, it calls the
GitHub REST API (`POST /repos/{owner}/{repo}/actions/workflows/{id}/dispatches`)
using a stored PAT/GitHub App token to trigger `dashboard-live.yml` directly —
functionally identical to what the current watchdog does, but the CHECK and
DISPATCH decision happens entirely outside GitHub Actions' own scheduler.

- **Failure-domain independence**: High. The check/trigger logic runs on
  infrastructure with no dependency on GitHub's cron delivery at all (only
  the dispatch API call touches GitHub, and that's the same
  `workflow_dispatch` path already proven to work reliably even when
  `schedule` triggers were silent).
- **Cost**: Low. A single scheduled function (e.g. a free-tier serverless
  cron — Cloudflare Workers Cron Triggers, GitHub-external scheduler like
  cron-job.org, a tiny always-on VM, etc.) hitting one public URL and
  conditionally one API call every few minutes.
- **Implementation complexity**: Low-moderate. Needs: (1) a place to run
  (see cost), (2) one GitHub PAT or App installation token with
  `actions:write` scope stored as a secret on that external platform, (3)
  the same staleness-check logic already written and tested in
  `dashboard/check_live_freshness.py` — directly reusable, just called from
  outside a GitHub Actions job instead of inside one.
- **Auth/security**: A GitHub token with repo `actions:write` living outside
  GitHub's own secret store is a real, new credential-exposure surface that
  doesn't exist today — needs careful scoping (fine-grained PAT limited to
  this one repo, this one permission) and rotation plan.
- **Expected recovery latency**: Comparable to or better than the current
  watchdog's own cadence (external cron services commonly support 1-5 minute
  intervals; this session's evidence found no capacity/execution delay once
  a dispatch actually lands, so latency is dominated by the external
  scheduler's own check interval, same as today).
- **Duplicate-writer risk**: None — this option only ever calls
  `workflow_dispatch` on the existing `dashboard-live.yml`, never writes
  `docs/live.json` itself. Fully compatible with the existing single-writer
  contract.
- **Compatibility with existing canonical live writer**: Full — no changes
  needed to `dashboard-live.yml`, `merge_live_files.py`, or any live-state
  code at all.
- **Free-tier feasibility**: Yes — this is squarely within free tiers of
  common external cron platforms (Cloudflare Workers Cron Triggers' free
  tier, cron-job.org, GitHub Actions on a DIFFERENT, low-traffic repo acting
  purely as an external trigger source — though that last option partially
  reintroduces GitHub-scheduler dependency and should be avoided if the goal
  is genuine independence).

### Option B — External primary live orchestration, GitHub as fallback
Move the actual grading/repricing/live-write logic to run on external
infrastructure (e.g. the `alive_brain_prototype.py`/Cloudflare Workers design
already sketched in `backtest/alive_brain_design.md`) as the PRIMARY path,
keeping `dashboard-live.yml` as a periodic reconciliation/fallback rather than
the sole writer.

- **Failure-domain independence**: Highest of the three options — the
  primary live path no longer depends on GitHub Actions scheduling at all.
- **Cost**: Higher — real compute running continuously or near-continuously,
  not just a periodic check. Likely still free-tier-feasible at current
  scale per the alive-brain design doc's own budget sketch, but this is a
  materially bigger commitment than Option A.
- **Implementation complexity**: High. Requires: a real deployed runtime,
  the actual grading/pricing logic ported or shared with the external
  environment, careful definition of exactly when GitHub's own writer
  defers to (or reconciles with) the external one, and new tests for that
  reconciliation logic.
- **Auth/security**: Needs write access to `docs/live.json` (or an
  equivalent externally-hosted live-state store) from outside GitHub — a
  bigger, more sensitive credential surface than Option A's single dispatch
  token.
- **Expected recovery latency**: Best-in-class — this isn't "recovery," it's
  removing the dependency on the failure-prone path entirely for the
  time-critical portion of the pipeline.
- **Duplicate-writer risk**: Real and non-trivial. Two live writers (GitHub's
  existing `dashboard-live.yml` and the new external orchestrator) racing to
  update the same canonical state is exactly the class of bug
  `merge_live_files.py`'s existing per-field-recency merge logic was built to
  guard against for GitHub's OWN internal retry races — extending that
  guarantee across two genuinely different runtimes is a real, careful
  design problem, not a given.
- **Compatibility with existing canonical live writer**: Requires redesigning
  the "sole writer" contract, not just adding a bystander — the biggest
  compatibility cost of the three options.
- **Free-tier feasibility**: Plausible per the existing alive-brain design
  doc's own numbers, but unverified at the "acting as primary, not a
  prototype" scale this option implies.

### Option C — Reduce/consolidate internal GitHub scheduled workflow count + external freshness observer
Combine a lightweight version of Option A (external freshness check +
dispatch-only recovery) with the consolidation ideas already recorded in
`backtest/scheduled_workflow_inventory_2026-08-25.md` (e.g. folding Dashboard
Pages Deploy's own commit into Dashboard Live Update's) to reduce total
GitHub-scheduled-trigger volume WITHOUT moving primary orchestration off
GitHub.

- **Failure-domain independence**: Same as Option A for the recovery path
  itself (the external check/dispatch is independent); the consolidation
  half does not by itself increase independence, it only reduces exposure
  frequency to whatever underlying GitHub-side mechanism is involved.
- **Cost**: Same as Option A for the external piece, plus normal engineering
  time for the (already-scoped, already-deferred) consolidation work.
- **Implementation complexity**: Moderate — the external piece is Option A;
  the consolidation piece is scoped but explicitly NOT yet measured/executed
  per the standing "measure first" instruction.
- **Auth/security**: Same single-token concern as Option A.
- **Expected recovery latency**: Same as Option A.
- **Duplicate-writer risk**: None from the external piece; the consolidation
  piece could introduce risk if done carelessly (already flagged in the
  inventory doc) — mitigated by doing it AFTER, and separately from, the
  external recovery path, not bundled into one risky change.
- **Compatibility with existing canonical live writer**: Full, same as
  Option A, plus whatever the (separately-evaluated) consolidation preserves.
- **Free-tier feasibility**: Yes, same basis as Option A.

## Recommendation

**Option A**, with Option C's consolidation half kept as a separate,
independently-evaluated follow-up (not bundled). Reasoning: Option A directly
satisfies the stated core rule (the recovery mechanism must not share the
protected system's failure domain) with the lowest cost, complexity, and new
risk surface of the three — it reuses already-written, already-tested
staleness-check logic (`dashboard/check_live_freshness.py`) and the
already-proven-reliable `workflow_dispatch` recovery path, changing only
WHERE the check/decision runs. Option B is the more ambitious, eventually
appealing "real fix" (matches the long-term alive-brain direction), but its
duplicate-writer risk and implementation cost are real and not yet justified
by evidence — the user's own standing instruction is "do not deploy a large
migration yet," and Option B is a large migration. Option C's external half
is just Option A; its consolidation half is real but was already explicitly
deferred pending its own measurement pass and should stay deferred rather
than be coupled to this recovery-path decision.

**Not implemented in this update** — this is the requested evidence-backed
architectural recommendation, not an authorization to build it. Building
Option A would need: picking a concrete external scheduler (needs a
product/cost decision the user should weigh in on, since it's the first
credential/service dependency outside GitHub this project would take on),
provisioning a token, and wiring the dispatch call — worth scoping as its own
follow-up once prioritized.

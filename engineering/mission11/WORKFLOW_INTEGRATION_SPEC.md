# Mission 1.1 — workflow integration specification (NOT APPLIED)

**Status: SPECIFIED, NOT IMPLEMENTED. No workflow file in this repository has
been modified by Mission 1.1.**

`git diff --stat` for this branch touches **zero** files under `.github/`.
That is deliberate and it is a scope boundary, not an omission:

* §17 of the mission brief withholds authority to enable shadow persistence in
  live workflows.
* Two of the three required edits are to `dashboard-deploy.yml`, which is the
  path that puts real Top Picks in front of a bettor. Protocol §14 forbids the
  shadow from changing publication behaviour.

Everything below is what a later, separately authorized change would do. It is
written precisely enough to apply without re-deriving it.

---

## Why a post-deployment hook is unavoidable

Public convergence **is not recorded durably anywhere today.** A repo-wide
grep for `converged` / `convergence` across `*.py` and `*.yml` returns only two
log strings in `dashboard-deploy.yml` and unrelated numerical-solver code.

The only durable side effect of a converged deploy is a
`data/public_top_picks/registry.json` entry, and it is a lossy proxy:

* written **only** when the deploy admitted at least one genuinely new Top Pick
  (12 distinct deployments across ~2 weeks, against dozens of deploys per day);
* records **no triggering workflow name** — so §7's decisive criterion
  (refresh-originated vs live-update-originated) is unrecoverable after the
  fact;
* records **no public `data.json` generated_at** — so §8's hash binding is
  unrecoverable;
* records **no convergence timestamp**.

So a purely post-hoc binder cannot exist. The fact must be captured at the
moment it is true, or it is gone.

---

## The three edits

### 1. `dashboard-refresh.yml` — enable capture persistence

Job `build-and-publish`, step `Build dashboard`, add a step-level `env:`

```yaml
        env:
          FULLCOUNT_SHADOW_PERSIST: "1"
```

Risk: low. The tap is guarded by `BaseException` twice (inside `capture()` and
again at the call site), and a failed research persist returns a report the
caller ignores.

### 2. `dashboard-deploy.yml` — expose the convergence result

The step `Verify public site converges to deployed artifact` has no `id:` and
writes nothing to `$GITHUB_OUTPUT`. Add `id: converge`, and at the existing
`CONVERGED` success branch — immediately before its `sys.exit(0)` — append to
`os.environ["GITHUB_OUTPUT"]`:

```
converged=true
converged_at=<UTC now>
public_generated_at=<public_generated_at>
public_source_commit=<public_commit>
```

**Additive only.** It cannot change the step's pass/fail decision.

### 3. `dashboard-deploy.yml` — the research hook

A new step, **last in the job**, after `Confirm durable public exposure`, with
`continue-on-error: true`.

Placement is load-bearing in both directions:

* **After** `Verify public site converges…`, which has no `continue-on-error`.
  Any later step running at all is therefore itself proof that convergence
  succeeded — no new detection logic is needed.
* **After** `Confirm durable public exposure`, never before it. Research code
  upstream of the registry write could, inside a 10-minute job timeout, cost a
  real Top Pick its exposure record.
* **`continue-on-error: true`** so a research failure can never redden or abort
  the publication job.

It writes a deployment observation JSON and invokes:

```
python3 -m backtest.prospective_lifecycle bind-exposure \
  --deployment "$RUNNER_TEMP/deployment.json" \
  --payload "$RUNNER_TEMP/pages-artifact/data.json" \
  --ledger "$RUNNER_TEMP/fullcount-research-ledger"
```

Fields for the deployment JSON, with their exact sources:

| field | source |
|-------|--------|
| `slate_date` | `jq -r .date` of the artifact `data.json` |
| `triggering_workflow_name` | `${{ github.event.workflow_run.name }}` |
| `triggering_workflow_run_id` | `${{ github.event.workflow_run.id }}` |
| `converged` / `converged_at` | `steps.converge.outputs.*` (edit 2) |
| `public_generated_at` | `steps.converge.outputs.public_generated_at` |
| `public_source_commit` | `steps.converge.outputs.public_source_commit` |
| `source_commit` | `jq -r .source_commit` of `publication_manifest.json` |
| `prepared_at` | `jq -r .prepared_at` of the manifest |
| `publication_cutoff_at` | `jq -r .publication_cutoff_at` of the manifest |
| `artifact_id` | `jq -r .artifact_id` of the manifest |
| `run_id` | `${{ github.run_id }}` |
| `page_url` | `${{ steps.deployment.outputs.page_url }}` |

`triggering_workflow_name` **must** be captured here. It is not recoverable
afterwards from anything committed, and it is the field §7 uses to exclude
live-update-originated deployments from the primary scoreboard.

### 4. `mlb-daily.yml` — settlement (separate, later)

A new `continue-on-error` step after `Grade yesterday's picks`, running
`designate` then `settle` for the completed slate date.

---

## Known risk that must be measured before enabling

`dashboard-deploy.yml`'s single concurrency group is shared with the ~288
live-update-triggered deploys per day, and with `cancel-in-progress: false` a
*pending* run is cancelled by a newer one. A refresh-originated deploy sitting
pending is therefore likely to be cancelled by the next live-update deploy.

§7 requires the decisive epoch to be **Dashboard Refresh-originated**. Whether
enough such deployments actually converge per slate date is answerable **only
from the Actions run history**, which is not in this repository.

**Query a week of `dashboard-deploy.yml` runs before enabling persistence.** If
refresh-originated converged deployments turn out to be rare, the honest
outcome is many NO PRIMARY EPOCH dates. Loosening §7 to admit live-update
deployments is not available: the protocol forecloses it in terms, and doing it
would require Jacob to re-lock the protocol.

---

## Separate pre-existing production bug (NOT this mission's to fix)

`dashboard/confirm_publication.py` appends a hash-chained event per new
registry entry to `data/prediction_ledger/events.jsonl`, but
`dashboard-deploy.yml` stages **only** `registry.json`, and the next retry's
`git checkout --detach origin/main` discards the rest.

Evidence: `registry.json` holds 108 entries; `events.jsonl` holds 6, and
`git log -- data/prediction_ledger/events.jsonl` shows only two development
commits and **not one workflow-produced commit**, while `registry.json` has a
long run of `Record deployed Top Pick exposure` commits.

**The immutable Prediction Ledger has recorded nothing in production.** The fix
is one line — add the path to that `git add`. Not made here: it is outside this
mission's scope, it edits the live publication workflow, and it deserves its
own change on its own merit.

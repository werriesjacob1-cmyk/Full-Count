# fc-live-heartbeat

A Cloudflare Worker whose entire production job is: **every ~5 minutes,
wake `dashboard-live.yml`.** Nothing else. It never fetches MLB/FanDuel
data, never grades, never prices, never writes to `docs/live.json` or
`docs/data.json`, and never calls an LLM. See `src/index.js`'s own header
comment for the full contract, and `test/index.test.js`'s "structural
contract" suite, which statically asserts the code contains none of that.

## Why this exists

`dashboard-live.yml`'s GitHub `schedule` trigger (configured for every 5
minutes) is observed firing every 34-136 minutes in this repo -- a
repo-wide GitHub-side scheduling throttle (this repo runs 8+ other
5-15-minute cron workflows competing for the same allocation), not a bug
in the workflow itself. See `P0_LIVE_LIFECYCLE_INCIDENT_2026-08-26.md` for
the full forensics. `live-freshness-watchdog.yml`'s own recovery dispatch
rides the same throttled scheduler, so it isn't the independent backstop
it was designed to be. An external scheduler outside GitHub's own cron
infrastructure is the fix; this Worker is that scheduler, chosen over
n8n for this one job because it needs no server we maintain, has a free
tier that comfortably covers ~288 invocations/day, and only has to make
one authenticated REST call.

**"Independent of GitHub's throttle" is a hypothesis, not yet a proven
fact.** The soak test below is how it gets proven or disproven.

## Architecture

```
Cloudflare Cron Trigger (*/5 * * * *)
  -> Worker scheduled() handler
  -> POST https://api.github.com/repos/werriesjacob1-cmyk/Full-Count/actions/workflows/dashboard-live.yml/dispatches
     { "ref": "main" }
  -> dashboard-live.yml runs (unchanged) and does all the real work
```

GitHub's own `schedule` trigger on `dashboard-live.yml` **remains in
place** as redundancy -- this Worker adds a trigger source, it does not
replace the existing one.

## Duplicate-trigger / concurrency safety

We will have three trigger sources hitting the same workflow: this
Worker, GitHub's own `schedule`, and `live-freshness-watchdog.yml`'s
recovery `workflow_dispatch`. This is safe **because of existing code,
unchanged**:

1. `dashboard-live.yml` already declares:
   ```yaml
   concurrency:
     group: dashboard-live-observation
     cancel-in-progress: false
   ```
   GitHub Actions guarantees at most one run of a concurrency group
   executes at a time; `cancel-in-progress: false` means an in-progress
   run is never killed by a new trigger, and GitHub coalesces extra
   *pending* triggers into a single queued run rather than queuing one
   per trigger source (this workflow's own header comment states this is
   deliberate: "coalesce obsolete five-minute observations instead of
   building an hours-long queue"). Three trigger sources cannot produce
   two simultaneous executions.
2. Even so, the "Commit and push live state" step has its own independent
   defense-in-depth: on every attempt (including retries after a push
   rejection) it re-fetches `origin/main` and re-merges via
   `dashboard/merge_live_files.py` -- a semantic, per-field merge, not a
   blind overwrite -- before pushing. `merge_live_files.py` tracks
   settlement authority (`official_final` durably protected) and rejects
   an older observation overwriting a newer one at the field level.
3. This logic is exercised by `test_state_races.py`, `test_live_lifecycle.py`,
   and `test_lifecycle_contract_v3.py`, all already passing, plus the new
   frontend per-field regression fix in PR #68
   (`test_frontend_lifecycle.py`'s `test_colt_keith_style_final_state_never_regresses_to_a_stale_live_poll`).

**No change to `dashboard-live.yml`'s concurrency configuration was made**
-- it was inspected and found already sufficient for this. Do not change
it without a real, demonstrated defect; none was found.

## Security contract

- **Credential**: a GitHub **fine-grained personal access token**, scoped
  to exactly one repository (`werriesjacob1-cmyk/Full-Count`), with
  repository permission **Actions: Read and write** only. No Contents
  write, no Administration write, no organization-wide scope, not a
  classic broad `repo` token.
- **Storage**: the token is stored ONLY as a Cloudflare Worker secret
  (`wrangler secret put GITHUB_PAT`). It is never committed, never placed
  in `wrangler.toml`, never logged, never printed, and never returned in
  any Worker response. `test/index.test.js` has two dedicated tests
  asserting the token string never appears in anything passed to
  `console.log`/`console.error`, on both the success and failure paths.
- **Failure behavior**: a non-2xx GitHub response is classified
  (`classifyResponse()` in `src/index.js`) and logged as a small,
  secret-free structured line (timestamp, cron string, status, a short
  machine-readable `reason`) -- enough to diagnose from the Cloudflare
  dashboard without ever including the request itself (which is the only
  place the Authorization header lives). One retry is attempted for a
  transient 5xx or a network-level error only; a 401/403/404 is a
  configuration problem a retry cannot fix, so it fails immediately and
  visibly. There is no complex retry engine -- the next 5-minute cron
  tick is the real backstop, matching the governing instruction not to
  build duplicate-dispatch complexity.

## Required Jacob setup -- PHONE-ONLY, browser dashboards, no terminal

Everything below works from Safari on an iPhone (or any browser) against
the Cloudflare dashboard and GitHub's website. Nothing requires a local
terminal, `npx`, or `wrangler` -- the CLI commands later in this file are
optional developer alternatives, not what you need to do this.

### 1. GitHub fine-grained PAT (create this first -- Cloudflare will ask for it)
1. In Safari: https://github.com/settings/personal-access-tokens/new
   (sign in if needed).
2. **Resource owner**: `werriesjacob1-cmyk`.
3. **Repository access**: "Only select repositories" -> `Full-Count`.
4. **Permissions** -> Repository permissions -> **Actions**: Read and write.
   Leave every other permission at "No access."
5. Set an expiration (90 days is reasonable -- GitHub will email a
   reminder; rotating just means repeating step 3 of the next section
   with the new value, nothing else changes).
6. Tap Generate, then **copy the token immediately** -- GitHub only shows
   it once. Paste it somewhere safe (like Notes) until step 2.3 below.

### 2. Connect this repo in the Cloudflare dashboard (no CLI)
1. https://dash.cloudflare.com -> sign in (or create a free account, no
   card required for the Workers Free plan).
2. **Workers & Pages** -> **Create** -> **Workers** (Workers, not Pages)
   -> **Import a repository** (sometimes labeled "Connect to Git").
3. Authorize Cloudflare's GitHub App if prompted, and grant it access to
   `werriesjacob1-cmyk/Full-Count` specifically (not all repos, if GitHub
   offers a choice).
4. Select the repository. Cloudflare will show build configuration:
   - **Production branch**: `main`
   - **Root directory**: `infra/live-heartbeat` -- this is the field that
     makes a monorepo work; Cloudflare only builds/deploys what's inside
     this folder, and (per current Cloudflare docs) the deploy command
     defaults to `npx wrangler deploy` automatically once a
     `wrangler.toml` is found there, so no build command needs typing.
   - **Worker name**: `fc-live-heartbeat` (or accept the suggested name).
5. Deploy. Cloudflare reads `wrangler.toml`'s `[triggers] crons = ["*/5 * * * *"]`
   and registers the Cron Trigger automatically -- no separate schedule
   setup step.

### 3. Restrict rebuilds to this folder only (optional but recommended)
Cloudflare Workers Builds supports this natively: Worker -> **Settings**
-> **Build** -> **Build watch paths**. Set **Include paths** to
`infra/live-heartbeat/*` (check the field's own inline syntax help when
you're there -- Cloudflare's wildcard syntax may accept a more specific
pattern than shown here; this is the documented mechanism, verify the
exact glob against what the UI currently offers). This stops an unrelated
commit anywhere else in Full-Count (a dashboard refresh, a data snapshot)
from triggering a Worker rebuild.

### 4. Add the GitHub PAT as a Cloudflare secret (no CLI)
1. On the Worker's page: **Settings** -> **Variables and Secrets** -> **Add**.
2. Type: **Secret**. Variable name: `GITHUB_PAT`. Value: paste the token
   from step 1.
3. Tap **Deploy** to apply it. Cloudflare's own docs confirm the value is
   hidden afterward in both the dashboard and any CLI.

**That's it -- four browser steps, zero terminal.** Once step 4 is done,
the heartbeat is live and will fire every 5 minutes.

## Optional: developer/CLI path (not needed for the phone setup above)

Everything above uses only the Cloudflare dashboard and GitHub's website.
The CLI is an alternative for anyone deploying from a laptop instead:
`npx wrangler login` (browser-based auth) then `npx wrangler deploy` from
this directory publishes the Worker and its Cron Trigger; `npx wrangler secret put GITHUB_PAT`
stores the secret the same way step 4 above does via the dashboard. These
are equivalent paths to the same result, not two different setups to do.

## Local testing (developer-only; not required to deploy)

```
cd infra/live-heartbeat
npm install
npm test
```

Runs entirely inside the real Workers runtime locally (Miniflare, via
`@cloudflare/vitest-plugin` -- Cloudflare's current supported local
testing mechanism; note this superseded the older
`@cloudflare/vitest-pool-workers` package, which this project briefly
tried first and found published a version whose `/config` export path no
longer exists -- always verify the current package/API before trusting a
remembered shape). 23 tests, no network access, no real GitHub calls:
correct endpoint/method/ref/headers, Authorization sourced from the passed
token (not hardcoded), 204/401/403/404/5xx/network-error handling, one
retry only, secret never logged on either the success or failure path, and
a structural check that the code contains no MLB/FanDuel/grading/pricing/
git-write logic.

`npm run dev` runs `wrangler dev` for interactive local testing
(`fetch()` returns a static "no public API" message; `wrangler dev --test-scheduled`
lets you trigger the cron handler manually against a `.dev.vars` file
holding a throwaway local `GITHUB_PAT` -- never commit `.dev.vars`, it's
gitignored here).

No unit test fires a real GitHub dispatch. One intentional real dispatch
is appropriate only once Jacob has completed the setup above and it's
time to validate the integration end-to-end -- not before.

## Soak test contract (required before the P0 incident can be called resolved)

Once Jacob has completed setup and the heartbeat is live, run a
multi-hour production soak during active MLB games. For each expected
heartbeat, measure:

- Cloudflare scheduled time (Worker logs)
- GitHub workflow run creation time
- GitHub job start time / completion time
- `docs/live.json`: `grades_checked_at`, `prices_checked_at`

Track: expected triggers, successful dispatches, GitHub-created runs,
duplicate triggers (expected and harmless per the concurrency section
above -- confirm they stay harmless), failed dispatches, failed
workflows, maximum game-state-verification gap, maximum settlement age,
maximum pricing age, final-game settlement latency.

**Success target**: normal cadence ~5 minutes; hard reliability target no
game-state verification gap exceeding 15 minutes during the soak; final
settlement normally within one successful observation cycle after
authoritative MLB final.

**Until this soak passes, the P0 incident is NOT resolved**, even once
this code is merged and live -- P0 code being ready is not the same claim
as the incident being resolved. GitHub's own `schedule` trigger continues
to fire during the soak too; that's expected and fine.

# External freshness-recovery heartbeat (Priority 3, 2026-08-25)

## What this is, and why

Direct, live evidence this session (`backtest/live_incident_2026-08-25_0329.md`):
`dashboard-live.yml` and its own `live-freshness-watchdog.yml` — two
**independently scheduled** GitHub Actions workflows — both went silent on
their 5-minute crons at the same time, for 30-40+ minutes, with zero
queued/in-progress runs (a trigger-delivery gap, not a runner-capacity one).
A manual `workflow_dispatch` restored freshness in 35 seconds but did **not**
restore either recurring schedule. This proves a GitHub-schedule-based
watchdog shares its protected workflow's failure domain — it cannot be relied
on as an independent recovery path (see
`backtest/independent_recovery_design_2026-08-25.md`, Option A, recommended).

This directory implements Option A: a Cloudflare Worker, on infrastructure
with zero dependency on GitHub's own scheduled-trigger delivery, that checks
`docs/live.json`'s public `updated_at` and dispatches the existing
`dashboard-live.yml` recovery workflow if it's stale. **It never writes live
state itself, never touches any GitHub `schedule` trigger, and only ever
calls the same `workflow_dispatch` REST endpoint already proven reliable
this session.**

## What's done vs. what needs your action

**Done, tested, ready to deploy:**
- `worker.js` — the full Worker: pure `decide()` logic (fresh/stale/
  cooldown/fetch-failure branches) plus the fetch → decide → dispatch →
  record-to-KV flow.
- `test_worker.mjs` — 9 tests against `decide()` with synthetic timestamps,
  including this session's own real 40-minute-stale incident as a case. No
  network calls, no credentials, no Cloudflare account needed to run:
  `node ops/external_heartbeat/test_worker.mjs` (all 9 passing as of this
  commit).
- `wrangler.toml` — deployment config, `DRY_RUN = "true"` by default so a
  first deploy is safe to observe before it can ever really dispatch.

**Needs a one-time action only you can safely take** (a Cloudflare account
and a GitHub token are both real credentials I cannot create or hold on your
behalf):

### 1. Create (or use an existing) free Cloudflare account
cloudflare.com → sign up. The free tier's Cron Triggers easily cover a
5-minute heartbeat (Workers free tier: 100,000 requests/day; a 5-minute cron
is ~288 invocations/day, far under that).

### 2. Install Wrangler and authenticate
```
npm install -g wrangler
wrangler login
```

### 3. Create the KV namespace this Worker uses for cooldown/history
```
cd ops/external_heartbeat
wrangler kv namespace create HEARTBEAT_KV
```
This prints an `id`. Paste it into `wrangler.toml`'s
`REPLACE_WITH_REAL_KV_NAMESPACE_ID` placeholder.

### 4. Create a GitHub token scoped to ONLY this repo, ONLY Actions:write
GitHub → Settings → Developer settings → Fine-grained personal access
tokens → Generate new token:
- Repository access: **Only select repositories** → `Full-Count`
- Permissions: **Actions: Read and write** (nothing else)
- Set an expiration and calendar-remind yourself to rotate it.

Store it as a Worker secret (never in source, never in `wrangler.toml`):
```
wrangler secret put GITHUB_TOKEN
```
(paste the token when prompted)

### 5. Deploy
```
wrangler deploy
```

### 6. Verify (dry-run first)
With `DRY_RUN = "true"` (the default), watch real ticks without any risk of
a real dispatch:
```
wrangler tail
```
Trigger one on-demand instead of waiting up to 5 minutes:
```
curl https://fullcount-live-heartbeat.<your-subdomain>.workers.dev/
```
You should see a log line like:
```
no_op_fresh: 187s old, under the 900s SLA
```
(or, if `docs/live.json` happens to be genuinely stale right now, a
`DRY_RUN: would dispatch dashboard-live.yml -- ...` line — this is real,
useful signal, not a bug, if it happens during verification).

### 7. Go live
Once you've watched several real ticks and are satisfied the freshness
reads are correct, flip `DRY_RUN` to `"false"` in `wrangler.toml` and
`wrangler deploy` again. From then on, a genuinely stale `docs/live.json`
(15+ minutes old, matching `dashboard/check_live_freshness.py`'s own SLA)
will trigger a real `workflow_dispatch` of `dashboard-live.yml`, at most
once per 20-minute cooldown.

## Recovery semantics (all in `worker.js`, quoted here for one-page reference)

- **Stale threshold**: 15 minutes (`STALE_THRESHOLD_MS`) — identical to the
  existing `dashboard/check_live_freshness.py`, not a new number.
- **Cooldown**: 20 minutes (`COOLDOWN_MS`) between real dispatches, tracked
  in KV (`last_dispatch_ms`) — durable across cron ticks, prevents a
  dispatch storm if the underlying cause takes longer than one tick to clear.
- **GitHub API unreachable**: `dispatchRecovery()`'s `fetch()` failing
  surfaces as an uncaught rejection inside `scheduled()` — Cloudflare
  retries a failed scheduled invocation automatically per its own platform
  behavior; nothing bespoke was built for this since the platform already
  provides it.
- **`docs/live.json` itself unreachable**: `fetchUpdatedAt()` catches and
  returns `null` → `decide()` returns `no_op_fetch_failed`, explicitly
  never dispatching on an absent signal (see `decide()`'s own comment: "do
  nothing rather than dispatch on an assumption").
- **Observability**: every tick's decision is both `console.log`'d (visible
  live via `wrangler tail`) and written to `HEARTBEAT_KV` under a
  timestamped key with a 7-day TTL — a human can inspect recent history via
  `wrangler kv key list --binding HEARTBEAT_KV` without needing a
  dashboard.

## What this deliberately does NOT do

- Does not write `docs/live.json` or any other live-state file — no second
  writer, no race with `merge_live_files.py`'s existing per-field-recency
  merge logic.
- Does not touch any GitHub `schedule` trigger, concurrency group, or
  workflow YAML.
- Does not replace `live-freshness-watchdog.yml` — that workflow still runs
  and is still useful for the (different) case where GitHub's scheduler is
  healthy but `dashboard-live.yml` itself fails for an in-repo reason. This
  heartbeat is specifically for the case that workflow structurally cannot
  cover: GitHub's own scheduled-trigger delivery going quiet.

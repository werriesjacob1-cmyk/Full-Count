# Full Count freshness watchdog

An independently-scheduled trigger for `.github/workflows/dashboard-live.yml`,
built because GitHub Actions' own cron scheduler cannot be trusted to fire
that workflow anywhere near its declared `*/5 * * * *` cadence — see
`src/index.js`'s own docstring for the real measured evidence. This Worker
does not replace the existing cron; it's a second, genuinely independent
path to the same `workflow_dispatch` call, so a GitHub-scheduler outage
doesn't leave the live board unwatched.

**This code has not been deployed.** Deploying it requires a real
Cloudflare account belonging to Jacob and a real GitHub credential scoped to
this one repository — neither of which this session has or should ever
fabricate. Everything below is the exact, smallest set of manual steps
needed; the code itself is complete and ready to go.

## What Jacob needs to do (one-time, ~10 minutes)

1. **Create a fine-grained GitHub PAT**, scoped to exactly this repository:
   - GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token
   - Repository access: **only** `werriesjacob1-cmyk/Full-Count`
   - Permissions: **Actions: Read and write** (nothing else — this is the
     minimum scope `workflow_dispatch` requires; no contents, no admin, no
     other repo access)
   - Expiration: your choice; a shorter expiry with a calendar reminder to
     rotate is safer than "no expiration."

2. **Have (or create) a Cloudflare account** — the free tier's Workers plan
   is sufficient (this Worker fires roughly every 5 minutes, well inside
   the free daily request allowance).

3. **Deploy:**
   ```
   cd cloudflare-watchdog
   npx wrangler login          # one-time browser auth to your Cloudflare account
   npx wrangler secret put GITHUB_PAT
   # paste the fine-grained PAT from step 1 when prompted
   npx wrangler secret put PING_TOKEN
   # paste any random string (e.g. `openssl rand -hex 32`) -- this is NOT
   # the GitHub token, just a shared secret so the redundant external
   # pinger can call this Worker's /ping route without ever touching or
   # being able to leak the real GitHub credential
   npx wrangler deploy
   ```
   This prints the Worker's public URL (`https://full-count-freshness-watchdog.<your-subdomain>.workers.dev`).

4. **(Recommended, for the second independent trigger path)** Register a
   free external monitor to hit `https://<your-worker-url>/ping?token=<PING_TOKEN>`
   every 5-7 minutes — e.g. [cron-job.org](https://cron-job.org) or
   [healthchecks.io](https://healthchecks.io)'s ping-URL feature, both free.
   This is optional: the native Cloudflare Cron Trigger (step 3) already
   provides one fully independent path on its own. The external pinger adds
   a second, differently-hosted failure domain on top of that, at zero
   marginal credential risk (see `src/index.js`'s docstring for why).

## Verifying it's working

- Cloudflare dashboard → Workers & Pages → `full-count-freshness-watchdog` →
  Logs, or `npx wrangler tail` for a live stream.
- GitHub → Actions → Dashboard Live Update → look for `workflow_dispatch`-
  triggered runs (as opposed to `schedule`-triggered) roughly every 5
  minutes even when the scheduled cron itself is lagging.
- A dispatch failure (bad/expired PAT, GitHub API outage, etc.) is logged
  by the Worker (`console.error`, visible in `wrangler tail`) and does not
  crash silently — see `dispatchWorkflow()`'s own comment for why a non-204
  response is always surfaced, never swallowed.

## Recovery if this breaks

The watchdog failing does not affect the primary `dashboard-live.yml`
schedule at all — it is purely additive. If the Worker itself needs
attention: check `wrangler tail` for the actual error, confirm `GITHUB_PAT`
hasn't expired (fine-grained PATs have a hard expiration, unlike classic
PATs), and re-run `wrangler secret put GITHUB_PAT` with a fresh token if so.

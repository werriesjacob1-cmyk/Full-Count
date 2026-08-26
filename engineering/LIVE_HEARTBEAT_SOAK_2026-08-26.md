# Live Heartbeat Production Soak — 2026-08-26

Started 2026-08-26 ~22:51 UTC, immediately after the Cloudflare Worker
heartbeat (`infra/live-heartbeat/`, merged `main` via PR #69,
`2fcffe6b`) was deployed by Jacob via the Cloudflare dashboard. This
ledger tracks real observed `workflow_dispatch` events against
`dashboard-live.yml`, correlated with `docs/live.json` freshness fields,
to determine when (or whether) the soak-success conditions in the
governing prompt are met. **Status: HEARTBEAT LIVE — SOAK PENDING, not
RESOLVED.** Updated at meaningful checkpoints, not every 5 minutes.

## Source-confidence classification: HIGH-CONFIDENCE CLOUDFLARE

Not "CONFIRMED" — this session has no direct Cloudflare account/API
access (verified via `ListConnectors`, filtered and unfiltered: zero
Cloudflare connectors present). The classification rests entirely on
GitHub-side behavioral evidence:

- **Event type**: every dispatch below is `workflow_dispatch`, not
  `schedule`. This repo's own native GitHub `schedule` trigger for this
  same workflow has been separately, extensively documented (P0 incident
  writeups from earlier this session) as erratic — observed gaps of
  34-136 minutes, never exact 5-minute spacing.
- **Actor**: every dispatch's `actor`/`triggering_actor` is
  `werriesjacob1-cmyk` (the human/PAT-owning account), not
  `github-actions[bot]`. The repo's other automated recovery dispatcher
  (`live-freshness-watchdog.yml`'s `gh workflow run dashboard-live.yml`)
  uses `env: GH_TOKEN: ${{ github.token }}` — the ephemeral Actions
  token — which shows as `github-actions[bot]`, confirmed by reading
  that workflow file directly. A PAT-based dispatch (which is what
  Jacob configured for the Cloudflare Worker's `GITHUB_PAT` secret)
  shows as the PAT owner's own account instead — exactly what's observed.
- **Cadence**: five consecutive dispatches, each exactly 5 minutes apart
  to the second/near-second, matching `infra/live-heartbeat/wrangler.toml`'s
  `crons = ["*/5 * * * *"]` exactly.
- **No plausible alternative explanation**: a human manually clicking
  "Run workflow" five times in a row at exact 5-minute boundaries is not
  credible.

This is the strongest evidence obtainable without direct Cloudflare
account access. It supports HIGH-CONFIDENCE, not CONFIRMED — a real
Cloudflare-side log/dashboard check (Jacob-only, phone) would be the only
way to reach CONFIRMED.

## Dispatch ledger (real, from `mcp__github__actions_list`)

| # | Cron boundary (UTC) | Dispatch created_at | Run ID | Conclusion | Head SHA (main) | Notes |
|---|---|---|---|---|---|---|
| 1 | 22:51 | 2026-08-26T22:51:00Z | 33021038607 | success | 8f59c35f | First confirmed post-deploy dispatch |
| 2 | 22:56 | 2026-08-26T22:56:00Z | 33021378764 | success | 671a284d | Exactly 5:00 after #1 |
| 3 | 23:01 | 2026-08-26T23:01:03Z | 33021736856 | success | eb703150 | +5:03 |
| 4 | 23:06 | 2026-08-26T23:06:00Z | 33022033985 | success | 67ff084a | +4:57 |
| 5 | 23:11 | 2026-08-26T23:11:02Z | 33022312465 | success | aa2d08d1 | +5:02 |

Zero duplicate/coalesced runs observed so far (each dispatch got its own
run, no overlapping in-progress runs colliding) — `dashboard-live.yml`'s
existing `concurrency: {group: dashboard-live-observation,
cancel-in-progress: false}` has not yet been tested under true overlap
since every run so far has completed well within its own 5-minute window
(~40-50s each, per `created_at`→`updated_at`).

## `docs/live.json` freshness correlation

Checked once at 23:11 UTC (5th dispatch):

- `updated_at`: 2026-08-26T23:11:33.660560+00:00
- `prices_checked_at` / `prices_updated_at`: 2026-08-26T23:11:33.660560+00:00
- `grades_checked_at`: 2026-08-26T23:11:16.806173+00:00
- `grades_updated_at`: 2026-08-26T23:11:20.269112+00:00

All four timestamps land within the same ~30s window as dispatch #5
(23:11:02Z run creation, 23:11:33Z file update) — consistent with the
heartbeat successfully driving `dashboard-live.yml` to completion each
cycle, not just successfully dispatching. No MLB games were confirmed
live/in-progress at the times checked so far (offseason-adjacent slate
timing) — the soak has not yet observed a real in-game freshness test,
which is the actual condition that matters most for P0. Continue
checking during an active game window before declaring success.

## Soak-success conditions (from governing prompt) — status so far

| Condition | Status |
|---|---|
| Near-5-min cadence sustained | ✅ so far (5/5 dispatches, all within ±3s of target) |
| No unexplained >15m game-state gap during active games | ⏳ not yet tested — no confirmed live game observed in this window |
| Workflows succeed | ✅ 5/5 `conclusion: success` |
| Duplicates harmless | ⏳ not yet tested — no observed overlap |
| Official finals settle within one observation cycle | ⏳ not yet tested |
| Pricing failures don't block settlement | ⏳ not yet tested |
| No stale-state regression | ✅ so far, `docs/live.json` timestamps tracking dispatches closely |

**Conclusion: too early to call the soak complete.** Continue periodic,
non-wasteful checks (not every 5 minutes) through at least one real
in-game window before considering P0 RESOLVED.

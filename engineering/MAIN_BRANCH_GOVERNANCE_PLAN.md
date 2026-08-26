# Main Branch Governance Plan (design only — no repository setting changed)

Written 2026-08-26. `main` currently has zero branch protection (confirmed
via the GitHub API's `protected: false` field). This document is why that
can't simply be flipped on today, and the direction to head instead.

## What actually writes directly to `main` right now (real, read from the workflow files)

| Workflow | Trigger cadence | Paths it commits |
|---|---|---|
| `dashboard-live.yml` | every 5 min (`*/5 * * * *`) | `docs/live.json` |
| `lineup-watch.yml` | every 10 min (`*/10 * * * *`) | `dashboard/lineup_watch_state.json` |
| `odds-snapshot.yml` | hourly (`0 * * * *`) | `data/odds/*.json`, `data/props/*.json` |
| `dashboard-refresh.yml` | 8x/day (13:00-03:00 UTC in 2hr steps) | `docs/index.html`, `docs/app.css`, `docs/app.js`, `docs/data.json` |
| `mlb-daily.yml` | ~5x/day (14:30, 15:30, 17:00, 20:00, 22:30, 23:30 UTC) | `output/top10_picks_*.md`, `output/picks_*.json`, `output/scratches_*.json`, `output/value_board_*.json`, `output/early_look_*.md`, `output/board_*.html`, `output/full_board_*.html`, `output/parlay_example_*.html`, `results/*.json`, `data/players/*.json`, `output/mlb_daily_*.txt`, `output/run_log_*.json` |
| `dashboard-deploy.yml` | on `workflow_run` (after deploy) | `data/public_top_picks/registry.json` |
| `calibration-recheck.yml` | weekly (Monday 09:00 UTC) | `backtest/calibrators_by_market.json`, `backtest/calibration_recheck_report.json` |

All seven declare `permissions: contents: write` and push straight to
`main` via `git push origin HEAD:main` (or plain `git push`), most with a
fetch-merge-retry loop for push rejections (the same pattern
`dashboard-live.yml`'s "Commit and push live state" step uses, already
reviewed this session). **The most frequent writer alone (`dashboard-live.yml`)
commits up to 288 times/day** when its scheduler behaves — real production
volume, not an edge case.

Separately, `mcp-github-actions-bot`/`autosave` (this session's own
`worktree-autosave.sh`, Part 11/12) is scoped to `refs/heads/autosave/*`,
never `main` — not part of this problem.

## What naive branch protection would break

A standard GitHub branch protection rule (require PR + review, or even
just "require status checks to pass before merging") on `main` would
reject every one of the 7 workflows above on their next push — they all
push directly with no PR, and several (`dashboard-live.yml` especially)
depend on being able to push within seconds of computing a delta, not
after a review cycle. This would not "slow down" the live pipeline, it
would **break it outright** the next time any of these workflows tries to
push.

## GitHub ruleset design that would NOT break this

GitHub's newer **Rulesets** (as opposed to classic branch protection)
support a **bypass list** scoped to specific actors — e.g. "require PR
review, except for these specific GitHub Actions workflows/service
accounts." This is the mechanism, if adopted, that could let human-authored
source PRs require review while the 7 known bots above keep pushing
directly. Whether GitHub's current bypass-list actor-matching is precise
enough to scope to "only these 7 workflows, not any other push" needs to
be verified against current GitHub docs before this is anything more than
a plausible direction — **not verified this session**, flagged honestly
rather than assumed.

## The real fix is architectural, not a GitHub setting

Rulesets with a bypass list is a patch, not the destination. The actual
problem is that **source code and live operational state share one git
branch**, which is exactly what a real code-review gate should never have
to reason around. Two real directions, not mutually exclusive:

1. **Short term**: a Ruleset with the bypass list above — protects human
   source changes without touching the 7 known bots' write paths. Small,
   reversible, no architecture change.
2. **Long term, and the one that actually matters**: generated/operational
   state (`docs/live.json`, `docs/data.json`, `dashboard/lineup_watch_state.json`,
   `data/odds/*`, `data/props/*`, `output/*`, `results/*`,
   `data/public_top_picks/registry.json`, `backtest/calibrators_by_market.json`)
   moves to a **separate data plane** entirely — not committed to `main`
   at all. This is the same direction the eventual Live Brain architecture
   already points toward (small ordered deltas over a real transport,
   not "giant Git commits as the permanent real-time transport" --
   `live_brain/README.md`'s own framing). Once operational state has its
   own storage (R2, a KV store, a Durable Object, or similar), `main`
   becomes exactly what a protected branch should be: source code only,
   reviewed, no bot ever needing to push to it directly.

## Migration path (design only, no step taken)

1. Verify Ruleset bypass-list actor-matching against current GitHub docs
   (not done this session).
2. If it works as expected: add a Ruleset requiring review on `main`,
   bypassed by exactly the 7 workflows above (by their GitHub Actions
   bot identity), nothing else. This alone gets human source changes a
   real review gate without breaking a single existing pipeline.
3. In parallel, as each generated-state consumer is touched anyway
   (matching this session's Live Brain foundation work), migrate its
   write target off `main` and onto whatever the eventual data plane is
   -- one file class at a time, `docs/live.json` first since it's both
   the highest-frequency writer and the one already being redesigned via
   the heartbeat/Live Brain work.
4. Once zero workflows write to `main` directly, drop the bypass list
   entirely -- standard branch protection, no exceptions needed.

## What this document explicitly does NOT do

Change any GitHub repository setting. Enable any protection. Modify any
workflow. This is the design gate before any of that, per the governing
instruction.

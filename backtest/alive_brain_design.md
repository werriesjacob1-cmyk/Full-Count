# Alive-brain sidecar: minimal free-tier design (design only, not deployed)

Grounded in real measurements from this session, not assumption:
`backtest/alive_brain_prototype.py` (one real live game, 5 real cycles) and
`backtest/fanduel_live_observer.py` (sustained multi-game observation). Numbers
below are cited from those runs, not invented.

## What the measurements actually justify

- Detect + selective recompute + delta-serialize: **<2ms combined**, real
  measurement. This is not the bottleneck at any plausible scale — a Worker's
  free-tier CPU-time limit (10ms/request on the free plan) comfortably covers
  it with 5x headroom even before any optimization.
- MLB live-feed fetch: **0.28–0.46s**. FanDuel market-page fetch (2–3 tabs):
  **0.50–1.19s**. These *are* the bottleneck — no runtime choice changes them,
  since they're round trips to someone else's API.
- Delta payload size: **261–391 bytes** per cycle in the real run. Trivially
  small for WebSocket/SSE push.
- Real per-game FanDuel suspend/reopen cadence observed: dozens of state
  transitions across a handful of games in under 30 minutes (see the
  observer's own report) — this is genuinely event-driven, not rare.

**Conclusion the numbers support:** a sidecar's value is in *not polling once
per browser tab* and *pushing deltas instead of full-slate reloads*, not in
beating GitHub Actions on compute speed — compute was never slow.

## Architecture

```
                    ┌─────────────────────────────┐
                    │   Scheduled Worker (Cron)    │
                    │   fires every N seconds       │
                    └──────────────┬────────────────┘
                                   │ triggers
                    ┌──────────────▼────────────────┐
                    │  Durable Object: one per LIVE  │
                    │  MLB game (keyed by game_pk)   │
                    │                                 │
                    │  - polls MLB live feed          │
                    │  - polls FanDuel market pages    │
                    │  - holds prev_game/prev_market   │
                    │    state in DO storage           │
                    │  - diffs -> touched props         │
                    │  - selective recompute (<2ms)     │
                    │  - broadcasts delta to WebSocket   │
                    │    subscribers attached to this DO  │
                    └──────────────┬──────────────────────┘
                                   │ WebSocket push (delta only)
                    ┌──────────────▼──────────────────────┐
                    │   Browser subscribers (N per game)   │
                    │   receive ~300-byte deltas, patch     │
                    │   their own client-side state          │
                    └───────────────────────────────────────┘
```

**One Durable Object per live game**, not per user, not one global object.
Justification: a DO is a single-threaded, strongly-consistent actor with its
own durable storage — exactly the "maintain previous MLB state, maintain
previous FanDuel state" requirement, and per-game isolation means one game's
polling failure or backlog can't stall another game's. At most ~15 concurrent
MLB games exist at once, so this is at most ~15 active DOs during peak
slate — nowhere near needing sharding.

**Centralized polling, never per-user.** The DO is the only thing that ever
calls the MLB/FanDuel APIs. However many browsers are watching a game, the
upstream request count is identical — this is the actual point of the
architecture, not the compute latency.

**Browser subscribers** attach via WebSocket (or SSE as a same-effect
fallback) to their game's DO. They receive only deltas — the same
~300-byte payload shape measured in the prototype — and patch local state,
never re-fetching the full slate on a live update.

## Reconciliation fallback

The existing GitHub Actions 5-minute workflow (`dashboard-live.yml`) is kept
running unchanged, exactly as instructed — it is not replaced. It becomes
the reconciliation path: every 5 minutes it still commits `docs/live.json`
from its own independent fetch. A Worker/DO can (and periodically should)
diff its own accumulated state against the latest committed `live.json` and
self-correct if they've drifted (a DO restart, a missed poll cycle, a bug).
This gives the sidecar a real, working fallback from day one instead of a
single point of failure — if the sidecar goes dark entirely, the GitHub path
still updates the site within 5 minutes, same as today.

## Degraded-mode behavior

- **DO can't reach MLB or FanDuel for N consecutive polls:** mark the game's
  live data `stale` (the same freshness-contract vocabulary already shipped
  this session — CURRENTLY POSTED / STALE-UNKNOWN) and broadcast that state
  to subscribers rather than silently serving old numbers as current.
- **A DO crashes/evicts:** Durable Objects automatically restart on the next
  request; `prev_game`/`prev_market` reload from DO storage, so the diff on
  the next poll is against real prior state, not a false "everything is new"
  burst — this needs its own regression test before any real deploy (not
  written yet; flagged for the build phase, not the design phase).
- **A subscriber's WebSocket drops:** on reconnect, the client re-fetches the
  current full game-state snapshot from the DO once (a real, bounded cost:
  one game's worth of props, not the full slate), then resumes receiving
  deltas — never silently missing a change.

## Rough free-tier budget (Cloudflare Workers, order-of-magnitude only)

- **Requests:** ~15 games × (poll cadence, e.g. every 20–40s per this
  session's observer results) × ~3 upstream calls (MLB + 2 FanDuel tabs) ≈
  a few thousand Worker-triggered upstream requests per day during a full
  slate. Cloudflare's free tier is 100,000 Worker requests/day — an order of
  magnitude of headroom even generously estimated.
- **Durable Object requests/duration:** each poll cycle is one DO invocation
  doing <2s of work (dominated by the two fetches, not compute) — free tier
  historically bundled a limited DO allowance; current pricing should be
  re-checked at build time, not assumed here, since Cloudflare's DO billing
  model has changed more than once.
- **Storage:** each DO's state is one game's live props (~100–200 props,
  the per-game slice of tonight's 1,073), a few KB — trivial against any
  free-tier KV/DO storage limit.
- **WebSocket connections:** bounded by real concurrent viewers, not by
  architecture — not a Workers-specific constraint to budget here.

**This budget is a rough order-of-magnitude sanity check, not a
capacity-planned deployment plan.** The real numbers (current DO pricing,
actual concurrent-viewer counts) need to be pulled at build time before any
actual Cloudflare spend commitment.

## What this design deliberately does NOT do yet

- No live win-probability model — recompute still means "re-price market
  edge against the last known pregame probability," exactly as disclosed in
  the prototype's own docstring. A real live-probability engine is a
  separate, already-flagged research item.
- No production deployment, no Cloudflare account wiring, no migration off
  GitHub Actions for anything currently working. This document is the
  design gate this session was asked to produce before any of that — not an
  implementation.

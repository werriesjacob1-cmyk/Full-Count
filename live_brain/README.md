# Live Brain foundation

Architecture, contracts, fixtures, small pure primitives. **Not a new
prediction formula, not a production deployment** -- nothing here changes
`generate_picks.py`'s scoring, and nothing here is wired into any real
pipeline.

## Read this first: there is already real design work, don't re-derive it

Before writing anything here, this branch found and read:

- `backtest/alive_brain_design.md` -- a real, measurement-grounded
  architecture (Worker Cron -> one Durable Object per live MLB game ->
  WebSocket delta push to browser subscribers), with a real free-tier
  budget, real degraded-mode behavior, and the GitHub Actions pipeline
  explicitly kept as the reconciliation fallback -- this already matches
  what the governing prompt asked this branch to design. **This document
  did that work more rigorously than a from-scratch redesign would have --
  it's grounded in real measurements, not estimates.**
- `backtest/alive_brain_prototype.py` -- a real, working, local proof:
  fetches real MLB live-feed state and real FanDuel market state, diffs
  against the prior observation, selectively recomputes only touched
  props, measures every stage's latency (detect+recompute+serialize:
  **<2ms measured**; MLB fetch: **0.28-0.46s measured**; FanDuel fetch:
  **0.50-1.19s measured**; delta payload: **261-391 bytes measured**).
- `backtest/fanduel_live_observer.py` + `backtest/event_targeted_observer.py`
  + `backtest/fanduel_observer_final_report.md` -- sustained real
  observation of FanDuel's actual market behavior. See the critical
  finding below.

This branch's job is narrower than "design Live Brain": **formalize the
EventEnvelope/DeltaEnvelope contracts that design describes only by
example payload, and write the ordering/monotonicity regression tests
that document explicitly flags as not yet written** ("this needs its own
regression test before any real deploy (not written yet; flagged for the
build phase, not the design phase)").

## The single most important finding for this whole initiative

`backtest/fanduel_observer_final_report.md`, read in full for this branch:
across **three real observation runs and ~130 minutes of combined
monitoring**, **zero confirmed FanDuel odds/line VALUE changes have ever
been observed**. What HAS been observed, extensively (286 real
`market_status_change` events in one 82.6-minute run alone), is
suspend/reopen -- a market going temporarily unavailable and coming back,
tightly correlated with inning rollovers, not with pitcher/batter/scoring
events.

**`alive_brain_design.md`'s own "Conclusion the numbers support" section
reads this suspend/reopen cadence as evidence the system is "genuinely
event-driven, not rare."** That's true for market STATUS -- it is not yet
true for market PRICE, which is the thing a live-probability-adjustment
product would actually need to react to. This is worth stating plainly
rather than let it pass unnoticed: **the infrastructure question
(sections 41-43 of the governing prompt: Workers, Durable Objects, Queues,
transport) is evaluable today; whether there's a real repricing signal
worth building serving infrastructure FOR is still an open research
question**, not yet resolved by any of the three real runs so far. The
`event_targeted_observer.py` report's own next-step recommendation
(target PLAYER PROP markets specifically during a detected trigger
window, not inning-special markets) has not yet been tried. Per section
46 of the governing prompt ("accuracy still outranks the brain"), that
research step is arguably higher-value right now than provisioning any
new infrastructure.

## Source discovery matrix (grounded in what's already proven, not invented)

| Source | Event granularity proven today | Latency (measured) | Reliability (measured) | Rate limits | Sequencing | Cost |
|---|---|---|---|---|---|---|
| MLB Stats API live feed (`statsapi.mlb.com`) | Poll-based state snapshot (inning/outs/score/baserunners/current batter-pitcher/last play type) -- NOT a push/webhook feed | 0.28-0.46s per fetch (`alive_brain_prototype.py`) | High in observed runs; no MLB-side rate-limit failures logged across any of the three real runs | Unofficial/undocumented -- polling cadence self-limited by this codebase's own discipline (20-40s base, burst only on trigger), not a published MLB limit | No stable per-event sequence number found in the fetched payload shape (verified: `fetch_mlb_state()` has no such field) -- ordering must be inferred from poll order, honestly, not assumed | Free, unauthenticated |
| FanDuel unauthenticated market pages (`odds_fanduel.py`) | Market-level status + per-runner odds, tab-scoped (`popular`/`batter-props`/`innings`) | 0.50-1.19s per fetch, 3 tabs (`alive_brain_prototype.py`); phase-tiered p50 0.92-1.12s across 346 real polls (`fanduel_observer_final_report.md`) | 19 failures across 346 polls in the trustworthy run, ALL attributable to a known concluded-game false-positive, not a real reliability problem | Unofficial; this codebase's progressive-cadence health gate (back off to baseline on any failure) is the only limiter in effect, not a published FanDuel limit | No stable event sequence; market/runner identity tracked via `snapshot_event()`'s own migration-detection logic | Free, unauthenticated |
| MLB transactions endpoint (injury/status) | Exists (`mlb_sources.py` already uses it for the backtest return-from-injury signal) | Not measured for LIVE latency -- only ever used in the offline backtest today | Not measured live | Unknown for live polling cadence | Unknown | Free |
| Weather/roof | Exists in the backtest pipeline (`mlb_daily.py`) | Not measured for LIVE latency | Not measured live | Unknown | Unknown | Free/paid depending on provider already in use |

Anything not in this table (a push-based MLB feed, an authenticated
FanDuel partner API, a paid odds-aggregator) is **FUTURE SOURCE
CAPABILITY**, not represented as available -- consistent with the
governing instruction never to fake source capability.

## Data plane vs control plane, and why Queues is a "not needed, and here's the math" conclusion

`alive_brain_design.md` already chose Durable Objects (poll-driven state
holder, one per live game) over any message-queue pattern -- this section
checks that choice with real numbers rather than assuming it.

Cloudflare Queues Free tier: **10,000 operations/day**, where an
operation is every 64KB written/read/deleted (confirmed against current
Cloudflare pricing docs this session). If events were queued individually
instead of held in a DO:

- Peak slate: **15 concurrent MLB games** (30 teams / 2, the real ceiling).
- Real observed status-change rate: **286 events / 96 markets / 82.6 min**
  for 3 games in the trustworthy run -- roughly **69 events/game/hour**,
  so **~207 events per 3-hour game** from market-status activity alone.
- 15 games x ~207 events/game = **~3,100 status-change events in one
  slate**, before counting any game-state (inning/batter/pitcher) events
  on top, and BEFORE ever reaching pitch-level granularity (not sourced
  today at all -- see the matrix above).
- Each queued message costs at least a write + eventual read/delete = up
  to 3 ops -> **~9,300+ ops for status events alone**, already close to
  the entire 10,000/day free budget, with zero margin for game-state
  events, retries, or a busier slate.

**Conclusion: Queues would be tight-to-insufficient at real observed
volumes even before pitch-level granularity exists, which is exactly the
governing prompt's own suspicion.** This confirms, with real numbers, that
`alive_brain_design.md`'s existing choice (Durable Object holds state
directly, no queue in the path) was the right one -- not a reason to add
Queues, a reason this design already avoided it correctly.

## What's actually new in this branch

- `live_brain/envelopes.py` -- `EventEnvelope`/`DeltaEnvelope` as real,
  typed dataclasses, every field's grounding cited back to a real fetcher
  or explicitly marked FUTURE CAPABILITY. `alive_brain_design.md`
  describes the delta shape only by example (`{"ts": ..., "game_state":
  ..., "recomputed": ..., "matchup_affected": ...}`); this formalizes it.
- `live_brain/ordering.py` -- pure primitives (`accept_settlement`,
  `accept_game_state`, `accept_price`, `impact_set`, `apply_delta`,
  `dedupe_events`, `register_candidate_identity`), reusing Full Count's
  EXISTING settlement-authority ranking (`none < live_observation <
  official_final`, already documented in `fc-live-sre.md`) rather than
  inventing a second one.
- `test_live_brain_ordering.py` -- 18 tests, 0.001s, zero I/O, covering
  the specific fixture list the governing prompt asked for: duplicate
  event idempotency, out-of-order (N+1 then N) non-regression, impact
  routing (one game's event never touches another game's state),
  settlement/game-state/price monotonicity, sportsbook-outage
  independence, restart/replay determinism (including out-of-order replay
  convergence), UTC-rollover identity stability, candidate identity
  stability through live updates, alt-line distinctness, and logical
  duplicate detection.

## What this branch deliberately does NOT do

No production implementation of the conditional-probability interface
(section 37 of the governing prompt) -- that's explicitly gated on
research validation, not this foundation pass. No market-refresh
implementation (section 38) -- interface only, and even the interface
isn't written yet; it's next, not here, given the volume of what this
pass already covers. No infrastructure provisioning of any kind. No
change to `generate_picks.py`, `recommendation.py`, any calibrator, or
any live production file.

# FULL COUNT North Star

Written 2026-08-26. **Design/vision only — not a sprint plan.** This
exists so future engineering doesn't accidentally shrink FULL COUNT into
"an MLB picks website with odds." Nothing here is implemented, scheduled,
or authorized to build. Every section below is explicitly CURRENT / NEXT /
FUTURE-RESEARCH — never pretend a FUTURE piece already exists.

## The North Star

FULL COUNT becomes an independent real-time sports pricing and decision
system — not a terminal that displays someone else's market, a system
that maintains its own model of the sporting world and decides when the
market is wrong and the price is actionable. MLB is Brain #1: the first,
and for now only, sport carrying real production weight. Other sports
(NFL, NBA, WNBA, NHL, NCAAF, NCAAB, women's college basketball, soccer,
tennis, golf, combat sports, motorsports, and others) are eventual
candidates, each earning production status through the same evidence
discipline MLB already uses — never launched because they're popular.

**CURRENT**: MLB only, production. **FUTURE**: every other sport listed
above, each independently certified (see "Sport Brain Certification"
below) before any real weight.

## Universal core concept chain (FUTURE — conceptual destination)

```
SPORT EVENT
  -> CANONICAL WORLD STATE
  -> SPORT-SPECIFIC WORLD MODEL
  -> JOINT OUTCOME DISTRIBUTION
  -> THESIS GRAPH
  -> WAGERABLE EXPRESSIONS
  -> FULL COUNT FAIR MARKET
  -> MULTI-BOOK MARKET STREAM
  -> ROBUSTNESS / INFORMATION-RISK GATES
  -> BEST EXPRESSION
  -> DYNAMIC BUY PRICE
  -> ACTIONABLE WINDOW
  -> CUSTOMER ALERT
  -> IMMUTABLE LEDGER
  -> OUT-OF-SAMPLE LEARNING
```

**CURRENT**: FULL COUNT today implements pieces of the middle of this
chain for MLB specifically and directly (per-market probability ->
static BET/PASS recommendation), not the full pipeline. **NEXT**: the
Live Brain foundation (`live_brain/` on `claude/live-brain-foundation-01`)
is the first real step toward WORLD STATE and WAGERABLE EXPRESSIONS being
formally represented, not yet the full chain.

## World Model (FUTURE)

One coherent underlying outcome distribution per player/game should
generate multiple correlated markets, rather than a disconnected model
per prop. MLB example: a single player/game distribution could derive
P(1+ hit), P(2+ hits), P(2+ TB), P(3+ TB), P(HR), P(H+R+RBI threshold),
etc. NFL would derive attempts/targets/receptions/yards/TDs from one
coherent model; NBA/NHL similarly. **No refactor of `generate_picks.py`
implied by this now** — it's a direction, not a rewrite order.

## Thesis + Expression (FUTURE)

Two first-class future concepts: a **THESIS** (the underlying sporting
hypothesis, e.g. "PHI offense materially advantaged versus current
pitching environment") and an **EXPRESSION** (a wagerable market
expressing it — Harper Hit, Harper TB, Turner Hit, PHI team total,
opposing SP Under Outs). A future Best-Expression comparison should
weigh correlated expressions of the same thesis against each other
rather than treat every pick as independent. Not implemented.

## Actionable Windows / Dynamic Buy Price (FUTURE)

Beyond a static BET/PASS label: a maintained dynamic fair price, a
minimum acceptable price, thesis status, freshness, and book
availability. An ACTIONABLE WINDOW opens when the thesis remains intact
AND price is attractive AND data is fresh AND information is complete
enough — closing when any of those stop being true. Example future alert
shape: player/prop, best book, current price, FC fair price, why now,
thesis intact, last verified. Not implemented.

## Robustness and Market Information Risk (FUTURE)

Two future risk controls, kept conceptually distinct:
- **Robustness**: a candidate is stronger when it stays attractive across
  plausible input uncertainty, not just one point estimate.
- **Market information risk**: if several major books move sharply
  against FULL COUNT at once while FULL COUNT's own known inputs haven't
  changed, that is NOT automatically "more edge" — it may mean the market
  knows something FULL COUNT doesn't yet. A future response could be
  HOLD / UNKNOWN INFORMATION / SOURCE RECONCILIATION REQUIRED rather than
  doubling down. This protects against mistaking a missing-information gap
  for alpha.

## Market microstructure research (FUTURE hypotheses, not proven)

Book lead/lag behavior, suspension/reopen behavior, price-change
propagation, event-to-market reaction latency, alpha half-life, stale
quote detection, cross-book inconsistencies, which event classes FULL
COUNT could plausibly process before the market adjusts, and which
sports/markets are more or less efficient. None of these are proven
today — see the Live Brain README's own finding that confirmed FanDuel
prop repricing hasn't yet been observed in ~130 minutes of real
monitoring. This is the actual current research floor, not a settled
premise.

## Shadow alerts before customer alerts (FUTURE)

Any future customer-alert logic runs in SHADOW mode first: record
WOULD_HAVE_ALERTED with timestamp, sport, market, candidate identity, FC
probability, fair price, available price, book, reason, thesis, event
cause, robustness, freshness, market state, eventual result, and
subsequent price movement. Real customer alerts launch only after
out-of-sample evidence supports the policy — same discipline as model
promotion (`AGENTS.md` rule 15).

## Immutable ledger (FUTURE, cryptographic immutability optional)

Every customer-visible recommendation/alert should retain what FULL COUNT
knew, when, what price existed, why it qualified, model/version, data
provenance, how long the window stayed open, why it closed, and the
result. Losses stay visible; no rewriting history. Cryptographic
immutability (extending the existing hash-chained publication-events work)
can stay future scope, not required for the ledger concept itself.

## Sport Brain Certification (FUTURE process)

No new sport launches because it's popular. Every Sport Brain earns
production status through the same evidence bar MLB already applies:
predictive validity, equal-volume realized performance, data integrity,
market identity, freshness, lifecycle correctness, settlement
correctness, replayability, alert reliability. States: RESEARCH -> SHADOW
-> CERTIFIED. **MLB is the only CERTIFIED brain today.**

## Positioning vs. commodity odds terminals (product principle, not a task)

First become operationally credible enough that an MLB prop user doesn't
sacrifice freshness, multi-book pricing, market coverage, reliability,
notifications, price history, or tracking relative to serious existing
products (use legitimate/licensed commodity market data where that's the
economical choice) — but don't spend years re-cloning that commodity
layer. Spend proprietary engineering effort on what a terminal can't be:
independent sport models, World State, the Thesis Engine, Best
Expression, Actionable Windows, live causal intelligence, robustness,
information risk, portfolio/correlation awareness, and the research
ledger. A terminal shows you the market. FULL COUNT's job is deciding
when the market is wrong.

## Portfolio / correlation awareness (FUTURE)

Several picks may express one underlying thesis (same game, team,
player, event, game script, weather). A future system should recognize
BEST EXPRESSION and THESIS CONCENTRATION rather than present five
correlated bets as five independent alpha discoveries. No customer
staking/sizing recommendation is implied by this note — that's out of
scope even for the future vision, not just for now.

## Market data source neutrality (architectural constraint, effective now)

The canonical future market contract (`MarketQuote`/`MarketEvent`) must
be able to represent: book, sport, league, game/event identity,
player/participant identity, market, line, side, price, market status,
source market id, source timestamp, observed timestamp,
available/suspended/reopened/closed, alternate-line identity, and
source/provenance. **FanDuel must never be hard-coded as the permanent
market authority** in anything built going forward — it's today's only
wired source, not an assumed permanent one. Potential future inputs:
licensed multi-book odds APIs, licensed streaming feeds, sportsbook
partner APIs, other authorized providers. Personal sportsbook account
scraping is explicitly NOT the canonical path — if ever supported, it
would be a separately authorized, permission-limited adapter, never the
backbone, and never automated against a real account without that
account's operator explicitly permitting the access mechanism.

## What this document is not

Not a sprint plan, not an authorization to build any of the above, not a
claim that any FUTURE section already exists. The only things CURRENT
today: MLB certified, the existing static per-prop pipeline, and the
Live Brain foundation's formal contracts (envelopes + ordering
primitives) as the first small step toward WORLD STATE being real. Get
the P0 heartbeat live and proven first — this document is deliberately
not urgent.

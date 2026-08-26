# Multi-Sport Data Source Matrix

Written 2026-08-26. **Data architecture research only — no model design, no
implementation, no sport launch authorized.** Organized per-sport
(transposed from this session's provider-oriented research) so a future
Sport Brain Certification pass (`FULL_COUNT_NORTH_STAR.md`) has a real
starting point for "what data would this sport even need." MLB remains
the only CERTIFIED brain; nothing here changes that.

Every sport needs the same two conceptually-distinct inputs (per the
North Star's market-data/sport-world-state separation): **market data**
(odds/lines/prices across books) and **sport-world-state** (live scores,
play-by-play, box scores, player/game state). A provider winning one
doesn't automatically win the other. Confidence markers: **[PRIMARY]** =
confirmed via direct fetch of the provider's own page this session;
**[SECONDARY]** = third-party-sourced, not independently confirmed;
**UNVERIFIED** = flagged explicitly, do not treat as fact.

## MLB (CURRENT — the only CERTIFIED brain)

- **World-state (already in production)**: MLB Stats API — already
  wired, not re-researched this session, out of scope for this matrix.
- **Market data (current)**: homemade FanDuel-only observer — has NOT
  yet confirmed a single real price change in ~130 min of observation
  (per `live_brain/README.md`'s existing finding). Single-book, not
  licensed/API-based.
- **Market data (researched alternatives)**: SportsGameOdds **[PRIMARY]**
  confirms MLB coverage at all paid tiers with exact pricing (Free $0,
  Rookie $99/mo, Pro $299/mo, All-Star custom w/ WebSocket). The Odds API
  **[PRIMARY]** confirms MLB + named US books (FanDuel, DraftKings,
  BetMGM, Caesars, +others) but REST-polling only, no push at any tier.
  Sportradar/Genius Sports/OpticOdds/Goalserve/SportsDataIO all claim MLB
  coverage; none disclose MLB-specific pricing or confirm real-time
  mechanism with certainty (see `MULTISPORT_DATA_SOURCE_MATRIX` provider
  research notes below).
- **World-state alternatives**: SportsDataIO **[PRIMARY]** has a
  dedicated MLB product page; Sportradar and Goalserve both claim MLB
  play-by-play + boxscore depth **[SECONDARY]**.

## NFL (FUTURE — not certified)

- **Market data**: The Odds API **[PRIMARY]** confirms NFL + full US book
  list. SportsGameOdds **[PRIMARY]** confirms NFL at all tiers. OddsJam,
  Sportradar, Genius Sports, OpticOdds, Goalserve all claim NFL coverage
  **[SECONDARY]**; Genius Sports specifically names NFL as a flagship
  official-data partnership **[SECONDARY]**.
- **World-state**: Sportradar **[SECONDARY]** claims full play-by-play,
  "every game." SportsDataIO **[PRIMARY]** has a dedicated NFL product.
  Goalserve **[PRIMARY]** bundles NFL in its "USA Sports" package with
  live player stats + play-by-play.

## NBA / WNBA (FUTURE — not certified)

- **Market data**: The Odds API and SportsGameOdds both **[PRIMARY]**
  confirm NBA coverage with exact pricing. WNBA coverage specifically was
  **not separately confirmed** for any market-data provider — only
  general "NBA" was found; treat WNBA market-data availability as
  UNVERIFIED for every provider researched.
- **World-state**: Sportradar **[SECONDARY]** claims NBA, NCAAMB, NCAAWB
  coverage. SportsDataIO **[PRIMARY]** confirms NBA, WNBA, and
  **NCAA Women's Basketball specifically** (dedicated product +
  workflow-guide pages found directly) — this is the strongest women's
  basketball evidence found in this research pass, worth noting since
  women's college basketball was explicitly in scope. Goalserve claims
  general "Basketball" coverage **[SECONDARY]**, WNBA not separately
  confirmed.

## NHL (FUTURE — not certified)

- **Market data**: SportsGameOdds and The Odds API both **[PRIMARY]**
  confirm NHL. Sportradar/Genius Sports/OpticOdds/Goalserve claim NHL
  **[SECONDARY]**.
- **World-state**: Sportradar **[SECONDARY]** claims NHL play-by-play.
  Goalserve **[PRIMARY]** bundles NHL in "USA Sports." SportsDataIO
  **[PRIMARY]** has a dedicated NHL product page.

## NCAAF / NCAAB (FUTURE — not certified)

- **Market data**: SportsGameOdds **[PRIMARY]** confirms broad college
  coverage at Pro/All-Star tiers (53+ leagues total, not itemized).
  OddsJam claims NCAAF/college coverage **[SECONDARY]**.
- **World-state**: Sportradar **[SECONDARY]** explicitly names NCAAMB
  and NCAAWB as separate confirmed products. SportsDataIO **[PRIMARY]**
  confirms NCAAF, NCAAB, and NCAA Women's Basketball as dedicated
  products — the strongest evidence in this matrix for NCAA coverage
  specifically.

## Soccer (FUTURE — not certified)

- **Market data**: All eight providers researched claim soccer coverage
  in some form; The Odds API **[PRIMARY]** confirms EPL-relevant UK/EU
  book coverage explicitly. Genius Sports names EPL/Serie A as flagship
  official partnerships **[SECONDARY]** — strongest official-rights
  claim of any provider for this sport.
- **World-state**: Sportradar and Goalserve both **[SECONDARY/PRIMARY
  mixed]** claim broad soccer live-data coverage; Goalserve confirms
  soccer as a standalone product on its own pricing page **[PRIMARY]**.

## Tennis (FUTURE — not certified)

- **Market data**: OddsJam and Genius Sports both claim tennis coverage
  **[SECONDARY]**; Genius Sports specifically names ITF/Australian Open
  ("BetVision") as an official partnership **[SECONDARY]**.
- **World-state**: Sportradar claims tennis coverage **[SECONDARY]**;
  Goalserve confirms tennis as a standalone product **[PRIMARY]**.
  SportsDataIO tennis coverage claimed but not confirmed via direct
  fetch in this pass — UNVERIFIED.

## Golf (FUTURE — not certified)

- **Market data**: OddsJam claims golf/PGA coverage **[SECONDARY]**.
  SportsGameOdds' league list includes "PGA" per a search snippet
  **[SECONDARY, not primary-confirmed]**.
- **World-state**: SportsDataIO **[PRIMARY]** has a dedicated PGA Golf
  product. Goalserve **[PRIMARY]** confirms golf as a standalone product.
  Sportradar claims golf coverage **[SECONDARY]**.

## Combat sports (boxing/MMA) — FUTURE, weakest evidence found

- **Market data**: OddsJam claims MMA/UFC **[SECONDARY]**; no provider
  confirmed boxing market-data coverage specifically.
- **World-state**: Sportradar and SportsDataIO both confirm MMA/UFC
  coverage (Sportradar names "Contender Series" specifically)
  **[SECONDARY]**, but **neither confirmed boxing coverage** — both were
  searched for specifically and no dedicated boxing product page was
  found for either. Goalserve MMA claim rests on a single weak search
  snippet, not a dedicated product page — treat as UNVERIFIED. **Boxing
  specifically is the single weakest-evidenced sport in this entire
  matrix** — if boxing were ever pursued, this would need real
  verification before any assumption of coverage.

## Motorsports (F1, NASCAR) — FUTURE

- **Market data**: Not specifically confirmed for any provider beyond
  general "broad sport" marketing claims **[SECONDARY]**.
- **World-state**: Sportradar **[SECONDARY]** and Goalserve **[PRIMARY]**
  both confirm NASCAR and Formula 1 as named, standalone products — the
  best-evidenced pairing in the combat/motorsports group.

## Cross-cutting notes (apply to every future sport, not sport-specific)

- **No sportsbook (FanDuel, DraftKings, BetMGM, Caesars, Fanatics,
  bet365, ESPN BET) has a confirmed public first-party developer API for
  odds data, for any sport.** This was checked directly this session —
  the honest finding is uniform across all seven books: access exists
  only via private enterprise/affiliate B2B contracts or third-party
  aggregators, never a public self-serve API. This applies identically
  regardless of which sport is being considered.
- **Real-time push is not the default state of most researched
  providers.** Only SportsGameOdds confirmed WebSocket at a disclosed
  price point (top custom tier); OpticOdds offers SSE (not WebSocket);
  Goalserve's real-time is a separately-priced add-on bolted onto a
  polling base; The Odds API confirmed **no** real-time push at any
  tier. This matters for every future sport equally — "the provider
  claims real-time" needs the same skepticism this session already
  applied to the current FanDuel observer's unconfirmed repricing.
- **Enterprise-tier pricing (Sportradar, Genius Sports) is genuinely
  opaque** for every sport — no per-sport pricing breakdown exists
  publicly for either. Evaluating either seriously would require a real
  sales conversation, not more public research, regardless of which
  sport is in scope.
- Full provider-by-provider technical detail (exact tiers, exact object/
  request limits, exact sportsbook lists, exact confidence markers) lives
  in this session's research agent transcript and is the source for every
  claim above — this file is the per-sport transposition of that
  research, not a duplicate of it.

## What this document is not

Not a decision to build any sport beyond MLB. Not a data-provider
selection. Not a claim that any of the above data is currently wired
into FULL COUNT — none of it is. A future sport still needs to pass
Sport Brain Certification (`FULL_COUNT_NORTH_STAR.md`) regardless of
what data source research says is theoretically available.

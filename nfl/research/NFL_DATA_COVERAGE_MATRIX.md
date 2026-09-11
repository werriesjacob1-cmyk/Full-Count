# NFL DATA COVERAGE MATRIX — P0

**Purpose:** define exactly what FULL COUNT can know, when it can know it, how it
can reconstruct it historically, and which model layers may consume it.

**Status date:** 2026-09-11  
**Scope:** research + evidence design only. No production scorer or pick authority.

## Hard temporal rule

A feature is admissible at cutoff T only if the underlying information was
actually observable by FULL COUNT at or before T.

Each future normalized observation must preserve at least:

- `effective_at`: when the football/world fact became true, if knowable
- `observed_at`: when FULL COUNT first observed it
- source identity / URL / artifact
- source-provided timestamp where available
- extraction/parser version
- supersession / contradiction links where applicable

For live prediction, `observed_at` governs admissibility. A later historical
file describing an earlier event is NOT retroactively knowable.

---

## A. FOUNDATION DATA — CURRENTLY STRONG

| Data family | Historical depth / source | In-season latency | PIT reconstructability | Current FULL COUNT state | Primary modeling use |
|---|---|---|---|---|---|
| Play-by-play | nflverse, 1999+ | nightly after game day; raw PBP usually available shortly after games | HIGH for prior games | source selected; normalized research ingest not built yet | plays, dropbacks, carries, targets, air yards, RZ/goal-line opportunity, game script |
| Player weekly stats | nflverse, 1999+ | same schedule as PBP | HIGH for completed prior weeks | source selected | baseline production, opportunity, outcomes |
| Team weekly stats | nflverse, 1999+ | same schedule as PBP | HIGH | source selected | pace, pass/rush environment, team opportunity |
| Schedules/game state | nflverse + official/ESPN archive | minutes in-season | HIGH when archived | live raw capture exists | opponent, venue, kickoff, rest, game state |
| Rosters | nflverse; long history | daily | MEDIUM-HIGH | live archive has roster-changing official sources; normalized roster state not built | player-team identity, role availability |
| Weekly rosters | nflverse, 2002+ | periodic/in-season | HIGH for released vintages | research source identified | historical player-team status |
| Depth charts | nflverse 2001+; timestamped snapshots from 2025 onward | daily | HIGH from 2025+ because snapshots append with timestamp | not yet ingested into FULL COUNT raw archive | role priors, backup hierarchy, injury reallocation |
| Next Gen weekly player stats | NFL NGS via nflverse, 2016+ | nightly | HIGH for completed weeks | source identified | efficiency/movement proxies for QB/RB/WR |
| Snap counts | PFR-derived nflverse | every ~6h in season | HIGH for completed games | source identified | role/opportunity, return-from-injury ramp |
| PFR advanced stats via nflverse | historical | daily | HIGH for completed games/weeks | source identified; terms/provenance must remain source-aware | pressure/receiving/rushing contextual features |
| FTN charting via nflverse | 2022+ | docs target every 0/6/12/18 UTC; upstream availability governs | HIGH for seasons/weeks actually published before cutoff | source identified; current-season availability must be verified continuously | motion, play action, screen, RPO, blitz/pass rush, catchability, QB context |

### Verified nflverse cadence

The nflverse availability schedule currently states:

- PBP and player/team stats: nightly after game day, plus additional game-day updates.
- FTN charting: scheduled every 6 hours during season, subject to FTN availability.
- roster data: daily.
- NGS weekly player stats: nightly during season.
- snap counts: every 6 hours during season.
- PFR advanced stats: daily.
- depth charts: daily; 2025+ updates are timestamped/appended.
- participation from 2023 onward: **post-season only; not in-season**.
- nflverse injury source: died after 2024; no dependable current-season injury feed should be assumed from it.

Those schedules are source behavior, not guarantees. FULL COUNT must record actual
retrieval/availability per vintage rather than infer success from documentation.

---

## B. LIVE/PREGAME DATA — CURRENT FULL COUNT ARCHIVE

| Data family | Current source | Authority | PIT live value | Current state |
|---|---|---|---|---|
| FanDuel prop prices/markets | FanDuel public sportsbook web feed | sportsbook itself | VERY HIGH | raw capture exists; silent-default ambiguity now represented as PARTIAL; dynamic tab discovery added |
| Official practice/injury page | NFL.com injuries | authoritative league publication | VERY HIGH | raw HTML archived, unparsed |
| Transactions | NFL.com transactions | authoritative league publication | HIGH | raw HTML archived |
| Schedule/scores/standings | NFL.com | authoritative | MEDIUM | raw HTML archived |
| Injury aggregation | ESPN public feed | aggregator, not authoritative | HIGH | raw payload archived with source timestamp |
| Venue/game summary | ESPN public feed | secondary | HIGH | raw payload archived |
| HC/OC/DC staff identity | Wikipedia MediaWiki staff templates | public secondary, CC BY-SA | HIGH for regime identity | current 32-team batched capture + historical revision reconstruction |
| Weather forecast vintages | NWS | authoritative US forecast | HIGH | source module exists but is BLOCKED on verified venue coordinates |
| Media discovery | NFL.com indexes | authoritative discovery | MEDIUM | league-level index archived; team-level media/transcripts not yet acquired |
| Official inactives | unresolved | must be authoritative | EXTREMELY HIGH | OPEN GAP |
| Actual offensive/defensive play caller | media evidence + staff context required | source-by-source | VERY HIGH | OPEN GAP; title is NOT treated as duty |
| Beat/practice observations | credible beat/local sources | secondary | HIGH | OPEN GAP / future Intelligence Engine |
| Contracts/holdouts/incentives | official transactions/team reports/credible contract sources | mixed | MEDIUM-HIGH when mechanism affects role | OPEN GAP |
| Market movement history | FULL COUNT FanDuel vintages | primary market observation | VERY HIGH | raw capture started; prospective only |
| Public betting percentages | no trustworthy FULL COUNT source selected | market context | UNKNOWN | OPEN / do not fabricate |

---

## C. THE BIGGEST NFL DATA HOLE: IN-SEASON PARTICIPATION / ALIGNMENT

The public nflverse `pbp_participation` file from 2023 onward is delivered only
after the postseason. It must NEVER be used as if it existed during that same
season.

This blocks a large part of the mechanistic matchup layer in-season:

- per-play personnel grouping
- offensive formation
- receiver alignment
- route-level participation where participation is the only source
- coverage shell/man-zone fields carried only in post-season participation
- box/front/alignment combinations that rely on that dataset

### Required response

Do NOT silently replace these with season-end participation during a 2026 backtest.

P0 sourcing research must look for legitimate in-season alternatives in this order:

1. freely licensed / public structured charting
2. official APIs or feeds
3. permitted paid/licensed data whose incremental value justifies cost
4. structured inference from PBP/NGS/FTN where the mapping is explicit and measurable
5. manual/film research only where automation rights are unclear

Every substitute must declare WHICH concepts it truly measures. A proxy cannot
quietly inherit the name of the unavailable variable.

---

## D. MODEL-LAYER READINESS

### Ready for historical research now, once normalized ingestion exists

- game environment
- team play volume
- dropbacks / attempts
- rush volume
- target/carry opportunity
- air yards / aDOT
- red-zone and goal-to-go opportunities
- goal-line carry share
- designed QB runs / sneaks from PBP where identifiable
- prior-week snap share
- score-dependent play calling
- basic rest/schedule effects
- NGS weekly efficiency variables
- market-independent football baselines

### Ready only prospectively / requires FULL COUNT archive

- FanDuel exact line + exact price at cutoff
- line movement between capture vintages
- Wednesday/Thursday/Friday practice state as observed
- coach/player statement evidence
- contradictions across sources
- source completeness / information risk
- exact official inactive state unless authoritative acquisition is solved
- weather forecast vintage once venue coordinates are verified
- play-caller claims / handovers
- beat-reporter practice observations
- contract/role news

### Not admissible in-season today without new source

- current-season post-2023 participation data that has not yet been released
- any same-season field first published only after postseason
- paywalled charting obtained without an authorized license
- final weather used as a substitute for forecast-at-cutoff
- end-of-week/end-of-season depth state substituted for earlier vintages

---

## E. P0 DATA ACQUISITION TODO

1. Build verified venue ID → latitude/longitude → roof/surface table from authoritative/public sources.
2. Identify authoritative machine-readable or reliably archivable official inactive source.
3. Add nflverse depth-chart vintages to prospective raw archive.
4. Add nflverse roster/snap/NGS/FTN availability manifests with observed timestamps.
5. Build a source-health check for expected current-season FTN release; no file == explicit unavailable/stale, not zero.
6. Build actual play-caller evidence registry.
7. Build 32-team official media index registry and per-team terms/access review.
8. Determine legitimate press-conference transcript/caption path; preserve evidence, not generated summaries.
9. Investigate in-season participation/alignment alternatives and price any licensed option separately.
10. Build social/competitor intelligence as a separate evidence estate; never mix competitor claims with football truth.

---

## F. FIRST SCORING MODEL DATA GATE

The first research scorer may start before every gap is closed, but it must use
ONLY feature families with honest historical/PIT availability.

Version 0 should therefore begin from:

1. game environment
2. team opportunity
3. player opportunity
4. stable prior efficiency
5. scoring opportunity
6. historical coaching-regime variables that can actually be reconstructed
7. market state ONLY in a separate market-informed challenger

It must NOT pretend that currently unavailable film/participation/intelligence
features are zero. They are absent, and their absence is part of the research
state.

The first benchmark question is not “can we make picks?”

It is:

**Can a football-only outcome model improve held-out distribution accuracy, and
does a market-informed residual layer add or remove information without merely
copying the sportsbook?**

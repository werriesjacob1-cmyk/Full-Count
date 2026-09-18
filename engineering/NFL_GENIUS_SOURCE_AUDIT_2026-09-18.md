# NFL Genius Source Audit Packet #1 — Public Multi-Year Foundations

Date: 2026-09-18

Purpose: verify high-value public NFL data sources before Claude implementation. This document records source capability, historical depth, live-use semantics, and critical limitations. It does not promote any signal or authorize purchases.

## 1. nflverse depth charts

Authoritative docs:
- https://nflreadr.nflverse.com/reference/load_depth_charts.html
- https://nflreadr.nflverse.com/articles/dictionary_depth_charts.html
- https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html

Verified:
- Data available back to 2001.
- Through 2024, source semantics are team/week depth charts.
- Source changed after the 2024 season.
- From 2025 onward, records include an ISO8601 `dt` load timestamp.
- 2025+ updates append new timestamped snapshots rather than assigning one static week.
- Depth chart data updates daily.

Implication:
This is substantially more useful for 2025+ point-in-time role/depth state than the prior roadmap assumed. We should build a versioned adapter with separate pre-2025 and 2025+ schemas.

Do not assume:
- depth rank equals expected snap share;
- current depth chart proves historical state unless the correct timestamp/week snapshot is used;
- a player listed first will necessarily start.

## 2. nflverse weekly rosters

Authoritative docs:
- https://nflreadr.nflverse.com/reference/load_rosters_weekly.html
- https://nflreadr.nflverse.com/reference/load_rosters.html

Verified:
- Weekly roster data available back to 2002.
- Season-level roster data is available much deeper, but should not substitute for week-level state in historical prediction work.
- Weekly roster rows contain status and stable cross-source ids.

Implication:
Use weekly rosters as a long-horizon identity/status substrate. They can help reconstruct player-team membership and role availability, but exact field semantics and historical update timing still need a file-level audit.

## 3. nflverse participation

Authoritative docs:
- https://nflreadr.nflverse.com/reference/load_participation.html
- https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html

Verified:
- Available from 2016 onward.
- Data prior to 2023 comes from NFL Next Gen Stats.
- 2023 onward comes from FTN.
- 2023+ participation is released only after all postseason games are complete.
- Therefore current-season participation is NOT a live 2026 role signal under the current source policy.

Implication:
Excellent historical research substrate for formation/box count/on-field participation, but cannot be used to determine current-week 2026 participation. Prospective role models must rely on other live sources such as depth charts, snap counts from already completed games, official inactives, rosters, and market/news evidence.

This distinction is mandatory in model code.

## 4. PFR snap counts via nflverse

Authoritative docs:
- https://nflreadr.nflverse.com/reference/load_snap_counts.html
- https://nflreadr.nflverse.com/articles/dictionary_snap_counts.html
- https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html

Verified:
- Game-level snap counts available from 2012 onward.
- Current-season source updates multiple times daily.
- Fields include offense, defense, and special-teams snap counts and percentages.

Implication:
Strong strictly-prior role/continuity input after games are completed.

Do not assume:
- current-game snaps are known pregame;
- PFR historical files are a timestamped vintage archive;
- 2012 is complete enough for every position without a separate coverage audit.

## 5. NFL Next Gen Stats weekly player metrics via nflverse

Authoritative docs:
- https://nflreadr.nflverse.com/reference/load_nextgen_stats.html
- https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html

Verified:
- Weekly passing, receiving, and rushing NGS player metrics begin in 2016.
- Current-season data updates nightly.
- NGS only publishes rows for players above minimum pass/rush/receive attempt thresholds.
- Rushing data includes expected rushing yards-related fields.

Implication:
This is a major public multi-year intelligence source and should be separated from the future/raw-tracking lane. We can research NGS-derived player efficiency now without pretending we possess raw coordinates.

Critical modeling rule:
Missing row != zero. Below-threshold players must remain missing/unknown.

## 6. PFR advanced stats via nflverse

Authoritative docs:
- https://nflreadr.nflverse.com/reference/load_pfr_advstats.html

Verified:
- Advanced player stats available from 2018 onward.
- Supports pass, rush, receive, and defense stat families.
- Weekly summaries exist where supported.
- Passing fields include drops, bad throws, sacks, blitzes, hurries, hits, pressures and pressure rate.

Implication:
This can supply multi-year pressure/blitz/QB-response research before any paid charting decision.

Limit:
It is retrospective completed-game data, not raw tracking and not a historical pregame snapshot.

## 7. nflverse officials

Authoritative docs:
- https://nflreadr.nflverse.com/reference/load_officials.html

Verified:
- Assignments available from 2015 onward.
- One row per game per official.
- Includes official name/id, role, season and week.

Implication:
We have enough multi-year assignment history to test referee/crew hypotheses.

Critical unresolved detail:
Historical assignment rows prove who officiated the game, not necessarily when the upcoming assignment became publicly knowable. A pregame officials feature requires a current assignment publication-time source or a documented publication schedule.

Therefore:
- historical crew-effect research is allowed;
- prospective use must preserve assignment announcement timing;
- skeptical shrinkage required.

## 8. nflverse injury feed

Authoritative docs:
- https://nflreadr.nflverse.com/reference/load_injuries.html
- https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html

Verified:
- Loader documents historical injury-report availability since 2009.
- Current nflverse data schedule explicitly states the injury data source died after the 2024 season.
- It states no 2025 data and no ETA for restoration.

Important discrepancy:
nflverse release metadata may still list an `injuries` release. Release existence must not be confused with a functioning current-season feed.

Implication:
- audit/use historical 2009-2024 injury reports;
- DO NOT wire nflverse injuries as a 2026 live source;
- use official inactive capture and locate a separate timestamped practice-report source for current weeks.

## 9. coaching / coordinators / playcallers

Verified public baseline:
- Pro-Football-Reference season/team pages expose head coach, offensive coordinator, defensive coordinator, scheme/alignment and some assistant roles.
- Example season pages can reconstruct much of HC/OC/DC history.

Important limitation:
Coordinator title != playcaller.

Contemporary NFL reporting can document explicit playcalling changes. Example: NFL.com reported Sean Payton handing primary offensive playcalling to Davis Webb for 2026.

Implication:
Build two related datasets:
1. broad season-level HC/OC/DC/scheme history from stable team-season sources;
2. higher-confidence dated playcaller intervals from official/team/NFL reporting.

Never infer playcaller solely from coordinator title.

## 10. immediate implementation consequences

Highest-confidence no-cost source opportunities now:

1. 2001+ depth-chart history with a 2025+ timestamped schema.
2. 2002+ weekly roster state.
3. 2012+ snap history.
4. 2015+ officials assignment history.
5. 2016+ participation historical research.
6. 2016+ NGS weekly player metrics.
7. 2018+ PFR advanced player metrics.
8. 2009-2024 historical injury reports, but not live 2026.
9. long-horizon PBP/weekly player/team stats already under existing audits.

## 11. source-age vs model-age

Different signal families begin in different seasons. The model must not force a common start year.

Examples:
- weekly player/team history can go very deep;
- depth chart: 2001+;
- weekly roster: 2002+;
- injury: 2009-2024 historical;
- snaps: 2012+;
- officials: 2015+;
- NGS: 2016+;
- participation: 2016+;
- PFR advanced: 2018+;
- prospective FanDuel: only from our actual captured evidence forward.

Each challenger should use the deepest defensible population available for its own feature family and report the resulting eligible population explicitly.

## 12. next source audits

Next packets should resolve:

- exact 2025/2026 depth-chart schema and identity completeness;
- route-level information available in participation/FTN fields;
- current 2026 practice/injury report source;
- weather forecast historical-vintage options;
- stadium/roof/surface effective-date history;
- coach/playcaller historical reconstruction method;
- NGS dictionaries by passing/receiving/rushing family;
- PFR advanced definitions and missingness thresholds;
- current official assignment publication timing;
- public route/coverage/charting sources before any commercial purchase.

Alligator

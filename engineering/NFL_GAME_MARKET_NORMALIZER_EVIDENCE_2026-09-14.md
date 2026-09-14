# NFL full-game market normalization evidence — 2026-09-14

Research/infrastructure only. This document does not claim a predictive edge or public pick readiness.

## Source fixture

The normalizer contract was derived from the retained raw FanDuel root payload in the successful 2026-09-14 DEN-KC prospective research artifact (workflow run `34906529900`, exact code SHA `4c7289e9bb9a019f290a4be76c42efdb8ecefcb3`).

Observed event: `35601246`, Denver Broncos at Kansas City Chiefs, market time `2026-09-15T00:15:00.000Z`.

Observed primary full-game markets:

- Moneyline — market `734.168694353`, source type `MONEY_LINE`: DEN +116, KC -136.
- Spread — market `734.168694354`, source type `MATCH_HANDICAP_(2-WAY)`: DEN +2.5 -115, KC -2.5 -105.
- Total Points — market `734.168694356`, source type `TOTAL_POINTS_(OVER/UNDER)`: 43.5, OVER -102, UNDER -120.

## Contract

`nfl/normalize/game_markets.py`:

- recognizes only those three exact primary source market types;
- requires OPEN and not-in-play state;
- requires exactly two ACTIVE runners;
- requires AWAY/HOME identity for moneyline/spread;
- requires opposite spread handicaps;
- requires OVER/UNDER identity and an identical total line on both runners;
- preserves event ID, market ID, market time, capture timestamp and source-payload digest;
- excludes duplicate primary markets for an event/canonical market and records an explicit failure;
- ignores alternates and unrecognized market types rather than silently treating them as primary.

## Explicit non-capabilities

This branch does **not** provide:

- a spread, total, or moneyline probability model;
- historical sportsbook price vintages;
- a validated selector;
- grading activation;
- public eligibility;
- any claim that the observed DEN-KC spread/total/moneyline has betting value.

The next scientific layer is a leakage-safe team/game baseline plus a prospective grader; normalization alone must not be mistaken for prediction.

Alligator

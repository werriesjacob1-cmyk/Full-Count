# NFL Genius Source Audit Packet #2 — Current Injury + Weather Vintages

Date: 2026-09-18

Purpose: close two high-risk leakage gaps before implementation: current-week official injury/practice information and weather forecasts.

## A. Official NFL injury/practice reports

Verified public source:
- https://www.nfl.com/injuries/

Observed current-season fields include:
- player
- position
- injury
- practice status
- game status

Practice-status vocabulary includes full, limited, and did-not-participate states. Game status can include designations such as Out or Questionable.

Why this matters:
The existing nflverse historical injury feed is not a functioning 2026 live source. NFL.com itself provides the official current weekly injury/practice surface, so the prospective system can capture the live source directly instead of waiting on nflverse restoration.

Required prospective capture behavior:
- capture each defined weekly clock rather than overwrite latest state;
- preserve source URL, observed_at, raw payload/page digest and target game/week identity;
- preserve practice status separately from game status;
- preserve injury/body-part text;
- retain status transitions such as DNP -> LP -> FP;
- join by stable player identity only after explicit identity resolution;
- keep official game-day inactives as a separate later source.

Historical limitation:
Systematic historical archive depth of the NFL.com injury surface is not yet certified. Until audited, use this as PROSPECTIVE_ONLY for live 2026 intelligence. Historical injury research can use nflverse's older 2009-2024 data after its own audit, but source semantics must remain separate.

## B. Leakage-safe weather

Primary candidate:
- https://open-meteo.com/en/docs/historical-forecast-api
- https://open-meteo.com/en/docs/previous-runs-api
- https://open-meteo.com/en/docs/single-runs-api

Verified capabilities:

### Historical Forecast
- archived weather forecasts from roughly 2021/2022 onward;
- same response variable structure as live Forecast API;
- creates a continuous hourly series from archived model forecasts.

Use:
broad recent historical forecast-context research.

Caveat:
A stitched historical-forecast series is not the same thing as the exact full forecast run a bettor saw at a specified decision time.

### Previous Runs
- fixed lead-time forecast values from 1 to 7 days before valid time;
- most model archive coverage begins around January 2024;
- selected variables/models can have deeper history.

Use:
excellent controlled experiments such as:
"What did the forecast say 48 hours before kickoff?"

This is highly useful for FULL COUNT's defined weekly decision clocks.

### Single Runs
- retrieves complete forecast horizon by exact model initialization time;
- ECMWF IFS HRES archive from March 2024;
- most other individual model-run archives from April 2026.

Use:
best source for reproducing the exact forecast run available at a specific 2026 decision time.

## C. Weather clocks FULL COUNT should freeze

For each outdoor/retractable-roof game where weather can matter:

1. early-week baseline
2. 72h
3. 48h
4. 24h
5. Sunday morning / game-day morning
6. after roof decision if available
7. final pregame freeze

Each weather snapshot should include:
- stadium id
- latitude/longitude
- stadium timezone
- scheduled kickoff
- roof type
- roof status if known
- forecast provider/model
- model run initialization
- valid forecast time
- lead time in hours
- temp
- apparent temp
- sustained wind
- gust
- wind direction
- precipitation probability
- precipitation amount/type
- snowfall/snow depth where relevant
- humidity
- visibility if useful
- raw response digest
- observed_at

## D. Backtest rules

DO:
- use archived pregame forecast runs for supported periods;
- preserve forecast lead time as a feature/diagnostic;
- compare forecast vs realized weather separately;
- test interaction effects rather than universal thresholds.

DO NOT:
- use postgame observed conditions as if known pregame;
- use ERA5/reanalysis as a historical pregame forecast;
- mix forecast horizons without recording lead time;
- ignore retractable/domed roof status.

Long-term pre-archive weather:
ERA5/reanalysis can still be used to study whether *actual* wind/temperature historically correlates with football outcomes, but that is mechanism/exploratory evidence, not a leakage-safe reconstruction of what FULL COUNT could have known before old games.

## E. First weather experiments

Prefer named interactions over generic weather dumping:

1. wind/gust × QB aDOT/deep-attempt rate
2. wind × field-goal distance/accuracy
3. precipitation × passing efficiency/turnover rate
4. temperature × kicking and ball handling
5. outdoor exposure × dome-team travel
6. weather × pass rate/playcaller adjustment
7. gust uncertainty × total market movement

Each needs:
- multi-year historical population where forecast vintage exists;
- season-by-season stability;
- baseline vs challenger;
- no universal "wind > X = under" rule assumed.

## F. Injury-progression experiments

Once prospective official reports accumulate:

- DNP -> DNP -> DNP vs DNP -> LP -> FP
- game status by position
- first game after absence
- practice progression vs actual snap share
- practice progression vs route/carry/target redistribution
- OL practice status vs pressure allowed
- WR/TE absence vs teammate routes/targets
- defensive secondary absence vs target/coverage outcomes

Do not evaluate these from reconstructed latest-only records. The progression sequence itself must be frozen.

## G. Implementation consequence

The live Sunday intelligence stack can eventually use:

NFL.com official injury/practice capture
-> depth chart timestamp
-> weekly roster state
-> prior completed-game snap history
-> official inactive list
-> weather forecast runs
-> final market snapshot

That creates a much richer point-in-time state without waiting on paid data.

Alligator

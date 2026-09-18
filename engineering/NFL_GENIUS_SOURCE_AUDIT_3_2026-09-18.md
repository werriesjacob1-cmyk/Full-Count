# NFL Genius Source Audit Packet #3 — Free Tactical Charting Before Paid Data

Date: 2026-09-18

Purpose: determine how much tactical football intelligence can be built from public/free nflverse sources before considering paid charting or a film system.

## 1. FTN manual charting via nflverse

Authoritative docs:
- https://nflreadr.nflverse.com/reference/load_ftn_charting.html
- https://nflreadr.nflverse.com/articles/dictionary_ftn_charting.html
- https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html

Verified:
- manually charted play-level data from FTN Data;
- available from 2022 onward;
- released under CC-BY-SA 4.0 with attribution required;
- nflverse polls/updates during the season multiple times daily;
- charting is generally available within roughly 48 hours after games.

This means it is not a same-game pregame source, but it IS useful strictly-prior tactical history for the next game.

### Available tactical fields

The public subset includes:

- starting hash;
- QB pre-snap location: under center / shotgun / pistol;
- number of offensive players in backfield;
- number of defenders in box;
- no-huddle;
- motion;
- play action;
- screen;
- RPO;
- trick play;
- QB out of pocket;
- interception-worthy throw;
- throwaway;
- progression read / checkdown / designed read / scramble drill;
- catchable ball;
- contested ball;
- created reception;
- receiver drop;
- QB sneak;
- number of blitzers;
- number of pass rushers;
- QB-fault sack;
- source retrieval timestamp `date_pulled`.

### Important schema nuance

The progression-read coding changes by season:
- 2023+ includes explicit primary-read coding;
- in 2022 primary reads are not coded the same way and can appear NA.

This must be versioned rather than imputed away.

## 2. Participation data tactical depth

Authoritative docs:
- https://nflreadr.nflverse.com/articles/dictionary_participation.html
- https://nflreadr.nflverse.com/reference/load_participation.html

The participation dataset contains tactical fields far beyond simple "who played":

- offense formation;
- offense personnel;
- defenders in box;
- defense personnel;
- number of pass rushers;
- exact players on play;
- offense players;
- defense players;
- time to throw;
- pressure;
- primary receiver route;
- man vs zone;
- explicit coverage family such as Cover 0/1/2/3/4/6/9, 2-Man, combo, blown coverage.

This is extremely valuable for historical coverage/route/personnel research.

Critical limitation:
2023+ participation data is supplied after the postseason is complete and is not an in-season feed.

Therefore:
- excellent for historical mechanism discovery and model fitting;
- unusable for current-week 2026 live tactical state;
- current live state must be inferred from strictly-prior completed-game FTN charting + snap/depth/roster sources, unless another live charting source is added.

## 3. Free tactical brain now possible

Without PFF or raw tracking, FULL COUNT can already research:

### Offensive structure
- shotgun/pistol/under-center;
- backfield count;
- no-huddle;
- motion;
- play action;
- RPO;
- screens;
- trick plays.

### Defensive structure
- box count;
- pass-rusher count;
- blitzers;
- historical personnel;
- historical man/zone;
- historical coverage family.

### QB process
- out of pocket;
- throwaway;
- progression read;
- checkdown;
- designed read;
- scramble drill;
- QB-fault sacks;
- interception-worthy throws.

### Receiving quality/context
- catchable ball;
- contested ball;
- created reception;
- drops;
- historical route family via participation.

This should be exploited before concluding that expensive commercial charting is required.

## 4. What remains missing/free-data limited

The free sources do NOT yet prove access to:

- raw player x/y coordinates;
- detailed pre-snap safety shell rotation on every play;
- corner leverage/release technique;
- full individual route tree for every eligible receiver on every play;
- defender matchup assignment;
- double-team/bracket identity;
- individual blocking assignment/win-loss;
- exact pressure geometry;
- receiver separation at each time step;
- open-but-untargeted receiver geometry;
- route gravity;
- full motion trajectory.

These remain candidates for NGS-derived public metrics, commercial charting, or future legal film/vision research.

## 5. Recommended first tactical experiments

These can be built from free data before any purchase:

1. Motion rate persistence by coach/playcaller regime.
2. Play-action/RPO rate persistence by regime.
3. No-huddle/shotgun/pistol/under-center tendencies.
4. Box-count response to offensive personnel.
5. Blitz/pass-rusher environment vs QB pressure/sack response.
6. Interception-worthy throw rate vs actual INT rate.
7. QB-fault sack rate as a QB-specific pressure-response trait.
8. Checkdown/progression-read behavior vs pressure.
9. Catchable/contested/drop decomposition of receiving efficiency.
10. Historical route family × coverage family outcome research using participation.
11. Man/zone and coverage-family response by QB/receiver.
12. Offensive personnel/formation × play family.
13. Defensive personnel × run/pass effectiveness.
14. Motion × man/zone declaration and efficiency where overlapping data allows.

Every experiment must retain season-specific source coverage and cannot silently compare an FTN-defined feature to a participation-defined feature as if definitions were identical.

## 6. Multi-year architecture

Because tactical sources begin later than core PBP:
- deep PBP/team history provides long-term prior;
- 2016+ participation/NGS provides richer intermediate-era prior;
- 2022+ FTN charting provides richer recent tactical history;
- 2026 prospective captures provide current state.

Do NOT force one common sample start date.

Instead use hierarchical/shrinkage logic:
league/team/player long-term prior
+ tactical-source-era prior
+ current coach/regime
+ recent strictly-prior tactical observations.

## 7. Paid-data decision rule

Do not buy PFF/commercial charting merely because it sounds richer.

First:
1. exploit the free FTN/participation/NGS/PFR advanced fields;
2. quantify residual errors;
3. identify which missing tactical concepts plausibly explain remaining error;
4. specify the commercial fields required;
5. estimate expected incremental value;
6. then ask Jacob for purchase authorization.

The paid source should solve a measured residual-information gap, not satisfy curiosity.

Alligator

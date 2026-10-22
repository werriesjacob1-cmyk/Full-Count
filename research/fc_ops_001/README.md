# FC-OPS-001 — inactive offer-integrity contract

Research only. Nothing imports this parser from the dashboard, ingestion,
selector, publication, or workflow paths. The synthetic fixtures are not
historical offers. This work does not cure the production parser until a
separately authorized integration is reviewed and deployed.

## Reproduced baseline defects

At main `466f73355023a3b57b466af44d4848e9a76b0bbc`,
`odds_fanduel.py` `_event_props` accepted a CLOSED market's INACTIVE
one-sided runner at -120. `_parse_two_sided` paired Alice Over 5.5 with Bob
Under 6.5 and returned Alice's name with Bob's line. The pitcher-outs fetch
path produced Bob at 16.5 with Alice's Over price. The first-inning totals
fetch path accepted a SUSPENDED NRFI/YRFI market. The four baseline tests
intentionally assert those current behaviors; they are defect witnesses, not
approval of production behavior.

## Offer acceptance

`offer_contract.parse_archived_page` consumes exact archived page bytes and
their expected SHA-256, source request, FanDuel event ID, MLB game ID,
verified player-name/ID map, listed start, observation time, and decision
time. An accepted offer retains sportsbook, market type and ID, selection ID,
game and player identity, side, line, integer `needs`, American price,
timestamp, source request/host and raw hash. It emits separate rejection
diagnostics; unsupported markets are not represented as available offers.

The page parser requires explicitly OPEN market status, ACTIVE runner status,
`inPlay=false`, pre-start observation/decision, and observation at or before
decision with age <=45 minutes. Unknown availability fails closed. A
two-sided market requires exactly one Over and one Under for the same player
and line with distinct selection IDs and valid prices. Whole-number lines
are rejected until a push-aware settlement contract exists. One-sided
`MARKET_MAP` offers remain explicitly one-sided: the parser never invents an
Under price. For the 0.5 first-inning total, Over means YRFI and Under means
NRFI; it is game-level and has no player ID. Other game markets remain out of
scope. Raw break-even is computed from the actual American odds. For a valid
two-sided pair only, fair no-vig is each side's raw implied probability
divided by the pair's sum. Neither is a model prediction or a guarantee of
execution. Duplicate selection identities are rejected within the page.

The page parser does **not** authenticate the event-discovery root, MLB
schedule/game binding, request transport, bytes' collection time, storage,
or complete slate. Those are required upstream proofs from PR #226's S1
capture and manifest verification. Its caller must also establish that the
player-ID map is derived from authenticated pregame roster/lineup evidence.
No isolated parser result is an operationally certified price.

## PR #226 / PR #230 compatibility

| Evidence | PR #226 S1 | PR #230 champion export | Required join rule |
|---|---|---|---|
| Game | `game_pk` plus verified FanDuel event binding | `game_pk` | Exact game; retain S1 event ID |
| Player/team | `player_id`, team, verified name binding | `player_id`, team, subject | Exact ID and team |
| Market/side/threshold | candidate stat, side, needs, line; offer market/side/line | market, wager_side, needs, `projection.value` as line | Exact all four; a matching ID alone is insufficient |
| Price | authenticated S1 offer American odds and policy candidate odds | dashboard `market_odds` | Exact price or label `PRICE_CHANGED_UNREVALIDATED` |
| Time | S1 offer observation completion and unit decision/cutoff | dashboard preparation/publication cutoff and unverified market time | Same unit and cutoff; offer pre-cutoff and fresh |
| Source | S1 raw hash, request path, FanDuel event/market/selection IDs, availability | no raw response or per-offer authenticated source | Carry S1 identities; never infer them from export |
| Population | S1 board candidate/status and S1 policy count | uncapped `new_publication_policy_N`, intended/staged IDs | Actual operational N comes from PR #230; never substitute S1 N or static top ten |
| Exposure | research seal | intended/staged; public exposure needs registry | Do not claim customer-visible publication from either alone |

`join_s1_candidate` accepts the **actual S1 manifest row shape** (candidate
fields plus nested offer) and a PR #230 decision row. It requires the
caller to verify the S1 source chain, exact candidate identity, game,
player, team, market, side, needs, line and policy price; then checks the
offer's status, source IDs, raw hash, quote price and observation timing.
Its only success label is `MATCHED_RESEARCH_ONLY`. The tests use synthetic
rows bearing those field names. This is a contract-level compatibility
test, not a live end-to-end demonstration. PR #226 and #230 remain inactive
and separate. A future same-run verifier must validate both seals, same
unit and authenticated event binding before making a prospective claim.

## Future three-arm comparison, frozen before outcomes

1. Capture the complete eligible pregame slate and archived FanDuel bytes
   under one committed cutoff. Record incomplete, missing and true zero
   separately. Verify game/player binding, offer availability, raw hash,
   line, side, odds and observation time. A later price is a new observation,
   never retroactively the decision-time offer.
2. Replay the actual operational champion from the uncapped dashboard
   decision export and publication manifest. Its per-slate `N` is the
   original new-publication-policy Top Pick count before research quote
   exclusions. Separately track actual exposed/published picks via the
   immutable registry. Missing/invalid champion offers do not silently
   lower N or turn missing games into zero-pick slates. Mark such a slate
   non-comparable if equal-volume replacement is impossible.
3. On the same authenticated opportunity manifest, freeze three arms:
   actual champion, the fixed FC-ACC-014 challenger **only after** its
   independent leakage audit, and market-only ranking. Each selects N from
   exactly the same eligible pool using preregistered tie-breakers.
   Market-only uses raw price-implied win probability for ranking, with
   two-sided no-vig shown separately where available. Any selection rules
   for opposite sides of one market, multiple plays in a game or correlated
   players are fixed in advance and applied equally to all arms. Record
   overlap, added/removed selections and insufficiency explicitly.
4. Seal decisions before play. Grade every leg by matching official final
   game/player and market-specific rules, including push, void, DNP,
   postponement and unresolved states. Compare matched-count wins and
   actual-offer flat-stake return, with per-slate/game/player clustering
   and odds-band composition. Do not equate a high hit rate with positive
   ROI or a research decision with a public pick.

No 2025/26 archive or synthetic fixture can establish a genuine 2027
offered-price return or a forward selection lift. Before the 2027 regular
season, the code/identity contract, adverse tests, permissions, storage
retention and replay can be reviewed. Real source coverage, actual quote
freshness, full-slate timing, publication linkage and settled outcomes
require new-season authenticated observations and separate activation.

**Readiness: MODIFY.** The isolated parser and structural join are ready
for review; live operational compatibility and complete same-opportunity
capture remain unverified. The smallest next implementation is a single
inactive, same-unit replay fixture running the verified S1 manifest and
PR #230 export together, then a separately authorized bounded live
diagnostic before any prospective experiment is certified.

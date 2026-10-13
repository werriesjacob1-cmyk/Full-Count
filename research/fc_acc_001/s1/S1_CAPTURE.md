# FC-ACC-001-S1: research-only evidence capture

**Status: implemented and tested. Not active.** No schedule, workflow or
production path calls this package. Any live capture, prospective ledger
location, or forward experiment needs Jacob's explicit authorization and Codex
certification first. The only output so far is a synthetic dry run,
`research/fc_acc_001/out/s1_dry_run_NONCONFIRMATORY.json`. It is **not forward
evidence.**

## Pipeline

```
FanDuel sweep ─► raw_archive (gzip, keyed by sha256 of the original bytes)
      │                    ▲
      ▼                    │ re-read and re-hashed
capture record ──► parse_offers ──► offers (each bound to raw_response_sha256)
                                         │
MLB game log (archived raw) ─► workload ─┤
generate_picks candidate ───► features ──┤
games + decision_at ─────────────────────┴─► manifest.build ─► ledger.seal
```

| Module | Purpose |
|---|---|
| `raw_archive.py` | Write-once, content-addressed store. Re-hashes on every read, so a corrupt object raises an error. Identical bytes are stored once. |
| `fanduel.py` | The sweep:<br>• records per-request start and completion timestamps, host, raw sha and byte count;<br>• works to a time budget;<br>• gives each event a status;<br>• parses offers **only from the archived bytes**, skipping in-play markets. |
| `workload.py` | Pitcher starts from the archived statsapi game log. Uses only starts strictly before the game date; relief appearances are ignored. |
| `features.py` | A minimal pregame snapshot per candidate. A missing value is `UNKNOWN`, never imputed. |
| `manifest.py` | Joins candidates to offers on the full identity:<br>• game, player, stat, side and needs;<br>• games that started before the decision are excluded;<br>• an event must map uniquely to a game by matchup and start time within ±90 minutes.<br><br>It records separate eligibility for the existing and alternative policies and builds the three matched arms. Inputs are embedded, so the manifest can be re-derived. |
| `ledger.py` | Write-once seal per (date, window).<br>• PROSPECTIVE seals require `decision_at ≤ sealed_at < first pitch`.<br>• A dry-run manifest can never enter a PROSPECTIVE ledger.<br>• Replaying identical content returns `IDEMPOTENT_NOOP`; different content raises. |

### Architecture reused

From production `odds_fanduel.py`:
- the `HOSTS`, `UA` and `AK` constants;
- `MARKET_MAP` and `TWO_SIDED_MARKETS`;
- `_parse_two_sided` and `_parse_outs_runner`;
- `normalize_name`.

From `prop_probability.py`:
- `implied_probability`;
- `devig_two_sided`.

From PR #220 (v3 `capture.py`), the sweep structure is mirrored but not
imported:
- root discovery;
- the five tabs;
- per-request timestamps;
- the time budget;
- the status vocabulary.

PR #220 is **unaltered** and is not treated as production-ready.

**Changed relative to both.** Production `_get()` returns `r.json()` and #220
stores parsed JSON, so the original bytes, and with them any hash binding, are
lost. Here the raw response bytes are archived **before** parsing, and every
offer is re-derived from those archived bytes.

## Comparable selections

All arms draw from one shared offer manifest. N comes from the champion arm.

| Arm | Population | Rule |
|---|---|---|
| `CHAMPION_EXISTING_POLICY` | `existing_policy_eligible` (the selector marked it `top_pick`) **and** it has an authenticated offer | The existing policy, deduplicated per (player, stat, side). Defines N. |
| `MARKET_ONLY` | `alternative_policy_eligible`: an authenticated offer, QC kept, lineup not assumed, reliability A, B or C, and p present | Top N by raw break-even. |
| `BLEND_50_50` | Same as `MARKET_ONLY` | Top N by 0.5·p + 0.5·break-even. The weight is fixed. |
| Future factor challengers | Same as `MARKET_ONLY` | Must be preregistered, then added as an arm with its own fixed rule. |

**Rules:**
- Ties break on `candidate_id`.
- Arm status is `ZERO_CHAMPION`, `SHORTFALL` or `MATCHED`, and is recorded per slate.
- A top pick without an authenticated offer is listed in `champion_unauthenticated`. It is never priced from a later quote.
- If the model candidate's odds differ from the captured offer, `candidate_price_differs` is flagged and the **captured** price is used.

## Capturable versus unavailable evidence

| Field | Capturable now | Source / status |
|---|---|---|
| Sportsbook identity | Yes | `fanduel`, with host and request path |
| Raw response and sha256 | Yes | Gzip archive, verified on every read |
| Observation started / completed | Yes | Per request, UTC |
| Event identity | Yes | FanDuel eventId and name, plus a unique mapping to game_pk (otherwise `UNMAPPED_EVENT` or `AMBIGUOUS_EVENT`) |
| Player identity | Yes | Runner name, normalized. No FanDuel player id is used. |
| Market, side, exact line | Yes | Hits, HRR and SB are one-sided "over"; K and outs are two-sided |
| American odds and raw break-even | Yes | |
| No-vig fair price | Two-sided markets only | Null for one-sided markets; never imputed |
| Projected PA, lineup_assumed | Yes | From the model candidate at decision time |
| Batting order (slot) | Scaled only | `lineup_slot` signal from 0 to 100; the raw slot is not retained in production |
| Confirmed-lineup raw source | **No** | No raw lineup response is archived or bound yet |
| Pitcher recent outs, pitches, rest | Yes | Archived statsapi game log, prior starts only |
| Documented workload restriction | **No** | `UNKNOWN_NO_VERIFIED_SOURCE`; no reliable source exists |
| Opponent K and contact indicators | Scaled only | `opp_team_k_pct`, `platoon_xwoba` and `platoon_barrel_pct`, 0 to 100; raw values are not retained |
| Model and policy versions | Yes | Passed into the manifest |
| Historical offers (before today) | **Never** | No backfill; PROSPECTIVE seals after first pitch are refused |

## Fail-closed conditions

**Whole manifest:**
- the capture record does not verify against the archive;
- root discovery failed;
- capture completed after `decision_at`;
- no game starts after `decision_at`;
- a timestamp is naive or malformed.

**Per candidate:**
- `GAME_STARTED_BEFORE_DECISION`
- `GAME_NOT_IN_UNIT`
- `NO_SINGLE_PLAYER_OFFER`
- `MISSING_EVENT`
- `INCOMPLETE_CAPTURE`
- `MISSING_QUOTE`
- `CONFLICTING_OFFERS`
- `OBSERVED_AFTER_DECISION`
- `STALE_QUOTE` (older than 45 minutes)

All of these are counted in `accounting`.

## Tests

`test_fc_acc_001_s1_capture.py` holds 24 tests and uses synthetic fixtures
only, with no network. It covers:
- offer binding and tamper detection;
- source identity, including conflicts, ambiguous events, partial capture and the wrong sportsbook;
- timestamps;
- candidate/offer mismatches;
- missing features;
- prior-start-only workload;
- eligibility populations;
- matched, shortfall and zero-champion slates;
- determinism and re-derivation;
- idempotent replay and immutability;
- no backfill;
- bounded growth.

## Blockers to safe activation

1. **Hook into the scored candidate pool.**
   - Today `generate_picks` keeps only scaled signals and writes no per-slate candidate pool with features.
   - The capture needs a read-only export of the full scored pool, with `projected_pa`, signals, p and selector status, at decision time.
   - That needs a production touch, which is not authorized.
2. **Schedule binding.** `games[]` (game_pk, matchup, first pitch) should come from an archived raw statsapi schedule response, not a pre-parsed list.
3. **Lineup raw source.** Confirmed lineup and batting order should be bound to an archived raw response.
4. **Where it runs and where it is stored.** The scheduler, the ledger location and retention need a decision. That location is append-only storage outside `output/`.
5. **Pre-registration anchor.** A PROSPECTIVE start date and the stopping rule must be committed before the first seal.
6. **One measured live capture.** This settles the storage and time estimates below, and needs authorization.
7. **Codex certification** and **Jacob's activation**.

## Overhead and storage (estimate, not measured)

- **Requests.** One root request plus 5 tabs per event. A 15-game slate is about 76 requests, which is about 1 to 3 minutes against the 900 s budget.
- **Synthetic sizes.** Archive 1.6 KB; manifest 23.6 KB for 10 candidates.
- **Real raw payloads (unmeasured assumption).** FanDuel event-page JSON is commonly 50 to 400 KB, and gzip shrinks it about 8 to 12 times. That gives roughly 1 to 5 MB per sweep.
- **Season total.** At about 2 windows a day over about 180 days, that is about 0.4 to 1.8 GB a season. Content addressing removes identical repeats.
- **Manifest.** Grows linearly with the candidate pool: about 2.3 KB per candidate, or about 0.2 MB for a 70-candidate slate.

All of these numbers must be replaced by one measured capture before activation.

Alligator

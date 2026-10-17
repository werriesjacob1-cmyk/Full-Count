# FC-ACC-001-S1: research-only evidence capture (v2, integrity repair)

**Status: implemented and tested. Not active, and NOT certified.**

- No schedule, workflow or production path calls this package.
- Nothing has been captured live.
- No external anchor has been published.

Every manifest carries `certification.certified_prospective: false` with its
blockers listed. The only outputs are synthetic and non-confirmatory:
- `out/s1_dry_run_NONCONFIRMATORY.json`
- `out/s1_codex_attacks_before_after.json`

## Revision history

- **v1, `d316593c`, defective.** Codex's audit (PR #226 comment 6154204021)
  reproduced five defects, all of which also reproduce here: 8 of 8
  sub-attacks were VULNERABLE.
  1. Event identity in the capture record was not re-derived from the archived
     root.
  2. The join ignored the exact line and player identity.
  3. Two-sided runners from different players were combined.
  4. Feature and source times were not bound to the decision, and the champion
     N was set after offer filtering.
  5. The local seal could be contradicted after deleting its file.

  Those results are preserved in `out/s1_codex_attacks_before_after.json`.
- **v2, `4e6a1f04`.** All 19 attack variants were rejected. See "Adversarial
  results" below. Codex re-audit 6194240381 then found two timing P1s:
  - a fixed 22:00 UTC DAY/NIGHT split;
  - per-game exclusion of started games, which let a unit be decided, and even
    PROSPECTIVE-sealed, after its earliest first pitch.

  Both reproduced: 5 of 5 VULNERABLE in `out/s1_a2_timing_attacks_before_after.json`.
- **v3, `0a4d23d0` (`../AMENDMENT_A2.md`).**
  - DAY/NIGHT uses 17:00 America/New_York, converted with the IANA zone.
  - Each unit has one rule-determined cutoff: its earliest scheduled first
    pitch minus 30 minutes.
  - No game is ever dropped from a unit.
  - Result: 5 of 5 timing attacks REJECTED, and the 19 earlier attacks are
    still rejected.

  Codex's final A2 audit (6195082449) then found two more P1s:
  - a later schedule showing a delay moved the cutoff later (21:30 → 21:50);
  - a game missing from the board, or an incomplete capture, with N = 0 was
    labelled `ZERO_CHAMPION`.

  Both reproduced: 5 of 5 VULNERABLE in `out/s1_a3_attacks_before_after.json`.
- **v4, `9df0520c` (`../AMENDMENT_A3.md`).**
  - A `cutoff.py` commitment is made from a schedule observed by a fixed New York
    deadline: 09:00 for DAY, 15:00 for NIGHT. It is recorded write-once and binds
    the PROSPECTIVE seal.
  - The decision must equal the committed cutoff, so a delay never moves it
    later. A game moved earlier, or a change in the unit's games, makes the unit
    `NON_COMPARABLE`. No commitment means `MISSING_CUTOFF_EVIDENCE`.
  - Any coverage failure forces `NON_COMPARABLE` regardless of N, and
    `unit_tally` refuses contradictory arm labels.
  - All 29 attacks (19 + 5 + 5) are rejected.

  Codex's A3 certification HOLD (6197211757) then found that a commitment survived
  only as a standalone local file: deletion, replacement, recovery,
  idempotent re-seal and a commitment-free PROSPECTIVE seal all slipped through.
  Reproduced: 7 of 8 VULNERABLE in `out/s1_a4_attacks_before_after.json`.
- **v5 (this version, `../AMENDMENT_A4.md`).**
  - Cutoff commitments are `CUTOFF_COMMITMENT` entries in the same hash chain as
    seals; `cutoffs/<unit>.json` is only a verified index.
  - A PROSPECTIVE commitment extends the externally anchored head; a PROSPECTIVE
    seal requires the chained commitment and validates it before any NOOP.
  - All 8 A4 attacks and the 29 earlier ones are rejected. Science is unchanged.

## Pipeline

```
sealed production board_freeze ──► verify_board_seal ──► archive object (by sha)
raw statsapi schedule (lineups, probable SP) ──► schedule.load (observed ≤ cutoff)
raw FanDuel root ─► discover_events ─► exact tab paths ─► archived pages ─► parse_offers
raw statsapi game log ─► workload (observed ≤ cutoff, prior starts only)
                                     │
                      manifest.build (identity join, equal volume, comparability)
                                     │
                ledger.seal (hash chain, lock, O_EXCL, anchored head) ─► anchor statement
```

| Module | Integrity rule |
|---|---|
| `raw_archive.py` | Content-addressed and write-once via `os.link`, with re-hash on every read. Refuses a root inside a git work tree. Has an optional `max_bytes` bound. |
| `sources.py` | One archived observation per request: exact request string, host, sha256 of the delivered body (`requests.Response.content`, the decoded body rather than the wire bytes), and start and completion time. `verify` checks source, request, sha, size, chronology and `completed_at ≤ cutoff`. |
| `fanduel.py` | Lineage is **re-derived**:<br>• events from the archived root;<br>• each event's five tab paths must equal `event-page?eventId=<id>&tab=<tab>`;<br>• each market's `eventId` must equal the request's event (`MARKET_EVENT_ID_ABSENT` or `_MISMATCH` otherwise).<br><br>The budget is checked before **every** request, and `live_fetch_bytes` takes a deadline. Event and capture statuses are recomputed during verification. |
| `fanduel.parse_offers` | **One-sided:** duplicate runner names give `AMBIGUOUS_RUNNER_NAME`; a missing selection id or malformed odds is rejected.<br>**Two-sided:** exactly two runners, one player, one line, one OVER and one UNDER; anything else is `INCOHERENT_TWO_SIDED_MARKET`. No-vig is derived only from a coherent pair.<br>**Availability:** `marketStatus` must be OPEN and `runnerStatus` ACTIVE when present, and `inPlay: true` is rejected. An **absent** field gives `availability_verified: false` plus the `SPORTSBOOK_AVAILABILITY_SCHEMA_UNMEASURED` blocker, never "open". |
| `schedule.py` | Games, start time, `abstractGameState`, confirmed lineups and probable starters, all from archived raw statsapi only. An empty lineup array means **not confirmed**.<br><br>`window_of` maps a start to DAY/NIGHT at 17:00 `America/New_York` (IANA). Naive, malformed or before-official-date times are rejected. |
| `workload.py` | The game-log source must match the exact request for that pitcher and season and be observed by the decision cutoff. Only starts before the game date are used. The output carries its source record. |
| `features.py` | `captured_at` is required and timezone-aware. `check()` rejects:<br>• a mismatched identity;<br>• `captured_at` after the decision;<br>• a workload observed after the decision;<br>• a workload that does not **re-derive** from its archived source.<br><br>Model values are labelled `UNVERIFIED_PRODUCTION_VALUES`. |
| `manifest.py` | See "Join" and "Equal volume" below. It is re-derivable: `verify_manifest` rebuilds from the archived board, schedule record, capture and snapshots. |
| `ledger.py` | Hash-chained `chain.jsonl`, an O_EXCL unit index and an `fcntl` lock. See "Durable seal". |
| `anchor.py` | Defines the external anchor statement and its verification. It never publishes. |

## Join (candidate ⇄ offer), all exact

The candidate must come from the sealed board (`verify_board_seal`), with
`board_generated_at` and `sealed_at` at or before the decision.

| Check | Fail-closed code |
|---|---|
| `decision_at` is exactly the A2 unit cutoff (earliest unit first pitch − 30 min) | whole manifest refused |
| Unit game pregame in raw schedule (the game stays in the unit and in N) | `GAME_NOT_PREGAME_IN_RAW` (unit `NON_COMPARABLE`) |
| Board start time equals the raw schedule start time | `BOARD_MISSING_GAME`, `BOARD_SCHEDULE_MISMATCH` |
| Exactly one FanDuel event (matchup ± 90 min), fully captured | `MISSING_EVENT`, `AMBIGUOUS_EVENT`, `INCOMPLETE_CAPTURE` |
| Single-player market S1 can price | `NO_SINGLE_PLAYER_OFFER`, `MARKET_NOT_IN_S1_SCOPE` |
| Normalized name maps to one MLB player id in the unit | `AMBIGUOUS_PLAYER_NAME` |
| Batter in a confirmed raw lineup on the side matching `team` | `LINEUP_NOT_CONFIRMED_IN_RAW`, `PLAYER_NOT_IN_RAW_LINEUP`, `TEAM_MISMATCH` |
| Pitcher is the raw probable starter, with matching `team` | `STARTER_NOT_PROBABLE_IN_RAW`, `TEAM_MISMATCH` |
| Exact (game, player, market, side, **line**); needs agree | `MISSING_QUOTE`, `LINE_NEEDS_INCONSISTENT` |
| One price, selection and market; selection id never reused for another identity | `CONFLICTING_OFFERS` |
| Observed at or before the decision, at most 45 minutes old | `OBSERVED_AFTER_DECISION`, `STALE_QUOTE` |
| `offer_provenance.validate_offer` (now actually called) | `OFFER_PROVENANCE_INVALID` |

FanDuel runners carry no MLB player id. Player identity is therefore the
raw-lineup/raw-starter membership of the board's MLB id, plus a name that is
unique in the unit. This is stated, not hidden.

## Equal volume and comparability

- **N** is the number of board `top_pick` decisions across **all** of the unit's
  games, de-duplicated per (player, stat, side). It is computed **before** any
  offer filtering. Under Amendment A2 a unit is decided once, at its common
  cutoff before the earliest first pitch, and no game is dropped from it.
- **Champion opportunity status** is one of:
  - `SELECTED`;
  - any offer code;
  - `POLICY_PRICE_CHANGED_UNREVALIDATED`, when the captured price differs from
    the price the policy decided at.

  The old label is never carried over to a new price.
- **Unit status:**
  - `ZERO_CHAMPION` when N = 0;
  - `COMPARABLE` only when the capture is COMPLETE, no game coverage issue
    exists, and the champion and both alternative arms each have N;
  - otherwise `NON_COMPARABLE`, with the reasons listed.
- **Arm status:** `ZERO_CHAMPION`, `SHORTFALL`, `NON_COMPARABLE`, or `MATCHED`.
  `MATCHED` is used only inside a COMPARABLE unit.
- **Labelling:** `publication_eligible` is false on every arm. Alternative arms
  are labelled `policy: ALTERNATIVE`.

## Durable seal (local) and anchoring (defined, not deployed)

### Local ledger

- **Chain.** Every entry carries `seq` and `prev_entry_sha256`. There are two kinds:
  - **cutoff commitment** (A4): `commitment_sha256`, the commitment object hash,
    `cutoff_utc`, `commitment_deadline_utc`, `recorded_at`,
    `evidence_status: LOCAL_COMMITMENT_UNANCHORED`;
  - **seal**: the manifest hashes, `sealed_at`, `first_pitch_utc_min`,
    `evidence_status: LOCAL_SEAL_UNANCHORED`.

  Manifests and commitments are archive objects referenced by hash. Nothing nests.
  A unit's commitment must precede its seal in the chain.
- **One manifest per unit.** Identical replay returns `IDEMPOTENT_NOOP`;
  different content raises.
- **Deleted or replaced index file** (seal or commitment). Gives `MISSING_LINK`
  or a mismatch, which blocks all recording and sealing and fails `verify_ledger`.
  `restore_index` rewrites the file byte-identically from the intact chain
  entry and never re-seals; the chain's first commitment always stands.
- **Orphan index or truncated chain.** Blocks sealing, and is not locally
  recoverable.
- **Concurrency.** An `fcntl` lock covers check-and-append, and the O_EXCL index
  create is a second barrier. In a two-process race test, exactly one seals.
- **PROSPECTIVE commitments and seals require `anchored_head`.** This is the
  chain head last published externally. If the local head differs (a deleted,
  forked or unanchored local ledger), recording and sealing are refused.
- **PROSPECTIVE seal contract (A4).** The unit's chained commitment must exist and
  equal the manifest's; this is checked before an idempotent NOOP.

### External anchor (`anchor.py`)

- **What gets anchored.** After each seal, a one-line statement:
  `FC-ACC-001-S1-ANCHOR v1 kind=… seq=… entry=<sha> unit=… manifest=<sha>`.
  A head covers all earlier entries.
- **Proposed log (not provisioned).** A comment on a dedicated GitHub issue,
  whose `created_at` is server-assigned and whose edits are visible in its
  history.
- **Verification.** The verifier fetches the anchors itself, then calls
  `verify_anchors`. It returns `ANCHORED_PREGAME` only when an anchor covering
  the entry was created after `sealed_at` and before that unit's first pitch;
  otherwise `ANCHORED_LATE` or `UNANCHORED`. A commitment entry is
  `ANCHORED_BEFORE_DEADLINE` only when a covering anchor was created between its
  `recorded_at` and the earlier of its deadline and cutoff. An anchor naming an
  entry that the local chain lacks raises `CONFLICT`.
- **Local versus prospective.** `unit_evidence` returns
  `EXTERNALLY_ANCHORED_PROSPECTIVE` only when both the commitment and the seal are
  anchored in time; otherwise `LOCAL_ONLY`.
- **Status.** **No anchor has been published or verified.** No S1 result may
  be called "prospective immutable".

## Adversarial results (`out/s1_codex_attacks_before_after.json`)

**Before:** all 8 sub-attacks VULNERABLE at `d316593c`.

**After:** all 19 variants REJECTED.

| Attack | After repair |
|---|---|
| Forged event id with recomputed self-hash | `CaptureError`: identities differ from the archived root |
| Tab request rebound to another event | `CaptureError`: tab requests not bound to eventId |
| Market with a foreign `eventId` inside a page | `MARKET_EVENT_ID_MISMATCH` |
| Wrong line (1.5 vs 0.5) | `MISSING_QUOTE` |
| Same name, two MLB ids | `AMBIGUOUS_PLAYER_NAME` (both) |
| Wrong player id / wrong team | `PLAYER_NOT_IN_RAW_LINEUP` / `TEAM_MISMATCH` |
| "Ace Arm Over" + "Other Arm Under" | `INCOHERENT_TWO_SIDED_MARKET`; c5 `MISSING_QUOTE` |
| Two-sided split lines / suspended market | `INCOHERENT_TWO_SIDED_MARKET` / `MARKET_NOT_OPEN:SUSPENDED` |
| Feature snapshot after first pitch | `FEATURE_AFTER_DECISION` |
| Workload observed after the decision / edited workload | `WorkloadError` / `WORKLOAD_NOT_REDERIVABLE` |
| Schedule observed after the decision | `ManifestError` |
| 4 top picks, 1 unquoted | N = 4: champion `SHORTFALL`, alternatives `NON_COMPARABLE` |
| Partial sweep | unit `NON_COMPARABLE` (`CAPTURE_INCOMPLETE`) |
| Captured price differs from the policy price | `POLICY_PRICE_CHANGED_UNREVALIDATED` |
| Delete the index file, then reseal different content | `MISSING_LINK` |
| Delete the whole ledger, then a PROSPECTIVE reseal | refused: local head GENESIS ≠ anchored head |

## Real-data checks (non-live, no fabrication)

- **`board_freeze_2026-09-22.json`.** Its seal re-verifies, and all 965
  in-scope single-player records yield exact line, needs and MLB id.
- **Out-of-scope records.** The 14 `nrfi_combined` and 6
  `combined_strikeouts` records are game-level markets (the NRFI "player id"
  is synthetic). They are recorded as `MARKET_NOT_IN_S1_SCOPE`, or
  `NO_SINGLE_PLAYER_OFFER` for combos. A champion pick
  in those markets counts toward N and makes the unit `NON_COMPARABLE`.
- **Latest board, `2026-10-13`.** Its seal verifies; with no raw schedule the
  build fails closed.

## Capturable versus unavailable

| Field | Status |
|---|---|
| Sportsbook, event, market, selection, line, odds, raw sha, observation times | Captured; lineage re-derived |
| Market and runner availability | Enforced when the fields are present. **Absence is unverified**, because the live MLB prop schema is unmeasured by S1. |
| Schedule, game state, confirmed lineup, probable starter | Captured from raw statsapi (code ready; not live-tested) |
| Pitcher prior-start outs, pitches, rest | Captured from raw game log |
| Documented pitch limit | `UNKNOWN_NO_VERIFIED_SOURCE` |
| Model probability and policy status | Bound to the sealed board |
| Model feature values (projected PA, scaled signals) | `UNVERIFIED_PRODUCTION_VALUES` |
| Non-chosen thresholds, full candidate pool | Not available (`CANDIDATE_POOL_LIMITED_TO_FREEZE_CHOSEN_LINES`); see `EXPORT_DESIGN.md` |
| Historical offers | Never |

## Remaining blockers to activation

1. A separately authorized, isolated **live diagnostic capture**: shape,
   status fields, latency and size only. It must not score picks.
2. **The full-candidate export** (`EXPORT_DESIGN.md`), so the pool is not
   limited to freeze-chosen lines.
3. Fixing the **daily freeze overwrite**, or using per-run exports: the
   partial-slate bias reported in `EXPORT_DESIGN.md`.
4. Choosing an **external anchor log**, and an independent verifier that fetches
   it. This needs authorization and provisioning. Until then every unit is
   `LOCAL_ONLY` (A4).
4a. An early **schedule-only observer** able to commit before 09:00 ET (DAY);
   the production workflow's first run is after the EDT DAY deadline.
5. A **scheduler**, with retention for raw objects outside git.
6. A **PROSPECTIVE start date and stopping rule**, committed and anchored first.
7. **Codex certification and Jacob's activation.**

## Overhead (measured on fixtures only; live sizes unmeasured)

The synthetic dry run produces:
- a 7.2 KB raw archive (two events, plus the schedule and game log);
- a 23.9 KB manifest;
- chain entries of under 1 KB each.

The live estimates in `EXPORT_DESIGN.md` §8 remain **unmeasured** until the
diagnostic capture.

Alligator

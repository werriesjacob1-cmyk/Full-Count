# NFL primary game-line normalization evidence

Date: 2026-09-15

Scope: research ingestion only

Base: PR #92 head `598b7944561511a169f91c925919a0ca9bdaf5fc`

## Implemented boundary

`nfl/normalize/fanduel_game_lines.py` recognizes exactly two FanDuel NFL
pregame market families:

- `MATCH_HANDICAP_(2-WAY)` with market name `Spread`;
- `TOTAL_POINTS_(OVER/UNDER)` with market name `Total Points`.

The normalizer requires an open market with an explicit `inPlay: false`, a
resolvable `Away @ Home` event, two active runners, nonzero American prices,
and exact side cardinality. The market clock must equal the event kickoff, and
selection IDs must be present and distinct. Spread runners must bind to the
event's away and home team names and their handicaps must sum to zero. Total
runners must bind to Over and Under and carry the same positive line. Pick'em
spreads are valid.

Alternate, period, and team markets remain outside this contract. A market
that presents a primary name or type but violates the other half of the
identity is rejected rather than ignored. Conflicting copies of one market ID
invalidate that ID; identical copies collapse deterministically.

Normalized rows preserve the capture clock, raw-payload SHA-256, logical
artifact name, optional source URL, source event and market IDs, runner
selection IDs, event kickoff, lines, and both prices. The caller computes the
digest over archived raw bytes; the normalizer validates its representation
and carries it without claiming to reproduce the original serialization from
an already-decoded object.

## Sealed DEN-KC replay

The normalizer was replayed locally against the unmodified raw event bytes
from successful research capture run `34906529900`:

- logical artifact: `event_35601246_passing-props.json`;
- raw payload SHA-256:
  `7e4a3e89ebb6fd9055728a65110d5578e740094da846c8340c9bd3837129196f`;
- capture clock: `2026-09-14T22:57:21Z`;
- event: `35601246`, Denver Broncos at Kansas City Chiefs;
- spread market `734.168694354`: Denver +2.5 at -115, Kansas City -2.5 at -105;
- total market `734.168694356`: 43.5, Over -102, Under -120;
- result: 13 markets scanned, two primary markets seen, two normalized, zero
  rejected, 11 unrelated markets ignored.

The raw sportsbook bytes remain outside Git. This branch adds no capture job,
historical data, probability, selector, grader, publication path, or model.
Registry lifecycle is therefore only `NORMALIZED`; every later capability
remains false and explicitly blocked.

## Validation

- 19 focused game-line and lifecycle-gate contract tests pass.
- The sealed DEN-KC replay matches the exact observed event, market IDs,
  lines, prices, selection IDs, capture time, and raw-byte digest.
- 92 dependency-free NFL tests pass. Two source-adapter test modules cannot
  import locally because `requests` is unavailable in this Windows runtime;
  exact-head GitHub CI remains required before review.

Alligator

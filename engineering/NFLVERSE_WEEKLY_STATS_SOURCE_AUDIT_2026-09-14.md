# FULL COUNT nflverse weekly player-stat source audit

Date: 2026-09-14

Scope: public source availability, size, HTTP validators, license declaration, and CSV header compatibility. This is not a data-quality certification or a production ingestion activation.

## Result

A bounded HTTP range read checked every weekly player-stat CSV from 1999 through 2025 at the canonical nflverse release path. All 27 assets returned HTTP 206, exposed a reported full size, supplied both `ETag` and `Last-Modified`, and contained every field in FULL COUNT's current 19-column player-stat contract.

The 27 headers were byte-equivalent after parsing: one observed schema variant with 150 columns. Their combined reported size is 210,443,404 bytes, about 200.7 MiB. The smallest observed asset was 2000 at 7,268,743 bytes; the largest was 2025 at 8,656,387 bytes.

The compact evidence manifest is `engineering/evidence/nflverse_weekly_stats_source_manifest_2026-09-14.json`. It retains canonical URLs, sizes, public response validators, the full observed header once, and its SHA-256 fingerprint. It deliberately omits GitHub's expiring signed redirect URLs.

## What this establishes

- The current weekly-stat feature contract can begin historical ingestion as far back as 1999 without a header-level migration layer.
- A full first-pass download is small enough for a controlled external cache or object store. Committing the CSV corpus to Git is unnecessary.
- The current production workflow's 2023–2025 downloads are a policy choice, not the limit of upstream weekly-stat availability.
- The source repository declares the data under [Creative Commons Attribution 4.0](https://github.com/nflverse/nflverse-data/blob/main/LICENSE.md). Any retained or shared derivative must carry appropriate attribution and must not imply endorsement.

## What this does not establish

- Header equality does not prove stable field meaning, historical completeness, row-level identity quality, correction history, or point-in-time availability.
- `ETag` and `Last-Modified` are observation validators, not permanent content pins. A reproducible warehouse still needs full-byte SHA-256 digests and acquisition timestamps.
- This audit did not download or count complete rows, reconcile seasons to schedules, inspect null rates, or test field semantics.
- Weekly player totals alone cannot support spreads, totals, possession models, coaching effects, participation, line movement, or film/news claims.

## Safe ingestion design

1. Download each canonical asset to a dated cache outside Git and compute SHA-256 before parsing.
2. Store a raw immutable object keyed by source, season, acquisition time, and digest.
3. Validate the 150-column header fingerprint and required subset before accepting rows.
4. Add per-season row counts, duplicate keys, null profiles, season/week bounds, team/opponent validity, and cross-season identity continuity.
5. Build normalized tables only from the immutable raw object, carrying source digest and acquisition time into every derived partition.
6. Keep the current passing-yards B0 benchmark unchanged as the control while challengers use the broader substrate.

Alligator

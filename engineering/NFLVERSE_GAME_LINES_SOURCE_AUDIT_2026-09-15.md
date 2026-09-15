# FULL COUNT nflverse game-line source audit

Date: 2026-09-15

Source commit: `nflverse/nfldata@8ed09b2fe3ea42332b2249a995737e13dd931ff3`

Classification: `NFLVERSE_SCHEDULE_UNKNOWN_BOOK`

Scope: source provenance, byte identity, schema, coverage, outcome consistency, and market-pair coherence for `games.csv`, `closing_lines.csv`, and `initial_lines.csv`. This audit does not activate historical ingestion, a model, selection, grading, or publication.

## Result

The source is useful as a generic historical outcome and line baseline. It cannot be represented as FanDuel history and cannot support book-specific calibration, precise closing-line value, or open-to-close research because the broad schedule file does not identify a sportsbook or record a line-capture timestamp.

The reproducible audit is `nfl/research/nflverse_game_lines_audit.py`. It verifies the pinned SHA-256 for all three inputs before parsing, requires the expected schemas, rejects duplicate game IDs, rejects score/result inconsistencies, and rejects incoherent two-runner spread or total groups. The compact source manifest is `engineering/evidence/nflverse_game_lines_source_manifest_2026-09-15.json`. Raw CSV files remain outside Git.

## Full-file findings

`games.csv` contains 7,548 games across seasons 1999 through 2026. Of those, 7,292 are settled and 256 are future or unsettled. Every settled game has a spread and total line. Two-sided prices exist for 5,311 settled spreads and 5,308 settled totals. Price coverage is complete from 2010 through 2025, partial from 2006 through 2009, and absent from 1999 through 2005. The audit found no duplicate game IDs and no disagreement between final scores and the `result` or `total` outcome fields.

`closing_lines.csv` contains 20,490 rows for 3,415 games from 2006 through 2018. Each game has two moneyline, two spread, and two total rows. All spread pairs oppose and all total pairs agree on their threshold. Only 8,850 rows contain odds. The file has neither a sportsbook column nor a line timestamp.

`initial_lines.csv` contains 1,088 rows, all from the 2021 season and all labeled `WSGT`. It has 544 spread and 544 total rows. It supplies lines without prices or timestamps, so it is a narrow opening-line reference rather than a broad price-history source.

## Live-event comparison

The closest `games.csv` revisions around FULL COUNT's sealed DEN–KC capture were inspected separately:

- `3d0c3a372d...`, committed 2026-09-14 21:45:14Z, had DEN +2.5 at -112, KC -2.5 at -108, and total 42.5 at -110/-110.
- `1ea57d640...`, committed 2026-09-14 23:15:12Z, retained those values.
- FULL COUNT's sealed FanDuel observation at 2026-09-14 22:57:21Z had DEN +2.5 at -115, KC -2.5 at -105, and total 43.5 at -102/-120.

The line and prices differ during the same pregame window. This directly rules out silently substituting nflverse schedule lines for captured FanDuel evidence.

## Safe use contract

- Preserve `NFLVERSE_SCHEDULE_UNKNOWN_BOOK` on every derived row unless a separate primary source proves book identity.
- Carry source commit, file digest, acquisition time, and original game ID into every derived partition.
- Separate settled outcomes from future rows before training or evaluation.
- Keep bookmaker-specific calibration, closing-line value, and line-movement features disabled for this source.
- Do not infer that `closing_lines.csv` and `games.csv` are interchangeable. Their overlapping historical values frequently differ and represent distinct source vintages.
- Use chronological train/validation/test splits and make point-in-time limitations explicit before any model experiment.

The next safe step is a normalized, source-labeled historical game table and a leakage-audited market baseline. Prospective FanDuel captures remain the authoritative substrate for FanDuel-specific evaluation.

Alligator

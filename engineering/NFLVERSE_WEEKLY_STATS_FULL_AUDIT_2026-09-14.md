# FULL COUNT nflverse weekly player-stat full-file audit

Date: 2026-09-14

Scope: full-byte acquisition and row-level source quality for the canonical 1999–2025 nflverse weekly player-stat CSVs. This is research evidence, not a normalized warehouse, model promotion, or production activation.

## Result

All 27 previously range-verified assets were downloaded to a cache outside Git. The corpus contains 210,443,404 bytes and 476,159 rows. The compact manifest records a full-file SHA-256 for every season; all 27 digests are distinct. Each file retained the observed 150-column schema and every field in FULL COUNT's 19-column contract.

Across the corpus:

- No row had a blank or nonnumeric value in the 11 tracked offensive numeric fields.
- No file contained a season value different from its named season.
- Season types were limited to `REG` and `POST`; weeks span 1–21 through 2020 and 1–22 from 2021 onward.
- No duplicate `(player_id, season, week, season_type)` key was observed.
- 11,365 non-sentinel player IDs were observed. No ID mapped to multiple nonblank display names or positions in this corpus.

The machine-readable evidence is `engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json`. The reproducible downloader/auditor is `nfl/research/nflverse_full_audit.py`. Raw CSVs remain outside Git.

## Identity exceptions

The audit identified three distinct classes that must remain separate:

1. There are 523 blank-ID, blank-name, blank-position rows with zero tracked offense from 2001–2025. These are the structural rows already handled by the history builder.
2. There are 42 more zero-stat structural rows in 1999–2000 whose literal `player_id` is `0`. Treating `0` as a real player would join unrelated teams and weeks into one false career. The history builder now excludes this sentinel only when its name, position, and tracked offense are all empty/zero.
3. Seven rows have no stable player ID and carry nonzero tracked offense. They remain hard failures:

| Season/week | Game | Source name | Recorded offense |
| --- | --- | --- | --- |
| 2001 W11 | GB at DET | Team | 1 completion, 1 attempt, 42 passing yards |
| 2001 W16 | BAL at TB | blank | 1 carry |
| 2003 W4 | PHI at BUF | Team | 1 carry, -1 rushing yard |
| 2009 W3 | CLE at BAL | Team | 4 targets |
| 2009 W6 | HOU at CIN | Team | 1 target |
| 2009 W14 | PIT at CLE | Team | 1 target |
| 2012 W6 | PIT at TEN | D.Bryant | 2 targets, 2 receptions, 11 receiving yards |

The six team/blank records appear to be unattributed source aggregates. The D.Bryant record may refer to a person, but the source row does not supply a stable identity. Neither interpretation is promoted into code. A future normalized warehouse must quarantine these seven rows with their source digest and reason, then prove that excluding them cannot change the evaluated population for a target market.

Nineteen additional rows have a non-sentinel player ID but lack both display name and position after removing the 42 sentinel rows. Six rows across the corpus lack opponent identity, and one 1999 row lacks both team and opponent. Those rows require explicit target-specific eligibility rules before broader model research.

## Builder correction

`nfl/research/nflverse_history.py` previously recognized only blank IDs as structural. It now treats the literal `0` as missing identity and applies the same narrow zero-row audit. A `0` row carrying any tracked offense still raises an error. Three focused tests cover the blank structural row, the literal-zero structural row, and the literal-zero nonzero failure.

The active 2023–2025 B0 inputs contain 22 blank-ID structural zero rows per season and none of the seven nonzero identity exceptions. This correction therefore expands safe historical handling without changing current B0 projections or evidence.

## Remaining limits

- Current release assets are a present-day snapshot. They do not reconstruct what corrections or identities were available at historical decision time.
- Header and row checks do not validate statistic meaning, scoring conventions, schedule completeness, play-level reconciliation, or agreement with an independent source.
- A full research warehouse still needs immutable object storage, acquisition metadata, quarantine records, normalized partitions, schedule/team validation, point-in-time joins, and target-specific eligibility reports.
- No model should train on the full corpus until the seven nonzero missing-identity rows and the remaining critical-field blanks have explicit, tested dispositions.

Alligator

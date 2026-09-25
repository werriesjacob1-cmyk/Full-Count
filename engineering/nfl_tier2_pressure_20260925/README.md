# F13 — prior QB pressure and blitz tendencies, research only

This is a connected, separate passing-yards challenger. It does not edit B0,
Claude's F11/F12 coverage work, scheduled workflows or customer picks. The
consumer uses the existing rolling five-appearance QB passing-yards B0 from
`passing_yards_baseline_research.rolling_predictions` without changing it.

## Source and meaning

FTN Data via nflverse [2024 participation](https://nflreadr.nflverse.com/reference/load_participation.html)
records `was_pressure`; [2024 FTN charting](https://nflreadr.nflverse.com/reference/load_ftn_charting.html)
records `n_blitzers` and `n_pass_rushers`. The former is an observed pressure
label. Blitz count is separately observed; a blitz does not imply pressure.
Rusher-positive rows are called **charted pass-rush opportunities**, not all
legal pass attempts. A cross-check against PBP found that the rusher-positive
population includes QB scrambles classified as runs, so using it as an
all-pass denominator would be false. Sacks and hits are neither substituted for
nor equated with pressure. There is no player-specific pass-rusher or coverage
assignment here, and FULL COUNT did not watch the underlying footage.

The exact participation CSV is 49,688,308 bytes, SHA-256
`b1f436a98b2a7759eb4ed1181e072a35c2666f9aeb356a49c943d28d6be6b0b9`,
GitHub release asset created 2025-09-04 10:24:47Z. The exact FTN charting
CSV is 8,254,908 bytes, SHA-256
`6faae8118cc13ce62589210d553733128ed35e558671009b4a7a8fc5c674c2cb`,
release asset created 2025-09-01 01:29:36Z. Both precede the modeled 2025
week-2-and-later games. Every play is bound by exact 2024 game/play key; the
on-field QB must be uniquely identified by aligned GSIS ID and `QB` position.
The offense must be a team in the game ID, and the opponent is derived from
that identity. Unknown or inconsistent charting is excluded. Each contributing
QB and defense profile retains sample count, exact-source digests and a digest
of all game/play identities, plus sample play IDs. Source observation timing is
the release time, never backdated to the 2024 game date.

The charting and derivatives are attributed **FTN Data via nflverse** under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
The weekly QB files used for B0 and outcomes are SHA-pinned in the report.
Those exact weekly files were downloaded in 2026, so this is a retrospective
comparison; their pre-2025 revision state is not independently authenticated.
The currently pinned 2024 PBP CSV was itself created in August 2026, so it
was deliberately **not** used as a pre-2025 predictor or as a purportedly
point-in-time QB-under-pressure yardage split. Such a split remains blocked
without an appropriately dated, verified play-level yard source.

## Frozen experiment and result

Profiles use 2024 charting only. Activating a player/game requires at least
100 charted prior opportunities for both the QB and opposing defense. The two
features are fixed before evaluation: the mean of QB-experienced and
defense-created pressure rates, and the analogous blitz rates, each centered
on the 2024 eligible defense-wide mean. The DEV fit on 2025 weeks 2–8 estimates
a baseline scale and a three-term ridge challenger (penalty 1000 on tactical
terms). Weeks 9–18 are the held period. The period has been inspected by other
FULL COUNT research, so its evidence class is **retrospective exploratory**,
not fresh prospective validation. No hyperparameter was changed after seeing
the result.

| Same matched held population | Passing-yards MAE |
|---|---:|
| Unchanged B0 | 72.986 |
| DEV-fitted scale-only control | 71.715 |
| Pressure/blitz challenger | 72.967 |

There were 199 active DEV observations and 243 active held observations out of
368 candidate held QB games (66.0% activation; 38 distinct held QBs). The
challenger **worsened MAE by 1.252 yards** versus the fitted scale control;
the player-cluster bootstrap 95% interval for that delta is [+0.035, +2.534]
yards. This is a negative accuracy result, not a promotion candidate. The
method may be confounded by opponent strength, scheme continuity, QB roster
changes and the charted-rush denominator; it does not demonstrate value for
bet selection.

For a concrete activation, Dak Prescott vs Arizona in 2025 Week 9 had
unchanged B0 253.8, scale control 242.36, and F13 challenger 236.59 passing
yards. The fitted pressure and blitz contributions were −3.18 and −4.50 yards;
actual was 250. The challenger moved the forecast but worsened this example.
The report contains the matched rows, individual contributions, profile
samples, source identities, activation coverage and fixed player-cluster CI.

## Reproduce

Download only the two exact public 2024 assets above and the pinned nflverse
`stats_player_week_{2023,2024,2025}.csv` files to a stats directory. Verify
their hashes against `nfl.research.tier2.pressure_f13.SOURCES`; the CLI also
verifies them and fails closed on drift. Then:

```text
python -m nfl.research.tier2.pressure_f13 \
  --participation PATH/pbp_participation_2024.csv \
  --ftn PATH/ftn_charting_2024.csv \
  --stats-dir PATH_TO_WEEKLY_FILES \
  --output NEW_REPORT.json
python -m unittest nfl.tests.test_tier2_pressure_f13
```

The output is create-only. `evaluation.json` is the frozen result of the one
evaluation, with report seal in its `report_sha256` field. This work stops at
a negative research challenger; there is no B0 or selector change. Alligator.

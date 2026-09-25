# F14 — observed offensive concepts to receptions research challenger

The module `nfl/research/tier2/concepts_f14.py` leaves the existing rolling
receptions B0 untouched. It adds a separate, create-only retrospective
challenger. The model and tests were frozen at commit `19e3715c7110fc4885d80ad93c716577812f58a1`
before the single held evaluation. No parameter was changed after that result.

## Direct source and measured scope

[FTN Data via nflverse](https://nflreadr.nflverse.com/reference/load_ftn_charting.html)
charts `is_play_action`, `is_motion`, `is_rpo` and `is_screen_pass`. Its
[dictionary](https://nflreadr.nflverse.com/articles/dictionary_ftn_charting.html)
defines these as play-action pass, pre-snap/at-snap motion, an RPO play and a
screen pass, respectively. Each flag is known in all 48,031 rows of the
verified 2024 FTN CSV. The companion participation CSV supplies exact
game/play, possession team and on-field GSIS player IDs. The sources are
joined by exact game/play; 38,168 joined scrimmage-like plays have one
unambiguous on-field QB. Other rows remain excluded; unknown flags would
be excluded, never interpreted as false.

The FTN CSV SHA-256 is
`6faae8118cc13ce62589210d553733128ed35e558671009b4a7a8fc5c674c2cb`
(published 2025-09-01), and the participation CSV SHA-256 is
`b1f436a98b2a7759eb4ed1181e072a35c2666f9aeb356a49c943d28d6be6b0b9`
(published 2025-09-04). Both preceded the 2025 week-2-and-later evaluation
games. Source observation time is the asset publication time, not the 2024
game date. Attribution: **FTN Data via nflverse**, under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

For each team and player-on-team, the feature is the rate at which these
observed concepts occurred on charted plays with that player on the field.
This is **participation exposure**, not proof that the player ran a route, was
targeted, caught the screen, blocked, or received a QB read. Team use and
player on-field rates are averaged then centered on the charted league rate.
The consumer requires at least 400 prior team plays, 100 prior same-team player
plays and a valid B0; otherwise it returns `NO_ADJUSTMENT`. It carries source
hashes, release time, sample counts and a digest of every game/play identity.

Player-specific **concept targets and efficiency are SOURCE_BLOCKED** in this
strictly prior 2025 replay: neither FTN concept rows nor participation identify
the intended receiver. The pinned 2024 PBP file available locally was released
in 2026, after these 2025 games, so it was not backdated into a purported
point-in-time player-concept feature. Motion is not inferred from formations,
screens from pass distance, or RPO from ordinary play action.

## Frozen comparison and result

The baseline is the existing last-five role-positive appearance receptions B0.
An independently fitted scale-only control and a fixed-penalty (10) four-factor
ridge challenger were fitted on active matched 2025 regular-season weeks 2–8.
The one held evaluation uses weeks 9–18 and the same player-game rows for all
three forecasts. Current-week role-positive population membership is taken from
the retrospective weekly file; it cannot be reconstructed pregame from this
data, so the evaluation is **not an equal-volume operational selection test**.
The 2025 period was already inspected in other FULL COUNT work, and the exact
weekly files were obtained in 2026; their old revision states are not
authenticated. This is exploratory, not prospective validation.

| Matched held player games | Receptions MAE |
|---|---:|
| Unchanged B0 | 1.45673 |
| DEV-fitted scale-only control | 1.43098 |
| F14 concept challenger | 1.43159 |

The challenger is **0.00062 receptions worse** than the scale control. The
player-cluster bootstrap 95% interval for that difference is [−0.00422,
+0.00568], consistent with no demonstrated gain. DEV contained 1,016 active
rows; held activation was 1,389 of 2,399 candidate rows (57.9%), across 217
players. Do not promote F14 as an accuracy improvement.

For a real activation, CeeDee Lamb vs Arizona in 2025 Week 9 had B0 7.0,
scale control 6.405, and F14 6.327 receptions; actual was 7. The four
concept contributions (yards are **not** implied) were play action +0.050,
motion −0.072, RPO −0.024 and screen −0.003 receptions. The challenger changed
the forecast but worsened this example.

## Reproduction

Use only the two exact public 2024 charting files and pinned 2023–25
`stats_player_week_*.csv` files whose digests are in
`nfl.research.tier2.concepts_f14.SOURCES`. The CLI verifies every file:

```text
python -m nfl.research.tier2.concepts_f14 \
  --participation PATH/pbp_participation_2024.csv \
  --ftn PATH/ftn_charting_2024.csv \
  --stats-dir PATH_TO_WEEKLY_STATS \
  --output NEW_REPORT.json
python -m unittest nfl.tests.test_tier2_concepts_f14
```

`evaluation.json.gz` contains the complete UTF-8 `evaluation.json` output
compressed with gzip `mtime=0` to keep the review artifact small. Decompress
it before comparing a fresh CLI run. The uncompressed output was reproduced
byte-identically. Its internal report seal is
`edd5e15254e93d2556cb5b15c9bf8af8fe683301cf5ebc54acf46237a272d8a2`.
No B0, workflow, selector or public-pick change accompanies this result.
Alligator.

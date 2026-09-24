# NFL Tier 1, workstream B: player opportunity (F2, F3, F8, F9)

Branch `claude/nfl-tier1-player-opportunity-20260924`, built on the shared
contract and harness in `claude/nfl-tier1-foundation-20260924` (e3ca166de9).
This is research only. It does not change authoritative B0, any live workflow,
the official inactives gate, picks or selectors.

## What was built

| File | Purpose |
|---|---|
| `nfl/research/tier1/player_opportunity_features.py` | F2, F3, F8 and F9 feature builders and loaders (local files only, hashes recorded). Each builder reads strictly prior history and calls `contract.assert_strictly_prior`. |
| `nfl/research/tier1/player_opportunity_challenger.py` | The one consumer, `player_opportunity_challenger`. It has an ablation switch per factor, DEV-only parameter fitting and per-row attribution. |
| `nfl/research/tier1/player_opportunity_evaluate.py` | Runnable evaluation: `--phase dev` fits and freezes the parameters, `--phase full` scores all partitions once. Also writes status records, mass balance and injury-timing diagnostics. |
| `nfl/research/tier1/player_opportunity_live.py` | 2026 week 3 current-week feature rows with timestamped `information_cutoff`s. |
| `nfl/tests/test_tier1_player_opportunity.py` | 19 synthetic tests: known answers, leakage, UNKNOWN handling, mass balance and cap, fallback and attribution. |
| `frozen_params.json`, `dev_report.json` | DEV-fitted parameters and a DEV-only report, committed in **24231f9c6e before any holdout or fresh scoring**. |
| `evaluation_report.json` | The one full evaluation (holdout and fresh) of the frozen consumer, with status records and diagnostics. |
| `live_2026_w3_features.json` | 1,636 live feature rows (409 players × 4 factors) plus research-only per-market predictions. |

Reused, not copied:
- `role_intelligence_data_prep.parse_snap_counts_csv` and `parse_players_crosswalk_csv`, for the pfr to gsis join.
- `build_player_game_usage_rows`.
- `role_intelligence_features.build_teammate_absence_trigger_events`, the #183 events.
- `LARGE_USAGE_CHANGE_THRESHOLD`.
- `most_recent_prior_share` and `compute_mass_balance_diagnostics`.

Harness and contract were not modified.

## Consumer form (declared before holdout; see the module docstring)

`base = k_m × B0`, where `k_m` is the harness's own DEV-fitted scale control
(receptions 0.87, receiving_yards 0.78, rushing_yards 0.81). Every factor
adjusts `base`. When a factor's input is UNKNOWN, the row falls back exactly
to `base` (the scale control), and the fallback is counted
(`fallbacks` in the report). So the "Δ vs scale control" column measures
only the factor's own information.

- **F3.** `E_targets = target_share_last5 × team_targets_per_game_last5`.
  - Catch rate and yards per target are the player's last-5 rate, shrunk (M = 20 targets) toward a DEV line on prior aDOT:
    - catch rate = 0.757 − 0.0117·aDOT
    - yards per target = 6.13 + 0.156·aDOT
  - The prediction is a blend: `w·F3 + (1−w)·base`, with `w` = 0.35 (receptions) and 0.40 (receiving yards).
- **F2.** `base × clamp(snap_last3/snap_last5, 0.5, 2)^alpha[scenario]`.
  - The scenarios are specific: RISE_TEAMMATE_OUT_NOW, RISE_AFTER_RECENT_TEAMMATE_ABSENCE, RISE_NO_TEAMMATE_EVENT and FALL.
  - A rise or fall means the trend is at least ±0.15.
  - STABLE gets alpha = 0, so there is no blanket multiplier.
  - A BROAD_REFERENCE with one alpha on every row is scored separately, only to check the earlier finding.
- **F8.** `base × m[game status | practice status]`, with categories NOT_LISTED and RETURNING_FROM_OUT.
  - A category needs at least 200 DEV rows to be fitted; otherwise m = 1.
  - The fitted multipliers are in `frozen_params.json`.
- **F9.** `base × (share + gain)/share`.
  - `gain` comes from redistributing the prior target share (or carry share) of teammates listed OUT on the pregame report.
  - Recipients are the players who appeared in the team's last 3 games and are not OUT or DOUBTFUL.
  - Shares are split in proportion to prior share, with the same position first.
  - The vacated share is scaled by how much it overlaps the recipient's own B0-window games, so an absence already inside B0 is not added twice.
  - DEV-fitted retention is 0.4 to 0.5. No player receives more than 50% of a vacated share.
- **ALL** is the F3-blended base × F2 × F8 × F9, each with its own separately fitted parameters (no joint refit).

## Factor status (declared rule in `player_opportunity_evaluate.status_records`)

Each market gets a verdict, always measured against the scale control:
- **VALIDATED_CRITERION_MET**: the holdout CI is entirely below 0 and the fresh-2026 point estimate is ≤ 0.
- **HOLDOUT_SUPPORTED_FRESH_WORSE**: the holdout CI is below 0, but fresh 2026 is worse.
- **NO_HOLDOUT_BENEFIT_VS_SCALE**: otherwise.

The factor milestone is VALIDATED only if every primary market (receptions, receiving yards) meets the criterion. It is REJECTED if no market has holdout support, and BUILT otherwise. The holdout was inspected by earlier experiments, so VALIDATED here means "historically supported, not prospectively validated".

| Factor | Milestone | Consumer | Holdout activation | Receptions (holdout Δ vs scale [CI]; fresh Δ) | Receiving yards | Rushing yards | Blockers / notes |
|---|---|---|---|---|---|---|---|
| F2_SNAP_SHARE_ROLE | **BUILT** | player_opportunity_challenger | 25% (rec), 24% (rush) | −0.0020 [−0.0036, −0.0005]; fresh +0.0049 (worse) | −0.016 [−0.038, +0.002], NO benefit; fresh +0.079 [+0.001, +0.164], significantly worse | −0.072 [−0.112, −0.034]; fresh +0.047 (worse) | The effect is tiny and does not hold on fresh 2026. The one sharp scenario is RISE_TEAMMATE_OUT_NOW for rushing (holdout −3.58 [−5.51, −1.83], n=55). |
| F3_TARGET_AIR_YARDS | **VALIDATED** (historically supported, not prospectively validated) | player_opportunity_challenger | 99.5% | −0.0089 [−0.0119, −0.0056]; fresh −0.0077 [−0.024, +0.009] | −0.194 [−0.250, −0.140]; fresh −0.169 [−0.463, +0.108] | n/a | Fresh n = 436 and its CIs span 0. See caveat 1. |
| F8_INJURY_PRACTICE | **REJECTED** | player_opportunity_challenger | 7% (rec), 16% (yds), 2% (rush) | −0.0000 [−0.0009, +0.0009] | +0.005 [−0.016, +0.026]; fresh +0.070 [+0.009, +0.149], worse | −0.003 [−0.016, +0.009] | No gain even within listed players (subset table). Selection effect: see below. |
| F9_ABSENCE_REDISTRIBUTION | **BUILT** (receptions alone meets the VALIDATED criterion) | player_opportunity_challenger | 14% (rec), 34% (yds), 8% (rush) | −0.0035 [−0.0059, −0.0011]; fresh −0.0039 [−0.011, +0.0003] | −0.025 [−0.050, +0.001]; fresh −0.040 [−0.092, −0.007] | −0.060 [−0.117, −0.008]; fresh +0.122 [+0.002, +0.304], worse (n=222, 3% active) | Pregame trigger is OUT only; see what it misses below. |

ALL (every factor together) against the scale control, on the holdout:
- receptions: −0.0145 [−0.0188, −0.0099]
- receiving yards: −0.244 [−0.313, −0.174]
- rushing yards: −0.138 [−0.214, −0.071]

On fresh 2026, ALL is −0.005 (receptions), −0.097 (receiving yards) and +0.151 (rushing yards, worse). Every CI spans 0.

Against raw B0, every ablation looks better, because most of that improvement is the scale shrinkage (B0 is biased high). Only the scale-control column credits a factor.

## Full results

| Ablation | Market | Partition | n matched | activation (vs scale) | Δ MAE vs B0 [95% CI] | Δ MAE vs scale control [95% CI] | challenger bias |
|---|---|---|---|---|---|---|---|
| F2 | receptions | DEV_2016_2022 | 27101 | 0.273 | -0.0339 [-0.0394, -0.0287] | -0.0023 [-0.0034, -0.0012] | -0.366 |
| F2 | receptions | HOLDOUT_2023_2025 | 12094 | 0.254 | -0.0317 [-0.0401, -0.0240] | -0.0020 [-0.0036, -0.0005] | -0.343 |
| F2 | receptions | FRESH_2026 | 436 | 0.250 | -0.0166 [-0.0504, +0.0154] | +0.0049 [-0.0001, +0.0096] | -0.391 |
| F3 | receptions | DEV_2016_2022 | 27101 | 0.995 | -0.0386 [-0.0427, -0.0344] | -0.0070 [-0.0092, -0.0048] | -0.275 |
| F3 | receptions | HOLDOUT_2023_2025 | 12094 | 0.994 | -0.0386 [-0.0451, -0.0321] | -0.0089 [-0.0119, -0.0056] | -0.267 |
| F3 | receptions | FRESH_2026 | 436 | 0.995 | -0.0292 [-0.0600, +0.0020] | -0.0077 [-0.0241, +0.0086] | -0.307 |
| F8 | receptions | DEV_2016_2022 | 27101 | 0.078 | -0.0321 [-0.0375, -0.0269] | -0.0005 [-0.0012, +0.0002] | -0.369 |
| F8 | receptions | HOLDOUT_2023_2025 | 12094 | 0.072 | -0.0297 [-0.0381, -0.0219] | -0.0000 [-0.0009, +0.0009] | -0.342 |
| F8 | receptions | FRESH_2026 | 436 | 0.034 | -0.0201 [-0.0551, +0.0128] | +0.0015 [-0.0014, +0.0048] | -0.386 |
| F9 | receptions | DEV_2016_2022 | 27101 | 0.135 | -0.0344 [-0.0395, -0.0296] | -0.0028 [-0.0043, -0.0012] | -0.324 |
| F9 | receptions | HOLDOUT_2023_2025 | 12094 | 0.142 | -0.0332 [-0.0408, -0.0258] | -0.0035 [-0.0059, -0.0011] | -0.299 |
| F9 | receptions | FRESH_2026 | 436 | 0.055 | -0.0255 [-0.0595, +0.0064] | -0.0039 [-0.0110, +0.0003] | -0.374 |
| ALL | receptions | DEV_2016_2022 | 27101 | 0.996 | -0.0444 [-0.0490, -0.0399] | -0.0128 [-0.0155, -0.0099] | -0.266 |
| ALL | receptions | HOLDOUT_2023_2025 | 12094 | 0.995 | -0.0442 [-0.0513, -0.0375] | -0.0145 [-0.0188, -0.0099] | -0.255 |
| ALL | receptions | FRESH_2026 | 436 | 0.998 | -0.0267 [-0.0578, +0.0066] | -0.0052 [-0.0251, +0.0140] | -0.314 |
| F2 | receiving_yards | DEV_2016_2022 | 27101 | 0.273 | -0.8507 [-0.9500, -0.7549] | -0.0254 [-0.0390, -0.0120] | -7.056 |
| F2 | receiving_yards | HOLDOUT_2023_2025 | 12094 | 0.254 | -0.8351 [-0.9871, -0.6728] | -0.0160 [-0.0381, +0.0019] | -6.660 |
| F2 | receiving_yards | FRESH_2026 | 436 | 0.248 | -0.7126 [-1.3887, -0.0273] | +0.0793 [+0.0011, +0.1635] | -6.682 |
| F3 | receiving_yards | DEV_2016_2022 | 27101 | 0.995 | -0.9900 [-1.0587, -0.9177] | -0.1646 [-0.2048, -0.1248] | -4.420 |
| F3 | receiving_yards | HOLDOUT_2023_2025 | 12094 | 0.994 | -1.0134 [-1.1308, -0.8997] | -0.1944 [-0.2498, -0.1402] | -4.249 |
| F3 | receiving_yards | FRESH_2026 | 436 | 0.995 | -0.9609 [-1.5836, -0.3504] | -0.1691 [-0.4625, +0.1080] | -4.329 |
| F8 | receiving_yards | DEV_2016_2022 | 27101 | 0.166 | -0.8398 [-0.9359, -0.7425] | -0.0145 [-0.0297, +0.0010] | -6.969 |
| F8 | receiving_yards | HOLDOUT_2023_2025 | 12094 | 0.161 | -0.8138 [-0.9702, -0.6614] | +0.0052 [-0.0164, +0.0262] | -6.537 |
| F8 | receiving_yards | FRESH_2026 | 436 | 0.094 | -0.7219 [-1.3917, -0.0357] | +0.0699 [+0.0091, +0.1485] | -6.484 |
| F9 | receiving_yards | DEV_2016_2022 | 27101 | 0.326 | -0.8442 [-0.9381, -0.7534] | -0.0188 [-0.0345, -0.0030] | -6.335 |
| F9 | receiving_yards | HOLDOUT_2023_2025 | 12094 | 0.336 | -0.8436 [-0.9893, -0.6965] | -0.0246 [-0.0501, +0.0010] | -5.952 |
| F9 | receiving_yards | FRESH_2026 | 436 | 0.135 | -0.8321 [-1.4981, -0.1652] | -0.0402 [-0.0916, -0.0073] | -6.324 |
| ALL | receiving_yards | DEV_2016_2022 | 27101 | 0.996 | -1.0592 [-1.1323, -0.9826] | -0.2338 [-0.2804, -0.1845] | -4.631 |
| ALL | receiving_yards | HOLDOUT_2023_2025 | 12094 | 0.995 | -1.0630 [-1.1900, -0.9402] | -0.2440 [-0.3127, -0.1742] | -4.354 |
| ALL | receiving_yards | FRESH_2026 | 436 | 0.995 | -0.8892 [-1.5107, -0.2995] | -0.0973 [-0.4015, +0.1962] | -4.615 |
| F2 | rushing_yards | DEV_2016_2022 | 13168 | 0.234 | -0.7275 [-0.8545, -0.6072] | -0.0417 [-0.0708, -0.0119] | -5.629 |
| F2 | rushing_yards | HOLDOUT_2023_2025 | 6148 | 0.244 | -0.7319 [-0.9236, -0.5442] | -0.0724 [-0.1120, -0.0344] | -5.396 |
| F2 | rushing_yards | FRESH_2026 | 222 | 0.207 | -1.2577 [-2.0273, -0.3374] | +0.0472 [-0.1075, +0.2021] | -4.387 |
| F8 | rushing_yards | DEV_2016_2022 | 13168 | 0.031 | -0.6917 [-0.8185, -0.5731] | -0.0060 [-0.0172, +0.0051] | -5.612 |
| F8 | rushing_yards | HOLDOUT_2023_2025 | 6148 | 0.021 | -0.6627 [-0.8577, -0.4737] | -0.0032 [-0.0161, +0.0088] | -5.318 |
| F8 | rushing_yards | FRESH_2026 | 222 | 0.000 | -1.3049 [-2.0535, -0.3741] | +0.0000 [+0.0000, +0.0000] | -4.135 |
| F9 | rushing_yards | DEV_2016_2022 | 13168 | 0.105 | -0.7453 [-0.8671, -0.6298] | -0.0595 [-0.0906, -0.0265] | -5.165 |
| F9 | rushing_yards | HOLDOUT_2023_2025 | 6148 | 0.075 | -0.7195 [-0.9116, -0.5301] | -0.0600 [-0.1171, -0.0084] | -4.904 |
| F9 | rushing_yards | FRESH_2026 | 222 | 0.032 | -1.1833 [-1.9416, -0.2745] | +0.1216 [+0.0016, +0.3039] | -4.011 |
| ALL | rushing_yards | DEV_2016_2022 | 13168 | 0.330 | -0.7796 [-0.9100, -0.6610] | -0.0939 [-0.1387, -0.0475] | -5.378 |
| ALL | rushing_yards | HOLDOUT_2023_2025 | 6148 | 0.311 | -0.7978 [-0.9979, -0.5997] | -0.1383 [-0.2142, -0.0706] | -5.104 |
| ALL | rushing_yards | FRESH_2026 | 222 | 0.225 | -1.1537 [-1.9446, -0.2313] | +0.1512 [-0.0389, +0.3796] | -4.270 |

### F2 specific role-change scenarios (each scored alone, within its own rows, vs the scale control)

| Market | Scenario | DEV alpha | Partition | n | Δ vs scale [95% CI] |
|---|---|---|---|---|---|
| receptions | FALL | 0.4 | DEV | 3224 | -0.0150 [-0.0226, -0.0077] |
| receptions | FALL | 0.4 | HOLDOUT | 1478 | -0.0119 [-0.0228, -0.0012] |
| receptions | FALL | 0.4 | FRESH | 61 | +0.0295 [-0.0005, +0.0620] |
| receptions | RISE_AFTER_RECENT_TEAMMATE_ABSENCE | -0.1 | DEV | 300 | -0.0003 [-0.0060, +0.0048] |
| receptions | RISE_AFTER_RECENT_TEAMMATE_ABSENCE | -0.1 | HOLDOUT | 154 | -0.0057 [-0.0124, +0.0002] |
| receptions | RISE_NO_TEAMMATE_EVENT | 0.2 | DEV | 3613 | -0.0018 [-0.0046, +0.0011] |
| receptions | RISE_NO_TEAMMATE_EVENT | 0.2 | HOLDOUT | 1334 | -0.0038 [-0.0085, +0.0007] |
| receptions | RISE_NO_TEAMMATE_EVENT | 0.2 | FRESH | 46 | +0.0126 [-0.0096, +0.0344] |
| receptions | RISE_TEAMMATE_OUT_NOW | 0.7 | DEV | 260 | -0.0271 [-0.0631, +0.0089] |
| receptions | RISE_TEAMMATE_OUT_NOW | 0.7 | HOLDOUT | 112 | -0.0062 [-0.0675, +0.0580] |
| receptions | RISE_TEAMMATE_OUT_NOW | 0.7 | FRESH | 2 | -0.1232 n/a |
| receptions | BROAD_REFERENCE (every row, one alpha) | 0.3 | DEV | 27101 | -0.0028 [-0.0040, -0.0016] |
| receptions | BROAD_REFERENCE (every row, one alpha) | 0.3 | HOLDOUT | 12094 | -0.0026 [-0.0044, -0.0010] |
| receptions | BROAD_REFERENCE (every row, one alpha) | 0.3 | FRESH | 436 | +0.0027 [-0.0041, +0.0098] |
| receiving_yards | FALL | 0.6 | DEV | 3224 | -0.1998 [-0.3059, -0.0934] |
| receiving_yards | FALL | 0.6 | HOLDOUT | 1478 | -0.1332 [-0.2870, +0.0321] |
| receiving_yards | FALL | 0.6 | FRESH | 61 | +0.6500 [+0.1483, +1.1804] |
| receiving_yards | RISE_AFTER_RECENT_TEAMMATE_ABSENCE | -0.2 | DEV | 300 | -0.0113 [-0.1313, +0.0975] |
| receiving_yards | RISE_AFTER_RECENT_TEAMMATE_ABSENCE | -0.2 | HOLDOUT | 154 | -0.0260 [-0.1642, +0.0946] |
| receiving_yards | RISE_NO_TEAMMATE_EVENT | -0.1 | DEV | 3613 | -0.0004 [-0.0150, +0.0135] |
| receiving_yards | RISE_NO_TEAMMATE_EVENT | -0.1 | HOLDOUT | 1334 | +0.0211 [+0.0004, +0.0430] |
| receiving_yards | RISE_NO_TEAMMATE_EVENT | -0.1 | FRESH | 46 | -0.0383 [-0.1701, +0.1004] |
| receiving_yards | RISE_TEAMMATE_OUT_NOW | 0.5 | DEV | 260 | -0.1477 [-0.4470, +0.1592] |
| receiving_yards | RISE_TEAMMATE_OUT_NOW | 0.5 | HOLDOUT | 112 | -0.1898 [-0.5651, +0.1781] |
| receiving_yards | RISE_TEAMMATE_OUT_NOW | 0.5 | FRESH | 2 | -1.6589 n/a |
| receiving_yards | BROAD_REFERENCE (every row, one alpha) | 0.3 | DEV | 27101 | -0.0230 [-0.0348, -0.0113] |
| receiving_yards | BROAD_REFERENCE (every row, one alpha) | 0.3 | HOLDOUT | 12094 | -0.0265 [-0.0425, -0.0114] |
| receiving_yards | BROAD_REFERENCE (every row, one alpha) | 0.3 | FRESH | 436 | +0.0119 [-0.0785, +0.1056] |
| rushing_yards | FALL | 0.6 | DEV | 1416 | -0.2564 [-0.4660, -0.0529] |
| rushing_yards | FALL | 0.6 | HOLDOUT | 719 | -0.3896 [-0.6606, -0.1154] |
| rushing_yards | FALL | 0.6 | FRESH | 32 | -0.2595 [-1.3172, +0.7494] |
| rushing_yards | RISE_AFTER_RECENT_TEAMMATE_ABSENCE | -0.1 | DEV | 148 | -0.0004 [-0.1306, +0.1229] |
| rushing_yards | RISE_AFTER_RECENT_TEAMMATE_ABSENCE | -0.1 | HOLDOUT | 63 | +0.0320 [-0.1388, +0.2144] |
| rushing_yards | RISE_NO_TEAMMATE_EVENT | 0.3 | DEV | 1492 | -0.0494 [-0.1479, +0.0484] |
| rushing_yards | RISE_NO_TEAMMATE_EVENT | 0.3 | HOLDOUT | 695 | +0.0429 [-0.0859, +0.1696] |
| rushing_yards | RISE_NO_TEAMMATE_EVENT | 0.3 | FRESH | 14 | +1.3422 [+0.5271, +2.2596] |
| rushing_yards | RISE_TEAMMATE_OUT_NOW | 1.0 | DEV | 86 | -1.3123 [-3.1567, +0.7307] |
| rushing_yards | RISE_TEAMMATE_OUT_NOW | 1.0 | HOLDOUT | 55 | -3.5753 [-5.5116, -1.8273] |
| rushing_yards | BROAD_REFERENCE (every row, one alpha) | 0.5 | DEV | 13168 | -0.0563 [-0.0921, -0.0183] |
| rushing_yards | BROAD_REFERENCE (every row, one alpha) | 0.5 | HOLDOUT | 6148 | -0.0559 [-0.1055, -0.0082] |
| rushing_yards | BROAD_REFERENCE (every row, one alpha) | 0.5 | FRESH | 222 | +0.1883 [+0.0151, +0.3665] |

### Subsets (global DEV k; vs the scale control)

| Market | Subset | Partition | n | activation | Δ vs scale [95% CI] |
|---|---|---|---|---|---|
| receptions | F8_listed_or_returning_players | DEV | 5357 | 0.396 | -0.0025 [-0.0058, +0.0009] |
| receptions | F8_listed_or_returning_players | HOLDOUT | 2429 | 0.358 | -0.0000 [-0.0045, +0.0045] |
| receptions | F8_listed_or_returning_players | FRESH | 48 | 0.312 | +0.0135 [-0.0125, +0.0403] |
| receptions | F9_within_any_out_teammate_overlap_BROAD | DEV | 18258 | 0.201 | -0.0041 [-0.0064, -0.0019] |
| receptions | F9_within_any_out_teammate_overlap_BROAD | HOLDOUT | 8573 | 0.201 | -0.0050 [-0.0081, -0.0015] |
| receptions | F9_within_any_out_teammate_overlap_BROAD | FRESH | 194 | 0.124 | -0.0088 [-0.0229, +0.0001] |
| receptions | F9_within_observed_absence_scenarios | DEV | 8890 | 0.412 | -0.0084 [-0.0133, -0.0036] |
| receptions | F9_within_observed_absence_scenarios | HOLDOUT | 4102 | 0.420 | -0.0104 [-0.0176, -0.0031] |
| receptions | F9_within_observed_absence_scenarios | FRESH | 60 | 0.400 | -0.0285 [-0.0759, +0.0003] |
| receiving_yards | F8_listed_or_returning_players | DEV | 5357 | 0.841 | -0.0731 [-0.1533, -0.0020] |
| receiving_yards | F8_listed_or_returning_players | HOLDOUT | 2429 | 0.800 | +0.0258 [-0.0802, +0.1246] |
| receiving_yards | F8_listed_or_returning_players | FRESH | 48 | 0.854 | +0.6352 [+0.1042, +1.1508] |
| receiving_yards | F9_within_any_out_teammate_overlap_BROAD | DEV | 18258 | 0.484 | -0.0280 [-0.0509, -0.0041] |
| receiving_yards | F9_within_any_out_teammate_overlap_BROAD | HOLDOUT | 8573 | 0.475 | -0.0347 [-0.0698, -0.0003] |
| receiving_yards | F9_within_any_out_teammate_overlap_BROAD | FRESH | 194 | 0.304 | -0.0903 [-0.2045, -0.0181] |
| receiving_yards | F9_within_observed_absence_scenarios | DEV | 8890 | 0.994 | -0.0574 [-0.1101, -0.0098] |
| receiving_yards | F9_within_observed_absence_scenarios | HOLDOUT | 4102 | 0.992 | -0.0725 [-0.1501, +0.0045] |
| receiving_yards | F9_within_observed_absence_scenarios | FRESH | 60 | 0.983 | -0.2921 [-0.5956, -0.0697] |
| rushing_yards | F8_listed_or_returning_players | DEV | 2468 | 0.165 | -0.0318 [-0.0925, +0.0324] |
| rushing_yards | F8_listed_or_returning_players | HOLDOUT | 1132 | 0.117 | -0.0172 [-0.0867, +0.0477] |
| rushing_yards | F8_listed_or_returning_players | FRESH | 20 | 0.000 | +0.0000 [+0.0000, +0.0000] |
| rushing_yards | F9_within_any_out_teammate_overlap_BROAD | DEV | 8802 | 0.157 | -0.0890 [-0.1381, -0.0412] |
| rushing_yards | F9_within_any_out_teammate_overlap_BROAD | HOLDOUT | 4312 | 0.108 | -0.0855 [-0.1604, -0.0119] |
| rushing_yards | F9_within_any_out_teammate_overlap_BROAD | FRESH | 86 | 0.081 | +0.3139 [+0.0042, +0.8067] |
| rushing_yards | F9_within_observed_absence_scenarios | DEV | 2894 | 0.478 | -0.2707 [-0.4105, -0.1215] |
| rushing_yards | F9_within_observed_absence_scenarios | HOLDOUT | 1139 | 0.407 | -0.3237 [-0.5997, -0.0356] |
| rushing_yards | F9_within_observed_absence_scenarios | FRESH | 24 | 0.292 | +1.1247 [+0.0913, +2.8538] |

## Negative and null findings (kept on purpose)

1. **F8 injury and practice status adds nothing beyond B0 and the scale control** for players who went on to play.
   - Holdout deltas are within ±0.005 for receptions and receiving yards, and fresh 2026 is significantly worse for receiving yards.
   - DEV-fitted multipliers such as QUESTIONABLE|FULL at 0.79 for yards did not carry to the holdout.
   - **Selection effect:** the harness scores only role-positive rows (the player recorded a target or carry). Players ruled out, or inactive on game day, never enter the population. F8 was therefore tested only as a workload adjustment for players who played, not as an availability forecast, and this does not measure its value for availability.
2. **Specific F2 scenarios are mostly null.**
   - RISE_NO_TEAMMATE_EVENT and RISE_AFTER_RECENT_TEAMMATE_ABSENCE carry no information in any market: their CIs span 0, and one is slightly worse on the holdout for receiving yards.
   - RISE_TEAMMATE_OUT_NOW is sharp only for rushing, where a rising RB with the top RB still OUT keeps his new role (holdout n = 55).
   - FALL helps on DEV and holdout but is significantly worse on fresh 2026 for receiving yards (+0.65, n = 61).
   - The BROAD_REFERENCE multiplier (one alpha on every row) is slightly helpful on the holdout. That differs from the earlier finding (MAE 1.386 → 1.478 in `receptions_team_opportunity_challenger`), which used a different form: a season-over-season ratio on a 2025-only population.
   - Both the broad multiplier and F2 as a whole are worse on fresh 2026. Neither is credited.
3. **F9 does not generalize to rushing on fresh 2026.** It is +0.12 overall and +1.12 within observed absences, but from only 24 rows. It helps receptions consistently, and receiving yards on fresh 2026 and within observed absences, but the overall holdout CI for receiving yards touches 0.
4. **Mass balance.**
   - Over 4,281 team-weeks with an OUT teammate, the redistribution never over-allocates. Over-allocation is 0 in every market.
   - The largest share of a vacated opportunity any one player received is 0.40 for receptions, 0.375 for receiving yards and 0.50 for rushing. The 0.50 is the declared cap.
   - Between 36% and 39% of the retained budget stays unallocated, because of the overlap discount and the cap.
   - The reused #183 `compute_mass_balance_diagnostics`, run on the 500 top-player events, reports a small mean over-allocation (WR 0.006, RB 0.009). That happens because its budget is the removed player's previous-game share, while ours is his last-5 share. The disagreement is surfaced, not resolved.

## Caveats

1. **F3 is close to an alternative estimator of the whole row** (99.5% activation), not a narrow adjustment. Some of its gain may come from using every appearance (including 0-target games) and team volume, where B0 averages only role appearances. That is legitimate prior information, but this report does not separate it from the aDOT and share content. A follow-up ablation (F3 without the aDOT prior, and B0 over all appearances) would.
2. **The F9 "observed absence" subset** (an OUT teammate who held prior target or carry share and overlapped the player's B0 window) was corrected after the first full run.
   - The first definition counted any OUT teammate, including defenders and linemen.
   - Both definitions are reported: `..._BROAD` and `F9_within_observed_absence_scenarios`.
   - The consumer and its frozen parameters did not change. The full run was repeated only to add this reporting subset and the status records, and the numbers are byte-for-byte deterministic.
3. **Routes:** no route-participation source is ingested. Targets are targets, not routes, and `route_share` is UNKNOWN.
4. **Snap identity:** pfr to gsis goes only through nflverse `players.csv` (SHA-256 4dd70f32…, which matches the pin in `role_intelligence_source_digests`).
   - Unmatched rows with offense snaps > 0: 113 of 98,182 (0.115%). For skill positions: 74 of 57,924 (0.128%).
   - Snap counts are REG only.
5. **Team codes:** snap counts and injuries use the codes of the time (SD, OAK, STL), while weekly stats use current codes. They are normalized to LAC, LV and LA.
6. **Injury availability rule:** each row is treated as that week's final pregame report (Friday for Sunday games). The exact filing time is unknown.
   - 2016–2024 rows carry `date_modified`. Only 24 of 47,640 (0.05%) were modified after kickoff. That is reported as leakage risk, not corrected.
   - 2025–2026 rows carry no timestamp at all.
   - Practice DNP is not game-day inactive. The live official inactives gate is not touched.
7. **F9 trigger rule:** absent means listed OUT on the pregame report. This misses:
   - IR, PUP and NFI players (removed from the report)
   - suspensions, healthy scratches, trades and releases
   - Questionable or Doubtful players declared inactive on game day
   - in-game injuries

   Appearing in the box score is never used as a trigger.
8. **Holdout 2023–2025** had been inspected by earlier experiments, so it is exploratory. Fresh 2026 has only 436 receiving rows and 222 rushing rows.

## Live path (2026 week 3)

`PYTHONPATH=. python3 -m nfl.research.tier1.player_opportunity_live --target-season 2026 --target-week 3`

This writes `live_2026_w3_features.json`.
- Each row is `contract.validate_feature_row(row, prediction_cutoff=<game kickoff UTC>)`. 0 rows were rejected.
- `information_cutoff` is the local retrieval time (file mtime, UTC) of the newest source the row used. That is an upper bound on when the information became available.
  - F2: 2026-09-24T21:02:10Z (players.csv)
  - F3: 20:59:28Z
  - F8 and F9: 21:00:16Z
  - The earliest kickoff is ATL at GB, 2026-09-25T00:15Z.

| Factor | Live status |
|---|---|
| F2 | BUILT. The teammate-out scenario split is `UNKNOWN_PENDING_FINAL_INJURY_REPORT` for teams whose final report is not filed; FALL and STABLE are unaffected. |
| F3 | BUILT |
| F8 | PARTIAL. As retrieved, only ATL and GB (Thursday game) have a final report with game statuses. For the other 30 teams, the file holds practice status only, so game status is `UNKNOWN_FINAL_REPORT_NOT_YET_FILED` and the consumer falls back. |
| F9 | PARTIAL, for the same reason. |

To refresh, re-download `injuries_2026.csv` after Friday's final reports and rerun. A team-week counts as final only once at least one game status is present, so a team with zero designations stays UNKNOWN (fail-closed). Placeholder usage rows let the #183 trigger rank the live week without any realized numbers (`live_events`). `research_predictions` holds k·B0 and ALL-challenger values for research only. They are not picks.

## Reproduce

```
PYTHONPATH=. python3 nfl/tests/test_tier1_player_opportunity.py
PYTHONPATH=. python3 -m nfl.research.tier1.player_opportunity_evaluate --phase dev    # refits on DEV only
PYTHONPATH=. python3 -m nfl.research.tier1.player_opportunity_evaluate --phase full   # frozen params, all partitions
```

The inputs are the shared files under `/tmp/claude-0/nfl_tier1_shared/` and the audited weekly cache, plus `players.csv`, downloaded 2026-09-24T21:02:10Z to `/tmp/claude-0/nfl_tier1_B/`. The DEV phase takes about 2 minutes and the full phase about 2 minutes.

## Source SHA-256

| File | SHA-256 |
|---|---|
| nfl_tier1_B/players.csv | 4dd70f328f31b0bb7cbf043412298d5a325863e27b8f2eeea22c9e925c808dee |
| nfl_tier1_shared/injuries/injuries_2016.csv | 504e05968cb9c54ddc730fd9b6e7d1dd16a62db8029cc784d3d7bafcea7ff7cb |
| nfl_tier1_shared/injuries/injuries_2017.csv | 0d5139fbf41b0866517bd2bd5fd85bb20dbd4dd9bc36a706fed030a88731de6f |
| nfl_tier1_shared/injuries/injuries_2018.csv | 4724e1f37cc3076f564e997fbf269190e7dbe795a39315b903788e0a9c40065e |
| nfl_tier1_shared/injuries/injuries_2019.csv | daddfe8e04ddc3a29bce14f362e774fbadfb274c807f1f0fde7b5bf32aaf6cdf |
| nfl_tier1_shared/injuries/injuries_2020.csv | 706cca82824214f6b73ba0c6e537d3ee39766a50c644b8974b9fc28d7518de90 |
| nfl_tier1_shared/injuries/injuries_2021.csv | 1049fb9ff0fe7cfcf3ba8bfe1f5a35d3aba985c461c8fb7bf5201735b6d34254 |
| nfl_tier1_shared/injuries/injuries_2022.csv | 5f0d60324e597edd1a2ced8540b04e40b51dd440591dd1df11b8d96343387c9e |
| nfl_tier1_shared/injuries/injuries_2023.csv | 16b04e21da5aa3944cfa9c22cafcc84ed7e6d9866eaddf0344e9b8cbf2f63afe |
| nfl_tier1_shared/injuries/injuries_2024.csv | 498bce8e13cb64b2ab9bb0ad6cb81d0a63c2ddb24016c9fc90c2de2126fae449 |
| nfl_tier1_shared/injuries/injuries_2025.csv | 873ca1606dd575bd01152508a243ef6b3a0f8f97b90b707217e62ee8c7ceb735 |
| nfl_tier1_shared/injuries/injuries_2026.csv | b9f0740139d052ffb14bd23930f28cccd06547983a3fc4d2b7cd6192eb6aab01 |
| nfl_tier1_shared/schedules/games.csv | 7fdc123e11cf224b97d120980a4cd18171147f6db6d4c3b193f27406b4856c4e |
| nfl_tier1_shared/snap_counts/snap_counts_2016.csv | a778e24e9eb665ffe8f16093b03c5a263dca0250b3aa92bd6002f61f534bea59 |
| nfl_tier1_shared/snap_counts/snap_counts_2017.csv | eab2fad2df99249d4061ed9c5c34d312cdba07cf3159d027d255bdc9b6526917 |
| nfl_tier1_shared/snap_counts/snap_counts_2018.csv | 9a408b78a55f799110aed70de3564e157faf089ce9bed780d0368bf49ae13e0b |
| nfl_tier1_shared/snap_counts/snap_counts_2019.csv | 0cd52be8fa57f18503b670cb8263246c4f548375b768cbcad1c7a7462c98970d |
| nfl_tier1_shared/snap_counts/snap_counts_2020.csv | 512e35f17d076eb5d99b933c4a144eb95dcddf40c0320dbb397cae3c35683716 |
| nfl_tier1_shared/snap_counts/snap_counts_2021.csv | 8e4dae054a4749cf2d4919508d9161a6068fd67509979aefa385bfb3803d0ee5 |
| nfl_tier1_shared/snap_counts/snap_counts_2022.csv | 0018a4833fbf0f825286c1c27450c6254391b548d6c55bcde728e93e4816815a |
| nfl_tier1_shared/snap_counts/snap_counts_2023.csv | 303b61aa5c33ffda863f93a750fc14483f397f9187ad502b1ce71e9b516a64c0 |
| nfl_tier1_shared/snap_counts/snap_counts_2024.csv | a2aa58efe093f8aa0ad5aadf09f81d8ec690a1183bd2dde68d20e7f109a9c335 |
| nfl_tier1_shared/snap_counts/snap_counts_2025.csv | 3fc2deb0e9ad86d34d4578cb80bb21c95253e088ee22ca028adf46f7485eff1f |
| nfl_tier1_shared/snap_counts/snap_counts_2026.csv | de7dee6776781052e85fd697d13e6c1d18fb22e04df0b2794cfd0ec758c7efab |
| nfl_tier1_shared/stats_player_week_2026.csv | 736bdddef4779023f7eb1831a1f2c8627181aee60cc280d5f0464cf5f41a8e67 |
| engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json | 35eaf8cb803f5d1a516bd7873d090a133ec5ab7567854f54353dcf1e23f45e40 |
| weekly stats 2015-2025 | verified against the audit manifest (per-season hashes in evaluation_report.json provenance) |

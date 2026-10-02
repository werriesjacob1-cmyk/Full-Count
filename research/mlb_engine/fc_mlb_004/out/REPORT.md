# FC-MLB-004 — pitcher contact-suppression: NO_GAIN (K=10, P1 vs P0)

**DEVELOPMENT_EVIDENCE (2026 outcomes previously visible; hypothesis chosen after FC-MLB-002 2026 results -> doubly development; not confirmatory)**

- Criteria: engineering/ops/TASKS/FC-MLB-004.md @ claude/full-count-ops-state (sha256 3f627ad8...)
- FC-MLB-002 frozen fit sha256 `6c0ecde23577659c1e72e18448415208eb4f5d7d2748f24b9631cd01ded7d6aa` (P0 = its CH1a, unchanged)
- FC-MLB-004 frozen fit sha256 `fbe9362fe2ac0dddcb85ad363a1056ad4392a7a109385cca374e5d459e026c42`; kpx=150, kd=1500; P2 valid point-in-time: True
- Fidelity gate (P0, CH0 vs FC-MLB-002 b52ac418ba predictions): {'rows': 37125, 'max_abs_diff_CH0': 5.551115123125783e-17, 'max_abs_diff_P0_vs_CH1a': 0.0}
- Population: {'player_games': 37125, 'dates': 163, 'first': '2026-04-15', 'last': '2026-09-27', 'h1_base_rate': 0.6113131313131314, 'side_excluded': 0, 'switch_player_games': 4035}

## Frozen 2025 fit

- kpx grid (2025 Jul16–end holdout mean log-lik): [[-0.5266875831008208, 50], [-0.5266852023430016, 150], [-0.5267410137999259, 400], [-0.5268443422970006, 1000]]; kd grid: [[-0.5266796873094644, 500], [-0.5266648861957737, 1500], [-0.5266720125511285, 4000]]
- P1: intercept=0.613, Lb=0.572, Lpx=0.890, same_hand=-0.015
- P2: intercept=0.581, Lb=0.573, Lpx=0.869, same_hand=-0.015, Dteam=0.573

| 2025 split model (fit Apr15–Jul15) | holdout logloss/PA |
|---|---|
| P0_CH1a_refit_reference | 0.526880 |
| P1 | 0.526685 |
| P2 | 0.526665 |
| diag_P1_plus_pitcher_residual | 0.526687 |

## Primary — realized Hits 1+ at equal volume (top-min(K, eligible) per date; paired 95% date-block CI, pp)

| K | selected each | dates short | CH0 | P0 | P1 | P2 | P1−P0 | P2−P0 | P2−P1 | P1−CH0 | P0−CH0 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 815 | 0 | 0.6675 | 0.7141 | 0.7129 | 0.7202 | -0.12 [-2.94, +2.70] | +0.61 [-1.96, +3.19] | +0.74 [-1.35, +2.82] | +4.54 [+0.61, +8.47] | +4.66 [+0.61, +8.47] |
| 10 | 1630 | 0 | 0.6779 | 0.7037 | 0.7018 | 0.7153 | -0.18 [-1.96, +1.72] | +1.17 [-0.55, +2.94] | +1.35 [+0.18, +2.52] | +2.39 [+0.06, +4.72] | +2.58 [+0.12, +5.09] |
| 20 | 3258 | 1 | 0.6681 | 0.6883 | 0.6917 | 0.6923 | +0.34 [-0.86, +1.63] | +0.40 [-0.67, +1.56] | +0.06 [-0.80, +0.92] | +2.36 [+0.74, +4.14] | +2.02 [+0.46, +3.59] |

Reduces early instability (Apr–Jun P1−CH0 gain exceeds P0−CH0 by ≥1 pp): False

## Stability (K=10, pp; logloss gain ×1e4 per player-game)

| group | dates | rows | P1−P0 | P2−P0 | P2−P1 | P0−CH0 | P1−CH0 | ll P1 vs P0 | ll P2 vs P0 |
|---|---|---|---|---|---|---|---|---|---|
| apr15_jun30 | 77 | 17090 | -0.26 | +1.17 | +1.43 | +0.26 | +0.00 | +1.7 | +2.3 |
| jul01_end | 86 | 20035 | -0.12 | +1.16 | +1.28 | +4.65 | +4.53 | +1.6 | +3.2 |
| 2026-04 | 16 | 3414 | +1.88 | +3.12 | +1.25 | +1.25 | +3.13 | -1.1 | -1.8 |
| 2026-05 | 31 | 7001 | -3.55 | -1.29 | +2.26 | +0.65 | -2.90 | -0.1 | +1.9 |
| 2026-06 | 30 | 6675 | +2.00 | +2.67 | +0.67 | -0.67 | +1.33 | +5.0 | +4.8 |
| 2026-07 | 28 | 6471 | -1.79 | -0.36 | +1.43 | +5.71 | +3.93 | +0.8 | +1.8 |
| 2026-08 | 31 | 7250 | -0.32 | +2.26 | +2.58 | +3.55 | +3.23 | +2.9 | +3.9 |
| 2026-09 | 27 | 6314 | +1.85 | +1.48 | -0.37 | +4.81 | +6.67 | +0.9 | +3.9 |
| sp_bf_lt100 | 160 | 8506 | +0.19 | +0.56 | +0.38 | +0.19 | +0.37 | +2.4 | +3.9 |
| sp_bf_100_299 | 160 | 13722 | -1.00 | -0.63 | +0.37 | +1.69 | +0.69 | -1.1 | +0.2 |
| sp_bf_300_499 | 113 | 9463 | +0.44 | +1.68 | +1.24 | +1.24 | +1.68 | +3.6 | +3.5 |
| sp_bf_ge500 | 67 | 5434 | -0.90 | +0.75 | +1.64 | +2.84 | +1.94 | +4.0 | +6.4 |

## Probability quality (all player-games)

| model | logloss | brier | mean pred |
|---|---|---|---|
| CH0 | 0.66486 | 0.23603 | 0.6174 |
| P0 | 0.66496 | 0.23609 | 0.6183 |
| P1 | 0.66479 | 0.23602 | 0.6185 |
| P2 | 0.66468 | 0.23596 | 0.6168 |

- Correlations: {'P1~P0': 0.954, 'P1~CH0': 0.772, 'P0~CH0': 0.793, 'P2~P0': 0.968, 'P2~P1': 0.987, 'P2~CH0': 0.77}; corr(Lp, Lpx) starter = 0.748
- Calibration P0 deciles (pred, act): [(0.5831, 0.516), (0.596, 0.5568), (0.6039, 0.5739), (0.6106, 0.6024), (0.6168, 0.5979), (0.6224, 0.6315), (0.6277, 0.635), (0.6332, 0.658), (0.6394, 0.6584), (0.6501, 0.6833)]
- Calibration P1 deciles (pred, act): [(0.5818, 0.5109), (0.5955, 0.5539), (0.6037, 0.5766), (0.6106, 0.6026), (0.6169, 0.6133), (0.6226, 0.6212), (0.6281, 0.639), (0.6337, 0.651), (0.6403, 0.6589), (0.6518, 0.6857)]
- Calibration P2 deciles (pred, act): [(0.5799, 0.5109), (0.5936, 0.5593), (0.6019, 0.5739), (0.6088, 0.5999), (0.6151, 0.6049), (0.6208, 0.6304), (0.6262, 0.6325), (0.632, 0.658), (0.6387, 0.6517), (0.6507, 0.6916)]

## Market subset (FanDuel Over 0.5 Hits, last pregame snapshot; one-sided de-vig: APPROXIMATE)

- Source: data/props @ origin/main a8e472272c3cb57884c8a6ccb7cb41beaabb9b26; identity: {'method': 'event_id -> game_pk via statsapi schedule (teams + start time); name within game', 'props_rows': 227536, 'events': 709, 'events_matched': 703, 'events_ambiguous': 1, 'events_unmatched': 5, 'player_prices_in_matched_events': 14331, 'player_unmatched_in_eligible_population': 2287, 'player_ambiguous_name_in_game': 0, 'matched_player_games': 12044, 'doubleheader_player_games_matched': 228, 'doubleheader_exclusions': 0}
- Rows 12044 over 53 dates; logloss {'mkt_fair': 0.6595, 'CH0': 0.66433, 'P0': 0.66471, 'P1': 0.66453, 'P2': 0.66431}
- topK3: selected 159 each; rates {'mkt_fair': 0.805, 'CH0': 0.6226, 'P0': 0.7484, 'P1': 0.761, 'P2': 0.7862}; P1_minus_P0 +1.26 [-5.66, +8.81]; P1_minus_mkt_fair -4.40 [-12.58, +3.77]; P0_minus_mkt_fair -5.66 [-15.09, +4.40]; P2_minus_P0 +3.77 [-2.52, +10.06]; P2_minus_mkt_fair -1.89 [-9.43, +5.66]
- topK5: selected 265 each; rates {'mkt_fair': 0.7358, 'CH0': 0.6528, 'P0': 0.7434, 'P1': 0.7472, 'P2': 0.7585}; P1_minus_P0 +0.38 [-3.77, +4.91]; P1_minus_mkt_fair +1.13 [-3.77, +6.04]; P0_minus_mkt_fair +0.75 [-5.67, +7.17]; P2_minus_P0 +1.51 [-2.26, +5.66]; P2_minus_mkt_fair +2.26 [-3.40, +7.92]

## Artifacts

- {'predictions_sha256': '3328267f08086cc59213240eaa4fc6edef9f956a9c501da2d58ac35183f5066d'}

No production, selector, pick, ledger or V3 change. Development evidence only; confirmation is prospective.

Alligator.

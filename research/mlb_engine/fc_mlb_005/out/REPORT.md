# FC-MLB-005 — team conversion residual isolation: NO_GAIN (K=10, D1 vs D0)

**DEVELOPMENT_EVIDENCE: 2026 outcomes previously visible AND hypothesis generated after inspecting 2026 development evidence (FC-MLB-004 P2-P1). Hypothesis-generating only; not confirmatory.**

- Criteria: engineering/ops/TASKS/FC-MLB-005.md @ claude/full-count-ops-state f487b2e69d (sha256 7ef7d98b...)
- Terminology: Dteam = TEAM CONVERSION RESIDUAL (not established as defense)
- Fits: FC-MLB-002 `6c0ecde23577` (D0 = CH1a), FC-MLB-004 `fbe9362fe2ac` (k_d source), FC-MLB-005 `2206fa1cd3784d3d6e781dafa71538e3208bbd431230da3afb56c32db026337b`; k_d = 1500
- Fidelity gate (D0, CH0 vs FC-MLB-004 P0/CH0 @ 46712d8091): {'rows': 37125, 'max_abs_diff_CH0': 5.551115123125783e-17, 'max_abs_diff_D0_vs_P0': 0.0}
- Population: {'player_games': 37125, 'dates': 163, 'first': '2026-04-15', 'last': '2026-09-27', 'h1_base_rate': 0.6113131313131314, 'side_excluded': 0, 'switch_player_games': 4035}

## Frozen 2025 fit

- D0: intercept=0.1799, Lb=0.5572, Lp=0.5661, same_hand=-0.0191
- D1: intercept=0.1751, Lb=0.5589, Lp=0.5633, same_hand=-0.0186, Dteam=0.3219
- D0 refit check: max |Δ| vs frozen CH1a = 0.0 (tol 0.0001)
- 2025 split holdout logloss/PA (fit Apr15–Jul15, score Jul16–end): D0_refit 0.526880; D1 0.526868

## Primary — realized Hits 1+ at equal volume (top-min(K, eligible) per date; paired 95% date-block CI, pp)

| K | selected each | dates short | CH0 | D0 | D1 | D1−D0 | D0−CH0 | D1−CH0 |
|---|---|---|---|---|---|---|---|---|
| 5 | 815 | 0 | 0.6675 | 0.7141 | 0.7117 | -0.25 [-1.84, +1.35] | +4.66 [+0.61, +8.47] | +4.42 [+0.73, +8.10] |
| 10 | 1630 | 0 | 0.6779 | 0.7037 | 0.7037 | +0.00 [-1.17, +1.23] | +2.58 [+0.12, +5.09] | +2.58 [-0.12, +5.15] |
| 20 | 3258 | 1 | 0.6681 | 0.6883 | 0.6877 | -0.06 [-0.74, +0.61] | +2.02 [+0.46, +3.59] | +1.96 [+0.31, +3.62] |

- K5/K20 under the same rule (descriptive): {'K5': 'NO_GAIN', 'K20': 'NO_GAIN'}
- Half gap (Jul–Sep minus Apr–Jun gain vs CH0, pp): {'D0_minus_CH0_jul_minus_apr': 4.3914225309574135, 'D1_minus_CH0_jul_minus_apr': 3.4068257324071283}

## Stability (K=10, pp; logloss gain ×1e4 per player-game)

| group | dates | rows | D1−D0 | D0−CH0 | D1−CH0 | ll D1 vs D0 |
|---|---|---|---|---|---|---|
| apr15_jun30 | 77 | 17090 | +0.52 | +0.26 | +0.78 | +0.2 |
| jul01_end | 86 | 20035 | -0.47 | +4.65 | +4.19 | +0.8 |
| 2026-04 | 16 | 3414 | +0.00 | +1.25 | +1.25 | -0.6 |
| 2026-05 | 31 | 7001 | -0.32 | +0.65 | +0.32 | +0.9 |
| 2026-06 | 30 | 6675 | +1.67 | -0.67 | +1.00 | -0.2 |
| 2026-07 | 28 | 6471 | -1.79 | +5.71 | +3.93 | +0.4 |
| 2026-08 | 31 | 7250 | +1.29 | +3.55 | +4.84 | +0.4 |
| 2026-09 | 27 | 6314 | -1.11 | +4.81 | +3.70 | +1.6 |
| sp_bf_lt100 | 160 | 8506 | +0.00 | +0.19 | +0.19 | +0.8 |
| sp_bf_100_299 | 160 | 13722 | +0.31 | +1.69 | +2.00 | +0.5 |
| sp_bf_300_499 | 113 | 9463 | +0.53 | +1.24 | +1.77 | -0.2 |
| sp_bf_ge500 | 67 | 5434 | -0.30 | +2.84 | +2.54 | +1.3 |
| batter_away_team_fielding_at_home | 163 | 18543 | -0.18 | +1.35 | +1.17 | +0.1 |
| batter_home | 163 | 18582 | +0.25 | +1.66 | +1.90 | +0.9 |

## Probability quality

| model | logloss | brier | mean pred |
|---|---|---|---|
| CH0 | 0.66486 | 0.23603 | 0.6174 |
| D0 | 0.66496 | 0.23609 | 0.6183 |
| D1 | 0.66491 | 0.23607 | 0.6174 |

- corr(D0, D1) = 0.9959; mean top-10 overlap per date = 0.891
- Calibration D0 deciles (pred, act): [(0.5831, 0.516), (0.596, 0.5568), (0.6039, 0.5739), (0.6106, 0.6024), (0.6168, 0.5979), (0.6224, 0.6315), (0.6277, 0.635), (0.6332, 0.658), (0.6394, 0.6584), (0.6501, 0.6833)]
- Calibration D1 deciles (pred, act): [(0.5816, 0.5241), (0.5948, 0.5496), (0.6028, 0.5696), (0.6096, 0.6034), (0.6157, 0.6022), (0.6214, 0.6298), (0.6268, 0.6401), (0.6324, 0.6453), (0.6389, 0.6641), (0.6501, 0.6849)]

## Mechanism diagnostics (descriptive only)

- **persistence**: {"2025_first_half_vs_second_half": {"r": 0.3889957140083527, "n": 30}, "2025_odd_vs_even_dates_split_half": {"r": 0.3517605998058287, "n": 30}, "2025_full_vs_2026_full": {"r": 0.5272445796943778, "n": 30}, "team_season_raw_residual_sd": {"2025": 0.04248883214333237, "2026": 0.05042609245512368}, "team_season_bip_mean": 4158.316666666667}
- **park_home**: {"team_home_resid_vs_team_away_resid": {"r": 0.23220406357982834, "n": 60}, "team_home_resid_vs_visitors_resid_at_same_park": {"r": 0.3692551535480284, "n": 60}, "team_away_resid_vs_visitors_resid_at_its_park": {"r": -0.17227410841381818, "n": 60}, "note": "team-seasons (2025, 2026). Skill -> home~away; park -> home~visitors-at-park."}
- **predictive_association**: {"2025": {"corr_dteam_vs_rest_of_season_resid": 0.29196693220876035, "corr_unshrunk_past_vs_rest_of_season_resid": 0.28440603536462117, "team_dates": 3976, "rule": "rest-of-season BIP >= 300; from START date"}, "2026": {"corr_dteam_vs_rest_of_season_resid": 0.4248002858291528, "corr_unshrunk_past_vs_rest_of_season_resid": 0.42065535741103577, "team_dates": 3967, "rule": "rest-of-season BIP >= 300; from START date"}}
- **distribution_eval_rows**: {"mean": -0.008106125011567974, "sd": 0.03576608296976648, "quantiles": {"0.01": -0.0854, "0.1": -0.0496, "0.25": -0.0317, "0.5": -0.0097, "0.75": 0.0112, "0.9": 0.0381, "0.99": 0.089}, "by_team_bip_to_date": [{"d_bip": "<=500", "size": 747, "mean": -0.0331, "std": 0.0241}, {"d_bip": "501-1000", "size": 4821, "mean": -0.0235, "std": 0.029}, {"d_bip": "1001-2000", "size": 9769, "mean": -0.0212, "std": 0.031}, {"d_bip": "2001-3000", "size": 10189, "mean": -0.0029, "std": 0.0347}, {"d_bip": ">3000", "size": 11599, "mean": 0.0063, "std": 0.0364}]}
- **team_identity_eta2_eval_rows**: 0.7628539863833573
- **late_season_team_dteam_range**: {"min": -0.07948173368611501, "max": 0.09113924566627232, "min_team": "TB", "max_team": "ATH"}

## Market subset (FanDuel Over 0.5 Hits, last pregame snapshot; one-sided de-vig: APPROXIMATE)

- Source: data/props @ origin/main a8e472272c3cb57884c8a6ccb7cb41beaabb9b26; identity: {'method': 'event_id -> game_pk via statsapi schedule (teams + start time); name within game', 'props_rows': 227536, 'events': 709, 'events_matched': 703, 'events_ambiguous': 1, 'events_unmatched': 5, 'player_prices_in_matched_events': 14331, 'player_unmatched_in_eligible_population': 2287, 'player_ambiguous_name_in_game': 0, 'matched_player_games': 12044, 'doubleheader_player_games_matched': 228, 'doubleheader_exclusions': 0}
- Rows 12044 over 53 dates; logloss {'mkt_fair': 0.6595, 'CH0': 0.66433, 'D0': 0.66471, 'D1': 0.6646}
- topK3: selected 159 each; rates {'mkt_fair': 0.805, 'CH0': 0.6226, 'D0': 0.7484, 'D1': 0.7736}; D1_minus_D0 +2.52 [-1.89, +6.92]; D1_minus_mkt_fair -3.14 [-13.21, +6.29]; D0_minus_mkt_fair -5.66 [-15.09, +4.40]
- topK5: selected 265 each; rates {'mkt_fair': 0.7358, 'CH0': 0.6528, 'D0': 0.7434, 'D1': 0.7358}; D1_minus_D0 -0.75 [-4.15, +2.26]; D1_minus_mkt_fair +0.00 [-7.17, +6.79]; D0_minus_mkt_fair +0.75 [-5.67, +7.17]

## Artifacts

- {'predictions_sha256': '378e9be3680321fdb1ee3fa929ea83260a8558536d41822f751a75e165fcc6e9'}

No production, selector, pick, ledger or V3 change. Development evidence only.

Alligator.

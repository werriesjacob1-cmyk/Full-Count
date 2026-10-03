# FC-MLB-006 — PA event world model: NO_GAIN (K=10, E1 vs P0)

**DEVELOPMENT_EVIDENCE: 2026 outcomes previously studied (FC-MLB-002/004/005); not confirmatory.**

- Criteria: engineering/ops/TASKS/FC-MLB-006.md @ claude/full-count-ops-state 22a0bc1a53 (sha256 4a63ef82...)
- Frozen fit sha256 `89874997bef3abd60e4159f3e512a13178a348982b08de6ded88f77359607622`; baseline fit `6c0ecde23577659c1e72e18448415208eb4f5d7d2748f24b9631cd01ded7d6aa`
- Fidelity gate (P0, CH0 vs FC-MLB-004 @ 46712d8091): {'rows': 37125, 'max_abs_diff_CH0': 5.551115123125783e-17, 'max_abs_diff_D0_vs_P0': 0.0}
- Population: {'player_games': 37125, 'dates': 163, 'first': '2026-04-15', 'last': '2026-09-27', 'h1_base_rate': 0.6113131313131314, 'side_excluded': 0, 'switch_player_games': 4035}

## Frozen 2025 fit

- Categories ['1B', '2B', '3B', 'HR', 'BB', 'HBP', 'K', 'OUT']; n PA 156735; convergence {'success': True, 'message': 'CONVERGENCE: RELATIVE REDUCTION OF F <= FACTR*EPSMCH', 'nit': 275, 'nll': 1.4670017406487124, 'max_abs_grad': 4.805200501738938e-08}
- Method-of-moments prior strengths (2025): {"batter": {"1B": 278.0947833045928, "2B": 5000.0, "3B": 378.074771972157, "HR": 198.384915828184, "BB": 114.69077660462447, "HBP": 238.34285409213015, "K": 52.34661448873013, "OUT": 79.5992059849564}, "batter_n_players": 348, "pitcher": {"1B": 445.9567695641649, "2B": 2240.374296422301, "3B": 5000.0, "HR": 673.0762105765087, "BB": 251.71958695661178, "HBP": 355.1707209669295, "K": 75.2100483380709, "OUT": 190.20506203593857}, "pitcher_n_players": 353}

| category | alpha | beta (batter) | gamma (pitcher) | delta (same hand) |
|---|---|---|---|---|
| 1B | -1.183 | 0.423 | 0.516 | 0.024 |
| 2B | -2.356 | 0.739 | 0.179 | -0.075 |
| 3B | -4.829 | 0.931 | 1.410 | -0.232 |
| HR | -2.671 | 1.086 | 0.733 | -0.151 |
| BB | -1.655 | 1.012 | 1.255 | -0.119 |
| HBP | -3.859 | 0.903 | 1.109 | 0.213 |
| K | -0.745 | 1.058 | 1.048 | 0.022 |

- 2025 split check (descriptive): {"multiclass_logloss_E1": 1.4698572483134702, "multiclass_logloss_league_std": 1.4915576961656662, "hit_logloss_E1": 0.5264551975282821, "hit_logloss_P0_refit_first_half": 0.5268796683544187, "hit_logloss_league_std": 0.5275168713176401, "n_score": 71776}

## Primary — realized Hits 1+ at equal volume (top-min(K, eligible) per date; paired 95% date-block CI, pp)

| K | selected each | dates | short | CH0 | P0 | E1 | E1−P0 | rule | P0−CH0 | E1−CH0 | overlap | added hit | removed hit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 815 | 163 | 0  | 0.6675 | 0.7141 | 0.6834 | -3.07 [-6.26, -0.12] | WORSE | +4.66 [+0.61, +8.47] | +1.60 [-2.45, +5.52] | 0.525 | 0.6589 (n=387) | 0.7235 (n=387) |
| 10 | 1630 | 163 | 0  | 0.6779 | 0.7037 | 0.7018 | -0.18 [-2.27, +1.96] | NO_GAIN | +2.58 [+0.12, +5.09] | +2.39 [+0.12, +4.72] | 0.594 | 0.6899 (n=661) | 0.6944 (n=661) |
| 20 | 3258 | 163 | 1 {'2026-07-16': 2} | 0.6681 | 0.6883 | 0.6874 | -0.09 [-1.26, +1.13] | NO_GAIN | +2.02 [+0.46, +3.59] | +1.93 [+0.43, +3.56] | 0.647 | 0.6823 (n=1149) | 0.6849 (n=1149) |

Concentration of selections:
- K5: {"E1": {"distinct_batters": 80, "distinct_games": 672, "max_selections_one_batter": 116, "top10_batters_share": 0.6245398773006134}, "P0": {"distinct_batters": 124, "distinct_games": 646, "max_selections_one_batter": 75, "top10_batters_share": 0.43558282208588955}}
- K10: {"E1": {"distinct_batters": 133, "distinct_games": 1092, "max_selections_one_batter": 129, "top10_batters_share": 0.4558282208588957}, "P0": {"distinct_batters": 185, "distinct_games": 1087, "max_selections_one_batter": 98, "top10_batters_share": 0.3343558282208589}}
- K20: {"E1": {"distinct_batters": 205, "distinct_games": 1638, "max_selections_one_batter": 132, "top10_batters_share": 0.29895641497851444}, "P0": {"distinct_batters": 254, "distinct_games": 1670, "max_selections_one_batter": 111, "top10_batters_share": 0.2470841006752609}}

## Stability (K=10, pp; logloss gain ×1e4 per player-game)

| group | dates | rows | E1−P0 | P0−CH0 | E1−CH0 | ll E1 vs P0 |
|---|---|---|---|---|---|---|
| apr15_jun30 | 77 | 17090 | +1.17 | +0.26 | +1.43 | +6.2 |
| jul01_end | 86 | 20035 | -1.40 | +4.65 | +3.26 | +4.5 |
| 2026-04 | 16 | 3414 | +6.25 | +1.25 | +7.50 | +14.5 |
| 2026-05 | 31 | 7001 | -2.26 | +0.65 | -1.61 | -0.4 |
| 2026-06 | 30 | 6675 | +2.00 | -0.67 | +1.33 | +8.9 |
| 2026-07 | 28 | 6471 | -1.79 | +5.71 | +3.93 | +3.2 |
| 2026-08 | 31 | 7250 | -0.32 | +3.55 | +3.23 | +6.6 |
| 2026-09 | 27 | 6314 | -2.22 | +4.81 | +2.59 | +3.5 |
| sp_bf_lt100 | 160 | 8506 | +1.13 | +0.19 | +1.31 | +3.5 |
| sp_bf_100_299 | 160 | 13722 | -1.62 | +1.69 | +0.06 | +4.2 |
| sp_bf_300_499 | 113 | 9463 | +0.00 | +1.24 | +1.24 | +7.2 |
| sp_bf_ge500 | 67 | 5434 | +1.94 | +2.84 | +4.78 | +7.5 |
| batter_away | 163 | 18543 | -1.04 | +1.35 | +0.31 | +4.2 |
| batter_home | 163 | 18582 | +0.80 | +1.66 | +2.45 | +6.3 |

## World-model diagnostics

- Invariants: {"max_abs_sum_minus_1": 4.440892098500626e-16, "any_nonfinite": false, "min_q": {"1B": 0.0745317452436794, "2B": 0.02786016374200307, "3B": 0.0009054309822789553, "HR": 0.005193786995287191, "BB": 0.019756068683432673, "HBP": 0.0014122721772745598, "K": 0.032323199097173835, "OUT": 0.22941116289437827}, "max_q": {"1B": 0.2170625924072696, "2B": 0.053152096301822334, "3B": 0.021184170028095, "HR": 0.08476078125264652, "BB": 0.2488765303801692, "HBP": 0.06743253543016627, "K": 0.5277706754587934, "OUT": 0.6485115201550833}, "h_E1_range": [0.16134911775758282, 0.2808823449860859], "E1_raw_range": [0.4806376026639909, 0.7689959431695574]}
- 2026 PA level (actual pitcher): {"n_pa": 156522, "multiclass_logloss": {"E1": 1.4718443327502637, "E1_nopitcher_diag": 1.4771272402093811, "league_season_to_date": 1.4925181691284457}, "hit_logloss": {"E1": 0.5238827879671782, "P0_pa_model": 0.5242382020265259, "E1_nopitcher_diag": 0.5245184201040831, "league_season_to_date": 0.5249792338978131}, "k_logloss": {"E1": 0.5141846311206466, "league_season_to_date": 0.5256926490628112}, "per_category_mean_pred_vs_actual": {"1B": [0.14376981859852286, 0.14266365111613702], "2B": [0.042699981394759126, 0.0409079873755766], "3B": [0.0034750453939284497, 0.003763049283806749], "HR": [0.031195712157111075, 0.031107448154253075], "BB": [0.08416528272532436, 0.08803874215765196], "HBP": [0.010563026388741762, 0.011602202885217414], "K": [0.21988410859347626, 0.2190171349714417], "OUT": [0.464247024748136, 0.4628997840559155]}}
- PA-level calibration deciles (pred, act): {"hit": [{"pred": 0.192, "act": 0.1836}, {"pred": 0.2042, "act": 0.1999}, {"pred": 0.2102, "act": 0.2064}, {"pred": 0.215, "act": 0.2043}, {"pred": 0.2192, "act": 0.2182}, {"pred": 0.2232, "act": 0.2222}, {"pred": 0.2274, "act": 0.2309}, {"pred": 0.232, "act": 0.2337}, {"pred": 0.238, "act": 0.2353}, {"pred": 0.2501, "act": 0.2499}], "K": [{"pred": 0.12, "act": 0.1133}, {"pred": 0.1564, "act": 0.1548}, {"pred": 0.1768, "act": 0.1768}, {"pred": 0.1937, "act": 0.1959}, {"pred": 0.2091, "act": 0.2064}, {"pred": 0.2245, "act": 0.225}, {"pred": 0.2408, "act": 0.2402}, {"pred": 0.2599, "act": 0.2623}, {"pred": 0.2843, "act": 0.2855}, {"pred": 0.3334, "act": 0.3299}]}
- Opponent information: {"var_hit_logit_starter": 0.009532075087700728, "share_var_from_pitcher_channel": 0.39021362009435334, "share_var_from_batter_channel": 0.6298280558791501, "corr_E1_vs_E1_nopitcher_diag": 0.9452591581521519, "K10_rate_E1_nopitcher_diag": 0.6865030674846625, "note": "diagnostic only; not a candidate model"}
- Rank correlation: {"spearman_E1_P0": 0.8993202539793935, "spearman_E1_CH0": 0.773338484168613, "spearman_P0_CH0": 0.8068748463127999}

| player-game model | logloss | brier | mean pred |
|---|---|---|---|
| CH0 | 0.66486 | 0.23603 | 0.6174 |
| P0 | 0.66496 | 0.23609 | 0.6183 |
| P0_raw | 0.66393 | 0.23561 | 0.6311 |
| E1 | 0.66443 | 0.23585 | 0.6208 |
| E1_raw | 0.66359 | 0.23543 | 0.6359 |

Actual Hits 1+ rate 0.6113.
- Calibration CH0 (pred, act): [(0.5331, 0.5263), (0.5767, 0.5714), (0.5939, 0.5804), (0.6065, 0.6048), (0.6169, 0.6127), (0.6266, 0.625), (0.6361, 0.6207), (0.6465, 0.6582), (0.6579, 0.6482), (0.6796, 0.6655)]
- Calibration P0 (pred, act): [(0.5831, 0.516), (0.596, 0.5568), (0.6039, 0.5739), (0.6106, 0.6024), (0.6168, 0.5979), (0.6224, 0.6315), (0.6277, 0.635), (0.6332, 0.658), (0.6394, 0.6584), (0.6501, 0.6833)]
- Calibration P0_raw (pred, act): [(0.5611, 0.5133), (0.5865, 0.5603), (0.6022, 0.5726), (0.6158, 0.597), (0.6281, 0.5984), (0.6394, 0.6336), (0.6499, 0.6382), (0.6607, 0.6593), (0.6729, 0.6506), (0.6939, 0.6897)]
- Calibration E1 (pred, act): [(0.582, 0.5053), (0.5961, 0.5501), (0.6047, 0.5799), (0.6121, 0.6024), (0.6189, 0.5979), (0.6253, 0.6242), (0.6312, 0.6476), (0.6372, 0.6488), (0.6439, 0.6711), (0.656, 0.686)]
- Calibration E1_raw (pred, act): [(0.5584, 0.5031), (0.5867, 0.5477), (0.604, 0.5785), (0.6187, 0.6067), (0.6322, 0.5998), (0.645, 0.6207), (0.6569, 0.6544), (0.6687, 0.6415), (0.6822, 0.6721), (0.7062, 0.6887)]

## Market subset (secondary; one-sided de-vig APPROXIMATE)

- Source: data/props @ pinned main a8e472272c3cb57884c8a6ccb7cb41beaabb9b26; identity: {'method': 'event_id -> game_pk via statsapi schedule (teams + start time); name within game', 'props_rows': 227536, 'events': 709, 'events_matched': 703, 'events_ambiguous': 1, 'events_unmatched': 5, 'player_prices_in_matched_events': 14331, 'player_unmatched_in_eligible_population': 2287, 'player_ambiguous_name_in_game': 0, 'matched_player_games': 12044, 'doubleheader_player_games_matched': 228, 'doubleheader_exclusions': 0}
- Rows 12044 over 53 dates; logloss {"mkt_fair": 0.6595, "CH0": 0.66433, "P0": 0.66471, "E1": 0.66427}
- topK3: selected 159 each; rates {"mkt_fair": 0.805, "CH0": 0.6226, "P0": 0.7484, "E1": 0.6792}; E1_minus_P0 -6.92 [-13.84, -0.63]; E1_minus_mkt_fair -12.58 [-22.64, -1.89]; P0_minus_mkt_fair -5.66 [-15.09, +4.40]
- topK5: selected 265 each; rates {"mkt_fair": 0.7358, "CH0": 0.6528, "P0": 0.7434, "E1": 0.6755}; E1_minus_P0 -6.79 [-12.08, -1.88]; E1_minus_mkt_fair -6.04 [-12.83, +0.38]; P0_minus_mkt_fair +0.75 [-5.67, +7.17]

## Artifacts

- {'predictions_sha256': 'ff4faf4eee28ca867e921ca8e3cdf7eb4120528c0b436e0ad8793b77c75e73e0'}

No production, selector, pick, ledger, site, V3 or NFL change. Development evidence only.

Alligator.

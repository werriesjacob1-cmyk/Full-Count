# FC-MLB-002 — Hits 1+ batter×pitcher challenger: INCONCLUSIVE (K=10, CH1 vs CH0)

**DEVELOPMENT_EVIDENCE (2026 outcomes previously visible to the project; not confirmatory)**

- Criteria: engineering/ops/TASKS/FC-MLB-002.md @ claude/full-count-ops-state cb1a338b28 (sha256 5779cfe9...)
- Champion code: main `8b689852342bd49014b86ad637e64002ad6f47b4` (production functions imported unchanged)
- Frozen 2025 fit sha256 `6c0ecde23577659c1e72e18448415208eb4f5d7d2748f24b9631cd01ded7d6aa`; shrinkage k = {'kb': 200, 'kp': 150, 'kf': 100, 'ku': 400}
- Population: {'batter_games': 37125, 'dates': 163, 'first': '2026-04-15', 'last': '2026-09-27', 'h1_base_rate': 0.6113131313131314}

## Frozen 2025 coefficients (PA-level logistic; fitted on 2025 only)

| model | coefficients | 2025 Jul16–Sep holdout logloss/PA (fit Apr15–Jul15) |
|---|---|---|
| CH0b | intercept=-0.486, Lb=0.613 | 0.52723 |
| CH1a | intercept=0.180, Lb=0.557, Lp=0.566, same_hand=-0.019 | 0.52688 |
| CH1 | intercept=0.047, Lb=0.499, Lp=0.520, same_hand=-0.019, M=0.183 | 0.52688 |

## Repair (Codex findings 1-4)

- {'settlement_identity': '(game_pk, batter); hits/TB summed over every PA of the player-game', 'audited_rule_fragments': {'rows_by_stand': 41356, 'player_games_split': 2278, 'split_with_conflicting_h1': 1158}, 'matchup_side': "statsapi batSide; switch hitter bats opposite the starter's hand; bullpen side mixed by league RHP share of relief PAs as of D-1; undeterminable side excluded", 'side_excluded_rows': 0, 'switch_hitter_player_games': 4035, 'rows_before_min_pa': 39078, 'raw_provenance': {'raw_files_verified': 385, 'manifest_sha256': '3fb1d0170a3f465ec30625b3deb08e17bae3f4858a468b0cae9f97d608b1d16a', 'pa_parquet_sha256': 'cb06b8f38c9dd8275194ebe60dc4c24e263b1fa366edd03f42c4b9abf03387a4', 'pitches_parquet_sha256': 'a87b1c7e560639aa692bfad87ae8a875147db53c3c612e484928fe26de639c76', 'pitches': 1429320, 'pa': 366775}}

## Primary — realized Hits 1+ rate at equal volume (top-min(K, eligible) per date, paired by date; 95% date-block CI)

| K | selected rows each | dates short | CH0 | CH0b (Δ, CI) | CH1a (Δ, CI) | CH1 (Δ, CI) | CH1−CH1a (CI) |
|---|---|---|---|---|---|---|---|
| 5 | 815 | 0  | 0.6675 | 0.6871 (+1.96, [-1.10, +5.03]) | 0.7141 (+4.66, [+0.61, +8.47]) | 0.7104 (+4.29, [+0.37, +8.10]) | -0.37 [-1.60, +0.86] |
| 10 | 1630 | 0  | 0.6779 | 0.6865 (+0.86, [-0.86, +2.64]) | 0.7037 (+2.58, [+0.12, +5.09]) | 0.7006 (+2.27, [-0.12, +4.66]) | -0.31 [-1.10, +0.55] |
| 20 | 3258 | 1 {'2026-07-16': 2} | 0.6681 | 0.6742 (+0.61, [-0.74, +1.87]) | 0.6883 (+2.02, [+0.46, +3.59]) | 0.6899 (+2.18, [+0.61, +3.74]) | +0.15 [-0.25, +0.58] |

Stability (K=10, pp vs CH0): apr15_jun30: CH1 -0.26 / CH1a +0.26 (77 dates); jul01_end: CH1 +4.53 / CH1a +4.65 (86 dates); 2026-04: CH1 +0.00 / CH1a +1.25 (16 dates); 2026-05: CH1 +0.32 / CH1a +0.65 (31 dates); 2026-06: CH1 -1.00 / CH1a -0.67 (30 dates); 2026-07: CH1 +4.64 / CH1a +5.71 (28 dates); 2026-08: CH1 +4.84 / CH1a +3.55 (31 dates); 2026-09: CH1 +4.07 / CH1a +4.81 (27 dates)

## Secondary diagnostics

| model | logloss | brier | mean pred |
|---|---|---|---|
| CH0 | 0.66486 | 0.23603 | 0.6174 |
| CH0b | 0.66528 | 0.23625 | 0.6200 |
| CH1a | 0.66496 | 0.23609 | 0.6183 |
| CH1 | 0.66497 | 0.23610 | 0.6186 |

| diagnostic family | CH0 logloss (mean pred) | CH1 logloss (mean pred) | actual |
|---|---|---|---|
| h2 | 0.52562 (0.2239) | 0.52026 (0.2191) | 0.2193 |
| tb2 | 0.65350 (0.3650) | 0.64800 (0.3626) | 0.3559 |

- Correlation CH0 vs CH1: 0.811; mean top-10 overlap per date: 0.456
- Subgroup logloss gain CH1 vs CH0 (per batter-game): {'LHB_side': {'n': 18332, 'll_gain_CH1_vs_CH0': 0.0003391024788885755}, 'RHB_side': {'n': 18793, 'll_gain_CH1_vs_CH0': -0.0005523269585767654}, 'switch': {'n': 4035, 'll_gain_CH1_vs_CH0': -0.0015878482721516374}, 'same_hand_sp': {'n': 14078, 'll_gain_CH1_vs_CH0': 0.00048399303176360764}, 'opp_hand_sp': {'n': 23047, 'll_gain_CH1_vs_CH0': -0.0004762922632322031}}
- Calibration CH0 deciles (pred, act): [(0.5331, 0.5263), (0.5767, 0.5714), (0.5939, 0.5804), (0.6065, 0.6048), (0.6169, 0.6127), (0.6266, 0.625), (0.6361, 0.6207), (0.6465, 0.6582), (0.6579, 0.6482), (0.6796, 0.6655)]
- Calibration CH1 deciles (pred, act): [(0.5832, 0.516), (0.5963, 0.5579), (0.6042, 0.5718), (0.611, 0.6043), (0.6172, 0.5944), (0.6229, 0.6342), (0.6282, 0.6409), (0.6336, 0.6528), (0.6397, 0.6552), (0.6499, 0.6857)]

## Market-relative (FanDuel Over 0.5 Hits, last pregame snapshot, devigged with production assumed hold)

- Source: data/props @ origin/main f43d904a564a9091fad19dfb48f4a805ace147e2; one-sided FanDuel price, production assumed hold (pp.devig): APPROXIMATE
- Identity: {'method': 'event_id -> game_pk via statsapi schedule (teams + start time); name within game', 'props_rows': 227536, 'events': 709, 'events_matched': 703, 'events_ambiguous': 1, 'events_unmatched': 5, 'player_prices_in_matched_events': 14331, 'player_unmatched_in_eligible_population': 2287, 'player_ambiguous_name_in_game': 0, 'matched_player_games': 12044, 'doubleheader_player_games_matched': 228, 'doubleheader_exclusions': 0}
- Matched player-games: 12044 over 53 dates; logloss {'mkt_fair': 0.659501882949481, 'CH0': 0.664333872673056, 'CH1a': 0.6647109691575739, 'CH1': 0.6647387169899793}; mean {'mkt_fair': 0.6015386018606657, 'CH0': 0.6193565215453717, 'CH1a': 0.6204066155168734, 'CH1': 0.6205152063012966, 'actual': 0.6105114579873796}
- topK3: selected 159 each; CH0 0.6226, CH1a 0.7484, CH1 0.7484, market 0.8050; CH1a_minus_CH0 +12.58 [+3.14, +22.01]; CH1_minus_CH0 +12.58 [+3.77, +20.77]; CH1a_minus_mkt_fair -5.66 [-15.09, +4.40]; CH1_minus_mkt_fair -5.66 [-15.72, +3.77]
- topK5: selected 265 each; CH0 0.6528, CH1a 0.7434, CH1 0.7434, market 0.7358; CH1a_minus_CH0 +9.06 [+2.64, +15.47]; CH1_minus_CH0 +9.06 [+2.26, +15.85]; CH1a_minus_mkt_fair +0.75 [-5.67, +7.17]; CH1_minus_mkt_fair +0.75 [-6.05, +7.17]
- disagreement_coef_CH0: {'coef': 0.17549439894516508, 'market_coef': 1.3115395138048678, 'note': 'confounded by de-vig compression; descriptive only'}
- disagreement_coef_CH1: {'coef': 0.7879253322120869, 'market_coef': 1.783963580580141, 'note': 'confounded by de-vig compression; descriptive only'}
- CH1_disagreement_quintiles: [{'dis': -0.0391, 'mkt': 0.6738, 'ch1': 0.6348, 'act': 0.6936, 'n': 2409}, {'dis': -0.0056, 'mkt': 0.6335, 'ch1': 0.6279, 'act': 0.6488, 'n': 2409}, {'dis': 0.0178, 'mkt': 0.6022, 'ch1': 0.6199, 'act': 0.6022, 'n': 2408}, {'dis': 0.0413, 'mkt': 0.5732, 'ch1': 0.6144, 'act': 0.5766, 'n': 2409}, {'dis': 0.0805, 'mkt': 0.525, 'ch1': 0.6055, 'act': 0.5313, 'n': 2409}]

## Artifacts

- {'predictions_sha256': '50c1d98b137e95799d0170af9c392026715cd5567a12b8f92dcc21ef8a18521c', 'statcast_manifest_sha256': '3fb1d0170a3f465ec30625b3deb08e17bae3f4858a468b0cae9f97d608b1d16a'}

No production, selector, pick, ledger or V3 change. Development evidence only; confirmation is prospective.

Alligator.

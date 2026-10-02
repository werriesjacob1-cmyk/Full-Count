# FC-MLB-002 — Hits 1+ batter×pitcher challenger: INCONCLUSIVE (K=10, CH1 vs CH0)

**DEVELOPMENT_EVIDENCE (2026 outcomes previously visible to the project; not confirmatory)**

- Criteria: engineering/ops/TASKS/FC-MLB-002.md @ claude/full-count-ops-state cb1a338b28 (sha256 5779cfe9...)
- Champion code: main `8b689852342bd49014b86ad637e64002ad6f47b4` (production functions imported unchanged)
- Frozen 2025 fit sha256 `6c0ecde23577659c1e72e18448415208eb4f5d7d2748f24b9631cd01ded7d6aa`; shrinkage k = {'kb': 200, 'kp': 150, 'kf': 100, 'ku': 400}
- Population: {'batter_games': 39298, 'dates': 163, 'first': '2026-04-15', 'last': '2026-09-27', 'h1_base_rate': 0.584660796987124}

## Frozen 2025 coefficients (PA-level logistic; fitted on 2025 only)

| model | coefficients | 2025 Jul16–Sep holdout logloss/PA (fit Apr15–Jul15) |
|---|---|---|
| CH0b | intercept=-0.486, Lb=0.613 | 0.52723 |
| CH1a | intercept=0.180, Lb=0.557, Lp=0.566, same_hand=-0.019 | 0.52688 |
| CH1 | intercept=0.047, Lb=0.499, Lp=0.520, same_hand=-0.019, M=0.183 | 0.52688 |

## Primary — realized Hits 1+ rate at equal volume (top-K per date, paired by date)

| K | CH0 (production) | CH0b batter-only fitted | CH1a +pitcher | CH1 +interaction | CH1−CH0 pp [95% CI] |
|---|---|---|---|---|---|
| 5 | 0.6393 | 0.6663 (+2.70) | 0.7031 (+6.38) | 0.6945 | +5.52 [+1.47, +9.45] |
| 10 | 0.6577 | 0.6724 (+1.47) | 0.6859 (+2.82) | 0.6810 | +2.33 [-0.19, +4.91] |
| 20 | 0.6451 | 0.6528 (+0.77) | 0.6678 (+2.27) | 0.6647 | +1.96 [+0.37, +3.56] |

Stability (K=10 CH1−CH0 pp by half): {'apr15_jun30': -0.38961038961038863, 'jul01_end': 4.767441860465117}

## Secondary diagnostics

| model | logloss | brier | mean pred |
|---|---|---|---|
| CH0 | 0.67578 | 0.24138 | 0.6039 |
| CH0b | 0.67661 | 0.24181 | 0.6067 |
| CH1a | 0.67628 | 0.24165 | 0.6050 |
| CH1 | 0.67629 | 0.24165 | 0.6053 |

| diagnostic family | CH0 logloss (mean pred) | CH1 logloss (mean pred) | actual |
|---|---|---|---|
| h2 | 0.50396 (0.2234) | 0.49879 (0.2188) | 0.2023 |
| tb2 | 0.64132 (0.3641) | 0.63613 (0.3620) | 0.3357 |

- Correlation CH0 vs CH1: 0.813; mean top-10 overlap per date: 0.441
- Subgroup logloss gain CH1 vs CH0 (per batter-game): {'LHB': {'n': 19280, 'll_gain_CH1_vs_CH0': 9.163697435443652e-05}, 'RHB': {'n': 20018, 'll_gain_CH1_vs_CH0': -0.0010890552691582567}, 'same_hand_sp': {'n': 14129, 'll_gain_CH1_vs_CH0': 0.0005111498194990682}, 'opp_hand_sp': {'n': 25169, 'll_gain_CH1_vs_CH0': -0.00108291880138911}}
- Calibration CH0 deciles (pred, act): [(0.5189, 0.4885), (0.5628, 0.5455), (0.5804, 0.5567), (0.5931, 0.5811), (0.6036, 0.5858), (0.6133, 0.5969), (0.6228, 0.5943), (0.6332, 0.6334), (0.6446, 0.622), (0.6664, 0.6422)]
- Calibration CH1 deciles (pred, act): [(0.5697, 0.484), (0.5828, 0.528), (0.5908, 0.555), (0.5977, 0.5793), (0.6039, 0.5702), (0.6096, 0.6041), (0.6149, 0.6164), (0.6203, 0.6186), (0.6264, 0.6295), (0.6366, 0.6616)]

## Market-relative (FanDuel Over 0.5 Hits, last pregame snapshot, devigged with production assumed hold)

- Source: data/props @ origin/main f43d904a564a9091fad19dfb48f4a805ace147e2; matched batter-games: 9554 over 53 dates
- logloss: {'mkt_fair': 0.6578612397287001, 'CH0': 0.6630868537354951, 'CH1': 0.6638709538032539}
- mean: {'mkt_fair': 0.6004126924421865, 'CH0': 0.6060602350006554, 'CH1': 0.6069917136081938, 'actual': 0.6109482939083106}
- disagreement_coef_CH0: {'coef': 0.2662780302357619, 'ci95': [-0.041209918681368925, 0.5828213696192042], 'market_coef': 1.476956798215235}
- disagreement_coef_CH1: {'coef': 0.9689073910625098, 'ci95': [0.30032223349224463, 1.6610245647394284], 'market_coef': 2.0310864147916163}
- topK3: {'CH0': 0.6477987421383646, 'CH1': 0.7358490566037734, 'market': 0.7547169811320755, 'CH1_minus_CH0_pp': 8.805031446540882, 'CH1_minus_market_pp': -1.886792452830189}
- topK5: {'CH0': 0.683018867924528, 'CH1': 0.7245283018867924, 'market': 0.720754716981132, 'CH1_minus_CH0_pp': 4.150943396226416, 'CH1_minus_market_pp': 0.3773584905660381}
- CH1−market disagreement quintiles: [{'dis': -0.052, 'mkt': 0.6737, 'ch1': 0.6217, 'act': 0.7017, 'n': 1911}, {'dis': -0.018, 'mkt': 0.6324, 'ch1': 0.6145, 'act': 0.6457, 'n': 1911}, {'dis': 0.0056, 'mkt': 0.6004, 'ch1': 0.606, 'act': 0.6131, 'n': 1910}, {'dis': 0.0289, 'mkt': 0.572, 'ch1': 0.6008, 'act': 0.5714, 'n': 1911}, {'dis': 0.0683, 'mkt': 0.5237, 'ch1': 0.592, 'act': 0.5228, 'n': 1911}]

## Artifacts

- {'predictions_sha256': '5d7958cca108e900eede429c97a4c5d684f046d19dee791c2d0dbec197f1d89c', 'statcast_manifest_sha256': '3fb1d0170a3f465ec30625b3deb08e17bae3f4858a468b0cae9f97d608b1d16a'}

No production, selector, pick, ledger or V3 change. Development evidence only; confirmation is prospective.

Alligator.

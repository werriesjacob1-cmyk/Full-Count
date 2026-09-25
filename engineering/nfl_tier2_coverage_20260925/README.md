# NFL Tier 2 Mission 1: F11 receiver vs coverage, F12 defensive coverage tendencies

Research only. Nothing here changes authoritative B0, a workflow, a public pick or any Codex PR. The work builds on the Tier 1 contract and harness (`claude/nfl-tier1-foundation-20260924`).

## Verdict
**F11 and F12 are REJECTED for receptions and receiving yards under this formulation.**
- The pre-declared DEV fit (target seasons 2019–2022) gave the receiver-specific man/zone split zero weight (α = 0) in both markets. F12 alone got a small weight (α = 0.3).
- Every holdout and 2026 interval spans 0, including the pre-declared full-strength (α = 1) sensitivity.
- The largest effect anywhere is about 0.0005 receptions or 0.005 receiving yards per player-game.
- **Why the effect is so small:** coverage-driven matchup ratios have a standard deviation of only 0.02–0.03. Once B0 and the scale control are in place, man/zone tendencies move a receiver's expected output very little.

This is a real negative finding, kept as such. It is **not** evidence about route-level or individual-matchup effects, which the source cannot observe (see "Unsupported").

## Source and provenance (measured before modelling)
- **Coverage labels:** nflverse `pbp_participation_{2016..2025}.csv`. Hashes are in `/tmp/claude-0/nfl_tier1_shared/participation/MANIFEST.sha256` and reproduced below.
  - Labels (`defense_man_zone_type`, `defense_coverage_type`) exist **only for 2018–2025**, on 38–50% of plays.
  - Among joined dropbacks, over 99% carry a man/zone label, and about 96% have a targeted receiver. That means labels are not limited to targeted plays.
  - 2018–2022 is NGS-sourced; 2023–2025 is FTN-sourced. FTN's taxonomy differs: it adds COMBO, COVER_9 and BLOWN.
  - League man share drifts sharply: 29–37% in the NGS era; 41% (2023), 48% (2024) and 31% (2025) in the FTN era. This looks like charting drift, so every ratio compares against a mix drawn from the *same* prior seasons.
- **`route`** is recorded for the targeted receiver only (target-conditioned) and is not used.
- **Denominator:** on-field participation (`offense_players`). Opportunity is "targets per on-field coverage-labelled dropback", **not per route run**.
- **No in-season participation exists** (2023+ is released after the postseason). Features for season S use only completed seasons S−1 (weight 1.0) and S−2 (weight 0.5). Historical evaluation therefore sees exactly what a live 2026 prediction sees: 2024–2025.
- **Play-by-play:** nflfastR `play_by_play_{season}.csv.gz` from the shared Tier 1 pin. Dropbacks exclude sacks, spikes and 2-point tries.
- **Head coach:** from nflverse `games.csv` (`home_coach`/`away_coach`). A regime change means the coach differs between the last REG game of S−1 and the first game of S.
- **DC identity is UNKNOWN.** The repo's regime registry (#181) holds HC intervals only; DC and defensive-playcaller entries read `NO_INTERVALS_INGESTED`.
- **Licensing:** nflverse data under its published licence. FTN charting reaches this pipeline only through nflverse participation and is used for research, not redistributed beyond this repo.

## Method (pre-declared in `evaluate_coverage.py`, frozen in `coverage_params.json` at `f67d959ff0` before any holdout scoring)
- **F11**, per receiver and per man/zone bin:
  - target rate per on-field dropback, shrunk toward the position rate (K = 150 dropbacks);
  - catch rate and yards per target, conditional on being targeted and labelled as such, shrunk with K = 30 targets.
  - Abstains below 100 weighted on-field labelled dropbacks.
- **F12**, per defense: shrunk man share (K = 200). A verified HC change, or an unknown regime, halves the prior-season weight. Abstains below 200 labelled dropbacks. Coverage families are descriptive only.
- **Consumer:** `k·B0 · clip(ratio, 0.75, 1.333)^α`, where k is the harness's DEV-fitted scale control. The ratio compares the expected per-dropback quantity under the opponent's man share with the share the receiver actually faced. It comes in three modes:
  - **COMBINED:** receiver's own rates, opponent's man share versus his faced share;
  - **F11_ONLY:** receiver's own rates, league man share versus his faced share;
  - **F12_ONLY:** position-average rates, opponent's man share versus the league share.
- **Fallback:** a row missing either side falls back to exactly k·B0, and the reason is recorded.
- **α fit:** grid 0–2, on DEV target seasons 2019–2022 only. `--stage final` refits and aborts on any drift.

## Results (`coverage_report.json`)
- **HOLDOUT_2023_2025** was previously inspected by earlier NFL experiments, so it is exploratory.
- **FRESH_2026** covers weeks 1–2, with features from 2024–2025 only.

**receptions** (k = 0.87; Δ = challenger − scale control, mean per player-game, game-clustered 95% CI)

| Mode | α (DEV) | HOLDOUT 2023–25 n / activation / Δ [CI] | FRESH 2026 n / Δ [CI] |
|---|---|---|---|
| COMBINED | 0.0 | 12094 / 0.00 / +0.0000 [+0.0000, +0.0000] | 436 / +0.0000 [+0.0000, +0.0000] |
| COMBINED_ALPHA1_SENSITIVITY | 1.0 (sensitivity) | 12094 / 0.77 / -0.0004 [-0.0014, +0.0006] | 436 / -0.0001 [-0.0048, +0.0050] |
| F11_ONLY | 0.0 | 12094 / 0.00 / +0.0000 [+0.0000, +0.0000] | 436 / +0.0000 [+0.0000, +0.0000] |
| F11_ONLY_ALPHA1_SENSITIVITY | 1.0 (sensitivity) | 12094 / 0.77 / +0.0002 [-0.0005, +0.0009] | 436 / +0.0025 [-0.0011, +0.0061] |
| F12_ONLY | 0.3 | 12094 / 0.99 / -0.0002 [-0.0004, +0.0000] | 436 / -0.0004 [-0.0013, +0.0005] |
| F12_ONLY_ALPHA1_SENSITIVITY | 1.0 (sensitivity) | 12094 / 0.99 / -0.0005 [-0.0012, +0.0002] | 436 / -0.0011 [-0.0041, +0.0019] |

**receiving_yards** (k = 0.78; Δ = challenger − scale control, mean per player-game, game-clustered 95% CI)

| Mode | α (DEV) | HOLDOUT 2023–25 n / activation / Δ [CI] | FRESH 2026 n / Δ [CI] |
|---|---|---|---|
| COMBINED | 0.0 | 12094 / 0.00 / +0.0000 [+0.0000, +0.0000] | 436 / +0.0000 [+0.0000, +0.0000] |
| COMBINED_ALPHA1_SENSITIVITY | 1.0 (sensitivity) | 12094 / 0.77 / +0.0045 [-0.0040, +0.0137] | 436 / -0.0012 [-0.0431, +0.0440] |
| F11_ONLY | 0.0 | 12094 / 0.00 / +0.0000 [+0.0000, +0.0000] | 436 / +0.0000 [+0.0000, +0.0000] |
| F11_ONLY_ALPHA1_SENSITIVITY | 1.0 (sensitivity) | 12094 / 0.77 / +0.0007 [-0.0051, +0.0063] | 436 / -0.0174 [-0.0542, +0.0129] |
| F12_ONLY | 0.3 | 12094 / 0.99 / -0.0001 [-0.0015, +0.0012] | 436 / +0.0004 [-0.0055, +0.0063] |
| F12_ONLY_ALPHA1_SENSITIVITY | 1.0 (sensitivity) | 12094 / 0.99 / +0.0003 [-0.0044, +0.0048] | 436 / +0.0013 [-0.0182, +0.0210] |

**COMBINED fallback reasons** (receptions; receiving yards identical):

| Reason | Rows |
|---|---|
| OK | 21,423 |
| `NO_FEATURES_PRE_2019` | 10,321 |
| `INSUFFICIENT_RECEIVER_COVERAGE_SAMPLE` | 6,493 |
| `UNSUPPORTED_POSITION_OR_NO_PRIOR_SEASON` | 408 |

**Descriptive holdout splits** (`coverage_report.json`) are computed at the α = 1 sensitivity, because the frozen COMBINED α is 0 and frozen splits would be identically 0. They cover opponent man-share tercile, receiver sample tercile, season and position.
- All are within ±0.004 receptions and ±0.02 receiving yards.
- Signs are inconsistent.
- No subgroup shows a coverage effect.

**Real 2026 examples** (`fresh_2026_examples`, largest COMBINED ratios): these show how the matchup would move a prediction at α = 1. At the frozen α = 0 the challenger equals the scale control.
- `2026_02_LV_LAC`, RB `00-0040666` vs LV:
  - man target rate 0.109 vs zone 0.222, faced man share 0.46;
  - LV man share 0.30 (HC changed);
  - ratio 1.12, B0 3.6, actual 2.
- `2026_01_DEN_KC`, RB `00-0038134` vs DEN: man share 0.48, ratio 0.91, B0 3.0, actual 3.

## Unsupported claims (not made)
- **Individual WR vs CB matchups.** The source has no coverage assignments.
- **Routes run, separation, and target rate per route.** Not observed.
- **Slot vs outside alignment.** `offense_positions` gives roster position, not alignment.
- **Coordinator tendencies across teams, and the current DC's identity.** BLOCKED: no DC intervals in the registry. A licensed source would be needed.
- **Coverage by down and distance, and personnel-dependent coverage.** Available in the data but not modelled. The man/zone null makes finer splits unlikely to help receptions or yards, and they would multiply thin cells.
- **2026 in-season coverage behaviour.** Unavailable until after the 2026 postseason.

## Remaining requirements before any prospective use
None are recommended. F11 and F12 are rejected for these markets.

A future attempt would need:
- a route-level denominator (tracking or route charting);
- a licensed DC-interval source;
- a pre-registered hypothesis on a market where coverage plausibly matters more, such as aDOT or air yards, or explosive-play thresholds for alternate receiving-yard lines.

## Reproduce
```
PYTHONPATH=. python3 engineering/nfl_tier2_coverage_20260925/evaluate_coverage.py --stage final
PYTHONPATH=. python3 -m unittest nfl.tests.test_tier2_coverage
```
Deterministic. The `final` stage refuses to run if the DEV refit differs from the committed parameters.

## Participation SHA-256
```
f47ae39cbe44ca88aa69d5a8a4c134f9edb1f63e0c60c277cf98f9240ec37116  pbp_participation_2016.csv
227e0e00dbb1507bae4cc167ce31be9b4c46d8d50c09c74c4e9d1a2b0e3885b5  pbp_participation_2017.csv
b577ac402622dbecda8589bbc0b6ef1e08c1fece9916b6105ce4b29a675be905  pbp_participation_2018.csv
1a6091df8e4fdb6f937c5ca5be555e4d5b65ea363a69c3d1a3b7dd9fdb0fbf0b  pbp_participation_2019.csv
7e3e47021025067d2229716867523f1a42e995dff608bfafd55ed9ee385433fc  pbp_participation_2020.csv
b58e79cd91ea078396c91f9c06452da0b9d650fa42203ee580e1df06e306e785  pbp_participation_2021.csv
37eae0a8c388d0974c6df9a1f9a1d01f09f63a21440d3b03d7f208c1438c51e4  pbp_participation_2022.csv
ad01aeb4045ee19a4f086ff38b52b14c8f427d3401e529c3078a4545921650a9  pbp_participation_2023.csv
b1f436a98b2a7759eb4ed1181e072a35c2666f9aeb356a49c943d28d6be6b0b9  pbp_participation_2024.csv
59069adfee7b0f464befba8a5e8be331e523633cc6a7ab403d37bcbcdfbe66ac  pbp_participation_2025.csv
```

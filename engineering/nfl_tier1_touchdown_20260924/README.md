# NFL Tier 1, Workstream D: F4_RED_ZONE and touchdown_opportunity_challenger

Research only. No B0, pick, selector, workflow or pricing file was changed.
This is **not** a validated anytime-TD pricing system.

- Producer: `nfl/research/tier1/touchdown_features.py`. Its module docstring
  lists every exclusion rule.
- Consumer: `nfl/research/tier1/touchdown_consumer.py`. The DEV-fitted
  `PARAMS` are constants in the file.
- Runner: `nfl/research/tier1/touchdown_evaluate.py`, with stages `fit`,
  `full` and `status`.
- Tests: `nfl/tests/test_tier1_touchdown.py` (14 tests).
- Evidence:
  - `touchdown_report.json` (full run, live rows, status record);
  - `dev_fit_stage.json` and `dev_fit_grids.jsonl` (DEV-only fit, committed
    before the holdout run).

## Commit order (pre-declaration)

1. `47d35ab540`: the features, the consumer with the DEV-fitted `PARAMS`, and
   the DEV-only fit evidence. No holdout or fresh rows had been scored when
   this was committed.
2. The next commit adds the single `--stage full` run. `--stage full` refits on
   DEV and refuses to run if the result differs from the committed `PARAMS`.
   It reproduced them.

## Outcome definition

The outcome is the harness `anytime_td` market: the player scores at least one
**rushing or receiving** TD.

- Passing TDs are out of scope.
- Return, defensive and fumble-recovery TDs are not counted.
- Two-point conversions are not TDs.

## Factor status

Metric: log loss (primary) and Brier (secondary). Δ = challenger − control,
with 95% CIs clustered by game. A negative Δ means the challenger is better.
The primary variant is `rz_blend`, with red-zone weight w = 0.20 and scale
c = 0.98.

| Partition | Matched n | Activation | Log loss (challenger / B0 / scale ctrl k=0.71) | Δ vs B0 | Δ vs scale control | Brier (challenger / B0 / scale ctrl) |
|---|---|---|---|---|---|---|
| DEV_2016_2022 (in-sample) | 32,751 | 0.988 | 0.5039 / 1.1466 / 1.0978 | −0.643 [−0.670, −0.613] | −0.594 [−0.622, −0.563] | 0.1608 / 0.1861 / 0.1755 |
| HOLDOUT_2023_2025 (exploratory) | 14,829 | 1.000 | 0.4805 / 1.1104 / 1.0640 | −0.630 [−0.675, −0.583] | −0.584 [−0.627, −0.539] | 0.1547 / 0.1770 / 0.1674 |
| FRESH_2026 (weeks 1–2) | 544 | 1.000 | 0.4788 / 0.9917 / 0.9283 | −0.513 [−0.692, −0.342] | −0.450 [−0.634, −0.281] | 0.1535 / 0.1744 / 0.1642 |

**Beating B0 or the scale control is NOT evidence for F4.**

- For `anytime_td`, the harness B0 is the rolling mean of the last five 0/1
  outcomes. It is exactly 0 on 36% of rows: 11,840 of 32,751 in DEV and 5,757
  of 14,829 in the holdout.
- Those rows still score at 14.6% in DEV, 13.4% in the holdout and 11.4% in
  FRESH.
- The log loss clips at 1e-6, so any model with nonzero probabilities wins by
  about 0.6.
- The volume-only model below has **no red-zone information** and beats the
  scale control by the same amount.

The factor-specific test compares against the `volume_only` control. That
control uses the same machinery (L8 shrunk targets and carries per game ×
positional all-field TD rate, Poisson link, DEV-fitted c), without the
red-zone split.

| Variant vs `volume_only` | DEV Δ log loss | HOLDOUT Δ log loss | FRESH Δ log loss (n=576) |
|---|---|---|---|
| **rz_blend (PRIMARY)** | −0.00039 [−0.00066, −0.00011] | **−0.00170 [−0.00212, −0.00131]** | −0.00211 [−0.00472, +0.00040] |
| rz_share_x_team (first declared form) | +0.00468 [+0.00332, +0.00606] | −0.00124 [−0.00338, +0.00065] | −0.00411 [−0.01703, +0.00784] |
| rz_direct | +0.00469 [+0.00336, +0.00599] | −0.00155 [−0.00358, +0.00035] | −0.00492 [−0.01590, +0.00511] |
| rz_zone_split (m_frac=160) | +0.00242 [+0.00044, +0.00465] | −0.00204 [−0.00267, −0.00141] | −0.00391 [−0.00747, −0.00045] |
| rz_team_conv (team TD/trip) | +0.00511 [+0.00357, +0.00652] | −0.00108 [−0.00327, +0.00103] | −0.00273 [−0.01749, +0.01116] |

In Brier on the holdout, the primary scores 0.15338 and volume-only scores
0.15398.

| Factor | Milestone | Consumer | Notes |
|---|---|---|---|
| F4_RED_ZONE | **VALIDATED**: historically supported, not prospectively validated | touchdown_opportunity_challenger (`rz_blend`) | See the note below the table. |

The primary beats the scale control, with the holdout CI entirely below 0, and
is not worse on FRESH. It also clears the stricter factor-specific test: it
beats `volume_only` on the holdout (CI below 0), and FRESH has a negative point
estimate with a CI that crosses 0.

Limits on this result:

- The gain is small, about 0.35% of log loss.
- The holdout was previously inspected by other NFL experiments.
- The primary form was chosen after DEV development (see below).
- The coordinator may prefer to record BUILT.

Sub-component findings:

- **Team conversion efficiency (TD per red-zone trip):** REJECTED as a
  multiplier. `rz_team_conv` is worse than `rz_share_x_team` in every
  partition.
- **Closing-line team context:** `CLOSING_LINE_PROXY_RETROSPECTIVE` ablation
  only, with γ = 1.05 fitted on DEV. Holdout log loss is 0.4767 against 0.4805
  for the primary. It uses closing lines, so it is not usable pregame.

## Negative results and DEV development history (preserved)

1. The first declared form, `rz_share_x_team` (player share of team bucket
   plays × team bucket plays/game × positional per-bucket conversion), **lost
   to volume_only on DEV**: log loss 0.5090 vs 0.5043.
2. A DEV probe found the red-zone tilt points the right way:
   - Top-decile tilt: actual 0.216 vs volume-only 0.188.
   - Bottom-decile tilt: actual 0.168 vs volume-only 0.186.
   - The pure red-zone form over-reacts, at 0.279 / 0.109.
3. The player-specific zone split (`rz_zone_split`) preferred ever-larger
   shrinkage on DEV. Its m_frac grid was monotone up to the edge value of 160.
   That variant nevertheless beat volume-only on the holdout and on FRESH. The
   DEV and holdout directions disagree, and this is recorded here, not
   resolved.
4. The primary `rz_blend` is a geometric pool:
   `lambda = c · lambda_vol^(1−w) · lambda_rz^w`. w was fitted on DEV (grid
   0–1): w = 0 gives DEV log loss 0.49238 and w = 0.2 gives 0.49199.
5. Calibration by decile is **under-dispersed**. On the holdout, decile 2
   predicts 0.125 against an actual 0.083, and decile 10 predicts 0.394
   against 0.449. This was not tuned on the holdout.
6. The 2+ TD research distribution (Poisson) is coherent: 0 violations of
   P(2+) ≤ P(1+). It under-predicts:
   - HOLDOUT: mean P(2+) 0.0293 vs actual 0.0334.
   - FRESH: 0.0303 vs 0.0521 (n=576).

   Label: `RESEARCH_DISTRIBUTION_NOT_VALIDATED_2PLUS_PRICING`.

## FanDuel settlement vs the research outcome

Source: the FanDuel House Rules filed in Massachusetts (2023-08-24 filing),
SHA-256 `2a8cb81b…fa845`. The current fanduel.com pages returned 403 to this
session, so the 2026 wording is unverified. The repo also has
`nfl/normalize/player_prop_markets.py`, which maps `ANY_TIME_TOUCHDOWN_SCORER`
as threshold 1 (it was read, not edited), and
`nfl/docs/PLAYER_PROP_SETTLEMENT_SPEC.md`.

The quoted rules:

- "Only when a player does not play a snap in that game are the selections
  voided."
- The winner is "the player who possesses the ball in the endzone".
- Overtime counts.

How that differs from the research outcome:

- **Void.** FanDuel voids only when a player plays zero snaps. The research
  population is harness role rows, meaning at least one carry or target, which
  is known only after the game. A player who plays but gets no touch is a
  FanDuel LOSS but is absent here. Research probabilities are therefore
  conditional on a touch and run high against FanDuel's settled population.
  Paired comparisons are unaffected because every model is scored on the same
  rows.
- **Scope.** FanDuel, and the repo grader, count return, defensive and
  fumble-recovery TDs. Rows whose only TD was of that kind are 0.15% of DEV
  (54 of 35,507), 0.16% of the holdout (26 of 15,823) and 0 in FRESH. They are
  outcome 0 here.
- **Passing TDs** are not a win for the QB in either definition.
- **Two-point conversions** are not TDs in either definition.

## Live path: 2026 week 3, ATL@GB (`2026_03_ATL_GB`, kickoff 2026-09-25T00:15:00Z)

The live section of `touchdown_report.json` has 23 feature rows and
probabilities: 12 ATL players and 11 GB players. A player is included when his
latest 2026 appearance was with ATL or GB.

- **`information_cutoff` = `2026-09-24T14:13:56Z`.** This is the later of two
  nflverse release-asset HTTP `Last-Modified` times, used as the **release
  time**:
  - `play_by_play_2026.csv.gz`: 14:12:31Z.
  - `stats_player_week_2026.csv`: 14:13:56Z.
- Both files were independently re-downloaded, at 21:02:35Z and 21:05:38Z, and
  their SHA-256 matched the shared files.
- Every live row passes `contract.validate_feature_row(row,
  prediction_cutoff=kickoff)`.
- No injury or inactive source is read, because that is F8 (Workstream B). A
  player ruled out still has a row.
- Example rows:
  - Bijan Robinson: P = 0.466.
  - Drake London: 0.287.
  - Christian Watson: 0.272.
  - Kyle Pitts: 0.270.
  - MarShawn Lloyd: 0.258 (only 3 prior appearances).

To rebuild from the repo root, run `python3 -m
nfl.research.tier1.touchdown_evaluate --stage full` and then `--stage status`.
The full stage rescores every partition deterministically. For live rows only,
call `touchdown_evaluate.live_rows(load(args))`.

## Sources (SHA-256)

- `play_by_play_2016..2026.csv.gz`: the values in `MANIFEST.sha256`, recorded
  in the report's `sources.pbp_sha256`. For 2026:
  `6643f82a…ece8`.
- `stats_player_week_2026.csv`: `736bdddef4779023f7eb1831a1f2c8627181aee60cc280d5f0464cf5f41a8e67`.
- Weekly stats 2015–2025: verified against
  `engineering/evidence/nflverse_weekly_stats_full_audit_2026-09-14.json`.
- `games.csv`: `7fdc123e11cf224b97d120980a4cd18171147f6db6d4c3b193f27406b4856c4e`.
  It is used only for the retrospective closing-line ablation.

## Data notes

- The weekly-stats `team` column reports `LV` for 2016–2019 Oakland games,
  while `game_id` keeps `OAK`. Joining on period-accurate codes lost about 800
  appearances' team context, so both sides are now canonicalised.
- `role_intelligence_data_prep.build_pbp_opportunity_rows`, a Codex/earlier
  module that was not edited, counts two-point tries as red-zone and goal-line
  opportunities. It filters on `pass_attempt`/`rush_attempt`, which nflfastR
  sets on 2-pt tries (1,281 tries in 2016–2026). Its `red_zone_*` and
  `goal_line_carries` values are therefore slightly inflated.
- 616 appearances have no play-by-play opportunity. They are almost all QBs
  whose only weekly "carries" were kneels, which rule 5 excludes.

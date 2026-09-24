# Mission 10: unit-consistent pregame target-share stage

Research only. B0, live selectors, and public picks are unchanged.

## The finding, in one sentence

The NFL receptions opportunity engine was not losing to B0 because target
shares are hard to estimate. It multiplied a share of **team targets** by a
prediction of **team dropbacks** (attempts + sacks), and real teams produce
only ~0.83 targets per dropback. So every projection was inflated ~21%.

## How it was found (exploratory; already-inspected 2024-2025 wk8+, n=5,877)

`exploratory_diagnosis.py` / `exploratory_diagnosis_report.json`:

| Check | Result |
|---|---|
| Six pregame share estimators (season-shrunk, last-4/6/10 ratio-of-sums, same-team only) | share MAE 0.0497-0.0508, bias about 0: all equivalent |
| Real team targets per dropback | mean 0.828 (p10 0.772, p90 0.880) |
| Engine bias vs B0 bias | +0.495 vs +0.023 receptions |
| Same share, volume and catch rate, converted to team targets | MAE 1.2982 vs B0 1.3242 |

PR #191's oracle (substituting the realized target share cut MAE to 1.045)
diagnosed where the error lives but mixed two things: irreducible
game-to-game share noise, which no pregame model can remove, and this
fixable unit bias. This work separates them.

## The challenger (C1), locked before any untouched data was read

`nfl/research/pregame_target_share.py`:

    expected receptions = predicted team dropbacks
                          x team's own targets per dropback (last 8 games, strictly prior)
                          x existing target-share estimate
                          x existing catch-rate estimate

Only the conversion factor is new. With no prior team game it abstains and
never substitutes a constant. See `PREREGISTRATION.md`. The git history
shows its lock commit (`d1666e06d1`) comes before the holdout result
(`addffb20dd`).

## Locked holdout result (2019-2022 weeks 8+, untouched by this workstream)

`holdout_evaluation.py` / `holdout_evaluation_report.json`. n = 10,961
paired player-games, 712 players, all four models on identical rows.

| Model | MAE | Bias | Poisson Brier (1.5-6.5) |
|---|---|---|---|
| B0 (authoritative) | 1.3873 | +0.030 | 0.1239 |
| Existing unadjusted chain | 1.4662 | +0.431 | 0.1277 |
| Existing full engine (snap-informed) | 1.7115 | +0.753 | 0.1506 |
| **C1** | **1.3539** | **-0.017** | **0.1203** |

**Primary (pre-registered, single test):** C1 minus B0 MAE **-0.0334**,
player-clustered 95% CI **[-0.0456, -0.0210]**, verdict
`CONFIRMED_IMPROVEMENT_OVER_B0`.

Descriptively, C1 beat B0 in every season (2019 -0.011, 2020 -0.033, 2021
-0.035, 2022 -0.051), position (WR -0.041, RB -0.034, TE -0.018), and share
tier (<0.10 -0.013, 0.10-0.20 -0.035, >=0.20 -0.113). The largest gain is
on high-share receivers. C1 changed every prediction, by a median factor of
0.842 (p05 0.773, p95 0.904).

### Honest limits

- **Modest size.** 0.033 receptions (about 2.4%). A lower MAE is not the
  same as more winning picks.
- **No real prices.** The repo has no real 2019-2022 sportsbook lines, so
  there is no equal-volume hit rate or return result. None was fabricated.
- **Log score.** B0's Poisson log score (4.19) is inflated by its own
  exact-zero projections (point mass at 0). That is a B0 quirk, not a C1
  win, and is not claimed as one.
- **One disclosed post-run fix.** The first holdout run printed the primary
  verdict, then crashed in a secondary metric: `poisson_pmf` rejected a
  real zero B0 projection. The fix (`f43c07dd43`, lambda=0 is a point mass
  at zero, plus a regression test) touches only that helper. The primary
  rule, models, population and holdout script are unchanged, and the
  rerun's primary result was identical.
- **Population contract.** Candidates are players with a real stats row in
  the target game, the same contract as every prior evaluation. 94 real
  2019 rows abstained for `NO_MATCHUP_ROW`, and all abstentions are
  symmetric across models (paired rows only).
- **Prior use of these seasons elsewhere, disclosed.**
  `role_regime_redistribution.py` trained a different model on 2012-2021
  absence-event rows. B0 research scored 2000-2025, which, if anything,
  favors the champion.

## Frozen forward shadow (2026 week 3, genuinely prospective)

`forward_shadow_freeze.py` froze 293 research-only candidates across all 16
week-3 games at **2026-09-24T01:24:56Z**, about 23 hours before the first
kickoff (2026-09-25 00:15 UTC). The script refuses to run at or after
kickoff.

- Artifact: `forward_shadow_2026_wk3.json`, SHA-256
  `357c27f5e0e86872c8d2be8afaeda178eb8d3e4ab4e39b6ca747caf63197e988`.
- Committed in `9744243f63`. It records `code_commit_at_freeze`
  `fcc51bcdd9`, the commit containing the exact freeze script.

Real changed pregame predictions (2026_03_ATL_GB):

| Player | B0 | Existing unadjusted | C1 |
|---|---|---|---|
| Bijan Robinson (ATL RB) | 5.20 | 6.29 | 5.58 |
| Christian Watson (GB WR) | 4.00 | 5.14 | 4.53 |
| Drake London (ATL WR) | 2.80 | 4.89 | 4.34 |
| Tucker Kraft (GB TE) | 3.80 | 3.95 | 3.49 |

Source note: 2026 has no digest-pinned PBP, so the shadow derives team
offense rows for both 2025 and 2026 from real weekly player stats, which is
one consistent source. On 2025, where both exist, that source runs 2.54
dropbacks per team-game below pinned PBP (`forward_sources.
validate_against_pbp`). B0 ignores it entirely. C1 is nearly invariant to
it, because dropbacks appear in both its volume prediction and its
targets-per-dropback denominator.

Grading happens only once games are FINAL. Players who do not play are
VOID, not misses. Real sportsbook lines are joined only if a real captured
offer exists.

## Reproduce

    python engineering/nfl_pregame_target_share_20260923/data_cache.py
    python engineering/nfl_pregame_target_share_20260923/exploratory_diagnosis.py
    python engineering/nfl_pregame_target_share_20260923/holdout_evaluation.py

Network access to nflverse-data is required. PBP and snap counts are
digest-pinned.

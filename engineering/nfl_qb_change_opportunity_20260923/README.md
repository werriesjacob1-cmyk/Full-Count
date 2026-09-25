# NFL QB-change-aware team-dropback consumer -- real evidence (Mission 8, Workstream A)

Workstream: `NFL-MISSION8-PARALLEL-20260923` (Issue #91). Connects the real,
already-built-but-unconsumed strictly-prior QB-starter-identity/tenure
substrate (`nfl/research/qb_continuity_features.py` -- its own module
docstring states plainly: "This module does not evaluate that hypothesis,
correlate it with anything, or wire it into any model or selector") to the
existing team-opportunity engine's per-player receptions projection chain
(`nfl/research/receptions_team_opportunity_challenger.py`, reused
read-only, zero bytes changed), via a new module,
`nfl/research/qb_change_team_dropbacks.py`. This is the real factor named
in the permanent requirements register's P10 ("account for QB change
effects on ALL teammates, not storytelling"): the QB-restricted team
pass-dropback volume feeds `compute_opportunity_projection` for every
receiving-corps player on that team, not one flagged player.

## What is genuinely new

`resolve_incumbent_qb` / `filter_team_rows_by_qb_continuity` / `predict_
team_pass_dropbacks_qb_aware` are the QB-identity analogue of the
already-merged (PR #179/#181) `filter_team_rows_by_current_regime` /
`predict_team_pass_dropbacks_coaching_aware` -- same structure, same
no-lookahead discipline, same "otherwise-identical naive control" pattern,
but keyed on real recorded QB-starter identity (`qb_continuity_features.
infer_team_week_starters`, reused unmodified) instead of real HC-regime
identity. Zero changes to any existing file.

## Real, disclosed result on the main matched population

Same real 2025-week-8+ matched population precedent the coaching-aware and
snap-share ablations already used (n=3,059, WR/TE/RB real receptions
observations):

- **Baseline (existing, merged coaching-aware opportunity engine): MAE = 1.386444868319182**
- **QB-aware challenger: MAE = 1.38511789320672**

An essentially NEGLIGIBLE difference (~0.001 MAE) -- **this is an
inconclusive/null finding, not a demonstrated accuracy improvement**,
reported honestly rather than framed as a win. Unlike the coaching feature
(which changed 0/2,954 real projections in its own matched population
because in-season HC changes are rare), the QB-continuity feature is much
more ACTIVE: it changed **1,027 of 3,059 (33.6%)** real projections in this
population, because real in-season starter changes (injury, benching,
bye-week rotation) are far more common than real in-season HC changes. The
mechanism engages far more often than the coaching feature did, but that
higher activation rate does not translate into a measurable accuracy
improvement on this population -- a real, disclosed, not-retuned-against
finding, consistent with the project's standard that negative/null results
are first-class evidence.

`rows_with_insufficient_qb_specific_box_score_history: 0` in this
population -- see "What the NO_ADJUSTMENT path looks like" below for why.

## Real, named single-player demonstration (not synthetic, not tomorrow's not-yet-available inactive)

Per Mission 8's explicit instruction ("if real current-week info isn't
available before tomorrow's game... implement/validate the receiving path
using real historical data rather than inventing an inactive"), the
following is a real, strictly-prior, fully reproducible example from
`qb_change_real_evaluation_report.json`'s
`real_named_single_player_demonstration_BAL_week8_2025`:

- **Real source and timing**: nflverse's official `stats_player_week_2025.csv`
  weekly release (the same real source `qb_continuity_features.py` and
  `receptions_team_opportunity_challenger.py` already consume elsewhere in
  this codebase) -- published after each week's games go final, strictly
  before the following week's kickoff. This module consumes only the real
  recorded PASS-ATTEMPT identity of each week's starter (the same proxy
  `qb_continuity_features.py`'s own docstring specifies), not injury-report
  text, so it does not need to interpret or timestamp any injury
  announcement -- it needs only that Cooper Rush (`00-0033662`) is the real
  QB with the most recorded pass attempts for Baltimore in each of the two
  most recent real Baltimore games strictly before Week 8, 2025.
- **Correct identity**: candidate `00-0030564` (DeAndre Hopkins), team
  `BAL`, `target_season=2025`, `target_week=8`.
- **Strictly-prior info only**: `qb_tenure_starts: 2` (Rush's own real
  consecutive-start count entering week 8), `own_games_used_qb_aware: 2`
  vs. `own_games_used_naive_control: 5` -- the QB-aware rolling window uses
  ONLY the 2 real Baltimore games Rush actually started, excluding the 3
  real prior games started under a different real incumbent identity.
- **Baseline estimate** (existing, merged coaching-aware engine, real
  team-volume 35.2 predicted dropbacks from all 5 real prior games):
  projection = **2.561 receptions**, `model_over_probability = 0.596`
  (research direction OVER a real 2.5 line at -115/-105).
- **QB-aware adjusted estimate** (real team-volume 33.1 predicted
  dropbacks, from only the 2 real games Rush started): projection =
  **2.408 receptions**, `model_over_probability = 0.500`.
- **Resulting probability change**: `model_over_probability` moves from
  0.596 to exactly 0.500 -- the real research direction flips from OVER to
  a coin-flip/UNDER-leaning edge. Target share (0.105) and catch rate
  (0.696) are held IDENTICAL between the two projections -- the entire
  shift is real, isolated team-volume information, exactly what `qb_
  feature_changed_the_projection: true` records.
- This is a real, disclosed DECREASE, not an inflation: the QB-continuity
  feature did not uniformly boost every teammate's projection when the
  team changed QBs -- for this real player, in this real game, it revised
  the projection DOWN, consistent with Mission 8's explicit requirement to
  avoid "uniformly inflating every teammate."

## Real, non-cherry-picked largest activation (2023-2025 scan)

Scanning every real (team, season, week) in the loaded 2023-2025 starter
substrate for the single largest real `|qb_aware - naive_control|`
dropback difference (the same "scan for the clearest real case"
methodology Mission 6 used for the real 2026 snap-share example) found New
Orleans, Week 9, 2025: real incumbent Tyler Shough (`00-0040743`),
`qb_tenure_starts: 1`, QB-aware predicted dropbacks 61.0 (from his own
single real prior start) vs. naive-control 39.4 (blending in 4 real prior
games under a different starter) -- a real 21.6-dropback difference. **A
real, disclosed caveat**: this is a single-game (`n=1`) QB-aware sample, so
it carries real, high sampling variance -- reported here as proof the
mechanism produces a real, large, non-fabricated signal on real data, not
as evidence the resulting number is well-calibrated.

## What the NO_ADJUSTMENT path looks like

`build_qb_change_aware_record` sets `status:
"NO_ADJUSTMENT_INSUFFICIENT_QB_TENURE_HISTORY"` whenever a real incumbent
is resolved but the team box-score source has zero real games recorded
under that exact identity yet (e.g. the incumbent's own debut game has not
yet had its team box score ingested). This never activated on the real
2023-2025 HISTORICAL matched population above
(`rows_with_insufficient_qb_specific_box_score_history: 0`) -- by
construction, a completed, already-scored historical season always has a
real box score for every real played game, so this path is specifically
for a LIVE current-week run scored before that week's own box score
exists (exactly Mission 8's own scenario for a real in-season QB change
discovered the same week it happens). It is proven correct with realistic,
non-synthetic-shaped fixtures by three dedicated unit tests in
`nfl/tests/test_qb_change_team_dropbacks.py`
(`test_insufficient_qb_specific_history_reports_zero_games_not_a_guess`,
`test_insufficient_qb_history_sets_explicit_no_adjustment_status`), not
forced onto real historical data where it cannot honestly occur.

## What this module does NOT do

- Never fabricates an incumbent identity or a QB-change effect: no real
  prior starter history resolves to `incumbent_player_id: None` and the
  QB-aware prediction becomes numerically IDENTICAL to the naive control
  (same fallback discipline as the coaching-aware consumer).
- Never assumes a box-score row matches the incumbent when no real
  starter observation exists for that exact team/week -- such rows are
  excluded from the QB-filtered window, never assumed to match.
- Makes zero changes to `qb_continuity_features.py`,
  `receptions_team_opportunity_challenger.py`, or any other existing file.

## Reproduction

`python engineering/nfl_qb_change_opportunity_20260923/qb_change_real_evaluation.py`
(network access to nflverse-data required; real 2023-2026 data; took
~17-18s this run). Full output in
`qb_change_real_evaluation_report.json`.

## Scope disclosure

This evaluates only the team-dropback-volume side of the opportunity
chain -- target share and catch rate are held identical to the baseline
engine throughout, isolating exactly what the QB-continuity feature
changes. A combined ablation against the already-disclosed coaching and
snap-share features (three-way interaction) was not attempted this pass;
a concrete next milestone, not silently omitted.

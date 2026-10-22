# FC-ACC-012 — frozen exploratory H+R+RBI PA diagnostic

Frozen before evaluation in [Issue #91 comment 6345511265](https://github.com/werriesjacob1-cmyk/Full-Count/issues/91#issuecomment-6345511265). This is neither a prospective preregistration nor a model promotion.

## Current source path

`mlb_sources._empirical_batter_one` turns each real game log into `hits + runs + rbi`, then measures the fraction clearing each threshold. `_apply_shrinkage` partially pools each batter's game-level fraction toward the observed slate-scoped league rate. `generate_picks._batter_options` selects `emp_p("hits_runs_rbis_{k}plus")` for thresholds 1–4; this family has no `fn` modelled component. The source fetch needs at least 20 game logs, while production's `MIN_EMPIRICAL_GAMES=25` is the effective scoring gate; with fewer than 25 or unavailable source data, no H+R+RBI probability is produced. It is an empirical **per-game** distribution, not a conditional opportunity model. Hits, runs and RBI are summed in the same game and are never assumed independent.

`generate_picks.project_batter_pa` computes a slot intercept plus `0.1006 × implied team runs`; absent an implied total, it uses 4.245 league runs, and it clamps supplied totals to 2–7.5. These intercepts were measured on 2026 data. The value is stored on a batter candidate and may affect other prop families. It is not used by H+R+RBI's `_batter_options` probability. Batting order already affects the batter context score, so a PA challenger must demonstrate incremental discrimination against the existing probability and a probability-only recalibration control.

## Data and fixed model

Use only `backtest/rows_canonical.jsonl` after independently verifying its source SHA, chronology and row integrity. `backtest/build_canonical_backtest.py` documents its original repair/main splice and code SHA guard. The JSONL is ignored/untracked, is absent from the current checkout, and has no retained Actions artifact under that name. The evaluator rejects absent/mixed replay-code provenance and emits the data SHA-256. Its historical baseline is the code regime recorded in those rows, which may differ from today's production source; any such difference must be disclosed and evaluated before a current-production claim. Do not use published Top Pick outcomes as a substitute.

For each threshold `needs ∈ {1,2,3,4}`, use its raw `predicted_prob` as the baseline. Deduplicate all 2024 player-games and estimate the mean actual PA for each of the nine pregame batting-order slots **only from 2024**. The 2024-fitted mean is a strictly earlier-period estimate for 2025 and 2026. Do not use the present 2026-measured slot constants to build a historical 2024/25 feature. `actual_pa` is a training target, never an evaluation-row input. The pregame slot comes from `signals.lineup_slot`. A missing/invalid slot or probability is counted and excluded from paired comparison, never imputed from postgame order.

Fit two line-specific ridge logistic regressions on 2024 only:

* Control: `logit(P(Y=1)) = a + b × logit(p_baseline)`.
* Single challenger: the control plus `c × (expected_PA_2024_by_slot − 4)`.

`Y` is the **composite** threshold event `(H+R+RBI >= needs)`. The fit makes no independence assumption among components. Both fits use penalty 10 around `(a,b,c)=(0,1,0)`, and baseline probabilities are capped to `[0.01,0.99]` inside `logit` only. There is no threshold search or later-period retuning. The control identifies generic global recalibration; the added PA coefficient and paired evaluation identify residual opportunity information. This is a conditional event model, not a new distribution for independent hits/runs/RBI components.

Report 2025 and 2026 separately, with 2024 clearly in-sample. Evaluate paired Brier/log loss, AUC, predicted versus actual rates, pregame expected-PA and order bands, and date-clustered paired bootstrap intervals. Also report changes at a fixed 0.60 diagnostic threshold and same-N swaps per date and line, with added/removed wins. These are **not** historical sportsbook selections: canonical rows lack authenticated offered prices, full public-policy eligibility, and assured publication-time lineup timestamps.

The prior backtest engine uses cutoff-filtered season/game-log statistics and a guarded Statcast store, but it replays later scoring code, has known missing production features, uses 2026-measured constants elsewhere, and lacks historical sportsbook offers. Its `actual_pa` is derived at grading. Therefore even a positive result is exploratory. A valid 2027 test would freeze the challenger and champion decisions from the same authenticated pregame opportunity manifest, match pick counts, and grade actual offers and outcomes forward.

## Execution gate

`python backtest/hrrbi_pa_diagnostic.py backtest/rows_canonical.jsonl`

If the historical JSONL is missing, the script fails. A future operator must supply and hash the authentic canonical data, audit its row/code provenance, then run this frozen code unchanged. The source report must include the input SHA-256 emitted by the script. Until then, no numerical accuracy or selection-lift conclusion is supportable.


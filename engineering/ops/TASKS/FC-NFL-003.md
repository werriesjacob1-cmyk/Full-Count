# FC-NFL-003 — NFL market-prior residual research

## ACCEPTANCE_CRITERIA

- **Target:** NFL regular-season full-game spread and total, games with both final scores and a matching archived market line in the pinned games source. Player-prop implications are architectural only in this first experiment.
- **Baseline:** existing FULL COUNT `GAME_MARKET_B0_PRIOR_SCORING_BLEND` point predictions and its implied spread/total sides, evaluated on the exact same games and thresholds as the challenger.
- **Challenger:** a predeclared shrinkage blend of an independent B0 football projection with a sportsbook market prior. Investigate whether an out-of-sample football residual adds value over the market alone; do not make the market a retrospective production input.
- **Same opportunity:** compare market-only, B0, and blend on identical game/market/side rows; equal number of selections, explicit pushes, no selective abstention. Historical closing lines are a development proxy, never proof of a pregame executable offer.
- **Primary metric:** realized spread cover / total hit rate at equal usable volume when prospective, authentic pregame offers exist. For this bounded historical study, report the matched-game cover/hit proxy and label it non-actionable; no official pick or profitability claim.
- **Secondary:** margin/total MAE, Brier/log loss where probabilities are supported, interval uncertainty, season/subgroup stability, and residual correlation.
- **Point in time:** B0 features must use only prior completed games. Market line is an archived closing benchmark in this study and cannot be called point-in-time available at an earlier selection time. No future injuries, news, outcomes, or postgame revisions in predictors.
- **Data identity:** pin source repository commit, file hash, season/game keys, code revision, sample counts and excluded rows; reject duplicate or malformed games.
- **Uncertainty:** predeclare fit/validation/held-out seasons; bootstrap by game week or season, with held-out results shown separately. Treat small deltas as inconclusive.
- **Stop:** stop this cycle after one bounded comparison and artifact, or when legitimate source/identity checks fail. Do not tune on held-out results, deploy, publish picks, promote a model, or change Week 4 seals.
- **Evidence:** commit a reproducible research script, its focused tests, machine-readable report and interpretation on `codex/fc-nfl-003-20261002`. Expose exact SHA and source limitations for Claude challenge.

## BUILDER_NOTES

Research files are isolated under `nfl/research/fc_nfl_003/` and `engineering/nfl_fc_nfl_003/`. Current main base is `24e3b96ad031789178f4a9562329f9e515e0baf1`. No shared production module or sealed prospective artifact is in scope.

## LOG

- 2026-10-02: Codex claimed Lane B and preregistered this bounded first experiment before implementation.

- 2026-10-02: Pinned 6,906-game residual study committed as draft PR #222 @ 17dab718f0bb33bbdae02ddde76c9f15c214f6b5. No held-out gain; challenge requested. Local legacy temp-directory tests blocked by Windows sandbox permission.


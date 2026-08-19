# Backtest data contract

Every component below reads or writes **backtest rows**. One row = one prop
the model would have recommended on one historical date, plus what actually
happened. This is the single interface between the three pieces.

```json
{
  "date": "2026-06-14",
  "game_pk": 812345,
  "player_id": 670541,
  "player_name": "Yordan Alvarez",
  "prop_type": "hits",
  "line": 0.5,
  "needs": 1,
  "signals": {"platoon": 80.0, "l7_form": 62.1, "park_hr_index": 55.0},
  "score": 74.2,
  "cat_matchup": 71.0,
  "cat_recent_form": 68.5,
  "cat_environment": 55.0,
  "cat_baseline_skill": 62.0,
  "cat_context": 80.0,
  "sb_cat_skill": null,
  "sb_cat_matchup": null,
  "sb_cat_context": null,
  "predicted_prob": 0.71,
  "outcome": 1,
  "actual": 2,
  "fair_test": true,
  "actual_pa": 4,
  "code_git_sha": "6d01e83",
  "backtest_generated_at": "2026-08-16T14:02:11+00:00",
  "recommendation_status": "lean",
  "status_reasons": ["a real read, but no market price is posted yet to grade a Top Pick's price/value requirement against"],
  "reliability": "A"
}
```

## Field rules

- `signals` — every signal that fired, by stable name, with its raw numeric
  value. This is what weight-fitting and signal-pruning consume. A signal that
  did not fire is **absent**, not zero: absent and zero mean different things
  and conflating them teaches the fitter that missing data is a real reading.
- `cat_matchup`/`cat_recent_form`/`cat_environment`/`cat_baseline_skill`/
  `cat_context` — the five raw 0-100 category components `score` is built
  from (MATCHUP/RECENT FORM/ENVIRONMENT/BASELINE SKILL/CONTEXT), BEFORE the
  hand-set 35/25/15/15/10 weighting is applied. Only present for batters and
  pitchers (`score_batter`/`score_pitcher`) — the other prop-specific scorers
  (pitcher_outs, combined_strikeouts, stolen_base, laser, walk, first_inning)
  don't use this framework, so these are `null` on those rows. This is what
  `fit_score_weights.py` fits against `outcome` to test whether 35/25/15/15/10
  is actually the best split, or just what an old manual-reasoning report
  section happened to say.
- `sb_cat_skill`/`sb_cat_matchup`/`sb_cat_context` — the analogous raw 0-100
  components for `score_stolen_base`'s own 3-category scheme (weighted
  50/28/22), only present on `stolen_base` rows. Deliberately NOT named
  `cat_*` — a different weight scheme, must not collide with the
  batter/pitcher fields above in the shared schema. In practice these will
  never appear in `backtest/rows.jsonl`: `score_stolen_base()` always
  returns `None` in a backtest replay because sprint speed is a season-final
  Statcast leaderboard with no date-window support (see engine.py's own
  "WHAT COULD NOT BE RECONSTRUCTED POINT-IN-TIME" section) — recorded here
  for schema completeness and for the day a point-in-time sprint-speed
  source exists, not because today's backtest can use it.
- `predicted_prob` — model probability BEFORE any calibration is applied.
  Calibration must never be fit on already-calibrated values.
- `outcome` — 1 if the prop hit, 0 if not. Rows that cannot be graded are
  omitted entirely rather than encoded as 0.
- `fair_test` — did the pick get a real opportunity (see grade_results.py).
  Kept per-row so analysis can include or exclude, and must NOT be
  pre-filtered by the producer.
- `code_git_sha`/`backtest_generated_at` — added Phase 3, 2026-08-16. WHICH
  scoring-code commit produced this row and WHEN the row was generated —
  never confuse this with `date`, which is the historical date being
  replayed. A backtest replays a past date through TODAY's scoring
  functions, so two runs of the identical date range on two different
  commits can legitimately disagree, and nothing on disk could tell them
  apart before this. Both are `None` on any row written before this field
  existed (`backtest/rows.jsonl` predates it) — treat a missing
  `code_git_sha` as "unknown formula version," never as a specific one,
  when segmenting historical rows for the data-integrity tiers this
  enables (see `results/ANALYSIS.md`).
- `recommendation_status`/`status_reasons`/`reliability` — Stage 5,
  POLICY-ACCURATE REPLAY, added 2026-08-19. Present ONLY when a run used
  `--apply-policy` (`simulate_date(..., apply_policy=True)`); absent
  entirely (not `null`) on every row from a default run, including every
  row already in `backtest/rows.jsonl` today. When present, these are the
  REAL `generate_picks.apply_calibration()`/`attach_reliability()` and
  `recommendation.attach_recommendations()` — the identical functions and
  call order `generate_picks.py`'s live board uses, reused verbatim, never
  reimplemented (see `backtest/engine.py`'s `apply_replay_policy_
  precalibration()`/`apply_replay_policy_classification()`). This answers
  "does the CURRENT probability+evidence+calibration policy hold up out of
  sample on historical data it never saw," not "what would this exact
  historical date's board have shown" — `recommendation_status` here can
  only ever be `"lean"` or `"neutral"`, structurally NEVER `"top_pick"` or
  `"value"`, because no real historical market odds exist for a
  point-in-time replay (market signals are explicitly out of scope for
  backtesting, same limitation as always). Do not read a `"lean"` row here
  as "this would have been published" — it means "the model's own read,
  independent of price, was real and positive."
- `predicted_prob` above is always the raw, pre-calibration probability
  regardless of `--apply-policy` — Stage 5 does not change what
  `predicted_prob` means; the calibrated value lives only inside the
  `recommendation_status` classification, matching how `hit_probability`
  is likewise only ever calibrated on the live candidate object, never on
  the row's own probability field.
- `calibrated_prob`/`calibrated_by` — Stage 5, present only alongside
  `recommendation_status` (same `--apply-policy` gate). The actual number
  `classify_recommendation()` evaluated: raw `predicted_prob` run through
  the real fitted calibrator, kept OUT of `predicted_prob` itself for the
  exact reason above (a real bug caught while building this: `apply_
  calibration()` mutates the live candidate's probability in place, so
  reading it directly here would have silently changed `predicted_prob`'s
  meaning whenever `--apply-policy` was used). `calibrated_by` is `None`
  both when no curve exists for this market AND when one exists but this
  exact probability sat outside its own fitted support region (see
  `generate_picks._calibrate_one()`'s own docstring) — the two are told
  apart by whether `calibrated_prob` actually differs from `predicted_prob`,
  never by `calibrated_by` alone.

## THE RULE THAT MATTERS MOST: no lookahead

Every input used to score date D must be knowable strictly BEFORE the first
pitch on D. Season-to-date stats must be recomputed as-of D, never taken from
a season-final table. Statcast must be filtered to `game_date < D`.

This is the single failure mode that makes a backtest worthless while looking
excellent — leaked future information produces spectacular fake accuracy. Any
component that cannot guarantee point-in-time correctness for an input must
drop that input and say so, not approximate it.

Known unavailable historically, and therefore out of scope for backtesting:
betting-market signals (odds/sharp money). Line history only began being
captured 2026-08-05 and cannot be reconstructed backwards. Backtest validates
the stats-derived signals; the market signals stay unvalidated until enough
forward data accumulates, and that limitation must be stated in results
rather than hidden.

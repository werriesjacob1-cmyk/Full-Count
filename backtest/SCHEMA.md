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
  "predicted_prob": 0.71,
  "outcome": 1,
  "actual": 2,
  "fair_test": true,
  "actual_pa": 4
}
```

## Field rules

- `signals` — every signal that fired, by stable name, with its raw numeric
  value. This is what weight-fitting and signal-pruning consume. A signal that
  did not fire is **absent**, not zero: absent and zero mean different things
  and conflating them teaches the fitter that missing data is a real reading.
- `predicted_prob` — model probability BEFORE any calibration is applied.
  Calibration must never be fit on already-calibrated values.
- `outcome` — 1 if the prop hit, 0 if not. Rows that cannot be graded are
  omitted entirely rather than encoded as 0.
- `fair_test` — did the pick get a real opportunity (see grade_results.py).
  Kept per-row so analysis can include or exclude, and must NOT be
  pre-filtered by the producer.

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

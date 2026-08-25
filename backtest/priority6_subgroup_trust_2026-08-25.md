# Priority 6 -- same-nominal-probability subgroup trustworthiness

First real accuracy study run against canonical history, 2026-08-25. Script:
`backtest/prob_subgroup_trust_report.py` (11 tests), run against the real
`backtest/rows_canonical.jsonl` (1,027,462 rows). Full raw output is
reproducible any time via:

```
/tmp/mlbvenv/bin/python3 backtest/prob_subgroup_trust_report.py backtest/rows_canonical.jsonl
```

**Question**: within a fixed probability bucket (e.g. every row the model
called 0.60-0.65), does the realized hit rate vary by market, season phase,
`fair_test`, or opportunity (`actual_pa`) -- i.e. are some 65% predictions
more trustworthy than others?

**Scope honestly stated up front**: `backtest/rows_canonical.jsonl` is a
default (non `--apply-policy`) run, so `recommendation_status`/`reliability`/
lineup-confirmation-timing do not exist on these rows (see
`canonical_baseline_report.py`'s own UNAVAILABLE notes) -- this analysis
could not and does not segment by those. `cat_*` component fields exist on
only 3 of 13 markets (hits 69%, hits_runs_rbis 46%, strikeouts 100%; 0% on
the other 10) -- deliberately not segmented on here since partial,
market-inconsistent coverage would conflate a "market" effect with a
"component quality" effect; scoped as explicit future work, not attempted.

## Finding 1 (dominant, high-confidence): opportunity shortfall is the
largest source of within-bucket variance, and it is a POSTGAME-observed
signal, not a pregame-actionable one today

`fair_test=False` rows (5.4% of the whole dataset, 54,985 rows) pool to a
13.0% hit rate regardless of what probability the model assigned pregame --
and within nearly every probability bucket from 0.20 up through 0.75, the
`fair_test=False` subgroup underperforms its bucket's overall rate by
20-45 percentage points (e.g. the 0.70-0.75 bucket: overall 71.4%,
`fair_test=False` subgroup 26.7%, n=813). `actual_pa` tells the same story
with more granularity: the `0-1_pa` opportunity tier underperforms its
bucket by 26-40 points across every probability level tested, while
`5plus_pa` outperforms by 15-24 points, consistently, across essentially
every bucket from 0.20 to 0.75.

**Why this matters, and what it does NOT mean**: `fair_test` and
`actual_pa` are both filled in by `grade_results.opportunity_context()`
AFTER the game is final -- a real batter getting pulled for a pinch-hitter,
scratched, or facing an unusually short outing is not knowable with
certainty at scoring time. This is NOT evidence the model's own
probability math is wrong; it is evidence that a meaningful share of
recorded "misses" are opportunity failures rather than probability
failures, and that today's predicted_prob makes no attempt to distinguish
them. This is the direct empirical justification for the ALREADY-PLANNED
Priority 11 (realistic opportunity/PA-distribution modeling) -- if any
PREGAME-knowable signal predicts low expected PA (batting order slot,
blowout risk from the pregame total/spread, known short-leash pitcher
plans), incorporating it could genuinely separate "the model was wrong"
misses from "the batter never got a fair chance" misses. Not attempted
here -- this finding is the map, not the fix.

## Finding 2 (secondary, needs more investigation before acting): market
and season-phase effects are smaller and mixed

`strikeouts` underperforms its bucket in the 0.75-0.80 range (60.6%->44.8%,
n=2,584) but OVERperforms in the 0.35-0.40 and 0.40-0.45 ranges (+15 points
each, smaller n). `season_phase=offseason_or_other` underperforms notably
in the 0.70-0.80 range (n=250-1,512) -- root cause not investigated here
(this label technically means "date outside Apr/May-Jul/Aug/Sep-Oct", which
is surprising for a canonical file built from real MLB game dates only;
worth checking whether this is a real schedule-fringe effect, e.g. early
November games during an extended postseason, or an artifact of how few
rows land there before trusting it). Per the standing instruction, this is
flagged as a real, measured, low-confidence signal -- explicitly NOT acted
on, NOT promoted, and NOT assumed to be a baseball-explicable pattern
without further digging.

## What this does NOT do

Per the standing constraints: this is descriptive research, not a
promotion decision. Nothing here changes scoring, thresholds, or the
board. It sets the agenda for where Priority 7 (fragility), Priority 9
(market specialization), and Priority 11 (opportunity modeling) should
look first -- opportunity/PA modeling now has direct empirical backing to
be prioritized over more speculative feature work.

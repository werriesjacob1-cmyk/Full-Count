# What FULL COUNT is actually trying to beat

Measured 2026-09-03 from the **public Top Pick ledger estate only** —
`public_top_picks` inside `results/grades_*.json`, the immutable record of what
was actually published to users, carrying its own publication provenance and
settlement state. It never touches the legacy static-board rows, never touches
the prospective shadow, and never pools the three.

Reproduce: `python3 engineering/evidence/champion_baseline.py <results dir>`

## Headline

| | All public Top Picks | Hits only |
|---|---|---|
| published N | 130 | 27 |
| decided N (hit+miss) | 114 | 24 |
| void / ungraded | 2 / 14 | 0 / 3 |
| **realized hit rate** | **0.4825** | **0.5417** |
| Wilson 95% CI | [0.393, 0.573] | [0.351, 0.721] |
| **date-clustered 95% CI** | **[0.333, 0.606]** | — |
| mean predicted probability | 0.6443 | 0.6235 |
| **calibration gap** | **−0.1619** | −0.0819 |
| mean market-implied | 0.5898 | 0.5821 |
| **realized vs market** | **−0.1074** | −0.0405 |
| dates / picks per day | 10 / 13.0 | 9 / 3.0 |

## The three things that matter

**1. Realized is 16 points below predicted, and 11 points below the market's
own price.** Every market family is negative against its price:
`hits_runs_rbis` −8.7, `hits` −3.8, `strikeouts` −13.5, `pitcher_outs` −33.2.

**2. But the sample cannot carry a verdict.** Picks share a slate — same
weather, same umpires, same day's lineups — so the Wilson interval, which
assumes independent picks, is too narrow. Resampling **dates** instead of picks
(20k reps) widens it to **[0.333, 0.606]**, and the 0.60 floor sits *inside*
that interval. With 10 date-clusters this is a strong warning signal, not proof
of failure. Anyone quoting 48.25% as an established fact is overreading it.

**3. ~19 of the 114 decided picks were not fair tests.** Restricting to
`fair_test == True` moves realized 0.4825 → 0.5474 and the gap −0.162 → −0.100.
That splits the problem: roughly 6 points is operational (players who never got
a real chance), and roughly 10 points is the model being overconfident.

## Where the overconfidence lives — the actionable finding

| predicted band | n | realized | predicted | vs market |
|---|---|---|---|---|
| [0.60, 0.62) | 40 | **0.350** | 0.609 | **−0.198** |
| [0.62, 0.65) | 31 | 0.548 | 0.635 | −0.040 |
| [0.65, 0.70) | 32 | 0.500 | 0.670 | −0.122 |
| [0.70, 1.01) | 11 | **0.727** | 0.727 | **+0.072** |

The `[0.70, +)` band is **perfectly calibrated and the only band beating the
price**. It is 11 of 114 decided picks — about one a day.

The `[0.60, 0.62)` band — picks admitted by `MIN_LINE_PROB = 0.60` and sitting
just over the line — returns **35%** and loses **20 points** to the price. It is
35% of all decided picks.

**The threshold sits exactly inside the miscalibrated zone.**

## What this means for the supply question

The supply frontier does not point where it was assumed to point. Loosening
gates adds candidates from *below* 0.60, i.e. adjacent to the worst-performing
band already in production. On this evidence, added supply there adds losers,
not winners.

That is not an argument for tightening either — `MIN_LINE_PROB` is a production
threshold and changing it is Jacob's decision, on more than 10 date-clusters of
evidence. It is an argument that **the bottleneck is calibration in the
0.60–0.70 band, not gate restrictiveness.**

## Limitations, stated

* 10 date-clusters. Wide intervals. No verdict is available at this n.
* 14 of 130 published picks are ungraded; if they resolve non-randomly the
  point estimate moves.
* `pitcher_outs` at n=10 decided is anecdote, not measurement — the −33 point
  figure should not be quoted without its n.
* Public-ledger coverage begins 2026-08-18. Earlier `grades_*.json` files carry
  no `public_top_picks`, so this window is the whole available estate.

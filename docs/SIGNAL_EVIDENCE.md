# What the signals are actually worth

Measured on 12,582 backtested picks (2026-05-17 to 06-15), graded against
real box scores. AUC 0.500 is a coin flip. "Separates" means the 95%
confidence interval excludes 0.500.

## hits — the largest market (n = 5,654)

| signal | n fired | AUC | 95% CI | separates |
|---|---|---|---|---|
| lineup_slot | 5,654 | 0.554 | [0.538, 0.569] | yes |
| park_hr_index | 5,602 | 0.520 | [0.504, 0.535] | yes |
| l7_avg_ev | 5,620 | 0.519 | [0.504, 0.535] | yes |
| sp_era_weak | 5,548 | 0.518 | [0.503, 0.534] | yes |
| iso | 5,479 | 0.517 | [0.502, 0.533] | yes |
| l7_barrel_pct | 5,620 | 0.515 | [0.500, 0.531] | no |
| season_barrel_pct | 5,479 | 0.514 | [0.499, 0.530] | no |
| bat_speed_trend | 1,735 | 0.508 | [0.481, 0.536] | no |
| platoon | 5,077 | 0.500 | [0.483, 0.516] | no |
| bullpen_era_diff | 2,732 | 0.498 | [0.476, 0.520] | no |
| pitch_exploit | 2,519 | 0.488 | [0.464, 0.511] | no |
| bullpen_fatigue | 5,475 | 0.486 | [0.470, 0.502] | no |

**Seven of twelve are indistinguishable from noise**, and two of those sit
below 0.500. Platoon advantage — one of the most widely cited factors in
baseball betting — measures 0.500 exactly.

The strongest signal by a clear margin is `lineup_slot`, which is not a
measure of skill at all. It is a proxy for how many plate appearances a
hitter will get. **The model's real edge on batter props comes from
opportunity and base rates, not from matchup analysis.**

That finding also explains why so much effort produced so little: the
elaborate matchup machinery was being layered on top of a signal
(opportunity) that was already doing nearly all the work.

## walks (n = 5,634)

| signal | n fired | AUC | 95% CI | separates |
|---|---|---|---|---|
| batter_bb_pct | 5,459 | 0.568 | [0.551, 0.585] | yes |
| sp_bb_pct | 5,528 | 0.526 | [0.509, 0.542] | yes |

Both real. Fitted weights beat the hand-picked ones on held-out later dates
(AUC 0.591 vs 0.576, difference CI [0.0029, 0.0288], excludes zero), so the
0.4/0.4/0.2 split was replaced with the fitted 0.73/0.27 between batter and
pitcher walk rates.

## Fitted vs hand-picked weights

- **walks** — fitted wins, adopted.
- **hits** — no measurable difference (fitted AUC 0.549 vs 0.531, difference
  CI [-0.0058, 0.0442] contains zero). Hand-picked weights kept. Replacing
  them would have been a change with no evidence behind it.

## Why this matters more than it looks

The 0-100 score is now only a floor (a pick must clear 55). Probabilities
come from empirical game-log rates blended with a per-PA outcome model, not
from these signals. So pruning the dead signals cleans up the gate but does
not by itself move the picks much.

The actionable conclusion is the opposite of "add more signals": the things
that predict a prop hitting are how many chances the player gets and what
his own base rate is. Effort is better spent on estimating those two
precisely than on finding an eighth matchup adjustment.

## Recommendation mix after adding the six missing prop families (2026-08-07)

The commit that added runs/RBIs/HRR/singles/doubles/triples to `_batter_options`
flagged one likely side effect as UNMEASURED: that `hits_runs_rbis 1+`, at
roughly 0.767 for a good hitter, clears `MIN_LINE_PROB = 0.60` and could
therefore compete with "Over 0.5 Hits" for the recommendation slot.

Measured on a full slate scoring, 180 batter recommendations:

    before          hits 1+            180 of 180      100.0%
    after           hits 1+            110 of 180       61.1%
                    hits_runs_rbis 1+   69 of 180       38.3%
                    singles 1+           1 of 180        0.6%

So the every-batter-gets-the-same-prop monotony did break, and it broke without
touching the probability floor — 39% of batter recommendations moved to a
different market purely because the model can now price one it previously
could not.

What did NOT change, and why: still only three distinct batter markets on the
board. No home run, total bases, RBI or doubles recommendation appears, because
none of those clears a 0.60 floor for essentially any hitter — 1+ HR runs
0.10-0.25, 2+ TB around 0.40. That is the floor working exactly as specified,
not a bug. Those markets are now priced by the model and reachable through the
value board; they are simply not recommendable under a rule that requires the
recommendation itself to be likely.

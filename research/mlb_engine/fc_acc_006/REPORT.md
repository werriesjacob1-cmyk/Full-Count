# FC-ACC-006 — historical hitter early-exit diagnostic

**Recommendation: CONTINUE as inactive research only.** A prior-start,
player-specific replacement signal predicts future lost batting turns and
slightly improves PA and 1+ hit prediction in both evaluated years. The hit
gain is very small; there is no measured winner-selection or betting edge.
Do not change production scoring or selection policy on this evidence.

This is a retrospective **outcome-only exploratory** experiment. It uses
official final box scores to identify historical starters and outcomes. It
does not assert that the *target* game's lineup was confirmed before first
pitch, nor that any historical sportsbook offer was available. The 2025/26
evaluations were specified before seeing their FC-ACC-006 results but are not
clean prospective holdouts; this project has examined those seasons before.

## Official-source interpretation, validated before fitting

Six actual official box scores were compared with the corresponding official
play-by-play substitution actions. The URLs and SHA-256 digests are in
`validation_samples.json`. The checked games are 744795 (2024), 777722
(2025), and 822844, 824546, 824222, 824789 (2026). They cover pinch hitters,
pinch runners, defensive replacements, multiple successive replacements in
one batting slot, and position switches. Every expected action and parsed
starter/replacement identity matched.

Crucial encoding: a player `battingOrder` value of `100`, `200`, ..., `900`
denotes the original starter in slots 1–9. `101`, `201`, etc. denote later
occupants of that slot. The team-level `battingOrder` list can reflect the
*final* on-field lineup; treating it as the original lineup would create
false starter labels. A defensive position switch does not change the
player's batting-order sequence. A pinch runner or defensive replacement
with 0 subsequent PA verifies an exit but **does not** establish a lost
batting turn. We define `lost_turn` only when replacement occupants
collectively have at least one PA. This avoids calling a late replacement
after the slot's final turn an opportunity shortfall. The six-game check
demonstrates interpretation, not exhaustive proof of every scoring edge case.

## Population and provenance

The official MLB regular-season schedule returned 2,430 `abstractGameState`
`Final` entries in each of 2024, 2025 and 2026. Two were actually rain-
cancelled with zero plays and no batting order: game 746577 (2024-09-29)
and game 823490 (2026-09-27). Both fail closed. The 7,288 remaining played
games yielded 131,184 x00 starting hitters: 43,722 / 43,740 / 43,722 by
year. No played game was partially admitted; a game with any malformed side
is excluded in full. The official-source response URL, observation time and
SHA-256 appear in every normalized local row. `source_hashes.tsv` preserves
the hash and exclusion status for all 7,290 fetched box scores. Its SHA-256
is `89f4f07771ec9710752da08f155978edc98186bde44a242acf2c27d3980602c3`.
The full local raw-response cache (122,821,205 compressed bytes) and
normalized starter file (131,184 rows; uncompressed-content SHA-256
`b22d57a92dbbf5eb53d0f833fbfba8984655d35e6e6f6033626f9be66344cf3d`)
were retained outside Git under `work/fc-acc-006-data/`; they are not a
durable prospective seal. All official material was retrieved after the
games, so its retrieval timestamp is **not** a historical pregame timestamp.

| Year | Played games | Starting hitters | Any replacement | Lost later turn | Exit with 0 lost PA |
|---|---:|---:|---:|---:|---:|
| 2024 | 2,429 | 43,722 | 6,158 | 4,810 | 1,348 |
| 2025 | 2,430 | 43,740 | 6,194 | 4,808 | 1,386 |
| 2026 | 2,429 | 43,722 | 6,932 | 5,539 | 1,393 |

## Frozen comparison

All population constants come from **2024 only**. For each batting slot,
the primary baseline is the 2024 mean *starter* PA. The challenger uses the
same slot total PA mean, subtracting the expected PA ceded to a replacement:

`adjusted_PA = 2024_mean_slot_total_PA - risk * 2024_mean_lost_PA_given_lost_turn`

`risk = (lost_turns_in_last_20_prior_starts + 10 * 2024_slot_lost_turn_rate)
        / (number_of_last_20_prior_starts + 10)`

No target-game or same-day outcome enters that feature. Game outcomes update
history only after every game on the target's official date has been scored;
this deliberately forgoes potentially available first-game information in a
doubleheader. Prior games from earlier seasons may enter the last-20 window.
The simple `P(>=1 hit) = 1 - (1 - p_hit_per_PA)^projected_PA` comparison uses
an identical pregame-chronological 40-start, 40-PA-shrunk individual hit rate
in both arms. It isolates the PA adjustment; it is **not** an exact replay of
FULL COUNT's per-PA hit-outcome distribution. We separately extract the
current production `project_batter_pa(slot, None)` formula directly from
`generate_picks.py` (source SHA-256 recorded in `results.json`) as a
secondary sensitivity check. That formula was fitted with 2026 information,
so its use on 2025 cannot be called a chronological holdout baseline;
historical implied team totals and context vectors are unavailable.

The lookback 20, shrinkage 10, 40-PA hit-rate prior, outcome definition and
models were fixed without repeated 2025/26 tuning. Paired differences below
are challenger minus baseline, so **negative is better**. Each interval is
the two-way game/player cluster-robust 95% interval; separate 1,000-draw
game and player bootstrap intervals are in `results.json`.

| Outcome | 2025, n=43,740 | 2026, n=43,722 |
|---|---:|---:|
| Lost-turn prevalence | 10.99% | 12.67% |
| Exit Brier, baseline → challenger | 0.09603 → 0.09018 | 0.10886 → 0.10032 |
| Δ exit Brier (two-way 95% CI) | -0.00585 [-0.00712, -0.00457] | -0.00855 [-0.01046, -0.00663] |
| Starter PA MAE, baseline → challenger | 0.55638 → 0.54975 | 0.58091 → 0.57390 |
| Δ PA MAE (two-way 95% CI) | -0.00663 [-0.00858, -0.00468] | -0.00701 [-0.00929, -0.00473] |
| 1+ hit Brier, proxy baseline → challenger | 0.236714 → 0.236488 | 0.237851 → 0.237581 |
| Δ 1+ hit Brier (two-way 95% CI) | -0.000227 [-0.000325, -0.000129] | -0.000270 [-0.000396, -0.000145] |

The signal is meaningful for *replacement risk*: at predicted risk >=20%,
actual lost-turn incidence was 29.97% (5,772 rows) in 2025 and 31.47%
(7,462) in 2026. Their mean 1+ hit-probability adjustments were about
-1.96 and -2.07 percentage points. The aggregate PA MAE gain is only about
0.007 PA (roughly 1.2% of baseline MAE), and the hit Brier gain is about
0.1% relative. The hit metric worsened in March of both years; the 2025
fifth-slot PA MAE also worsened despite improvement in exit-risk prediction.
This is not uniformly stable across periods or slots.

An illustrative 60% *proxy* 1+ hit-probability threshold flips for 1,624
(3.71%) 2025 and 1,876 (4.29%) 2026 starter rows. These are not actual
FULL COUNT candidate or Top Pick changes. The board freezes include selected
and nonselected candidate records but lack the complete original feature
vectors and authenticated common alternative-offer history needed to replay
this new factor against champion/market-only at matched legitimate volume.
**Additional winners, usable pick volume and ROI: NOT MEASURABLE.** No price
or alternative offer was fabricated.

## Failure modes and next gate

- Substitution cause is unknown. Injury exits, platoon swaps, routine
  defensive replacements and lineup changes are pooled; previous starts
  cannot authenticate a manager's next-game intention.
- A replacement who never bats is a true exit but not a lost PA. If the
  official sequence or PA is missing, the game is excluded rather than
  assigned a favorable `lost_turn=0` label.
- Target starters are identified from **final** boxes; this cannot measure
  the fraction whose lineups were confirmed at an actual pregame cutoff.
- Team implied totals, authentic player-prop offers and full historical
  candidate feature vectors are missing. The chronological 2024 slot baseline
  is a historical proxy; the current production formula is post-2025 fit.
- The signal may already be priced by sportsbooks. A statistically clear
  substitution result and a tiny hit Brier gain do not establish another
  winning bet or profitable execution.

The minimum forward test is one **inactive, preregistered shadow arm** once
the existing capture/anchor gates are authorized and operational. At each
2027 pregame cutoff, preserve every confirmed-lineup eligible hitter, actual
two-sided sportsbook offer and price, raw source bytes/time, 20-prior-start
risk, champion probability, market-only probability and fixed challenger
probability. Freeze identical per-slate selection counts before first pitch,
including no-pick and missing-slate accounting. Compare champion, market-only
and challenger on added/removed settled winners, hit rate, real offered-price
return, odds/market mix and game/slate-clustered uncertainty. A positive
historical Brier result alone is not a promotion trigger.

## Reproduction

Python 3 with NumPy. The scripts use only public official MLB endpoints for
collection and sample validation; no credentials, sportsbook requests or
production workflow are involved. From repository root:

```text
python -m unittest research/mlb_engine/fc_acc_006/test_run.py -v
python research/mlb_engine/fc_acc_006/validate_official.py --cache <local-cache-dir> --out research/mlb_engine/fc_acc_006/validation_samples.json
python research/mlb_engine/fc_acc_006/run.py collect --cache <local-cache-dir> --out <local-output-dir>/starters.jsonl.gz
python research/mlb_engine/fc_acc_006/run.py evaluate --cache <local-output-dir>/starters.jsonl.gz --out research/mlb_engine/fc_acc_006/results.json
```

The collector uses four concurrent requests, a 20-second timeout, four
bounded attempts per URL, a 5 MB response cap, and resumable local gzip
caching. Raw responses and full normalized rows stay outside Git. The
committed source-hash index, six validation digests, fixed script and
aggregate result are the reviewable research artifact. Rerunning against
live historical endpoints may change bytes if MLB revises a response;
compare each returned digest against the index before treating it as the
same source snapshot.

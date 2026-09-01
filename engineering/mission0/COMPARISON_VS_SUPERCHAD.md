# Mission 0 — comparison of the independent reproduction against PR #79 / #81

Performed **after** the independent results were frozen and hashed
(`FROZEN_HASHES.txt`). Nothing below influenced any implementation choice.

## 1. Agreement with the stored SUPERCHAD results

Field-by-field against `hits_pa_opportunity_locked_result.json`, at the
precision SUPERCHAD stored (6 dp):

**Numeric disagreements: ZERO, in both walk-forwards, across every field.**

Including quantities I derived independently and never targeted:
`joint_cells_fit` 24 / 35, `train_player_games` 42,552 / 86,040,
`challenger_unavailable_neutral_fallback_n` 0, `challenger_direct_coverage`
1.0, `candidate_dates` 206 / 150, `added_hits` 2,674 / 2,605,
`removed_hits` 2,598 / 2,473.

The disagreement and combined reproductions likewise match every reported
quantity, including the disagreement score-source counts
(29,500 / 12,401 / 1,575 in 2025; 22,886 / 12,552 / 103 / 1 in 2026).

**Verdict: mathematically equivalent on this universe.**

## 2. Implementation differences

### 2.1 `backtest/experiment_primitives.py` (PR #79)

| | PR #79 | locked Aug-31 protocol / my implementation |
|---|---|---|
| candidate identity | `("date","game_pk","player_id","prop_type","line")` — **5 fields** | combined protocol states `(date, game_pk, player_id, prop_type, line, side)` — **6 fields**; I used `needs` as the side field |
| bootstrap cluster unit | **`game_pk`** (`paired_game_cluster_bootstrap`) | **date** — both the PA and combined protocols say "date-cluster bootstrap" |
| bootstrap seed | `20260829` | unspecified in either protocol |

### 2.2 `backtest/pa_opportunity_decisive.py` (PR #81)

| | PR #81 | locked Aug-31 protocol |
|---|---|---|
| walk-forwards | **one** — `TRAIN_END = "2025-12-31"`, evaluate 2026 only | **two** — train 2024 → eval 2025, and train 2024+2025 → eval 2026 |
| bootstrap | `paired_game_cluster_bootstrap` (game clusters) | date clusters |

## 3. Can any difference change selected identities?

**Identity width — no, on this universe; yes, as a contract.** Measured
directly on the certified Hits rows: 121,554 rows, and **121,554 distinct
keys under both the 5-field and the 6-field identity, zero duplicates
either way.** So #79's narrower identity selects the same candidates here
and `require_unique_population` would not raise. But it drops a field the
locked protocol names explicitly, and the bound
`pa_opportunity_model.candidate_key` docstring carries an explicit
alternate-line safety audit. A future market that quotes both sides of the
same line would silently collapse two candidates into one. This is a latent
contract weakening, not a present numerical error.

**Cluster unit — yes, it changes the reported interval.** Game-clustering
and date-clustering are different dependence structures; a date contains
many games, so date-clustering is the coarser and more conservative unit.
It does not change any *selection*, but it changes the confidence interval,
which is criterion 3 of the PA promotion bar and criterion 3 of the combined
bar. Using #79's bootstrap would evaluate a locked criterion against a
statistic the protocol did not specify.

**#81's single walk-forward — yes, structurally.** It cannot produce the
2025 result at all, so it cannot satisfy PA criterion 4 ("the 2025
walk-forward hit-rate delta is non-negative"). This confirms the standing
instruction: #81 implements PR #78's earlier design, not the Aug-31
experiment.

## 4. Primitives worth retaining for later engineering

Genuinely valuable and worth keeping, independent of this reproduction:

- `require_unique_population()` — fail-closed duplicate-identity rejection.
  I did not implement an equivalent; my reproduction would have silently
  tolerated a duplicated identity. Worth adopting, **widened to the 6-field
  identity**.
- `build_prediction_freeze()` / `deterministic_sha256()` — freezing and
  hashing the selection *before* outcomes are read. This is exactly the
  discipline the prospective ledger will need in Mission 1.
- `select_floor_matched_per_date()` — the per-date floor-matched volume
  contract, which is the right shape and the thing the old
  `equal_volume_ranking_comparison()` got wrong.
- The explicit invalid/empty changed-set handling in the bootstrap.

## 5. What should be retired or superseded

- **`paired_game_cluster_bootstrap` should not be used to evaluate the
  canonical-v2 locked criteria** without either (a) adding a date-cluster
  mode, or (b) a new dated protocol that changes the clustering unit
  deliberately. It is not a defect in itself — it is the right statistic for
  a different, earlier protocol.
- **`backtest/pa_opportunity_decisive.py` (#81) is superseded** as a
  reproduction vehicle for the Aug-31 experiment. Its single `TRAIN_END`
  cutoff encodes PR #78's design. Either retire it or extend it to the
  two-walk-forward contract and re-point its bootstrap.
- `IDENTITY_FIELDS` should be widened to include the side field before
  `experiment_primitives` is used for anything that could see two-sided
  markets.

## 6. Under-specification found in the locked protocols themselves

Both the PA protocol and the combined protocol require a "deterministic
date-cluster bootstrap 95% interval" but pin **no RNG, seed, or replicate
count**. Any independent implementation therefore draws different clusters.
Mine vs the reported intervals:

| | mine | reported |
|---|---|---|
| PA 2025 | [-0.000486, +0.007209] | [-0.000269, +0.007230] |
| PA 2026 | [+0.003332, +0.014043] | [+0.003509, +0.014263] |
| disagreement 2025 | [-0.0991pp, +0.2212pp] | [-0.1094pp, +0.2207pp] |
| disagreement 2026 | [-0.1661pp, +0.2134pp] | [-0.1791pp, +0.2095pp] |
| combined-minus-PA 2025 | see result JSON | [-0.0964pp, +0.0614pp] |
| combined-minus-PA 2026 | see result JSON | [-0.1818pp, +0.0337pp] |

Every interval agrees on its decision-relevant property, so no verdict turns
on the gap. But a promotion criterion currently depends on a statistic whose
computation is not fully pinned. **Any protocol that gates a decision on a
bootstrap bound should pin the seed and replicate count**, exactly as PR #79
already does internally. Recorded as a real finding, not silently smoothed.

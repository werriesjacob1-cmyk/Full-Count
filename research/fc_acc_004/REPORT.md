# FC-ACC-004 — exploratory pitcher workload test (2026-10-16)

**Decision: MODIFY the hypothesis; do not adopt this simple adjustment.** It marginally reduces strikeout-count error in retrospective data, but does not identify more fixed-threshold winners, sharply reduces the number of over decisions, and has no authentic historical sportsbook offers. This is outcome-only development evidence, not an operational selection or profitability result.

## Data availability and provenance

| Source / seasons | Pitcher start fields | Batter / market fields | Point-in-time status | Use here |
| --- | --- | --- | --- | --- |
| Public MLB StatsAPI pitching game logs, 2024–26, retrieved 2026-10-16 UTC | MLBAM pitcher ID, game ID, date, opponent, pitches, BF, outs, K, innings | No offers or batting order | Historical logs retrieved after the games. Earlier *game dates* can be excluded strictly, but no contemporaneous response bytes or original publication times exist. | Primary outcome-only sample. Raw JSON kept locally, SHA-256 manifest in `report.json`. |
| Public MLB StatsAPI final regular-season schedule, 2024–26 | Game ID, final status | No offers | Retrieved retrospectively | Independent denominator. 2 starter logs for every final game: 2,429 / 2,430 / 2,429 games. |
| Repository `backtest/rows.jsonl` / canonical rebuild | Earlier research audit describes 574 pitcher-market rows across 20 dates with `actual_ip`, but underlying rows are absent in this checkout/current main. | Backtest contract explicitly has no authentic historical odds; only one candidate per player/game. | Today's scoring code replaying historical dates; not original pregame decisions. | Inventory only. |
| FC-MLB-002/004/005/006 research branch `a555d4d` (not current main), 2025–26 | Pitch-level raw files described in a 385-day manifest, not committed; frozen fits/results committed on that research branch. | Batter/PA/matchup features and some FanDuel observations in separate studies. | Retrospective 2025 fit and 2026 previously inspected development outcomes. | Demonstrates no clean untouched 2025/26 holdout remains. |
| PR #226 FC-ACC-001 capture | Last-three prior start pitches/outs and provenance code on draft branch | Prospectively authenticated offers possible, currently inactive | Synthetic/inactive only; external chain anchor still missing | 2027 dependency, not used for historical betting claims. |

The repo's `data/accuracy_lab/holdout_manifest.json` points to `backtest/rows.jsonl` with a 2025-08-22 cutoff, but the rows are absent here and that historical period has since been inspected. We claim no untouched historical holdout. The previously reconciled public baseline (609 settled published Top Picks, 321 wins, 288 losses, roughly -11.2% flat-stake ROI) is a distinct population and is **not** mixed with these starter logs.

## Production code baseline and fixed diagnostic

`generate_picks.py` at current main still computes expected BF as `n/(n+2.62) * mean BF of starts in prior 14 days + 2.62/(n+2.62) * 23.1`, bounded 12–30. `project_pitcher_ks` consumes that workload and a K/BF rate. `score_pitcher_outs` instead uses MLB game-log threshold frequencies shrunk by six pseudo-starts; it does **not** numerically consume recent pitch counts or pitcher-specific rest. This script reproduces the BF workload component, then uses strictly earlier same-season outs/BF and K/BF with fixed weak 100-BF priors to predict *counts*. It is a reproducible **component proxy**, not a replay of the full production probability, opponent adjustment, actual line selection, or publication policy.

One challenger was specified before evaluation: fit an additive count residual on 2024 starts using (a) last minus first of the previous three starts' pitch counts, divided by 20, and (b) an indicator for at most three **full off-days** since the prior start. Ridge penalty 10 was fixed. Apply the same 2024 coefficients unchanged to 2025 validation and 2026 already-exposed evaluation. Require at least three prior same-season starts, strict earlier calendar date, regular-season games, and valid official counts. Same-day multi-game pitcher records are excluded. This does not identify documented injury restrictions; no such archived pregame source was present.

Coverage: 14,576 official starter logs match 7,288 final games (two per game). Two 2024 starts had 0 BF and were excluded as invalid for rate modeling. The remaining starts were 4,856 / 4,860 / 4,858 in 2024/25/26. Three-prior-start requirement removed 2,893 starts, leaving 3,883 / 3,907 / 3,891 opportunities. 2024 training contains only **9** short-rest starts; 2025 has **3**, 2026 **19**. This makes the fitted short-rest term unstable. L14 BF history was empty in 167 / 130 / 157 modeled starts respectively; both arms used the same 23.1 fallback. MLB `gamesStarted=1` includes any opener, which these logs cannot reliably identify pregame; bulk relief appearances are excluded. Long gaps of at least 30 full off-days occurred in 80 / 64 / 73 modeled starts, but a gap does not authenticate injury or a pitch restriction. No target start was retrospectively removed for its own short outing, avoiding outcome-based eligibility leakage.

| Evaluation | Target | Baseline MAE | Challenger MAE | Paired delta (challenger − baseline) | Pitcher-cluster 95% bootstrap CI |
| --- | --- | ---: | ---: | ---: | ---: |
| 2025 validation | Outs | 2.7791 | 2.7906 | +0.0116 | [+0.0008, +0.0223] |
| 2025 validation | K | 1.8445 | 1.8379 | -0.0065 | [-0.0104, -0.0025] |
| 2026 development | Outs | 2.9182 | 2.9092 | -0.0089 | [-0.0229, +0.0040] |
| 2026 development | K | 1.8610 | 1.8503 | -0.0107 | [-0.0155, -0.0062] |

Two thousand fixed-seed resamples clustered by pitcher produced the intervals; game-cluster intervals in `uncertainty.json` agree in direction. These tiny differences concern count error, not winner selection. The 2024 fit assigned short rest -2.965 outs and -0.829 K from just nine short-rest training starts. No causal or prospective interpretation is justified.

At fixed *diagnostic* thresholds of 15 outs and 5 K, no odds or actual line presumed:

| Period/target | Baseline over decisions | Challenger over decisions | Newly over / winners | Removed over / winners |
| --- | ---: | ---: | ---: | ---: |
| 2025 outs | 3,571 | 3,370 | 0 / 0 | 201 / 147 |
| 2025 K | 1,873 | 1,689 | 1 / 0 | 185 / 105 |
| 2026 outs | 3,442 | 3,244 | 1 / 1 | 199 / 115 |
| 2026 K | 1,944 | 1,778 | 5 / 2 | 171 / 91 |

The challenger does not preserve decision volume. These are **not** matched sportsbook opportunities; a threshold of 15 outs or 5 K was chosen as a fixed baseball diagnostic, not inferred to be a real offered line. No bet ROI, edge or Top Pick lift is computed. The 2026 results are development-only because 2026 outcomes were previously inspected in FC-MLB studies.

## Limited secondary inventory and next hypotheses

1. **Documented start-specific pitch cap/recovery** is the strongest workload lead but unmeasured: official previous-start pitch counts are available; contemporaneous restriction statements and reliable point-in-time interpretation are not archived. A future prespecified cap indicator must beat the same BF baseline and market-only selection without discarding opportunities.
2. **Confirmed-lineup playing time**: current code already consumes batting slot and `project_batter_pa`, with a team-total fallback; lineup capture exists. The gap is authenticated pre-cutoff confirmation/substitution status for *all* candidates and proof that it changes hits/hits+runs+RBIs winners beyond price. Retrospective finalized batting order is not pregame proof.
3. **Contact and matchup**: current code already consumes pitcher hand, platoon, starter context, bullpen fatigue/quality, and some contact features. Retrospective FC-MLB experiments have exposed 2025/26 outcomes. A narrow forward test should isolate handedness-adjusted contact quality and expected starter/bullpen exposure, rather than retune on these periods.

## Minimal 2027 comparison

Use one preregistered unit and all-candidate opportunity manifest per slate. Freeze A) current publication-policy champion, B) authentic-price market-only, C) fixed 50/50 model-market blend, and D) one prespecified workload challenger before first pitch. Bind each candidate to event/player/market/side/line/odds/source/time and point-in-time features. Match selection counts per slate, record missing and zero-pick slates, preserve policy-specific eligibility, independently anchor the immutable decision/evidence chain, and grade all outcomes including voids/pushes. Primary comparison: additional settled winners at matched legitimate volume and realized return at captured prices. The inactive PR #226 capture path still needs an independent published chain anchor, durable evidence recovery, full-source capture and operational cutoff validation before activation; none is supplied by this offline study.

## Reproduction

Run `workload_exploratory.py`, then `coverage_check.py`, then `uncertainty.py` with Python 3 and NumPy. The first run downloads bounded public MLB season stats/game logs; subsequent runs use local raw caches. `report.json` records every raw-response SHA-256. The output `model_rows.csv` was replayed byte-identically from the cached raw files, SHA-256 `d81315c3628a4c81487d6dbd1000fb2ab4114ef046d571f0a189d636922ad6af`. Raw source bytes and the 1.35 MB derived row table remain local; no public or protected evidence was changed.

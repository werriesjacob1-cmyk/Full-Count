# FULL COUNT — Prospective Hits PA Shadow Protocol v1

**Status:** LOCKED BEFORE ANY FORWARD OUTCOME IS USED  
**Locked:** 2026-09-01  
**Evidence regime:** prospective full-candidate / operational shadow  
**Production authority:** NONE. Shadow only until Jacob explicitly authorizes otherwise.

## 1. Objective

Test whether the historically validated Hits PA/opportunity selector beats CURRENT FULL COUNT on genuinely unseen MLB games at the same legitimate usable operational pick volume.

Historical evidence is not enough. The forward test must bind the exact live candidate state, real posted line/price, real publication timing, and later settlement without rewriting the pregame receipt.

## 2. Challenger

**PA-v1 only.**

The disagreement standalone result is not sufficiently robust under the full-universe/per-date standard, and the one preregistered PA+disagreement tie-break combination failed to improve PA. Neither disagreement nor a combined score is part of the primary forward policy.

### PA-v1 algorithm

Use the same algorithmic form that earned historical shadow status:

- empirical P(PA | batting order, days-rest group, getaway-day group);
- MIN_CELL_N = 200;
- sparse/missing joint cell -> order-only P(PA|order);
- empirical P(Hits prop succeeds | actual PA);
- challenger P(hit) = sum_k P(PA=k|context) * P(hit|PA=k);
- if live PA score is unavailable, neutral fallback = current Full Count hit probability;
- no postgame field is a live feature.

No feature, threshold, cell size, fallback rule, or ranking rule may change in response to prospective results without creating a new version and new forward regime.

## 3. PA-v1 fitting freeze before launch

Before the first forward receipt is eligible for evaluation, fit PA-v1 ONCE using the independently certified canonical-v2 research view available at launch:

- research rows SHA256 = `8ca010641d08008044c8c3b609162d6e5d69f07bb79be6705b2690a51ab2cb34`;
- only rows dated on/before 2026-08-25;
- graded hitter-market rows for PA distribution;
- player-game dedupe exactly `(date, game_pk, player_id)`;
- graded Hits rows for P(hit|PA);
- whole-date quarantines remain excluded exactly as certified.

Persist a deterministic fitted artifact containing the empirical tables and:

- training rows SHA / certified input identity;
- training cutoff;
- fitting code SHA;
- algorithm/protocol version;
- MIN_CELL_N and all feature/grouping definitions;
- fitted artifact SHA256;
- created_at;
- effective_from.

Once the first eligible prospective receipt exists, PA-v1 is frozen. September/forward outcomes may NOT refit PA-v1. A later refit is PA-v2 with a new effective timestamp and may not retroactively replace PA-v1 scores.

## 4. Exact live capture boundary

Primary implementation target: the same full Dashboard Refresh event that runs `dashboard/build_dashboard.py::run_live_fetch()`.

Capture the Hits research universe after, in the SAME process/event:

1. `generate_picks._build_and_score()` has produced the candidate state;
2. quality control has partitioned confirmed vs assumed lineups;
3. signal-trust weights have been applied;
4. the same FanDuel price maps used by the board are attached;
5. `odds_fetched_at` is known;
6. `select_best_by_category(..., n_per_category=9999)` has materialized the board-expression Hits population;
7. already-started games have been removed;
8. recommendation classification has been attached;
9. scientific/raw identity fields have NOT yet been stripped by public `clean()` serialization.

For Hits, prefer the exact raw `by_category_full["hits"]` expression population at this point, after dedup/identity validation.

Do not run a second network/scoring pass for the primary prospective scoreboard.

The research tap must be non-blocking: failure to persist research state must never alter public recommendations or abort the customer dashboard build. It should fail loudly in research observability while production output proceeds unchanged.

## 5. Primary operational candidate pool

From that exact Hits expression population, a row is eligible for the shadow ranking only when ALL policy-independent operational requirements hold:

- valid canonical v2 prop/settlement identity;
- stat = Hits;
- exact line/needs/side captured;
- structured public settlement supported;
- game is pregame and before the canonical betting/publication cutoff;
- authoritative commencement has not occurred;
- game is not a prior-date resumption of an already commenced game;
- confirmed lineup (`lineup_assumed == False`);
- nonzero real evidence sample (`sample_n != 0`);
- reliability is A or B;
- real current FanDuel price exists for this exact expression;
- board/price freshness is valid;
- no known source-integrity hold applies to this candidate/team/game.

Do NOT require current predicted_prob >= 0.60 for challenger-pool membership; that is part of champion selection, not a policy-independent usability requirement.

Do NOT require the candidate to clear CURRENT model value/ROI logic to enter the challenger pool, because that test is mathematically conditional on the current probability. The primary North Star here is realized Hits accuracy at equal selection volume. Preserve the real odds so ROI/value diagnostics can be reported separately.

## 6. Champion selection and equal volume

For the decisive epoch, champion selection is the exact set of Hits Top Picks actually eligible for public exposure in the corresponding Pages artifact/deployment, not a reconstructed historical proxy.

Let `N(date)` be the number of champion Hits Top Picks from that exact decisive epoch that are also present in the bound shadow candidate pool.

Fail the epoch closed if any champion Hits Top Pick cannot be matched exactly to the frozen shadow universe / canonical identity.

PA-v1 selects exactly N(date) candidates from that same frozen operational pool, ranked by:

1. higher frozen PA-v1 score;
2. higher current Full Count hit probability;
3. stable canonical candidate identity.

If N(date) = 0, no primary comparison is created for that date. Do not manufacture picks.

## 7. One deterministic decisive epoch per MLB slate date

To prevent repeated intraday snapshots from becoming duplicated observations, the PRIMARY prospective scoreboard uses exactly one decisive full-build epoch per MLB slate date.

After the date is over, choose mechanically and outcome-blind:

> the latest successfully converged **Dashboard Refresh-originated Pages deployment** whose artifact was prepared early enough to admit new Top Picks under the existing publication cutoff contract, and whose shadow snapshot is hash-bound to that exact full build.

Current publication infrastructure already requires new exposure candidates to remain at least 15 minutes from first pitch at artifact preparation. Preserve that rule; do not create a second looser shadow cutoff.

If no eligible full-refresh deployment with a valid bound shadow snapshot exists for a date, that date has **NO PRIMARY EPOCH**. It is missing operational evidence, not a loss and not an invitation to choose a more favorable snapshot.

Live-update-only Pages deployments may be retained as engineering/secondary observations but are not part of v1's primary scoreboard because the full scientific candidate features needed for PA scoring are not guaranteed to be re-materialized from the same in-memory scoring event.

## 8. Two-stage exposure proof

The pregame shadow snapshot is created before deployment and contains no outcome.

A primary event becomes operationally countable only after the corresponding Pages artifact successfully converges publicly under the existing deployment verification.

Bind the shadow snapshot to at least:

- full-build workflow/run identity;
- source commit used for the build candidate;
- board generated_at;
- odds_fetched_at;
- hidden-candidate snapshot SHA;
- public data/live state hashes or publication manifest identity where available;
- Pages publication artifact identity/source commit;
- public convergence/deployment timestamp;
- champion public candidate IDs for that event.

Do NOT mutate `data/public_top_picks/registry.json` into the research ledger. It is lifecycle/publication truth and explicitly not the Prediction Receipts ledger. Link to it/provenance; keep research receipts separate.

## 9. Immutable pregame receipt

Each selected champion and PA candidate receipt must preserve the exact wager expression and decision state, including where available:

- receipt schema/version;
- decisive_epoch_id;
- snapshot_id/content SHA;
- canonical prop id + settlement identity;
- date/game/player/team;
- stat/side/line/threshold/needs;
- game start;
- lineup confirmation;
- source-integrity state;
- current Full Count probability;
- PA-v1 probability and fallback state;
- current score/components/signals needed for audit;
- reliability/sample/CI provenance;
- book and exact odds;
- odds timestamp;
- market implied/fair/edge fields where available;
- recommendation status/gate trace;
- champion membership/rank;
- PA membership/rank;
- model/selection/calibration/feature versions;
- git SHA;
- PA fitted-artifact SHA/protocol SHA;
- receipt content SHA.

No outcome/actual field is allowed in the pregame receipt.

A later price/line/snapshot is a different receipt state; it may not overwrite the decisive receipt.

## 10. Durable append-only storage

The sole evidence copy may NOT be a local/gitignored JSONL.

Implement an interruption-safe, remotely durable append-only Prediction Receipt / shadow ledger with:

- idempotent keys;
- no silent overwrite;
- content hashes;
- append-only logical events;
- writer/git/model provenance;
- restart/container-loss safety;
- concurrency-safe retries;
- retention suitable for multi-season prospective evidence.

Prefer a dedicated research-data branch/path or another durable architecture that does not create five-minute generated churn on `main`. GitHub Actions artifacts may be a redundant evidence copy but are not sufficient as the only long-term ledger if retention expires.

Pregame receipt events and settlement events remain distinct event types.

## 11. Settlement

Settlement occurs later through the existing production grading / conservative FanDuel settlement semantics.

Never mutate the pregame receipt.

Outcome events key to the exact receipt/canonical wager expression, not `latest candidate state`.

The old `candidate_funnel_grader.load_latest_records()` reduction is explicitly NOT acceptable for the primary receipt ledger because it can substitute a later candidate state for the wager expression selected earlier.

Record separately:

- hit;
- miss;
- void;
- ungraded/missing;
- settlement reason/authority/source;
- graded_at;
- exact receipt id/hash.

Hit-rate denominator uses decided hit+miss only, matching wager settlement semantics. But selection N, void rate and ungraded/missing rate must also be reported; a challenger may not appear superior merely by producing more non-decisions.

## 12. Required prospective reporting

At every reporting checkpoint:

- number of primary slate dates;
- champion selected N, decided N, hit/miss/void/ungraded;
- PA selected N, decided N, hit/miss/void/ungraded;
- exact selected-N equality by date;
- champion realized hit rate;
- PA realized hit rate;
- delta;
- overlap;
- PA-only and champion-only N/hit rates;
- date-cluster bootstrap CI for PA-minus-champion;
- game/player clustering notes;
- date contribution direction;
- lineup/source/fallback rates;
- odds distribution diagnostics;
- all missing-primary-epoch dates and reasons;
- exact PA-v1 fitted artifact identity and receipt schema versions.

No market mix ambiguity: primary v1 market is Hits only.

## 13. No optional stopping / promotion review bar

Progress may be monitored, but a hot early streak cannot trigger promotion.

Formal human promotion review is not earned until BOTH are satisfied:

- at least **30 distinct primary MLB slate dates**, and
- at least **100 decided champion selections AND 100 decided PA selections** under v1.

If the 2026 season ends before these minima, report `INSUFFICIENT PROSPECTIVE EVIDENCE` and carry the same frozen PA-v1 regime forward unless a new version is explicitly started. Do not lower the bar after seeing results.

At/after the minimum, a positive review requires ALL:

1. PA-v1 realized hit rate > champion at exact equal selected N per decisive date;
2. PA-only decided hit rate > champion-only decided hit rate;
3. direction is not explained by one date/game cluster; date-cluster bootstrap 95% lower bound for PA-minus-champion > 0;
4. no material increase in void/ungraded rate that is masking lower usable settled volume;
5. no source-integrity or receipt-identity violations;
6. exact candidate-universe/policy/model provenance verifies;
7. Jacob explicitly authorizes any next step.

Until then the state is `PROSPECTIVE SHADOW — NO PRODUCTION PROMOTION`.

## 14. Safety and authority

Shadow code must not change:

- current probabilities;
- Top Pick selection;
- recommendation thresholds;
- public ordering;
- public copy;
- notification behavior;
- public registry truth;
- settlement semantics.

No merge/deployment/promotion is authorized by this protocol. Stop before merge and report the branch/PR/evidence for Jacob's decision.

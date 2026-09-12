# FULL COUNT total sports intelligence baseline and execution roadmap

Date: 2026-09-12

Evidence branch: `codex/total-sports-foundations-20260912`

Audited branch base: `db095b30d5f66192a40a558c286db03dfb42da1b` (`main`, Dashboard refresh at 23:30 UTC). Remote `main` was rechecked at `787cc0389063e62c8bbb59a722d300ac049582a8` during final validation. `main` is a moving branch because MLB workflows commit generated state; re-fetch before every integration decision.

This report inspects code and retained data. A filename, comment, or planned module is not counted as an operating capability without a caller, workflow, or artifact proving use.

## A. Current repository truth

- PR #88 merged as `fb52ca53c2ad9756df61e6a8773cd13334043a63`.
- PR #90 merged as `eb328df962ffa7390ec34c90b1ca1667d3a45229`.
- Cloudflare Worker `994ee921` is active and its post-deploy dispatch succeeded.
- GitHub Pages deployment `34725230618` succeeded. The public NFL assets are byte-identical to the PR #90 merge commit.
- The NFL surface is an empty prospective research control. PR #89 grading remains separate and inactive.

## B. NFL historical data that exists

The repository contains temporal transformation code, not a committed NFL warehouse.

- `nfl/research/nflverse_history.py` converts nflverse weekly player-stat CSV rows into strictly prior-appearance features. Its required source columns cover passing, rushing, receiving, team, opponent, position, season, week, and season type.
- The live shadow workflow downloads byte-pinned nflverse weekly player statistics for 2023, 2024, and 2025 on each run. Those CSVs are retained inside the run's evidence artifact, not as a repository-native historical warehouse.
- The workflow downloads a byte-pinned 2026 roster for current identity binding.
- B0 evaluation uses 2024 and 2025 regular-season QB rows with at least three prior appearances: 611 rows in 2024 and 617 in 2025. Its recorded MAE is 70.8354 and 72.4199 passing yards respectively. The pooled empirical residual population is 1,228.
- Current 2026 projections use 2025 prior appearances, including legitimate postseason appearances.
- No NFL play-by-play, drive, participation, route, pressure, formation, personnel, coaching, historical injury, historical odds, or line-movement warehouse is committed.

## C. Exact NFL seasons currently used

| Use | Seasons |
| --- | --- |
| Downloaded weekly player-stat substrate | 2023, 2024, 2025 |
| B0 held historical benchmark/residual population | 2024, 2025 regular seasons |
| Current projection history | 2025, including postseason |
| Current roster identity | 2026 |

The URL builder accepts other plausible years, but accepting a year parameter is not evidence that those seasons have been downloaded, validated, or warehoused.

## D. NFL markets currently discovered or ingested

Before this branch, no durable complete market registry existed. `nfl/archive/sources/fanduel_nfl.py` could fetch broad tabs, but production's passing-yards workflow only normalized its target family.

A bounded live census at 2026-09-12 23:39:12 UTC inspected all eight verified research-relevant tabs for Buffalo at Houston and found 107 distinct FanDuel market types. The seed registry is `data/market_coverage/registry.json`; its gap report is `engineering/evidence/nfl_market_coverage_report_2026-09-12.json`.

The observed universe includes:

- Standard moneyline, spread, total, first-half winner/spread/total, quarter spreads, alternate spreads/totals, and team totals.
- Primary and alternate passing yards, passing touchdowns, rushing yards, receiving yards, receptions, and drive-specific player markets.
- Anytime/first/last/multiple/period touchdown markets.
- Defensive sack, kicking, scoring sequence, winning margin, race-to-points, and game-special families.

This is one-event evidence, not a claim that 107 is FanDuel's permanent or exhaustive NFL universe. Daily multi-event census and other books remain required.

## E. NFL markets that were invisible to Full Count decisions

Of the 107 live-observed source families, 106 have no normalizer. Fifteen were recognized as alternate families but have no represented normalized ladder. Every source family lacks an active NFL grader.

The previous implementation explicitly ignored alternate passing yards, passing touchdowns, receiving, rushing, spreads, totals, moneylines, and unknown types. The registry now makes those gaps visible without pretending they are modeled.

## F. Current NFL player-prop coverage

Only primary two-sided quarterback passing yards is operational:

- Exact FanDuel market/runner/line agreement.
- Exact 2026 roster and event-team identity binding.
- Official inactive coverage and same-day publication chronology.
- Five-appearance rolling B0 projection.
- Empirical residual side probabilities and two-sided no-vig market probabilities.
- Prospective `SHADOW_ONLY` or `QUARANTINED` capture.
- Research-only website projection.

Passing-yard alternates and every other player family are discovery-only. There is no active NFL public selector.

## G. Current NFL game-market coverage

| Market | Feed observed | Normalized | Historical dataset | Model | Prospective capture | Grader |
| --- | --- | --- | --- | --- | --- | --- |
| Moneyline | Yes | No | No | No | No | No |
| Full-game spread | Yes | No | No | No | No | No |
| Alternate spread | Yes | No | No | No | No | No |
| Full-game total | Yes | No | No | No | No | No |
| Alternate total | Yes | No | No | No | No | No |
| Team totals/alternates | Yes | No | No | No | No | No |
| First-half winner/spread/total | Yes | No | No | No | No | No |
| Quarter spreads | Yes | No | No | No | No | No |

Spreads and totals are therefore the highest-value game-market research gap, exactly as the mission prioritizes.

## H. Current coaching data

None. No time-bound head-coach, coordinator, organization, or scheme history exists. Team identity in weekly player rows cannot answer who called plays or whether a tendency was portable.

## I. Current play-caller data

None. Title and actual play-calling responsibility are not represented. There is no dated responsibility interval, provenance, confidence, or transition record.

## J. Current film-related assets

None. There is no licensed video ingestion, play-to-video identity, film ontology, observation store, reviewer agreement, confidence model, or residual-value experiment. A code comment refers to `media_discovery.py`, but that file does not exist. The repository correctly does not label ordinary tracking data as film.

## K. Current news-intelligence assets

The NFL archive source captures raw official NFL injury, inactive, transaction, score, and standings pages. The live workflow parses and binds game-day inactive reports, including article publication time and observation time. This is useful availability evidence, but it is not a News Brain.

There is no structured claim ledger, speaker identity/reliability, contradiction graph, practice-progression history, market-reaction join, beat-reporter source system, or prospective claim evaluation.

## L. Current alternate-line support

- NFL: feed discovery sees ladders, but normalization and capture intentionally reject them. B0's empirical residual function can calculate a probability at an arbitrary threshold, but no ladder identity, adjacent-price validation, full runner capture, hold curve, or prospective alternate evidence exists.
- MLB: one-sided threshold ladders are captured for several batter markets, and candidates can retain alternatives. Two-sided pitcher markets are supported. There is no complete book-wide alternate coverage registry or cross-line distribution/hold research program.

## M. Current probability and distribution architecture

NFL B0 is a control, not a full distribution model:

- Projection: mean passing yards from the last five supplied appearances, with a three-appearance minimum.
- Probability: empirical pooled distribution of `actual - B0 projection`, evaluated at the current line with Laplace smoothing.
- Market comparison: exact two-sided American-odds de-vig.
- Output: a research direction and probability edge, never a public pick.

It does not model attempts and efficiency separately, conditional game script, correlation, tails by player/regime, drive/possession state, or a coherent joint game world.

MLB has market-specific Bernoulli/count approximations, empirical rates, intervals, calibrators for a subset of markets, price/value math, and alternative lines. It does not yet provide a coherent full-game joint distribution across all markets.

## N. Current MLB market coverage

The current dashboard exposes 15 labeled candidate families: hits, total bases, home runs, runs, RBIs, hits+runs+RBIs, singles, doubles, triples, stolen base, pitcher strikeouts, pitcher outs, combined starter strikeouts, NRFI/YRFI, and 105+ MPH hard-hit research.

The FanDuel one-sided map recognizes 28 source market types across threshold ladders. Twelve two-sided pitcher-strikeout type names are recognized, with separate fetchers for pitcher outs, combined starter strikeouts, and first-inning totals.

The retained September 12 MLB odds snapshots contain moneyline, spread, total, and team-score rows. Those game prices are archived context; the customer candidate system does not model or select full-game moneylines, run lines, or totals. Current visible game selection is limited to NRFI/YRFI research.

Unknown MLB source markets are not yet recorded by the new registry. The next adapter must census the complete MLB payload before existing `MARKET_MAP` filtering.

## O. Existing cross-sport learning infrastructure

MLB has substantial research components: `eval_lib.py`, backtest schemas and replay, calibration audits, market benchmarks, equal-volume challenger comparisons, `accuracy_lab.py`, `champion_challenger.py`, publication-aware grading, and error-oriented reports.

The infrastructure is not cross-sport. NFL does not use the MLB backtest/challenger stack. There is no shared learning ledger, error taxonomy, experiment registry, missed-opportunity engine, or research-priority engine. `backtest/candidate_funnel_logger.py` can represent broader MLB candidate evidence but is not wired into production and has known dedup/storage sizing concerns.

## P. Biggest accuracy blind spots

1. No durable complete candidate universe for either sport. Missed opportunities cannot be reconstructed honestly after the fact.
2. No NFL historical sportsbook odds or line movement. Model-vs-market residual research and CLV measurement cannot yet be done.
3. No NFL play-level warehouse, participation, coaching, play-caller, injury history, or regime-change substrate.
4. No NFL spreads/totals model despite those being priority markets.
5. No full alternate-ladder normalization, hold curve, or tail calibration.
6. No NFL grading pipeline is active; prospective passing evidence cannot yet close the learn loop.
7. B0 pools residuals across QBs and contexts and models neither opportunity nor efficiency explicitly.
8. No coach portability, opponent-style interaction, offensive-line, film, or structured-news measurement.
9. MLB's measured within-market ranking AUC is 0.492 with a tight interval around chance. The candidate capture gap, rather than unsupported score tuning, remains the immediate bottleneck.
10. `main` still lacks force-push/deletion protection after the September 3 ledger incident; repository Actions detect loss but do not prevent it.
11. The root first-paint overlay fixture remains clock-sensitive and can fail unchanged source builds, weakening CI signal quality.

## Q. Exact implementation roadmap

### Phase 0 — preserve controls

- Keep NFL B0 schedules, raw evidence, snapshot seal, quarantine, and website semantics unchanged.
- Keep MLB schemas, production model, public registry, and immutable grading population unchanged.
- Require separate promotion evidence and authorization for every production model, selector, or grading change.

### Phase 1 — market coverage control plane

1. Land the source-agnostic registry and raw-payload census foundation from this branch.
2. Add a FanDuel NFL multi-event census job that consumes already archived bytes and writes a compact daily registry/report artifact.
3. Add MLB census before `MARKET_MAP` filtering.
4. Track newly discovered, disappeared, malformed, unsupported, unnormalized, no-history, and no-grader families. Suppress disappearance claims whenever capture is partial or failed.
5. Add book adapters only after preserving book-native identifiers and line ladders.

### Phase 2 — prospective candidate and learning ledgers

1. Define a compact cross-sport frozen-candidate schema with evidence identity, price, probability, eligibility, rejection reason, and model/selector versions.
2. Size storage outside git using representative full-universe captures; compare Parquet/object storage, Actions artifacts, and compact JSONL manifests.
3. Repair candidate-funnel dedup before MLB wiring.
4. Add immutable experiment, hypothesis, negative-result, and error-taxonomy schemas.
5. Build missed-opportunity analysis only from frozen pregame candidates joined to later outcomes.

### Phase 3 — multi-year NFL warehouse

1. Pin source contracts and raw digests for schedules, play-by-play, weekly stats, rosters, participation, depth charts, and injuries.
2. Build season/week/game/play identity and explicit source-vintage columns.
3. Establish availability dates for each field; refuse features whose point-in-time availability is unknown.
4. Add data-quality, duplicate, reconciliation, and season-boundary reports.
5. Produce warehouse coverage before fitting models.

### Phase 4 — spreads, totals, and market ladders

1. Normalize full-game moneyline/spread/total plus alternate ladders and team totals from archived bytes.
2. Capture every runner, handicap, price, book, event, and timestamp.
3. Validate two-sided and multi-runner hold math and adjacent-ladder consistency.
4. Build historical closing/opening snapshots only where legitimately sourced; do not backfill unavailable price vintages by guesswork.
5. Establish market-only baselines before adding football features.

### Phase 5 — coherent NFL game distribution

1. Model possessions/drives/plays and team points with a joint score-margin/total output.
2. Separate opportunity from efficiency and represent uncertainty/tails.
3. Price moneyline, spread, total, alternates, and team totals from the same simulation.
4. Evaluate sportsbook residual error, calibration, log loss, Brier, MAE, ROI, and equal-volume realized accuracy.
5. Add player distributions only after cross-market consistency checks are defined.

### Phase 6 — coach, play-caller, opponent, and regime brains

1. Build dated coach/coordinator/play-caller responsibility intervals with citations and confidence.
2. Estimate conditional tendencies by situation and test year-to-year stability.
3. Decompose coach, roster, QB, opponent, organization, and era effects.
4. Treat portability as a trained prior with held-out team-change evaluation.
5. Detect QB, OL, injury, coordinator, and tactical regime changes with evidence-based half-lives.

### Phase 7 — News Brain and Film Brain

1. News: archive first, then extract timestamped entity/claim/category/confidence/contradiction records.
2. Join claims to market state at the time and score speakers by claim type and horizon.
3. Film: resolve licensing and retrieval before ingestion; create game/play/player identity and an observation ontology.
4. Measure annotator agreement and incremental residual value beyond quantitative and market baselines.
5. Descriptive observations remain non-probabilistic unless they survive historical and prospective tests.

### Phase 8 — cross-sport continuous learning

1. Shared evidence, market, candidate, experiment, error, and learning-ledger schemas.
2. Sport-specific graders and models behind the shared control plane.
3. Daily process review of wins, losses, misses, and correct avoids.
4. Research-priority ranking from frequency, magnitude, value, fixability, and diagnosis confidence, with the scoring rule itself validated.
5. Champion stability; challenger historical OOS, season stability, leakage audit, equal-volume comparison, and prospective gates.

## R. First code and research completed

This branch now contains:

- `market_coverage/registry.py`: shared lifecycle vocabulary, deterministic coverage IDs, exhaustive FanDuel market extraction, durable merge semantics, digest provenance, explicit classifications, and fail-closed loss reporting.
- `market_coverage/cli.py`: atomic registry/report generation from archived raw FanDuel payloads.
- `market_coverage/classifications/nfl_fanduel.json`: the current passing-yards control's explicit capabilities and blockers. No other family is silently promoted.
- `data/market_coverage/registry.json`: 107 families from the bounded live census.
- `engineering/evidence/nfl_market_coverage_report_2026-09-12.json`: machine-readable gap counts.
- `test_market_coverage_registry.py`: unknown-family retention, aggregation, malformed identity, raw-digest provenance, explicit status, preservation, invalid status, and incomplete-capture loss suppression.

Initial result: 107 known/observed, 106 unnormalized, 15 unrepresented alternate families, 107 without graders. One observed passing-yards source type is `PROSPECTIVE_SHADOW`; every other observed family remains `DISCOVERED`.

## S. Work suited to later Codex delegation

No delegation is required to continue the foundation safely. Once schemas are stable, parallel bounded work would help with:

- Source/licensing matrices.
- NFL warehouse contract and data-quality audit.
- Spreads/totals market normalizer fixtures.
- Coach/play-caller historical responsibility research.
- Film and news ontology design.
- MLB coverage-registry adapter and storage sizing.

These tracks must share evidence identity and cannot independently promote production behavior.

## T. Items requiring Jacob

- No further authorization is needed for the reversible research/branch work already approved.
- Repository owner action remains needed to add a `main` ruleset that blocks force pushes and branch deletion without breaking pipeline fast-forwards.
- Jacob must approve purchases, paid data licenses, commercial agreements, new credentials/access, production merges/deployments, grading activation, public selectors/picks, model promotion, and immutable-ledger changes after each is concrete and reviewable.

Alligator

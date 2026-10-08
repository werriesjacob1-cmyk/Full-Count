# FC-NFL-003 — first NFL architecture probe

Status: exploratory research only. No selector, model promotion, official pick, production change, or prospective evidence modification.

## Exact study

The preregistered first test asks whether the existing FULL COUNT game-market B0 has *incremental* information beyond an archived market prior. The pinned nflverse/PFR `games.csv` at commit `8ed09b2fe3ea42332b2249a995737e13dd931ff3` passed its 2,177,838-byte and SHA-256 `26332ae5d8d8d0481f0670cf5e3849497a415351d4026ae5bee15a5aab96d188` gates. These are retrospective **closing** spread/total lines, with neither actionable earlier timestamp nor odds. They are suitable for an information test, not a usable-volume, expected-value, or return claim.

The source loader validates game IDs, team identity, scores, line semantics, and duplicates. Existing `scoring_prior_features` constructs rolling-five priors from explicitly final earlier games; `game_market_b0` requires three previous team games and contains no market feature. Matched eligible games: 6,906. The slope of actual-minus-close on B0-minus-close was fit **only on 2000–2019** (5,095 games), then frozen for validation 2020–2022 (796) and held 2023–2025 (816). The signed closing spread line is the home expected margin. Pushes and B0 ties to the line do not count as directional calls. Identical rows are used for all MAE comparisons, and identical nonpush/non-tie rows for B0-versus-blend direction.

| Market | Development residual slope | Held B0 MAE | Held close MAE | Held blend MAE | Held B0 correct / sides | Held blend correct / sides |
|---|---:|---:|---:|---:|---:|---:|
| Home margin / spread | -0.01778 | 10.4730 | 9.7445 | 9.7480 | 405 / 791 (51.20%) | 386 / 791 (48.80%) |
| Game total | -0.03048 | 10.7189 | 10.1207 | 10.1206 | 409 / 808 (50.62%) | 399 / 808 (49.38%) |

The small negative fitted slopes did not produce a held-out gain. Season-week block-bootstrap 95% intervals for held blend-minus-close MAE span zero: spread [-0.00215, 0.00963] and total [-0.00906, 0.00911]. The pure B0 held MAE deficits are substantial: +0.729 spread points and +0.598 total points. This rejects **this scalar B0 residual** as the first promotable architecture. The test does **not** refute market priors paired with better point-in-time personnel, role, pace, or injury information.

For any positive scalar slope, blend and B0 call exactly the same side at the same volume. Thus even a smaller forecast MAE would not establish a better cover rate. The observed negative slope simply reverses those sides; its 2020–2022 total result (407/778) did not replicate in 2023–2025 (399/808). No bet odds were in this dataset, so neither direction is an ROI result.

## Current stack and verified gaps

- Full-game B0 is a rolling prior-scoring average. Existing C1 is rejected; C2 adds prior team/defense/PBP features; C3 explores QB continuity/availability. The current game-market live path captures and seals FanDuel primary offers separately from B0, but this study neither reads nor changes its prospective seals. The repo has no validated shared game-state distribution that jointly drives score, plays, pass attempts, and player opportunity.
- Passing yards and receptions B0 use individual rolling history. A real 2025 week-8+ matched receptions opportunity evaluation in `engineering/nfl_team_opportunity_engine_20260923/team_opportunity_real_evaluation_report.json` shows B0 MAE 1.2991 against a pure team-dropbacks × target-share × catch-rate challenger at 1.4122 on 2,954 rows. Its coaching feature changed zero of those projections. This is evidence against that specific opportunity factorization, **not** evidence that opportunity is unimportant.
- A matched role/opponent report has 441 rows and a no-adjustment target-share MAE of 0.06050; the tested hierarchical, proportional, depth-chart, and recent-usage alternatives were worse. A reliable current-week replacement/role change may still matter, but this source does not establish it.
- Matchup, weather, practice, coaching comments, and line movement exist in varying collection/research stages; they are not demonstrated here as point-in-time, identity-verified, outcome-improving consumers. The relative error share of opportunity versus efficiency versus game environment **cannot be identified** from this game-level study or the existing aggregated receptions report.

## Architecture implication and next falsifiable step

Treat an authenticated, timestamped, game-matched **pregame market** as a strong external prior for game-score and script distributions, subject to actual availability and price gates. Add football evidence only if a predeclared point-in-time residual predicts the market's error across future weeks. A shared game-state distribution could then drive play volume and score-dependent pass/run rates, with player route/target/carry shares conditional on that state and per-opportunity efficiency modeled separately. This is a hypothesis, not an implemented or validated system.

The highest-information next test is a **prospective paired-residual trial** on the existing sealed FanDuel spread/total capture: hold its timestamp, threshold, two-sided price, event identity, and eligibility fixed; compare a market-only reference with a narrow, independently sourced personnel/availability plus projected play-volume residual at the same opportunities. Record offer-time information and later outcomes without modifying Week 4 seals. Then decompose passing/receptions/rushing errors into team volume, individual share, and efficiency on a matched player-game pool. Choose the next factor only after that decomposition; do not broadly add features or claim that market disagreement is edge.

Live intelligence priority is official inactive/availability and verified replacement role **before** selection time, followed by reliable weather/roof changes. Unstructured comments and inferred matchup splits require identity and timestamp validation before consumer use. Historical closing lines cannot serve as the pregame prior in this next test.

## Verification and limitations

`market_residual_report.json` is the machine-readable result, including fixed partitions, exact source contract, counts, push treatment, and 2,000-iteration season-week bootstrap intervals. Two new focused tests pass. Existing B0 and prior-scoring tests pass where no temporary files are created; the broader targeted unittest run hit Windows sandbox `PermissionError` inside `tempfile.TemporaryDirectory`, including with `TEMP/TMP/TMPDIR` redirected into the worktree. That is an observed test-environment restriction, not a test pass. No CI or independent challenge is yet claimed.

The biggest surprise is the negative development slope for both spread and total B0 disagreement with the closing market, paired with no held-out gain. A stronger independent football signal may still exist, but it is not this simple prior-scoring residual.


# FULL COUNT NFL spread / total readiness audit — 2026-09-14

Status: repository-truth audit only. No model, selector, grader, workflow, deployment, public pick, or immutable evidence changed.

## Conclusion

Spreads and full-game totals are **high-value discovered markets, not pick-ready markets**.

PR #92's audited total-sports roadmap records that FanDuel live discovery sees standard moneyline, spread, total, alternates, team totals, first-half markets, and quarter spreads. The same roadmap explicitly records the current game-market capability matrix below.

| Capability | Full-game spread | Full-game total | Evidence status |
| --- | --- | --- | --- |
| Live FanDuel feed observed | YES | YES | Bounded one-event / eight-tab census |
| Normalized canonical representation | NO | NO | 106 of 107 observed families remain discovery-only; only primary passing yards is prospective shadow |
| Historical sportsbook dataset | NO | NO | No NFL historical odds/line-movement warehouse exists |
| Point-in-time football feature warehouse | NO | NO | Weekly player substrate exists; play-by-play/participation/injury/coaching/odds warehouse does not |
| Baseline predictor | NO | NO | No operating spread/total baseline found |
| Probability / calibration method | NO | NO | No game-distribution pricing path exists |
| Prospective capture | NO | NO | No sealed game-market candidate path exists |
| Grader | NO | NO | Every observed NFL family lacks an active grader |
| Public selector | NO | NO | No NFL public selector exists |

## What exists and should be reused

1. FanDuel broad-tab discovery and market-family census already prove these families are observable.
2. The cross-sport market registry can preserve market-type identity/provenance without pretending a family is modeled.
3. Existing NFL evidence doctrine supplies the required point-in-time, source-digest, exact-identity, quarantine, and seal semantics.
4. The passing-yards scorer's two-sided de-vig math can inspire price handling, but its player residual distribution must **not** be reused as a spread/total model.
5. MLB champion/challenger and equal-volume evidence discipline should be reused conceptually, not by silently sharing sport-specific model assumptions.

## Fastest legitimate implementation sequence

### S0 — normalize game markets

Preserve every observed event ID, market ID, runner ID, handicap/total, price, capture timestamp, source URL/payload SHA, and book. Fail closed on duplicate or internally inconsistent runners. Standard and alternate lines must remain distinct.

### S1 — outcome/grading substrate

Bind schedule/game identity and final team scores. Grade spread/total only from authoritative final outcomes. Pushes must be explicit; overtime treatment follows the sportsbook market contract. This is an outcome grader, not a historical-odds backfill.

### S2 — market-only controls

Before football features, create reproducible controls:

- Spread control: market line itself as expected margin; evaluate final-margin residual distribution.
- Total control: market total itself as expected total points; evaluate final-total residual distribution.

These controls do **not** establish betting edge because historical opening/closing prices are not yet legitimately warehoused. They establish residual/error baselines and grading contracts.

### S3 — leakage-safe football baseline

Build a rolling-origin team model using facts available before kickoff. Initial feature families, in priority order:

1. prior-only team scoring and allowed-points efficiency,
2. opponent adjustment / team strength,
3. expected possessions / pace,
4. QB/start-status value,
5. offensive and defensive efficiency,
6. offensive-line and high-impact injury availability,
7. rest/travel/home field,
8. weather/wind for totals.

No current-game outcome, closing line captured after decision time, or present-day historical feature revision may leak into training rows.

### S4 — joint game distribution

Spread and total should eventually come from the same coherent score-margin/total distribution so moneyline, spread, total, alternates, and team totals cannot contradict each other mechanically.

## Promotion evidence

A challenger does not promote because MAE improves alone. Report exact eligible population, same-volume champion/challenger decisions, realized side accuracy, calibration, season stability, uncertainty, game/team clustering, line/price provenance, and leakage audit. Historical prices may only be used where the actual source/vintage exists.

## Priority relative to tonight

Do **not** delay the 2026-09-14 DEN-KC passing-yards preliminary/final capture to build spread/total. Tonight's passing path already has identity, probabilities, market de-vig, availability, timing, and prospective sealing. Spread/total lacks most of those layers.

Immediately after the final MNF capture is secure, spreads and totals become the highest-priority NFL game-market lane.

Alligator

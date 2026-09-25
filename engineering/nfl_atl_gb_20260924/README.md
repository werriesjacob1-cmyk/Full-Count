# ATL@GB `2026_03_ATL_GB` — game-day research evidence (2026-09-24)

Kickoff was 2026-09-25T00:15Z (7:15 pm CDT). This folder is research evidence only: no picks were published and no gate was weakened.

| Cycle | Receptions run | Passing-yards run | B0 sealed | Receptions B0 | Passing-yards B0 | Capture start | Join |
|---|---|---|---|---|---|---|---|
| 01, 22:50Z trigger | 36069701429 | 36069703817 | 22:52:07Z | 10 QUARANTINED (`UNKNOWN_GAME_COVERAGE`) | 2 QUARANTINED | 22:52:44Z | 0 joined / 56 |
| 02, after inactives posted | 36070067349 | 36070069819 | 22:56:15Z | 9 SHADOW_ONLY, 1 QUARANTINED (`INSUFFICIENT_HISTORY`) | 2 SHADOW_ONLY | 22:56:37Z | **9 joined** / 56, 0 bettable |

- **Code:** Codex PR #199 @ `285252d313`, run read-only via `gameday_join.py`.
- **Inactives timing:** NFL.com linked the ATL@GB inactives article at 22:55:11Z (`idx_found_*.html`). Cycle 01's capture used only Week 2 MNF reports, which is why it had no game coverage.
- **Cycle 01 join failure:** the 9 primary quotes were exact, but the matcher accepts only `SHADOW_ONLY` B0 rows, and every cycle 01 row was quarantined. Cycle 02 joined all 9 exactly: market ID, line, both odds and both selection IDs.
- **Remaining quarantine reasons, all 56 rows:**
  - `BOOK_ACTION_RULES_NOT_CERTIFIED`
  - `CURRENT_ROLE_NOT_VERIFIED`
  - `QUOTE_TIMESTAMP_NOT_PROVIDED`
  - The 47 alternate thresholds also have no B0 probability.
- **Do not read the joined "EV" values as edges.** B0 probabilities are from an unvalidated, scale-biased empirical-residual model. For example, one line shows 0.92 against a fair 0.54. That is pipeline evidence, not a signal.
- **Passing yards:** its B0 is SHADOW_ONLY. It is priced only by its own workflow shadow scorer; Codex's receptions join does not cover it.
- **Artifacts:** zip bytes were verified against GitHub's recorded digests (`summary_*.json`). The original artifacts are unmodified. Board JSONs and official inactive HTML are copied here.
- **Large external CSVs:** the pinned nflverse CSVs are excluded and listed by SHA-256 in `capture_*/EXCLUDED_EXTERNAL_CSVS.sha256`.

## Cycle 03 — final pre-lock (23:50Z trigger)
- **Receptions:** run 36074758237 at code `11c038aadb`, sealed 23:51:55Z, snapshot `fc53cbeef986ffa0…`. 9 SHADOW_ONLY, 1 QUARANTINED (`INSUFFICIENT_HISTORY`).
- **Passing yards:** run 36074760211 **failed, fail-closed, with no artifact.** FanDuel returned HTTP 403 in all 4 regions to the Actions runner at 23:51:57Z (`py_fail_36074760211.log`). The cycle-02 seal (22:56:21Z, 2 SHADOW_ONLY) remains the pregame passing-yards evidence.
- **Capture:** `capture_03` started 23:52:07Z, 23 min before kickoff; snapshot `7eb4df50d1f62e11…`.
- **Join:**
  - `integration_03.json` was joined by mistake against the cycle-02 B0 (22:56:15Z). The runner's glob took the first run directory. It is still a valid prior-seal join, but it is not the pre-lock seal. The runner is fixed to select the B0 by run ID.
  - `integration_03b.json` is the correct join against the pre-lock B0 (23:51:55Z): **9 joined, 0 bettable**, integration seal `b2509c7b16229b62…`.
- **Quote stability:** none of the 9 primary quotes changed between 22:56Z and 23:52Z.
- **Gates still blocking eligibility:** rules certification, current role, and quote-origin timestamp.

## Postgame (04:00Z trigger)
- **Final status:** ESPN event 401872948 is `STATUS_FINAL`. Score: ATL 35, GB 14. Snapshot `espn_scoreboard_20260924_postgame.json` fetched at 04:02Z.
- **Player outcomes are PENDING.** The repo's grading path (`box_score_outcomes` → `receptions_paired_grader` / `player_prop_grader`) reads nflverse `stats_player_week_2026.csv`. That file was last modified 2026-09-24T14:13:56Z and has 0 rows for `2026_03_ATL_GB`.
- **Nothing is graded from any other source.**
- **What will be graded once outcomes exist:** the frozen research predictions, meaning the receptions SHADOW_ONLY B0 plus challengers, and the passing-yards SHADOW_ONLY B0.
- **What is not graded:** eligible selections. There were 0 bettable offers.

## Grading (15:00Z 2026-09-25 trigger)

**Outcome source.** The source is the nflverse `stats_player_week_2026.csv` release from 2026-09-25 14:37:31Z. Its SHA-256 is `315cfb8d…3b2b` and it has 69 rows for `2026_03_ATL_GB`. The full pin is in `outcomes_source.json`; the CSV itself is not committed. Grading used the repo's unmodified path: `box_score_outcomes.build_player_outcomes`, `receptions_paired_grader`, and `player_prop_grader.grade_player_prop_market`. Results are in `grading_20260925.json`.

**What was graded.** Only research predictions frozen before kickoff were graded, and none of them was an eligible selection: there were 0 bettable offers. Nothing is P&L. Nothing is published.

### Receptions
These are the 9 SHADOW_ONLY paired records from the pre-lock seal (run 36074758237). The source comparison file is now copied into `run_36074758237_*/`.

| Player | Line | Actual | B0 P(over) | NB challenger P(over) |
|---|---|---|---|---|
| Christian Watson | 4.5 | 7 | 0.23 | 0.27 |
| Matthew Golden | 4.5 | 5 | 0.08 | 0.05 |
| Drake London | 5.5 | 9 | 0.20 | 0.27 |
| Skyy Moore | 1.5 | 3 | 0.20 | 0.02 |
| Austin Hooper | 0.5 | 2 | 0.63 | 0.68 |
| Jonnu Smith | 1.5 | 2 | 0.59 | 0.57 |
| Olamide Zaccheaus | 1.5 | 2 | 0.38 | 0.33 |
| Chris Brooks | 1.5 | 1 | 0.27 | 0.13 |
| Jahan Dotson | 1.5 | 1 | 0.43 | 0.40 |

**Mean scores:**

| Model | Brier | Log loss |
|---|---|---|
| B0 | 0.4055 | 1.1128 |
| Challenger | 0.4276 | 1.3696 |

**Better Brier, record by record:** B0 on 4 records, challenger on 5.

**Direction.** 7 of the 9 went OVER, while B0 had P(over) < 0.5 on 7 of the 9. That is consistent with the known under-projection bias of the scale. It is one game with n=9, so it is anecdotal: no conclusion is drawn.

### Passing yards
These are the 2 SHADOW_ONLY rows from the cycle-02 seal. The direction shown is the research direction only; neither was a bet.

| Player | Line | B0 projection | Direction | Actual | Result |
|---|---|---|---|---|---|
| Jordan Love | 231.5 | 228.8 | UNDER | 312 | MISS |
| Michael Penix Jr. | 204.5 | 212.8 | OVER | 256 | HIT |

### Tier 1 prospective protocol: integrity check only
See `tier1_week3_seal_integrity.json`.

**Seal checks.** All 3 week-3 seal files re-hash to their sealed SHA-256, and all 3 were committed between 21:23Z and 21:28Z, before the 00:15Z kickoff.

**Not scored.** No Tier 1 accuracy was computed. The earlier self-scheduled trigger said to score week 3 "descriptively", but that contradicts the frozen protocol:
- Section 5 allows no interim looks at outcomes.
- Section 3 sets the outcome source as the first nflverse release *after* Monday night. This file predates Monday night.

The protocol wins.

**Gap found.** The seals do not store the exact primary H1/H2/H3 predictions:
- F3-only is not stored.
- F6-alone is not stored.
- volume_only is not stored.

They are expected to be recomputable from the sealed features and frozen code. Future Thursday/Saturday seals must store them directly.

### Capability classification after grading
- **Receptions:** PARTIAL. Capture, B0 seal, exact join and grading are all exercised end to end. Eligibility stays blocked by three gates: `BOOK_ACTION_RULES_NOT_CERTIFIED`, `CURRENT_ROLE_NOT_VERIFIED` and `QUOTE_TIMESTAMP_NOT_PROVIDED`.
- **Passing yards:** PARTIAL. B0 seal and grading are exercised. There is no Codex join path, and the pre-lock run failed on a FanDuel 403.
- **Authentic pricing:** PARTIAL. Offers were captured and joined exactly, but 0 were eligible because of the same three gates.

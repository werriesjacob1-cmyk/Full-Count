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

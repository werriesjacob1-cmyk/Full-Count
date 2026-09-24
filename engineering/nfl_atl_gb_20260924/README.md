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

# NFL Tier 1 intelligence: consolidated status (2026-09-24)

This file holds the coordinator's verdicts after the independent adversarial review. The per-factor evidence is in `status.json`. The workstream reports live on their own branches:

| WS | Branch @ head | Factors |
|---|---|---|
| B | `claude/nfl-tier1-player-opportunity-20260924` @ `8aa8067fbc` | F2, F3, F8, F9 |
| C | `claude/nfl-tier1-team-context-20260924` @ `40d842c9a9` | F1, F5, F6, F7, F10 |
| D | `claude/nfl-tier1-touchdown-20260924` @ `76b42547c3` | F4 |

The foundation is `claude/nfl-tier1-foundation-20260924`. It holds `nfl/research/tier1/contract.py` and `harness.py`. Its binary-market B0 was fixed after the review; see below.

| Factor | Status | One line |
|---|---|---|
| F1 game lines | BUILT | Historical lines are closing lines, so they cannot be credited pregame. Live FanDuel lines were captured. |
| F2 snap share / role | BUILT | Small historical gain, worse on 2026. One sharp scenario: backup RB with the top RB out. |
| F3 target share | VALIDATED* (narrowed) | Target share × team targets, with catch rate and yards per target shrunk toward the average. aDOT, WOPR and air-yards share add nothing. |
| F4 red zone | VALIDATED* (narrow) | −0.0017 log loss vs the volume-only control. That comparison is the only informative one. |
| F5 PROE / pace | REJECTED | No value beyond team volume. |
| F6 environment | VALIDATED* (receiving yards only) | Passing yards were downgraded to BUILT because they fail the multiple-testing check. Forecasts are GFS MOS, issued before kickoff. |
| F7 rest / travel | REJECTED | No incremental value. |
| F8 injury / practice | PARTIAL | Rejected as a workload adjustment. Use as an availability forecast was never tested. |
| F9 absence redistribution | BUILT | Receptions improve; rushing is worse on 2026. |
| F10 opponent by position | BUILT | Allowed-by-position only, not WR-vs-CB. |

\* **VALIDATED** means historically supported on the 2023–2025 holdout, which earlier experiments had already inspected. It is not prospective validation, and no factor is LIVE_RESEARCH. Promotion into authoritative B0 needs prospective evidence and Jacob's approval.

## Review findings carried forward
- **Status rules came after the holdout run.** Each workstream wrote its VALIDATED/BUILT rule after its holdout run; the consumers and parameters were frozen before it. Next time, freeze the decision rule together with the parameters.
- **Workstreams used different rules.** C also required the DEV CI to be below 0; B and D did not. A single rule should be set in `contract.py` before the next evaluation.
- **The binary-market B0 was fixed.** It used to be exactly 0 on about 36% of anytime_td rows, which let any nonzero model "beat" it by about 0.6 log loss. It is now a smoothed frequency `(TDs + 0.5) / (appearances + 1)`, so it is never 0. The count markets are unchanged, and the receptions drift guard still reproduces.
- **F4's comparison uses two populations.** Its volume_only comparison includes rows with no B0 history. FRESH has n=576 there versus 544 in the scale comparison.
- **F6 fallback logging.** F6 UNKNOWN fallbacks are logged as `TEAM_VOLUME_UNKNOWN`. This is cosmetic and does not inflate the result.

# FC-MLB-002 — minimum repair of Codex findings 1–4 (Jacob-authorized)

**Scope.** The audited head was `cc3ff960cc`. Its evidence is kept unchanged in `out/superseded_cc3ff960cc/`.
- The same frozen 2025 fit is used: `f48f87dea7`, sha256 `6c0ecde2…`, not refit.
- The same CH0 definition, coefficients, shrinkage, feature families, starter/bullpen formula, 0.5 league shrink, K = 5/10/20, primary K = 10, verdict rule and date-block bootstrap.
- The frozen 2026 evaluation was run **once** after the repair.
- **DEVELOPMENT evidence only.**

| # | Defect | Repair | Effect |
|---|---|---|---|
| 1 | `batter_games()` grouped by `stand`. A switch hitter's player-game split into side fragments carrying partial-game labels, and the same player-game could be selected twice | Settlement identity is now `(game_pk, batter)`: hits and TB are summed over every PA, and a duplicate raises. Top-K selection refuses duplicate player-games | 2026 population 39,298 fragment rows → **37,125 player-games**. The audited rule split 2,278 player-games, 1,158 of them with conflicting H1+ labels |
| 1b | Matchup side came from realized PAs | `assign_matchup_side()` (see below) uses pregame-knowable information only | 4,035 switch-hitter player-games; 0 excluded for undeterminable side |
| 2 | Market subset: player-days with split rows and doubleheaders were dropped silently; matching was by name + date | **Event identity**: FanDuel `event_id` → `game_pk` via the statsapi schedule (away/home names and \|start − official first pitch\| ≤ 90 min), then player name **within that game's** eligible player-games. Ambiguous and unmatched rows are counted, never guessed. Price is the last snapshot strictly before min(FanDuel start, official first pitch). The one-sided assumed-hold de-vig is kept and labelled APPROXIMATE | 703/709 events matched (1 ambiguous, 5 unmatched). 12,044 matched player-games (was 9,554), including 228 doubleheader player-games (previously dropped). 0 doubleheader exclusions |
| 3 | `picks_each` reported K × dates | Selection is `min(K, eligible)` per date; actual selected rows and per-date shortfalls are reported | K=20: 3,258 selected (2026-07-16 short by 2). K=5 and K=10: no shortfall |
| 4 | Raw CSVs were read without checking them against the manifest | `verify_raw_files()` checks every cached file against the **committed** `STATCAST_MANIFEST.jsonl` (decompressed byte count plus sha256 of the exact Savant response bytes). The verified bytes are what gets parsed. Unlisted, missing, FAIL or conflicting entries fail closed. `PA_PROVENANCE.json` binds the PA/pitch parquet hashes; the evaluation refuses tables not built from verified bytes | 385/385 raw files verified. **Limit:** the manifest proves byte identity with what was fetched on 2026-10-02, not Savant's own versioning |

**Switch-hitter representation (pregame, outcome-independent):**
- **Side source:** MLBAM biographical `batSide` (statsapi `/people`).
  - Non-switch hitters bat their own side.
  - A switch hitter bats opposite the starter's throwing hand (the starter's hand is the pitcher's modal `p_throws`). Against a switch hitter, `same_hand` is 0.
- **Bullpen term for switch hitters:** a mixture over reliever hand. The weight is the league RHP share of relief PAs, season-to-date as of D−1:
  - against a RHP he bats L, so stand L features are used;
  - against a LHP he bats R, so stand R features are used.
- **Non-switch bullpen term:** unchanged (league same-hand relief share as of D−1).
- **Not used:** realized PA-side frequencies, in-game pitcher changes, or outcomes.

**Result change:** the verdict is unchanged, still **INCONCLUSIVE**.
- At K=10, CH1 − CH0 moved from +2.33 [−0.19, +4.91] to **+2.27 [−0.12, +4.66]**.
- CH1a − CH0 at K=10 is +2.58 [+0.12, +5.09]. This is an ablation diagnostic only.
- CH1 − CH1a is about 0 at every K.
- The details are in `out/RESULTS.json` and `out/REPORT.md`.

**Tests:** `test_fc_mlb_002_repair.py`, 10 tests on synthetic fixtures (settlement, switch side, K counts, market identity, manifest). The 2025 in-sample self-check exercised the mechanics. No iterative 2026 runs.

Alligator.

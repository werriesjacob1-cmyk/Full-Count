# NFL Tier 1: frozen prospective evaluation protocol (v1)

This protocol is committed before the first prospective game is played: ATL@GB `2026_03_ATL_GB`, kickoff 2026-09-25T00:15Z. Commit order is the proof. It covers research shadow evaluation only. It never changes authoritative B0, tonight's frozen predictions, selectors or public picks. Promotion always requires Jacob's explicit authorization.

## Why a new protocol
Two problems with the historical evaluation make a fresh protocol necessary:
- **The holdout was already seen.** The Tier 1 results (see `README.md` / `status.json`) come from the 2023–2025 holdout, which earlier experiments had already inspected.
- **The acceptance rules came late.** They were written after each workstream's holdout run (review finding 3).

This protocol fixes the versions, hypotheses, population, metrics, multiplicity handling and decision rules before any prospective outcome exists.

## 1. Frozen versions
| Role | Model | Code | Frozen parameters (SHA-256 of the file at that commit) |
|---|---|---|---|
| Champion | Authoritative B0: mean of the last 5 role appearances, minimum 3 (the live workflow rule). Anytime-TD B0 is the smoothed frequency from `harness.b0_rolling_mean`. | main live workflows; `nfl/research/tier1/harness.py` @ `c128fc6b60` | n/a |
| C-F3 | `player_opportunity_challenger`, **F3 only** (target share × team targets, shrunk rates) | `8aa8067fbc`, `player_opportunity_challenger.py` sha `1b7d8d45…dadbe9` | `frozen_params.json` sha `e7757514…3dd5dd` |
| C-F6 | `team_context_challenger`, **F6 alone** over VOLUME_BASE | `40d842c9a9`, `team_context_challenger.py` sha `77e85300…5840c` | `team_context_dev_params.json` sha `34c803fb…049f` |
| C-F4 | `touchdown_opportunity_challenger`, `rz_blend` (red-zone weight 0.20, scale 0.98) | `76b42547c3`, `touchdown_consumer.py` sha `d14cc1c7…a81bd` (parameters are in the file) | same file |

Controls are frozen alongside the challengers. Each challenger's own DEV-fitted scale-only control `k·B0` is always reported. In addition:
- C-F6 is also compared against **VOLUME_BASE**.
- C-F4 is also compared against **volume_only**.

No parameter may be refitted during the evaluation window.

## 2. Hypotheses (primary family, one-sided: challenger better)
| ID | Challenger | Market | Comparator | Metric |
|---|---|---|---|---|
| H1 | C-F3 | receiving_yards | its scale control | paired MAE difference |
| H2 | C-F6 | receiving_yards | VOLUME_BASE | paired MAE difference |
| H3 | C-F4 | anytime_td (rushing + receiving TD) | volume_only | paired log-loss difference |

**Multiplicity.** Holm–Bonferroni across H1–H3 at family-wise α = 0.05, one-sided. The p-values come from the game-clustered bootstrap (B = 2000, seed 20260925), as the proportion of resampled mean differences ≥ 0.

**Secondary and exploratory results.** These are reported but carry no claim:
- C-F3 on receptions;
- C-F6 on passing_yards (downgraded);
- F1 using live FanDuel lines, which are market input;
- F2, F9, F10;
- ALL_NO_MARKET and ALL.

## 3. Population and information cutoff
- **Games:** 2026 regular-season weeks 3 through 8, starting with ATL@GB.
- **Rows:** player-games in the harness population. That means role-positive for the market, with B0 present. Anytime-TD uses the touch-conditional population, as in WS-D.
- **Weekly seal.** Every prediction for a game is sealed before that game's kickoff: computed, hashed and committed or archived. Each seal records the source files it used, with their SHA-256 and `information_cutoff`. Any row whose prediction was not sealed before kickoff is **excluded**, never back-filled. Seals happen twice a week:
  - **Thursday** before the Thursday night kickoff: all factors for all games of the week.
  - **Saturday after 18:00Z:** F6 for Sunday and Monday games, using the pre-declared 12Z day-before MOS run. F8/F9 are also updated from the final injury reports then.
- **Week 3:** only ATL@GB can be sealed before its kickoff tonight. The other week-3 games are sealed at the Saturday seal. Anything not sealed before kickoff is excluded.
- **Outcomes:** official nflverse weekly stats for the target week, taken as the first release after that week's Monday night game. They are hashed. Unknown or incomplete outcomes stay PENDING and are never imputed.

## 4. Metrics and uncertainty
- For every comparison: paired per-row differences on identical matched rows, the game-clustered bootstrap confidence interval and p-value, activation share, fallback counts and bias.
- Anytime-TD: log loss is primary; Brier score and calibration by decile are secondary.
- **Equal-volume selection comparison.** Reported only when legitimate priced and eligible offers exist, which requires the Codex PR #196/#199 gates to pass. In that case:
  - champion and challenger each take the same number of top-edge eligible offers per slate;
  - report hit rate, units won or lost at the captured price, the overlap between the two pick sets, and outcomes for picks added versus removed.

  If no eligible offers exist, the comparison is reported as **NOT AVAILABLE**. It is never simulated.

## 5. Single analysis point and decision rules
- **One analysis** after week 8 (last game on Monday, 2026-11-02) is final and graded. There are no interim looks at outcomes. Integrity checks on seals and source hashes during the window are allowed.
- A hypothesis is **SUPPORTED PROSPECTIVELY** when it passes Holm, has at least 1,500 matched rows (anytime-TD: at least 3,000), and its secondary market is not significantly worse (the one-sided test at 0.05 in the other direction does not reject).
- A hypothesis is **REJECTED** when the point estimate is ≥ 0, meaning no improvement.
- Anything else is **INCONCLUSIVE**. That includes falling below the minimum row count.
- **Promotion candidate** (Jacob decides; it is never automatic) requires both:
  - SUPPORTED PROSPECTIVELY;
  - either an equal-volume selection result that is not worse, or an explicit statement that no priced comparison was possible.

## 6. Known limitations, stated in advance
- The effect sizes seen on the holdout may be optimistic, because that holdout was previously inspected.
- The Saturday seal depends on the MOS run being published. If the run is missing, the F6 rows fall back to VOLUME_BASE and are counted as fallbacks.
- The anytime-TD population is conditioned on the player getting a touch. FanDuel settles bets for anyone who plays at least one snap. So H3 is a forecast-quality test, not a pricing test.
- Weeks 3 to 8 are one short regime.

# Protocol v1 seal builder

`build_week_seal.py` implements the weekly seal in `PROSPECTIVE_PROTOCOL.md` §3. It corrects the gap found in the ATL@GB seal (`engineering/nfl_atl_gb_20260924/tier1_week3_seal_integrity.json` on the evidence branch). Those Thursday files are **not** altered or back-filled.

## What each seal stores directly
The builder writes `primary_predictions.json`, one row per player-game. Each row holds the challenger prediction and its comparator:

| Hypothesis | Challenger | Comparator |
|---|---|---|
| H1 | C-F3 F3-only (`w·f3_estimate + (1−w)·k·B0`, the frozen `player_opportunity_challenger.predict` "F3" ablation) | scale control `k·B0` |
| H2 | C-F6 F6-alone (frozen config `F6`, exponents already in `team_context_dev_params.json`) | VOLUME_BASE |
| H3 | C-F4 `rz_blend` | `volume_only`, both from the frozen `touchdown_consumer` |

Each row also records:
- the authoritative-rule B0 (champion harness `c128fc6b60`, including smoothed anytime-TD);
- the workstream's own B0, plus a match flag;
- game ID, GSIS ID, team, kickoff and fallback reasons;
- the exploratory configs.

`seal.json` records:
- the full frozen commit SHAs;
- pinned input hashes;
- the injury refresh (upstream Last-Modified and SHA-256);
- per-driver source hashes, information cutoffs and parameter hashes;
- the SHA-256 of every output file.

## How it runs
- **Frozen code, extracted fresh.** Every driver runs against a fresh `git archive` extraction of its frozen commit, never the working tree.
- **No refits, no outcomes.** Nothing is refit and no outcome is read.
- **Future games only.** Only games whose kickoff is more than 45 minutes away are sealed.
- **Pinned pre-week inputs.** Weekly stats and 2026 play-by-play stay pinned to the pre-week files. A later file would contain target-week rows, and the WS-C builder refuses those.

## Known, recorded differences
- **WS-C B0 differs on some rows.** WS-C's frozen live builder loads history from `season − 1` only, so its B0 differs from the authoritative rule on some rows (15 in the Friday dry run). H2 pairs are still internally consistent, because challenger and comparator share that B0. The analysis restricts rows to the protocol population, where the authoritative B0 is present.
- **H3 fallback.** When a `rz_blend` or `volume_only` lambda has UNKNOWN inputs, the stored prediction falls back to the authoritative-rule B0, with the reason `FALLBACK_B0 (...)`. This mirrors the frozen `touchdown_consumer.predict`, which falls back to B0. The one difference: that frozen code's harness B0 is unsmoothed, while this fallback uses the smoothed champion rule. The Friday dry run had 0 such rows. This was found by independent review.
- **F6 needs the 12Z MOS run.** F6 requires the 12Z day-before MOS run. Before it is issued, rows fall back with `TEAM_VOLUME_UNKNOWN`, which the protocol counts as a fallback. Monday games cannot have it at the Saturday seal.

## Command (Saturday, after 18:00Z)
```
PYTHONPATH=. python3 engineering/nfl_tier1_status_20260924/seal/build_week_seal.py \
    --week 3 --label sun_mon --exclude 2026_03_ATL_GB --refresh-injuries
```
Then commit the output directory and push before the first kickoff.

## Per-week pinned inputs (week 4 onward; authorized by Jacob 2026-10-01)
- `PINNED_BY_WEEK` pins each target week's current-season files: the first nflverse release after the previous week's Monday game, with every row through week−1 and none from the target week.
  - Week 3's pins are unchanged.
  - Week 4 pins:

    | File | SHA-256 | Upstream Last-Modified |
    |---|---|---|
    | `stats_player_week_2026.csv` | `e293e213…` | 2026-09-30 16:25:46Z |
    | `play_by_play_2026.csv.gz` | `321433f8…` | 16:15:15Z |
    | `snap_counts_2026.csv` | `c5868527…` | 2026-09-29 11:01:26Z |

    Each was verified to contain weeks 1–3 (all 16 week-3 games) and no week-4 rows.
  - `schedules/games.csv` stays `7fdc123e…`. Its week-4 kickoffs equal current upstream.
  - `players.csv` stays `4dd70f32…`.
- **Staging.** Copy the week's files into `/tmp/claude-0/nfl_tier1_shared` before building. The builder refuses to build if any pin mismatches.
- **WS-D cutoff.** The WS-D rows carry that week's true information cutoff (`D_INFORMATION_CUTOFF`). Week 3 keeps the frozen constant.
- **WS-C cache.** WS-C builds a missing PBP summary cache with the frozen `summarize_pbp`.
- **Unchanged.** No frozen model code, parameter, hypothesis or B0 rule changes.
- **Seal precedence when a game has more than one seal.** A Thursday all-games seal plus a Saturday Sunday/Monday seal is the protocol §3 pattern. The analysis uses, for each game, the latest seal committed before that game's kickoff. Every seal is kept.

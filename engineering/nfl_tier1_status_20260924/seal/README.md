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
- **F6 needs the 12Z MOS run.** F6 requires the 12Z day-before MOS run. Before it is issued, rows fall back with `TEAM_VOLUME_UNKNOWN`, which the protocol counts as a fallback. Monday games cannot have it at the Saturday seal.

## Command (Saturday, after 18:00Z)
```
PYTHONPATH=. python3 engineering/nfl_tier1_status_20260924/seal/build_week_seal.py \
    --week 3 --label sun_mon --exclude 2026_03_ATL_GB --refresh-injuries
```
Then commit the output directory and push before the first kickoff.

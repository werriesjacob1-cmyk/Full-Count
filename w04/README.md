# Week-4 recovery overlay for the Tier 1 protocol-v1 seal

This branch is the week-3 recovery bundle (`claude/nfl-seal-inputs-20260925` @ `4b7fd4babe`, unchanged) plus this `w04/` overlay.

**What the overlay pins:**
- The week-4 current-season inputs from `PINNED_BY_WEEK[4]` on `claude/nfl-tier1-seal-2026w04`. Each holds weeks 1–3 and no week-4 rows:

  | File | SHA-256 | Upstream Last-Modified |
  |---|---|---|
  | stats | `e293e213…` | 2026-09-30 16:25:46Z |
  | pbp | `321433f8…` | 16:15:15Z |
  | snaps | `c5868527…` | 09-29 11:01:26Z |

- `players.csv` (`4dd70f32…`), the copy pinned since week 3.
  - Upstream changed it after 2026-09-25, to `5b6f22c2…` as of 2026-10-01.
  - So the week-3 `restore.py` alone now fails on a fresh container.
  - On 2026-10-01 all 49 other upstream-pinned files still matched byte for byte.

**Restore** (fresh or intact container):
```
git clone --depth 1 --branch claude/nfl-seal-inputs-2026w04 https://github.com/werriesjacob1-cmyk/Full-Count /tmp/claude-0/seal_inputs4
/tmp/claude-0/seal_inputs4/w04/recover_w04.sh
```
- It prints `W04 ENVIRONMENT OK` only if every pin matches.
- It was exercised on 2026-10-01 at about 05:30Z: restore `ok: true` (1,840 bundled files OK, 50 present OK), then all three worktrees verified (tier1, cov, tier1w4).
- The Saturday command then dry-ran cleanly: 15 games, with rows H1 320, H2 283 and H3 387.

**Saturday Sunday/Monday seal** (after 18:00Z, so that the 12Z day-before MOS run exists):
```
cd /tmp/claude-0/full-count-worktrees/tier1w4
PYTHONPATH=. python3 engineering/nfl_tier1_status_20260924/seal/build_week_seal.py --week 4 --label sun_mon --exclude 2026_04_PIT_CLE --refresh-injuries
```

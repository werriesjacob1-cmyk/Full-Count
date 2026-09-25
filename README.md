# Disaster-recovery bundle for the week-3 Tier 1 protocol seal (Sunday/Monday games)

This bundle holds the **pre-week-3** inputs the seal builder pins. It lives on
`claude/nfl-tier1-foundation-20260924`, under `engineering/nfl_tier1_status_20260924/seal/build_week_seal.py`, `PINNED`.
Upstream nflverse has already replaced these files with versions that include week-3 rows. The frozen WS-C builder refuses those files, so a reclaimed container could not rebuild the seal without this bundle.

**Contents** (strace-verified complete input set, expanded 2026-09-25 16:40Z):
- **`pinned_inputs.tar.gz`**, under `tmp/claude-0/`, containing 1,840 bundled files:
  - `stats_player_week_2026.csv` (`736bddde…`), `play_by_play_2026.csv.gz` (`6643f82a…`), `snap_counts_2026.csv`;
  - `schedules/games.csv` (`7fdc123e…`). Upstream `games.csv` has already changed, from both nflverse-data and nfldata, so it is bundled.
  - the WS-C play-by-play summary cache, the stadium table and the MOS cache.
- **`MANIFEST.json`** lists every input, its SHA-256 and its source:
  - bundled files;
  - 50 upstream nflverse files, each verified byte-identical to the pregame copies on 2026-09-25: play-by-play 2016–2025, weekly stats 2015–2025, snap counts and injuries 2016–2025, participation 2018–2025, and `players.csv`;
  - the unpinned `injuries_2026.csv`, which the builder refreshes;
  - the required Python packages.
- **`restore.py`** restores and verifies everything, and fails closed.

**Restore:**
```
python3 restore.py --root /
```
It exits 0 only when every pinned file matches.

**Guarantees:** a mismatching upstream file is never accepted; it is renamed `*.MISMATCH`. Derived coverage caches are rebuilt from the verified inputs.

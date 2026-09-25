# Disaster-recovery bundle for the week-3 Tier 1 protocol seal (Sunday/Monday games)

This bundle holds the **pre-week-3** inputs the seal builder pins. It lives on
`claude/nfl-tier1-foundation-20260924`, under `engineering/nfl_tier1_status_20260924/seal/build_week_seal.py`, `PINNED`.
Upstream nflverse has already replaced these files with versions that include week-3 rows. The frozen WS-C builder refuses those files, so a reclaimed container could not rebuild the seal without this bundle.

**Contents:** `pinned_inputs.tar.gz`, with absolute paths under `tmp/claude-0/`:
- `stats_player_week_2026.csv` (sha `736bddde…`);
- `play_by_play_2026.csv.gz` (sha `6643f82a…`);
- `snap_counts_2026.csv`;
- the WS-C derived play-by-play summary cache and stadium table.

**Restore:**
```
tar -xzf pinned_inputs.tar.gz -C /
```
Then verify against `SHA256SUMS`. Everything else the builder needs can be re-downloaded from nflverse, and a changed hash for any historical file must be recorded in the seal: pbp 2016–2025, weekly stats 2015–2025, injuries, games.csv and players.csv.

- **Data:** nflverse (nflfastR play-by-play, player stats, PFR snap counts). CC-BY 4.0 / per nflverse terms; attribution nflverse.
- **Scope:** research recovery only, never merged. Captured 2026-09-25 from files retrieved 2026-09-24 20:59–21:00Z.

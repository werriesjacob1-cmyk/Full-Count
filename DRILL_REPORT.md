# FULL COUNT fresh-container drill 2 — Week 4 Tier 1 (dry run, NO seal)

- Drill date: 2026-10-01 (UTC)
- Checkout: `claude/nfl-seal-inputs-2026w04` @ 4461b04; report branch: `claude/nfl-seal-drill-2026w04`
- No outcomes were read. No GitHub issues or PRs were posted to. TLS verification and HTTPS_PROXY were left as they were.
- No permission prompts or denials came up during the drill.

## Step 1: `bash w04/recover_w04.sh`

### Attempt 1: FAILED (exit 1)
- Started 2026-10-01T06:13:59Z, ended 2026-10-01T06:14:22Z
- The fresh container lacks numpy and pandas. `restore.py` fails closed, so `set -e` aborts the script before any clone runs.
- Tail:
```
snap_counts_2026.csv: OK
players.csv: OK
pinned_inputs.tar.gz: OK
MANIFEST.json: OK
restore.py: OK
recover_environment.sh: OK
{
 "ok": false,
 "counts": {
  "BUNDLE_OK": 1840,
  "DOWNLOADED_OK": 49,
  "PRESENT_OK": 1,
  "UNPINNED_PRESENT": 1
 },
 "failures": [
  "python packages missing: ['numpy', 'pandas']; pip install numpy pandas"
 ]
}
```

### Fix applied
- 2026-10-01T06:14:30Z to 06:14:38Z: `pip install numpy==2.4.6 pandas==3.0.5`, the versions pinned in MANIFEST.json `python_requirements`. requests 2.34.2 was already installed.

### Attempt 2: OK (exit 0)
- Started 2026-10-01T06:14:38Z, ended 2026-10-01T06:14:51Z
- Tail:
```
stats_player_week_2026.csv: OK
play_by_play_2026.csv.gz: OK
snap_counts_2026.csv: OK
players.csv: OK
pinned_inputs.tar.gz: OK
MANIFEST.json: OK
restore.py: OK
recover_environment.sh: OK
{
 "ok": true,
 "counts": {
  "BUNDLE_OK": 1840,
  "PRESENT_OK": 50,
  "UNPINNED_PRESENT": 1
 },
 "failures": []
}
tier1 OK at e05e02c592
cov OK at f4fb17aa0b
tier1w4 OK at 693526aa
W04 ENVIRONMENT OK
```

## Step 2: dry-run builder (from /tmp/claude-0/full-count-worktrees/tier1w4)
- Started 2026-10-01T06:15:04Z, ended 2026-10-01T06:17:14Z, exit 0
- Command: `PYTHONPATH=. python3 engineering/nfl_tier1_status_20260924/seal/build_week_seal.py --week 4 --label sun_mon --exclude 2026_04_PIT_CLE --dry-run --no-capture`
```json
{
 "out": "/tmp/claude-0/seal_dry/2026_w04_sun_mon",
 "games": 15,
 "row_counts": {
  "H1_SECONDARY_receptions": 320,
  "H1": 320,
  "H2_SECONDARY_passing_yards": 38,
  "H2_SECONDARY_receptions": 283,
  "H2": 283,
  "H3": 387
 },
 "b0_mismatches": 26
}
```
- Expected 15 games, H1 320, H2 283, H3 387. **All four MATCH.**
- The output also reports `b0_mismatches: 26`, which the drill brief did not give an expected value for. It counts primary rows whose b0 differs from the B0 authoritative rule. Compare it with the Week 3 run before Saturday.

## Step 3: GitHub API reachability (issues/91 via urllib)
- Started 2026-10-01T06:17:14Z, ended 2026-10-01T06:17:15Z, exit 0
- Output: `91`. **It printed 91.**

## Action needed before Saturday
The scheduled run must install the Python packages before running `recover_w04.sh`. Add `pip install numpy==2.4.6 pandas==3.0.5 requests==2.34.2` to the trigger prompt or the environment setup script. Without it, a fresh container fails at step 1.

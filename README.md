# PROJECT-GRIDIRON — MLB Daily Betting Pipeline

Automated data-generation half of Jacob's MLB player-prop research pipeline.
A GitHub Actions workflow runs `mlb_daily.py` every morning, pulls lineups,
weather, injuries, umpire assignments, and ~88 sections of FanGraphs/Statcast/
MLB Stats API data, and commits the result to this repo as a single text file
Jacob pastes into his Claude.ai `mlb-betting-analyst` project.

This repo owns **data generation only**. It does not touch the betting-analyst
skill (versioned separately by Jacob), does not scrape closing lines, and does
not place or size bets.

## How it runs

- **Scheduled**: every day at 10:30 AM ET (`.github/workflows/mlb-daily.yml`,
  cron `30 14 * * *` = 14:30 UTC). This is pinned to EDT (UTC-4). MLB's regular
  season runs entirely within U.S. daylight saving time, so this is accurate
  for every in-season day. If you ever manually re-run this in the off-season
  (outside DST), the run will fire an hour earlier than 10:30 local — not
  worth the YAML complexity of resolving DST dynamically for a non-issue.
- **Manual**: Actions tab → "MLB Daily Pipeline" → **Run workflow**. Check the
  "Dry run" box to validate the pipeline (lineups, injuries, weather, umpires
  only, ~1 minute) without waiting on the full ~15-20 minute run or committing
  anything. Use this from GitHub mobile if the scheduled run fails or lineups
  shift after 10:30.
- **Output**: `output/mlb_daily_YYYY-MM-DD.txt` — open it in the GitHub app or
  browser, select all, copy, paste into the Claude.ai betting session exactly
  like before. `output/run_log_YYYY-MM-DD.json` ships alongside it — see
  "Run log" below.

No secrets are required. Every data source is free/public.

## Repo layout

```
mlb-daily-pipeline/
├── .github/workflows/mlb-daily.yml   # schedule + manual trigger
├── mlb_daily.py                      # the pipeline (single file, ~88 sections)
├── requirements.txt                  # pinned dependency versions
├── README.md
└── output/                           # generated .txt + run_log .json land here
```

## Run log

Every run writes `output/run_log_YYYY-MM-DD.json` and prepends a run summary
to the top of the `.txt` file itself, so you (and Claude, reading the pasted
file) can see at a glance which of the ~88 sections came back empty or failed
without reading the whole document. Example:

```json
{
  "total_sections": 88,
  "ok": 81,
  "empty": 6,
  "failed": 1,
  "sections": [{"section": 6, "title": "HP UMPIRE CAREER STATS...", "status": "ok"}, ...]
}
```

`empty` usually means expected same-day-not-posted-yet data (HP umpire
assignments before game morning, a lineup nobody's confirmed yet) — not a
scrape failure. `failed` means an exception was caught; check the section body
in the `.txt` for the error message.

## Manual run (local)

```bash
pip install -r requirements.txt
python3 mlb_daily.py              # full run, ~15-20 min
python3 mlb_daily.py --dry-run    # fast subset, ~1 min — same as DRY_RUN=1
```

## What changed from the prior manual script

The version of `mlb_daily.py` handed off for automation (internally "v5") had
drifted from a later "v15" that existed only on a Chromebook that later died.
`FIX_LIST_v5_to_v15.md` (kept for history) reconstructed what v15 fixed from
prior chat history; this automation pass re-verified each item against live
sources rather than trusting the reconstruction blind, and found a few of the
described fixes didn't match what the actual current APIs need. What's real,
verified live against each source as of this rebuild:

- **Rotowire lineup scraper**: rebuilt against the live site's current
  structure (`div.lineup__box` → `div.lineup__abbr` + `ul.lineup__list`), and
  demoted to a **last-resort** fallback, called once globally — never per-team.
- **Primary + preferred fallback for lineups**: MLB Stats API stays primary.
  When a team's lineup isn't posted yet, the pipeline now tries the MLB.com
  dated `starting-lineups` page first (server-rendered, keyed by the same
  `gamePk` the primary API uses — exact matching, not name-fuzzy) before
  falling through to Rotowire.
- **Injuries**: the legacy `/api/v1/injuries` endpoint MLB used to expose is
  **dead** (confirmed: hard 404, not merely flaky). Rotowire's injury-report
  page was evaluated as the documented fallback and rejected — it's fully
  client-rendered with zero data in the raw HTML, would require a headless
  browser dependency disproportionate to a daily cron. The real fix: pull each
  team's 40-man roster (`rosterType=40Man`) and filter to injured-list status
  codes (`D7`/`D10`/`D15`/`D60`) — same underlying data, still free, still
  same-day accurate, no extra dependency.
- **`mlb-statsapi` breaking signature changes**: verified against the pinned
  1.9.0 signature. `schedule()`'s team filter kwarg is `team=`, not `teamId=`
  (the latter raised `unexpected keyword argument`). `player_stats()` has no
  `sportId` kwarg and no catch-all `**params` — the old `sportId=1` call
  raised the same error on every run.
  Both call sites patched.
- **FanGraphs blocking**: this isn't a soft rate-limit — FanGraphs sits behind
  a Cloudflare bot challenge (`Cf-Mitigated: challenge`) that a rotated
  User-Agent alone does not bypass, verified by direct request. Added UA
  rotation + retry/backoff anyway (cheap, sometimes helps depending on the
  runner's IP reputation), and — more importantly — season batting/pitching
  now falls back to Statcast expected-stats/exit-velo data when FanGraphs is
  fully unreachable, so those sections still carry useful data instead of
  going empty.
- **Statcast column drift**: `compute_regression_clusters` referenced columns
  (`xba`, `xwoba`, `barrel_batted_rate`, `hard_hit_percent`) that don't exist
  on pybaseball's *current* `statcast_batter_expected_stats()` output — it
  actually returns `est_ba`/`est_woba`, and barrel%/hard-hit% live on a
  separate endpoint (`statcast_batter_exitvelo_barrels`, as `brl_percent`/
  `ev95percent`). This section silently returned "Failed" on every run before
  this fix; it's now a real merge across both endpoints with verified columns.
- **UmpScorecards**: the site is a client-rendered SvelteKit app, but it calls
  a real JSON API under the hood — verified live at
  `https://umpscorecards.com/api/umpires`, one request for all umpires (not
  per-umpire), giving real career accuracy/consistency/run-impact/ABS-challenge
  data instead of "check umpscorecards.com."
- **Active hit-streak bug**: the old logic broke a player's streak on any
  *calendar-day* gap between hit-games (an off day, a doubleheader quirk),
  even when he'd started every game his team actually played — so it returned
  "no streaks" almost every run. Fixed to walk backward through the player's
  actual games (hit or not) and stop at the first hitless one.
- **New sections**: Times-Through-Order splits (K%/BB%/AVG by 1st/2nd/3rd time
  through the order) and First-Inning Profile (real per-start runs/hits/walks,
  not the placeholder cross-reference text they were before) are now genuinely
  computed from Statcast data instead of pointing at other sections. Added a
  new team-level K% section (distinct from the existing per-batter K% table).
- **Reliability**: retry/backoff wrapper for flaky endpoints, a job-level
  30-minute Actions timeout so a hung Statcast pull doesn't burn the whole
  Actions minute budget, and a run log (see above) so failures/empties are
  visible at a glance instead of discovered mid-analysis.

Section 32 (multi-year Statcast aging curves) is legitimately slow due to data
volume — left as-is, not a bug. UmpScorecards sections returning "not in
career database" close to game time is expected (same-day assignments often
aren't posted until game morning), not a scrape failure.

## Non-goals (out of scope, by design)

- Modifying the `mlb-betting-analyst` Claude.ai skill or its reference files.
- Fanatics closing-line scraping / CLV automation — separate, deferred effort.
- Any bet placement, sizing, or bankroll logic. This repo produces data only.

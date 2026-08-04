# PROJECT-GRIDIRON — MLB Daily Betting Pipeline

Fully automated MLB player-prop research + picks pipeline. GitHub Actions does
all of it, unattended, with **no LLM call and no API key to manage**:
`mlb_daily.py` pulls lineups, weather, injuries, umpire assignments, splits,
and ~88 sections of FanGraphs/Statcast/MLB Stats API data. `generate_picks.py`
scores tonight's actual candidates with an explicit, deterministic weighted
formula — including non-obvious signals a typical bettor wouldn't compute by
hand (pitch-type-specific exploits, a bat-speed leading indicator, times-
through-order matchups) and a "public-awareness discount" that deliberately
downweights picks built on nothing but "star player, high average," since the
market already prices that in. Every pick commits to a real projected number,
not just a category label. `grade_results.py` then closes the loop: each
morning it fetches the actual box scores for yesterday's picks and grades
them, building a running accuracy record in `results/history.json` — so the
system's performance is measured, not assumed. All three land their output in
`output/`/`results/`, committed back to this repo automatically. GitHub is
the interpreter here, not an external model — the scoring logic is plain,
readable Python, not a prompt.

This repo does **not** touch Jacob's separate `mlb-betting-analyst` Claude.ai
skill (still versioned independently), does not scrape Fanatics odds/lines,
and does not place or size bets — it produces research and a shortlist for
Jacob to act on manually.

## How it runs

- **Scheduled**: every day at 10:30 AM ET (`.github/workflows/mlb-daily.yml`,
  cron `30 14 * * *` = 14:30 UTC). Pinned to EDT (UTC-4) — MLB's regular
  season runs entirely within U.S. daylight saving time, so this is accurate
  for every in-season day. A manual off-season re-run would fire an hour
  early — not worth the YAML complexity of resolving DST dynamically for a
  non-issue.
- **Manual**: Actions tab → "MLB Daily Pipeline" → **Run workflow**. Two
  optional toggles: "Dry run" (fast subset — lineups/injuries/weather/umpires
  only, ~1 min, skips picks generation and doesn't commit) and "Skip picks"
  (run the full ~15-20 min data pull but skip the scoring step — useful for
  testing the data side in isolation).
- **Output**: `output/mlb_daily_YYYY-MM-DD.txt` (full research),
  `output/top10_picks_YYYY-MM-DD.md` (the day's picks, human-readable), and
  `output/picks_YYYY-MM-DD.json` (the same picks, structured — what
  `grade_results.py` reads the next morning). `output/run_log_YYYY-MM-DD.json`
  ships alongside the research package — see "Run log" below.
- **No secrets required.** Every data source is free/public, and picks
  generation and grading are local computation, not API calls.

## Repo layout

```
mlb-daily-pipeline/
├── .github/workflows/mlb-daily.yml   # schedule + manual trigger
├── mlb_daily.py                      # data pipeline (single file, ~88 sections)
├── generate_picks.py                 # deterministic scoring -> top 10 picks, no LLM
├── grade_results.py                  # grades yesterday's picks against real box scores
├── requirements.txt                  # pinned dependency versions
├── README.md
├── output/                           # generated .txt / .md / .json land here
├── results/                          # grades_YYYY-MM-DD.json + running history.json
└── data/players/                     # {player_id}.json — longitudinal per-player snapshot history
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

## How picks are generated

`generate_picks.py` is deliberately a separate script from `mlb_daily.py`:
the data pipeline's job is exhaustive, unopinionated data generation; the
picks step is the opinionated scoring layered on top. **No LLM call, no
external API, no key to configure** — it's plain Python implementing this
pipeline's own synthesis framework (see `mlb_daily.py` Section 87) as an
explicit weighted formula instead of prose:

- **35% Matchup** — platoon (batter's bat side vs. pitcher's throwing hand,
  fetched via a batched MLB Stats API call) + opposing pitcher/lineup quality
  + a **pitch-type-specific exploit check**: the batter's actual run-value
  track record against each pitch type the opposing pitcher throws >=15% of
  the time, not just their overall stat line
- **25% Recent form** — L7 rolling exit velo/barrel rate for hitters, L14 K
  rate for pitchers, plus a **bat-speed trend** signal (2nd-half-of-window vs.
  1st-half mean bat speed) — this is a *leading* indicator, since rising bat
  speed tends to precede a batting-average uptick rather than just confirming
  a player who's already hot — and a **times-through-order** check for
  pitchers (does K rate hold up or collapse the 3rd time through the lineup)
- **15% Environment** — wind vs. park orientation, park HR index, temperature
- **15% Baseline skill** — season-long wRC+/ISO/Barrel% (hitters), K%/CSW%/
  Stuff+ (pitchers)
- **10% Context** — lineup slot for hitters, HP umpire zone accuracy +
  opposing lineup handedness composition for pitchers

Weighted toward **trend/data convergence** (how many independent signals
point the same way) rather than a single computed statistical edge, per
explicit direction — an edge still matters, it just isn't the sole filter.
A negative-edge screen actively penalizes patterns like a hot batting average
unconfirmed by underlying contact quality (BABIP luck, not skill).

**Public-awareness discount.** Nothing stops a purely stat-driven model from
just repeatedly picking whoever has the highest season average — but the
market already prices in "this player is good," so that's not a useful
signal on its own. Every candidate's non-obvious converging signals (pitch-
type exploit, bat-speed trend, TTO exploit, extreme park/weather, elite
umpire zone) are counted; a pick built on an obviously-elite season profile
*with none* of those gets scored down, while a pick built from 2+ of them —
even on a more middling player — gets a small boost. This is the actual
mechanism for surfacing "niche" picks rather than just adding more stats.

**Every pick commits to a real number**, not a category label — e.g. "Over
1.5 Total Bases (proj. 2.1 TB)" or "Over 6.0 Strikeouts (proj. 6.5)." The
projection blends recent form and season skill (total bases uses the
AVG+ISO sabermetric identity for expected TB/AB; strikeouts blend L14 K% with
a league-average batters-faced-per-start estimate). This makes picks more
concrete *and* is what makes them gradeable the next morning without a real
sportsbook line — see "Results tracking" below.

### Prop universe

Six prop types, each with its own signal set — not just "whoever has the
highest average":
- **Total bases / hits / home run** — the matchup/form/skill formula above
- **Stolen base** — dominated by sprint speed (a real threat needs to clear a
  minimum speed threshold before matchup context matters at all), then the
  opposing catcher's pop time to 2B and season SB rate as secondary signals
- **Walks** — batter BB% vs. opposing pitcher BB%, plus HP umpire accuracy
  (a less accurate/looser zone tends to mean more walks) — degrades to "no
  pick" when FanGraphs is blocked, since neither BB% input is available from
  the Statcast fallback
- **Strikeouts** (pitcher) — as described above
- **NRFI/YRFI lean** — real per-start first-inning results (not a season
  aggregate) for tonight's starters

A **prop-type diversity cap** (max 4 picks per prop category, same idea as
the existing max-3-picks-per-game cap) keeps the top 10 from being swept by
one category — found this the hard way: an early version of the NRFI scoring
had a scaling bug that tied every scoreless-first-inning starter at exactly
100 and let NRFI leans fill all 10 slots. Fixed the underlying bug (see git
history) and added the cap as a second line of defense regardless.

### Per-player history

Every candidate evaluated each night — not just the top 10 — gets a snapshot
appended to `data/players/{player_id}.json`: what props were considered, what
they scored, and why. This is the audit trail for any pick ("what did the
model know about this player on this date") and the beginning of a
longitudinal dataset beyond the current L7/L14 windows, bounded to a rolling
60-day history per player so file size doesn't grow unbounded over a season.

It reuses `mlb_daily.py`'s already-defined fetchers/constants (`STADIUMS`,
`retry_get`, the fixed bullpen-fatigue fetcher, `fg_bat`/`fg_pit`, etc.)
rather than parsing the finished `.txt` report back into structured data —
pybaseball's on-disk cache (shared within one job run) means this doesn't
mean doubling network calls for what `mlb_daily.py` already pulled. If picks
generation fails for any reason, it degrades gracefully and does **not**
block the research package from being committed.

**No live sportsbook odds are fetched.** A live-odds integration (FanDuel via
a licensed odds-data provider, not direct scraping — see below for why) was
evaluated and shelved: even the free tier of a legitimate odds API doesn't
cover a full daily slate's player props without paying, and that's off the
table for now. So picks are statistical/trend-based, not price-verified +EV
bets. Every pick in the output explicitly says to check the current line
before betting. Treat this as a research shortlist, not a finished bet slip.

### Why FanDuel/Fanatics odds aren't scraped directly

Evaluated and rejected. Both are regulated real-money sportsbooks whose Terms
of Service prohibit automated/bot access, and books actively detect and
restrict accounts suspected of using scraping or betting-automation tools —
that's a real risk to an actual account, not just an engineering hurdle. The
legitimate path is a licensed odds-data provider (e.g. The Odds API), which
does carry FanDuel as a bookmaker and does support MLB player-prop markets —
verified live against their docs. The blocker is purely cost: player-prop
odds are billed per-market-per-game on that API, and even a single market
(e.g. just home runs) across a 15-game slate burns roughly the entire 500
credit/month free-tier allowance in under two weeks. Paid tiers start at
$30/month for full coverage. Revisit if that tradeoff changes.

## Results tracking — closing the feedback loop

`grade_results.py` runs each morning, before that day's new picks are
generated. It reads the previous day's `output/picks_YYYY-MM-DD.json`, checks
each game's actual status via the MLB Stats API (a pick is only graded once
its game is confirmed `Final` — grading against an in-progress or
not-yet-started game would silently score every open pick as a false "miss,"
verified against a real edge case while building this), fetches the real box
score, and grades each pick's own projection as a hit or miss (actual stat >
projection − 0.5, the same "Over X.5" convention the pick's own text commits
to — this is a self-consistency check against the model's own call, not a
market-beating claim, since no real sportsbook line is available to grade
against).

Output:
- `results/grades_YYYY-MM-DD.json` — per-pick detail for that day
- `results/history.json` — running total (hits/misses/ungraded), overall hit
  rate, and a rolling last-14-day hit rate so a slow start doesn't
  permanently anchor the headline number

This is the mechanism that makes "getting more accurate over time" a
measurable claim instead of an assumption — every scoring change going
forward can be checked against whether it actually moved the hit rate, not
just whether it sounds more sophisticated.

## Manual run (local)

```bash
pip install -r requirements.txt
python3 mlb_daily.py                # full run, ~15-20 min
python3 mlb_daily.py --dry-run       # fast subset, ~1 min — same as DRY_RUN=1
python3 generate_picks.py           # scores today's slate, writes top10_picks_*.md + picks_*.json
python3 grade_results.py            # grades yesterday's picks against real box scores
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
  35-minute Actions timeout so a hung Statcast pull doesn't burn the whole
  Actions minute budget, and a run log (see above) so failures/empties are
  visible at a glance instead of discovered mid-analysis.
- **Bullpen fatigue was silently dead**: `box[side]["pitchers"]` (the
  boxscore field the original code read) is just a list of player IDs, not
  the stat lines it was treated as — verified live against a real box score.
  The actual per-pitcher lines live under top-level `awayPitchers`/
  `homePitchers` keys, with pitch count as `p`, not `numberOfPitches`. Every
  team in every run silently printed "No recent usage data." Fixed, and while
  in there, parallelized the ~30 teams x up to 6 sequential API calls each
  (this was the single slowest section, several minutes serial) since an
  unattended daily run needs to reliably finish inside the job timeout —
  cut to well under a minute.
- **Season leaderboard tables were truncated to 300 rows** even when 600-900+
  players qualify at the script's PA/IP thresholds, silently dropping close to
  half the league from the "exhaustive" sections. Raised to 500 — enough to
  cover effectively every regularly-used player without blowing up the
  document size picks generation reasons over.
- **Every lineup's batting order was silently broken** — the biggest find
  while wiring up the picks scorer. The MLB Stats API's lineup objects are
  flat (`{"id","fullName","primaryPosition":{...}}`), not nested under
  `"person"`/`"position"`/`"batSide"` the way the original parsing assumed —
  verified against a live response. Section 1's batting orders printed a
  literal `?` for every name/position/handedness on every game where the
  primary lineup source was used (i.e. most games, most runs), for as long as
  this script has existed. There's also no per-player `battingOrder` field in
  this hydrate — array position *is* the order. Handedness isn't in this
  hydrate at all; now backfilled with one batched `/api/v1/people` call per
  ~100 discovered players (covering both lineup batters and probable
  pitchers) rather than one call per player.
- **Statcast's `player_name` column on raw pitch-by-pitch pulls is the
  pitcher, not the batter** — a well-known but easy-to-miss quirk. The
  picks scorer's L7 rolling-form fetch initially grouped by that column,
  silently building a pitcher-keyed table that never matched a single batter
  lookup. Fixed to group by the numeric `batter` ID column instead, which
  lineup entries already carry.
- **The FanGraphs-blocked Statcast fallback tables renamed their name column
  to "Name" without reformatting the values** — they stayed in Statcast's
  native "Last, First" order while every downstream name-based lookup (and
  the report's own display) expected "First Last," so lookups against the
  fallback silently missed and the report displayed names backwards whenever
  FanGraphs was down. Fixed at the source so both the report text and the
  picks scorer see correctly formatted names.

Section 32 (multi-year Statcast aging curves) is legitimately slow due to data
volume — left as-is, not a bug. UmpScorecards sections returning "not in
career database" close to game time is expected (same-day assignments often
aren't posted until game morning), not a scrape failure.

**`pybaseball`'s `statcast_catcher_framing()` is currently broken upstream**
(confirmed live: a CSV-parsing error, not a threshold/argument issue — Baseball
Savant appears to have changed that export's format). A catcher-framing signal
for the picks scorer was planned and dropped for this reason rather than
building a detector on a dead source; Section 77 in the daily report has the
same underlying issue and was already failing silently before this. Worth
revisiting if pybaseball patches it upstream.

## What's next

Explicitly deferred, not forgotten:
- **Runs/RBI, doubles, and pitcher-unders (ER/H/BB allowed) props** — the
  current prop universe (TB/hits/HR, stolen base, walks, strikeouts,
  NRFI/YRFI) is real but not exhaustive.
- **Defensive-positioning mismatch** — cross-referencing a hitter's Pull%
  against the opposing team's actual defensive positioning/range metrics.
  Dropped for the last pass specifically because Pull% isn't available from
  the Statcast fallback data used whenever FanGraphs is blocked — revisit
  once there's a Statcast-native Pull%-equivalent or FanGraphs access
  improves.
- **Using the per-player history now being collected** — `data/players/` is
  accumulating real data every run, but nothing reads it back yet for
  genuine multi-week trend detection beyond the current L7/L14 windows. That
  becomes possible once there's a few weeks of history to work with.

## Non-goals (out of scope, by design)

- Modifying the `mlb-betting-analyst` Claude.ai skill or its reference files.
- Fanatics closing-line/odds scraping or CLV automation — a separate, deferred
  effort. This is also *why* picks aren't priced against a real line yet (see
  "How picks are generated").
- Any bet placement, sizing, or bankroll logic. This repo produces research
  and a shortlist — Jacob decides and executes manually.

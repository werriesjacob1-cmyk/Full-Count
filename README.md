# Full Count — MLB Daily Betting Pipeline

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
system's performance is measured, not assumed. Real FanDuel prices
(`odds_fanduel.py`) drive a value screen (`value_board.py`) that reports only
props where this model's read beats the real price by more than its own
uncertainty, a parlay builder (`parlay_builder.py`) that turns a plain-text
request into a real, correlation-checked multi-leg parlay, and a forward test
(`grade_value.py`) that settles the value screen against real captured
closing prices rather than the model's own self-consistency check. Everything
lands its output in `output/`/`results/`/`data/`, committed back to this repo
automatically. GitHub is the interpreter here, not an external model — the
scoring logic is plain, readable Python, not a prompt.

---

## Current status

**UPDATED, 2026-08-12.** This section used to be a "pick up here" handoff
banner dated 2026-08-05, with a "do this first" list (diagnose the
schedule trigger, add grading catch-up for missed days) and a longer
priority-ordered backlog (implied team totals, a real expected-PA model,
expected pitcher workload, and eight more items). Left in place across
eight days and dozens of merged fixes, it stopped being a handoff and
became actively misleading: the schedule question and the grading
catch-up were both resolved, and implied team totals, expected PA, and
expected pitcher workload are all now live inputs to `score_batter`/
`score_pitcher` (see "How picks are generated" below) — none of that
was reflected here. Rather than leave a stale to-do list at the top of
the file for the next reader to trust by accident, it's removed. For
what actually happened between 2026-08-05 and now, `git log` and this
project's own accumulated fixes are the real record; for what's still
open, see "What's next" further down.

Standing principles that outlived that specific handoff and still apply:

- **Verify against live data before trusting anything.** The large majority
  of real bugs found in this project were invisible from reading the code
  and only surfaced by running it and checking real values. A theory that
  hasn't been run doesn't count.
- **Do not tune coefficients against thin results.** A record with only a
  handful of graded days has no signal, only noise — any change that
  "improves" a small sample is overfitting. Ground constants in measured
  baselines or explicit reasoning, and wait for a real backtest before
  trusting a number.
- **Don't ship a plausible-looking number that isn't real.** Where a metric
  is genuinely unavailable, say so plainly and name what's shown instead,
  rather than presenting a guess as data.
- **Absent is not zero.** A signal or a stat that could not be determined
  must stay absent, not default to 0 — the two mean different things, and
  conflating them has been the single most repeated real bug in this
  project's history.

---

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

**UPDATED, 2026-08-12** — this tree covered the pipeline as of the original
handoff and had not been touched since; the project grew a live-pricing
layer, a parlay builder, and a real backtest harness in between, none of
which appeared here.

```
Full-Count/
├── .github/workflows/
│   ├── mlb-daily.yml                 # schedule + manual trigger, commits picks/board/results
│   ├── odds-snapshot.yml             # scheduled prop_snapshot.py runs (closing-price capture)
│   └── test.yml                      # push/PR-triggered: runs every test_*.py, glob-based
├── mlb_daily.py                      # data pipeline (single file, ~88 sections)
├── mlb_sources.py                    # shared empirical-rate/season-data fetchers
├── generate_picks.py                 # deterministic scoring -> top 10 picks + full board, no LLM
├── odds_fanduel.py                   # real FanDuel prices, free, from its public web-app API
├── value_board.py                    # the value screen -- good read + fair price, both required
├── parlay_builder.py                 # request-to-parlay engine, correlation-checked
├── correlation.py                    # structural relationship classifier for parlay legs
├── prop_probability.py               # odds math: implied prob, de-vigging, Kelly, calibration
├── prop_snapshot.py                  # scheduled closing-price capture (data/props/)
├── grade_results.py                  # grades yesterday's picks against real box scores (self-consistency)
├── grade_value.py                    # settles the value screen against real captured prices (forward test)
├── check_scratches.py                # confirmed-lineup scratch detection
├── render_board.py / render_full_board.py / render_parlay.py / final_card.py
│                                      # HTML/markdown board renderers + manual price-entry tool
├── backtest/
│   ├── engine.py                     # point-in-time replay over real historical dates
│   ├── signals.py                    # per-signal separation power (AUC) + hand-picked-vs-fitted comparison
│   ├── calibration.py                # probability calibrators, fit on backtest output
│   └── SCHEMA.md                     # the one data contract all three of the above share
├── measure_signals.py                # grades every persisted signal against real outcomes
├── requirements.txt                  # pinned dependency versions
├── README.md
├── output/                           # generated .txt / .md / .json / .html land here
├── results/                          # grades_*.json, history.json, value_screen_record.json, backtest summaries
├── data/players/                     # {player_id}.json — longitudinal per-player snapshot history
├── data/props/                       # props_YYYY-MM-DD.json — captured closing prices
└── test_*.py                         # one file per module under test; run by test.yml automatically
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
- **10% Context** — lineup slot for hitters (blended with opposing bullpen
  fatigue when the sample is real — see below), HP umpire zone accuracy +
  opposing lineup handedness composition for pitchers

**Opposing K% has three independent sources, tried in order.** FanGraphs'
*team*-level batting page is a separate endpoint from its individual
leaderboards and fails on its own schedule — verified live: on one real run,
every individual batting/pitching leaderboard succeeded while the
team-batting/team-pitching/team-fielding pages all came back empty. The
original fallback for that (deriving opposing K% from tonight's confirmed
lineup) turned out to have a silent gap of its own: when FanGraphs'
*individual* batting page ALSO fails, it falls back to Statcast expected-stats
(`_fg_statcast_bat_fallback`), which has no K% column at all — so every batter
the lineup-derived fallback checked came back with no K%, and "Opposing team
K% unavailable" kept showing regardless of whether tonight's lineup was
confirmed. Found live 2026-08-15 chasing a direct report that this message
was still appearing. Fixed by inserting a real, always-available second tier:
`mlb_sources.team_batting_table()`, a team K% derived from the MLB Stats API's
own `/teams/stats` endpoint (not FanGraphs, not Statcast) — already being
fetched into backtest extras and never used to close this exact gap. Order
now: FanGraphs team page → MLB Stats API team page → tonight's confirmed (or
assumed) lineup's own mean K% → honestly unavailable only if all three come up
empty. Every pitcher pick's "why" states which of the four outcomes applied.

Weighted toward **trend/data convergence** (how many independent signals
point the same way) rather than a single computed statistical edge, per
explicit direction — an edge still matters, it just isn't the sole filter.
A negative-edge screen actively penalizes patterns like a hot batting average
unconfirmed by underlying contact quality (BABIP luck, not skill).

**Opposing bullpen fatigue** — a real gap found on review: this data was
already being fetched every run (L7 reliever usage/pitch counts, via the same
verified box-score parsing as the main report) but never actually used in
scoring anywhere. Now blended into a hitter's context component when the
opposing bullpen has a real tracked sample (3+ relievers): a heavily fatigued
pen (40%+ of tracked relievers over 60 pitches in L7) is a genuine,
non-obvious edge — tired relief is more hittable — that most casual bettors
don't check before betting. Blended alongside, but kept distinct: bullpen
*quality* (ERA — see below), since a tired elite bullpen and a tired bad
bullpen are not the same matchup.

**Weather is cross-checked against a second source.** Open-Meteo is the
primary weather feed; the National Weather Service (`api.weather.gov`, free,
no key, verified live) is pulled as an independent check for every non-dome
park (all in the US — dome games skip weather entirely). When both sources
agree, temp/wind are averaged for a steadier read; when they disagree by
10°F+, that disagreement itself becomes a watch-out on the pick rather than
silently trusting one source. Open-Meteo's hourly feed also carries
precipitation probability, applied as a watch-out (50%+ chance) uniformly to
every pick in a game — a delay or postponement affects a batter prop, a
pitcher prop, and an NRFI lean alike, so this is applied once per game rather
than threaded through every scoring function separately.

**Investigated and deliberately not shipped:** ESPN's scoreboard/summary API
as a third lineup source (would help — it carries real player IDs — but
returned a hard 403/access-denied from this environment, not a soft rate
limit). A real gap, not forgotten — noted here rather than shipped
half-verified, consistent with this project's rule of not trusting a source
that hasn't been checked live.

**Statcast catcher framing, root-caused precisely.** Previously just noted
as "broken upstream in pybaseball"; traced it further this pass. Baseball
Savant's `csv=true` export parameter — which every other Statcast
leaderboard `pybaseball` uses still honors (verified live against the
sprint-speed leaderboard, which returns real CSV) — now returns a plain
HTML page instead of CSV specifically for the catcher-framing leaderboard,
which is what breaks `pybaseball`'s parser (`Expected 1 fields in line 38,
saw 4`, since it's trying to parse an HTML `<meta>` tag as a CSV row). This
is a genuine change on Baseball Savant's side for this one leaderboard, not
a stale URL or a missing header — checked both the un-prefixed and
`/leaderboard/`-prefixed URL variants, and neither returns CSV. No
JSON API was found backing the page's current frontend either. Left
unshipped rather than building a fragile HTML-table scraper for one
secondary metric.

**Team bullpen quality (ERA)** — initially deferred (see git history) for
the same reason FanGraphs' team-level page can't be trusted: it's a separate
endpoint from the individual leaderboards and fails independently of them.
Checking whether Baseball-Reference could substitute for FanGraphs' team
page found the same failure mode there too (403, plus the pybaseball wrapper
for it is itself broken against B-Ref's current page structure) — so another
scraped leaderboard site isn't a real fix, just the same risk with extra
steps. Solved instead by never touching a team-level page at all:
`compute_bullpen_era()` aggregates it from the individual pitcher data
that's already fetched (GS==0 filter for relievers, IP-weighted ERA),
bridging FanGraphs' own non-standard team abbreviations (CHW/KCR/SDP/SFG/
TBR/WSN) to the MLB Stats API's official ones so it actually matches
tonight's games. Degrades to unavailable — same discipline as everything
else here — when the individual pitching pull itself fell back to Statcast,
since that fallback doesn't carry Team/G/GS columns to aggregate from.

**Sharp-money divergence** — a genuinely different signal type (market-
derived, not stats-derived), from Action Network's public scoreboard data
(unofficial API, no auth, verified live with real per-team tickets%/money%
splits — e.g. a real slate showed the Angels at 41 points and the Orioles at
-41 points of money%-vs-tickets% divergence on the same night). tickets% is
the share of individual bets on a side; money% is the share of dollars
wagered — when money% runs well ahead of tickets%, professional money is
backing that side despite less public support. Deliberately kept outside the
core weighted formula (capped at ±5 points, only triggers on a real 10+ point
gap) so a market signal can nudge a stats-driven pick but never override it,
per explicit direction that edge "should not be completely ignored" but
isn't the primary filter.

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

**UPDATED — this section described six prop types and was stale by the time
it was checked during a later audit pass** (2026-08-12): it's grown to
fifteen, and Walks was built, shipped, and then deliberately *retired* (see
below) in between — none of that history was reflected here. What's live
now:

**One formula, nine batter markets.** `score_batter()` computes a single
composite score (the matchup/form/skill formula above) per batter, per game.
`generate_picks.py`'s `_batter_options()` then re-prices that same
underlying read against every real FanDuel threshold across nine families —
**hits, total bases, home runs, runs, RBIs, hits+runs+RBIs, singles,
doubles, triples** — and recommends whichever threshold is most likely to
cash. Hits/total bases/home runs blend an empirical rate (this player's own
real game logs) with a modelled per-plate-appearance distribution; runs and
RBIs are empirical-only (they depend on teammates reaching base and driving
him in, which no per-batter distribution can capture) but are real,
priced markets and were being computed and thrown away for months before
that was noticed and fixed.

**Its own formula:**
- **Stolen base** — dominated by sprint speed (a real threat needs to clear a
  minimum speed threshold before matchup context matters at all), then the
  opposing catcher's pop time to 2B and on-base ability as secondary signals
- **Strikeouts** (pitcher) — as described above, blended with FanGraphs
  CSW%/Stuff%/K% where available
- **Pitcher Outs Recorded** — the starter's own shrunk workload rate (outs
  recorded per start, from real innings-pitched data), anchored to
  FanDuel's real posted line when one exists rather than self-selecting an
  easier threshold — see `score_pitcher_outs`'s own docstring for the real
  mis-selection bug this replaced.
- **Combined Starter Strikeouts** — both tonight's starters' strikeout
  totals added together, modelled as two independent binomials (a
  documented approximation — see `prop_probability.combined_strikeouts_
  distribution`) and priced only when FanDuel has actually posted the
  ladder; there is no model-only fallback for this one, since a made-up
  combined line would have no real market to grade against.
- **Laser (105+/110+ MPH exit velocity)** — a batter's own shrunk rate of
  clearing either real Statcast threshold in a game; `score_laser` picks
  whichever of the two the batter clears more often, by lift over league
  rather than raw probability (105+ is structurally always higher than
  110+, so raw probability would just always pick 105+ and hide a genuine
  110+ standout).
- **NRFI/YRFI (both teams)** — the REAL, books-comparable both-teams-
  scoreless market, built from both starters' own shrunk first-inning reads
  combined (see `_build_combined_nrfi`'s own docstring for why the honest
  expectation here is a number close to a coinflip for almost every game —
  that's not a bug, it's what a season of measurement says a starter's own
  first-inning record is worth).

**Retired.** Walks (batter BB% vs. opposing pitcher BB% plus HP umpire zone
accuracy) and a standalone first-inning-per-starter market both shipped,
then were deliberately removed from the board: no "Player to Draw a Walk"
market exists on FanDuel to bet the former against, and the latter is a
one-sided read (this starter's own history) rather than the real,
books-comparable NRFI/YRFI market above, which replaced it. `score_walk` is
never called by `build_candidates()` any more; `score_first_inning` still
runs (its output feeds the real combined NRFI/YRFI market) but no longer
surfaces as its own board entry.

Every one of these families, plus the real FanDuel price wherever one
exists, is documented in `odds_fanduel.MARKET_MAP` and cross-checked for
drift by `test_lookup_table_consistency.py` — see "Live prices" below.

### Ranking: chance of cashing, not score

The board is sorted by **how likely each bet is to hit**, which is the
stated objective. That is a different question from the one the 0-100
quality score answers, and the two came apart badly enough to be worth
recording. The 2026-08-05 board, ranked on score:

| | pick | score | real chance of cashing |
|---|---|---|---|
| #1 | Bobby Witt Jr. — To Steal a Base | 88.1 | ~28% |
| — | Yordan Alvarez — Over 0.5 Hits | unranked | ~79% |

The score was not wrong about Witt: he genuinely has the best steal profile
on the slate — elite sprint speed, a weak-armed catcher, a good on-base
rate. It was answering "which pick has the strongest signals behind it",
not "which bet is most likely to win", and only the second question was
being asked.

Each probability blends two independent reads, both reported on every pick:

- **Empirical (60%)** — the fraction of that player's real games this
  season in which he actually cleared the line, straight from MLB game
  logs. This is not a model of the prop, it *is* the prop, measured. It
  needs no assumption about plate-appearance independence or projected PA
  counts, and it automatically includes everything a model never sees:
  early exits, blowouts, pinch-hit removals. Small samples use the Wilson
  lower bound, so a 4-for-5 week cannot outrank a season of evidence.
- **Modelled (40%)** — a per-plate-appearance outcome distribution built
  from real hit composition (singles/doubles/triples/homers, not a
  league-average guess), convolved over tonight's projected PAs. This is
  the half that knows about tonight's catcher, park and opposing starter,
  which the empirical rate structurally cannot.

The 60/40 split is a starting position, not a fitted result.
`backtest/signals.py` exists to replace it with a measured one.

**The quality score is now a floor, not the ordering.** A pick must score
55+ to make the board. Without that gate, ranking on probability alone puts
a 70% prop on a player in an awful spot above a 68% prop with every signal
behind it — the score is the part that knows about tonight.

**Thresholds are chosen, not assigned.** Prop lines used to come from rules
unrelated to whether they land: a season K% under 18 got "Over 1.5 Hits"
regardless of whether that hitter ever clears two hits. The same board
shipped Kyle Schwarber at *"Over 1.5 Total Bases (proj. 1.45 TB)"* — the
pipeline recommending a line its own projection missed. Every standard line
is now evaluated and the most-likely-to-cash one is taken, with the
rejected alternatives printed alongside so the choice is auditable.

**This says nothing about value.** These rankings ignore price completely.
Books price likely outcomes short, and a 79% prop at heavy juice is not
automatically a good bet. `MAX_USEFUL_PROB` caps the band at 92% so the
list does not fill with near-certainties, but beyond that the output makes
no value claim and says so.

Beneath the probability ordering there is
no per-game or per-prop-type cap. An earlier version capped picks per prop
category to stop one category from sweeping the list (found the hard way:
a scaling bug tied every scoreless-first-inning starter at exactly 100 and
NRFI leans filled all 10 slots). Per explicit direction, that cap was
removed — forcing category variety just to have variety means swapping a
genuinely better pick for a worse one, which defeats the point. Instead the
underlying scaling bug was fixed at the source, and `score_first_inning` now
carries a steep small-sample penalty plus a hard confidence cap below 3
starts, so a thin sample can't out-score a real multi-signal read just
because it landed on 0 runs. If the 10 best-scoring picks tonight are all
the same prop type or the same game, that's what ships.

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

**UPDATED — this section described an earlier version of the project and was
wrong by the time it was checked during a later audit pass.** The paragraphs
below (kept struck through in spirit, not literally, for the reasoning
trail) explain why a licensed odds API looked like the only legitimate path.
That turned out to be based on testing the wrong two sources and
generalizing: `odds_fanduel.py` fetches FanDuel's own public web-app API
directly — the same JSON its website reads, no account, no login, no key of
one's own (a single public client parameter that identifies the calling
*application*, not a user, works unchanged across every regional FanDuel
subdomain). It is not scraping HTML and it is not the ToS-violating
bot-access risk the section below describes; it is the same public,
unauthenticated request a browser makes. Verified live: 108+ batters priced
across a full slate, every prop family this pipeline scores, updated
continuously.

Live prices now drive real parts of the pipeline, not just a display field:
- The main board's picks carry a real posted price (`market_odds`,
  `market_implied`, `market_edge`) wherever FanDuel offers a matching line.
- `value_board.py` screens every priced prop against this pipeline's
  calibrated probability and reports only where a real edge survives the
  model's own uncertainty — see its own module docstring for the two tests
  a prop has to pass.
- `prop_snapshot.py` captures the closing (last pregame) price for every
  prop on a schedule; `grade_value.py` settles the value screen's own past
  calls against those real captured prices and real outcomes — a genuine
  forward test, not the self-consistency check `grade_results.py` runs
  (see below), because prices cannot be reconstructed backwards the way box
  scores can.
- `parlay_builder.py` prices real multi-leg parlays against these same live
  odds, correlation-checked by `correlation.py`.

Every pick still says to check the current line before betting — a captured
price can move between capture and bet — but "no live odds are fetched" is
no longer an accurate description of this project.

## Parlays: request-to-parlay, correlation-checked

`parlay_builder.py` turns a request ("2 home runs, 1 double, 1 triple, $5 to
$1,000, riskier") into a real parlay built from real, currently-scored
candidates — not just the top 10 that made the published board, but the
full ~450-candidate pool `persist_player_snapshots` writes every night
(`data/players/*.json`). `parse_request()` is deliberately minimal pattern
matching, not general language understanding — a production chat interface
would sit in front of this exact function as a translation layer, the same
"the chat layer isn't the moat" principle as everywhere else in this
project; `build_parlay()`/`build_best_available_parlay()` are directly
callable with a structured `ParlayRequest` by anything, no parser required.

**The risk dial** (0 = safest, 50 = balanced, 100 = risky, continuous, not
just three buckets) maps to a `hit_probability` band via linear
interpolation between three fixed anchors — a UI/threshold choice, never a
change to how any individual leg's probability was computed.

**Correlation, not independence, decides which legs can go together.**
Every prop in this pipeline is scored independently — nothing computes a
joint probability for two legs together — which is fine for a single-leg
board and wrong the instant two picks get multiplied into a parlay, since
independent-probability multiplication only holds when the legs actually
are independent, and game props usually are not. `correlation.py`
classifies every candidate pair structurally (same player, same team, a
pitcher's strikeout/outs/first-inning prop against a hitter on the team he
is actually facing) into four labels — **redundant** (stacking barely adds
diversification, e.g. a player's own Runs and RBIs), **positive**, **negative**
(rejected outright — "K prop + opposing hitter" is a hard rule, not a
suggestion), **independent** — deliberately labels, not a fitted
coefficient, since a real correlation number needs backtested outcome data
that doesn't exist yet. `parlay_builder.py`'s leg selection rejects any
candidate that would be negatively correlated or redundant with a leg
already picked, checking every pair, not just consecutive ones.

The reported "combined probability" is explicitly the naive independent-legs
product, labelled a conservative floor rather than a promise — legs flagged
positively correlated are likely somewhat better than that number, by an
amount this project does not yet claim to know.

## Results tracking — closing the feedback loop

`grade_results.py` runs each morning, before that day's new picks are
generated. It reads the previous day's `output/picks_YYYY-MM-DD.json`, checks
each game's actual status via the MLB Stats API (a pick is only graded once
its game is confirmed `Final` — grading against an in-progress or
not-yet-started game would silently score every open pick as a false "miss,"
verified against a real edge case while building this), fetches the real box
score, and grades each pick's own projection as a hit or miss (actual stat >
projection − 0.5, the same "Over X.5" convention the pick's own text commits
to — this is a self-consistency check against the model's own call ("did the
pick turn out right"), not a market-beating claim. `grade_value.py` is the
market-beating claim: it settles the value screen (see above) against real
captured closing prices, which is a genuinely different, harder question
("would this have made money") — kept separate because that forward test
only has as much history as `prop_snapshot.py` has been capturing.

Output:
- `results/grades_YYYY-MM-DD.json` — per-pick detail for that day
- `results/history.json` — running total (hits/misses/ungraded), overall hit
  rate, and a rolling last-14-day hit rate so a slow start doesn't
  permanently anchor the headline number

This is the mechanism that makes "getting more accurate over time" a
measurable claim instead of an assumption — every scoring change going
forward can be checked against whether it actually moved the hit rate, not
just whether it sounds more sophisticated.

**Correction, 2026-08-05:** the first version of `grade_pick()` only knew
two prop shapes — every non-pitcher pick was graded as `total_bases` and
every pitcher pick as `strikeouts`, regardless of the pick's actual prop.
That silently mis-graded stolen-base, walk, and NRFI/YRFI picks the moment
those prop types shipped. Worst case: an NRFI pick's projection (a YRFI rate
like `0.0`) fed into the old threshold math as `0.0 - 0.5 = -0.5`, and the
old code then compared actual strikeouts against that — a real pitcher's K
count is never negative, so **every NRFI pick was guaranteed to grade "hit"
regardless of what actually happened in the game.** 2026-08-04's recorded
80% hit rate was inflated by this bug (4 of its 10 picks were NRFI leans).
Fixed to dispatch on the pick's actual `projection.stat`, added a linescore-
based first-inning check for NRFI/YRFI, and re-graded 2026-08-04 against
real box scores with the corrected logic: the honest result is 2 hits / 4
misses / 4 ungraded (the 4 NRFI picks from before this fix don't carry the
`side`/`lean` fields the corrected grader needs, so they're correctly left
ungraded rather than guessed at) — `results/history.json` reflects the
corrected number, not the original inflated one.

## Manual run (local)

```bash
pip install -r requirements.txt
python3 mlb_daily.py                # full run, ~15-20 min
python3 mlb_daily.py --dry-run       # fast subset, ~1 min — same as DRY_RUN=1
python3 generate_picks.py           # scores today's slate, writes top10_picks_*.md/full board/picks_*.json
python3 grade_results.py            # grades yesterday's picks against real box scores (self-consistency)
python3 value_board.py              # screens today's priced props for real edge (needs FanDuel live)
python3 parlay_builder.py "2 home runs, 1 double, riskier"   # build a real parlay from today's pool
python3 grade_value.py              # settles the value screen against real captured closing prices
python3 prop_snapshot.py            # capture right now's FanDuel prices to data/props/
python3 measure_signals.py          # grade every persisted signal against real outcomes
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
- **`pyb.playerid_lookup()` completely broken in this environment, silently
  killing 10 report sections** — the biggest-scope bug found in a night of
  continued hardening. `playerid_lookup()` downloads and reads a cached
  Chadwick player-ID register as a zip file; that download is corrupted in
  this environment ("File is not a zip file"), and it fails the same way
  every single time, for every pitcher — verified live against 6 real
  tonight's-starters, 0 succeeded. Ten functions (pitcher velocity/spin/
  extension trends, pitch tunneling, in-game micro-fatigue, VAA/HAA,
  pitcher complexity, umpire/catcher/pitcher 3-way, aging curves, tempo
  profiles, TTO splits, first-inning profile) all used this exact same
  pattern to resolve a starter's name to an MLBAM ID before pulling their
  Statcast data — despite the MLB Stats API already handing this pipeline
  that exact ID directly (`away_sp_id`/`home_sp_id` on every `game_meta`
  entry) at the point lineups are fetched. Fixed all 10 call sites to use
  the ID already in hand instead of re-deriving it through a broken lookup.
  Verified live end-to-end post-fix: all 10 now return real per-pitcher data
  for tonight's actual starters.
- **Rotowire fallback silently dropping every batter ID** — Rotowire has no
  MLBAM IDs of its own, and every per-player Statcast lookup in `generate_picks.py`
  (L7 form, bat-speed trend, sprint speed, pitch-type exploits, per-player
  history persistence) keys off that ID. Whenever a lineup fell all the way
  through to Rotowire — verified live, an entire slate did exactly this on
  one real run — those signals didn't error, they just silently went empty,
  showing up as generic "thin sample" notes instead of the real cause. Fixed
  by backfilling IDs from the full active-roster endpoint
  (`/api/v1/sports/1/players`, one call for the whole league) matched by
  name, with a normalized fallback match (strips accents and Jr./Sr./II/III
  suffixes) since Rotowire's own names diverge from the roster's exact
  spelling on both counts (`José Ramírez` → `Jose Ramirez`, `Bobby Witt Jr.`
  → `Bobby Witt`). Verified live against a real slate: 230 of 261 batters
  missing an ID before the fix, 1 after.
- **Two stale stadium names silently breaking weather for those games** —
  `STADIUMS` still keyed the Astros' park as `"Minute Maid Park"` and the
  Athletics' as `"Oakland Coliseum"`; the live MLB API now returns `"Daikin
  Park"` (the Astros' park was renamed) and `"Sutter Health Park"` (the
  Athletics relocated) — neither is a substring match for the old key, so
  the lookup failed outright and those games got no weather data at all.
  Cross-checked all 30 teams' current venue names against `STADIUMS` live
  (not just tonight's slate) to confirm these were the only two stale
  entries. Fixed both; Sutter Health Park's coordinates are confirmed but
  its wall dimensions are a best-effort estimate for this AAA park, not
  independently verified like the rest of the table.
- **Section 67 (lineup context table) made its own separate, fallback-free
  raw MLB API call instead of reusing the already-fetched, already-3-tier-
  fallback-protected `game_meta`** — and its parsing assumed lineup player
  objects are nested under `"person"`/`"battingOrder"`, the exact same
  wrong-structure assumption that was the original biggest bug fixed early
  in this project (in `fetch_lineups()` itself, on a different code path).
  Whenever lineups weren't posted by the primary MLB API tier yet — the
  normal case for a morning run — that combination guaranteed this section
  was empty every time. Rewritten to reuse `game_meta`'s lineups directly,
  and it now actually computes the "OBP ahead" context the section's own
  title always promised but never delivered (`bat_season` was accepted as
  a parameter and silently never used).
- **Three sections (56, 58, 59) silently querying pitchers for hitting
  stats, every run** — `fetch_mlb_splits_batters`, `fetch_mlb_game_logs`,
  and `fetch_babip_career_compare` all took the flat `player_ids` dict and
  sliced its first N entries. `player_ids` mixes probable-pitcher IDs and
  lineup-batter IDs, with pitchers inserted first in `fetch_lineups()` — on
  a normal 15-game slate that's up to 30 pitcher IDs, exactly filling a
  `[:30]` slice every time. Since all three ask the MLB API for *hitting*
  splits — structurally empty for any pitcher — they silently processed 30
  pitchers, got 200 OK responses with zero real rows every time, and
  reported "unavailable." Verified live: `player_ids` had 290 real entries
  and every API call succeeded; 30 of the first 30 were starters, not
  batters. Fixed all three to build a real batter-only ID map from
  `game_meta`'s lineups directly. Also fixed a related cosmetic bug found
  along the way in the same function: the game-log API's `opponent` field
  has no `abbreviation` key (only `id`/`name`), so the opponent column was
  always "?" — now resolved through the same team-ID list used elsewhere.
- **Five more zero-retry raw API calls hardened**, found on a systematic
  sweep for the same fragile pattern that caused two other real bugs
  tonight: `compute_directional_hr_score` and `compute_threshold_flags`
  each independently call Open-Meteo (the same endpoint that logged a real
  "Read timed out" failure for Wrigley Field elsewhere tonight) with no
  retry at all; `fetch_umpire_ou_records` (Covers.com), `fetch_mlb_leaders`,
  and `fetch_mlb_splits_pitchers` had the same gap against their own APIs.
  All five switched to `retry_get`. `compute_directional_hr_score` also now
  explicitly flags in its own output when it had to fall back to
  league-average weather estimates instead of silently presenting a guess
  as if it were the real forecast.
- **CSW% (Section 36) and opposing-lineup K% (Section 45) — corrected, not
  left as gaps.** Both are FanGraphs-only columns with no equivalent in the
  lighter "expected stats" endpoint the batting/pitching fallback uses, and
  an earlier pass left them as accepted gaps on the assumption that a real
  fix — a full leaguewide season Statcast pull — would be too slow for this
  pipeline's time budget. That assumption was never actually checked. It
  was wrong: verified live, a full-season leaguewide pull is ~480K rows and
  completes in ~50 seconds. Built `fetch_season_statcast()` (cached
  module-level, one pull shared by both sections — confirmed live: 44s
  cold, 0.2s warm on the second call) and compute real CSW%/K% from it
  (CSW% = called strikes + whiffs / total pitches; K% = strikeouts / real
  plate appearances, both verified against actual Statcast `description`/
  `events` values first) whenever FanGraphs' own columns aren't available;
  when they are, the real FanGraphs numbers are used unchanged, confirmed
  live to skip the Statcast pull entirely in that case (near-zero added
  time). Also fixed a real design mismatch found in Section 45 along the
  way: its title always promised "per GAME... matchup context," but the
  code just printed a leaguewide top-60 K% table with no connection to
  tonight's actual games. Rebuilt to genuinely deliver what the title
  promises — each of tonight's games, each team's confirmed lineup shown
  against the opposing starter, sorted by K% within that lineup.
- **Section 52 (batter splits vs starters/relievers) hard-failing** — the one
  section a real run_log ever marked `failed` rather than `empty`, i.e. an
  actual uncaught-then-caught exception, not just no data. Root cause: it
  made its own raw `pyb.pitching_stats()` call straight to FanGraphs instead
  of reusing `fg_pit()`'s already-built legacy→modern→Statcast-fallback
  chain, so it had zero protection against exactly the FanGraphs blocks
  every other pitching section already handles. Fixed to consume Section
  33's already-fetched `pit_season` instead of fetching its own copy —
  degrades to "unavailable" (not a crash) on the rare case that even that
  fell back to Statcast, since Statcast's shape doesn't carry the GS/G
  columns this needs to split starters from relievers.
- **The run_log status classifier itself was misreporting healthy sections
  as "empty"** — worth flagging clearly, since this has been the tool used
  all night to decide what needed fixing. `build_run_log()` scans each
  section's body text for failure-marker phrases, but did so across the
  *whole* body regardless of length — so one legitimate per-player caveat
  buried inside an otherwise rich section (e.g. "no Statcast data" for the
  one backup pitcher without a Statcast profile, alongside real data for a
  dozen other pitchers) was enough to flag the entire section "empty."
  Verified live: Section 20 had a real, 16,656-character body full of
  genuine velocity/spin data — misclassified as empty on that basis alone.
  Cross-checked every genuinely-empty section in a real run's actual output
  and found they're all ≤202 characters (just the bare "X unavailable."
  message and nothing else), so the marker checks are now gated on a
  250-character threshold: a substantial body can no longer be downgraded
  by an incidental phrase inside it, while a real short failure message is
  still caught correctly. Re-run against tonight's actual captured output:
  70 ok / 18 empty / 0 failed → **73 ok / 15 empty / 0 failed**, with the
  remaining 15 confirmed genuinely empty, not further false positives.
- **Workflow's own commit step losing an entire run's output on a push
  race** — a real run failed live tonight (run #7): a manual code push
  landed on `main` while that run's ~15-20 min of data collection was still
  in flight, so its own end-of-run `git push` was rejected outright
  (non-fast-forward) with no handling for that case at all, silently
  discarding every section that run had just pulled. Fixed the "Commit
  generated output" step to retry with a fetch + rebase onto `origin/main`
  (up to 5 attempts) instead of a bare `git push`. Verified with a local
  repro of the exact race (a code-only commit landing on the remote between
  this step's `git add` and `git push`): first push correctly rejected,
  rebase reconciles cleanly since generated-output commits never touch the
  same files a code push does, second attempt succeeds with both changes
  intact. Aborts loudly (not silently) if a rebase ever hits a real
  conflict, rather than leaving the repo in a half-merged state.
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
- **Runs/RBI, doubles, pitcher-unders (ER/H/BB allowed) props** — UPDATED:
  runs, RBIs and doubles shipped (see "Prop universe" above); pitcher-unders
  (ER/H/BB allowed) have not.
- **PLAYERS_TO_COMBINE_FOR_\* (two or more players in one bet)** — a real,
  heavily-priced FanDuel market family (~640 lines a night) deliberately
  left unmapped: pricing it honestly needs the JOINT distribution, and
  teammates' outcomes are correlated (same game, same pitcher, same run
  environment) — multiplying two independent probabilities would overstate
  every one of them. `correlation.py` now exists (built for
  `parlay_builder.py`) and may be reusable here; not yet evaluated for that.
  This is a real feature build, not a quick wire-up — do not start it
  without checking scope first.
- **Pull tendency vs. park, done and MEASURED (2026-08-12); opposing-team
  positioning specifically, not done.** `mlb_sources.pull_rates()` computes
  real Pull% directly from Statcast batted-ball spray angle, no FanGraphs
  dependency at all, and `score_batter` has recorded it (`pull_park_synergy`,
  pull rate interacted with the park's own handedness-split HR index) since
  before this session. It only became backtest-measurable this session (the
  extras-construction gap that made it and eight other signals invisible to
  `backtest/signals.py` was fixed 2026-08-12 — see `backtest/engine.py`).
  Measured on a fresh 33-date backtest: no real separation power on its own
  (AUC 0.522, CI containing 0.50) and fully redundant with `park_hand_index`
  (r=0.877, which has the stronger univariate read of the two) — see
  `generate_picks.py`'s own audit comment near where it's recorded. Left
  unweighted; this is the honest "record, measure" outcome, not an
  unfinished step. What's still genuinely missing is the OTHER half of the
  original idea: the opposing team's own defensive positioning/range, to
  catch when a pull-heavy hitter is running into an already-well-positioned
  defense. No free per-team positioning data source has been found for
  this. Also worth noting: MLB's 2023 shift rule (two infielders required on
  each side of second base) already eliminated the most extreme version of
  what this bullet originally chased — the aggressive
  three-infielders-on-one-side shifts that made "pull into the shift" a
  dramatic signal no longer exist, so even a real positioning data source
  would likely move the needle less than it once would have.
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
modified

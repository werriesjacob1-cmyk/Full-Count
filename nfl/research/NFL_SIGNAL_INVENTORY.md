# NFL SIGNAL INVENTORY — MISSION NFL-01, PHASE B (SIGNAL RESEARCH)

Generated 2026-09-11 by the NFL-01 signal research agent.
Repo: /home/user/Full-Count, branch `claude/practical-maxwell-wgs4l6`.
Observed HEAD at generation: `93eb22dc601eb098a1f831408cc0f1df711cfc43`.
(The mission brief cited `97c3dab64a29cd3146a478895f30a40d9922b510` as `repo_ref`.
That commit is NOT current HEAD on this branch. The JSON keeps the briefed
`repo_ref` verbatim as instructed and adds `repo_head_at_generation` with the
commit actually read. THE TREE WINS: every count below was run at 93eb22dc.)

**THE RANKING IS A RESEARCH PRIORITY ORDER, NOT EVIDENCE.**

**EVERY SIGNAL CARRIES EXACTLY FIFTEEN FIELDS. A signal missing any one of the
FIFTEEN is INADMISSIBLE and is not in this inventory.**

This document is research and design. It contains NO pipeline code, NO scorer,
NO model, NO weight, and NO probability. It proposes nothing be trusted.

---

## 0. LANGUAGE DISCIPLINE, AND WHY IT IS NOT OPTIONAL

Nothing in this document is called "proven", "established", or "validated".
The reason is Full Count's own MLB record, which must be cited whenever anyone
is tempted to promote a signal because the story is good:

- 2,134 graded predictions across 32 dates and 15 markets.
- NO measurable within-market ranking skill: **AUC 0.492 [0.461, 0.521]**.
- None of eight pre-specified alternative rankings beat it out of sample.
- Pooled cross-market AUC **0.748 [0.721, 0.777]** was **BASE-RATE SEPARATION,
  not ranking skill** — the pooled number looked strong precisely because easy
  markets and hard markets were mixed together.
- **Two findings were RETRACTED in September 2026** after failing to replicate
  on held-out dates.

That is what happened to a system built on 48 causally excellent signals. NFL
gets the same discipline, harder, because football's causal stories are *more*
seductive (coaching! scheme! film!) and the sample is *far* smaller
(~17 games/team/season vs 162).

The base-rate trap is the reason the markets section below leads with base
rates: **any future NFL evaluation that pools anytime-TD (~30-45% events) with
receptions-over (~50%) with 2+ TD (~5-8%) will reproduce MLB's fake 0.748.**

---

## 1. REPO GROUNDING — COUNTS ACTUALLY RUN AT HEAD 93eb22dc

Commands run verbatim and their output:

```
$ grep -c "_sig(" generate_picks.py
57

$ grep -n "AUDIT\|MEASURED" generate_picks.py | wc -l
19

$ grep -oE '_sig\(signals, "[A-Za-z0-9_]+"' generate_picks.py \
    | sed -E 's/.*"([A-Za-z0-9_]+)"/\1/' | sort -u | wc -l
48

$ wc -l generate_picks.py
7308
```

### 1.1 Prompt-era claims vs the tree

| Claim | Status at HEAD | Evidence |
|---|---|---|
| 48 distinct signal names | **HOLDS** | 48 unique `_sig()` keys (list below) |
| 57 `_sig()` call sites | **HOLDS** | `grep -c "_sig("` = 57 (includes the `def _sig(` definition line and one bare continuation, so 57 is the raw pattern count, not 57 distinct recordings) |
| 19 AUDIT/MEASURED blocks | **HOLDS** | `grep -n "AUDIT\|MEASURED" \| wc -l` = 19 |
| 35% matchup / 25% recent form / 15% environment / 15% baseline skill / 10% context, INVENTED and never fitted | **NO LONGER HOLDS IN SHIPPING CODE** | see 1.2 |

### 1.2 The weights have been FITTED since the claim was written — this matters enormously for NFL

`backtest/signals.py` still opens with the original indictment ("The weights in
generate_picks.py (35% matchup / 25% recent form / 15% environment / 15%
baseline skill / 10% context) were *invented*"), and `backtest/SCHEMA.md` still
describes `cat_*` as the components captured "BEFORE the hand-set 35/25/15/15/10
weighting is applied". But the live scorers no longer use that split:

```
generate_picks.py:1572  (score_batter)
  score = clamp(matchup*0.04 + form*0.03 + env*0.20 + skill*-0.09 + context*0.64)

generate_picks.py:2326  (score_pitcher)
  score = clamp(matchup*0.11 + form*-0.16 + env*0.15 + skill*0.48 + context*0.10)

generate_picks.py:2925  (score_stolen_base)   score = skill*0.50 + matchup*0.28 + context*0.22
generate_picks.py:3217  (score_walk)          score = skill*0.66 + matchup*0.24 + context*0.10
```

Two findings that should govern NFL design:

1. **Fitting reversed signs.** `form` is NEGATIVE for pitchers (-0.16) and
   `skill` is NEGATIVE for batters (-0.09). The in-code comment states the
   reason plainly: season-level power/contact stats "are largely already priced
   in by the market itself". That is an empirical *market-efficiency* finding
   discovered only by fitting — and it is exactly the criterion this NFL
   inventory ranks on.
2. **Fitting mostly did NOT help.** The walk market is described in-code as
   "the ONE market where fitted weights demonstrably beat the hand-picked ones
   on held-out later dates: AUC 0.591 vs 0.576". On hits the same comparison
   "came back inside the noise (CI [-0.0058, 0.0442], contains zero), so those
   weights were deliberately left alone."

**Therefore: the documentation string 35/25/15/15/10 is stale, the shipping
numbers are fitted, and fitting bought almost nothing.** The NFL lesson is not
"fit the weights sooner". It is: *a category weight is a downstream consequence
of measurement, and even correct measurement of hand-designed categories yielded
one AUC point in one of fifteen markets.* This is why this document assigns NO
weights to anything — including to coaching, which the brief explicitly warns
must not be answered with "coaching = 20%".

### 1.3 The 48 MLB signal names (vocabulary calibration)

```
bat_speed_trend  batter_bb_pct  bullpen_era_diff  bullpen_fatigue  bvp_ops
catcher_poptime  consecutive_games  csw_pct  days_rest  getaway_day
hard_hit_105_rate  iso  l14_k_pct  l7_avg_ev  l7_barrel_pct  lineup_slot
money_ticket_split  on_base  opp_catcher_framing  opp_team_cs_pct
opp_team_k_pct  park_hand_index  park_hr_index  pitch_exploit  platoon
platoon_barrel_pct  platoon_xwoba  pull_park_synergy  same_hand_ratio
season_barrel_pct  season_k_pct  series_game  sp_bb_pct  sp_era_weak
sp_rp_ops_gap  sprint_speed  stuff_plus  team_total_move  team_total_open
tto_penalty  ump_accuracy  ump_bb_pct  ump_k_pct  ump_run_impact  vs_rp_ops
woba_ahead  woba_behind  wrc_plus
```

Note the shape MLB converged on: mostly *opportunity and context* names
(`lineup_slot` carries a 0.64 weight — batting order, i.e. plate-appearance
opportunity, is the single largest fitted component in the batter scorer).
**That is the MLB analogue of "opportunity dominates efficiency" and it is the
one place where fitting and causal reasoning agreed.**

### 1.4 Contracts this NFL inventory inherits

- **`backtest/SCHEMA.md`** — `signals` is a `name -> raw numeric` dict. **A
  signal that did not fire is ABSENT, not zero.** Every NFL signal below must
  be expressible as one stable snake_case key mapping to one raw number, and
  must have a defined "did not fire" condition.
- **`backtest/signals.py`** — median-impute + companion `<signal>__fired`
  indicator; NaN/inf treated as ABSENT (a real shipped bug had NaN reach
  `clamp()` and score as MAXIMUM); **time-based splits only**, never random;
  `MIN_EVENTS_PER_PARAM = 15.0` with `authoritative=False` below threshold;
  segment by prop type because pooling tracks base rate not skill;
  collinearity is treated as a first-class hazard ("implied team total already
  encodes park, weather and opposing pitcher quality"). **Every one of those
  five hazards is worse in NFL**, and the collinearity one is severe: implied
  team total, spread, pass rate over expectation and red-zone opportunity are
  all partially the same variable.
- **`measure_signals.py`** — 17 signals were wired in "on the explicit promise
  that they would be measured before being weighted", and that promise "had no
  way to be kept when this was written" because `backtest/engine.py` built its
  kwargs without `extras`, the published board held only ten rows a day, and
  `data/players/*.json` (~420 candidates/day, signals included) was written and
  never read by anything. **The substrate was being written and thrown away.**
  Four signals (`bvp_ops`, `team_total_move`, `team_total_open`,
  `money_ticket_split`) remain *forward-only*: live aggregates with no
  reconstructable historical archive. **That asymmetry — recordable live,
  unreconstructable historically — is precisely field 5 of this inventory and
  precisely why NFL-01 Phase A archives prospectively.**
- **`correlation.py`** — four LABELS (`redundant` / `positive` / `negative` /
  `independent`), deliberately NOT a coefficient, because "a correlation
  COEFFICIENT implies a level of precision nothing here has earned yet", and
  `independent` means "no rule fired", not "verified uncorrelated".
- **`test_info_beyond_market.py`** — the repo already treats "is our
  disagreement with the sportsbook predictive?" as a first-class, tested
  question, and the test exists because a real display bug swapped a Brier
  score for a log loss. Information beyond market price is not a new idea being
  introduced here; it is the existing standard.
- **`AGENTS.md`** rules that bind this document: #11 probability and value are
  different; #14 missing/stale data must reduce confidence, never silently
  become favorable evidence; #15 feature changes require held-out evidence and
  explicit versioning; #16 backtest and forward performance must never be
  conflated.
- **`nfl/archive/provenance.py`** (Phase A, already committed, 166 lines) —
  already implements SEVEN coverage outcomes: `CHECKED_AND_FOUND`,
  `CHECKED_AND_NONE_FOUND`, `SOURCE_FAILED`, `NOT_CHECKED`,
  **`UNAVAILABLE_BY_POLICY`**, `STALE`, `UNRESOLVED_CONTRADICTION`, plus
  `CONCLUSIVE_OUTCOMES = {CHECKED_AND_FOUND, CHECKED_AND_NONE_FOUND}` and a
  `coverage_summary()` that names `sources_with_no_conclusive_observation`.
  It separates `observed_at` (what this process can vouch for) from
  `source_timestamp` (what the payload claims) and refuses to collapse them.
  **`UNAVAILABLE_BY_POLICY` is a state the mission brief's own list of six
  omits** — it is the correct home for Next Gen Stats (401), Pro-Football-
  Reference (403 with a real error body), and any film source whose automation
  rights are not established. Section D below aligns to these seven and
  proposes what they still lack.

---

## (RESEARCH IN PROGRESS — sections 2 onward are being written now)

---

## 2. THE SIGNAL INVENTORY — 56 ADMISSIBLE SIGNALS

**EVERY SIGNAL CARRIES EXACTLY FIFTEEN FIELDS.** The fifteen are:
`name`, `market_scope`, `mechanism`, `point_in_time`,
`historical_point_in_time_reconstructability`, `source`, `access_cost`,
`access_method`, `terms_status`, `fragility`, `historical_depth`,
`expected_marginal_info_beyond_market`, `market_efficiency`,
`volatility_risk`, `prior_confidence`.

A signal missing any one of the **fifteen** is INADMISSIBLE. All 56 entries below
satisfy all fifteen; the machine-readable full text of every field for every
signal is in `nfl/research/nfl_signals.json` (validated: 56 signals, 15/15
fields each, ranks 1..56 contiguous).

### 2.1 RANKING BASIS

Ranked on **EXPECTED MARGINAL INFORMATION BEYOND MARKET PRICE**. Not
plausibility. Not effect size in a paper. Not football intuition. Not how
interesting it sounds. **A signal that is real, large, and FULLY PRICED ranks
BELOW a smaller signal the market incompletely prices.**

**THE RANKING IS A RESEARCH PRIORITY ORDER, NOT EVIDENCE.**

Three consequences of that rule are visible in the table and are deliberate:

- `days_rest_differential` ranks **56th of 56** despite being the single most
  reliable, most perfectly point-in-time-reconstructable, most immutable item in
  the entire inventory. It is known months in advance to everyone, so its
  marginal information beyond the price is approximately zero.
- `team_red_zone_trips_expectation` ranks 52nd even though it is arithmetically
  *necessary* for the touchdown family, because its market-derived component
  **is the market**.
- `alt_ladder_internal_coherence` ranks 3rd while requiring no football
  prediction at all, because internal inconsistency in a price ladder is
  discoverable from the price sheet alone.

### 2.2 THE RANKED TABLE

| # | name | family | prior_conf | marginal info | market efficiency | live PIT | historical PIT reconstructability |
|---|---|---|---|---|---|---|---|
| 1 | `inactive_role_reallocation_delta` | usage | MEDIUM | HIGHEST IN THIS INVENTORY, as a hypothesis. The market unquestionably ... | PARTIAL AND ASYMMETRIC. Expect the primary replacement ... | T-90 minutes from kickoff at the earliest (official NFL inacti... | POOR WITHOUT PROSPECTIVE ARCHIVAL. No free structured historical archi... |
| 2 | `goal_line_carry_share_change` | scoring | MEDIUM | HIGH. Anytime-TD is a heavily traded market, but its pricing tends to ... | LIKELY INCOMPLETE. Books have the same play-by-play. Th... | Computable from the previous week's play-by-play the night aft... | GOOD. Derivable from prior-week pbp using yardline_100, goal_to_go, ru... |
| 3 | `alt_ladder_internal_coherence` | market | MEDIUM on the method, LOW on whether exploitable incoherence actually exists at FanDuel's pricing quality. | MEDIUM-HIGH AND UNUSUAL, BECAUSE IT DOES NOT REQUIRE PREDICTING FOOTBA... | This signal IS a test of market efficiency rather than ... | Whenever the archive holds a full ladder for a player, i.e. an... | ONLY FROM FULL COUNT'S OWN ARCHIVE, same as all market signals. |
| 4 | `contradiction_state_flag` | intelligence | LOW on any specific implementation; the design principle is inherited from shipped MLB behaviour rather than from measurement. | HIGH, AND STRUCTURALLY DIFFERENT FROM EVERY OTHER ENTRY. This does not... | NOT APPLICABLE IN THE USUAL SENSE. A book does not post... | Continuous through the week; strongest Wednesday-Saturday. Req... | ONLY VIA PROSPECTIVE ARCHIVAL, and only if each contributing observati... |
| 5 | `coach_statement_role_claim` | intelligence | LOW. NOTHING may be HIGH on coach-quote grounds alone, and this entry is the canonical case. | HIGH AS A HYPOTHESIS AND EXPLICITLY UNMEASURED. Jacob's requirement th... | UNKNOWN AND PROBABLY HETEROGENEOUS BY SPEAKER. A widely... | Continuously through the week from press conferences and media... | POOR TO NONE WITHOUT PROSPECTIVE ARCHIVAL, AND THIS IS THE SHARPEST EX... |
| 6 | `end_zone_target_share` | scoring | LOW | MEDIUM-HIGH. It requires a derivation rather than a lookup, it is the ... | MEDIUM. Anytime-TD pricing is dominated by team total a... | In-season, nightly after each game day ('on a nightly basis af... | GOOD, WINDOW-CAPPED. Keyed by game_id/week so a prior-games-only aggre... |
| 7 | `designed_qb_red_zone_run_rate` | scoring | MEDIUM | HIGH. It directly explains a recurring pricing puzzle — an RB with hea... | PARTIAL. Books certainly know which quarterbacks sneak.... | In-season, nightly after each game day ('on a nightly basis af... | GOOD, WINDOW-CAPPED. Keyed by game_id/week so a prior-games-only aggre... |
| 8 | `third_down_and_two_minute_role` | usage | MEDIUM | MEDIUM-HIGH. This is the cleanest interaction in the inventory between... | PARTIAL. Books condition receptions lines on game scrip... | In-season, nightly after each game day ('on a nightly basis af... | GOOD, WINDOW-CAPPED. Keyed by game_id/week so a prior-games-only aggre... |
| 9 | `speaker_reliability_profile` | intelligence | LOW. Explicitly: DO NOT assume all coaches are equally informative, and DO NOT assume specificity implies usefulness — a very specific claim can be a very specific error. | POTENTIALLY HIGH AND STRUCTURALLY UNAVAILABLE TO CASUAL ACTORS. This i... | Not a priced quantity. | Derived; available whenever the archive supports it. Requires ... | ONLY PROSPECTIVELY, BY CONSTRUCTION. A reliability profile computed on... |
| 10 | `ol_configuration_change` | film | LOW | MEDIUM-HIGH. Offensive line news is systematically less covered than s... | PARTIAL. Books respond to a starting tackle being ruled... | Depends on the layer. Starting-five INTENT is reported during ... | PARTIAL AND TRICKY. Snap counts reconstruct what the line WAS, but tha... |
| 11 | `red_zone_target_share` | scoring | MEDIUM | MEDIUM-HIGH. Red-zone target share is a standard public metric, so raw... | MODERATELY EFFICIENT on levels. The per-player anytime-... | Nightly after each game day from prior-week pbp; available ~T-... | GOOD, window-capped. Derived from pbp yardline_100 <= 20 (and <=10) wi... |
| 12 | `beat_reporter_practice_observation` | intelligence | LOW | MEDIUM-HIGH AS A HYPOTHESIS. Practice observation is genuinely earlier... | PARTIAL. Prominent reports move lines. Less prominent o... | Wednesday through Saturday, published within hours of practice... | POOR. Articles are edited, moved and paywalled; publication timestamps... |
| 13 | `practice_status_trajectory` | intelligence | MEDIUM | MEDIUM-HIGH for availability, LOWER for workload. Books price the fina... | HIGH on the binary availability question. UNKNOWN on th... | Wednesday afternoon, Thursday afternoon, Friday afternoon loca... | POOR AT DAY GRANULARITY, FAIR AT WEEK GRANULARITY. nflverse injuries_<... |
| 14 | `evidence_completeness_deficit` | intelligence | MEDIUM that the mechanism is sound, because an equivalent mechanism already ships in MLB. LOW that any particular threshold for acting on it is right. | HIGH IN OPERATIONAL VALUE, ZERO AS A FOOTBALL EDGE, and the distinctio... | Not applicable. | Available at prediction time by construction — it is a propert... | PERFECT, and uniquely so in this inventory, because it is emitted by t... |
| 15 | `player_share_of_team_td_opportunity` | scoring | LOW as measurement. MEDIUM as an architectural requirement. | HIGH AS AN ARCHITECTURE, UNPROVEN AS A SIGNAL. The architectural claim... | The market prices each TD expression separately and is ... | In-season, nightly after each game day ('on a nightly basis af... | GOOD for the share component; INHERITS THE MARKET PROBLEM for the team... |
| 16 | `adot_profile` | usage | MEDIUM | MEDIUM-HIGH, CONCENTRATED IN THE TAIL MARKETS. FanDuel's observed NFL ... | MEDIUM. Central receiving-yards lines are efficient. Wh... | Two independent routes, both in-season: pbp air_yards (verifie... | GOOD, window-capped, both routes. |
| 17 | `score_dependent_play_calling_profile` | coaching | LOW. No measurement exists; the persistence of such profiles across rosters is explicitly one of the things this pass could NOT establish. | MEDIUM-HIGH. This is the most concrete bridge between the coaching fam... | MEDIUM. Books condition on spread; whether they conditi... | In-season, nightly after each game day ('on a nightly basis af... | GOOD for the profile; the spread input inherits the mutable-line probl... |
| 18 | `motion_rate_change` | film | LOW | MEDIUM-HIGH AS A CHANGE DETECTOR. This is the cleanest legitimately fr... | UNKNOWN. Motion rate is discussed publicly but rarely p... | Documented as in-season: 'charted within 48 hours following ea... | GOOD for seasons 2022-2025, window-capped; UNVERIFIED for the current ... |
| 19 | `air_yards_share` | usage | MEDIUM | MEDIUM-HIGH. Air-yard share is well known to the analytics public, whi... | LIKELY WELL PRICED on the main receiving-yards line. LE... | Weekly, in-season. nflverse Next Gen Stats assets update 'ever... | GOOD BUT WINDOW-CRITICAL. The NGS weekly file is keyed (season, week, ... |
| 20 | `screen_and_manufactured_touch_rate` | film | LOW | MEDIUM-HIGH FOR RECEPTIONS SPECIFICALLY. FanDuel's observed menu has a... | MEDIUM. | Documented as in-season: 'charted within 48 hours following ea... | GOOD 2022-2025; current season UNVERIFIED. |
| 21 | `red_zone_route_participation` | scoring | LOW | HIGH IN CONCEPT. This is close to the ideal TD-opportunity denominator... | UNKNOWN. Books with licensed charting can compute it in... | LIVE PREGAME: NOT AVAILABLE for the current season at all. Par... | HISTORICALLY EXCELLENT, LIVE USELESS — the exact inverse of the usual ... |
| 22 | `defense_man_zone_rate` | defensive_matchup | LOW | HIGHEST OF THE DEFENSIVE FAMILY IN CONCEPT. This is the field that mak... | UNKNOWN. Books using licensed charting have in-season c... | LIVE PREGAME: NOT AVAILABLE for the current season at all. Par... | HISTORICALLY EXCELLENT, LIVE USELESS — the exact inverse of the usual ... |
| 23 | `route_participation_change` | usage | LOW | HIGH IN PRINCIPLE, STRUCTURALLY THROTTLED IN PRACTICE. The concept — a... | UNKNOWN. Books that license charting data (which Full C... | FOR THE CURRENT SEASON: NOT AVAILABLE AT ANY HOUR. The nflvers... | HISTORICALLY EXCELLENT, LIVE USELESS — the exact inverse of the usual ... |
| 24 | `personnel_package_usage_rate` | coaching | LOW | MEDIUM-HIGH in concept. Personnel usage is the most direct measurable ... | UNKNOWN, and plausibly the market knows more in-season. | LIVE PREGAME: NOT AVAILABLE for the current season at all. Par... | HISTORICALLY EXCELLENT, LIVE USELESS — the exact inverse of the usual ... |
| 25 | `te_inline_vs_detached_rate` | film | LOW | MEDIUM-HIGH IN CONCEPT. Tight end is the position where nominal design... | UNKNOWN; books with licensed charting have in-season al... | LIVE PREGAME: NOT AVAILABLE for the current season at all. Par... | HISTORICALLY EXCELLENT, LIVE USELESS — the exact inverse of the usual ... |
| 26 | `defense_box_count_profile` | defensive_matchup | LOW | MEDIUM. Box count is the most mechanistic available read on run-game m... | MEDIUM. Rushing-yards lines are not obviously set from ... | SPLIT. defenders_in_box is participation-only (NOT in-season).... | GOOD historically via either route, window-capped. HISTORICALLY EXCELL... |
| 27 | `wind_forecast_vintage` | environment | MEDIUM that wind affects passing and kicking outcomes; LOW that a forecast reading beats the price. | MEDIUM-HIGH FOR OUTDOOR LATE-SEASON GAMES, NEGLIGIBLE FOR DOMES. Total... | HIGH on the game total. LESS CERTAIN on individual play... | Continuously available from roughly seven days out and refresh... | ONLY BY PROSPECTIVE ARCHIVAL OF FORECASTS, AND THIS IS A HARD, VERIFIE... |
| 28 | `play_caller_change_flag` | coaching | LOW. Nothing here is measured. The causal argument that coaching creates and reallocates opportunity is strong; the brief is explicit that a strong causal story is not evidence, and no weight is proposed. | HIGH AS A REGIME MARKER, LOW AS A PREDICTION. A play-caller change is ... | The FACT of the change is public and instantly priced i... | Announced or reported when it happens: a midseason change is t... | POOR. THIS IS THE SINGLE LARGEST STRUCTURED-DATA GAP FOUND IN THIS RES... |
| 29 | `rushing_yards_over_expected` | film | LOW — the exact fields available were not confirmed, so even the measurement definition is provisional. | MEDIUM. Directly addresses the brief's 'yards blocked vs created where... | MEDIUM. | Weekly in-season, overnight. ngs_rushing.csv.gz verified 200 (... | GOOD, window-capped. |
| 30 | `qb_pressure_response_profile` | film | LOW | MEDIUM. This is the most honest available answer to the brief's questi... | MEDIUM. Sack props (TO_RECORD_1+_SACK observed live) an... | SPLIT AND UNSATISFACTORY. The best measurements (was_pressure,... | GOOD for 2022-2025 via FTN and 2016-2025 via participation, window-cap... |
| 31 | `line_movement_from_open` | market | MEDIUM that it detects coverage gaps; LOW that it predicts outcomes. | HIGH AS A DETECTOR OF THE PIPELINE'S OWN BLINDNESS, NOT AS AN EDGE OVE... | THE MOVEMENT IS THE MARKET BECOMING MORE EFFICIENT. Any... | Available continuously from the first archived snapshot onward... | ONLY FROM FULL COUNT'S OWN ARCHIVE, AND THIS IS NOT NEGOTIABLE. The ML... |
| 32 | `rookie_and_backup_integration_curve` | usage | LOW | MEDIUM. Trailing averages systematically understate a player whose rol... | PARTIAL. Books clearly adjust for obvious role expansio... | In-season, nightly after each game day ('on a nightly basis af... | FAIR. Snap-share and usage curves by weeks-since-debut are reconstruct... |
| 33 | `offensive_snap_share_change` | usage | MEDIUM | MEDIUM. Snap share is among the most widely watched public metrics in ... | LIKELY EFFICIENT on levels for prominent players; plaus... | Available in-season within roughly a day of each game — snap_c... | GOOD, window-capped. Keyed by game_id/week, so prior-weeks-only aggreg... |
| 34 | `separation_and_cushion_profile` | film | MEDIUM | MEDIUM. These are the best free tracking-derived film substitutes avai... | MEDIUM-HIGH. | Weekly in-season, overnight ('every night (in the range of 3 a... | GOOD, window-capped. Single rolling file, so an explicit week filter i... |
| 35 | `catchable_and_contested_target_profile` | film | LOW | MEDIUM. These are human charting judgements and therefore carry annota... | UNKNOWN. Not a mainstream pricing input as far as this ... | Documented as in-season: 'charted within 48 hours following ea... | GOOD 2022-2025; current season UNVERIFIED. |
| 36 | `defense_pressure_rate` | defensive_matchup | MEDIUM | MEDIUM. Pressure rate is a heavily published metric. Its marginal valu... | MEDIUM-HIGH on team-level pressure; lower on the OL-vs-... | SPLIT. was_pressure and time_to_throw are participation-only, ... | GOOD for the pbp proxies (window-capped); participation-derived true p... |
| 37 | `coaching_regime_continuity_flag` | coaching | LOW | MEDIUM. Not a directional edge. Its value is as a GATE on every traili... | The fact is public and priced. The question is whether ... | Known before the season or when the change is reported; stable... | PARTIAL. Head-coach continuity IS reconstructable by comparing games.c... |
| 38 | `defense_red_zone_tendency` | defensive_matchup | LOW | MEDIUM. Notable because it links two market families that are usually ... | MEDIUM. Red-zone defence rates are small-sample and not... | In-season, nightly after each game day ('on a nightly basis af... | GOOD, WINDOW-CAPPED. Keyed by game_id/week so a prior-games-only aggre... |
| 39 | `roster_transaction_event` | intelligence | MEDIUM | MEDIUM. Headline transactions are priced instantly. The plausible resi... | HIGH for notable moves, lower for elevations. | When transacted; elevations are typically announced the day be... | PARTIAL. nflverse rosters and weekly rosters refresh daily (roster_wee... |
| 40 | `pass_rate_over_expectation` | coaching | MEDIUM | MEDIUM. This is one of the best-known metrics in public football analy... | HIGH on levels. A book setting a QB's attempts line is ... | Available in-season from prior-week pbp, nightly after each ga... | GOOD, with one caveat that must be stated: `xpass` is produced by a mo... |
| 41 | `neutral_script_pace` | game_script | MEDIUM | MEDIUM. A standard public adjustment, so partly priced. Where it may a... | MEDIUM-HIGH. | In-season, nightly after each game day ('on a nightly basis af... | GOOD, WINDOW-CAPPED. Keyed by game_id/week so a prior-games-only aggre... |
| 42 | `play_action_rate` | film | LOW | MEDIUM. Well covered in public analysis, so partly priced; more useful... | MEDIUM-HIGH. | Documented as in-season: 'charted within 48 hours following ea... | GOOD 2022-2025, window-capped; current season UNVERIFIED. |
| 43 | `carry_share_trend` | usage | MEDIUM | MEDIUM. Carry share is the most watched NFL usage metric in existence.... | HIGH. Rush-attempt and rushing-yards lines move on carr... | In-season, nightly after each game day ('on a nightly basis af... | GOOD, WINDOW-CAPPED. Keyed by game_id/week so a prior-games-only aggre... |
| 44 | `target_share_trend` | usage | MEDIUM | MEDIUM-LOW as a level; MEDIUM as a change. Target share is the single ... | HIGH on levels. | In-season, nightly after each game day ('on a nightly basis af... | GOOD, WINDOW-CAPPED. Keyed by game_id/week so a prior-games-only aggre... |
| 45 | `team_play_volume_expectation` | game_script | MEDIUM | MEDIUM-LOW as a standalone edge. ITS REAL IMPORTANCE IS STRUCTURAL, NO... | HIGH. Pace is central to how totals are set. | Trailing pace from prior-week pbp nightly; expected pace also ... | GOOD for the pace component (pbp timestamps, drive and series fields a... |
| 46 | `cb_shadow_assignment` | defensive_matchup | LOW | POTENTIALLY HIGH, UNREACHABLE. Included deliberately so the gap is rec... | Books using licensed charting price this. Full Count ca... | NOT ESTABLISHED. Alignment-level assignment requires either tr... | NOT ESTABLISHED. Cannot be reconstructed from any source verified in t... |
| 47 | `dropbacks_per_game_trend` | usage | MEDIUM | LOW-MEDIUM. Dropback volume is the most obvious input to a passing lin... | HIGH. | In-season, nightly after each game day ('on a nightly basis af... | GOOD, WINDOW-CAPPED. Keyed by game_id/week so a prior-games-only aggre... |
| 48 | `role_consolidation_after_repeated_losses` | coaching | LOW | LOW-MEDIUM, AND RANKED LOW DELIBERATELY. The causal story is attractiv... | UNKNOWN; probably not an explicit pricing input, which ... | The team's situation is known from the standings continuously;... | POOR IN PRACTICE, FOR A SAMPLE REASON RATHER THAN A DATA REASON. The p... |
| 49 | `contract_incentive_exposure` | intelligence | LOW | LOW-MEDIUM, AND DELIBERATELY NARROW. THERE IS NO 'CONTRACT YEAR MOTIVA... | UNKNOWN. Rarely discussed as a pricing input, which is ... | Contract terms are known when reported and persist; proximity ... | PARTIAL. nflverse contracts (OverTheCap-derived; historical_contracts.... |
| 50 | `precipitation_and_temperature_forecast` | environment | LOW | LOW-MEDIUM. Heavy precipitation games are rare, and when they occur th... | HIGH in the rare extreme cases that matter. | Same vintages as wind — seven days out, refreshed continuously... | ONLY BY PROSPECTIVE FORECAST ARCHIVAL, for exactly the reason given un... |
| 51 | `kickoff_slot_and_primetime` | schedule | LOW | LOW AS FOOTBALL, MEDIUM AS MARKET STRUCTURE. The interesting hypothesi... | The slot itself is fully known; the claim is about effi... | Known when the schedule is published; flex scheduling can chan... | GOOD. games.csv weekday and gametime are verified present (e.g. 2026_0... |
| 52 | `team_red_zone_trips_expectation` | scoring | MEDIUM (deliberately NOT HIGH — see 2.6) | LOW FOR THE MARKET VERSION — it IS the market, and ranking it as a dis... | ESSENTIALLY FULLY EFFICIENT for the implied total. This... | Two vintages. A market-derived version (implied team total fro... | SPLIT AND THIS SPLIT IS LOAD-BEARING. The pbp-derived version reconstr... |
| 53 | `roof_and_surface_state` | environment | MEDIUM | LOW AS A STANDALONE SIGNAL, ESSENTIAL AS A GATE. Its real function is ... | FULLY PRICED. Everyone knows which stadiums have roofs. | Static per venue for fixed roofs and known from the schedule; ... | GOOD FOR THE STATIC PART, POOR FOR THE DECISION. nflverse games.csv ca... |
| 54 | `opponent_position_group_concession` | defensive_matchup | LOW | LOW. THIS ENTRY EXISTS TO BE RANKED LOW ON PURPOSE. It is the closest ... | HIGH. Positional concession rates are the most widely c... | In-season, nightly after each game day ('on a nightly basis af... | GOOD, WINDOW-CAPPED. Keyed by game_id/week so a prior-games-only aggre... |
| 55 | `travel_and_neutral_site_flag` | schedule | MEDIUM | LOW. Public and priced. Its genuine use is to flag games where every t... | FULLY PRICED. | Known when the schedule is published. | EXCELLENT. VERIFIED LIVE: games.csv carries a `location` field valued ... |
| 56 | `days_rest_differential` | schedule | MEDIUM | LOW. Known months in advance to everyone, therefore priced for months.... | FULLY PRICED. | Known as soon as the schedule is published, i.e. months ahead.... | EXCELLENT — the best in this inventory. Fully determined by the schedu... |

### 2.6 NOTHING IN THIS INVENTORY IS RATED HIGH PRIOR CONFIDENCE

Of 56 signals: **0 HIGH, 27 MEDIUM-flavoured, 29 LOW-flavoured.** That is
deliberate and it is the rule the brief imposes: **NOTHING may be HIGH on
published-literature, film intuition, coach quote, or plausibility grounds
alone**, and there is no Full Count NFL evidence yet that could warrant a HIGH on
any other grounds.

One entry tempted a HIGH and was demoted on review.
`team_red_zone_trips_expectation` rests on an arithmetic necessity — touchdowns
require red-zone possessions — but **a definitional truth is not evidence about a
signal's usefulness**, and the signal's market-derived component *is* the price.
It is MEDIUM, with the reason recorded in its own field.

No signal anywhere in this document is called **proven**, **established**, or
**validated**, because no Full Count evidence warrants that language for any of
them.

**Family counts:** usage 10, film 9, intelligence 8, scoring 7, coaching 6,
defensive_matchup 6, environment 3, schedule 3, market 2, game_script 2.

### 2.3 FIELD 4 vs FIELD 5 — THE ASYMMETRY THIS WHOLE MISSION EXISTS FOR

Field 4 (`point_in_time`) is **when a signal is available live**. Field 5
(`historical_point_in_time_reconstructability`) is **whether it can later be
reconstructed as it was ACTUALLY KNOWABLE at the declared cutoff, without
hindsight or leakage**. They are different questions and they dissociate hard.

**MANY OF THE HIGHEST-RANKED SIGNALS SCORE WELL ON FIELD 4 AND BADLY ON FIELD 5.
THAT ASYMMETRY IS EXACTLY WHY NFL-01'S PROSPECTIVE ARCHIVAL EXISTS.** The
signals with the most marginal information — inactive-driven role reallocation,
coach statements, beat-reporter practice observations, contradiction states,
weather forecast vintages, sportsbook line movement — are all available live and
**none of them can be measured retrospectively unless they were archived on the
day**. This is not a new discovery; it is the NFL recurrence of a documented MLB
condition. `measure_signals.py` names four MLB signals (`bvp_ops`,
`team_total_move`, `team_total_open`, `money_ticket_split`) as permanently
forward-only "live market/matchup-history aggregates with no reconstructable
historical archive", and its own opening section records that the substrate for
measuring seventeen signals "was being written and thrown away". NFL is being
built so that does not happen again.

**There is also a rarer inverse asymmetry, and it is a genuinely important
finding of this pass:** the nflverse participation dataset scores *excellently*
on field 5 and is *useless* on field 4. It is published only after the
post-season completes — verbatim from its documentation: *"provided after all
post-season games are completed. It does not update during the season!"* —
verified live on 2026-09-11 by `pbp_participation_2026.csv` returning **404**
while `pbp_participation_2025.csv` returned **200 (49,094,943 bytes)**.
Everything mechanistic about coverage, personnel and route participation lives
in that file. **So a backtest of a past season can read coverage scheme, and the
live pipeline this season cannot.** Worse, the trap is silent: a naive engine
that joins current-season participation into a 2026 backtest would be reading a
file that did not exist on the prediction date — pure lookahead. Any future
implementation must gate participation to `season < current_season`.

### 2.4 OPPORTUNITY DOMINATES EFFICIENCY IN NFL — AND WHAT THAT DOES NOT MEAN

**STATED EXPLICITLY, AS REQUIRED: OPPORTUNITY DOMINATES EFFICIENCY IN NFL as a
structural modelling prior.** Carries, targets, routes, dropbacks, red-zone
snaps and goal-line touches are allocated by design; per-touch efficiency is
high-variance and substantially a function of blocking, scheme and defensive
front. A model that projects yards without an explicit opportunity term is
mis-specified.

**BUT THAT IS NOT EVIDENCE THAT ANY PARTICULAR OPPORTUNITY SIGNAL PREDICTS
BEYOND MARKET PRICE. NOTHING BECOMES PREDICTIVE MERELY BECAUSE THE CAUSAL STORY
IS EXCELLENT.** This is not a rhetorical caution. Full Count's MLB side fitted
its category weights against 41,000 rows and discovered that `skill` — the
category holding season-level power and contact stats — came back **negative**
for batters (`skill * -0.09` at `generate_picks.py:1572`), with the in-code
explanation that those stats "are largely already priced in by the market
itself". The most causally obvious inputs were the ones the market had already
consumed. Carry share and target share are the NFL equivalents, and they are
ranked 43rd and 44th here for exactly that reason.

### 2.5 THE SCORING / TOUCHDOWN-OPPORTUNITY FAMILY IS FIRST-CLASS AND V1

Seven signals form the scoring family: `goal_line_carry_share_change` (rank 2),
`end_zone_target_share` (6), `designed_qb_red_zone_run_rate` (7),
`red_zone_target_share` (11), `player_share_of_team_td_opportunity` (15),
`red_zone_route_participation` (21), `defense_red_zone_tendency` (38), with
`team_red_zone_trips_expectation` (52) as the market-derived team quantity.

**THE ARCHITECTURAL REQUIREMENT, STATED PLAINLY: THE FUTURE NFL SYSTEM MUST
DERIVE MULTIPLE TOUCHDOWN WAGER EXPRESSIONS FROM ONE UNDERLYING
SCORING-OPPORTUNITY MODEL. TOUCHDOWNS MUST NOT BE MODELLED AS "SEASON TD RATE x
GENERIC PROJECTION".**

FanDuel's observed NFL menu makes the case concrete. From the Phase A archive,
the following are all *different wagers on the same underlying scoring process*:
`ANY_TIME_TOUCHDOWN_SCORER`, `FIRST_TOUCHDOWN_SCORER`,
`LAST_TOUCHDOWN_SCORER`, `ANYTIME_1ST_HALF_TD_SCORER`,
`ANYTIME_1ST_QTR/2ND_QTR/3RD_QTR/4TH_QTR_TD_SCORER`,
`TO_SCORE_2+_TOUCHDOWNS`, `TO_SCORE_3+_TOUCHDOWNS`, `TO_SCORE_4+_TOUCHDOWNS`,
`EITHER_PLAYER_-_TO_SCORE_2+_TOUCHDOWNS`, `X_HOME_TEAM_TD_SCORER`,
`1ST_TEAM_TOUCHDOWN_SCORER`, `TOTAL_TOUCHDOWNS_-_HOME_TEAM`. Pricing those
twelve independently guarantees internal incoherence. Deriving them from one
opportunity model does not.

The decomposition the family implies is:
`team scoring opportunity` (red-zone and goal-to-go possessions, itself driven
by implied team total) x `player share of that opportunity` (goal-line carries,
end-zone targets, red-zone snaps and routes) x `conversion` (which is the part
most contaminated by small samples and most likely already priced). Full Count's
MLB side has the direct precedent in `prop_probability.py`'s plate-appearance
outcome distribution and in `test_pa_opportunity_model.py` /
`test_opportunity_decomposition.py` / `test_residual_opportunity_decomposition.py`.

**The honest caveat:** `end_zone_target_share` has the tightest mechanism in the
family and the **worst sample**. A team may produce only a handful of end-zone
targets across several games. A strong mechanism meeting a sample too small to
measure it is not a signal yet; it is a hypothesis with a good story, which is
the precise failure mode that retracted two MLB findings in September 2026.


---

## 3. MARKETS — BASE RATES, TYPICAL LINES, SETTLEMENT NUANCE

### 3.1 WHY BASE RATES LEAD THIS SECTION

**CROSS-MARKET BASE-RATE SEPARATION IS EXACTLY THE TRAP THAT MADE MLB'S POOLED
AUC LOOK STRONG WHILE WITHIN-MARKET SKILL WAS NULL.** MLB's pooled cross-market
AUC of 0.748 [0.721, 0.777] was base-rate separation; within-market ranking skill
was 0.492 [0.461, 0.521] — a coin flip. The mechanism is arithmetic: if a model
does nothing but know that easy markets are easy, pooling produces a strong-looking
AUC.

The NFL inventory below contains a **5.0%** market (2+ touchdowns) and a
**85.7%** market (1+ field goal made) and a **71.8%** market (over 2.5
receptions). **Any future NFL evaluation that pools those will reproduce the fake
0.748.** `backtest/signals.py` design decision 4 already enforces the remedy
("SEGMENT BY PROP TYPE ... because a signal that looks predictive is often just
tracking which prop types are easier to hit"); NFL must inherit it unchanged.

### 3.2 PROVENANCE OF THESE BASE RATES


All base rates below were COMPUTED IN THIS PASS from nflverse stats_player_week_2025.csv (18,540 regular-season player-weeks) and play_by_play_2025.csv (46,452 regular-season plays, 272 games), not quoted from memory. Each is stated with its conditioning population, because an unconditional NFL base rate is meaningless: the rate depends entirely on which players a sportsbook chooses to post a line on.

No base rate below is quoted from memory. Each was computed in this pass. The
commands and populations are reproducible from the two named nflverse assets.

**Every rate is stated with its conditioning population, because an
unconditional NFL base rate is meaningless.** A sportsbook does not post a
receiving-yards line on every player who takes a snap; it posts on players it
expects to be involved. The correct denominator is the book's posting pool, which
this pass approximated with opportunity floors (>=15 pass attempts, >=8 carries,
>=3 targets, >=20 defensive snaps) and said so rather than pretending to know it.

### 3.3 THE SEVENTEEN REQUIRED MARKETS


#### passing yards

- **Base rate:** Pool = QB-games with >=15 attempts (n=537, 2025 REG). Over 199.5: 60.9%. Over 224.5: 47.9%. Over 249.5: 36.1%. Over 274.5: 23.1%.
- **Typical line:** PLAYER_X_PASSING_YARDS_LOW/MEDIUM/HIGH observed live; central lines cluster near the 47-52% region, i.e. roughly 215-235 yards for a starting QB.
- **Settlement nuance:** Sacks reduce passing yards (yardage lost on a sack is charged to the team, not added to passing yards). Settles on official gamebook passing yards; a lateral behind the line can alter attribution. FanDuel additionally posts PLAYER_X_ALT_PASSING_YARDS_LOW/MEDIUM/HIGH ladders whose settlement is the same quantity at different thresholds.

#### pass completions

- **Base rate:** Pool = QB-games with >=15 attempts (n=537). Over 17.5: 67.4%. Over 19.5: 51.8%. Over 21.5: 40.0%. Over 23.5: 27.0%.
- **Typical line:** Near-coinflip at roughly 19.5-20.5 completions.
- **Settlement nuance:** NOT OBSERVED on FanDuel in the 139-marketType sample captured 2026-09-11. Settlement, where offered, is official completions; a completion nullified by penalty does not count.

#### pass attempts

- **Base rate:** Pool = QB-games with >=15 attempts (n=537). Over 29.5: 57.0%. Over 31.5: 46.9%. Over 33.5: 38.5%. Over 35.5: 29.1%.
- **Typical line:** Near-coinflip at roughly 31.5-32.5 attempts.
- **Settlement nuance:** NOT OBSERVED on FanDuel in the sample. Sacks are NOT attempts; spikes and throwaways ARE. Plays nullified by penalty do not count.

#### passing touchdowns

- **Base rate:** Pool = QB-games with >=15 attempts (n=537). Over 0.5: 78.2%. Over 1.5: 44.9%. Over 2.5: 18.1%.
- **Typical line:** 1.5 is the near-coinflip line. PLAYER_X_PASSING_TOUCHDOWNS_LOW/MEDIUM/HIGH and ALT variants observed live.
- **Settlement nuance:** A passing TD requires the pass to be caught in the end zone or the receiver to reach it; a two-point conversion pass is NOT a passing touchdown for settlement purposes at most books, which is a real settlement trap and should be verified against FanDuel's own rules rather than assumed.

#### interceptions thrown

- **Base rate:** Pool = QB-games with >=15 attempts (n=537). Over 0.5 (1+ INT): 48.8%. Over 1.5: 14.5%.
- **Typical line:** 0.5 is almost exactly a coinflip, which makes this the most base-rate-neutral market in the required set.
- **Settlement nuance:** NOT OBSERVED on FanDuel in the sample. Interceptions on plays nullified by penalty do not count. A pass tipped by a receiver still counts against the QB.

#### rushing yards

- **Base rate:** Pool = RB-games with >=8 carries (n=746). Over 39.5: 70.8%. Over 49.5: 56.4%. Over 59.5: 44.1%. Over 69.5: 31.5%. Over 79.5: 23.1%.
- **Typical line:** Near-coinflip around 52-56 yards for a starting back. PLAYER_X_RUSHING_YARDS_LOW/MEDIUM/HIGH plus ALT ladders observed live.
- **Settlement nuance:** Kneel-downs count as rushing attempts for negative yardage and can materially hurt an under-friendly QB rushing line. Scrambles count as rushing yards; designed QB runs likewise.

#### rush attempts

- **Base rate:** Pool = RB-games with >=8 carries (n=746). Over 11.5: 62.1%. Over 13.5: 46.4%. Over 15.5: 33.0%. Over 17.5: 22.4%.
- **Typical line:** Near-coinflip at roughly 13.5 carries.
- **Settlement nuance:** NOT OBSERVED on FanDuel in the sample. Attempts nullified by penalty do not count.

#### receiving yards

- **Base rate:** Pool = receiver-games with >=3 targets (n=2,488). Over 29.5: 58.0%. Over 39.5: 43.1%. Over 49.5: 31.6%. Over 59.5: 23.4%. Over 69.5: 17.1%.
- **Typical line:** Near-coinflip near 35-37 yards over the whole >=3-target pool; higher for a team's primary receiver. PLAYER_X_RECEIVING_YARDS_LOW/MEDIUM/HIGH and ALT ladders observed live.
- **Settlement nuance:** Settles on official receiving yards including yards after catch. Yards on a play nullified by penalty do not count. A completed pass behind the line still counts as a reception and receiving yards.

#### receptions

- **Base rate:** Pool = receiver-games with >=3 targets (n=2,488). Over 2.5: 71.8%. Over 3.5: 46.6%. Over 4.5: 29.5%. Over 5.5: 18.0%. Over 6.5: 10.6%.
- **Typical line:** 3.5 is near-coinflip across the pool. PLAYER_X_RECEPTIONS_LOW/MEDIUM/HIGH and PLAYER_X_ALT_RECEPTIONS_LOW/MEDIUM/HIGH observed live.
- **Settlement nuance:** A reception requires a completed catch; a two-point conversion catch typically does NOT count. Zero-yard and negative-yard catches DO count, which is why a manufactured-touch receiver has a high reception floor.

#### anytime touchdown

- **Base rate:** Pool = offensive players with >=3 combined carries+targets (n=3,598): 27.3%. Pool = RBs with >=8 carries (n=746): 44.5%. Pool = receivers with >=3 targets (n=2,488): 28.1%.
- **Typical line:** ANY_TIME_TOUCHDOWN_SCORER observed live, along with FIRST_TOUCHDOWN_SCORER, LAST_TOUCHDOWN_SCORER and ANYTIME_1ST_HALF/1ST_QTR/2ND_QTR/3RD_QTR/4TH_QTR_TD_SCORER.
- **Settlement nuance:** Includes rushing AND receiving touchdowns; typically also includes a defensive or return TD by that player at most books, which must be verified against FanDuel's rules rather than assumed. A two-point conversion is NOT a touchdown.

#### 2+ touchdowns

- **Base rate:** Pool with >=3 combined opportunities (n=3,598): 5.0%. Pool = RBs with >=8 carries (n=746): 12.1%. 3+ TD over the broad pool: 0.8%.
- **Typical line:** TO_SCORE_2+_TOUCHDOWNS, TO_SCORE_3+_TOUCHDOWNS, TO_SCORE_4+_TOUCHDOWNS and EITHER_PLAYER_-_TO_SCORE_2+_TOUCHDOWNS observed live.
- **Settlement nuance:** THIS IS THE MARKET THAT MAKES THE BASE-RATE TRAP CONCRETE. Its 5.0% event rate sits beside receptions-over-2.5 at 71.8% in the same inventory. Pooling those two in any AUC evaluation reproduces MLB's pooled 0.748 [0.721, 0.777] as pure base-rate separation with zero within-market ranking skill.

#### longest reception

- **Base rate:** Computed from 2025 pbp, pool = receiver-games with >=2 receptions (n=2,641). Over 14.5: 57.6%. Over 17.5: 45.5%. Over 19.5: 38.9%. Over 22.5: 30.8%. Over 24.5: 25.9%.
- **Typical line:** Near-coinflip around 17-18 yards. NOT OBSERVED as a FanDuel market in the sample; the nearest observed expressions are PLAYERS_WITH_10+/15+/20+/30+_YARDS_RECEPTION.
- **Settlement nuance:** A single long reception settles it, so it is a pure tail bet and is almost uncorrelated with total receptions. It is the market where aDOT and air-yard-share information should matter most and mean-based projection matters least.

#### longest rush

- **Base rate:** Computed from 2025 pbp, pool = rusher-games with >=8 carries (n=747). Over 9.5: 72.6%. Over 11.5: 60.4%. Over 13.5: 47.0%. Over 15.5: 37.8%. Over 17.5: 31.5%. Over 19.5: 25.4%.
- **Typical line:** Near-coinflip around 13.5 yards. NOT OBSERVED as a FanDuel market in the sample.
- **Settlement nuance:** Tail bet on a single carry. A long run negated by penalty does not count. Kneel-downs cannot lower it but do consume a carry.

#### tackles + assists

- **Base rate:** Computed from 2025 pbp solo_tackle_1/2 and assist_tackle_1..4 player ids. Pool = defender-games with >=1 combined (n=11,025): over 2.5: 49.1%; over 3.5: 35.1%; over 4.5: 25.1%; over 5.5: 17.1%; over 6.5: 11.4%; over 7.5: 7.7%; over 8.5: 5.0%. Restricting to defender-games with >=4 combined (n=3,872), over 5.5 = 48.7%, which is closer to how a book would select its posting pool.
- **Typical line:** NOT OBSERVED ON FANDUEL. Absent from all 139 marketTypes in the 262-payload archive, absent from the event's own 11-tab layout.tabs list, and 12 guessed slugs all returned the 8-market default. Reported honestly as NOT OFFERED ON THE SAMPLED SLATE; whether it appears later is UNKNOWN.
- **Settlement nuance:** MAJOR SETTLEMENT NUANCE. Tackle statistics are NOT officially standardised the way yardage is: solo and assisted tackles are assigned by scorers and differ between the official gamebook and third-party sources. Any tackles market must be settled against a NAMED source, and grading it from nflfastR pbp would be grading against a different source than the book uses. That is precisely the class of bug Full Count's MLB grading_sources.py exists to control.

#### sacks (to record 1+)

- **Base rate:** Computed from 2025 pbp: 998 player-games with >=1.0 full sack across 272 games, i.e. 3.67 per game league-wide. Against a denominator of defender-games with >=20 defensive snaps (7,809 from snap_counts_2025) the implied rate is 12.8%; restricted to front-seven positions with >=20 snaps (4,813) it is 20.7%. These are ratios of independently computed counts, not a row-level join (gsis and pfr id systems differ), so they are rate ESTIMATES.
- **Typical line:** TO_RECORD_1+_SACK observed live, plus a 'To Record a Sack Parlay Builder' tab title in the NFL root page.
- **Settlement nuance:** HALF SACKS ARE THE CENTRAL NUANCE. Two players splitting a sack are each credited 0.5, which at most books does NOT satisfy a 1+ sack market. Any grading must decide and document whether 0.5 counts, and 17.6% of the sack player-games in this 2025 sample were half-sacks only (1,211 player-games with >=0.5 versus 998 with >=1.0).

#### kicking points

- **Base rate:** Computed from 2025: pool = kicker-games with any score (n=530). Over 6.5: 58.9%. Over 7.5: 47.4%. Over 8.5: 36.0%. Over 9.5: 27.5%.
- **Typical line:** Near-coinflip around 7.5-8 points. On FanDuel these appear as TEAM-level selections inside GAME_SPECIALS_-_KICKING with verified runner names 'BUF Bills 7+ Total Kicking Points', 'BUF Bills 10+ Total Kicking Points', 'BUF Bills 12+ Total Kicking Points' — NOT as an individual kicker market in the sampled slate.
- **Settlement nuance:** Kicking points = 3 per field goal + 1 per extra point. A missed extra point or a blocked kick costs a point. A TEAM total-kicking-points market also captures a second kicker after an injury, whereas a player market would not — a real difference in what is being bet.

#### field goals made

- **Base rate:** Computed from 2025: pool = kicker-games with any score (n=530). Over 0.5: 85.7%. Over 1.5: 54.2%. Over 2.5: 23.2%. Over 3.5: 9.4%.
- **Typical line:** 1.5 is near-coinflip. Observed on FanDuel as team selections 'BUF Bills 2+ Made Field Goals', '3+', '4+' inside GAME_SPECIALS_-_KICKING, plus TEAM_TO_HAVE_1ST_FIELD_GOAL as a separate market.
- **Settlement nuance:** A field goal negated by penalty and re-kicked counts once. A field goal attempt on the final play of a half still counts. The 2+ made-FG market is strongly NEGATIVELY related to the same team's touchdown markets: red-zone trips convert to one or the other, which is a within-game negative correlation correlation.py's label scheme would classify explicitly.

### 3.4 SEVEN OF THE SEVENTEEN REQUIRED MARKETS WERE NOT OBSERVED ON FANDUEL

This is a material finding and it is reported rather than smoothed over. The
Phase A archive for 2026-09-11 holds **262 FanDuel artifacts across 29 events and
8 tab slugs, containing 139 distinct `marketType` values**. Of the seventeen
required markets, the following were **NOT OBSERVED anywhere in that sample**:

| required market | observed on FanDuel? | nearest observed expression |
|---|---|---|
| pass completions | **NO** | none |
| pass attempts | **NO** | none |
| interceptions thrown | **NO** | none |
| rush attempts | **NO** | none |
| longest reception | **NO** | `PLAYERS_WITH_10+/15+/20+/30+_YARDS_RECEPTION` |
| longest rush | **NO** | none |
| tackles + assists | **NO** | none |
| kicking points | team-level only | `GAME_SPECIALS_-_KICKING` runners "BUF Bills 7+/10+/12+ Total Kicking Points" |
| field goals made | team-level only | `GAME_SPECIALS_-_KICKING` runners "BUF Bills 2+/3+/4+ Made Field Goals"; plus `TEAM_TO_HAVE_1ST_FIELD_GOAL` |

**On tackles + assists specifically, as required:** it was NOT observed, and this
pass went beyond the archive to check. Twelve additional candidate tab slugs were
probed live against event 35599... (event 35596960, SF @ LAR) on 2026-09-11 —
`player-props`, `defensive-props`, `defense-props`, `tackles-props`, `rb-props`,
`qb-props`, `wr-props`, `nfl-player-specials`, `player-specials`, `big-kick`,
`kicking-props`, `alternate-props` — and **every one silently returned the same
8-market default payload** (`4TH_QUARTER_HANDICAP`, `4TH_QUARTER_TOTAL`,
`4TH_QUARTER_WINNER`, `4TH_QUARTER_WINNER_THREE_WAY`,
`ANYTIME_4TH_QTR_TD_SCORER`, `MATCH_HANDICAP_(2-WAY)`, `MONEY_LINE`,
`TOTAL_POINTS_(OVER/UNDER)`), confirming the mission's prior finding that an
unrecognised slug does not error.

**A better discovery method was found and is reported as an operational
improvement:** the event payload publishes its **own authoritative tab list** at
`layout.tabs`, as `{id: {"id":.., "title":..}}`. For event 35596960 on
2026-09-11 that list was exactly eleven tabs — *Popular, 4th Quarter, 2nd Half,
Scoring, Passing Props, Receiving Props, Rushing Props, TD Scorer Props, Live
SGP, Quick Bets, Drive SGP*. **No defensive, tackles, D/ST or Game Specials tab
appears in that event's own list**, and there is no tackles market in it. Note
also that **tabs are PER-EVENT**: `d-st` and `game-specials` returned distinct
(non-default) payloads for all 29 archived events, yet neither appears in this
event's published tab list — so slug-guessing and layout-reading are both
partial, and a robust capture should union them while recording which method
found each tab.

**Honest verdict: FanDuel did not offer a player tackles+assists market on the
sampled 2026 Week 1 slate. Whether it appears on later slates, on other events,
or via a discovery path not tried here is UNKNOWN.** The market is retained in
this inventory because the brief forbids dropping it, and because the base rates
computed above are what a future implementation would need on the day it appears.

### 3.5 THE TACKLES SETTLEMENT WARNING

Tackle statistics are **not officially standardised the way yardage is**. Solo
and assisted tackles are assigned by human scorers and differ between the
official gamebook and third-party sources. Grading a tackles market from
nflfastR play-by-play would grade against a **different source than the book
settles on**. Full Count's MLB side already has dedicated machinery for exactly
this hazard (`grading_sources.py`, `test_grade_results.py`), and any NFL tackles
market must name its settlement source before a single prediction is made.


---

## A. SOURCE AUDIT

Every source below was probed live on **2026-09-11** with an honest,
self-identifying User-Agent (`FullCount-NFL-research/0.1 (analytics research;
contact ...)`). **LESSON APPLIED THROUGHOUT: A 403 GATHERED UNDER A UA YOU WOULD
NOT SHIP IS NOT EVIDENCE A SOURCE IS UNAVAILABLE.** An earlier probe in this
mission wrongly concluded ESPN was blocking automation; that was a bare
`Mozilla/5.0` artefact.

### A.0 THREE THINGS THAT MUST NEVER BE CONFLATED

This audit keeps them strictly separate, and the reader should too:

1. **BLOCKED BY THIS CONTAINER'S EGRESS POLICY** — an *environment* fact about
   this sandbox. `api.github.com` is in this category. It says nothing about
   whether the source is available to Full Count in production. Note that the
   nflverse **release-asset download URLs** on `github.com` returned 200
   repeatedly in this same pass, so even for GitHub the two are different.
2. **THE ORIGIN REFUSES AUTOMATION** — a *server behaviour* fact.
   `pro-football-reference.com` is in this category: 403 with a real HTML error
   body under two UAs.
3. **REQUIRES AUTHENTICATION** — `nextgenstats.nfl.com/api/...` is in this
   category: HTTP 401 under two UAs.

And a fourth, which this pass added and which is the most consequential:

4. **TECHNICALLY REACHABLE BUT TERMS-PROHIBITED.** ESPN's endpoints return 200
   to an honest UA and the Disney Terms of Use nonetheless prohibit automated
   extraction for dataset building. **REACHABILITY IS NOT PERMISSION.**

### A.1 SOURCE TABLE

| source | coverage | granularity | historical depth | live latency | point-in-time reconstructable? | cost | access method | terms_status | fragility |
|---|---|---|---|---|---|---|---|---|---|
| **nflfastR pbp** (`play_by_play_<yr>.csv`) | every play, all games | play, 372 columns | 1999-present | "nightly after each game day (and additionally at specific points on game days)"; 2026 asset held only the Wed opener on 9/11 | **YES if the aggregation window is capped**; the hazard is the window, not the file | free | bulk CSV, 200 verified (2026: 333 KB; 2025: 97.9 MB) | UNKNOWN-REQUIRES-REVIEW (no nflverse umbrella licence found) | LOW-MED; **asset naming HAS changed** (`player_stats_2026.csv` 404s, `stats_player_week_2026.csv` 200s) |
| **nflfastR pbp `temp`/`wind`/`weather`** | same | game | same | **post-game only** | **NO — LOOKAHEAD.** Verified: 0 of 272 2026 rows in `games.csv` have temp or wind populated | free | same | same | **CRITICAL FLAG** |
| **nflfastR pbp `spread_line`/`total_line`** | same | game | same | live, mutable | **NO — becomes the CLOSING line** | free | same | same | **CRITICAL FLAG** |
| **nflverse participation** | every play | play; personnel, formation, coverage, route, pressure, time_to_throw | 2016-2025 | **none in-season.** Verbatim: *"provided after all post-season games are completed. It does not update during the season!"* Verified: 2026 = 404, 2025 = 200 (49.1 MB) | **YES for past seasons, and a LEAKAGE TRAP for the current one** | free | bulk CSV | UNKNOWN-REQUIRES-REVIEW | **HIGH**; annual single drop; `ngs_air_yards` already NA from 2024 on |
| **FTN charting** (`ftn_charting_<yr>.csv`) | every play | play; is_motion, is_play_action, is_screen_pass, is_rpo, is_no_huddle, qb_location, n_defense_box, is_catchable_ball, is_contested_ball, is_interception_worthy, is_throw_away, is_qb_out_of_pocket | **2022 onwards only** | documented "charted within 48 hours following each game", release updates 0/6/12/18 UTC in season — **but 2026 asset = 404 on 9/11** | YES for 2022-2025 | free | bulk CSV; 2025 = 200 (8.13 MB), 2024 = 200 (8.25 MB), 2026 = **404** | **PERMITTED WITH ATTRIBUTION — the only explicit licence found: CC-BY-SA 4.0, "attribution must be made to FTN Data via nflverse". SHARE-ALIKE obligations on derived published data: UNKNOWN-REQUIRES-REVIEW** | MED-HIGH |
| **Next Gen Stats via nflverse** (`ngs_passing/receiving/rushing.csv.gz`) | weekly player aggregates | week; avg_cushion, avg_separation, avg_intended_air_yards, percent_share_of_intended_air_yards, avg_yac_above_expectation | UNKNOWN first season (commonly cited 2016; **NOT verified**) | "every night (3-5 am ET) during the season"; **2026 week-1 partition already present** | YES, window-capped (single rolling file → explicit week filter mandatory) | free | bulk gz CSV; receiving 200 (981 KB), rushing 200 (324 KB), passing 200 (584 KB) | UNKNOWN-REQUIRES-REVIEW | MED — sole route; NGS API itself 401s |
| **NGS direct API** (`nextgenstats.nfl.com/api/...`) | — | — | — | — | — | unknown | **requires authentication** | UNKNOWN-REQUIRES-REVIEW | n/a |
| **nflverse snap counts** | offense/defense/ST snaps & pct | player-game | 2012+ commonly cited, **NOT verified** | "every day at 0, 6, 12, 18 UTC during the season"; 2026 Week-1 rows present 9/11 | YES, window-capped | free | bulk CSV, 200 (8.4 KB for 2026 wk1) | UNKNOWN-REQUIRES-REVIEW; **PFR-derived, and PFR itself 403s** | MED |
| **nflverse injuries** | weekly injury/practice report | **player-WEEK** (`report_status`, `practice_status`, `practice_primary/secondary_injury`) — **no Wed/Thu/Fri axis** | 2009-2024 documented | daily-ish | PARTIAL — week granularity only | free | bulk CSV, 200 (14.9 KB, real 2026 wk1 rows) | UNKNOWN-REQUIRES-REVIEW | **HIGH + DEMONSTRATED CONTRADICTION**: nflverse docs say verbatim *"Our data source died after the 2024 season. At the moment, there is no 2025 data"*, yet `injuries_2026.csv` returned 200 with genuine content. Documentation is not an availability oracle |
| **nflverse depth charts** | weekly depth chart | player-week | long, **span not verified** | "every day at 7AM UTC throughout the year" | **NO unless snapshotted** — mutable daily file | free | bulk CSV, 200, ~48 MB | UNKNOWN-REQUIRES-REVIEW | MED-HIGH |
| **nflverse rosters / weekly rosters / players** | roster state | player-week / player | long | daily 7AM UTC | **NO unless snapshotted**; transactions are the DIFF | free | 200 (`roster_weekly_2026.csv` 938 KB; `players.csv` 7.3 MB) | UNKNOWN-REQUIRES-REVIEW | MED |
| **nfldata `games.csv`** | schedule + context | game; `away_rest`, `home_rest`, `weekday`, `gametime`, `location`, `roof`, `surface`, `stadium`, **`away_coach`/`home_coach`**, `referee`, `spread_line`, `total_line` | 2006+ (coach cols added Feb 2020) | "every 5 minutes during the season" | **MIXED — the most dangerous file in the audit.** Immutable: rest, weekday, location, roof, surface, coach. Mutable: spread_line/total_line (→ closing). Post-game: temp, wind, referee (verified: 0/272 temp, 0/272 referee, 103/272 spread for 2026) | free | bulk CSV, 200 (2.18 MB) from both `raw.githubusercontent.com/nflverse/nfldata` and `habitatring.com` | UNKNOWN-REQUIRES-REVIEW | MED — one community CSV, no redundancy |
| **nflverse contracts** | OverTheCap-derived contracts | player-contract | UNKNOWN span | unknown | PARTIAL, mutable file | free | 200 (`historical_contracts.csv.gz`, 1.19 MB) | UNKNOWN-REQUIRES-REVIEW; OverTheCap terms **not reviewed** | MED-HIGH |
| **nflverse PFR advanced stats** | weekly advanced pass/rec/def | player-week | prior seasons only | **2026 = 404** for pass/rec/def on 9/11 | YES for past seasons | free | bulk CSV | UNKNOWN-REQUIRES-REVIEW | MED-HIGH |
| **ESPN `site.web.api.espn.com`** | scoreboard; league `/injuries` (~8.9 MB, carries own server `timestamp`); `/summary?event=` | game/team/player | unknown | near-real-time | **NO unless snapshotted** | free | **TECHNICALLY reachable, 200 with honest UA.** `summary` on a SCHEDULED event verified to carry `pickcenter` (DraftKings spread/total/ML), `predictor` (gameProjection), per-team `injuries`, `gameInfo.venue` with `grass` bool + city/state/zip, `odds`, `winprobability`, `againstTheSpread`, `standings`, `leaders`. On a COMPLETED event, `pickcenter`/`predictor`/`injuries` were ABSENT | **RESTRICTED-TO-PROHIBITED.** Disney ToU §2.B.x prohibits automated access/extraction "including ... for the purposes of creating or developing any AI Tool, data mining or web scraping or otherwise compiling ... any collection of data, data set or database" | MED — undocumented JSON shape |
| **Pro-Football-Reference direct** | — | — | — | — | — | free | **ORIGIN REFUSES AUTOMATION** — 403 with a real HTML error body under two UAs | UNKNOWN-REQUIRES-REVIEW | n/a |
| **api.weather.gov (NWS)** | US forecasts | hourly gridpoint, 7 days | forecasts not archived by Full Count | continuous | **ONLY IF FORECAST VINTAGES ARE ARCHIVED** | free | **documented public API.** Requires a UA ("A User Agent is required to identify your application"); rate limited but "allows a generous amount for typical use"; `/points/{lat},{lon}` → `/gridpoints/{office}/{x},{y}/forecast/hourly`; covers "the next seven days" | **PERMITTED — clearest basis in the audit: "All of the information presented via the API is intended to be open data, free to use for any purpose."** | LOW for the API; **blocked on a venue lat/lon table Full Count does not have** |
| **nfl.com / club sites (transcripts, inactives, transactions)** | official statements | article/video/transcript | current-facing | immediate | **NO — no corpus** | free to view | public page read | **PROHIBITED for systematic retrieval.** §1.3: "Systematic retrieval of data or other content from the Services ... to create or compile ... a collection, compilation, database, or directory, is prohibited absent our express prior written consent." §11 prohibits "spiders, robots ... to harvest or otherwise collect information ... for any commercial purpose" | HIGH |
| **YouTube (team/league channels, captions)** | press conference video | video/caption | channel archives | immediate | NO | free to view | **PROHIBITED** — ToS bar accessing the Service "using any automated means (such as robots, botnets or scrapers)" except search engines obeying robots.txt or with prior written permission, and bar downloading content | HIGH |
| **FanDuel `sbapi.nj.sportsbook.fanduel.com`** | NFL prices | market/selection | **ZERO before Phase A** | live | **ONLY via Full Count's own archive** | free | undocumented JSON API, already in Phase A | **UNKNOWN-REQUIRES-REVIEW — not reviewed in this pass, no legal conclusion offered** | MED-HIGH — **silent-wrong-payload failure mode: unknown tab slugs return an 8-market default rather than an error** |
| **PFF and other paywalled charting** | — | — | — | — | — | **paid** | out of scope | n/a — **EXCLUDED. No paywalled metric appears anywhere in this inventory presented as free** | n/a |

### A.2 EVERY SOURCE THAT CANNOT BE RECONSTRUCTED POINT-IN-TIME — FLAGGED

**A backtest that can see the future silently produces fake edge.** That is what
`backtest/engine.py`'s `PointInTime` class and `verify_no_lookahead()` exist to
prevent, and what `test_backtest_engine.py` and `test_backtest_provenance.py`
regression-test. The following NFL sources fail point-in-time reconstruction and
**must be archived prospectively or excluded from any backtest**:

1. **`temp` / `wind` / `weather` in pbp and `games.csv`** — realised, post-game.
   Verified: 0 of 272 2026 rows populated. **Using these is reading the future.**
2. **`spread_line` / `total_line` in `games.csv`** — one mutable field, refreshed
   every 5 minutes, ending as the closing line.
3. **All FanDuel prices** — no free historical archive exists.
4. **Depth charts** — mutable daily file.
5. **Rosters / weekly rosters** — mutable daily; transactions are only visible as
   day-over-day diffs.
6. **Contracts** — mutable rolling file.
7. **Official inactive lists** — no machine-readable source established at all.
8. **Daily practice trajectory (Wed/Thu/Fri)** — the historical file is weekly.
9. **Coach statements, press conferences, beat reports** — no corpus.
10. **Weather forecast vintages** — NWS serves the current forecast, not the
    forecast as of a past date.
11. **`referee`** in `games.csv` — post-game (0 of 272 populated for 2026).
12. **Current-season participation and FTN** — not that they are mutable, but
    that they **did not exist on the prediction date**; joining them into a
    same-season backtest is lookahead of the subtlest kind.

And one that is **not** a leakage risk but is a reproducibility risk worth
naming: **`xpass`/`pass_oe` model vintage drift.** These are model outputs, and a
value read today for a 2019 play is not necessarily the value that existed in
2019. That is the same class of problem `backtest/SCHEMA.md` solves for Full
Count's own code with `code_git_sha`, and it needs an analogous field for
third-party model versions.

---

## 4. COACHING / SCHEME / PLAY-CALLING REGIME — A FOUNDATIONAL CAUSAL LAYER

**Coaching matters structurally FAR more in NFL than in MLB.** An MLB manager
sets a batting order; an NFL staff designs every unit of opportunity that exists.
The causal chain this layer is researched against is:

```
COACHING / SCHEME / PLAY-CALLING REGIME
        -> personnel packages / formations / pace / pass-run choice
        -> designed player usage / role / opportunity
        -> matchup interaction
        -> player outcome distribution
```

**NO WEIGHT IS ASSIGNED TO COACHING ANYWHERE IN THIS DOCUMENT.** Assigning
"coaching = 20%" would repeat the exact hand-weighting mistake NFL is being built
to avoid, and the repo's own history shows why: the invented 35/25/15/15/10 split
survived for months, and when it was finally fitted against 41,000 rows the
answer was `matchup*0.04 + form*0.03 + env*0.20 + skill*-0.09 + context*0.64` —
nothing like the guess, with two signs reversed, and the fitting improved
held-out AUC in **one of fifteen markets**.

Coaching is researched here because **COACHES CREATE AND REALLOCATE
OPPORTUNITY**, and it is expressed as six admitted signals plus the usage and
scoring families it drives.

### 4.1 COACHING IDENTITY / AUTHORITY — THE LARGEST STRUCTURED-DATA GAP FOUND

**FINDING: the only coaching identity available in free structured NFL data is
HEAD COACH.** `nfldata/games.csv` carries `away_coach` ("Name of the head coach
of the away team") and `home_coach`, added February 2020; verified live with
**272 of 272 2026 rows populated**. **No dataset examined in this pass carries
offensive coordinator, defensive coordinator, or actual play-caller identity.**

That means the following concepts from the brief are **NOT historically
measurable from any free structured source located**: OC identity, DC identity,
**who actually calls the plays** (which is frequently neither coordinator),
midseason play-caller changes, coordinator changes, first-season-in-system,
inherited-vs-established roster. Third-party listings exist as web pages; their
terms are UNKNOWN-REQUIRES-REVIEW and they are not structured.

The consequences are concrete and they propagate through several signals:

- `score_dependent_play_calling_profile` (rank 17) can only be attributed to a
  **head coach**, not to the person who actually calls the plays.
- `role_consolidation_after_repeated_losses` (rank 48) is ranked near the bottom
  partly for this reason and partly for sample poverty.
- `coaching_regime_continuity_flag` (rank 37) can compute head-coach tenure
  honestly and must record coordinator continuity as `NOT_CHECKED`.
- `play_caller_change_flag` (rank 28) is admitted as a signal whose primary
  value is to **distrust every trailing average** for that offense, not to
  predict a direction.

### 4.2 OFFENSIVE PHILOSOPHY — WHAT IS AND IS NOT MEASURABLE

| concept | measurable? | how, and with what limit |
|---|---|---|
| pass rate over expectation | **YES, in-season** | pbp `xpass`/`pass_oe`; verified populated on 114/114 scrimmage plays of the 2026 opener |
| neutral pass rate | **YES** | pbp filtered on `score_differential` and `half_seconds_remaining` |
| pace / seconds per play | **YES** | pbp `fixed_drive`, `drive_play_count`, `play_clock`, clock fields |
| no-huddle | **YES** (two routes) | pbp `no_huddle`; FTN `is_no_huddle` |
| shotgun vs under center | **YES** | pbp `shotgun`; FTN `qb_location` |
| personnel grouping | **PRIOR SEASONS ONLY** | participation `offense_personnel`/`offense_formation` — not in-season |
| motion | **2022+, in-season UNVERIFIED** | FTN `is_motion`; 2026 asset 404 at probe time |
| play action | **2022+, same caveat** | FTN `is_play_action` |
| RPO | **2022+, same caveat** | FTN `is_rpo` |
| screen rate | **YES** (two routes) | FTN `is_screen_pass`; pbp `air_yards` proxy |
| deep-shot tendency | **YES** | pbp `air_yards` distribution; NGS `avg_intended_air_yards` |
| early-down aggression | **YES** | pbp `down`/`ydstogo` conditional rates |
| 4th-down aggressiveness | **YES** | pbp; relevant here ONLY where it changes opportunity (an extra series is an extra set of touches) |
| **opening-script tendencies** | **PARTIALLY, AND HONESTLY LIMITED** | pbp identifies the first N plays of the game, so a first-15 tendency is computable. What is NOT knowable is whether those plays were *scripted*; the script itself is not public. Recorded as a proxy, never as "the script" |

### 4.3 SITUATIONAL PLAY CALLING

All of leading / trailing / neutral / 3rd down / short yardage / two minute /
red zone / goal to go / inside 10, 5, 2 are **computable from pbp** using
`score_differential`, `down`, `ydstogo`, `yardline_100`, `goal_to_go` and the
clock fields — every one of which was verified present in
`play_by_play_2026.csv`. **The data is not the constraint here. SAMPLE IS.** A
team's inside-2 play calls across a season number in the low dozens.

### 4.4 PLAYER-USAGE PHILOSOPHY

Bell-cow vs committee, early-down RB, third-down RB, two-minute RB, goal-line RB,
TE deployment, manufactured touches, receiver target hierarchy, QB designed runs,
QB sneak usage, and role consolidation vs distribution after injury are all
**computable from pbp plus snap counts**, and they are the substance of signals
at ranks 2, 7, 8, 25, 32, 43, 44 and 48.

Two are **NOT** obtainable and are recorded as such rather than approximated:

- **"Designed first reads"** — no free source identifies which receiver was the
  intended first read on a play. The nearest legitimate proxies are FTN's
  `is_screen_pass` (a designed target by construction) and very low `air_yards`
  with immediate release. **A first-read signal must NOT be invented from target
  order or from film opinion.**
- **Hot-hand behaviour** — computable in principle from within-game carry
  sequences, but indistinguishable from game script without far more data than
  17 games a season provides.

### 4.5 MATCHUP ADAPTATION

How much a staff changes personnel and packages **by opponent** is the concept
with the widest gap between importance and availability. Measuring it requires
per-play personnel, which is **participation-only and therefore out of season**.
Defensive shadow philosophy requires per-play defender-to-receiver assignment,
which **no free source provides** (recorded in `unavailable` as
`per_play_defender_to_receiver_assignment`). Blitz philosophy and box-count
philosophy are measurable from participation (`number_of_pass_rushers`,
`defenders_in_box`) for **past seasons only**, with FTN's `n_defense_box` as an
in-season substitute if the current-season asset materialises.

### 4.6 INJURY REALLOCATION — THE HIGHEST-VALUE COACHING QUESTION

"What does **THIS** staff historically do when RB1 / WR1 / TE1 / OL / QB is out?
Does volume consolidate or fragment? Do replacements follow the depth chart or
package-specific function? Does nominal starter status translate to actual
routes, carries and snaps?"

This is answerable **in principle** from historical pbp + snap counts +
weekly injury designations, and it is the mechanism behind the #1-ranked signal.
Three limits are real and must be stated:

1. **Regime attribution is broken** by the coordinator/play-caller gap (4.1).
2. **Sample poverty is severe**: a given staff experiences a given starter's
   absence a handful of times.
3. **Nominal starter status genuinely does not translate.** NFL depth charts are
   widely understood to be imperfect, and the only honest resolution is to check
   depth chart against *realised* snap counts — which is post-hoc.

### 4.7 REGIME CHANGE, AND THE PERSISTENCE WARNING

New HC/OC/DC/play-caller, bye-week philosophical change, midseason system shift,
role change after repeated losses: each is a **structural break**, meaning the
trailing data describing that offense was generated by a different process.

**RESEARCHED CAREFULLY, AS INSTRUCTED: A COACH'S TENDENCY ON ONE ROSTER MAY NOT
TRANSFER UNCHANGED TO ANOTHER, AND PERSONNEL COMPOSITION MUST NOT BE MISTAKEN FOR
COACHING PHILOSOPHY.** A coach who ran the ball 55% of the time may have had a
dominant line and a limited quarterback. A coach who threw 68% may have been
trailing all year. `pass_rate_over_expectation` partially controls for game state
and controls for **nothing** about personnel. **This pass could NOT establish the
degree to which coaching tendencies persist across roster changes**, and that is
recorded in section I as an explicit non-determination rather than resolved by
assumption.

---

## 5. COACH STATEMENT RELIABILITY — RECORD BEFORE TRUSTING

**COACH SPEECH IS NOT GROUND TRUTH.** Coaches are strategically vague, sometimes
uninformed about their own staff's plans, and occasionally deliberately
misleading. A statement is an *observation about a speaker*, not a fact about the
world.

### 5.1 THE DESIGN (design only — nothing built)

A future **SPEAKER / STAFF RELIABILITY PROFILE** would be keyed on
`(speaker_id, staff_id, claim_category)` and estimated from archived
statement→outcome pairs. Claim categories to record, taken from the brief and
kept verbatim because the exact phrasing is the unit of analysis:

`"more involved"`, `"full workload"`, `"committee"`, `"starting"`,
`"third-down role"`, `"goal-line role"`, `"limited"`, `"good to go"`,
`"snap count"`, `"we want to establish the run"`,
`"we need to get him the ball"`, `"player X will take over role Y"`.

Outcome comparisons to measure each claim against:

- next-game **snap** share change (from `snap_counts`)
- next-game **route** participation change (participation — **prior seasons
  only**, a real limitation on this whole design)
- next-game **target** share change (pbp)
- next-game **carry** share change (pbp)
- **active / inactive** status
- **red-zone and goal-line** usage change (pbp yardline bands)
- **role persistence** — did the change hold for two, three, four weeks?

### 5.2 HARD RULES ON THIS DESIGN

- **DO NOT assume all coaches are equally informative.** The heterogeneity is the
  object of study.
- **DO NOT assume specificity implies usefulness.** A very specific claim can be
  a very specific error. "He'll get 20 carries" is more falsifiable than "he'll
  be involved" — falsifiable is not the same as accurate.
- **RECORD FIRST, MEASURE LATER, PROMOTE ONLY IF EVIDENCE WARRANTS.**
- **A reliability profile dies with the staff that produced it.** Given NFL staff
  turnover, most profiles will never reach a usable sample. That is a reason to
  record broadly and promote almost nothing.
- **SAMPLE ARITHMETIC, STATED SO NOBODY IS SURPRISED LATER:** a head coach gives
  roughly three media availabilities a week across ~17 games. Even if every one
  yielded a codable role claim, that is a few dozen claims a season per speaker,
  across ~13 claim categories. **A per-speaker-per-category reliability estimate
  will be underpowered for years.** `backtest/signals.py` already encodes the
  relevant guard: `MIN_EVENTS_PER_PARAM = 15.0`, and it sets
  `authoritative=False` below it.

---

## 6. DEFENSIVE MATCHUP — MECHANISTIC, NOT RANK-BASED

**The target abstraction is:**

```
PLAYER ROLE  x  OFFENSIVE SCHEME  x  DEFENSIVE SCHEME  x  PERSONNEL
             x  EXPECTED GAME STATE
```

**NOT** `PLAYER x DEFENSE RANK`. "Opponent ranks 28th vs WR" and "team allows X
fantasy points to RB" are not stopping points; they are the thing to get past.
`opponent_position_group_concession` is included at **rank 54 of 56** precisely so
that the rank-based construction is present, visibly deprioritised, and honestly
labelled as a fallback rather than smuggled in as mechanism.

### 6.1 FEASIBILITY AND FREE AVAILABILITY, CONCEPT BY CONCEPT

| concept | free? | source | in-season? |
|---|---|---|---|
| expected player alignment | **NO** | — | no |
| slot vs outside | **NO** for per-play; partial via participation positions | participation | **no** |
| inline vs detached TE | partial | participation `offense_formation`/`offense_positions` | **no** |
| route tree / route concept | partial (`route` field exists) | participation | **no** |
| aDOT / depth profile | **YES** | pbp `air_yards`; NGS `avg_intended_air_yards` | **yes** |
| man vs zone tendency | **YES for past seasons** | participation `defense_man_zone_type` | **NO** |
| coverage shell | **YES for past seasons** | participation `defense_coverage_type` | **NO** |
| CB assignment / shadow evidence | **NO** | none located | no |
| LB/S matchup for TE and RB receiving | partial, inferential only | participation personnel + pbp outcomes | **no** |
| pass-rush matchup | partial | participation `number_of_pass_rushers`, `was_pressure` | **no** |
| OL matchup | **NO** structured source | — | no |
| pressure tendency | partial | participation `was_pressure`; pbp `sack` proxy | proxy only |
| blitz tendency | **YES for past seasons** | participation `number_of_pass_rushers` | **NO** |
| QB response to pressure | partial | participation `time_to_throw`; FTN `is_qb_out_of_pocket`, `is_throw_away`, `is_interception_worthy` | FTN only, 2026 asset absent |
| box count | **YES** | participation `defenders_in_box`; FTN `n_defense_box` | FTN only |
| defensive front / personnel packages | **YES for past seasons** | participation `defense_personnel` | **NO** |
| run concept vs front | **NO** — run concept is not in any free source | — | no |
| motion / play action / RPO / screen faced | **2022+** | FTN flags | FTN only |
| explosive-play allowance | **YES** | pbp `yards_gained` distribution by `defteam` | **yes** |
| YAC allowance / prevention | **YES** | pbp `yards_after_catch`; NGS `avg_yac_above_expectation` | **yes** |

### 6.2 THE HONEST BOTTOM LINE ON DEFENSIVE MATCHUP

**Almost everything genuinely mechanistic about defensive matchup lives in the
nflverse participation dataset, and participation does not publish during the
season.** The in-season alternatives are (a) FTN charting, which covers offensive
scheme flags well and defensive structure thinly, and whose 2026 asset did not
exist on 2026-09-11; (b) pbp outcome aggregates, which are results rather than
mechanisms; and (c) NGS weekly aggregates, which describe how a receiver was
defended in the aggregate rather than by scheme.

**Therefore, for the 2026 season, Full Count structurally cannot compute
mechanistic coverage matchup, and books using licensed charting can. On this
specific family the market very likely knows more than this pipeline, and no
amount of modelling closes that gap.** Saying so is more useful than substituting
a defensive rank and calling it mechanism.

**DO NOT quietly include PFF or any paywalled metric as if free.** None appears
anywhere in this inventory; PFF is listed in `unavailable`.

---

## 7. FILM INTELLIGENCE ENGINE — INVESTIGATION AND DESIGN (NOTHING BUILT)

### 7.1 THE FOUR-LAYER PRINCIPLE

- **NUMBERS** describe **WHAT** happened.
- **FILM** explains **HOW**, and **WHAT ROLE / SCHEME** generated it.
- **COACH / PRACTICE intelligence** explains **WHAT MAY CHANGE NEXT**.
- **THE MARKET** says what price is charged for all of it.

Full Count ultimately needs all four. It currently has the first and is building
the third and fourth. This section establishes what is legitimately obtainable
for the second.

**FILM IS NOT LICENSE TO GENERATE FOOTBALL OPINIONS.** Any film-derived
observation obeys **RECORD -> MEASURE -> PROMOTE**. "AI watched tape and thinks
the matchup looks great" must never become a production signal. A vision-model or
LLM conclusion without exact play provenance is **NOT promotion-grade evidence**.

### 7.2 FILM SOURCE / ACCESS AUDIT — WHAT COULD NOT BE ESTABLISHED

| film route | status | basis |
|---|---|---|
| Broadcast game film | **NOT AUTOMATABLE** | No legitimate automation route established. A consumer viewing entitlement does not confer automation rights. |
| NFL+ / league apps / club apps | **NOT AUTOMATABLE** | Authentication-gated consumer products. **DO NOT bypass authentication. DO NOT scrape protected film because a subscription can view it. DO NOT circumvent DRM or access controls.** |
| All-22 / coaches film | **NOT AVAILABLE** | No public automatable source located at all. Any signal requiring all-22 alignment reading is **unbuildable**, not merely hard. |
| Official licensed film API | **NONE FOUND** | No official film API accessible to Full Count was located in this pass. Recorded as UNKNOWN rather than assumed absent. |
| Public highlight clips (e.g. surfaced in ESPN scoreboard `highlights`) | **TECHNICALLY PRESENT, TERMS-ADVERSE** | Disney ToU §2.B.x prohibits automated extraction for dataset building. Also useless for role analysis: highlights are selected for outcomes, so any corpus built from them is **outcome-selected and structurally biased**. |
| YouTube team/league channels | **PROHIBITED** | ToS: no automated access except search engines per robots.txt or with prior written permission; downloading prohibited. |
| Historical film archive | **NOT ESTABLISHED** | No automatable historical film corpus was located. |

**CONCLUSION, STATED PLAINLY: DIRECT FILM CANNOT BE AUTOMATED LEGITIMATELY BY
FULL COUNT TODAY. IT IS RECORDED AS UNAVAILABLE.** The remainder of this section
is about structured substitutes, which is what the brief asks for when film
itself is unavailable.

### 7.3 STRUCTURED FILM SUBSTITUTES — WHAT EACH CAN AND CANNOT REPRESENT

#### (a) nflverse participation (2016-2025, **NOT in-season**)
- **CAN represent:** offensive formation, offensive and defensive personnel
  grouping, defenders in box, number of pass rushers, whether the play had
  pressure, time to throw, the route run, man-vs-zone, coverage type, and the
  exact set of 22 players on the field.
- **CANNOT represent:** *who covered whom*, release technique, leverage, safety
  rotation, pre/post-snap disguise, blocking quality, intended gap, read
  progression, or any per-player *assignment*.
- **Fatal practical limit:** unavailable during the season it would be used in.

#### (b) FTN charting (2022+, four seasons, 0/6/12/18 UTC in season — **but 2026 asset 404 on 2026-09-11**)
- **CAN represent:** no-huddle, **motion**, **play action**, **screen**, **RPO**,
  trick play, QB location (under centre / shotgun / pistol), backfield count,
  **defenders in box**, QB out of pocket, throwaway, **interception-worthy**
  throw, **catchable ball**, **contested ball**.
- **CANNOT represent:** coverage shell, man vs zone, defender assignment, route
  tree, blocking attribution, intended gap, or read progression.
- **This is the single best legitimately free, structured, film-adjacent source
  found in this research pass**, and the only one with an explicit licence.
- **Licence obligation is real, not a formality:** CC-BY-SA 4.0 with required
  attribution to FTN Data via nflverse. **SHARE-ALIKE** has consequences for any
  derived dataset Full Count publishes. Whether Full Count's intended use
  complies is **UNKNOWN-REQUIRES-REVIEW**.

#### (c) nflfastR pbp contextual fields (1999+, nightly in season)
- **CAN represent:** shotgun, no-huddle, QB dropback, QB scramble, pass location,
  air yards, yards after catch, field position, goal-to-go, down and distance,
  score state, drive and series structure, `xpass`/`pass_oe`, EPA, win
  probability, and per-play tackler and assist attribution.
- **CANNOT represent:** anything about alignment, coverage, personnel, blocking,
  or intent.

#### (d) Next Gen Stats-derived weekly aggregates via nflverse (in-season, overnight)
- **CAN represent:** average cushion (a real proxy for press-vs-off coverage),
  **average separation** (a real proxy for how open a receiver gets), average
  intended air yards (aDOT), **share of team intended air yards**, expected YAC
  and **YAC above expectation** (which separates scheme-created from
  player-created yards), and rushing-yards-over-expected on the rushing side.
- **CANNOT represent:** anything play-level, anything about assignment, or
  anything about a specific matchup within a game. These are **weekly
  aggregates**, so they describe a player's week, not a play.

#### (e) Public tracking-derived datasets
- **STATUS: UNKNOWN.** This pass did not establish a free, redistributable,
  automatable source of NFL player tracking data beyond the NGS aggregates above.
  The NGS API itself returns 401. Recorded as UNKNOWN rather than asserted absent.

### 7.4 FILM CONCEPT COVERAGE MATRIX — HONEST

For each film concept the brief names, whether a legitimate free structured
substitute exists:

**RECEIVERS / TEs** — alignment: participation only, **off-season**. slot/outside:
same. motion: **FTN, in-season**. release: **NO**. route family: participation
`route`, **off-season**. route depth: **YES** (pbp `air_yards`, NGS aDOT).
designed first read: **NO**. clear-out vs true read: **NO**. manufactured touches:
**FTN `is_screen_pass`, in-season**. target intent: **NO**. man/zone faced:
participation, **off-season**. bracket / double coverage: **NO**. safety help:
**NO**. press vs off: **proxy only** (NGS `avg_cushion`). separation context:
**proxy only** (NGS `avg_separation`). target quality / catchability: **FTN
`is_catchable_ball`**. contested target: **FTN `is_contested_ball`**. YAC
opportunity: **YES** (pbp `yards_after_catch`, NGS expected YAC). blocking
responsibilities reducing route volume: **NO direct source** — inferable only as
the gap between snap share and route participation, which needs participation.

**RUNNING BACKS** — run concept: **NO**. intended gap: **NO**. box structure:
**YES** (participation `defenders_in_box`; FTN `n_defense_box`). personnel
grouping: participation, **off-season**. blocking quality: **NO**. yards blocked
vs created: **PARTIAL** — NGS rushing-yards-over-expected is the responsible
version, and it is a model output, not a film reading. broken tackles: **NO**.
pass protection: **NO**. route role / checkdown role: partial via pbp target
depth. third-down role: **YES** (pbp `down`/`ydstogo`). two-minute role: **YES**
(pbp clock). goal-line package: **YES** (pbp `yardline_100`, `goal_to_go`).
situational committee structure: **YES** (pbp). trust in high-leverage
situations: **YES** as usage, **NO** as intent.

**QUARTERBACKS** — read progression: **NO**. first-read tendency: **NO**.
pressure recognition: **PARTIAL** (participation `was_pressure` + `time_to_throw`,
off-season; FTN `is_qb_out_of_pocket`, in-season). pocket behaviour: **FTN
partial**. time to throw: participation, **off-season**. scramble opportunity:
**YES** (pbp `qb_scramble`). designed movement: **PARTIAL**. checkdown tendency:
**PARTIAL** (pbp `air_yards` distribution). deep-shot aggressiveness: **YES**
(pbp `air_yards`). coverage recognition proxies: **off-season only**. response to
blitz: participation `number_of_pass_rushers`, **off-season**. response to
man/zone: participation, **off-season**. designed red-zone rushing role: **YES**
(pbp). **whether a statistical failure came from QB / protection / receiver /
scheme: PARTIALLY ATTRIBUTABLE AT BEST** — FTN's `is_throw_away`,
`is_interception_worthy` and `is_catchable_ball` separate some of it, and the
honest answer is that free structured data **cannot** fully attribute it.

**OFFENSIVE LINE** — protection failures: **NO** attribution source. stunt/blitz
communication: **NO**. replacement-player weakness: **NO**. run-lane creation:
**PARTIAL** (NGS rushing-over-expected, at the back's level not the line's).
pressure attribution OL vs QB: **NO**. interior vs edge weakness: **NO**.
**continuity: YES** — computable from `snap_counts` week over week, which is the
one OL concept that is genuinely free and structured, and it is why
`ol_configuration_change` is ranked 10th.

**DEFENSE** — coverage shell, man/zone, box structure, blitz design, personnel
adjustment: participation, **off-season only**. pre/post-snap disguise, safety
rotation, CB travel/shadow, leverage, run fits, pressure quality, LB
responsibility, goal-line structure: **NO free source**.

**COACHING / SCHEME** — personnel-package change, formation change: participation,
**off-season**. **motion change, route-tree change (partially), run-scheme change
(partially), protection adjustment: FTN, in-season**. red-zone package, designed
touch creation, QB designed runs: **YES via pbp**. matchup-specific plan:
requires by-opponent personnel, **off-season**. **role redistribution box scores
do not reveal: THIS IS THE CORE PROMISE OF THE FILM LAYER AND IT IS THE PART LEAST
SERVED BY FREE DATA IN-SEASON.**

### 7.5 FILM CHANGE DETECTION — RESEARCH HYPOTHESIS, NOT ESTABLISHED SKILL

**A CHANGE in role or scheme may carry more marginal information than a season
average.** That is a **RESEARCH HYPOTHESIS** and it is treated as one. The reason
it is plausible is that a season average is available to everyone and is what a
book's own model most likely uses, whereas a two-week-old discontinuity is a
small-sample fact both sides must decide how much to believe.

Week-over-week change detection feasibility, by concept:

| concept | in-season change detection? | source |
|---|---|---|
| route tree | **NO** | participation, off-season |
| alignment / slot rate | **NO** | participation, off-season |
| **motion** | **YES (2022+, if FTN current-season asset exists)** | FTN `is_motion` |
| **manufactured touches** | **YES** | FTN `is_screen_pass` + pbp `air_yards` |
| first reads | **NO** | no source |
| target design | **PARTIAL** | pbp `air_yards` by receiver, NGS air-yard share |
| **RB package / goal-line role** | **YES** | pbp yardline bands + `down`/`ydstogo` |
| **pass protection** | **NO** | no attribution source |
| run concepts | **NO** | no source |
| offensive personnel | **NO** | participation, off-season |
| protection scheme | **NO** | no source |
| **QB designed runs** | **YES** | pbp `rusher_player_id` + `qb_scramble` |
| coverage structure | **NO** | participation, off-season |
| CB shadow behaviour | **NO** | no source |
| **blitz rate** | **NO in-season** | participation, off-season |
| defensive fronts | **NO in-season** | participation, off-season |
| **TE blocking-vs-route role** | **NO** | needs routes; participation, off-season |
| **snap share** (not a film concept, but the honest in-season workhorse) | **YES** | `snap_counts`, four refreshes a day |

**SUMMARY: in-season week-over-week film change detection is possible for
roughly six of eighteen concepts, and the six that work are the offensive-scheme
FTN flags plus pbp-derived situational usage.** Everything about defensive
structure and about route/alignment is off-season only.

### 7.6 FILM OBSERVATION CONTRACT — DESIGN ONLY

A future structured film/charting observation record. **Design; not implemented.**
The governing requirement is that an audit must be able to answer **WHAT PLAY /
WHAT SOURCE CAUSED THIS OBSERVATION?**

```
film_observation:
  # --- exact play provenance: non-negotiable ---
  game_id                 nflverse game_id, e.g. "2026_01_NE_SEA"
  play_id                 nflverse play_id within that game
  season / week           redundant with game_id, stored for query sanity
  # --- entity ---
  player_id               gsis_id where available; pfr_player_id when the
                          source is PFR-derived; NEVER a name alone
  team / opponent         posteam / defteam
  # --- source identity ---
  film_source             "ftn_charting" | "nflverse_participation" |
                          "ngs_weekly" | "pbp" | "vision_model" | "analyst"
  source_url_or_artifact  release asset URL, or the archived artifact path and
                          sha256 from nfl/archive/provenance.py
  film_angle_or_view      broadcast | all-22 | endzone | n/a  (n/a for charting)
  source_timestamp        what the payload claims for itself; None if absent
  observed_at            when THIS process saw it -- the field a point-in-time
                          reconstruction keys on, per provenance.py's own rule
                          that these two must never be collapsed
  extractor_version       version of the code/model that produced the value;
                          the analogue of backtest/SCHEMA.md's code_git_sha
  # --- the observation ---
  observation_type        "alignment" | "route_family" | "coverage_shell" |
                          "motion" | "box_count" | "pressure" | "target_quality"
                          | "run_concept" | "protection" | ...
  structured_value        the value, typed; never free prose
  confidence              0..1, or a label where a number would be false
                          precision -- correlation.py's four-label choice is the
                          precedent: "A correlation COEFFICIENT implies a level
                          of precision nothing here has earned yet"
  uncertainty_reason      why confidence is below 1: occlusion, ambiguous
                          formation, annotator disagreement, model abstention
```

**Rules that make this contract worth having:**

1. **No observation without `game_id` + `play_id`.** An observation that cannot be
   traced to a play is not evidence.
2. **`observed_at` and `source_timestamp` are separate fields and must never be
   collapsed** — inherited verbatim from `nfl/archive/provenance.py`.
3. **`extractor_version` is mandatory.** A model revision changes the meaning of
   every value it produced, and `backtest/SCHEMA.md` already shows what happens
   without a version field: "two runs of the identical date range on two
   different commits can legitimately disagree, and nothing on disk could tell
   them apart before this."
4. **A vision-model or LLM conclusion without play provenance is NOT
   promotion-grade evidence**, regardless of how confident the model is.
5. **Charting observations from a licensed third party (FTN) carry that party's
   licence forward.** CC-BY-SA is share-alike.

---

## B. NFL INTELLIGENCE ENGINE DESIGN (DESIGN ONLY — NO CODE)

**ONLINE SEARCH IS A PRODUCT AND SCIENTIFIC REQUIREMENT FROM JACOB, NOT
OPTIONAL. WebSearch and WebFetch ARE PERMANENT NFL INFRASTRUCTURE**, not a
one-off research convenience. They are the *discovery* layer of a standing job.

### B.1 SCOPE OF THE FUTURE JOB

Continuously collect pregame intelligence across **ALL 32 TEAMS** and **BOTH
SIDES of every matchup**:

official NFL and team sources; HC / OC / DC press conferences; position-coach
comments; QB / RB / WR / TE pressers; OL and defensive player media; official
injury and practice reports; practice progression; official inactives;
transactions; IR / PUP / NFI / suspension / practice-squad elevations;
depth-chart and role changes; contracts, extensions, restructures and incentives
where verifiable; holdouts and hold-ins; trade, signing and release events;
credible beat-reporter practice observations; credible local reporting; **opponent
injury and personnel state**; matchup-specific scheme and personnel information;
coaching and play-calling information; weather; market movement.

**BOTH SIDES is load-bearing.** A receiver's props depend on the opposing
secondary's availability as much as on his own team's. A pipeline that archives
only the team it has a pick on is systematically half-blind.

### B.2 SOURCE HIERARCHY — DISCOVERY BREADTH AND EVIDENTIARY WEIGHT ARE SEPARATE CONCERNS

**Broad web output is NOT automatically trustworthy.** The tiering below governs
*weight*; it does not govern *whether to look*. Full Count should discover as
broadly as it can and weight as narrowly as the evidence deserves.

| tier | what | examples | evidentiary weight |
|---|---|---|---|
| **Tier 1** | Authoritative / official | official injury report, official inactives, official transaction wire, official club transcript, on-record coach statement | **Highest.** A Tier-1 statement is a fact *about what was said or filed*, which is still not a fact about what will happen. |
| **Tier 2** | High-quality national and team beat | established national reporters, credentialed team beat writers | High. Usually earliest on transactions and inactives. |
| **Tier 3** | Credible local reporting and analysis | local outlets, established analysts with named methods | Medium. Useful for practice observation. |
| **Tier 4** | Broad web discovery | aggregators, general sports sites | Low. **DISCOVERY ONLY.** May establish that something exists and point at a better source; may never be the sole basis for a claim. |
| **Tier 5** | Rumour / unverified | unattributed social posts, speculation | **AWARENESS ONLY.** Recorded so a later contradiction can be explained, never weighted. |

**HARD RULES:**
- **DO NOT fabricate transcripts, quotes, or coverage.** Ever, for any reason.
- **DO NOT bypass access controls, authentication, or paywalls.**
- **HONEST UNAVAILABLE BEATS FABRICATED COVERAGE.**
- **NEVER treat an LLM summary as underlying evidence.** A summary is *derived*;
  **the source is the evidence.** The evidence object below therefore requires a
  raw reference and an exact quoted span alongside any extracted claim.
- **Extracted intelligence must preserve provenance sufficient to trace any claim
  to its source.**

### B.3 PRESS-CONFERENCE / MEDIA INTELLIGENCE — FINDINGS

**Jacob specifically requires reviewing coach and player press conferences EVERY
DAY.** What this pass established about automating that across 32 team media
ecosystems:

| route | finding |
|---|---|
| **Official club transcript pages** | **THEY EXIST AND ARE PUBLIC.** Verified by search on 2026-09-11: `patriots.com/news/transcripts` is an official transcripts index; individual transcript pages exist (a Patriots HC press-conference transcript dated 9/5 and a Dolphins HC transcript dated September 7 were both surfaced). **So the content is there.** |
| **Terms for bulk retrieval of those pages** | **PROHIBITED.** nfl.com Terms §1.3 and §11, quoted verbatim in the source audit. Club sites operating under NFL terms inherit this. **Whether each club's own site terms differ was NOT determined per club (32 separate documents) and is UNKNOWN-REQUIRES-REVIEW.** |
| **Official NFL/club video + NFL.com press-conference channel** | Exists (`nfl.com/videos/channel/press-conferences-vc`). Automated retrieval: **PROHIBITED** per the same clauses. |
| **Closed captions** | No route established that does not first require prohibited automated retrieval of the video. **UNKNOWN-REQUIRES-REVIEW / effectively unavailable.** |
| **YouTube team/league channels + auto-transcription** | **PROHIBITED.** ToS bar automated access and downloading. |
| **Third-party transcript services** | Not evaluated in this pass. **UNKNOWN.** |
| **Search/fetch as a DISCOVERY layer** | **THIS IS THE ONE VIABLE ROUTE and it is what Phase A already does.** Verified from the Phase A manifest: `media_discovery` recorded **2 CHECKED_AND_FOUND and 10 UNAVAILABLE_BY_POLICY**. That is the correct shape: discover what exists, retrieve only what may legitimately be retrieved, and **record the rest as UNAVAILABLE_BY_POLICY rather than skipping it silently.** |

**The honest bottom line on Jacob's daily press-conference requirement:** the
*information* is publicly published and the *requirement is legitimate*, but
**bulk automated harvesting of official transcripts and video is prohibited by the
terms actually quoted above.** The compliant design is: use search/fetch to
discover that a specific availability happened and what was said, record the
source URL and an exact quoted span as evidence, and record the underlying
transcript artifact as `UNAVAILABLE_BY_POLICY`. **If terms change or express
written consent is obtained, the design does not change — only the outcome state
does.**

### B.4 THE EVIDENCE OBJECT — DESIGN

```
evidence:
  source_url                  exact URL. Mandatory. No URL, no evidence.
  source_type                 official_report | official_transcript |
                              official_video | beat_report | local_report |
                              national_report | aggregator | social | derived
  source_tier                 1..5 per B.2
  team                        club the claim concerns
  player_or_entity_id         gsis_id where resolvable; entity may also be a
                              unit ("OL"), a package, or the staff itself
  speaker                     who said it
  speaker_role                HC | OC | DC | position_coach | player |
                              GM | club_official | reporter | unknown
  opponent / game_id          which matchup it bears on, when it bears on one
  event_timestamp             when the statement was MADE / the event occurred
  published_timestamp         when the source published it
  first_observed_timestamp    when FULL COUNT first saw it -- THE ONLY ONE THIS
                              PROCESS CAN VOUCH FOR, and the one the strict
                              point-in-time rule keys on
  raw_reference               archived artifact path + sha256 of the raw
                              transcript/caption/page, OR an explicit
                              UNAVAILABLE_BY_POLICY marker when terms forbid
                              storing it
  supporting_quote_span       the EXACT words, with character offsets into the
                              raw reference where the raw is held. An extracted
                              claim with no quoted span is NOT evidence.
  extracted_claim             the structured claim
  claim_type                  availability | role | workload | scheme |
                              personnel | transaction | contract | health |
                              depth_chart | other
  source_reliability          the SPEAKER/OUTLET reliability estimate, when one
                              exists; explicitly null until measured
  confidence                  extraction confidence -- about the EXTRACTION,
                              not about the football
  expected_validity           see section C
  supersession_link           the evidence id this one supersedes
  contradiction_link          evidence id(s) this one CONTRADICTS, unresolved
  extraction_model_version    which model/prompt/code produced the extraction
  affected_world_state_fields which canonical fields this would change
  affected_markets            which FanDuel marketTypes it bears on
```

**Note the two separate confidences.** `confidence` is about whether the
extraction faithfully represents the source. `source_reliability` is about whether
the source is worth believing. Collapsing them would let a perfectly-extracted
worthless quote look strong.

### B.5 COST ARCHITECTURE

**DISCOVER CHEAPLY -> ARCHIVE RAW -> FINGERPRINT / DEDUPE -> DETECT WHAT CHANGED
-> PROCESS ONLY NEW / RELEVANT -> SMALLEST ADEQUATE MODEL -> ESCALATE ONLY
IMPORTANT / AMBIGUOUS.**

1. **DISCOVER CHEAPLY** — search and lightweight fetch to establish *what
   exists* per team per day. Cheap, broad, and recorded whether or not anything
   is found (a `CHECKED_AND_NONE_FOUND` is a real result).
2. **ARCHIVE RAW** — store bytes exactly as received with sha256, per
   `provenance.py`'s existing `Fetched` contract. **A source state never fetched
   is gone forever; raw archives can be reparsed.**
3. **FINGERPRINT / DEDUPE** — content hash per artifact. The same quote reaches
   Full Count through five outlets; it is one claim with five sources.
4. **DETECT WHAT CHANGED** — diff against the last fingerprint.
5. **PROCESS ONLY NEW / RELEVANT** — **DO NOT retranscribe or resummarise an
   unchanged press conference on every run.** This is stated as a hard design
   constraint because it is the single largest avoidable cost in a daily
   32-team job.
6. **SMALLEST ADEQUATE MODEL** — pattern matching and rules for
   `DNP/Limited/Full`, designations, and transaction verbs; a small model for
   routine claim extraction.
7. **ESCALATE ONLY IMPORTANT / AMBIGUOUS** — a large model only for ambiguous
   role language on players who actually have posted markets, and for
   contradiction adjudication (which produces a *recorded contradiction*, not a
   resolution).

---

## C. TEMPORAL VALIDITY AND NEWS DECAY

Every piece of intelligence needs four timestamps and three links:

```
effective_at    when the claim STARTS being true
observed_at     when Full Count first saw it  (== first_observed_timestamp)
expires_at      when it stops being assumed true, or expected_validity as a
                duration/class when no hard expiry exists
supersedes      the prior observation this replaces
contradicted_by observation(s) that disagree and have NOT been reconciled
```

Worked examples, using the brief's own cases:

| claim | expected validity | behaviour |
|---|---|---|
| "RB2 will start this week" | **expires at that game's kickoff** | Single-game scope. Must not leak into next week. |
| "Player placed on IR" | **persists until a status change** | No expiry; superseded only by activation or another transaction. |
| "WR limited Wednesday" | **superseded by Thursday's report, and again by Friday's designation, and again by the T-90m inactive list** | A chain of supersessions, each preserved. |
| "New OC installed a new scheme" | **persists for the regime** | Long-lived; invalidates trailing baselines rather than expiring. |
| "Coach says he's fine" + "report says LIMITED" | **neither expires the other** | **CONTRADICTION. Both retained.** |
| Weather forecast, Wednesday vintage | **superseded by each later vintage; never deleted** | The vintage series *is* the data. |
| FanDuel price snapshot | **superseded by the next snapshot; never overwritten** | Same principle as weather. |

**THE CANONICAL WORLD STATE MUST REPRESENT CURRENT VALID INFORMATION WHILE
PRESERVING IMMUTABLE HISTORICAL OBSERVATIONS. BOTH, NOT EITHER.** The
architecture that satisfies this is an append-only observation log plus a derived
current-state view — never an in-place update. `AGENTS.md` #10 already says
"Never silently alter prediction history"; this extends the same rule to the
*inputs* of predictions.

**CONTRADICTIONS MUST BE PRESERVED, NOT SILENTLY RESOLVED.** Coach says healthy +
official report says LIMITED + beat reporter observes reduced work is a
**CONTRADICTION / INFORMATION-RISK STATE**. It must **NOT** become whichever
source the pipeline happened to process last. `provenance.py` already reserves
`UNRESOLVED_CONTRADICTION` for exactly this, and `coverage_summary()`'s
`sources_with_no_conclusive_observation` is the same instinct applied to silence.

---

## D. COMPLETENESS / COVERAGE WATCHDOG

**WE CANNOT SILENTLY MISS A MATERIAL POINT OF NFL INFORMATION.** No system can
guarantee awareness of everything, so **the engineering answer is OBSERVABILITY OF
COVERAGE**, not a promise of completeness.

### D.1 ALIGNMENT WITH PHASE A (ALREADY IMPLEMENTED)

`nfl/archive/provenance.py` already implements the outcome states, and it
implements **SEVEN**, one more than the six the mission brief lists:

| state | in brief's list | in provenance.py | meaning |
|---|---|---|---|
| `CHECKED_AND_FOUND` | yes | **yes** | fetched, parsed as the expected container, non-empty |
| `CHECKED_AND_NONE_FOUND` | yes | **yes** | understood; the source genuinely has nothing |
| `SOURCE_FAILED` | yes | **yes** | transport/status/structure failure — **we do NOT know what the source would have said** |
| `NOT_CHECKED` | yes | **yes** | never attempted this run |
| `STALE` | yes | **yes** | served from a prior capture; no fresh observation |
| `UNRESOLVED_CONTRADICTION` | yes | **yes** | sources disagree; disagreement preserved rather than resolved by write order |
| **`UNAVAILABLE_BY_POLICY`** | **NOT in the brief's list** | **yes** | deliberately not fetched: terms unclear, requires authentication, or automation not established as permitted. **Recorded, not silently skipped.** |

`UNAVAILABLE_BY_POLICY` is the **most important state for this research pass's
findings**, because it is the correct home for: Next Gen Stats direct (401),
Pro-Football-Reference (403), ESPN-as-a-dataset (Disney ToU), official transcripts
and video (nfl.com ToS), YouTube captions (YouTube ToS), and every film route in
section 7.2. Without it, "we chose not to fetch this for legal reasons" is
indistinguishable from "we forgot".

It is already in use: the 2026-09-11 capture recorded `media_discovery` as
**2 CHECKED_AND_FOUND + 10 UNAVAILABLE_BY_POLICY**.

### D.2 WHAT THE SEVEN STATES STILL LACK

Four gaps, proposed as additions rather than replacements:

1. **`PARTIAL`** — a source returned a well-formed payload that is *incomplete by
   its own structure*. The exact case verified in this pass: a FanDuel tab slug
   that silently returns an 8-market default is neither `CHECKED_AND_FOUND` (we
   did not get the passing props) nor `SOURCE_FAILED` (HTTP 200, valid JSON). It
   is the most dangerous observed failure mode in the whole audit and it currently
   has no state.
2. **`OUT_OF_SEASON_BY_DESIGN`** — distinct from `NOT_CHECKED` and from
   `UNAVAILABLE_BY_POLICY`. Participation is not missing, not refused, and not
   policy-blocked; it is *structurally not published yet*. Collapsing it into
   `NOT_CHECKED` invites someone to "fix" it by fetching current-season
   participation, which would be lookahead.
3. **`SOURCE_CONTRADICTS_ITS_OWN_DOCUMENTATION`** — or at minimum a
   `documentation_mismatch` note. Verified case: nflverse documentation says the
   injuries source "died after the 2024 season" and there is "no 2025 data",
   while `injuries_2026.csv` returned 200 with genuine content. Today that
   discrepancy has nowhere to live.
4. **A per-source EXPECTED-CADENCE field**, so `STALE` can be computed rather than
   asserted. A depth chart refreshed daily at 7AM UTC and a participation file
   refreshed annually cannot share one staleness threshold.

### D.3 THE COVERAGE MANIFEST — PER TEAM, PER GAME

Rows required, each carrying one of the seven (plus proposed) states, a
`last_conclusive_observation_at`, and the expected cadence:

| row | notes |
|---|---|
| official injury report | both teams, every game |
| HC media | both teams |
| OC media | both teams — **frequently unavailable; that must show as a state, not as silence** |
| DC media | both teams — same |
| position-coach media | sparse by nature; `CHECKED_AND_NONE_FOUND` is the common correct answer |
| player media | QB / RB / WR / TE / OL / defense |
| transactions | both teams |
| contracts | both teams |
| depth chart / roster | both teams; **must be snapshotted, not read later** |
| beat sweep | both teams |
| **opponent injury / personnel** | explicit row so the both-sides requirement is auditable |
| coaching / play-calling coverage | **will frequently be `NOT_CHECKED` or `UNAVAILABLE_BY_POLICY` given section 4.1** |
| film / charting coverage | FTN and participation availability per season/week; **`OUT_OF_SEASON_BY_DESIGN` for participation all season** |
| weather | per game; **plus which forecast VINTAGES were captured** |
| market state | per event per tab; **plus WHICH TABS WERE FOUND and by which discovery method** |

**A MISSING EXPECTED SOURCE MUST NEVER SILENTLY LOOK LIKE "NO RELEVANT NEWS."
ABSENCE OF EVIDENCE MUST NOT MASQUERADE AS EVIDENCE OF ABSENCE.**

### D.4 FEEDING A FUTURE INFORMATION-RISK CONCEPT — WITHOUT BUILDING OR WEIGHTING A SCORER

**No scorer is designed here and no weight is proposed.** What is proposed is the
*shape* of the eventual relationship, expressed as constraints rather than as a
formula:

1. Coverage deficit may only ever **reduce** confidence or availability, never
   increase either — `AGENTS.md` #14 verbatim: "Missing/stale data must reduce
   confidence or availability, never silently become favorable evidence."
2. It must be reported **per source**, not pooled. `coverage_summary()` already
   states the reason: "a run in which the sportsbook succeeded and the injury
   report failed is NOT 50% healthy, it is a run with a specific known hole, and
   the hole is the actionable part."
3. An **`UNRESOLVED_CONTRADICTION`** on a player must be capable of removing that
   player's candidates from publication entirely, independent of any score.
4. The mapping from coverage state to action must itself be **versioned and
   measured** before it gates anything, exactly as `AGENTS.md` #15 requires of any
   model change.
5. **`evidence_completeness_deficit` (rank 14) is a signal, not a scorer.** It is
   recorded through `_sig()` like any other and left unweighted until measured —
   the same treatment the seventeen MLB signals were promised and, eventually,
   given.

---

## E. STRICT POINT-IN-TIME RULE

**INFORMATION FIRST OBSERVED AFTER THE PREDICTION CUTOFF CANNOT BE USED AT THAT
CUTOFF.** This applies without exception to: press conferences, statements,
injury and practice reports, transactions, inactives, articles, beat reports,
weather forecasts, sportsbook lines, depth-chart changes, and film or charting
data not actually available by the cutoff.

The keying field is **`observed_at` / `first_observed_timestamp`**, not
`source_timestamp`. `provenance.py` states the rule directly: "`observed_at` is
the only one this process can vouch for, and it is the field a point-in-time
reconstruction has to key on: information first observed after a prediction cutoff
cannot be used at that cutoff, **no matter what timestamp the payload claims for
itself**."

**THE OBJECTIVE IS TO RECONSTRUCT THE FOOTBALL INFORMATION STATE AVAILABLE AT
TIME T, NOT MERELY HISTORICAL FINAL STATISTICS.** A backtest built from final box
scores and season-end files is not a backtest of a decision anyone could have
made.

Four NFL-specific violations that a reasonable engineer would commit by accident,
all verified live in this pass:

1. Reading `temp`/`wind` from pbp or `games.csv` — **post-game fields, 0 of 272
   populated for 2026 today.**
2. Reading `spread_line`/`total_line` from `games.csv` — **the closing line,
   from a field refreshed every five minutes.**
3. Joining current-season participation — **a file that does not exist until
   after the post-season.**
4. Reading `referee` from `games.csv` — **post-game, 0 of 272 populated.**

Two more that are subtler:

5. Reading the **current** depth chart or roster file for a past date. Both are
   mutable daily files.
6. Reading a **third-party model output** (`xpass`, expected YAC) whose model was
   retrained after the date in question. Not date leakage, but a reproducibility
   failure that needs an `extractor_version` analogue of `code_git_sha`.

---

## F. INFERENCE STRUCTURE RECOMMENDATION

**INDIVIDUAL PROP ROWS MUST NEVER BE TREATED AS INDEPENDENT SIMPLY BECAUSE THEY
HAVE DISTINCT CANDIDATE IDs.** NFL observations are strongly dependent within
game, within team, within week/slate, within recurring player processes, within
recurring coaching regimes, and within recurring team processes.

**DO NOT pre-commit to Wilson intervals or row-level bootstrap as sufficient.**
Both treat rows as exchangeable, and NFL rows are not.

### F.1 EVALUATION OF THE CANDIDATE STRUCTURES

| structure | verdict | reasoning |
|---|---|---|
| **Row-level bootstrap / Wilson** | **INSUFFICIENT — do not use alone** | Assumes exchangeable rows. On a single Sunday, one game's outcome moves ~12-20 correlated prop rows at once. Intervals will be far too narrow. |
| **Game-level clustering** | **NECESSARY, NOT SUFFICIENT** | The tightest real dependency unit: one game's script drives its QB's yards, both teams' rushing volume, every receiver's targets, and the kickers. This is the minimum credible unit. |
| **Date / slate clustering** | **NECESSARY on top of game clustering** | An NFL "date" is ~13 games sharing weather regimes, a common news cycle, and a common book-pricing state. MLB's own analysis clustered by date (32 dates, 2,134 predictions); NFL has *fewer, larger* slates, which makes the correction bigger, not smaller. |
| **WEEK-BLOCK resampling / bootstrap** | **RECOMMENDED AS THE PRIMARY STRUCTURE** | The natural NFL block is the **week**: ~13-16 games, one news cycle, one injury-report cycle, one line-setting cycle. Block bootstrap over weeks respects both within-game and within-slate dependence without needing to model either. It also composes correctly with time-based splits, which `backtest/signals.py` design decision 2 already mandates ("TIME-BASED SPLITS ONLY. Never random."). |
| **Repeated player effects** | **REQUIRED** | The same 30 quarterbacks appear every week for years. A signal that works on one quarterback is not 17 independent observations. |
| **Repeated team effects** | **REQUIRED** | 32 teams, each appearing ~17 times a season. Team is a stronger grouping in NFL than in MLB because the roster barely changes week to week. |
| **Coaching-regime dependence** | **REQUIRED AND PARTIALLY UNIMPLEMENTABLE** | A regime is the correct grouping for every coaching and usage-philosophy signal. **But regime identity beyond head coach is unavailable (section 4.1)**, so regime clustering can only be done at head-coach granularity, and that is an acknowledged mis-specification rather than a solved problem. |

**RECOMMENDATION: week-block bootstrap as the primary uncertainty structure, with
game-level clustering inside the block, plus explicit player and team random
effects (or, more conservatively, per-player and per-team leave-one-out checks),
plus head-coach-level regime grouping for coaching signals with the
mis-specification stated. Strictly time-ordered splits throughout. The test split
scored exactly once.**

### F.2 SAMPLE POVERTY IS SEVERE AND IS THE DOMINANT CONSTRAINT

**~17 REGULAR-SEASON GAMES PER TEAM CREATES SEVERE SAMPLE POVERTY. STRONG
EVIDENCE WILL REQUIRE MULTIPLE SEASONS.**

The arithmetic, using the counts computed in this pass:

- 272 regular-season games in 2025. **A whole NFL season has fewer games than 1.7
  days of MLB's own backtest had graded predictions.**
- MLB's null result came from **2,134 graded predictions over 32 dates**. NFL
  would need roughly **a full season** of ten-pick-a-day publication to reach a
  comparable *row* count — and those rows would cluster into ~18 weeks rather
  than 32 independent dates, so the *effective* sample would be smaller still.
- `backtest/signals.py` requires `MIN_EVENTS_PER_PARAM = 15.0`. For a 5.0%-event
  market like 2+ touchdowns, 15 events per parameter means **300 candidate rows
  per parameter**. A ten-signal model in that market needs ~3,000 rows of
  2+-TD candidates before a fit is authoritative.
- **Per-coach or per-regime estimates are worse by another order of magnitude.**

**The operational consequence: for at least the 2026 season, NFL's honest posture
is RECORD, not PROMOTE.** Any pressure to weight a signal before multiple seasons
of evidence exist should be met with the September 2026 retractions.

### F.3 WITHIN-GAME CORRELATION IS SEPARATELY CRITICAL

**A QB's passing yards and his WR's receiving yards are different wagers sharing
one underlying event process.** The same completion contributes to both. This
breaks independence in **two distinct places**, and conflating them is itself an
error:

1. **Aggregate accuracy evaluation.** If the pipeline publishes a QB over and his
   receiver's over from the same game, those are close to one bet. A hit rate
   computed as if they were two independent observations overstates both the
   sample size and the confidence.
2. **Parlay pricing.** Independent multiplication is only correct when legs are
   independent, and same-game NFL legs are strongly positively dependent.

`correlation.py` is the existing precedent and its design choice should carry over
unchanged: **four labels, deliberately NOT a number**, because "a correlation
COEFFICIENT implies a level of precision nothing here has earned yet", and
`independent` must continue to mean "no rule fired", **not** "verified
uncorrelated". The NFL label set needs at minimum:

- **redundant** — e.g. a receiver's `PLAYER_X_RECEPTIONS` over and his
  `PLAYERS_WITH_10+_YARDS_RECEPTION`; or `ANY_TIME_TOUCHDOWN_SCORER` and
  `TO_SCORE_2+_TOUCHDOWNS` for the same player.
- **positive** — QB passing yards + his own receiver's receiving yards; two
  players on the same offense; any player prop + his own team total.
- **negative** — a team's `2+ Made Field Goals` and its own players'
  anytime-TD markets (red-zone trips convert to one or the other); a defender's
  `TO_RECORD_1+_SACK` and the opposing QB's passing yards; opposing skill players
  in a low-total game.
- **independent** — different games only, and even then only as "no rule fired".

### F.4 MARKET EFFICIENCY IS THE BAR

**THE BAR IS NOT "DOES THIS SIGNAL PREDICT?" BUT "DOES THIS ADD VALUE BEYOND WHAT
THE MARKET ALREADY KNOWS?"** `backtest/info_beyond_market.py` and
`test_info_beyond_market.py` already implement that question for MLB, and the
existing test exists because a real bug displayed a Brier score where a log loss
belonged. NFL props may be **sharply priced**, and a true signal fully captured by
price adds little proprietary value. **Every ranking in section 2 is a ranking on
this criterion, not on predictive strength.**

### F.5 USAGE REALLOCATION BREAKS MORE THAN ONE PLAYER

**ONE INACTIVE MAY ALTER ROUTES, TARGETS, CARRIES, PASS PROTECTION, RED-ZONE
OPPORTUNITY, FORMATIONS, PERSONNEL PACKAGES AND THE GAME PLAN ACROSS AN ENTIRE
OFFENSE.** This has two consequences the inference structure must respect:

1. An inactive is a **team-level shock**, so the correlated unit for that week is
   the offense, not the player. It is another argument for game- and
   week-clustered inference.
2. A per-player signal that reads "RB2 is now the starter" while ignoring that
   the same inactive changed the TE's route count and the QB's attempt total is
   **modelling one visible consequence of a shock with many**. That is why
   `inactive_role_reallocation_delta` is ranked first and why its
   `expected_marginal_info_beyond_market` is framed around **second-order**
   effects.

---

## G. UNAVAILABLE SIGNALS — ONE LINE EACH

| signal / source | reason |
|---|---|
| PFF grades and charting | Paywalled. Excluded by the no-paywalled-metric rule. |
| Next Gen Stats direct API | HTTP 401 under two UAs — requires authentication. |
| Pro-Football-Reference direct scrape | HTTP 403 with a real HTML error body under two UAs — origin refuses automation; terms UNKNOWN-REQUIRES-REVIEW. |
| Raw game film, automated analysis | No legitimate automation route established; a viewing entitlement is not an automation right. |
| Coaches / all-22 film | No public automatable source at all; alignment-reading signals are unbuildable. |
| YouTube press-conference auto-transcription | YouTube ToS prohibit automated access and downloading. |
| Bulk official transcript harvesting | nfl.com Terms §1.3 prohibit systematic retrieval to build a database. |
| ESPN endpoints as a dataset source | Reachable (200) but Disney ToU §2.B.x prohibits automated extraction for dataset building. Reachability is not permission. |
| In-season route participation | Participation "does not update during the season!" — 2026 asset 404, 2025 asset 200. |
| In-season coverage scheme (man/zone, shell) | Same participation release schedule. |
| Per-play defender-to-receiver assignment | No free source located; shadow/CB-assignment signals unbuildable. |
| OC / DC / actual play-caller identity | Not in any free structured dataset; `games.csv` has HEAD COACH ONLY. |
| Official pregame inactive list feed | No authoritative machine-readable source established — the highest-value NFL timestamp. |
| Day-by-day practice report history | nflverse injuries is one row per player per WEEK; no Wed/Thu/Fri axis. |
| Point-in-time historical sportsbook lines | No free archive; `games.csv` spread/total is one mutable field that becomes the close. |
| NFL venue coordinate table | Not located; NWS needs lat/lon; ESPN gives address, not coordinates. Gap is a geocoding step. |
| `api.github.com` metadata | Blocked by **THIS CONTAINER's** egress policy — an environment fact, NOT source unavailability; the release-asset URLs returned 200. |
| Fantasy points allowed by position | Deliberately excluded: the archetypal non-mechanistic matchup metric, and fully priced. |
| Contract-year motivation | Not a signal. Sports-talk psychology is not evidence. |
| FTN charting, current season (2026) | `ftn_charting_2026.csv` 404 on 2026-09-11 despite a documented in-season cadence. |
| PFR advanced stats, current season | `advstats_week_pass/rec/def_2026.csv` all 404 on 2026-09-11. |
| Retractable-roof game-day status | No feed; `games.csv` roof is a venue attribute, not a game-day decision. |
| FanDuel player tackles+assists market | NOT OBSERVED in 139 marketTypes / 262 payloads / 11-tab layout / 12 probed slugs. |
| FanDuel completions, attempts, interceptions markets | NOT OBSERVED in the same sample. |
| FanDuel longest reception / longest rush markets | NOT OBSERVED; nearest are the 10+/15+/20+/30+ reception thresholds. |
| FanDuel individual kicker markets | NOT OBSERVED; kicking is team-level inside `GAME_SPECIALS_-_KICKING`. |

---

## H. THE THREE THINGS I AM LEAST CONFIDENT ABOUT

### H.1 That the market is inefficient anywhere in this inventory

Every ranking in section 2 is a claim about where the market is *incompletely*
efficient, and **I have no evidence for any of them.** The rankings are reasoned
guesses about where to look. The single most likely outcome, given that Full
Count's MLB side ran this exact experiment across 15 markets and found
within-market AUC of 0.492 [0.461, 0.521], is that **NFL props are priced well
enough that most or all of these 56 signals add nothing measurable beyond price.**
I ranked `alt_ladder_internal_coherence` 3rd partly because it is the only entry
whose value does not depend on the market being wrong about football — only on a
price sheet being internally inconsistent, which is at least a *checkable* claim.
If I had to bet on which section of this document survives contact with 2027 data,
it would be the section that says most of it will not.

### H.2 Whether coaching tendencies persist across rosters, staffs and seasons

The coaching layer is the part of the brief with the strongest causal argument and
**it is the part I could verify least.** I established that head-coach identity is
available and that coordinator and play-caller identity are not. I did **not**
establish — and could not, in this pass — whether a staff's measured tendencies
(pass rate over expectation, committee structure, goal-line philosophy, injury
reallocation behaviour) **persist when the roster changes, when a coordinator
changes, or across seasons.** The brief warns specifically against mistaking
personnel composition for coaching philosophy, and `pass_rate_over_expectation`
controls for game state and for *nothing* about personnel. It is entirely possible
that the coaching family as specified here measures rosters wearing coaching
labels. I have ranked six coaching signals from 17th to 48th on reasoning alone,
and I would not defend any of those positions against data.

### H.3 Whether the media/press-conference layer can be built compliantly at the scale Jacob requires

Jacob requires daily review of coach and player press conferences across the
league. I established that the content is publicly published, and I established
that **nfl.com's terms prohibit systematic retrieval to build a database and
YouTube's prohibit automated access and downloading** — both quoted verbatim. The
compliant design I proposed (search/fetch as discovery, quoted spans as evidence,
`UNAVAILABLE_BY_POLICY` for the raw artifact) may or may not scale to 32 teams
times several daily availabilities, and I did **not** determine whether individual
club sites' own terms differ from nfl.com's, whether express written consent is
obtainable, or whether any third-party transcript service offers licensed
programmatic access. **This is the gap most likely to make the difference between
the press-conference layer being real infrastructure and being a well-documented
aspiration.** I have not resolved it and I have not pretended to.

---

## I. EXPLICIT LIST OF WHAT I COULD NOT DETERMINE

1. **Whether FanDuel offers a player tackles+assists market at all.** Not observed
   in 139 marketTypes across 262 archived payloads, not in the event's own 11-tab
   `layout.tabs`, and not behind 12 probed slugs. Later slates untested.
2. **Whether completions, attempts, interceptions-thrown, longest-reception and
   longest-rush markets exist on FanDuel** on any slate. Not observed here.
3. **Whether individual (as opposed to team) kicker markets exist on FanDuel.**
4. **Whether `ftn_charting_2026.csv` appears in-season**, and if so with what lag.
   Documented cadence says 48 hours; the asset was 404 two days into the season.
5. **When `advstats_week_*_2026.csv` becomes available.** All 404 today.
6. **The first season available for NGS-derived weekly aggregates** (passing,
   receiving, rushing). Commonly cited as 2016; **not verified** in this pass.
7. **The exact column set of `ngs_rushing.csv.gz`.** The asset is reachable; its
   columns were not enumerated, so `rushing_yards_over_expected` is specified
   provisionally.
8. **The first season available for nflverse snap counts and depth charts.**
9. **The nflverse umbrella data licence.** No licence statement was found. Only
   the FTN subset has an explicit one (CC-BY-SA 4.0).
10. **Whether CC-BY-SA 4.0's share-alike obligation is compatible with Full
    Count's intended use** of FTN-derived features in a product.
11. **FanDuel's terms regarding automated access to `sbapi.*.sportsbook.fanduel.com`.**
    Not reviewed. No legal conclusion offered.
12. **Whether individual club websites' terms differ from nfl.com's** with respect
    to automated retrieval of transcripts. 32 separate documents, none reviewed.
13. **Whether express written consent for transcript/video retrieval is
    obtainable** from the NFL or clubs.
14. **Whether any licensed third-party service offers programmatic NFL
    press-conference transcripts.** Not evaluated.
15. **Whether any official or licensed NFL film API exists** that Full Count could
    access. None found; absence not proven.
16. **Whether a free, redistributable NFL player-tracking dataset exists** beyond
    the NGS weekly aggregates.
17. **Coordinator and actual-play-caller identity, historically or prospectively,
    from any structured free source.** The largest structured-data gap found.
18. **Whether coaching tendencies persist across rosters and staffs** (see H.2).
19. **Whether the nflverse injuries feed is stable**, given the documentation says
    the source died after 2024 while `injuries_2026.csv` returns real content.
20. **Whether nflverse injuries data exists for the 2025 season**, which the
    documentation explicitly denies.
21. **An authoritative machine-readable source for official pregame inactives.**
    Phase A's own known-gaps note stands unresolved.
22. **A verified NFL venue latitude/longitude table.** Narrowed to a geocoding
    step over a verified venue list, but not closed.
23. **Retractable-roof open/closed status on a game-day basis.**
24. **Whether FanDuel's settlement rules count a 0.5 sack for `TO_RECORD_1+_SACK`,
    count a two-point conversion toward touchdown markets, or count defensive and
    return touchdowns in `ANY_TIME_TOUCHDOWN_SCORER`.** The rules were not read;
    the base rates in section 3 flag each as a settlement question.
25. **Which source FanDuel settles tackle markets against**, which matters because
    tackle statistics are not standardised across sources.
26. **Whether `xpass`/`pass_oe` values are stable across nflfastR model
    revisions**, i.e. the magnitude of model-vintage drift.
27. **The complete FanDuel NFL tab universe.** Tabs are per-event; the root page
    exposed additional title strings ("Player Props", "RB Props", "NFL Player
    Specials", "Big Kick Desktop", "Head to Head") whose slug forms all returned
    the default payload for the one event probed.
28. **Whether `route` in participation is granular enough to reconstruct a route
    tree**, or only a coarse route label. The field exists; its value domain was
    not inspected.
29. **Whether `spread_line`/`total_line` in `games.csv` for future games represent
    an opening line, a current line, or a projection.** 103 of 272 populated for
    2026; provenance unknown.
30. **Whether the 2026 season is 17 or 18 regular-season games per team.** 2025
    had 272 games (= 32 x 17 / 2) and `games.csv` holds 272 rows for 2026, which
    is consistent with 17 — but the schedule file could be incomplete and this was
    not independently confirmed.

---

*End of NFL_SIGNAL_INVENTORY.md. Companion machine-readable artifact:
`nfl/research/nfl_signals.json` (56 signals, 15/15 fields each, 26 unavailable
entries, 17 markets).*

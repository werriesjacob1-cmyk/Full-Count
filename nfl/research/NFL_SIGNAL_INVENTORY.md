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

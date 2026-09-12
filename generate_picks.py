#!/usr/bin/env python3
"""
generate_picks.py — deterministic, rule-based top-10 MLB prop picks.

No LLM call, no external API key. GitHub Actions does both the data
ingestion and the interpretation: this script reuses mlb_daily.py's already-
defined fetchers/constants (statsapi, pybaseball wrappers, STADIUMS, weather
helpers) to pull a smaller, tonight-scoped dataset, then scores and ranks
candidates with an explicit weighted formula instead of prose reasoning.

Methodology is this pipeline's own synthesis framework (see mlb_daily.py
Section 87) implemented as code:
  35% MATCHUP        — platoon (bats vs. throws) + opposing pitcher/lineup quality
                        + pitch-type-specific exploits (batter's performance vs.
                        the specific pitches the opposing pitcher actually throws)
  25% RECENT FORM     — L7/L14 rolling performance + bat-speed trend (a leading
                        indicator: rising bat speed tends to precede an AVG uptick)
  15% ENVIRONMENT     — weather, wind vs. park orientation, park HR factor
  15% BASELINE SKILL   — season-long established skill level
  10% CONTEXT         — lineup slot / umpire zone / TTO-specific matchup

Weighted toward trend/data convergence (how many independent signals agree)
rather than a single computed statistical edge, per explicit direction — and
negative-edge patterns (hot form contradicted by weak underlying batted-ball
quality) are actively penalized, not ignored.

The top 10 is a pure score ranking across every candidate in tonight's slate
(batters and pitchers, every prop type together) — no per-game or per-prop-
type cap. Per explicit direction, forced category variety is not a goal:
if the best 10 picks tonight all happen to be the same prop type, that's
what ships. The corollary is that every scoring function has to be honest
about uncertainty on its own terms (sample-size penalties, confidence caps)
rather than relying on a downstream diversity cap to paper over an
overconfident score.

A "public-awareness discount" also actively de-emphasizes picks built mostly
on "star player, high season average" — the whole betting market already
prices that in — in favor of picks built from multiple non-obvious converging
signals on a less-obvious player. See score_signal_count() / apply_discount().

Every pick commits to an explicit projected number (not just a category label)
so it's both more useful and gradeable the next morning by grade_results.py
against actual box scores — see output/picks_YYYY-MM-DD.json.

No live sportsbook odds are fetched (evaluated and shelved — see README: even
the free tier of a legitimate odds API doesn't cover a full daily slate
without paying), so every pick is a direction/confidence call, not a priced
bet: always check the current line before betting.

Runs after mlb_daily.py in the same job. Thanks to pybaseball's on-disk
cache (already enabled, same cache dir within one job run), re-calling shared
fetchers here does not mean a second round of network traffic for anything
mlb_daily.py already pulled.
"""
import os, sys, json, re, unicodedata, math, functools
from datetime import datetime, timezone
from collections import defaultdict
import pandas as pd

import mlb_daily as m
import prop_probability as pp
import stable_base_rate as sbr

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)
PICKS_FILE = os.path.join(OUTPUT_DIR, f"top10_picks_{m.TODAY}.md")
PICKS_JSON_FILE = os.path.join(OUTPUT_DIR, f"picks_{m.TODAY}.json")
PLAYERS_DIR = os.environ.get("PLAYERS_DIR", "data/players")
PLAYER_SNAPSHOT_HISTORY_DAYS = 60  # bounds each player file's growth over a season
# Measured live from 93 completed games (7 days ending 2026-08-05) via
# statsapi.boxscore_data: 6227 AB / 6848 PA / 2397 TB.
#   AB/PA 0.9093 | league SLG (TB/AB) 0.3849 | league TB/PA 0.3500
# The previous LEAGUE_AVG_TB_PA of 0.38 was on the SLG (per-AB) scale despite
# being multiplied by plate appearances — the same per-AB/per-PA conflation
# fixed in project_batter_tb.
LEAGUE_AVG_TB_PA = 0.350    # league-average total bases per PA (measured)
AB_PER_PA = 0.9093          # measured; converts a per-AB rate (SLG) to per-PA
# LEAGUE_AVG_BF_PER_START is defined next to project_pitcher_workload(), with
# the measurement that produced it.

# ══════════════════════════════════════════════════════════════════════════
#  SCORING HELPERS
# ══════════════════════════════════════════════════════════════════════════

def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def _team_label(c):
    """Display text for a pick's "(TEAM)" parenthetical. Every candidate
    used to be one player on one team, so every markdown call site read
    c['team'] directly -- bracket access, not .get(), so it never raised
    on a MISSING key. score_combined_strikeouts's picks broke that
    assumption: team is explicitly None (the pick spans both teams), a
    PRESENT key with a None value, which bracket access happily returns
    and an f-string happily renders as the literal text "(None)". Found
    live in the category-picks table, the one place a combined_strikeouts
    pick was already confirmed to reach the board."""
    return c.get("team") or "combined"


def _sig(bag, name, raw, scaled):
    """Record one named signal on a candidate — ONLY when its underlying input
    actually existed.

    Added for backtest/engine.py, which needs the individual 0-100 sub-scores
    the weighted formula is built from (backtest/SCHEMA.md's `signals` field)
    so signal weights can be re-fit and pruned against real outcomes. Nothing
    here changes any score: every value recorded is the same number the
    scoring expression right above it already computed.

    ABSENT IS NOT ZERO AND NOT NEUTRAL. scale() deliberately returns the 0-100
    midpoint for a missing input, which is the right thing for *scoring* (a
    missing signal shouldn't push a pick either way) but is actively wrong for
    *fitting*: recording 50 for "no data" teaches the fitter that missing data
    is a real, average reading. So a signal whose raw input is None/NaN is
    simply not present in the bag — see SCHEMA.md and backtest/signals.py's
    impute-plus-indicator handling, which depends on that distinction."""
    if raw is None:
        return
    try:
        if float(raw) != float(raw):   # NaN
            return
    except (TypeError, ValueError):
        pass
    try:
        bag[name] = round(float(scaled), 4)
    except (TypeError, ValueError):
        return

def scale(value, lo, hi, out_lo=0, out_hi=100):
    """Linear map value in [lo,hi] to [out_lo,out_hi], clamped at the ends.

    Bug found and verified live: a real pandas NaN (as opposed to None) sailed
    straight through the "value is None" check and through float(value)
    (float('nan') doesn't raise), leaving t = nan and landing on
    clamp(nan, out_lo, out_hi). Python's min()/max() don't treat NaN as
    incomparable the way you'd expect: min(out_hi, nan) always returns out_hi
    (nan < out_hi is False, so the "no swap" branch wins), so clamp() silently
    returned out_hi — the MAXIMUM of the range — for every NaN input, not a
    neutral midpoint. Confirmed against tonight's real season-batting pull:
    89 real batters (Statcast-fallback data) have NaN Barrel%/HardHit% from
    too few batted-ball events, and multiple are in tonight's actual lineups
    (Abimelec Ortiz, Max Clark, Osleivis Basabe, Grant McCray). For Abimelec
    Ortiz specifically (5 PA, Barrel% literally NaN), the BASELINE SKILL
    sub-score computed as 65.0 pre-fix — i.e. missing data scored *better*
    than a real average Barrel% would have (a real 8% Barrel% scores ~33 on
    this same scale) — silently inflating a thin-sample player's score rather
    than treating the missing signal as neutral, exactly backwards from every
    other None-handling path in this file. Fixed by explicitly checking for
    NaN (x != x is True only for NaN) alongside the existing None check.
    Re-verified live post-fix: Ortiz's Barrel% component now scores neutral
    (50, same as the None case), pulling his overall skill sub-score down
    from 65.0 to 50.0 (wRC+/ISO are also None for him this thin-sample data
    pull, so all three components now correctly land at neutral)."""
    if value is None: return (out_lo + out_hi) / 2
    try: value = float(value)
    except (TypeError, ValueError): return (out_lo + out_hi) / 2
    if value != value: return (out_lo + out_hi) / 2  # NaN check
    if hi == lo: return (out_lo + out_hi) / 2
    t = (value - lo) / (hi - lo)
    return clamp(out_lo + t * (out_hi - out_lo), out_lo, out_hi)


# ══════════════════════════════════════════════════════════════════════════
#  DATA COLLECTION — tonight-scoped, reusing mlb_daily.py's fetchers
# ══════════════════════════════════════════════════════════════════════════

NWS_UA = {"User-Agent": "(full-count-mlb-pipeline, contact: github.com/werriesjacob1-cmyk/Full-Count)"}

def fetch_nws_weather(lat, lon, hour):
    """Second, independent weather source (National Weather Service — free,
    no key, US-only; every non-dome MLB park is in the US, and dome parks
    never reach this call). Verified live: api.weather.gov requires a
    descriptive User-Agent or it 403s, otherwise no auth needed. Used to
    cross-check Open-Meteo rather than trusted alone — either source can have
    stale or wrong grid data for a given hour, and disagreement itself is a
    signal worth surfacing rather than silently picking one."""
    try:
        r = m.retry_get(f"https://api.weather.gov/points/{lat},{lon}", headers=NWS_UA, timeout=15, retries=2)
        r.raise_for_status()
        hourly_url = r.json()["properties"]["forecastHourly"]
        r2 = m.retry_get(hourly_url, headers=NWS_UA, timeout=15, retries=2)
        r2.raise_for_status()
        periods = r2.json()["properties"]["periods"]
        target = next((p for p in periods
                        if datetime.fromisoformat(p["startTime"]).hour == hour), periods[0])
        wind_mph = float(target["windSpeed"].split()[0]) if target.get("windSpeed") else None
        return {"temp": target.get("temperature"), "wind_mph": wind_mph}
    except Exception as e:
        m.warn(f"NWS weather cross-check: {e}")
        return None


def park_hr_index(temp, wsp, wdir, humid, cf_deg, elev, dome):
    """The park/weather HR index itself, 0-100, plus the wind-effect label.

    Extracted verbatim from fetch_park_weather so backtest/engine.py can score
    a past date from historical weather observations without carrying a second
    copy of the formula — the only difference between the two callers is where
    temp/wind/humidity come from (a forecast tonight, an archive back then).
    Returns (index, wind_effect)."""
    # REAL BUG, found by test_park_hr_index.py: both real callers currently
    # short-circuit dome parks before ever reaching this function (each
    # hardcodes {"park_hr_index": 50, "wind_effect": "dome"} itself), which
    # is the only reason this was never hit live. Called directly with
    # dome=True, it wasn't neutral: m.wind_vs_field returns the string "DOME
    # — no wind effect", and the word WIND contains the substring "IN" --
    # so `"IN" in wvf.upper()` matched, and a dome game got scored as if
    # wind were blowing IN (a real, negative wsp*2.5 penalty on an indoor
    # game with no wind at all). Guarding here closes the gap for any future
    # caller that doesn't happen to duplicate the two existing callers' own
    # pre-filtering.
    if dome:
        return 50.0, "dome"
    wvf = m.wind_vs_field(wdir, cf_deg, dome)
    dens = m.air_density_pct(elev, temp, humid)
    idx_score = 50
    if "OUT" in wvf.upper(): idx_score += min(wsp * 2.5, 30)
    elif "IN" in wvf.upper(): idx_score -= min(wsp * 2.5, 25)
    if temp >= 85: idx_score += 8
    elif temp <= 45: idx_score -= 10
    idx_score += (1.0 - dens) * 100 * 0.3
    wind_effect = "out" if "OUT" in wvf.upper() else ("in" if "IN" in wvf.upper() else "neutral")
    return round(clamp(idx_score), 1), wind_effect


def fetch_park_weather(game_meta):
    """Per-matchup weather + park HR index. Same sources/logic as mlb_daily.py's
    Section 5, kept independent here rather than parsing that section's text.
    Cross-checked against a second source (NWS) below — see fetch_nws_weather."""
    out = {}
    seen = set()
    for gm in game_meta:
        venue = gm["venue"]
        sk = None
        for k in m.STADIUMS:
            if k.lower() in venue.lower() or venue.lower() in k.lower():
                sk = k; break
        if not sk or sk in seen: continue
        seen.add(sk)
        lat, lon, dome, team, cf_deg, elev, lf, cf_d, rf, lfw, cfw, rfw, foul, surf, humidor, eye, retract = m.STADIUMS[sk]
        # 2026-08-2X data-integrity fix: a retractable-roof park (retract=
        # True) is no longer force-treated as permanently closed. Real
        # per-game roof state comes from m.real_roof_status() (the MLB
        # Stats API's own weather.condition field) -- only a genuinely
        # closed roof (or a true fixed dome, retract=False) gets the
        # neutral/indoor treatment below.
        roof_status = m.real_roof_status(gm.get("mlb_weather_condition"), retract)
        if dome and not retract:
            out[gm["matchup"]] = {"dome": True, "park_hr_index": 50, "wind_effect": "dome", "temp": None,
                                   "roof_status": None}
            continue
        if dome and roof_status == "closed":
            out[gm["matchup"]] = {"dome": True, "park_hr_index": 50, "wind_effect": "dome", "temp": None,
                                   "roof_status": "closed"}
            continue
        # UNKNOWN-ROOF INTEGRITY FIX (2026-08-26, PR #67 release-candidate
        # review): "unknown" used to fall through to the real outdoor-weather
        # fetch below, same as a confirmed-open roof -- awarding directional
        # temp/wind/HR credit as though open were known, with only a customer-
        # facing caveat sentence, not a neutral model input. Audited: MLB's
        # weather.condition field is commonly still completely blank 8-12
        # hours before first pitch (verified live, 2026-08-26: all 4 of that
        # day's still-to-play retractable-park games showed condition=None at
        # T-8.2h to T-11.7h) -- squarely inside this pipeline's own daily
        # generation window (14:30 UTC and later cron runs, `.github/
        # workflows/mlb-daily.yml`). And "unknown" is NOT evidence of "open":
        # a real 8-day/7-park eventual-outcome sample found 4 of the 7
        # retractable parks (Chase Field, Daikin Park, Globe Life Field,
        # loanDepot park) closed in 100% of observed games, T-Mobile Park
        # open in 100%, and the rest mixed -- concretely, tonight's real
        # unknown-roof loanDepot park forecast (86F, 6.8mph wind blowing out)
        # would have scored park_hr_index=76.6 (a real HR-boost "why" line)
        # under the old policy, at a park that was CLOSED in all 4 sampled
        # games this season -- directional credit built on absence of
        # evidence, not evidence itself. Absence of a "closed" reading is not
        # affirmative evidence the roof is open. Per the 11/11 audit already
        # in real_roof_status()'s own docstring: a PRESENT non-closed reading
        # is real evidence of open (kept as-is, unchanged). A MISSING reading
        # is not that -- it is no evidence either way.
        #
        # Fix: unknown now gets the same neutral numeric treatment as a
        # confirmed-closed roof (dome=False here, not True, so downstream
        # consumers -- the rain-risk QC checks, the dashboard game-weather
        # widget -- correctly see "no weather data", never a fabricated
        # "confirmed dome/closed" signal; roof_status stays "unknown", never
        # silently rewritten to "closed"). generate_picks.score_batter()'s
        # explicit roof_status=="unknown" watchout (search that function)
        # already surfaces the real uncertainty to the customer; its wording
        # was updated in the same commit to stop saying "assumes the roof is
        # open" now that it no longer does. A later refresh that obtains a
        # real MLB condition string naturally re-classifies as open/closed on
        # its own next call -- no special-cased update path needed, since
        # every candidate is regenerated from live inputs each run, not
        # patched in place.
        if dome and roof_status == "unknown":
            out[gm["matchup"]] = {"dome": False, "park_hr_index": 50, "wind_effect": "unknown", "temp": None,
                                   "roof_status": "unknown"}
            continue
        try:
            r = m.retry_get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon,
                "hourly": "temperature_2m,windspeed_10m,winddirection_10m,relativehumidity_2m,precipitation_probability",
                "temperature_unit": "fahrenheit", "windspeed_unit": "mph",
                "timezone": "auto", "forecast_days": 1,
            }, timeout=20, retries=2)
            r.raise_for_status()
            meteo = r.json(); h = meteo["hourly"]
            idx = m.forecast_hour_index(gm.get("game_start_utc"), meteo)
            temp = h["temperature_2m"][idx]; wsp = h["windspeed_10m"][idx]
            wdir = h["winddirection_10m"][idx]; humid = h["relativehumidity_2m"][idx]
            precip_prob = h.get("precipitation_probability", [None]*24)[idx]

            wx_disagreement = None
            nws = fetch_nws_weather(lat, lon, gm["hour"])
            if nws and nws.get("temp") is not None:
                temp_diff = abs(nws["temp"] - temp)
                if temp_diff >= 10:
                    wx_disagreement = f"Open-Meteo {temp:.0f}F vs NWS {nws['temp']:.0f}F — sources disagree, treat weather read with caution"
                else:
                    temp = round((temp + nws["temp"]) / 2, 1)  # reconcile: average when sources agree
                if nws.get("wind_mph") is not None and (wx_disagreement is None):
                    wsp = round((wsp + nws["wind_mph"]) / 2, 1)

            # dome is always False on this path now -- either a real
            # open-air park, or a retractable roof confirmed/assumed open.
            idx_score, wind_effect = park_hr_index(temp, wsp, wdir, humid, cf_deg, elev, False)
            out[gm["matchup"]] = {"dome": False, "park_hr_index": idx_score,
                                   "wind_effect": wind_effect, "temp": temp, "wind_mph": wsp,
                                   "wx_disagreement": wx_disagreement, "precip_prob": precip_prob,
                                   "roof_status": roof_status if retract else None}
        except Exception as e:
            m.warn(f"Picks weather {sk}: {e}")
            out[gm["matchup"]] = {"dome": False, "park_hr_index": 50, "wind_effect": "unknown", "temp": None,
                                   "roof_status": roof_status if retract else None}
    return out


def fetch_umpire_scores(game_meta):
    """HP umpire accuracy/consistency per matchup, reusing the verified UmpScorecards API."""
    out = {}
    try:
        r = m.retry_get("https://umpscorecards.com/api/umpires",
                        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        by_name = {row.get("umpire"): row for row in r.json().get("rows", [])}
    except Exception as e:
        m.warn(f"Picks umpire data: {e}")
        return out
    for gm in game_meta:
        u = by_name.get(gm.get("hp_ump"))
        if u:
            out[gm["matchup"]] = {"accuracy": u.get("overall_accuracy_wmean"),
                                   "consistency": u.get("consistency_wmean")}
    return out


# ── Team run-scoring distribution, MEASURED (not assumed) ────────────────
# Measured live from 186 completed MLB games (372 team-games) in the 14 days
# ending 2026-08-05, via m.statsapi.boxscore_data:
#     mean team runs/game = 4.245, variance = 9.679
# Variance/mean = 2.28, so team runs are strongly OVER-DISPERSED relative to
# Poisson — a Poisson inversion of a betting line would be materially wrong.
# A negative binomial with var = mu + mu^2/k matches both moments at
#     k = mu^2 / (var - mu) = 4.245^2 / (9.679 - 4.245) = 3.317
# k is held fixed and mu solved for; that is the only free parameter.
LEAGUE_TEAM_RUNS_MEAN = 4.245
LEAGUE_TEAM_RUNS_VAR = 9.679
TEAM_RUNS_NB_K = 3.317


def _american_to_prob(o):
    """American odds -> implied probability (still vig-inclusive)."""
    try: o = float(o)
    except (TypeError, ValueError): return None
    if o != o or o == 0: return None
    return (-o) / ((-o) + 100) if o < 0 else 100 / (o + 100)


def _implied_total_from_line(line, over_odds, under_odds):
    """Convert a per-team runs line (always a half-run, e.g. 3.5/4.5) plus its
    two-sided American odds into an actual implied expected runs total.

    The raw line alone is far too coarse to use directly: on tonight's real
    slate, 9 of 15 games priced BOTH teams at 3.5 or both at 4.5, so the line
    by itself cannot distinguish a 3.1-run team from a 3.9-run team. The odds
    carry that information. Verified live tonight: Toronto over 3.5 is +114 /
    under 3.5 is -145 (a team the market expects BELOW 3.5), while Houston
    over 4.5 is -125 / under 4.5 is +110 (expected ABOVE 4.5). Same slate,
    lines one run apart, but the true gap is wider than one run.

    Method: de-vig the two-sided price to a fair P(runs > line), then solve
    numerically for the negative-binomial mean mu that reproduces that
    probability, with the dispersion fixed at the MEASURED league value above.
    No fitted-to-results constants; the only inputs are tonight's live prices
    and a run distribution measured from real box scores.

    The negative binomial itself was validated against the same 372 real
    team-games before being used (empirical P(R>=n) vs NB prediction):
        R>=3  .6640 / .6673   R>=5  .3978 / .3920   R>=7  .2070 / .2032
    Poisson, by contrast, is badly wrong in the tail that matters here
    (R>=7: .1377 predicted vs .2070 actual), which is why it isn't used.

    KNOWN BIAS, found by verification and corrected by the caller: this
    solver run on raw prices returns team totals that are systematically ~0.34
    runs/team HIGH. Measured on tonight's live slate — across the 12 games
    with a clean two-sided pair for both teams, the summed team implieds
    averaged 9.304 against a mean game total of 8.625 (+0.68/game). The cause
    is that the dispersion measured above is the MARGINAL variance across all
    team-games, which includes between-game variation in the true mean; the
    within-game conditional distribution is tighter, and a tighter, less
    right-skewed distribution maps a given P(over) to a lower mean. Rather
    than invent a conditional dispersion, the caller renormalizes each game's
    two team totals to sum to that game's own game-total line (see
    fetch_public_betting_bias) — the market's own anchor, exact per game, and
    it leaves this solver responsible only for the SPLIT between the two
    teams, which is far less sensitive to the dispersion constant.

    Returns None (never a guessed number) if odds are missing."""
    if line is None or over_odds is None or under_odds is None:
        return None
    try:
        line = float(line); over_odds = float(over_odds); under_odds = float(under_odds)
    except (TypeError, ValueError):
        return None
    if line != line or over_odds != over_odds or under_odds != under_odds:
        return None  # NaN guard (see scale() docstring — NaN has bitten this file before)

    p_over_raw = _american_to_prob(over_odds)
    p_under_raw = _american_to_prob(under_odds)
    if not p_over_raw or not p_under_raw: return None
    p_over = p_over_raw / (p_over_raw + p_under_raw)   # de-vig
    # Guard against a degenerate price producing an absurd mean.
    p_over = min(max(p_over, 0.02), 0.98)

    try:
        from scipy.stats import nbinom
    except Exception:
        # Graceful degradation: without scipy, fall back to the raw line.
        return round(line, 2)

    k = TEAM_RUNS_NB_K
    # P(X > line) with a half-run line == P(X >= ceil(line)) == sf(floor(line))
    import math
    floor_line = math.floor(line)

    def p_over_at(mu):
        # nbinom(n=k, p) with mean mu -> p = k/(k+mu)
        return float(nbinom.sf(floor_line, k, k / (k + mu)))

    lo, hi = 0.3, 12.0
    if p_over_at(lo) > p_over: return round(lo, 2)
    if p_over_at(hi) < p_over: return round(hi, 2)
    for _ in range(60):
        mid = (lo + hi) / 2
        if p_over_at(mid) < p_over: lo = mid
        else: hi = mid
    return round((lo + hi) / 2, 2)


def fetch_public_betting_bias(game_meta):
    """Public vs. sharp-money divergence on the moneyline, from Action
    Network's public scoreboard API — unofficial (no published docs) but
    openly served to their own website with no auth, verified live: real
    per-team tickets%/money% data confirmed on a live slate, team names
    ('Athletics', 'New York Mets', etc.) matching this pipeline's own naming
    exactly, no mapping table needed.

    tickets% = share of individual bets on a side; money% = share of dollars
    wagered. When money% runs well ahead of tickets% on a side, professional
    ('sharp') money is backing it despite less public support — a genuinely
    different signal type than anything else here (market-derived, not
    stats-derived). Used as a small, transparent nudge only, per explicit
    direction: edge 'should not be completely ignored' but isn't the primary
    filter — this is not folded into the weighted formula's core components.

    ALSO parses the implied team totals carried in the *same* response, which
    this function previously downloaded and threw away every single run. Under
    g['markets']['15']['event'] the response also carries 'total' (the game
    over/under) and 'core_bet_type_6_team_score' (per-team runs lines, each
    entry with team_id / side / value / odds). Verified live on tonight's real
    slate: 15/15 games returned both, all 30 team names matching this
    pipeline's own naming with no mapping table needed.

    An implied team total is the single most information-dense environment
    input available here: it is the market's own forecast of runs scored by
    that specific team tonight, and it already prices park, weather, the
    opposing starter, the opposing bullpen and lineup strength simultaneously
    — all of which this file otherwise estimates piecemeal and independently.

    Each team entry gets: implied_total (de-vigged, see
    _implied_total_from_line), implied_total_line (the raw half-run line),
    and game_total. The sharp-money keys are unchanged and are still written
    even when the totals markets are absent, so the existing divergence signal
    is untouched by this addition."""
    out = {}
    try:
        r = m.retry_get("https://api.actionnetwork.com/web/v2/scoreboard/mlb",
                        params={"bookIds": 15, "date": m.TODAY.replace("-", "")},
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=20, retries=2)
        r.raise_for_status()
        games = r.json().get("games", [])
    except Exception as e:
        m.warn(f"Public betting bias (Action Network): {e}")
        return out
    for g in games:
        teams = {t.get("id"): t.get("full_name") for t in g.get("teams", [])}
        try:
            ev = g["markets"]["15"]["event"]
        except (KeyError, TypeError):
            continue

        # -- existing sharp-money signal (unchanged) --
        for entry in (ev.get("moneyline") or []):
            team_name = teams.get(entry.get("team_id"))
            bi = entry.get("bet_info", {})
            tickets = bi.get("tickets", {}).get("percent")
            money = bi.get("money", {}).get("percent")
            if team_name and tickets is not None and money is not None:
                out.setdefault(team_name, {}).update(
                    {"tickets_pct": tickets, "money_pct": money,
                     "sharp_divergence": money - tickets})

        # -- implied team totals (new; same response, previously discarded) --
        # The 'total' market can carry an ALT line alongside the main one
        # (verified live tonight: 4 of 15 games listed two, e.g. TOR@HOU
        # quoted both 8 and 9.5). Picking by frequency or by min/max both
        # pick wrong. The main line is the one priced closest to even money,
        # since that is what a book balances its total to; an alt line sits
        # far off 50/50 (TOR@HOU: the 8 was -114/-108, the 9.5 was +130/-174).
        game_total = None
        by_val = defaultdict(dict)
        for e in (ev.get("total") or []):
            v, s, o = e.get("value"), e.get("side"), e.get("odds")
            if v is not None and s in ("over", "under") and o is not None:
                by_val[v][s] = o
        best_skew = None
        for v, sides in by_val.items():
            if "over" not in sides or "under" not in sides: continue
            po = _american_to_prob(sides["over"]); pu = _american_to_prob(sides["under"])
            if not po or not pu: continue
            skew = abs(po / (po + pu) - 0.5)
            if best_skew is None or skew < best_skew:
                best_skew, game_total = skew, v
        # Group the per-team runs market by team, keeping only entries whose
        # over and under share the same line (a mismatched pair is an alt line
        # and cannot be de-vigged against each other).
        by_team = defaultdict(dict)
        for e in (ev.get("core_bet_type_6_team_score") or []):
            tn = teams.get(e.get("team_id"))
            side = e.get("side")
            if not tn or side not in ("over", "under"): continue
            by_team[tn][side] = (e.get("value"), e.get("odds"))
        raw = {}
        for tn, sides in by_team.items():
            rec = out.setdefault(tn, {})
            if game_total is not None:
                rec["game_total"] = game_total
            ov, un = sides.get("over"), sides.get("under")
            if not ov or not un or ov[0] != un[0]:
                continue
            implied = _implied_total_from_line(ov[0], ov[1], un[1])
            if implied is not None:
                rec["implied_total_line"] = ov[0]
                raw[tn] = implied

        # Renormalize to the market's own game total (see the KNOWN BIAS note
        # in _implied_total_from_line). Only possible when BOTH teams priced
        # cleanly and a game total exists; otherwise keep the raw solve and
        # flag it, rather than silently shipping a differently-scaled number
        # next to normalized ones.
        s = sum(raw.values())
        if len(raw) == 2 and game_total and s > 0:
            f = game_total / s
            for tn, v in raw.items():
                out[tn]["implied_total"] = round(v * f, 2)
                out[tn]["implied_total_normalized"] = True
        else:
            for tn, v in raw.items():
                out[tn]["implied_total"] = v
                out[tn]["implied_total_normalized"] = False
    return out


def _bullpen_role_classifier(pit_season_df):
    """callable(name, person_id) -> True/False/None for _bullpen_fetch_one()'s
    is_rotation_starter parameter (bullpen ROLE AUDIT, 2026-08-26): a real,
    already-established convention in THIS codebase (mlb_daily.py's stadium
    role split and compute_bullpen_era() below both already use gamesStarted/
    games >= 0.5 to call a pitcher a "starter") applied to a new call site,
    not a new heuristic. Reuses lookup_player()'s already-trusted MLBAM-id-
    first/name-fallback matching -- no new fetch, no name-matching risk this
    codebase hasn't already measured and closed elsewhere.

    None whenever the season frame lacks G/GS for this pitcher (most notably
    the Statcast-fallback pitching frame used when FanGraphs 403s, which
    carries no games/starts columns at all) -- degrades to the exact prior
    behavior (always exclude the game's first pitcher) rather than ever
    guessing from an absent signal."""
    if pit_season_df is None or pit_season_df.empty:
        return None
    lookup = name_lookup(pit_season_df)

    def classify(name, person_id):
        row = lookup_player(lookup, name, person_id)
        if not row:
            return None
        g, gs = row.get("G"), row.get("GS")
        if not g or g <= 0 or gs is None:
            return None
        return (gs / g) >= 0.5
    return classify


def fetch_bullpen_scores(game_meta, pit_season_df=None):
    """Reuses mlb_daily.py's already-fixed, parallelized bullpen fetch directly.

    pit_season_df (optional): season-to-date pitching frame, used only to
    build a role classifier so a real opener/bulk-reliever isn't
    misclassified as "that game's starter, not a reliever" -- see
    _bullpen_role_classifier()'s own docstring and _bullpen_fetch_one()'s
    ROLE AUDIT comment. Omitting it (existing callers, existing tests)
    preserves the exact prior behavior."""
    teams_seen = {}
    jobs = []
    for gm in game_meta:
        for team_name in (gm["away_team"], gm["home_team"]):
            if team_name in teams_seen: continue
            try:
                team_data = m.statsapi.lookup_team(team_name)
                if team_data:
                    jobs.append((team_name, team_data[0]["id"]))
                    teams_seen[team_name] = True
            except Exception:
                pass
    out = {}
    if jobs:
        role_classifier = _bullpen_role_classifier(pit_season_df)
        fetch_one = functools.partial(m._bullpen_fetch_one, is_rotation_starter=role_classifier)
        with m.ThreadPoolExecutor(max_workers=10) as ex:
            for team_name, usage, err in ex.map(fetch_one, jobs):
                if usage:
                    fatigued = sum(1 for u in usage.values() if u["pitches"] > 60)
                    out[team_name] = {
                        "fatigued_relievers": fatigued, "tracked": len(usage),
                        # Real bug, found 2026-08-26 (detailed-bullpen-presentation
                        # audit): mlb_daily._bullpen_fetch_one() already fetches each
                        # reliever's real name and per-game (date/ip/pitches) usage --
                        # the exact detail a customer-facing "name real relievers, not
                        # vague copy" pass needs (see that function's own 2026-08-25
                        # comment, which added the "games" list for exactly this
                        # reason) -- but this function discarded all of it down to two
                        # bare counts. Direct instruction: "Jacob specifically wants
                        # names and context... Cade Smith, 27 pitches yesterday, 3
                        # appearances in 4 days." Surfaced here as its own field,
                        # additive only -- fatigued_relievers/tracked (what scoring
                        # actually uses) are unchanged.
                        "relievers": _reliever_detail(usage),
                    }
    return out


def _reliever_detail(usage):
    """Real per-reliever usage facts for a team's bullpen, from the same
    `usage` dict fetch_bullpen_scores() already has -- see that function's
    own comment for the "computed, then discarded" bug this closes. Sorted
    by most-recently-used first (the read a bettor actually wants: who
    pitched last night, not an alphabetical list), capped at 8 -- a whole
    bullpen's raw usage table is not "context," it's noise past the real
    late-inning-relevant names.

    `games` entries are chronological (oldest first, matching
    _bullpen_fetch_one()'s own append order), so games[-1] is always the
    most recent outing. Never fabricates a role ("closer", "likely to
    appear") -- that would require a real, verified role model this
    function does not have; only real, dated facts are reported."""
    out = []
    for name, u in usage.items():
        games = u.get("games") or []
        if not games:
            continue
        last = games[-1]
        days_ago = None
        if last.get("date"):
            try:
                d = datetime.strptime(last["date"], "%Y-%m-%d")
                days_ago = (datetime.now() - d).days
            except (TypeError, ValueError):
                days_ago = None
        out.append({
            "name": name, "pitches_last_outing": last.get("pitches"),
            "days_since_last_outing": days_ago, "appearances_l7": u.get("apps"),
            "pitches_l7": u.get("pitches"),
        })
    out.sort(key=lambda r: (r["days_since_last_outing"] if r["days_since_last_outing"] is not None else 999))
    return out[:8]


def fetch_l7_batter_form():
    """L7 rolling batter form, keyed by MLBAM batter ID — same window mlb_daily.py's
    Section 15 uses. Deliberately NOT keyed by name: on pyb.statcast()'s raw
    pitch-by-pitch output, the "player_name" column is the *pitcher* for that
    pitch, not the batter (a well-known Statcast quirk) — grouping by it silently
    builds a pitcher-keyed table, so every batter-name lookup misses. The
    numeric "batter" column is the actual batter MLBAM ID, which lineup entries
    already carry from fetch_lineups().

    Bug found and verified live: filtering rows to launch_speed.notna() alone
    (as this used to do) also keeps foul balls — a foul has a real exit velo
    but does NOT end the plate appearance, so counting it as a "PA" row
    double-counts. Live check on the real L7 window: 9158 rows had a non-null
    launch_speed, but only 4820 of them were description=="hit_into_play"
    (i.e. PA-terminating); the other 4338 were fouls. For Yordan Alvarez
    specifically this inflated his L7 "PA" from 18 (real balls in play) to 30,
    which cut his computed AVG from a true 9/18=0.500 down to a wrong
    9/30=0.300, and did the same (understated) damage to TB_per_PA and
    barrel_pct — silently deflating recent-form scores and the TB projection
    for every batter with any foul balls in the window (nearly everyone), with
    no error or empty result to flag it. Fixed by also requiring
    events.notna(), restricting every rate in this table to PA-terminating
    batted balls only. Re-verified live post-fix: Alvarez's L7 PA is now 18
    (matches the hit_into_play count exactly) and AVG is 0.5, matching a
    hand-computed H/PA check on the raw Statcast rows."""
    try:
        df = m.pyb.statcast(start_dt=m.L7_START, end_dt=m.L7_END)
        if df is None or df.empty: return {}
        batted = df[df["launch_speed"].notna() & df["events"].notna()].copy()
        tb_map = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
        batted["tb"] = batted["events"].map(tb_map).fillna(0)
        form = batted.groupby("batter").agg(
            PA=("at_bat_number", "count"),
            H=("events", lambda x: x.isin(["single", "double", "triple", "home_run"]).sum()),
            TB=("tb", "sum"),
            avg_EV=("launch_speed", "mean"),
            barrel_cnt=("launch_speed", lambda x: (x >= 98).sum()),
        )
        form["AVG"] = (form["H"] / form["PA"]).round(3)
        form["TB_per_PA"] = (form["TB"] / form["PA"]).round(3)
        form["barrel_pct"] = (form["barrel_cnt"] / form["PA"] * 100).round(1)
        return form.to_dict("index")  # keyed by batter MLBAM id
    except Exception as e:
        m.warn(f"Picks L7 batter form: {e}")
        return {}


def fetch_l14_pitcher_form(pitcher_ids):
    """L14 K rate + times-through-order K% split per tonight's starters, via
    targeted per-pitcher Statcast pulls. TTO bucketing reuses the same
    at-bat-number proxy mlb_daily.py's Section 37 uses (groups of 9 batters
    faced ~= one time through a standard lineup)."""
    out = {}
    for name, pid in pitcher_ids.items():
        if not pid: continue
        try:
            df = m.pyb.statcast_pitcher(start_dt=m.L14_START, end_dt=m.L14_END, player_id=pid)
            if df is None or df.empty: continue
            pa = df[df["events"].notna()].copy()
            if len(pa) == 0: continue
            k_pct = round((pa["events"] == "strikeout").sum() / len(pa) * 100, 1)
            entry = {"l14_k_pct": k_pct, "l14_pa": len(pa)}

            # Per-pitcher WORKLOAD (batters faced per start). Free — this is
            # the same dataframe already pulled above, no extra network.
            #
            # A game_date is only counted as a START if the pitcher appears in
            # inning 1. This filter is not cosmetic: verified live tonight,
            # grouping naively by game_date put Drew Anderson at "5 starts,
            # 5.2 BF/start" (he is a reliever who made zero starts in the L14
            # window) and dragged Will Warren to 14.7 BF/start by mixing a
            # 4-batter relief outing in with two real starts. Across tonight's
            # 30 listed starters, the naive mean was 21.18 BF/start; filtered
            # to real starts it is 23.10 — the relief contamination alone was
            # worth ~2 batters faced, i.e. roughly half a strikeout.
            if "inning" in pa.columns:
                bf_per_start = []
                for _, sub in pa.groupby("game_date"):
                    if sub["inning"].min() == 1:      # was in the game from the 1st
                        bf_per_start.append(len(sub))
                if bf_per_start:
                    entry["bf_per_start"] = round(sum(bf_per_start) / len(bf_per_start), 1)
                    entry["n_starts"] = len(bf_per_start)
            if "at_bat_number" in pa.columns:
                pa["tto"] = ((pa["at_bat_number"] - 1) // 9 + 1).clip(upper=3).astype(int)
                tto3 = pa[pa["tto"] == 3]
                tto1 = pa[pa["tto"] == 1]
                if len(tto3) >= 10 and len(tto1) >= 10:
                    k1 = round((tto1["events"] == "strikeout").sum() / len(tto1) * 100, 1)
                    k3 = round((tto3["events"] == "strikeout").sum() / len(tto3) * 100, 1)
                    entry["tto1_k_pct"] = k1
                    entry["tto3_k_pct"] = k3
            out[name] = entry
        except Exception:
            continue
    return out


def fetch_sprint_speed():
    """Season sprint speed (ft/s), keyed by MLBAM id. Verified live."""
    try:
        df = m.pyb.statcast_sprint_speed(m.YEAR, min_opp=5)
        if df is None or df.empty: return {}
        return dict(zip(df["player_id"], df["sprint_speed"]))
    except Exception as e:
        m.warn(f"Picks sprint speed: {e}")
        return {}


def fetch_catcher_poptime():
    """Season catcher pop-time-to-2B on stolen base attempts, keyed by MLBAM
    id. Lower = faster/stronger arm = worse for the runner. Verified live."""
    try:
        df = m.pyb.statcast_catcher_poptime(m.YEAR, min_2b_att=3, min_3b_att=0)
        if df is None or df.empty: return {}
        return dict(zip(df["entity_id"], df["pop_2b_sba"]))
    except Exception as e:
        m.warn(f"Picks catcher poptime: {e}")
        return {}


def fetch_first_inning_form(pitcher_ids):
    """Per-starter first-inning results over the FULL SEASON, keyed by pitcher
    name (consistent with fetch_l14_pitcher_form).

    WHY THIS USES THE SEASON AND NOT THE L14 WINDOW. It used to reuse the same
    14-day pull as the other recent-form tables, which for a starting pitcher
    is two or three starts. Two starts cannot express a rate: the only values
    representable are 0%, 50% and 100%, so every starter came back at one of
    three numbers and most came back at exactly 0% -- a "100% certain" NRFI
    read built on two innings of evidence.

    That was not a cosmetic problem. Once the board began ranking on
    probability, EIGHT of the ten picks were first-inning leans carrying the
    identical figure of 0.8308, because with no real information in any of
    them the estimate collapsed to the prior for all eight. Eight identical
    numbers is the model reporting that it has no opinion, dressed as
    conviction.

    Measured on the season pull instead (62,481 first-inning pitch rows, 3,670
    game-halves): 214 starters have 5+ first innings, median 17, max 26, and
    the rate actually spreads -- p10 0.125, p25 0.182, p50 0.280, p75 0.382,
    p90 0.488. That spread is the signal, and the 14-day window was
    structurally incapable of seeing it.

    Costs nothing extra: fetch_season_statcast() is already pulled and cached
    for other sections, so this is a groupby over data that is in memory."""
    out = {}
    try:
        df = m.fetch_season_statcast()
        if df is None or df.empty:
            return out
        need = {"inning", "pitcher", "game_pk", "bat_score", "post_bat_score"}
        if not need.issubset(df.columns):
            m.warn("First-inning form: season Statcast is missing required columns")
            return out
        i1 = df[df["inning"] == 1]
        if i1.empty:
            return out
        # Vectorised on purpose. The equivalent groupby().apply() with a lambda
        # over 62k rows did not finish inside two minutes; .agg() of two
        # built-in reducers returns in seconds.
        g = i1.groupby(["pitcher", "game_pk"]).agg(
            hi=("post_bat_score", "max"), lo=("bat_score", "min"))
        g["runs"] = g["hi"] - g["lo"]
        g = g.dropna(subset=["runs"])
        if g.empty:
            return out
        g["yrfi"] = (g["runs"] > 0).astype(float)
        per = g.groupby("pitcher")["yrfi"].agg(["size", "mean"])
        runs_per = g.groupby("pitcher")["runs"].mean()
        by_id = {}
        for pid, row in per.iterrows():
            if row["size"] < 2:
                continue
            by_id[int(pid)] = {"n_starts": int(row["size"]),
                               "runs_per_1st_inning": round(float(runs_per.loc[pid]), 2),
                               "yrfi_rate": round(float(row["mean"]) * 100, 1)}
        for name, pid in pitcher_ids.items():
            if pid and int(pid) in by_id:
                out[name] = by_id[int(pid)]
    except Exception as e:
        m.warn(f"First-inning season form: {e}")
    return out


def fetch_bat_speed_trends():
    """Bat-speed delta over the L14 window (2nd half mean minus 1st half mean),
    keyed by batter MLBAM id. A leading indicator: rising bat speed tends to
    precede a batting-average uptick, versus recent-form stats which only show
    a player who's *already* hot. Verified live: pyb.statcast()'s "bat_speed"
    column is keyed correctly by "batter" (unlike player_name)."""
    try:
        df = m.pyb.statcast(start_dt=m.L14_START, end_dt=m.L14_END)
        if df is None or df.empty or "bat_speed" not in df.columns: return {}
        bt = df[df["bat_speed"].notna()].copy()
        if bt.empty: return {}
        bt["game_date"] = pd.to_datetime(bt["game_date"])
        midpoint = bt["game_date"].min() + (bt["game_date"].max() - bt["game_date"].min()) / 2
        early = bt[bt["game_date"] < midpoint].groupby("batter")["bat_speed"].agg(["mean", "count"])
        late = bt[bt["game_date"] >= midpoint].groupby("batter")["bat_speed"].agg(["mean", "count"])
        out = {}
        for bid in set(early.index) & set(late.index):
            if early.loc[bid, "count"] < 5 or late.loc[bid, "count"] < 5: continue
            out[bid] = round(late.loc[bid, "mean"] - early.loc[bid, "mean"], 2)
        return out
    except Exception as e:
        m.warn(f"Picks bat speed trend: {e}")
        return {}


def fetch_pitch_type_exploits():
    """Batter-vs-pitch-type performance (run value/100, hard-hit%) cross-
    referenced against each pitcher's actual arsenal mix. Verified live against
    both endpoints. Returns (batter_arsenal, pitcher_arsenal) lookups."""
    batter_arsenal = defaultdict(dict)  # batter_id -> {pitch_type: {...}}
    pitcher_arsenal = defaultdict(list)  # pitcher_id -> [(pitch_type, usage_pct), ...]
    try:
        bdf = m.pyb.statcast_batter_pitch_arsenal(m.YEAR, minPA=25)
        if bdf is not None and not bdf.empty:
            for _, row in bdf.iterrows():
                batter_arsenal[row["player_id"]][row["pitch_type"]] = {
                    "run_value_per_100": row.get("run_value_per_100"),
                    "hard_hit_percent": row.get("hard_hit_percent"),
                    "whiff_percent": row.get("whiff_percent"),
                }
    except Exception as e:
        m.warn(f"Picks batter pitch arsenal: {e}")
    try:
        pdf = m.pyb.statcast_pitcher_pitch_arsenal(m.YEAR, minP=100, arsenal_type="n_")
        if pdf is not None and not pdf.empty:
            pitch_cols = [c for c in pdf.columns if c.startswith("n_")]
            for _, row in pdf.iterrows():
                for c in pitch_cols:
                    usage = row.get(c)
                    if pd.notna(usage) and usage >= 15:
                        pitcher_arsenal[row["pitcher"]].append((c[2:].upper(), round(usage, 1)))
    except Exception as e:
        m.warn(f"Picks pitcher pitch arsenal: {e}")
    return dict(batter_arsenal), dict(pitcher_arsenal)


def find_pitch_type_exploit(batter_id, pitcher_id, batter_arsenal, pitcher_arsenal):
    """Returns the single best pitch-type exploit for this batter-vs-pitcher
    matchup, or None. "Exploit" = the pitcher throws this pitch >=15% of the
    time AND the batter has a clearly positive run-value track record against
    it specifically — a signal invisible from either player's overall line."""
    b_pitches = batter_arsenal.get(batter_id)
    p_pitches = pitcher_arsenal.get(pitcher_id)
    if not b_pitches or not p_pitches: return None
    best = None
    for pitch_type, usage in p_pitches:
        stats = b_pitches.get(pitch_type)
        if not stats or stats.get("run_value_per_100") is None: continue
        rv = stats["run_value_per_100"]
        if rv >= 1.5 and (best is None or rv > best["run_value_per_100"]):
            best = {"pitch_type": pitch_type, "usage_pct": usage, "run_value_per_100": rv,
                    "hard_hit_percent": stats.get("hard_hit_percent")}
    return best


def estimate_lineup_k_pct(lineup, batter_lookup):
    """Fallback for opposing-team K% when FanGraphs' team-batting page is
    unreachable. Verified live: this is a real, frequent failure independent
    of the individual batting/pitching leaderboards — FanGraphs' team-level
    endpoints (pyb.team_batting / fg_team_batting_data) came back empty on a
    run where the individual per-player pages (which already carry their own
    Statcast fallback) succeeded fine. Rather than add a third team-level
    source, derive the number directly from tonight's confirmed lineup's own
    K% (already fetched, per player) — arguably more accurate than a season
    team average anyway, since it reflects who's actually in the lineup
    tonight rather than the full-season roster."""
    vals = [row["K%"] for b in lineup
            if (row := lookup_player(batter_lookup, b.get("name"), b.get("id")))
            and row.get("K%") is not None]
    if not vals:
        return None, 0
    return round(sum(vals) / len(vals), 1), len(vals)


def compute_bullpen_era(pit_season_df):
    """Team bullpen quality (IP-weighted ERA of relievers, GS==0), aggregated
    from individual pitcher season data we already fetch — rather than
    depending on FanGraphs' team-pitching page, which is unreliable
    independent of the individual leaderboards it would need instead (found
    on review: FanGraphs' team-level endpoints failed on every real run
    tonight while the individual pages worked fine; a second scraped source,
    Baseball-Reference, was checked live and is equally blocked, so another
    external team-level page isn't a real fix here — deriving from data we
    already have and already trust is). Distinguishes bullpen *quality* from
    bullpen *fatigue* (fetch_bullpen_scores): a tired elite bullpen and a
    tired bad bullpen are not the same matchup. Returns {} when the columns
    this needs aren't present — i.e. FanGraphs individual pitching itself
    fell back to Statcast, which doesn't carry Team/G/GS — same
    degrade-gracefully discipline as every other signal here."""
    if pit_season_df is None or pit_season_df.empty:
        return {}
    needed = {"Team", "G", "GS", "ERA", "IP"}
    if not needed.issubset(pit_season_df.columns):
        return {}
    relievers = pit_season_df[(pit_season_df["GS"] == 0) & (pit_season_df["IP"] > 0)]
    by_abbr = {}
    for team, grp in relievers.groupby("Team"):
        total_ip = grp["IP"].sum()
        if total_ip < 30:  # too thin a sample to trust a team ERA read
            continue
        era = round((grp["ERA"] * grp["IP"]).sum() / total_ip, 2)
        by_abbr[team] = {"era": era, "ip": round(total_ip, 1), "n_relievers": len(grp)}
    # FanGraphs' "Team" column uses its own abbreviations, which diverge from
    # the MLB Stats API's in a handful of known cases (CHW/CWS, KCR/KC, SDP/SD,
    # SFG/SF, TBR/TB, WSN/WSH) — bridged here rather than assumed, so this
    # doesn't silently never match tonight's games.
    fg_to_official_abbr = {"CHW": "CWS", "KCR": "KC", "SDP": "SD", "SFG": "SF", "TBR": "TB", "WSN": "WSH"}
    abbr_to_name = {t["abbr"]: t["name"] for t in m.get_team_ids()}
    out = {}
    for fg_abbr, data in by_abbr.items():
        official_abbr = fg_to_official_abbr.get(fg_abbr, fg_abbr)
        team_name = abbr_to_name.get(official_abbr)
        if team_name:
            out[team_name] = data
    return out


_NAME_SUFFIX_RE = re.compile(r"\s+(jr|sr|ii|iii|iv|v)\.?$", re.I)


def _norm_name_key(n):
    """Accent-folded, suffix-stripped, lowercase form of a player name.

    Exists because the two sides of every season-stats lookup spell names
    differently and neither side is under our control:
      - the season frames (Statcast expected-stats fallback, which is the path
        that actually ships since FanGraphs 403s) carry the full diacritics
        and generational suffix: "Ronald Acuña Jr.", "Bobby Witt Jr.",
        "Carlos Narváez", "Michael Harris II";
      - tonight's lineups come from the MLB.com / Rotowire fallback scrapers,
        which strip both: "Ronald Acuna", "Bobby Witt", "Carlos Narvaez",
        "Michael Harris".
    A plain dict .get() on the raw name therefore misses exactly the players
    with accented or suffixed names — see name_lookup() for the measurement."""
    n = unicodedata.normalize("NFD", str(n))
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = n.replace(".", "").replace("'", "").replace("-", " ")
    n = _NAME_SUFFIX_RE.sub("", n.strip())
    return " ".join(n.lower().split())


def name_lookup(df, name_col_candidates=("Name", "last_name, first_name")):
    """Build a lookup from a FanGraphs/Statcast DataFrame, handling the
    "Last, First" format Statcast endpoints use vs FanGraphs "First Last".

    Keyed three ways, in the order lookup_player() tries them:
      1. MLBAM player_id (int) when the frame carries one — the only key that
         cannot be ambiguous or misspelled;
      2. the exact name string, as before;
      3. a normalized name (see _norm_name_key), registered only when it maps
         to a single row so a fold can never return the wrong player.

    BUG THIS FIXES, measured on tonight's real slate (2026-08-06, 11 games,
    189 lineup slots, Statcast-fallback season frame of 613 batters):
    16 of 189 lineup batters (8.5%) got NO season row at all from the exact-
    name .get(), even though every one of them was present in the frame —
    the names simply didn't match character-for-character:
        Ronald Acuna / Ronald Acuña Jr.        Bobby Witt / Bobby Witt Jr.
        Fernando Tatis / Fernando Tatis Jr.    Julio Rodriguez / Julio Rodríguez
        Michael Harris / Michael Harris II     Eugenio Suarez / Eugenio Suárez
        Andres Gimenez / Andrés Giménez        Jesus Sanchez / Jesús Sánchez
        Carlos Narvaez / Carlos Narváez        Mauricio Dubon / Mauricio Dubón
        Moises Ballesteros / Moisés Ballesteros  Endy Rodriguez / Endy Rodríguez
        Jose Tena / José Tena                  Nasim Nunez / Nasim Nuñez
        Heriberto Hernandez / Heriberto Hernández  Rafael Flores / Rafael Flores Jr.
    i.e. it silently deleted the season line of several of the best hitters in
    baseball. Every downstream consumer then ran on `bs = {}`:
      - project_batter_tb fell through to LEAGUE_AVG_TB_PA. Measured error in
        projected total bases (shipped vs. correct): Michael Harris II 1.48 vs
        1.88 (-21%), Heriberto Hernández 1.44 vs 1.75 (-18%), Bobby Witt Jr.
        1.55 vs 1.82 (-15%) — and in the other direction Nasim Nuñez 1.35 vs
        0.97 (+39%) and Carlos Narváez 1.26 vs 0.90 (+40%), i.e. weak hitters
        projected as league average and promoted onto the board.
      - BASELINE SKILL scored a flat neutral 50 for all 16 instead of their
        real values (Acuña 57.4, Nuñez 35.0, Giménez 36.4, Dubón 37.6).
      - the AVG-vs-xBA / wOBA-vs-xwOBA regression adjustment was skipped
        entirely (it gates on bs["pa"] >= 100, and pa was absent).
    Starters were unaffected on this slate (22/22 matched) because their names
    come from statsapi's probable-pitcher field rather than the scrapers, but
    the same fold protects them the first night an accented starter is listed.

    The player_id key also fixes a real ambiguity the name keys cannot: the
    frame holds TWO different players named exactly "Max Muncy" (613 rows
    collapse to 612 name keys), so whichever row came last silently won every
    "Max Muncy" lookup."""
    if df is None or df.empty: return {}
    name_col = next((c for c in name_col_candidates if c in df.columns), None)
    if not name_col: return {}
    id_col = next((c for c in ("player_id", "mlbam_id", "MLBAMID") if c in df.columns), None)
    out = {}
    norm_counts, norm_added = {}, set()
    for _, row in df.iterrows():
        n = row[name_col]
        if name_col == "last_name, first_name" and isinstance(n, str) and "," in n:
            last, first = [p.strip() for p in n.split(",", 1)]
            n = f"{first} {last}"
        rec = row.to_dict()
        out[n] = rec
        if id_col is not None:
            try: out[int(row[id_col])] = rec
            except (TypeError, ValueError): pass
        k = _norm_name_key(n)
        norm_counts[k] = norm_counts.get(k, 0) + 1
        if k not in out:
            out[k] = rec
            norm_added.add(k)
    # Ambiguous folds are withdrawn rather than resolved arbitrarily: returning
    # the wrong player's season line is strictly worse than returning none.
    # Measured on tonight's frame: exactly one fold is ambiguous ("max muncy",
    # two distinct real players), so this costs one name and protects the rest.
    for k, cnt in norm_counts.items():
        if cnt > 1 and k in norm_added:
            out.pop(k, None)
    return out


def lookup_player(lookup, name, player_id=None, default=None):
    """Resolve one player's season row: MLBAM id first, then exact name, then
    the accent/suffix-folded name. See name_lookup() for the measured miss
    rate the fold exists to close."""
    if not lookup:
        return default
    if player_id is not None:
        try:
            row = lookup.get(int(player_id))
        except (TypeError, ValueError):
            row = None
        if row is not None:
            return row
    if name is not None:
        row = lookup.get(name)
        if row is not None:
            return row
        row = lookup.get(_norm_name_key(name))
        if row is not None:
            return row
    return default


# ══════════════════════════════════════════════════════════════════════════
#  PROJECTIONS — every pick commits to a concrete number, not just a label.
#  This makes picks more useful AND makes them gradeable the next morning
#  without needing a real sportsbook line.
# ══════════════════════════════════════════════════════════════════════════

# ── Expected plate appearances: MEASURED, not assumed ────────────────────
# Derived from the SAME 186-game / 372-team-game live box-score pull described
# at LEAGUE_TEAM_RUNS_MEAN (m.statsapi.boxscore_data, 14 days ending
# 2026-08-05). For every team-game, every lineup slot's total PA (starter plus
# any substitute batting in that slot) was counted from the real box score and
# regressed on that team's real runs scored in that game.
#
# WHAT WAS ACTUALLY MEASURED (real numbers, n=372 team-games per slot):
#   slot | mean PA | observed range | old ORDER_PA/162
#     1  |  4.511  |     3-6        |     4.630
#     2  |  4.435  |     3-7        |     4.519
#     3  |  4.325  |     2-6        |     4.407
#     4  |  4.237  |     2-6        |     4.278
#     5  |  4.121  |     2-6        |     4.148
#     6  |  3.978  |     2-6        |     4.019
#     7  |  3.863  |     2-6        |     3.889
#     8  |  3.745  |     2-6        |     3.759
#     9  |  3.599  |     1-6        |     3.611
# Total lineup PA/game 36.81. Note the old static table was NOT badly wrong at
# league-average scoring — it sits within 0.12 PA of measured at every slot,
# and within 0.03 at slots 4-9. The static table's real defect is that it has
# no run-environment term at all, so it returns the same number for a leadoff
# hitter in a 3-run game and a 6-run game.
#
# Per-slot OLS of PA on runs gave slopes of 0.081-0.111 PA per run, tightly
# clustered with no slope trend across slots (mean 0.1006). The per-slot
# slopes are noisy at n=372 each, so the POOLED slope 0.1006 is used for all
# slots and only the intercept varies by slot, back-solved from each slot's
# measured mean at the measured league mean of 4.245 runs:
#     intercept_s = mean_PA_s - 0.1006 * 4.245
#
# HONEST SIZE OF THE EFFECT: the README's handoff estimated "leadoff on a
# 5.5-run team ~4.6 PA, 9-hole on a 3.5-run team ~3.7, a ~25% swing." The
# measurement only partly supports that. The slot term is real and large
# (4.51 down to 3.60, a 25% spread on its own). The run-environment term is
# much smaller than assumed: at 0.1006 PA per run, going from a 3.0-run team
# to a 5.5-run team moves a leadoff hitter only 4.38 -> 4.63 PA, about 6%.
# The joint range across slot AND environment is ~34% (3.48 to 4.65). So this
# model's gain over the static table is real but modest, and claiming 25% from
# run environment alone would have been wrong.
#
# CAVEAT (stated rather than hidden): runs and PA are mutually causal — more
# PA produce more runs as well as the reverse — so this is a descriptive
# conditional expectation E[PA | runs], not a causal coefficient. That is
# nonetheless exactly the right object here, because by the tower property
# E[PA] = a + b * E[runs], so evaluating the fit at the market's implied team
# total gives the correct expected PA as long as the relationship is close to
# linear over the range used, which the per-slot fits support.
PA_BY_SLOT_INTERCEPT = {1: 4.084, 2: 4.008, 3: 3.898, 4: 3.810, 5: 3.694,
                        6: 3.551, 7: 3.436, 8: 3.318, 9: 3.172}
PA_PER_RUN = 0.1006


def project_batter_pa(order, implied_total=None):
    """Expected plate appearances tonight for a given lineup slot, given the
    team's expected run environment. See the measurement block above.

    Degrades gracefully: with no implied total (Action Network unreachable,
    or a team the book hasn't priced) it evaluates at the measured league mean
    of 4.245 runs, which reproduces the measured per-slot means almost exactly
    — i.e. the no-data path is the old static table's behaviour, not a
    degraded one.

    The implied total is clamped to 2.0-7.5 runs before use so a stale or
    garbage line can't extrapolate the fit outside the range it was measured
    over. At those bounds a leadoff hitter spans 4.29 to 4.84 PA."""
    slot = min(max(int(order or 9), 1), 9)
    a = PA_BY_SLOT_INTERCEPT[slot]
    t = implied_total
    if t is None or t != t:
        t = LEAGUE_TEAM_RUNS_MEAN
    else:
        try: t = float(t)
        except (TypeError, ValueError): t = LEAGUE_TEAM_RUNS_MEAN
        if t != t: t = LEAGUE_TEAM_RUNS_MEAN
        t = clamp(t, 2.0, 7.5)
    return round(a + PA_PER_RUN * t, 2)


def project_batter_tb(bs, l7, order, implied_total=None):
    """Projected total bases for tonight, blending L7 form and season skill.
    TB/AB = AVG + ISO is a standard sabermetric identity; when ISO isn't
    available (Statcast-fallback season data has no ISO column), approximate
    from AVG with a league-average power multiplier instead of silently
    defaulting to a flat rate."""
    # TB rate per PLATE APPEARANCE. Two real errors were fixed here, both
    # invisible from reading the code and both found by checking real values:
    #
    # (1) SLG was being approximated when it was sitting right there. The
    #     Statcast-fallback season frame — the shape that actually ships, since
    #     FanGraphs 403s on most runs — has no ISO, so this fell to
    #     `avg * 1.35`. But that frame DOES carry `slg`, and slugging IS total
    #     bases per at-bat by definition, so no approximation is needed at all.
    #     Measured on tonight's real frame: Kyle Schwarber AVG .246 / SLG .532
    #     — the approximation returned .332, understating his true TB rate by
    #     38%. Bryce Harper .344 vs .502 (-31%), Yordan Alvarez .447 vs .649
    #     (-31%). Every batter TB projection on the common path was low by
    #     roughly a third, which also silently defeated the point of the new
    #     expected-PA model, since PA only scales whatever rate it multiplies.
    #
    # (2) SLG is per AT-BAT, but it was multiplied by projected PLATE
    #     APPEARANCES. PA includes walks and HBP, so this over-counted by the
    #     PA/AB ratio. Measured from 93 real completed games (7 days ending
    #     2026-08-05, statsapi.boxscore_data): 6227 AB / 6848 PA, so
    #     AB/PA = 0.9093, league SLG .3849, and league TB/PA .3500. The
    #     conversion is applied explicitly below.
    #
    # The L7 rate is NOT converted — fetch_l7_batter_form computes TB_per_PA
    # from real PA-ending Statcast rows, so it is already per plate appearance.
    season_rate = None
    if bs:
        avg = bs.get("AVG"); iso = bs.get("ISO"); slg = bs.get("slg")
        slg_per_ab = None
        if slg is not None and slg == slg:
            slg_per_ab = slg                      # exact: SLG == TB/AB
        elif avg is not None and iso is not None:
            slg_per_ab = avg + iso                # exact on the FanGraphs path
        elif avg is not None and avg == avg:
            slg_per_ab = avg * 1.35               # last-resort approximation
        if slg_per_ab is not None and slg_per_ab == slg_per_ab:
            season_rate = slg_per_ab * AB_PER_PA  # TB/AB -> TB/PA
    l7 = l7 or {}
    l7_pa = l7.get("PA", 0)
    l7_rate = l7.get("TB_per_PA") if l7_pa >= 5 else None
    if l7_rate is not None and season_rate is not None:
        w = min(l7_pa / 20, 0.5)
        rate = w * l7_rate + (1 - w) * season_rate
    elif season_rate is not None:
        rate = season_rate
    elif l7_rate is not None:
        rate = l7_rate
    else:
        rate = LEAGUE_AVG_TB_PA
    # Was: m.ORDER_PA[slot] / 162 — a static season-PA table with no game
    # context. Now a measured slot x run-environment model (project_batter_pa).
    pa_est = project_batter_pa(order, implied_total)
    return round(rate * pa_est, 2)


# ── Pitcher workload: MEASURED per-start batters faced ───────────────────
# Measured live from tonight's own 30 listed starters, L14 Statcast pulls,
# counting only real starts (in the game from inning 1):
#     n = 49 starts, mean 23.10 BF/start, median 23.0, SD 2.85, range 17-30
# The old flat constant of 22 was close to the league mean but hid the whole
# point: per-pitcher means tonight run 19.5 (Reid Detmers) to 27.5 (Tanner
# Bibee), a 41% spread that scales every strikeout projection linearly.
#
# HOW MUCH OF THAT SPREAD IS REAL, measured rather than assumed. Variance
# decomposition over the 27 starters with 2+ starts:
#     within-pitcher variance  6.068  (SD 2.46)
#     variance of pitcher means 5.351
#     between-pitcher variance  2.317  (SD 1.52)  [= 5.351 - 6.068/2]
# So MOST start-to-start variation in batters faced is noise, not pitcher
# identity. The empirical-Bayes shrinkage constant follows directly:
#     n0 = within / between = 6.068 / 2.317 = 2.62
# and the weight on a pitcher's own observed mean is n / (n + 2.62):
#     1 start 0.28 | 2 starts 0.43 | 3 starts 0.53 | 5 starts 0.66
# This is the same hard-won lesson as score_first_inning's sample penalty —
# an L14 window usually holds only 2-3 starts, and taken at face value a
# single long or short outing would swing a K projection by a full strikeout.
# Here the correction is derived from the measured variance rather than a
# chosen penalty, so it needs no separate cap.
LEAGUE_AVG_BF_PER_START = 23.1   # measured (was a flat 22, unsourced)
BF_SHRINK_N0 = 2.62              # measured empirical-Bayes constant, see above


def project_pitcher_workload(l14):
    """Expected batters faced tonight for this starter.

    Shrinks the pitcher's own measured BF/start toward the measured league
    mean by n/(n+2.62), where n is his real starts in the L14 window.
    Degrades gracefully: with no workload data at all (Statcast pull empty, a
    rookie's first start, a pitcher who only relieved in the window) it
    returns the measured league mean, which is the honest neutral answer.

    Returns (expected_bf, n_starts, observed_bf_per_start | None)."""
    l14 = l14 or {}
    obs = l14.get("bf_per_start")
    n = l14.get("n_starts") or 0
    if obs is None or n <= 0:
        return LEAGUE_AVG_BF_PER_START, 0, None
    try: obs = float(obs)
    except (TypeError, ValueError): return LEAGUE_AVG_BF_PER_START, 0, None
    if obs != obs: return LEAGUE_AVG_BF_PER_START, 0, None   # NaN guard
    w = n / (n + BF_SHRINK_N0)
    bf = w * obs + (1 - w) * LEAGUE_AVG_BF_PER_START
    # Never project outside the real observed range of a start (17-30 tonight,
    # widened slightly); a shrunk estimate can't reach these, but a future
    # data change shouldn't be able to produce an absurd workload silently.
    return clamp(bf, 12.0, 30.0), n, obs


def project_pitcher_ks(ps, l14, exp_k=None):
    """Projected strikeouts for tonight's start: K rate x expected batters
    faced, where the workload is now a real per-pitcher estimate rather than
    a flat league constant (see project_pitcher_workload).

    This gates every strikeout prop. A pitcher averaging 19.5 batters faced
    cannot reach the same K total as one averaging 27.5 at the same K rate,
    and the old flat 22 asserted that he could.

    K-RATE SOURCE, in priority order: exp_k (exponentially-decayed K rate
    from mlb_sources.exp_weighted_pitcher_k_rate, halflife 30 days over real
    starts this season) > the hard L14 Statcast window > season K%. exp_k is
    preferred because it is the only one of the three actually measured
    out-of-sample to beat the hard L14 window -- see the validation recorded
    on exp_weighted_pitcher_k_rate's docstring in mlb_sources.py: the current
    hard-14-day method scored WORSE (RMSE 0.0993) than a flat league-average
    K rate (RMSE 0.0807) on 147 real held-out starts, while halflife=30 beat
    every hard window tested and was statistically tied with the pitcher's
    own flat season rate (RMSE 0.0684 vs 0.0673) while staying responsive to
    a real recent change. exp_k is None whenever the sample is too thin
    (below 3 real starts or 40 batters faced) -- see that function's
    min_starts/min_raw_bf gates -- in which case this falls through to the
    previous behaviour unchanged."""
    l14 = l14 or {}
    if exp_k and exp_k.get("k_rate") is not None:
        k_pct = exp_k["k_rate"] * 100
    elif l14.get("l14_pa", 0) >= 15:
        k_pct = l14["l14_k_pct"]
    elif ps and ps.get("K%"):
        k_pct = ps["K%"]
    else:
        k_pct = 22.5
    exp_bf, _, _ = project_pitcher_workload(l14)
    return round(k_pct / 100 * exp_bf, 1)


# ══════════════════════════════════════════════════════════════════════════
#  SCORING
# ══════════════════════════════════════════════════════════════════════════

LEAGUE_AVG_EV = 88.5

LEAGUE_AVG_BULLPEN_ERA = 4.10

def score_batter(batter, gm, opp_sp_row, opp_sp_id, opp_sp_hand, park_wx, batter_season, batter_l7,
                  bat_speed_trend, batter_arsenal, pitcher_arsenal, opp_bullpen=None, sharp_bias=None,
                  opp_bullpen_quality=None, extras=None):
    name = batter["name"]
    # Which starter this batter actually faces, by name, for the BvP lookup.
    opp_sp_name = (gm.get("home_sp") if batter.get("team") == gm.get("away_team")
                   else gm.get("away_sp"))
    bid = batter.get("id")
    order = batter.get("order") or 9
    bats = batter.get("bats", "?")
    notable_signals = 0  # counts non-obvious converging signals, for the public-awareness discount

    # MATCHUP (35%) — platoon edge + opposing pitcher quality + pitch-type exploit
    #
    # RE-MEASURED 2026-08-15, PART OF THE RECOMMENDATION-LAYER REBUILD'S
    # SIGNAL ABLATION (direct instruction: "Do NOT automatically remove
    # platoon... run market-specific and interaction-aware ablation...
    # Remove it only if out-of-sample evidence shows it adds no value").
    # This crude binary flag was audited once before (AUC 0.500, small
    # sample, pooled only). Re-measured here on the full 242,776-row
    # multi-year backtest (backtest/rows.jsonl) via backtest/signals.py's
    # univariate_signal_report, PER PROP TYPE (not just pooled -- the
    # module's own full_report docstring warns a pooled AUC confounds with
    # base-rate differences across markets) and on a power-vs-contact
    # interaction split:
    #   pooled (n=128,180 fired):  AUC 0.490, 95% CI [0.486, 0.493] --
    #     statistically real, and BACKWARDS (below 0.5)
    #   every individual prop_type segment (hits/hits_runs_rbis/home_run/
    #     total_bases/doubles/triples/rbis/runs/singles, n=584-48,479 each):
    #     every single CI straddles 0.500 -- no measurable signal anywhere
    #   power markets (home_run/total_bases/doubles/triples, n=33,624):
    #     AUC 0.501, CI [0.493, 0.510] -- no signal
    #   contact markets (hits/singles/hits_runs_rbis, n=79,812):
    #     AUC 0.498, CI [0.493, 0.502] -- no signal
    # Conclusion: the pooled backwards-separation is the base-rate confound
    # the module warns about, not a real (if inverted) effect -- confirmed
    # by every real per-market segment showing nothing. This binary flag
    # earns no weight in ANY market or interaction tested. NOT YET REMOVED
    # from the live 0.55 coefficient below -- it is 19% of the total 0-100
    # score (35% matchup * 55%), and redistributing that weight needs its
    # own proper backtest/signals.py fit_weights()/compare_to_current_
    # weights() pass before touching a live-money formula, not a same-
    # session bolt-on. Flagged as the concrete next step.
    #
    # THE RELATED, GENUINELY NEW FINDING while re-running this: the
    # project's own 2026-08-12 audit comment below (still present,
    # unchanged, for the history) measured platoon_xwoba/platoon_barrel_pct
    # (exit velo/barrel rate BY HANDEDNESS -- the properly-continuous
    # version of this same platoon concept, computed at line ~1628 below
    # and already recorded in every candidate's signals dict for exactly
    # this kind of re-check, but never promoted into score) at only n=3,141
    # and called them DROP. Re-measured on the same 242,776-row set:
    #   platoon_xwoba    pooled AUC 0.518, CI [0.515, 0.521] -- separates,
    #     right direction, and holds up across nearly every individual
    #     batter market: hits 0.522, hits_runs_rbis 0.522, home_run 0.543,
    #     total_bases 0.530, doubles 0.525, runs 0.527, rbis 0.519 (only
    #     singles/triples, both under 9K fired, don't clear their own CI)
    #   platoon_barrel_pct   pooled AUC 0.506 (weak pooled) but home_run
    #     specifically: AUC 0.568, CI [0.549, 0.587] -- a real, meaningfully
    #     sized effect exactly where barrel rate should matter most
    # This is very likely the n=3,141 sample simply being too small to see
    # a real but modest effect, not a contradiction of the earlier result.
    # NOT promoted into score here either, same reasoning as above (needs a
    # real fit_weights pass, not a same-session guess at the right
    # coefficient) -- but this is a strong, concrete candidate to replace
    # this binary flag with, not just remove it outright.
    if bats in ("L", "R") and opp_sp_hand in ("L", "R"):
        platoon = 80 if bats != opp_sp_hand else 35
    else:
        platoon = 65
    sp_era = opp_sp_row.get("ERA") if opp_sp_row else None
    sp_weak = scale(sp_era, 2.5, 6.0)
    exploit = find_pitch_type_exploit(bid, opp_sp_id, batter_arsenal, pitcher_arsenal) if bid and opp_sp_id else None
    exploit_bonus = 0
    if exploit:
        exploit_bonus = scale(exploit["run_value_per_100"], 1.5, 5.0, 0, 20)
        notable_signals += 1
    matchup = clamp(platoon * 0.55 + sp_weak * 0.30 + exploit_bonus)

    # RECENT FORM (25%) — L7 contact quality + bat-speed trend (leading indicator)
    l7 = batter_l7 or {}
    l7_pa = l7.get("PA", 0)
    # Split into named sub-scores (same two scale() calls, same weights, same
    # result) so backtest/engine.py can record them individually — see _sig().
    sc_l7_ev = scale(l7.get("avg_EV"), 85, 93)
    sc_l7_barrel = scale(l7.get("barrel_pct"), 2, 16)
    form = sc_l7_ev * 0.6 + sc_l7_barrel * 0.4
    low_sample = l7_pa < 8
    bs_trend = bat_speed_trend.get(bid) if bid else None
    bat_speed_bonus = None
    if bs_trend is not None and bs_trend >= 1.0:
        bat_speed_bonus = scale(bs_trend, 1.0, 3.0, 0, 15)
        form = clamp(form + bat_speed_bonus)
        notable_signals += 1

    # ENVIRONMENT (15%) — park/weather HR index blended with the market's own
    # implied team total. The implied total is the strongest single
    # environment input available: it is a live forecast of how many runs
    # THIS team scores TONIGHT, and it already prices park, weather, the
    # opposing starter, the opposing bullpen and lineup strength at once,
    # where park_hr_index captures only the park-and-weather slice.
    #
    # Weighted 55/45 in the implied total's favour, inside the existing
    # ENVIRONMENT component rather than as an outside nudge, because it is
    # measuring exactly what this component is for (run environment) — unlike
    # the sharp-money divergence, which measures market *disagreement* and is
    # therefore kept outside and capped. Net effect on a pick is bounded by
    # ENVIRONMENT's own 15% weight.
    #
    # Band: scale() maps 3.0 -> 0 and 5.6 -> 100. Those are not round numbers
    # picked by feel — 3.0 and 5.6 are ~1 SD either side of the measured
    # league mean team total of 4.245 runs/game (measured SD of team runs
    # actually scored is 3.11, but the spread of *expected* totals is far
    # narrower; tonight's real implied totals after normalization run 3.07 to
    # 6.25 across the 27 teams priced, mean 4.314 vs the 4.245 measured league
    # mean — so this band covers the bulk of a live slate, with only genuine
    # extremes (a Coors game) clipping at the top). A team at the measured
    # league mean lands at 48, i.e. essentially neutral, by construction.
    park_env = park_wx.get("park_hr_index", 50) if park_wx else 50
    implied_total = (sharp_bias or {}).get("implied_total")
    if implied_total is not None:
        run_env = scale(implied_total, 3.0, 5.6)
        env = clamp(park_env * 0.45 + run_env * 0.55)
        if implied_total >= 5.2 or implied_total <= 3.5: notable_signals += 1
    else:
        env = park_env
    if park_env >= 70 or park_env <= 30: notable_signals += 1

    # BASELINE SKILL (15%)
    bs = batter_season or {}
    sc_wrc = scale(bs.get("wRC+"), 70, 140)
    sc_iso = scale(bs.get("ISO"), 0.10, 0.28)
    sc_barrel = scale(bs.get("Barrel%"), 3, 18)
    skill = sc_wrc * 0.4 + sc_iso * 0.3 + sc_barrel * 0.3
    star_profile = (bs.get("wRC+") or 100) >= 130  # season-obvious, already priced in by the market

    # CONTEXT (10%) — lineup slot + opposing bullpen fatigue. fetch_bullpen_scores()
    # was previously computed every run and never actually used anywhere in
    # scoring — a real gap found on review, since a tired opposing bullpen is a
    # genuine, non-obvious edge (more innings against shaky relief once the
    # starter comes out) that most casual bettors don't check before betting.
    lineup_context = scale(10 - order, 1, 9)
    bullpen = opp_bullpen or {}
    tracked = bullpen.get("tracked", 0)
    fatigued = bullpen.get("fatigued_relievers", 0)
    bullpen_fatigue_pct = (fatigued / tracked * 100) if tracked >= 3 else None
    sc_bullpen_fatigue = scale(bullpen_fatigue_pct, 0, 60) if bullpen_fatigue_pct is not None else None
    if bullpen_fatigue_pct is not None:
        context = clamp(lineup_context * 0.7 + sc_bullpen_fatigue * 0.3)
        if bullpen_fatigue_pct >= 40:
            notable_signals += 1
    else:
        context = lineup_context

    # Bullpen *quality* is a separate axis from *fatigue* above — a tired
    # elite bullpen and a tired bad bullpen are not the same matchup. Blended
    # in lightly (not part of the weighted formula's core, same treatment as
    # sharp money below) since it's aggregated from a fallback-prone source
    # (FanGraphs individual pitching -> Statcast, no team split) and should
    # nudge, not drive, the score.
    bp_era = (opp_bullpen_quality or {}).get("era")
    bullpen_era_diff = (bp_era - LEAGUE_AVG_BULLPEN_ERA) if bp_era is not None else None
    if bullpen_era_diff is not None and abs(bullpen_era_diff) >= 0.5:
        context = clamp(context + clamp(bullpen_era_diff * 8, -8, 8))
        if bullpen_era_diff >= 0.5: notable_signals += 1

    # PROMOTED 2026-08-14, replacing the original hand-set 35/25/15/15/10
    # split. backtest/fit_score_weights.py tested the hand-set weights
    # against a logistic-regression fit on the real multi-year backtest
    # (2024-2025, ~41K usable batter rows) and found a REAL, robust
    # improvement -- not a one-off: re-run across 5 independent train/
    # held-out split boundaries (test_frac 0.2 through 0.4), the fitted
    # weights cleared the hand-set formula's confidence interval in 4 of 5
    # (the 5th, on the smallest held-out slice, was underpowered rather
    # than contradictory -- same direction, same rough magnitude, just a
    # wider CI). CONTEXT was traced to its actual source (batting order
    # slot, see score_batter's own CONTEXT section above) and confirmed a
    # real, monotonic signal on 41K rows: raw hit rate climbs from 55.8% to
    # 71.2% across its range, no reversals. BASELINE SKILL came back
    # negative and small -- season-level power/contact stats are largely
    # already priced in by the market itself, the same "no additional
    # signal" pattern star_profile's own discount below already assumes.
    # See fit_score_weights.py's own CURRENT_WEIGHTS_BATTER, kept in sync
    # with this line so future reruns compare against what's actually
    # shipping, not a stale reference.
    score = clamp(matchup * 0.04 + form * 0.03 + env * 0.20 + skill * -0.09 + context * 0.64)

    # Sharp-money nudge: a small, capped adjustment from a genuinely different
    # signal type (market-derived, not stats-derived) — deliberately not part
    # of the weighted formula above so it can't dominate a stats-driven pick,
    # per explicit direction that edge shouldn't be ignored but isn't the
    # primary filter. Only acts on a real divergence (10+ points), not noise.
    sharp_divergence = (sharp_bias or {}).get("sharp_divergence")
    if sharp_divergence is not None and abs(sharp_divergence) >= 10:
        score = clamp(score + clamp(sharp_divergence / 3, -5, 5))
        if sharp_divergence >= 10: notable_signals += 1

    watchouts = []
    if low_sample:
        watchouts.append(f"L7 sample is thin ({l7_pa} PA) — treat recent-form read with caution")
    if park_wx and park_wx.get("wx_disagreement"):
        watchouts.append(park_wx["wx_disagreement"])
    if sharp_divergence is not None and sharp_divergence <= -10:
        watchouts.append(f"Public heavy on {batter.get('team')} (money% trails tickets% by {abs(sharp_divergence)} pts) — sharp money is fading this side")
    if l7.get("AVG", 0) and l7.get("AVG", 0) > 0.320 and l7.get("barrel_pct", 0) < 6 and l7_pa >= 8:
        score -= 12
        watchouts.append(f"L7 AVG {l7.get('AVG')} isn't backed by barrel rate ({l7.get('barrel_pct')}%) — likely BABIP-driven, due to cool off")

    # Fresh off the injured list. Informational only, deliberately not a
    # score adjustment -- see mlb_sources.fetch_recent_il_returns' own
    # docstring for why: no measured effect size exists for how long a
    # return-from-IL dip actually lasts in this league.
    # NOT using `ex` here -- this runs before `ex = extras or {}` is
    # assigned further down in this function, so it reads the raw `extras`
    # parameter directly instead (verified live: using `ex` here raised
    # UnboundLocalError, caught before this ever shipped).
    il = ((extras or {}).get("il_returns") or {}).get(bid) if bid else None
    if il:
        watchouts.append(f"Activated from the {il['il_days']}-day injured list {il['days_ago']} "
                         f"day(s) ago — early performance back can be inconsistent")

    # Recently called up from the minors -- same "fresh, uncertain track
    # record" theme as the IL check above, a different cause. See
    # mlb_sources.fetch_recent_callups' own docstring.
    cu = ((extras or {}).get("callups") or {}).get(bid) if bid else None
    if cu:
        watchouts.append(f"Recalled from the minors {cu['days_ago']} day(s) ago — thin or no MLB "
                         f"track record behind his season/rolling stats")

    # REGRESSION SIGNAL — expected-vs-actual gap, as a bounded two-sided
    # adjustment OUTSIDE the weighted formula.
    #
    # Placement is deliberate. This is not another estimate of how good the
    # hitter is — the formula's BASELINE SKILL component already uses his
    # actual results. It is a statement about how much those results are
    # likely to MOVE, which is a different kind of claim, so it belongs
    # outside the components and capped, exactly like the sharp-money nudge
    # (+/-5) rather than folded into one of the five weights.
    #
    # Inputs are already present, no extra fetch: the season frame carries
    # est_ba_minus_ba_diff and est_woba_minus_woba_diff. Sign verified
    # numerically against real rows tonight rather than trusted from the
    # column name (the name reads "est minus ba", but the values are
    # ACTUAL minus EXPECTED: Abimelec Ortiz AVG .400 / xBA .393 -> +.007, and
    # a second row wOBA .xxx - xwOBA .xxx reproduced its diff exactly). So
    # POSITIVE = outperforming his batted-ball quality = FADE, NEGATIVE =
    # underperforming with better contact than results = BUY.
    #
    # Thresholds are the MEASURED distribution, not chosen: across the 409
    # batters with 100+ PA in tonight's real pull, est_ba_minus_ba_diff has
    # mean .0000 and SD .0228 (p10 -.027, p90 +.028), and
    # est_woba_minus_woba_diff mean .0007, SD .0247 (p10 -.030, p90 +.032).
    # A gap only counts as a signal past ~1 SD, and the adjustment is scaled
    # so that a 2-SD gap (roughly the extremes of the real distribution, min
    # -.107 / max +.061) reaches the +/-6 cap. 6 is chosen to sit just above
    # the sharp-money nudge's 5 — a stats-derived regression read should
    # outweigh a market nudge — while still being unable to overturn a
    # genuine multi-signal edge on its own.
    reg_adj = 0.0
    reg_notes = []
    ba_gap = bs.get("est_ba_minus_ba_diff")
    woba_gap = bs.get("est_woba_minus_woba_diff")
    season_pa = bs.get("pa") or 0
    if season_pa >= 100:   # below this the gap is mostly sampling noise
        for gap, sd, label, actual, expected in (
                (ba_gap, 0.0228, "AVG vs xBA", bs.get("AVG"), bs.get("xBA")),
                (woba_gap, 0.0247, "wOBA vs xwOBA", bs.get("wOBA"), bs.get("xwOBA"))):
            if gap is None or gap != gap: continue
            try: gap = float(gap)
            except (TypeError, ValueError): continue
            if abs(gap) < sd: continue          # inside one SD — no signal
            # -6 for a 2-SD overperformer, +6 for a 2-SD underperformer.
            reg_adj += clamp(-gap / (2 * sd) * 6, -6, 6) / 2   # /2: two metrics, shared budget
            if gap > 0:
                reg_notes.append(f"{label}: {actual:.3f} vs {expected:.3f} (+{gap:.3f}) — "
                                  f"outperforming his contact quality, regression risk")
            else:
                reg_notes.append(f"{label}: {actual:.3f} vs {expected:.3f} ({gap:.3f}) — "
                                  f"underperforming his contact quality, positive regression candidate")
    reg_why_notes = []
    if reg_adj:
        reg_adj = clamp(reg_adj, -6, 6)
        score = clamp(score + reg_adj)
        if reg_adj > 0:
            reg_why_notes = reg_notes           # merged into `why` once it exists, below
            if reg_adj >= 3: notable_signals += 1   # a genuine non-obvious BUY the market underrates
        else:
            watchouts.extend(reg_notes)

    # Public-awareness discount: a pick leaning entirely on "star + high average,"
    # with no other converging signal, is exactly what the market already prices —
    # not useful. Downweight it. A pick with 2+ non-obvious signals gets a small
    # boost instead, even on a more middling player.
    if star_profile and notable_signals == 0:
        score -= 10
        watchouts.append("Built mainly on season-long star power with no additional converging signal — likely already priced by the market")
    elif notable_signals >= 2:
        score = clamp(score + 5)

    projected_tb = project_batter_tb(bs, l7, order, implied_total)
    projected_pa = project_batter_pa(order, implied_total)
    if exploit and exploit["hard_hit_percent"] and (exploit["hard_hit_percent"] >= 45 or projected_tb >= 1.8):
        prop = f"Home Run / 2+ Total Bases (proj. {projected_tb} TB)"
    elif (bs.get("K%") or 30) <= 18:
        prop = f"Over 1.5 Hits (proj. {projected_tb} TB)"
    else:
        prop = f"Over 1.5 Total Bases (proj. {projected_tb} TB)"

    why = []
    why.extend(reg_why_notes)
    # 2026-08-24 explanation-quality fix, part 2 (real live complaint, Weston
    # Wilson: batting 8th on a 3.08-run-implied team shown as a green-positive
    # reason). This used to be one combined sentence, unconditionally in
    # `why`, regardless of whether the batting slot or the implied total were
    # actually favorable. lineup_context (CONTEXT's own scale(10-order,1,9)
    # -- the single highest-weighted score component in this whole formula,
    # 64% of score) and run_env (already computed above, inside the
    # implied_total branch) are both genuinely directional -- reused here
    # instead of restating bottom-of-the-order or a weak implied total as if
    # they were self-evidently good news. Split into two independently-
    # routed facts, since order and implied total can disagree (a leadoff
    # hitter on a low-scoring team, or a #8 hitter in a laugher).
    pa_note = f"Projected {projected_pa} PA (batting slot {order})"
    if lineup_context >= 65:
        why.append(pa_note + " — a favorable lineup slot")
    elif lineup_context <= 35:
        watchouts.append(pa_note + " — a tough lineup slot for plate appearances")
    else:
        why.append(pa_note)
    if implied_total is not None:
        total_note = (f"Team implied for {implied_total} runs (league avg {LEAGUE_TEAM_RUNS_MEAN}; "
                       f"line {(sharp_bias or {}).get('implied_total_line')}, "
                       f"game total {(sharp_bias or {}).get('game_total')})")
        if run_env >= 65:
            why.append(total_note + " — a strong offensive environment")
        elif run_env <= 35:
            watchouts.append(total_note + " — a weak offensive environment")
        else:
            why.append(total_note)
    else:
        why.append("No market implied team total available — run environment assumed league-average")
    # 2026-08-24 explanation-quality fix, part 4: same live complaint,
    # Weston Wilson's card literally said "Platoon: R bat vs RHP
    # (unfavorable)" and still put the whole line under `why`, the
    # positive-reasons list -- the word "unfavorable" was honest, but the
    # placement wasn't. platoon is always exactly one of {80 favorable, 65
    # unknown/neutral, 35 unfavorable} (see its own three-way assignment
    # above), so this routes on the same real value already driving score,
    # not a second guess at what the sentence "sounds like".
    platoon_note = f"Platoon: {bats} bat vs {opp_sp_hand or '?'}HP"
    if platoon >= 80:
        why.append(platoon_note + " (favorable)")
    elif platoon <= 35:
        watchouts.append(platoon_note + " (unfavorable)")
    else:
        why.append(platoon_note + " (handedness unknown)")
    if exploit:
        why.append(f"Pitch-type exploit: RV/100 {exploit['run_value_per_100']:+.1f} vs {exploit['pitch_type']} "
                    f"(opposing SP throws it {exploit['usage_pct']}% of the time)")
    # 2026-08-24 explanation-quality fix: this used to append the opposing
    # SP's ERA to `why` (the positive-reasons list) unconditionally,
    # regardless of whether that ERA was actually favorable to the batter.
    # sp_weak = scale(sp_era, 2.5, 6.0) already exists and is genuinely
    # directional (low ERA/elite pitcher -> low score -> bad for the
    # batter; high ERA/shaky pitcher -> high score -> good for the batter)
    # -- reuse it instead of restating the raw number as if a bare ERA
    # digit were self-evidently good news. A pitcher in the neutral middle
    # isn't a real reason either way, so this stays silent there rather
    # than padding the list.
    if sp_era is not None:
        if sp_weak >= 65:
            why.append(f"Opposing SP ERA {sp_era:.2f} — shaky matchup for the pitcher")
        elif sp_weak <= 35:
            watchouts.append(f"Opposing SP ERA {sp_era:.2f} — elite pitcher, tough matchup")
    elif not opp_sp_row:
        # 2026-08-26 market-specific-explanation fix: when the opposing
        # starter genuinely isn't confirmed yet (opp_sp_row is empty, not
        # just missing an ERA field), this used to stay completely silent on
        # the starter matchup -- no ERA-based fact, and no explanation for
        # why not. Direct instruction: "If the starter is TBD, explicitly say
        # starter-specific [matchup] analysis is unavailable rather than
        # padding the case." sp_weak still defaults to a neutral 50 for
        # SCORING (scale()'s own documented behavior for a missing input),
        # which is correct there -- this is purely about the explanation
        # text saying so honestly instead of padding with nothing.
        watchouts.append("Opposing starter not yet confirmed — starter-specific matchup analysis unavailable")
    # 2026-08-24 explanation-quality fix, part 3 (same live complaint, Weston
    # Wilson: "L7 avg EV 82.8mph (league ~88.5)" -- 5.7mph BELOW league,
    # shown as a plain fact in `why` with no directional judgment attached).
    # sc_l7_ev/sc_l7_barrel (RECENT FORM's own scale() calls, already
    # computed above) are exactly as directional as sp_weak was -- reused
    # the same way, same neutral-middle-stays-silent rule.
    if l7.get("avg_EV"):
        ev_note = f"L7 avg EV {l7['avg_EV']:.1f}mph (league ~{LEAGUE_AVG_EV})"
        if sc_l7_ev >= 65:
            why.append(ev_note + " — hot recent contact")
        elif sc_l7_ev <= 35:
            watchouts.append(ev_note + " — cold recent contact")
        else:
            why.append(ev_note)
    if l7.get("barrel_pct") is not None:
        barrel_note = f"L7 barrel% {l7['barrel_pct']}"
        if sc_l7_barrel >= 65:
            why.append(barrel_note + " — hot recent contact")
        elif sc_l7_barrel <= 35:
            watchouts.append(barrel_note + " — cold recent contact")
        else:
            why.append(barrel_note)
    if bs_trend is not None and bs_trend >= 1.0: why.append(f"Bat speed trending up L14 ({bs_trend:+.1f}mph 2nd-half vs 1st-half)")
    # 2026-08-25 explanation-quality fix (release-readiness audit, same class
    # as the L14-K%/sp_era/ev_note/barrel_note/wind fixes above): season
    # wRC+ used to land in `why` unconditionally. sc_wrc = scale(wRC+, 70,
    # 140) already exists (it's SKILL's own component) and is exactly as
    # directional -- reused the same way. Not observed live in today's
    # slate (no genuinely below-average wRC+ batter happened to qualify),
    # but the same unconditional-append shape as the fixed bugs, so closed
    # proactively rather than left for the next slate to surface it.
    if bs.get("wRC+"):
        wrc_note = f"Season wRC+ {bs['wRC+']:.0f}"
        if sc_wrc >= 65:
            why.append(wrc_note + " — above-average hitter")
        elif sc_wrc <= 35:
            watchouts.append(wrc_note + " — below-average hitter this season")
        else:
            why.append(wrc_note)
    # 2026-08-26 market-specific-explanation fix: ISO and season Barrel% were
    # both already computed above (sc_iso/sc_barrel, SKILL's own components)
    # and never once rendered as text -- the exact "computed, then discarded"
    # failure this codebase has already found and fixed for several other
    # fields. Real, direct complaint: a home-run detail view showed
    # probability vs. league base rate and almost nothing about the batter's
    # actual power profile. ISO and Barrel% are the two power-specific season
    # stats a home-run/total-bases read should lead with -- wRC+ above is a
    # general hitting-quality stat, not a power-specific one. Same neutral-
    # middle-stays-plain convention as every other directional note here.
    if bs.get("ISO") is not None:
        iso_note = f"Season ISO {bs['ISO']:.3f}"
        if sc_iso >= 65:
            why.append(iso_note + " — real power, above-average isolated power")
        elif sc_iso <= 35:
            watchouts.append(iso_note + " — below-average isolated power")
        else:
            why.append(iso_note)
    if bs.get("Barrel%") is not None:
        barrel_season_note = f"Season barrel% {bs['Barrel%']}"
        if sc_barrel >= 65:
            why.append(barrel_season_note + " — well above-average barrel rate")
        elif sc_barrel <= 35:
            watchouts.append(barrel_season_note + " — below-average barrel rate")
        else:
            why.append(barrel_season_note)
    # 2026-08-26 market-specific-explanation fix: lineup protection
    # (woba_ahead/woba_behind) was wired into `signals` for backtest fitting
    # (see the "Lineup context" block further down, ex = extras or {}) but
    # never surfaced as a human fact -- same discarded-computation failure.
    # Directly relevant to RBI/Runs/Hits+Runs+RBIs: who's on base ahead of
    # this batter (his own RBI opportunity) and how well the lineup protects
    # him (whether pitchers will pitch around him). Real per-batter wOBA, not
    # a made-up composite. Uses `extras` directly, not `ex` -- `ex = extras
    # or {}` isn't assigned until further down in this function (see the
    # IL-returns check above for the identical reason).
    lwc_note = ((extras or {}).get("lineup_woba") or {}).get(bid) if bid else None
    if lwc_note:
        woba_ahead = lwc_note.get("woba_ahead")
        if woba_ahead is not None:
            sc_ahead = scale(woba_ahead, 0.290, 0.400)
            ahead_note = f"Hitters batting ahead of him: {woba_ahead:.3f} wOBA (league ~.320)"
            if sc_ahead >= 65:
                why.append(ahead_note + " — real RBI opportunity on base ahead of him")
            elif sc_ahead <= 35:
                watchouts.append(ahead_note + " — a weak on-base group ahead of him, fewer runners to drive in")
            else:
                why.append(ahead_note)
        woba_behind = lwc_note.get("woba_behind")
        if woba_behind is not None:
            sc_behind = scale(woba_behind, 0.290, 0.400)
            behind_note = f"Hitter batting behind him: {woba_behind:.3f} wOBA (league ~.320)"
            if sc_behind >= 65:
                why.append(behind_note + " — real lineup protection, pitchers can't just pitch around him")
            elif sc_behind <= 35:
                watchouts.append(behind_note + " — little lineup protection, an easier batter to pitch around")
            else:
                why.append(behind_note)
    # 2026-08-25 explanation-quality fix (release-readiness audit): the wind-
    # in branch used to land in `why`, the positive-reasons list, even
    # though its own text says "power suppressed" -- a real, self-
    # contradictory placement (the sentence itself is negative) confirmed
    # live in 163 currently-published props across Hits/Total Bases/
    # Doubles/Hits+Runs+RBIs. Wind blowing in suppresses offense broadly
    # (fewer balls carry out or find the gap), so it belongs in watchouts,
    # same as the wind-OUT branch correctly stays in why (its own text is
    # genuinely positive -- "HR boost").
    if not park_wx or park_wx.get("dome"): why.append("Dome — weather neutral")
    elif park_wx.get("wind_effect") == "out": why.append(f"Wind blowing OUT ({park_wx.get('wind_mph',0):.0f}mph) — HR boost")
    elif park_wx.get("wind_effect") == "in": watchouts.append(f"Wind blowing IN ({park_wx.get('wind_mph',0):.0f}mph) — power suppressed")
    # 2026-08-26 UNKNOWN-ROOF INTEGRITY FIX (PR #67 release-candidate review):
    # a retractable-roof park whose real per-game status couldn't be
    # confirmed at fetch time (MLB hadn't posted its weather field yet, which
    # real data shows is common 8-12 hours before first pitch -- squarely
    # inside this pipeline's own generation window) used to still get a real
    # outdoor-weather-based read above, as though open were known, on the
    # unproven assumption that "not yet reported closed" meant "open."
    # Measured: absence of a "closed" reading is not affirmative evidence of
    # an open roof -- a real 8-day/7-park sample found 4 of the 7 parks
    # closed in 100% of observed games. fetch_park_weather() no longer awards
    # that directional credit for "unknown" (same neutral park_hr_index=50
    # treatment as a confirmed-closed roof, but never mislabeled as
    # "closed" -- roof_status stays "unknown"); this watchout is the
    # customer-facing side of that fix. Never fires for a confirmed-open,
    # confirmed-closed, or non-retractable park (roof_status is None or
    # "open"/"closed" there).
    if park_wx and park_wx.get("roof_status") == "unknown":
        watchouts.append("Retractable-roof park — real roof status wasn't confirmed yet when this was "
                          "generated, so no directional weather effect is being applied until it's known")
    # 2026-08-2X explanation-quality fix (data-integrity/directionality
    # audit, real complaint: Jacob saw a "fresh pen" bullpen note under
    # "Why It Could Hit"). Same class of bug as the wind-in fix just above
    # -- all three of bullpen fatigue, bullpen quality, and sharp money
    # used to land in `why` unconditionally regardless of which way the
    # real value actually cut. A fresh/rested bullpen and an elite bullpen
    # are both genuinely BAD news for a batter (harder relievers to face
    # late); sharp money FADING this side is real negative context, not a
    # reason to like the pick. Routed on the same real, already-computed
    # values already driving these facts, not a second guess.
    if bullpen_fatigue_pct is not None:
        note = (f"Opposing bullpen fatigue: {fatigued}/{tracked} relievers over 60 pitches in L7")
        if bullpen_fatigue_pct >= 40:
            why.append(note + " (tired pen — favorable late)")
        else:
            watchouts.append(note + " (fresh pen — tougher matchup late)")
    if bullpen_era_diff is not None and abs(bullpen_era_diff) >= 0.5:
        note = f"Opposing bullpen ERA {bp_era} (league ~{LEAGUE_AVG_BULLPEN_ERA})"
        if bullpen_era_diff > 0:
            why.append(note + " — shaky pen")
        else:
            watchouts.append(note + " — elite pen")
    if sharp_divergence is not None and abs(sharp_divergence) >= 10:
        if sharp_divergence > 0:
            why.append(f"Sharp money backing {batter.get('team')} "
                        f"(money% +{sharp_divergence} pts vs ticket%)")
        else:
            watchouts.append(f"Sharp money fading {batter.get('team')} "
                              f"(money% {sharp_divergence} pts vs ticket%) — smart money moving away from this side")

    signals = {}
    _sig(signals, "platoon", bats if bats in ("L", "R") else None, platoon)
    _sig(signals, "sp_era_weak", sp_era, sp_weak)
    if exploit: _sig(signals, "pitch_exploit", exploit.get("run_value_per_100"), exploit_bonus)
    _sig(signals, "l7_avg_ev", l7.get("avg_EV"), sc_l7_ev)
    _sig(signals, "l7_barrel_pct", l7.get("barrel_pct"), sc_l7_barrel)
    _sig(signals, "bat_speed_trend", bat_speed_bonus, bat_speed_bonus)
    _sig(signals, "park_hr_index", (park_wx or {}).get("park_hr_index"), park_env)
    _sig(signals, "wrc_plus", bs.get("wRC+"), sc_wrc)
    _sig(signals, "iso", bs.get("ISO"), sc_iso)
    _sig(signals, "season_barrel_pct", bs.get("Barrel%"), sc_barrel)
    _sig(signals, "lineup_slot", order, lineup_context)
    _sig(signals, "bullpen_fatigue", bullpen_fatigue_pct, sc_bullpen_fatigue)
    if bullpen_era_diff is not None and abs(bullpen_era_diff) >= 0.5:
        _sig(signals, "bullpen_era_diff", bullpen_era_diff, clamp(bullpen_era_diff * 8, -8, 8))

    # ── Signals that were built and never consulted ───────────────────────
    #
    # Each is recorded via _sig so backtest/signals.py can measure whether it
    # separates hits from misses. Adding an input is not the same as trusting
    # one: every adjustment here is deliberately SMALL, because the honest
    # position is that these are untested, and a large weight on an untested
    # signal is how a model gets confidently worse.
    # RECORDED, NOT YET WEIGHTED, and that is deliberate. _sig writes a signal
    # onto the candidate for backtest/signals.py to measure; it does not touch
    # the score. So wiring these six changes what the system KNOWS about each
    # pick without yet changing which picks it makes.
    #
    # That ordering is the whole discipline. The last time signals were given
    # weights by judgement, seven of twelve turned out not to separate hits
    # from misses at all, and two scored below random -- weights invented for
    # inputs nobody had measured. These get measured against real outcomes
    # first, and only the ones that earn a weight get one.
    ex = extras or {}

    # Batter versus THIS pitcher. The most direct matchup evidence available,
    # and previously report-only. Capped hard because BvP samples are tiny --
    # a 3-for-7 career line is not information, and treating it as such is one
    # of the oldest errors in baseball analysis.
    # bvp_table returns a LIST of rows with Batter/Pitcher NAMES and AB/H/OPS
    # -- not a dict keyed by ids, and there is no PA column. Indexed by name
    # pair at the caller. Requires 10+ AB because a 3-for-7 career line is the
    # most over-read number in baseball.
    bvp = (ex.get("bvp_by_pair") or {}).get((name, opp_sp_name)) if opp_sp_name else None
    if bvp and (bvp.get("AB") or 0) >= 10:
        try:
            ops = float(bvp.get("OPS"))
        except (TypeError, ValueError):
            ops = None
        if ops is not None:
            _sig(signals, "bvp_ops", ops, clamp((ops - 0.720) * 6, -4, 4))
            # Made technical on request, not just "small sample": a batting
            # rate off N at-bats carries a real standard error --
            # sqrt(p(1-p)/N) -- and reporting the number itself is a more
            # defensible argument than an unquantified adjective, especially
            # for a reader who already knows this stat cold. A raw AB count
            # in the teens SOUNDS like a real sample; the SE band is what
            # shows why it still isn't one.
            bvp_ab = bvp.get("AB") or 0
            bvp_h = bvp.get("H") or 0
            bvp_p = bvp_h / bvp_ab if bvp_ab else 0
            bvp_se = (bvp_p * (1 - bvp_p) / bvp_ab) ** 0.5 if bvp_ab else 0
            watchouts.append(f"BvP: {bvp_h}-for-{bvp_ab} vs {opp_sp_name} "
                             f"(standard error ±{bvp_se*100:.0f} pts on a {bvp_ab}-AB career "
                             f"sample -- weighted lightly on that basis, not just because "
                             f"the count looks small)")

    # AUDIT, 2026-08-12: platoon_barrel_pct/platoon_xwoba/park_hand_index/
    # days_rest/consecutive_games/pull_park_synergy (below) plus ump_k_pct/
    # ump_bb_pct (elsewhere in this file) became measurable in backtest for
    # the FIRST TIME this session (see backtest/engine.py's extras dict --
    # they were computed and recorded live every night but structurally
    # invisible to backtest/signals.py before this session's fix). Measured
    # on a fresh 33-date backtest (2026-07-10..08-11, 15,440 graded rows) via
    # backtest/signals.py's univariate + fitted-weight report, same
    # discipline as every other signal here: record, measure, THEN promote --
    # never the reverse.
    #
    # THE HONEST RESULT: none of the eight clear the bar to promote.
    #   pull_park_synergy    AUC 0.522 CI [0.499,0.545] (n=2544) -- DROP,
    #                        redundant with park_hand_index (r=0.877) which
    #                        has the stronger univariate read anyway
    #   park_hand_index      AUC 0.528 CI [0.507,0.550] (n=2858) -- REVIEW,
    #                        real alone but not significant once every other
    #                        signal is in the fit (p=0.141) -- mixed, not a
    #                        promote
    #   platoon_barrel_pct   AUC 0.498 (n=3141) -- DROP, redundant with
    #                        season_barrel_pct (r=0.880)
    #   platoon_xwoba        AUC 0.512 (n=3141) -- DROP, no separation
    #   days_rest            AUC 0.487 (n=3415) -- DROP, no separation
    #   consecutive_games    AUC 0.430 (n=261, only 7.6% of rows fire --
    #                        >=10 consecutive games is rare) -- DROP
    #   ump_k_pct            AUC 0.495-0.493 across segments -- DROP
    #   ump_bb_pct           AUC 0.505-0.518 across segments -- DROP
    #
    # None of this is a wasted build: the infrastructure fix was real
    # (bvp/sp_rp/ump_env remain the genuinely permanent gaps, see
    # backtest/engine.py) and now these keep getting measured on every
    # future backtest run without further work. This is what "record,
    # measure" is supposed to look like when the honest answer is "not yet"
    # -- left unweighted below, exactly as before this audit. Do not
    # promote any of these without a fresh measurement showing otherwise.

    # Platoon measured properly: exit velocity and barrel rate BY handedness,
    # rather than the binary hand-versus-hand flag that measured AUC 0.500.
    pq = (ex.get("platoon_qoc") or {}).get(bid) if bid else None
    if pq and opp_sp_hand in ("L", "R"):
        # Keyed by the PITCHER's hand, matching how the table is built.
        side = pq.get(opp_sp_hand) or {}
        if (side.get("PA") or 0) >= 20:
            if side.get("Barrel%") is not None:
                _sig(signals, "platoon_barrel_pct", side["Barrel%"],
                     clamp((side["Barrel%"] - 7.0) * 0.8, -5, 5))
            if side.get("xwOBA") is not None:
                _sig(signals, "platoon_xwoba", side["xwOBA"],
                     clamp((side["xwOBA"] - 0.313) * 60, -5, 5))

    # Park factors split by batter hand. The existing park signal is
    # hand-blind, and the split is large -- Yankee Stadium plays 138 for
    # left-handed power and 108 for right-handed.
    ph = (ex.get("park_hand") or {}).get(gm.get("venue"))
    if ph and bats in ("L", "R"):
        side = ph.get(bats) or {}
        idx = side.get("Index")
        if idx is not None and (side.get("BBE") or 0) >= 300:
            _sig(signals, "park_hand_index", idx, clamp((idx - 100) * 0.10, -5, 5))

    # Catcher framing moves strikeouts and walks, so it moves every prop that
    # depends on a plate appearance ending a particular way.
    # Keyed by opposing TEAM rather than catcher id: score_batter does not
    # receive the catcher, and the caller resolves team -> tonight's catcher
    # -> framing runs before passing this in.
    fr = (ex.get("framing_by_team") or {}).get(
        gm["home_team"] if batter.get("team") == gm["away_team"] else gm["away_team"])
    if fr is not None:
        # A better framer steals more strikes, which is bad for the hitter.
        # League Steal% runs around 4-5%.
        _sig(signals, "opp_catcher_framing", fr, clamp(-(fr - 4.5) * 1.2, -4, 4))
        # AUDIT, 2026-08-12: MEASURED, same fresh 33-date backtest as the
        # audit near the platoon block above (catcher_framing() was wired
        # into backtest extras in the same pass, one signal at a time --
        # see backtest/engine.py's own comment). No real separation power:
        # AUC 0.501 (n=7200, hits), 0.518 (n=3409, hits_runs_rbis), 0.486
        # (n=3787, a third batter segment) -- every confidence interval
        # straddles 0.50, and backtest/signals.py's prune recommendation
        # says DROP in all three. Same honest "not yet" verdict as the
        # other eight signals audited above -- left unweighted, exactly as
        # it already was.

    # Rest and accumulated usage.
    # rest_and_usage nests under 'batters'/'starters', and the field is
    # days_since_last_game -- not a flat id map with days_rest. Looking up an
    # integer id in {'batters':..., 'starters':...} returned None for every
    # batter, so this signal never once fired.
    rs = ((ex.get("rest") or {}).get("batters") or {}).get(bid) if bid else None
    if rs:
        if rs.get("days_since_last_game") is not None:
            _sig(signals, "days_rest", rs["days_since_last_game"],
                 clamp((rs["days_since_last_game"] - 1) * 2, -3, 4))
        if rs.get("consecutive_games") is not None and rs["consecutive_games"] >= 10:
            _sig(signals, "consecutive_games", rs["consecutive_games"],
                 clamp(-(rs["consecutive_games"] - 9) * 0.6, -4, 0))

    # Lineup context: who's on base ahead (RBI opportunity) and who hits
    # behind (protection) -- see build_candidates' own comment on this for
    # why wOBA rather than OBP, why "ahead" doesn't wrap and "behind" does,
    # and why this one (unlike several of the "recorded, not weighted"
    # signals below) is fully backtest-measurable from day one.
    lwc = (ex.get("lineup_woba") or {}).get(bid) if bid else None
    if lwc:
        if lwc.get("woba_ahead") is not None:
            _sig(signals, "woba_ahead", lwc["woba_ahead"], scale(lwc["woba_ahead"], 0.290, 0.400))
        if lwc.get("woba_behind") is not None:
            _sig(signals, "woba_behind", lwc["woba_behind"], scale(lwc["woba_behind"], 0.290, 0.400))

    # Pull rate, which only means something ALONGSIDE the park. A pull-heavy
    # left-handed hitter in a park that plays 138 for left-handed power is a
    # different proposition from the same hitter in a neutral yard, and
    # neither number carries that on its own.
    pl = (ex.get("pull") or {}).get(bid) if bid else None
    if pl and (pl.get("BBE") or 0) >= 40 and ph and bats in ("L", "R"):
        park_idx = ((ph.get(bats) or {}).get("Index"))
        if park_idx is not None:
            # Interaction, not two separate nudges: pull% above league only
            # helps in a park that rewards that side.
            synergy = (pl["Pull%"] - 40.0) * (park_idx - 100) / 100.0
            _sig(signals, "pull_park_synergy", round(synergy, 2),
                 clamp(synergy * 0.35, -4, 4))

    # Hard-hit rate, already used by the value screen but never by the board.
    #
    # BASELINE CORRECTED 2026-08-14, same fix as mlb_sources.hard_hit_
    # game_rates() itself: that function now conditions on events ==
    # "home_run" (matching FanDuel's own "Laser = HR with Specified MPH
    # Exit Velocity" market definition, confirmed live), so this signal
    # is no longer "does he make loud contact in general" -- it now
    # specifically reads "does he combine power AND exit velocity,"
    # closer to a home-run-authority read than a generic contact-quality
    # one. The old 0.215 baseline was the LEAGUE-AVERAGE UNCONDITIONED
    # rate; left in place, every batter's real (now much smaller,
    # HR-conditioned) p_hat would sit far below it and this signal would
    # read strongly negative for nearly everyone, a real bug introduced
    # as a side effect of the correct upstream fix. Replaced with the
    # real HR-conditioned league average (4.66%, measured directly off
    # the same season data hard_hit_game_rates() itself uses).
    hh = (ex.get("hard_hit") or {}).get(bid) if bid else None
    if hh:
        r105 = (hh.get("rates") or {}).get("hard_hit_105_1plus")
        if r105 and r105.get("hit", 0) >= 4:
            _sig(signals, "hard_hit_105_rate", r105["p_hat"],
                 clamp((r105["p_hat"] - 0.0466) * 25, -5, 5))

    # How the market has MOVED on this batter's team since the day opened.
    # Distinct from every other signal here: the rest describe the player or
    # the matchup, this describes what everyone else has concluded since. A
    # team total climbing half a run between the morning and first pitch is
    # information arriving -- a scratch, a wind shift, a bullpen note -- that
    # our own inputs may not carry yet. Keyed by full team name, which was
    # verified to match game_meta's names 22-for-22 rather than assumed.
    lmv = (ex.get("line_move") or {}).get(batter.get("team"))
    if lmv and lmv.get("move") is not None and (lmv.get("hours") or 0) >= 1.0:
        # The move itself, in runs of implied team total. Guarded on elapsed
        # time because a "move" measured between two captures a minute apart
        # is rounding, not information.
        _sig(signals, "team_total_move", lmv["move"], clamp(lmv["move"] * 6, -6, 6))
    if lmv and lmv.get("current") is not None:
        _sig(signals, "team_total_open", lmv["current"],
             clamp((lmv["current"] - 4.5) * 4, -6, 6))
    # Where the money disagrees with the tickets. A line moving toward the
    # side taking the MINORITY of bets but the majority of the money is the
    # classic sharp footprint; recorded raw so that claim can be tested here
    # rather than inherited from betting folklore.
    if lmv and lmv.get("tickets_pct") and lmv.get("money_pct"):
        split = lmv["money_pct"] - lmv["tickets_pct"]
        _sig(signals, "money_ticket_split", split, clamp(split * 0.25, -5, 5))

    # Schedule context. Already computed in game_meta for the report's flag
    # lines and never once read by scoring.
    if gm.get("is_getaway") is not None:
        # Getaway day: the last game of a series, often an early start with
        # travel afterwards and regulars rested. Whether that actually moves
        # a hitter's line is exactly what the measurement is for.
        _sig(signals, "getaway_day", 1 if gm.get("is_getaway") else 0,
             -2 if gm.get("is_getaway") else 0)
    if gm.get("series_game") is not None:
        # Later games in a series mean a lineup that has already seen this
        # staff. NOT the same field as the pitching "opener" that
        # OPENER_BF_THRESHOLD handles -- game_meta's is_opener means SERIES
        # opener, and conflating the two would be a real bug.
        _sig(signals, "series_game", gm["series_game"],
             clamp((gm["series_game"] - 1) * 1.0, 0, 3))

    # The home-plate umpire's own strikeout and walk tendency. Shrunk hard —
    # see umpire_k_bb_rates() for why the raw numbers are three-quarters
    # noise. A high-strikeout umpire is bad for a hitter's contract with the
    # ball: more called third strikes, fewer balls in play to turn into hits.
    # HOW THIS HITTER HANDLES RELIEVERS, WHICH IS NOT A NICHE SPLIT.
    #
    # A hits or total-bases prop plays out over roughly four plate
    # appearances, and only about two or three of those come against the
    # starter — the rest are against a bullpen. Yet essentially every
    # matchup signal in this function is about the opposing STARTER: his
    # ERA, his arsenal, the platoon edge against him, the times-through-the-
    # order penalty. Half the bet has been priced off the wrong pitcher.
    #
    # sp_rp_splits() at the league level only gives the aggregate direction
    # and cannot catch a hitter who runs opposite to it, which is precisely
    # the hitter this would misprice.
    sr = (ex.get("sp_rp_by_id") or {}).get(bid) if bid else None
    if sr and sr.get("OPS_gap") is not None:
        vsp, vrp = sr.get("vsSP") or {}, sr.get("vsRP") or {}
        # Both halves need real evidence. A 5-PA split is noise, and the
        # function's own docstring flags exactly this trap.
        if (vsp.get("PA") or 0) >= 20 and (vrp.get("PA") or 0) >= 20:
            # Positive OPS_gap means better against starters than relievers,
            # so a bullpen-heavy remainder of the game works against him.
            _sig(signals, "sp_rp_ops_gap", sr["OPS_gap"],
                 clamp(-sr["OPS_gap"] * 8, -5, 5))
            try:
                _sig(signals, "vs_rp_ops", round(float(vrp["OPS"]), 3),
                     clamp((float(vrp["OPS"]) - 0.720) * 12, -5, 5))
            except (TypeError, ValueError, KeyError):
                pass

    uk = (ex.get("ump_kbb") or {}).get(gm.get("hp_ump"))
    if uk and uk.get("k_pct") is not None:
        _sig(signals, "ump_k_pct", uk["k_pct"],
             clamp(-(uk["k_pct"] - uk["league_k_pct"]) * 120, -4, 4))
    if uk and uk.get("bb_pct") is not None:
        # A walk-friendly umpire cuts both ways for a hitter: more free passes
        # is good for reaching base and bad for a HITS or TOTAL BASES line,
        # since a walk is neither. Recorded, direction left to the fitter.
        _sig(signals, "ump_bb_pct", uk["bb_pct"],
             clamp((uk["bb_pct"] - uk["league_bb_pct"]) * 120, -4, 4))

    return {
        "type": "batter", "name": name, "player_id": bid, "team": batter.get("team"), "matchup": gm["matchup"],
        "game_pk": gm.get("game_pk"), "prop": prop, "projection": {"stat": "total_bases", "value": projected_tb},
        "projected_pa": projected_pa, "projected_tb": projected_tb, "signals": signals,
        "score": round(score, 1), "why": why, "watchouts": watchouts, "notable_signals": notable_signals,
        "confidence": "High" if score >= 70 and not low_sample else ("Medium" if score >= 55 else "Low"),
        # Raw 0-100 category components BEFORE the 35/25/15/15/10 weighting is
        # applied, recorded so backtest/engine.py can capture them per graded
        # row -- these weights were carried over verbatim from an old manual-
        # reasoning report section (mlb_daily.py's "SYNTHESIS LAYER REFERENCE")
        # and have never been fit or validated as an ensemble against real
        # outcomes. See fit_score_weights.py.
        "cat_matchup": round(matchup, 2), "cat_recent_form": round(form, 2),
        "cat_environment": round(env, 2), "cat_baseline_skill": round(skill, 2),
        "cat_context": round(context, 2),
    }


def score_pitcher(sp_name, sp_id, sp_hand, gm, side, pit_season_lookup, l14_form,
                   opp_lineup, opp_team_k_pct, ump_scores, opp_k_source=None, exp_k_form=None,
                   ump_kbb=None, il_returns=None, callups=None):
    ps = lookup_player(pit_season_lookup, sp_name, sp_id, {})
    exp_k = (exp_k_form or {}).get(sp_id)
    k_pct = ps.get("K%")
    csw = ps.get("CSW%")
    stuff = ps.get("Stuff+")
    era = ps.get("ERA")
    notable_signals = 0

    # MATCHUP (35%) — opposing team K% + same-hand batter share
    same_hand = 0; known = 0
    for b in opp_lineup:
        if b.get("bats") in ("L", "R") and sp_hand in ("L", "R"):
            known += 1
            if b["bats"] == sp_hand: same_hand += 1
    same_hand_ratio = (same_hand / known) if known else 0.4
    sc_opp_k = scale(opp_team_k_pct, 17, 27)
    sc_same_hand = scale(same_hand_ratio * 100, 20, 60)
    matchup = sc_opp_k * 0.65 + sc_same_hand * 0.35

    # RECENT FORM (25%) — L14 K rate vs season K rate (trend) + TTO-specific exploit
    l14 = l14_form.get(sp_name, {})
    form_l14_raw = None
    if l14 and l14.get("l14_pa", 0) >= 15:
        form_l14_raw = scale(l14.get("l14_k_pct"), 15, 32)
        form = form_l14_raw
        low_sample_form = False
    else:
        form = scale(k_pct, 15, 32) if k_pct else 50
        low_sample_form = True
    tto_note = None
    tto_penalty = None
    tto_adjustment = None
    if l14.get("tto3_k_pct") is not None and l14.get("tto1_k_pct") is not None:
        tto_penalty = l14["tto1_k_pct"] - l14["tto3_k_pct"]
        if tto_penalty <= -3:  # maintains or improves K rate deep into starts
            tto_adjustment = 8
            form = clamp(form + 8)
            notable_signals += 1
            tto_note = f"Maintains K rate through the order (TTO1 {l14['tto1_k_pct']}% -> TTO3 {l14['tto3_k_pct']}%)"
        elif tto_penalty >= 8:
            tto_adjustment = -8
            form = clamp(form - 8)
            tto_note = f"Significant TTO K% drop-off (TTO1 {l14['tto1_k_pct']}% -> TTO3 {l14['tto3_k_pct']}%) — caution deep into the start"

    # ENVIRONMENT (15%) — minor for Ks; kept neutral
    env = 50

    # BASELINE SKILL (15%)
    sc_season_k = scale(k_pct, 15, 32)
    sc_csw = scale(csw, 24, 34)
    sc_stuff = scale(stuff, 80, 130)
    skill = sc_season_k * 0.4 + sc_csw * 0.3 + sc_stuff * 0.3
    star_profile = (k_pct or 0) >= 28 and (era or 5) <= 3.2

    # CONTEXT (10%) — tight-zone umpire favors called strikes -> more Ks
    ump = ump_scores.get(gm["matchup"], {})
    context = scale(ump.get("accuracy"), 90, 96) if ump.get("accuracy") else 50
    if ump.get("accuracy") and ump["accuracy"] >= 94: notable_signals += 1

    # PROMOTED 2026-08-14, replacing the original hand-set 35/25/15/15/10
    # split. backtest/fit_score_weights.py's PITCHERS-ONLY cut (strikeouts,
    # ~4.6K usable rows across the real 2024-2025 backtest) is the most
    # robust finding of the whole exercise: cleared the hand-set formula's
    # confidence interval in 5 of 5 independent train/held-out splits
    # (test_frac 0.2 through 0.4), with an almost identical AUC delta
    # (+0.080 to +0.087) every single time -- not a fluke of one cutoff.
    # RECENT FORM inverted (a hot recent K rate predicting WORSE, not
    # better) reproduced with the same sign and similar magnitude on all
    # 5 splits too. ENVIRONMENT and CONTEXT are deliberately left at their
    # ORIGINAL weights, not the fit's numbers -- both are functionally
    # constant for this market (env is hardcoded 50 above; context sits at
    # ump.get("accuracy") or 50 and this season's real data showed it
    # pinned at 50 for every single strikeout row), so the fit's small
    # negative shares for them were a math artifact of feeding a
    # regression two constant columns, not a real signal to promote.
    # MATCHUP/RECENT FORM/BASELINE SKILL are renormalized into the
    # remaining 75% of the budget the original formula gave them,
    # preserving their fitted RELATIVE proportions. See fit_score_weights.
    # py's own CURRENT_WEIGHTS_PITCHER, kept in sync with this line.
    score = clamp(matchup * 0.11 + form * -0.16 + env * 0.15 + skill * 0.48 + context * 0.10)

    watchouts = []
    if low_sample_form:
        watchouts.append("L14 Statcast sample too thin — recent-form read falls back to season K%")
    if era and k_pct and era > 4.5 and k_pct > 25:
        watchouts.append(f"High K% ({k_pct}%) paired with a shaky ERA ({era:.2f}) — command may be inconsistent start-to-start")
    if tto_note: watchouts.append(tto_note) if "drop-off" in tto_note else None

    # Fresh off the injured list -- see score_batter's identical check and
    # mlb_sources.fetch_recent_il_returns' own docstring for why this is
    # informational only, not a score adjustment.
    il = (il_returns or {}).get(sp_id) if sp_id else None
    if il:
        watchouts.append(f"Activated from the {il['il_days']}-day injured list {il['days_ago']} "
                         f"day(s) ago — early performance back can be inconsistent")

    # Recently called up from the minors -- see fetch_recent_callups' own
    # docstring. Real for pitchers too: a spot-start call-up or a September
    # addition has little or no MLB track record behind his season stats.
    cu = (callups or {}).get(sp_id) if sp_id else None
    if cu:
        watchouts.append(f"Recalled from the minors {cu['days_ago']} day(s) ago — thin or no MLB "
                         f"track record behind his season/rolling stats")

    if star_profile and notable_signals == 0:
        score -= 10
        watchouts.append("Built mainly on season-long star power with no additional converging signal — likely already priced by the market")
    elif notable_signals >= 2:
        score = clamp(score + 5)

    projected_ks = project_pitcher_ks(ps, l14, exp_k)
    exp_bf, bf_n_starts, obs_bf = project_pitcher_workload(l14)
    workload_note = None   # merged into `why` once it exists, further down
    if obs_bf is not None:
        workload_note = (f"Expected workload {exp_bf:.1f} batters faced "
                    f"(his own {obs_bf:.1f}/start over {bf_n_starts} real start{'s' if bf_n_starts != 1 else ''}, "
                    f"shrunk toward the {LEAGUE_AVG_BF_PER_START} league mean)")
        if bf_n_starts < 3:
            watchouts.append(f"Workload estimate rests on only {bf_n_starts} start"
                              f"{'s' if bf_n_starts != 1 else ''} in the L14 window — most start-to-start "
                              f"variation in batters faced is noise, so this is shrunk heavily toward league average")
        if exp_bf <= 20.5:
            watchouts.append(f"Short-outing profile ({exp_bf:.1f} expected batters faced) — caps the realistic "
                              f"strikeout ceiling regardless of K rate")
    else:
        workload_note = (f"Expected workload {exp_bf:.1f} batters faced (league average — no real start "
                    f"found for him in the L14 window)")
        watchouts.append("No L14 start found for this pitcher — workload defaulted to the league average")

    # 2026-08-24 explanation-quality fix: this note used to name the
    # specific internal data source and its failure mode directly to the
    # user ("MLB Stats API -- FanGraphs team page unreachable", "FanGraphs
    # and MLB Stats API team pages both unreachable") -- raw provider/outage
    # detail that means nothing to a bettor and reads like leaked debug
    # output (real complaint, Jose Urquidy card). "team" and "mlb_team" are
    # both genuine full-team K% numbers, so they read identically in public
    # copy; the confirmed-batters-average fallback is a real methodology
    # difference (a partial-lineup proxy, not the full team rate) so THAT
    # distinction stays, phrased in terms of what the number IS rather than
    # why the preferred source was unavailable.
    # 2026-08-2X explanation-directionality fix (pitcher-strikeout market,
    # same audit that fixed the batter-side bullpen/sharp-money bugs
    # above): opposing K%, the platoon (same-hand) note, season K%, CSW%,
    # and Stuff+ all used to land in `why` unconditionally, regardless of
    # whether the real, already-computed scale (sc_opp_k/sc_same_hand/
    # sc_season_k/sc_csw/sc_stuff -- each already feeds the score formula
    # above) said the number was actually favorable, neutral, or
    # unfavorable. A contact-oriented opposing lineup, a mostly-opposite-
    # handed lineup, or a below-average K%/CSW%/Stuff+ reading are all
    # genuinely bad news for a strikeouts prop -- they must not render as
    # unqualified positive evidence. Neutral-middle values stay a plain,
    # unqualified fact, matching the identical wRC+/L14-K%/wind convention
    # used elsewhere in this file (>=65 favorable, <=35 unfavorable, else
    # plain).
    why = []
    if opp_team_k_pct is not None:
        if opp_k_source in ("team", "mlb_team"):
            k_note = f"Opposing team K% {opp_team_k_pct:.1f}"
        else:
            k_note = f"Opposing lineup K% {opp_team_k_pct:.1f} (based on {opp_k_source} confirmed lineup batters, not the full team rate)"
        if sc_opp_k >= 65:
            why.append(k_note + " — strikeout-prone lineup")
        elif sc_opp_k <= 35:
            watchouts.append(k_note + " — contact-oriented lineup, tougher matchup")
        else:
            why.append(k_note)
    else:
        # Missing data is not a reason TO like the pick -- belongs in
        # watchouts, not why (this unconditionally landed in `why`, the
        # positive-reasons list, until this fix).
        watchouts.append("Opposing team strikeout tendency unavailable — no team or confirmed-lineup K% data could be matched")
    if workload_note: why.append(workload_note)
    # Natural-language platoon note (real complaint: "known-hand" is
    # internal jargon that means nothing to a bettor) -- was previously
    # "4/9 known-hand opposing batters same-handed", unconditionally in
    # why regardless of whether that ratio actually favored the pitcher.
    hand_word = {"R": "right-handed", "L": "left-handed"}.get(sp_hand)
    if known and hand_word:
        platoon_fact = f"{same_hand} of {known} projected hitters bat {hand_word} against this {sp_hand}HP"
        if sc_same_hand >= 65:
            why.append(f"Platoon advantage: {platoon_fact}")
        elif sc_same_hand <= 35:
            watchouts.append(f"Platoon disadvantage: {platoon_fact} (mostly opposite-handed lineup)")
        else:
            why.append(platoon_fact[0].upper() + platoon_fact[1:])
    elif known:
        why.append(f"{same_hand} of {known} opposing batters match the pitcher's throwing hand")
    else:
        watchouts.append("Opposing lineup handedness unavailable — could not compute a platoon-matchup read")
    if k_pct:
        k_skill_note = f"Season K% {k_pct}"
        if sc_season_k >= 65:
            why.append(k_skill_note + " — above-average strikeout rate")
        elif sc_season_k <= 35:
            watchouts.append(k_skill_note + " — below-average strikeout rate this season")
        else:
            why.append(k_skill_note)
    if csw:
        csw_note = f"CSW% {csw}"
        if sc_csw >= 65:
            why.append(csw_note + " — above-average called+swinging strike rate")
        elif sc_csw <= 35:
            watchouts.append(csw_note + " — below-average called+swinging strike rate")
        else:
            why.append(csw_note)
    if stuff:
        stuff_note = f"Stuff+ {stuff}"
        if sc_stuff >= 65:
            why.append(stuff_note + " — above-average per pitch-modeling")
        elif sc_stuff <= 35:
            watchouts.append(stuff_note + " — below-average per pitch-modeling")
        else:
            why.append(stuff_note)
    # 2026-08-25 explanation-quality fix (same class of bug as the
    # 2026-08-24 batter-side fixes above, found during a release-readiness
    # directional-safety audit): this used to append L14 K% to `why` (the
    # positive-reasons list) unconditionally, regardless of whether the
    # number was actually strong. Real production example: Clay Holmes'
    # L14 K% of 6.7% -- an extremely cold recent strikeout rate for an
    # Over-strikeouts pick -- rendered under "Why It Could Hit" with no
    # qualifying language. form_l14_raw = scale(l14_k_pct, 15, 32) already
    # exists (it IS the RECENT FORM component's own raw value before
    # blending) and is exactly as directional as sc_l7_ev/sc_l7_barrel
    # were for batters -- reused the same way, same neutral-middle-stays-
    # plain rule. When form_l14_raw is None (thin L14 sample), the number
    # stays an unqualified plain fact, same as before -- the separate
    # "L14 Statcast sample too thin" watchout above already covers that
    # case, so this doesn't double up on a thin-sample caveat.
    if l14.get("l14_k_pct") is not None:
        l14_k_note = f"L14 K% {l14['l14_k_pct']} ({l14.get('l14_pa')} PA)"
        if form_l14_raw is not None and form_l14_raw >= 65:
            why.append(l14_k_note + " — hot recent form")
        elif form_l14_raw is not None and form_l14_raw <= 35:
            watchouts.append(l14_k_note + " — cold recent form")
        else:
            why.append(l14_k_note)
    if exp_k and exp_k.get("k_rate") is not None:
        # Same fix, reusing season K%'s own (15, 32) scale bound -- exp_k's
        # k_rate is the same underlying quantity (K% of batters faced),
        # just recency-weighted instead of season-long, so the same bound
        # is a direct reuse, not a new invented judgment.
        exp_k_pct = exp_k['k_rate'] * 100
        sc_exp_k = scale(exp_k_pct, 15, 32)
        exp_k_note = (f"Recency-weighted K rate {exp_k_pct:.1f}% (exp. decay, halflife 30d, "
                    f"{exp_k['n_starts']} real starts / {exp_k['raw_bf']} BF)")
        if sc_exp_k >= 65:
            why.append(exp_k_note + " — drives a favorable strikeout-probability read")
        elif sc_exp_k <= 35:
            watchouts.append(exp_k_note + " — drives a below-average strikeout-probability read")
        else:
            why.append(exp_k_note + " — drives the strikeout probability model")
    if tto_note and "Maintains" in tto_note: why.append(tto_note)
    if ump.get("accuracy"):
        ump_note = f"HP ump accuracy {ump['accuracy']:.1f}%"
        if context >= 65:
            why.append(ump_note + " — tight, accurate zone favors called strikes")
        elif context <= 35:
            watchouts.append(ump_note + " — shakier ball-strike accuracy, fewer called strikes")
        else:
            why.append(ump_note)

    signals = {}
    _sig(signals, "opp_team_k_pct", opp_team_k_pct, sc_opp_k)
    _sig(signals, "same_hand_ratio", same_hand_ratio if known else None, sc_same_hand)
    if not low_sample_form:
        _sig(signals, "l14_k_pct", l14.get("l14_k_pct"), form_l14_raw)
    _sig(signals, "tto_penalty", tto_adjustment, tto_adjustment)
    signals["env_neutral"] = 50.0   # hard-coded neutral in production; always present
    _sig(signals, "season_k_pct", k_pct, sc_season_k)
    _sig(signals, "csw_pct", csw, sc_csw)
    _sig(signals, "stuff_plus", stuff, sc_stuff)
    _sig(signals, "ump_accuracy", ump.get("accuracy"), context)
    # Accuracy was the only umpire number this pipeline had, and it is the
    # wrong one for a strikeout prop: an umpire can call a big zone
    # accurately or a small one badly, so accuracy says nothing about how
    # many strikeouts a game produces. This is the number that does.
    uk = (ump_kbb or {}).get(gm.get("hp_ump"))
    if uk and uk.get("k_pct") is not None:
        _sig(signals, "ump_k_pct", uk["k_pct"],
             clamp((uk["k_pct"] - uk["league_k_pct"]) * 120, -4, 4))
    if uk and uk.get("bb_pct") is not None:
        # Walks end plate appearances without a strikeout and inflate the
        # pitch count, so a walk-friendly zone shortens the start.
        _sig(signals, "ump_bb_pct", uk["bb_pct"],
             clamp(-(uk["bb_pct"] - uk["league_bb_pct"]) * 120, -4, 4))

    return {
        "type": "pitcher", "name": sp_name, "player_id": sp_id,
        "team": gm["away_team"] if side == "away" else gm["home_team"],
        "matchup": gm["matchup"], "game_pk": gm.get("game_pk"),
        "prop": f"Over {max(projected_ks - 0.5, 0.5):.1f} Strikeouts (proj. {projected_ks})",
        "projection": {"stat": "strikeouts", "value": projected_ks}, "signals": signals,
        "expected_bf": exp_bf,
        # Same priority order as project_pitcher_ks: exp_k (measured to beat
        # the hard L14 window out-of-sample) > hard L14 window > season K%.
        "k_rate": (exp_k["k_rate"] if exp_k and exp_k.get("k_rate") is not None
                   else (l14["l14_k_pct"] / 100 if l14.get("l14_pa", 0) >= 15
                         else (k_pct or 22.5) / 100)),
        "score": round(score, 1), "why": why, "watchouts": watchouts, "notable_signals": notable_signals,
        "confidence": "High" if score >= 70 and not low_sample_form else ("Medium" if score >= 55 else "Low"),
        # See the matching comment in score_batter's return dict above.
        "cat_matchup": round(matchup, 2), "cat_recent_form": round(form, 2),
        "cat_environment": round(env, 2), "cat_baseline_skill": round(skill, 2),
        "cat_context": round(context, 2),
    }


LEAGUE_AVG_SPRINT = 27.0     # ft/s
LEAGUE_AVG_POPTIME = 2.0     # seconds, catcher pop time to 2B
LEAGUE_AVG_BB_PCT = 8.5

# On-base baselines, MEASURED live from tonight's real season-batting pull
# (Statcast-fallback shape, 409 batters with 100+ PA — the shape that actually
# ships, since FanGraphs 403s on most real runs):
#     wOBA  mean .3126  SD .0399  p10 .2608  p90 .3650
# The p10-p90 band is used as the scale, so a genuinely average on-base bat
# lands mid-scale by construction rather than by a chosen number.
LEAGUE_WOBA_MEAN = 0.3126
WOBA_P10, WOBA_P90 = 0.2608, 0.3650
OBP_P10, OBP_P90 = 0.290, 0.370   # standard OBP band, used only on the FanGraphs path


def _on_base_score(bs):
    """0-100 'how often does this man actually reach base' score, plus a
    human-readable note. Returns (score|None, note|None).

    Prefers real OBP when FanGraphs is reachable, but the COMMON case is the
    Statcast fallback, which carries no OBP at all — verified live tonight:
    the season-batting frame came back with columns
    [Name, player_id, year, pa, bip, AVG, xBA, est_ba_minus_ba_diff, slg,
     est_slg, est_slg_minus_slg_diff, wOBA, xwOBA, est_woba_minus_woba_diff,
     Barrel%, HardHit%] — no OBP, no BB%, and no SB. wOBA is present and is a
    direct weighted on-base rate, so it is the fallback."""
    bs = bs or {}
    obp = bs.get("OBP")
    if obp is not None and obp == obp:
        return scale(obp, OBP_P10, OBP_P90), f"OBP {obp:.3f}"
    woba = bs.get("wOBA")
    if woba is not None and woba == woba:
        # Thin samples produce absurd wOBA (a 1-PA batter showed .698 tonight).
        pa = bs.get("pa") or 0
        if pa and pa >= 40:
            return scale(woba, WOBA_P10, WOBA_P90), f"wOBA {woba:.3f} (league ~{LEAGUE_WOBA_MEAN:.3f})"
    return None, None


# Real starts of shrinkage evidence, confidence-cap side (mirrors
# LASER_SCORE_CONFIDENCE_GAMES's role, not mlb_sources.empirical_pitcher_outs_rates's
# own prior_games=6, which regularises the PROBABILITY). A starter with
# barely enough starts to clear min_starts=5 can still land a properly
# shrunk, genuinely useful hit_probability -- but the 0-100 quality score
# (what MIN_QUALITY_SCORE and category ranking actually gate on) should
# still read that as a thinner read than a starter with 15+ real starts.
PITCHER_OUTS_SCORE_CONFIDENCE_STARTS = 10


def score_pitcher_outs(sp_name, sp_id, gm, side, outs_rates, po_prices=None):
    """FanDuel's real "Pitcher Outs Recorded" market
    (PITCHER_A/B_OUTS_RECORDED_SB -- found live 2026-08-07, MARKET_MAP
    never carried it before now). Outs recorded is exactly what innings
    pitched counts, so mlb_sources.empirical_pitcher_outs_rates reads it
    straight off MLB's own official inningsPitched notation rather than
    reconstructing it from raw pitch events (which would have to
    separately handle double plays, sac flies, and every other multi- or
    zero-out outcome by hand -- a real and avoidable source of a subtly
    wrong number).

    Same design as score_laser and for the same reason: no separate
    matchup-specific model of how long a start goes exists here, only the
    pitcher's own shrunk record (Beta prior, prior_games=6, same constant
    empirical_pitcher_k_rates already uses and was independently measured
    for), so p_hat is used directly as hit_probability with no modelled
    blend.

    THE LINE-SELECTION BUG THIS REPLACES. _pick_line's floor (MIN_LINE_PROB
    = 0.60) is right for markets where the book posts SEVERAL thresholds
    (hits, strikeouts) and the model should pick the most informative one
    that still clears a "genuinely likely" bar. Pitcher Outs Recorded posts
    exactly ONE line per starter, set by the book near his own median
    workload -- close to a coinflip by construction (verified live against
    a real screenshot: FanDuel had Skenes at 17.5 outs, -144/+108, and Eury
    Perez at 16.5, -132/+100 -- both around 55-59% implied). Running that
    single real line through the 0.60 floor doesn't pick "the more
    informative of several real lines" -- it guarantees the real line is
    EXCLUDED and some much easier, lower threshold wins instead: a real run
    recommended "Over 11.5 Outs" (4.0 IP) for Skenes the same night FanDuel's
    actual market was 17.5 (5.83 IP), a number FanDuel never offered and
    this model had no read on -- and because market_odds is only attached
    when the recommended `needs` matches a real posted line, the mismatch
    also meant these picks silently carried no price at all.

    When the real market line is available (po_prices), it is used
    directly -- there is only one number to price, so there is nothing to
    select among. Only when no real line can be found (odds not posted yet,
    a name-match miss) does this fall back to the pitcher's own average
    workload as the anchor -- at least the same KIND of number the book
    would post, instead of the easiest threshold that happens to clear an
    unrelated probability floor."""
    if not sp_id:
        return None
    r = outs_rates.get(sp_id)
    if not r or not r.get("rates"):
        return None
    opts = []
    for t in range(12, 22):
        rate = r["rates"].get(f"outs_{t}plus")
        if not rate:
            continue
        lg = rate.get("league_p") or 0.0
        opts.append({"threshold": t, "prob": rate["p_hat"], "base_rate": lg,
                     "lift": round(rate["p_hat"] - lg, 4)})
    if not opts:
        return None

    real_line = None
    if po_prices:
        import odds_fanduel as _fd
        real_line = po_prices.get(_fd.normalize_name(sp_name))
    best = None
    if real_line and real_line.get("needs") is not None:
        best = next((o for o in opts if o["threshold"] == real_line["needs"]), None)
    if best is None:
        # No real market line to price against -- anchor on the pitcher's
        # own average workload rather than _pick_line's probability-floor
        # search, for the reason in the docstring above.
        avg = r.get("avg_outs")
        target = round(avg) if avg is not None else 15
        best = min(opts, key=lambda o: (abs(o["threshold"] - target), o["threshold"]))
    n = r.get("starts", 0)
    score = clamp(best["prob"] * 100 + best["lift"] * 150)
    if n < PITCHER_OUTS_SCORE_CONFIDENCE_STARTS:
        score = min(score, 55)
    ip_str = f"{best['threshold'] // 3}.{best['threshold'] % 3}"
    why = [f"Recorded {best['threshold']}+ outs ({ip_str} IP) in {best['prob'] * 100:.1f}% of his "
          f"last {n} real starts (league {(best['base_rate'] or 0) * 100:.1f}%, avg {r.get('avg_outs')} outs/start)"]
    watchouts = []
    if n < PITCHER_OUTS_SCORE_CONFIDENCE_STARTS:
        watchouts.append(f"Only {n} real starts of workload history -- shrunk heavily toward league average")
    if real_line is None:
        watchouts.append("No real FanDuel line found for this pitcher yet -- this threshold is model-anchored to his average workload, not a posted market number")
    alternatives = [{"stat": "pitcher_outs", "line": o["threshold"] - 0.5, "needs": o["threshold"],
                     "prob": o["prob"], "base_rate": o["base_rate"], "lift": o["lift"]}
                    for o in opts if o is not best][:3]
    return {
        "type": "pitcher", "name": sp_name, "player_id": sp_id,
        "team": gm["away_team"] if side == "away" else gm["home_team"], "side": side,
        "matchup": gm["matchup"], "game_pk": gm.get("game_pk"),
        "prop": f"Over {best['threshold'] - 0.5} Outs Recorded",
        "projection": {"stat": "pitcher_outs", "value": best["threshold"] - 0.5, "needs": best["threshold"]},
        "hit_probability": round(best["prob"], 4),
        "base_rate": best["base_rate"], "lift": best["lift"],
        "probability_basis": "empirical_shrunk",
        "probability_detail": {"empirical": best["prob"], "modelled": None},
        "sample_n": n, "alternatives": alternatives,
        "signals": {"outs_rate": best["prob"], "avg_outs_per_start": r.get("avg_outs")},
        "score": round(score, 1),
        "why": why, "watchouts": watchouts, "notable_signals": 1 if best["lift"] >= 0.05 else 0,
        "confidence": "High" if score >= 70 and n >= PITCHER_OUTS_SCORE_CONFIDENCE_STARTS
                     else ("Medium" if score >= 55 else "Low"),
    }


def _combined_k_starter_note(pitcher_c):
    """One line summarizing the real drivers behind a single starter's OWN
    strikeout projection, read directly off his already-scored
    score_pitcher() candidate. No new computation, no padding: a field
    that isn't actually known for this start (thin recent-form window, no
    matched opponent K%, etc.) is simply omitted rather than filled with a
    guess, and a starter the pipeline genuinely has little on gets a short,
    honest note instead of a padded one."""
    pitcher_c = pitcher_c or {}
    name = pitcher_c.get("name") or "?"
    proj = (pitcher_c.get("projection") or {}).get("value")
    exp_bf = pitcher_c.get("expected_bf")
    k_rate = pitcher_c.get("k_rate")
    sig = pitcher_c.get("signals") or {}
    opp_k = sig.get("opp_team_k_pct")
    l14_k = sig.get("l14_k_pct")
    csw = sig.get("csw_pct")
    parts = []
    if proj is not None:
        parts.append(f"proj. {proj} Ks")
    if exp_bf is not None:
        parts.append(f"{exp_bf:.1f} exp. BF")
    if k_rate is not None:
        parts.append(f"K rate {k_rate*100:.1f}%")
    if opp_k is not None:
        parts.append(f"opp K% {opp_k:.1f}")
    if l14_k is not None:
        parts.append(f"L14 K% {l14_k:.1f}")
    if csw is not None:
        parts.append(f"CSW% {csw}")
    if not parts:
        return f"{name}: little real data behind this projection yet"
    return f"{name}: " + ", ".join(parts)


def score_combined_strikeouts(gm, away_pitcher_c, home_pitcher_c, combined_prices):
    """"Starting Pitcher Combined Alt Strikeouts" -- found live under the
    same tab fetch_pitcher_outs already scans, a real ladder market
    (odds_fanduel.fetch_combined_pitcher_strikeouts) that had nothing
    reading it. A direct example of the "we shouldn't be blind to any real
    prop" audit: this project already computes everything the market needs
    -- each starter's own expected batters faced and K rate, set inside
    score_pitcher() for that pitcher's OWN strikeout line -- and simply
    never combined the two.

    UNLIKE Pitcher Outs Recorded, this market posts SEVERAL real
    thresholds per game (12+, 13+, 14+, ...), the same "book prices more
    than one line" shape as hits/total_bases/strikeouts, so _pick_line's
    existing floor-then-lift selection is the right tool here, not the
    single-real-number special case score_pitcher_outs needed. What's
    different from _pick_line's other callers: `base_rate` here is the
    MARKET's own implied probability at each rung (there is no
    pre-existing "league combined-strikeout rate" table this project
    tracks), so `lift` reads as a genuine PRICE edge -- model probability
    minus what FanDuel is charging for that same rung -- rather than a
    read against the league. That is arguably the more honest use of
    "lift" anyway: it is what the pick is actually being screened against.

    ONLY SCORED WHEN FANDUEL HAS ALREADY POSTED IT. No model-only fallback
    like score_pitcher_outs' average-workload anchor -- a made-up combined
    line with no real market to grade against would be a candidate this
    pipeline could never price or settle honestly, and this market simply
    doesn't exist to bet until the book posts it.

    INDEPENDENCE ASSUMPTION: see prop_probability.combined_strikeouts_
    distribution's own docstring -- two opposing starters' K totals are
    treated as independent, a documented approximation, not a proven fact."""
    rungs = (combined_prices or {}).get(gm.get("matchup"))
    if not rungs or not rungs.get("rungs"):
        return None
    bf_a, k_a = away_pitcher_c.get("expected_bf"), away_pitcher_c.get("k_rate")
    bf_b, k_b = home_pitcher_c.get("expected_bf"), home_pitcher_c.get("k_rate")
    if not bf_a or not bf_b or k_a is None or k_b is None:
        return None
    opts = []
    for threshold, odds in rungs["rungs"].items():
        prob = pp.p_at_least_combined_strikeouts(threshold, bf_a, k_a, bf_b, k_b)
        implied = pp.implied_probability(odds)
        if implied is None:
            continue
        opts.append({"threshold": threshold, "prob": prob, "base_rate": implied,
                     "lift": round(prob - implied, 4), "odds": odds})
    if not opts:
        return None
    best = _pick_line(opts)
    name_a, name_b = rungs["pitchers"]
    score = clamp(best["prob"] * 100 + best["lift"] * 150)
    # NOT away_pitcher_c.get("sample_n")/home_pitcher_c.get("sample_n") --
    # score_pitcher()'s own return dict never sets that key (only
    # attach_reliability adds it, AFTER build_candidates already ran and
    # returned this dict), so that read silently always evaluated to 0 --
    # not "zero real starts of evidence" the way it reads, just a field
    # that doesn't exist yet at this point in the pipeline. None here is
    # honest: this derived, brand-new market has no sample count of its
    # own to report (see the matching attach_reliability branch below).
    thin = (away_pitcher_c.get("confidence") == "Low" or home_pitcher_c.get("confidence") == "Low")
    if thin:
        score = min(score, 55)
    # 2026-08-24 explanation-quality pass: this used to be a single
    # "Model: X% chance..." line and nothing else -- the model probability
    # restated, not an explanation of what produced it. Each starter's own
    # score_pitcher() candidate ALREADY carries the real drivers behind his
    # individual projection (his own projected Ks, expected BF, K rate,
    # opponent K%, recent-form K%, CSW%) -- _combined_k_starter_note()
    # below just reads those straight off the candidate, no new
    # computation, no padding. A starter this pipeline has little on gets
    # a correspondingly short note instead of a fabricated one.
    why = [f"Model: {best['prob']*100:.1f}% chance of {best['threshold']}+ combined Ks "
          f"({name_a} + {name_b}), priced at {best['odds']:+d} ({best['base_rate']*100:.1f}% implied)",
          _combined_k_starter_note(away_pitcher_c), _combined_k_starter_note(home_pitcher_c)]
    ump_k = ((away_pitcher_c.get("signals") or {}).get("ump_k_pct")
             or (home_pitcher_c.get("signals") or {}).get("ump_k_pct"))
    if ump_k is not None:
        why.append(f"HP umpire strikeout rate {ump_k:.1f}% for tonight's game")
    watchouts = ["This is a newer market on the board -- not enough graded combined-strikeout "
                 "picks yet for a reliability grade. Treat it as unproven until more results come in."]
    if thin:
        thin_names = [c.get("name") for c in (away_pitcher_c, home_pitcher_c) if c.get("confidence") == "Low"]
        watchouts.append(f"Thin evidence behind {' and '.join(n for n in thin_names if n)}'s own strikeout "
                          f"projection -- capping how much this pick can be trusted")
    alternatives = [{"stat": "combined_strikeouts", "line": o["threshold"] - 0.5, "needs": o["threshold"],
                     "prob": o["prob"], "base_rate": o["base_rate"], "lift": o["lift"], "odds": o["odds"]}
                    for o in opts if o is not best][:3]
    return {
        "type": "pitcher_combo", "name": f"{name_a} & {name_b}",
        "player_id": away_pitcher_c.get("player_id"),
        "combo_player_ids": [away_pitcher_c.get("player_id"), home_pitcher_c.get("player_id")],
        "team": None, "matchup": gm["matchup"], "game_pk": gm.get("game_pk"),
        "prop": f"Over {best['threshold'] - 0.5} Combined Strikeouts",
        "projection": {"stat": "combined_strikeouts", "value": best["threshold"] - 0.5,
                       "needs": best["threshold"]},
        "hit_probability": round(best["prob"], 4),
        "base_rate": best["base_rate"], "lift": best["lift"],
        "probability_basis": "modelled_independent_binomials",
        "probability_detail": {"empirical": None, "modelled": best["prob"]},
        "market_odds": best["odds"], "market_implied": round(best["base_rate"], 4),
        "market_edge": best["lift"],
        # market-edge-semantics fix (P0-6): combined_strikeouts is a
        # one-sided ladder (12+, 13+, ... escalating odds, no paired
        # Under), so base_rate/market_implied is the RAW posted implied
        # probability, never de-vigged -- same honesty fix as
        # odds_fanduel.attach_market_prices' own generic branch.
        "posted_implied": round(best["base_rate"], 4),
        "market_fair": round(pp.devig(best["base_rate"]), 4),
        "market_fair_method": "assumed_hold",
        "edge_vs_fair": round(best["prob"] - pp.devig(best["base_rate"]), 4),
        "price_clears": pp.price_is_acceptable(best["odds"], best["prob"]),
        "sample_n": None, "alternatives": alternatives,
        "signals": {"combined_k_edge": best["lift"]},
        "score": round(score, 1),
        "why": why, "watchouts": watchouts,
        "notable_signals": 1 if best["lift"] >= 0.05 else 0,
        "confidence": "High" if score >= 70 and not thin else ("Medium" if score >= 55 else "Low"),
    }


def score_stolen_base(batter, gm, opp_catcher_poptime, sprint_speed, batter_season,
                      opp_cs_pct=None):
    """Speed is the dominant SKILL signal, but it is not the gating one: a
    player has to REACH BASE before speed and catcher pop time matter at all.
    Elite speed attached to a .280 OBP is far fewer steal chances than the
    same speed attached to a .360 OBP, and the previous version of this
    function had no on-base term whatsoever.

    Worse, the term it did have was dead on the common path. `context` was
    scale(season SB), and the Statcast-fallback season frame — which is what
    actually ships, since FanGraphs 403s on most real runs — carries no SB
    column at all, so bs.get("SB") was None and context silently defaulted to
    a flat 50 for every runner in the slate. Verified live tonight against
    the real 613-row fallback frame. On-base ability replaces it as the third
    component because, unlike season SB, it is genuinely available on the
    fallback path (via wOBA).

    Weights: speed .50 / catcher matchup .28 / on-base .22. On-base gets real
    weight because it gates opportunity, but stays below speed because the
    sprint-speed filter above is what makes a candidate plausible at all.
    Season SB, when it IS available (FanGraphs path), is kept as a converging
    signal rather than as a scored component.

    The projection stays at exactly 1 deliberately. grade_results.py grades
    stolen_base as actual >= projection - 0.5, so any fractional expected-SB
    number below 0.5 would make a 0-steal night grade as a HIT. The
    opportunity read belongs in the reasoning, not in that field."""
    bid = batter.get("id")
    if not sprint_speed or sprint_speed < 27.3:
        return None  # not a plausible SB threat regardless of matchup
    notable_signals = 0
    skill = scale(sprint_speed, 27.3, 30.5)
    # REAL BUG, found by test_score_stolen_base.py: this scale() call had its
    # bounds swapped (2.25, 1.90 -- descending), which maps a SLOW catcher
    # (poptime near 2.25, an easy target) to a LOW matchup score and a FAST,
    # elite-armed catcher (poptime near 1.90, a hard target) to a HIGH one --
    # exactly backwards for a stolen-base prop, and directly contradicted by
    # the very next line, which has always treated a slow poptime (>=2.10)
    # as a NOTABLE GOOD signal. Every other descending scale() call in this
    # file (score_first_inning's YRFI/NRFI branch) is deliberate and
    # comment-justified; this one wasn't, and had no such justification --
    # an accidental bound swap, not a convention. Ascending bounds (1.90,
    # 2.25) now score a slow catcher high and a fast catcher low, matching
    # the notable_signals check and the actual market.
    # A missing reading (no confirmed catcher yet, or a real one below Statcast's
    # own min_2b_att=3 reliability floor -- verified live: Atlanta's Sean Murphy
    # and LA's Ben Rortvedt each have exactly 1 real 2B steal attempt against
    # them as of 2026-08-12, not zero data, just not enough of it to trust) used
    # to default matchup to a flat 50. That doesn't correspond to anything real:
    # scale(LEAGUE_AVG_POPTIME, 1.90, 2.25) -- what an average-armed catcher
    # actually scores here -- is ~28.6, not 50. Falling back to the real league
    # average instead of an arbitrary midpoint keeps this honest without
    # fabricating a catcher-specific number that doesn't exist yet.
    matchup = scale(opp_catcher_poptime, 1.90, 2.25) if opp_catcher_poptime else scale(LEAGUE_AVG_POPTIME, 1.90, 2.25)
    if opp_catcher_poptime and opp_catcher_poptime >= 2.10: notable_signals += 1
    bs = batter_season or {}
    season_sb = bs.get("SB")
    on_base, on_base_note = _on_base_score(bs)
    context = on_base if on_base is not None else 50

    score = skill * 0.50 + matchup * 0.28 + context * 0.22
    if season_sb and season_sb >= 15: notable_signals += 1
    if on_base is not None and on_base >= 75: notable_signals += 1

    # 2026-08-25 explanation-quality fix (release-readiness audit, same class
    # of bug as generate_picks.py's L14-K%/sp_era/ev_note/barrel_note fixes
    # above): all three of these used to land in `why` unconditionally
    # whenever the raw value existed, regardless of whether it was actually
    # a GOOD reading. Real production examples found via docs/data.json: a
    # 1.88s catcher pop time (an elite, hard-to-steal-on arm -- matchup
    # scores near 0) rendered under "Why It Could Hit" for Bobby Witt Jr.
    # and 180 total stolen_base props on today's slate; Jose Ramirez's
    # 27.7ft/s sprint speed (barely above the 27.3 qualifying floor, skill
    # scores ~13) rendered the same way. `skill`/`matchup`/`on_base` are the
    # scored components' own already-computed directional values -- reused
    # the same way, same neutral-middle-stays-plain rule.
    why = []
    if skill >= 65:
        why.append(f"Sprint speed {sprint_speed:.1f}ft/s (league ~{LEAGUE_AVG_SPRINT}) — plus speed")
    else:
        why.append(f"Sprint speed {sprint_speed:.1f}ft/s (league ~{LEAGUE_AVG_SPRINT})")
    if opp_catcher_poptime:
        poptime_note = f"Opposing catcher pop time {opp_catcher_poptime:.2f}s to 2B (league ~{LEAGUE_AVG_POPTIME}s)"
        if matchup >= 65:
            why.append(poptime_note + " — a slow-armed catcher, easier to run on")
        elif matchup <= 35:
            # NOT appended here; folded into the opp_cs_pct-style watchout
            # below instead, which already exists at the >=0.30 CS%
            # threshold -- see the watchouts block for the poptime-specific
            # variant of that same "hard to run on" fact.
            pass
        else:
            why.append(poptime_note)
    if on_base_note:
        if on_base is not None and on_base >= 65:
            why.append(f"On-base ability: {on_base_note} — gates how often he's on first to run at all (favorable)")
        elif on_base is not None and on_base <= 25:
            # NOT appended to why -- the existing "Fast, but a weak
            # on-base rate..." watchout below already covers this fact
            # more specifically; showing the plain note here too would
            # put the same weak reading in both lists.
            pass
        else:
            why.append(f"On-base ability: {on_base_note} — gates how often he's on first to run at all")
    if season_sb is not None: why.append(f"Season SB: {season_sb}")
    watchouts = []
    if opp_cs_pct is not None and opp_cs_pct >= 0.30:
        watchouts.append(f"Opposing team throws out {opp_cs_pct*100:.0f}% of runners "
                          f"(league ~25%) — a genuinely hard team to run on")
    if opp_catcher_poptime and matchup <= 35:
        watchouts.append(f"Opposing catcher pop time {opp_catcher_poptime:.2f}s to 2B (league ~{LEAGUE_AVG_POPTIME}s) "
                          f"— a fast, hard-to-run-on arm")
    if not opp_catcher_poptime: watchouts.append(f"Opposing catcher pop time unavailable — matchup component defaulted to the league average ({LEAGUE_AVG_POPTIME}s)")
    if on_base is None:
        watchouts.append("No usable on-base rate (no OBP, and wOBA sample under 40 PA) — "
                          "steal-opportunity component defaulted to neutral")
    elif on_base <= 25:
        watchouts.append("Fast, but a weak on-base rate means materially fewer times on first to steal from")

    signals = {}
    # THE OPPOSING TEAM'S ACTUAL CAUGHT-STEALING RATE. Pop time is a proxy for
    # this; CS% is the outcome itself, and folds in what pop time misses --
    # the pitcher's time to the plate, how well he holds runners, the
    # catcher's accuracy rather than just arm speed. It was dismissed as
    # redundant without being looked at, which was wrong: nothing else in the
    # steal model measures it, and teams range from .195 to .324 this season.
    if opp_cs_pct is not None:
        _sig(signals, "opp_team_cs_pct", opp_cs_pct,
             clamp(-(opp_cs_pct - 0.25) * 60, -6, 6))
    _sig(signals, "sprint_speed", sprint_speed, skill)
    _sig(signals, "catcher_poptime", opp_catcher_poptime, matchup)
    # The third scored component is on-base ability, NOT season SB — season SB
    # is only a converging-signal flag on this path (see this function's
    # docstring). backtest/signals.py's CURRENT_WEIGHTS still calls the slot
    # "season_sb"; recording what the code actually scores under its real name
    # is the honest emit, and a name the fitter doesn't recognise surfaces as a
    # coverage warning there rather than silently mislabelling the input.
    _sig(signals, "on_base", on_base, context)

    return {
        "type": "batter", "name": batter["name"], "player_id": bid, "team": batter.get("team"),
        "matchup": gm["matchup"], "game_pk": gm.get("game_pk"), "prop": "To Steal a Base",
        "projection": {"stat": "stolen_base", "value": 1}, "signals": signals,
        "projected_pa": project_batter_pa(batter.get("order"), None),
        "score": round(score, 1),
        "why": why, "watchouts": watchouts, "notable_signals": notable_signals,
        "confidence": "High" if score >= 70 else ("Medium" if score >= 55 else "Low"),
        # Raw 0-100 category components BEFORE the 50/28/22 weighting, same
        # instrumentation pattern and purpose as score_batter/score_pitcher's
        # cat_matchup etc -- see fit_score_weights.py. Named sb_cat_* (not
        # cat_*) because this is a different 3-category scheme (skill/
        # matchup/context weighted 50/28/22), not the 5-category 35/25/15/
        # 15/10 framework, and the two must not collide in the shared rows.jsonl.
        "sb_cat_skill": round(skill, 2), "sb_cat_matchup": round(matchup, 2),
        "sb_cat_context": round(context, 2),
    }


# Games of shrinkage evidence for the score's own confidence cap -- separate
# from mlb_sources.hard_hit_game_rates()'s own prior_games=20 (which already
# regularises the PROBABILITY). This caps the 0-100 quality score specifically,
# same two-layer pattern as score_first_inning's sample penalty: a properly
# shrunk probability can still come from a thin, barely-past-the-floor sample,
# and the score is what the MIN_QUALITY_SCORE floor and category ranking
# actually gate on.
LASER_SCORE_CONFIDENCE_GAMES = 60


def score_laser(batter, gm, hard_hit_rates):
    """A real, priced FanDuel market (105+/110+ MPH exit velocity -- FanDuel
    calls it a "Laser") this project already computes a properly shrunk rate
    for (mlb_sources.hard_hit_game_rates), but the rate only ever fed OTHER
    props as a signal (see score_batter's hard_hit_105_rate) and never became
    its own candidate. MARKET_MAP has carried the pricing keys
    (hard_hit_105/hard_hit_110) since before this function existed -- the gap
    was entirely on the scoring side.

    NOT "any batted ball at that exit velocity" -- FanDuel's own "Full
    details" text for this market reads "Laser = HR with Specified MPH
    Exit Velocity," confirmed live 2026-08-14 against real market odds
    (Fernando Tatis Jr. +650, Jo Adell +900 -- both far too long to be
    "any hard-hit ball," a common event). hard_hit_game_rates() itself
    was fixed to condition on a real home run at that exit velocity, so
    this function needed no changes -- it was always going to use
    whatever that function returned, and now that's the right thing.

    hard_hit_game_rates() already shrinks toward the TRUE league rate (pooled
    across every batter, not the slate -- same discipline as every other
    empirical rate in this file) via a real Beta-prior fit, so p_hat is used
    directly as hit_probability. No separate modelled component: there is no
    matchup-specific physics model of exit velocity here, only the batter's
    own shrunk record, which is what the market itself mostly prices off of
    too. Picks whichever of the two thresholds this batter clears more often
    -- same "best of several real lines" pattern as score_stolen_base's
    sibling strikeout scorer, kept comparable across players because both
    thresholds are shrunk the same way -- via _pick_line, the same "highest
    LIFT among lines that clear the floor" rule strikeouts/hits use, not
    raw probability. Raw probability would be a real bug here specifically:
    P(105+) is structurally always higher than P(110+) (110+ is a strict
    subset of 105+), so a naive max() would deterministically always pick
    105+ and never let a genuine 110+ standout show up as itself."""
    bid = batter.get("id")
    if not bid:
        return None
    hh = hard_hit_rates.get(bid)
    if not hh or not hh.get("rates"):
        return None
    opts = []
    for thr in (105, 110):
        r = hh["rates"].get(f"hard_hit_{thr}_1plus")
        if not r:
            continue
        lg = r.get("league_p") or 0.0
        opts.append({"threshold": thr, "prob": r["p_hat"], "base_rate": lg,
                     "lift": round(r["p_hat"] - lg, 4), "n": r["n"]})
    if not opts:
        return None
    best = _pick_line(opts)
    lift = best["lift"]
    n = best["n"]
    score = clamp(best["prob"] * 100 + lift * 150)
    if n < LASER_SCORE_CONFIDENCE_GAMES:
        score = min(score, 55)  # thin-relative-to-a-season sample: never more than a low/medium lean
    why = [f"Hit a home run at {best['threshold']}+ MPH exit velocity in {best['prob'] * 100:.1f}% of his "
          f"last {n} games with a batted ball (league {(best['base_rate'] or 0) * 100:.1f}%)"]
    alternatives = [{"stat": f"hard_hit_{o['threshold']}", "line": 1, "needs": 1,
                     "prob": o["prob"], "base_rate": o["base_rate"], "lift": o["lift"]}
                    for o in opts if o is not best]
    return {
        "type": "batter", "name": batter["name"], "player_id": bid, "team": batter.get("team"),
        "matchup": gm["matchup"], "game_pk": gm.get("game_pk"),
        "prop": f"To Hit a Laser ({best['threshold']}+ MPH)",
        "projection": {"stat": f"hard_hit_{best['threshold']}", "value": 1, "needs": 1},
        "hit_probability": round(best["prob"], 4),
        "base_rate": best["base_rate"], "lift": lift,
        "probability_basis": "empirical_shrunk",
        "probability_detail": {"empirical": best["prob"], "modelled": None},
        "sample_n": n, "alternatives": alternatives,
        "signals": {"hard_hit_rate": best["prob"]},
        "score": round(score, 1),
        "why": why, "watchouts": [], "notable_signals": 1 if lift >= 0.05 else 0,
        "confidence": "High" if score >= 70 and n >= LASER_SCORE_CONFIDENCE_GAMES
                     else ("Medium" if score >= 55 else "Low"),
    }


MOONSHOT_SCORE_CONFIDENCE_GAMES = 60
MOONSHOT_THRESHOLD_FT = 420


def score_moonshot(batter, gm, moonshot_rates_table, threshold_ft=MOONSHOT_THRESHOLD_FT):
    """FanDuel's real "To Hit a Moonshot (420+ FT)" market
    (PLAYER_TO_HIT_A_HOME_RUN_420+_FEET) -- found live 2026-08-14 from a
    screenshot of the user's own FanDuel app, matched exactly against a
    live API pull for the same slate and players (Suzuki +1900, Happ
    +2000, Swanson +2200, all confirmed identical). Same shape as
    score_laser, deliberately: mlb_sources.moonshot_rates() already
    shrinks toward the true league-wide per-game rate via the same
    Beta-prior discipline hard_hit_game_rates uses, so p_hat is used
    directly as hit_probability -- no separate modelled component, same
    reasoning as the Laser market (there is no matchup-specific physics
    model of home-run distance here, only the batter's own shrunk power
    profile, which is close to what the real market prices off of too).

    Single threshold, unlike score_laser's two -- FanDuel only posts
    420+ FT (verified live, no 400+ FT market exists anywhere in the
    API), so there is nothing to pick between."""
    bid = batter.get("id")
    if not bid:
        return None
    mh = moonshot_rates_table.get(bid)
    if not mh or not mh.get("rates"):
        return None
    r = mh["rates"].get(f"moonshot_{threshold_ft}plus")
    if not r:
        return None
    prob = r["p_hat"]
    base_rate = r.get("league_p") or 0.0
    lift = round(prob - base_rate, 4)
    n = r["n"]
    score = clamp(prob * 100 + lift * 150)
    if n < MOONSHOT_SCORE_CONFIDENCE_GAMES:
        score = min(score, 55)
    why = [f"Hit a {threshold_ft}+ ft home run in {prob * 100:.1f}% of his last {n} games "
          f"with a batted ball (league {base_rate * 100:.1f}%)"]
    return {
        "type": "batter", "name": batter["name"], "player_id": bid, "team": batter.get("team"),
        "matchup": gm["matchup"], "game_pk": gm.get("game_pk"),
        "prop": f"To Hit a Moonshot ({threshold_ft}+ FT)",
        "projection": {"stat": f"moonshot_{threshold_ft}", "value": 1, "needs": 1},
        "hit_probability": round(prob, 4),
        "base_rate": base_rate, "lift": lift,
        "probability_basis": "empirical_shrunk",
        "probability_detail": {"empirical": prob, "modelled": None},
        "sample_n": n, "alternatives": [],
        "signals": {"moonshot_rate": prob},
        "score": round(score, 1),
        "why": why, "watchouts": [], "notable_signals": 1 if lift >= 0.03 else 0,
        "confidence": "High" if score >= 70 and n >= MOONSHOT_SCORE_CONFIDENCE_GAMES
                     else ("Medium" if score >= 55 else "Low"),
    }


def score_walk(batter, gm, opp_sp_row, ump_scores, batter_season, ump_kbb=None):
    """A patient hitter facing a wild pitcher and a loose-zone umpire — a
    genuine convergent signal most bettors don't compute, since none of the
    three inputs alone screams "walk prop."

    NO LONGER CALLED FROM build_candidates(). Verified live against
    FanDuel's raw API (2026-08-07, every tab: batter-props, popular,
    pitching, specials, pitcher-props, innings, across two different
    games): there is no "Player to Draw a Walk" market anywhere. This
    function was generating real, scored candidates for a bet that cannot
    actually be placed. The model itself is real and fitted (see the
    weights comment below -- AUC 0.591 vs 0.576 on held-out dates, a
    genuine improvement over the hand-picked weights), so the function is
    left in place rather than deleted: if a book ever lists this market,
    reconnecting it is a one-line change in build_candidates(), not a
    rebuild."""
    bs = batter_season or {}
    bb_pct = bs.get("BB%")
    sp_bb_pct = opp_sp_row.get("BB%") if opp_sp_row else None
    if bb_pct is None and sp_bb_pct is None:
        return None  # no real signal to work with (typically FanGraphs-blocked)
    notable_signals = 0
    skill = scale(bb_pct, 6, 15) if bb_pct is not None else 50
    matchup = scale(sp_bb_pct, 6, 12) if sp_bb_pct is not None else 50
    if sp_bb_pct and sp_bb_pct >= 10: notable_signals += 1
    ump = ump_scores.get(gm["matchup"], {})
    # Lower umpire accuracy tends to mean a looser/less consistent zone -> more walks
    context = scale(ump.get("accuracy"), 96, 90) if ump.get("accuracy") else 50
    if ump.get("accuracy") and ump["accuracy"] <= 92: notable_signals += 1

    # WEIGHTS FITTED, NOT GUESSED. These were 0.4 / 0.4 / 0.2, invented. Fitted
    # by logistic regression on 5,634 backtested walk props (1,676 events, 419
    # events per parameter -- adequately powered), the batter's own walk rate
    # dominates the opposing starter's by roughly 3:1, not the 1:1 assumed:
    #
    #     batter_bb_pct   coef +0.2270   p ~ 0        sign stable in 100% of bootstraps
    #     sp_bb_pct       coef +0.0843   p = 0.0035   sign stable in  99%
    #     -> normalised weights 0.729 / 0.271
    #
    # This is the ONE market where fitted weights demonstrably beat the
    # hand-picked ones on held-out later dates: AUC 0.591 vs 0.576, with the
    # paired-bootstrap CI on the difference [0.0029, 0.0288] excluding zero.
    # On hits the same comparison came back inside the noise (CI [-0.0058,
    # 0.0442], contains zero), so those weights were deliberately left alone.
    #
    # Umpire context keeps a small share rather than being fitted: it was not
    # in the backtest's signal set, so there is no evidence either way, and
    # dropping a plausible signal on the basis of untested absence would be
    # the same mistake as keeping one on the basis of untested presence.
    score = skill * 0.66 + matchup * 0.24 + context * 0.10
    why = []
    if bb_pct is not None: why.append(f"Season BB% {bb_pct} (league ~{LEAGUE_AVG_BB_PCT}%)")
    if sp_bb_pct is not None: why.append(f"Opposing SP BB% {sp_bb_pct}")
    if ump.get("accuracy"): why.append(f"HP ump accuracy {ump['accuracy']:.1f}% (lower = looser zone)")

    signals = {}
    _sig(signals, "batter_bb_pct", bb_pct, skill)
    _sig(signals, "sp_bb_pct", sp_bb_pct, matchup)
    _sig(signals, "ump_accuracy", ump.get("accuracy"), context)
    # The most direct umpire signal anywhere in this file: this prop IS a
    # walk, and this is that umpire's measured walk rate. Accuracy was
    # standing in for it, and accuracy does not distinguish a tight zone
    # called well from a wide one called well.
    uk = (ump_kbb or {}).get(gm.get("hp_ump"))
    if uk and uk.get("bb_pct") is not None:
        _sig(signals, "ump_bb_pct", uk["bb_pct"],
             clamp((uk["bb_pct"] - uk["league_bb_pct"]) * 150, -5, 5))

    return {
        "type": "batter", "name": batter["name"], "player_id": batter.get("id"), "team": batter.get("team"),
        "matchup": gm["matchup"], "game_pk": gm.get("game_pk"), "prop": "Over 0.5 Walks",
        "projection": {"stat": "walks", "value": 0.7}, "signals": signals, "score": round(score, 1),
        # attach_hit_probabilities() prices a walk prop as
        # P(>=1 BB in projected_pa trials) and needs the trial count. Without
        # this key it read None and EVERY walks candidate shipped with a null
        # hit_probability -- found by backtest/engine.py, which prices the same
        # candidates offline and had 432 of 432 walk rows come back unpriced.
        # Since the board now RANKS by chance of cashing, an unpriced candidate
        # sorts behind everything that could be priced, so this silently
        # buried the entire prop type. Same call the other two batter scorers
        # already make.
        "projected_pa": project_batter_pa(batter.get("order"), None),
        "why": why, "watchouts": [], "notable_signals": notable_signals,
        "confidence": "High" if score >= 70 else ("Medium" if score >= 55 else "Low"),
    }


def score_first_inning(sp_name, sp_id, gm, side, fi_form, ump_env=None, park_wx=None):
    """NRFI/YRFI lean per starter, from real per-start first-inning results
    (not season aggregates) — reuses the same targeted pull mlb_daily.py's
    Section 38 already validates, in structured form.

    Bug found while verifying live: scale()'s linear extrapolation isn't
    clamped on the input side, only the output — a 0% yrfi_rate with a tight
    [15,55] input band extrapolated past 100 and got clamped there, so every
    scoreless-first-inning starter (not rare in an L14/2-start sample) tied
    at exactly 100 and swept the top 10, regardless of how thin the sample
    backing it was. Fixed with a full 0-100 input band (no extrapolation)
    plus a steep small-sample penalty and a hard confidence cap below 3
    starts — since top 10 selection is now pure score ranking with no
    per-category cap (per explicit direction: "we want the best picks," not
    forced variety), a thin 2-start sample must not be able to out-score a
    real multi-signal read just because it happened to land on 0 runs."""
    fi = fi_form.get(sp_name)
    if not fi: return None
    yrfi_rate = fi["yrfi_rate"]
    n_starts = fi["n_starts"]
    # The lineup this starter faces -- an away starter works the bottom of the
    # first against the home team, and vice versa.
    opp_team = gm["home_team"] if side == "away" else gm["away_team"]
    lean = "YRFI" if yrfi_rate >= 38 else "NRFI"
    score = scale(yrfi_rate, 0, 100) if lean == "YRFI" else scale(yrfi_rate, 100, 0)
    sample_penalty = max(0, (5 - n_starts) * 15)  # 2 starts: -45; 3: -30; 4: -15; 5+: none
    score = clamp(score - sample_penalty)
    if n_starts < 3:
        score = min(score, 55)  # a 2-start read is never more than a low/medium-confidence lean
    notable_signals = 1 if (yrfi_rate >= 55 or yrfi_rate <= 10) and n_starts >= 3 else 0
    why = [f"1st-inning runs allowed/start {fi['runs_per_1st_inning']} across {n_starts} starts (L14)",
           f"Scored on in the 1st in {yrfi_rate}% of those starts",
           f"This is the one-sided market ({opp_team} only) — not a both-teams NRFI"]
    watchouts = []
    if n_starts < 3: watchouts.append(f"Only {n_starts} starts in the L14 window — thin sample for a first-inning read")

    # fetch_umpire_run_environment's own docstring is explicit that
    # run_impact_magnitude is UNSIGNED volatility (verified live: strictly
    # positive across all 142 umpires) -- it says how much this umpire's
    # incorrect calls tend to move a game's total in EITHER direction, not
    # whether that direction is toward more or fewer 1st-inning runs. Using
    # it to push the YRFI/NRFI score would be inventing a lean this data
    # cannot support. Recorded only, same as the other signals awaiting
    # measurement -- score is untouched below.
    signals = {"yrfi_rate": round(float(yrfi_rate), 4), "fi_n_starts": float(n_starts)}
    ump_run_impact = ((ump_env or {}).get(gm["matchup"]) or {}).get("run_impact_magnitude")
    _sig(signals, "ump_run_impact", ump_run_impact,
         scale(ump_run_impact, 0.95, 2.38) if ump_run_impact is not None else None)
    # park_hr_index is already fetched for every batter prop on the slate
    # (fetch_park_weather) and was never passed into this function at all --
    # first-inning props carried 2 signals against 18+ on batter props purely
    # because nothing wired an existing input through, not because nothing
    # existed. Already a 0-100 index (park_hr_index scales itself, see
    # park_hr_index() / fetch_park_weather above), so recorded as-is rather
    # than re-scaled. Recorded only, same reasoning as ump_run_impact: this
    # is a HR-scoring index, not a validated 1st-inning read, so it must earn
    # its way into the score through measure_signals.py first.
    wx = (park_wx or {}).get(gm["matchup"]) or {}
    _sig(signals, "park_hr_index", wx.get("park_hr_index"), wx.get("park_hr_index"))

    return {
        "type": "pitcher", "name": sp_name, "player_id": sp_id,
        "team": gm["away_team"] if side == "away" else gm["home_team"], "side": side,
        "matchup": gm["matchup"], "game_pk": gm.get("game_pk"),
        # NAME THE ACTUAL MARKET. This measures runs allowed by THIS pitcher in
        # the first inning, so it is the one-sided team market ("does the
        # opposing lineup score in the 1st"), NOT the standard NRFI that books
        # list, which requires BOTH teams to be held scoreless and prices very
        # differently. Labelling it "NRFI lean" invited betting a ~75% number
        # into a ~55% market.
        "prop": (f"{opp_team} to score in the 1st" if lean == "YRFI"
                 else f"{opp_team} scoreless in the 1st"),
        "projection": {"stat": "first_inning_run", "value": yrfi_rate},
        # attach_hit_probabilities re-picks the side once the rate has been
        # shrunk, and has to be able to rebuild this label for the other side.
        "fi_opp_team": opp_team,
        "signals": signals,
        "lean": lean, "score": round(score, 1), "why": why, "watchouts": watchouts,
        "notable_signals": notable_signals,
        "confidence": "High" if score >= 70 and n_starts >= 3 else ("Medium" if score >= 55 else "Low"),
    }


# ══════════════════════════════════════════════════════════════════════════
#  CANDIDATE ASSEMBLY
# ══════════════════════════════════════════════════════════════════════════

def build_candidates(game_meta, *, extras=None, batter_lookup, pitcher_lookup, team_k_lookup,
                     park_wx, ump_scores, bullpen_scores, bullpen_quality,
                     sharp_bias, l7_form, bat_speed_trend, batter_arsenal,
                     pitcher_arsenal, sprint_speed, catcher_poptime,
                     l14_pitcher_form, fi_form, exp_k_form=None, team_k_source=None):
    """Score every prop candidate on a slate. Pure function of its inputs —
    it fetches nothing, so the caller decides what "now" means.

    Lifted out of main() unchanged so backtest/engine.py can drive the REAL
    scoring path with point-in-time inputs instead of maintaining a parallel
    copy of this loop. A backtest of a reimplementation tests the
    reimplementation, not this file. main() calls it with today's live
    fetches; the backtest calls it with tables rebuilt as of a past morning.
    Every fetch that used to sit inline above this loop stayed in main().

    An input the caller cannot reconstruct honestly is passed empty ({}), and
    each scorer already degrades to neutral on a missing signal — which is
    exactly the behaviour a backtest needs, versus silently substituting a
    present-day value."""
    candidates = []
    team_k_source = team_k_source or {}
    catcher_by_team = {}
    for gm in game_meta:
        for side, team_key in [("away_lineup", "away_team"), ("home_lineup", "home_team")]:
            for p in gm.get(side, []):
                if p.get("pos") == "C" and p.get("id"):
                    catcher_by_team[gm[team_key]] = p["id"]

    # Catcher framing is keyed by catcher id, but score_batter sees the
    # opposing TEAM. Resolve it once here, where tonight's catcher per team is
    # already known, rather than re-deriving it for every batter.
    extras = dict(extras or {})
    _framing = extras.get("framing") or {}
    # The table reports Steal% (share of taken pitches outside the zone called
    # strikes), not runs. Using the field that exists rather than the one that
    # sounded right: a missing key would have silently disabled this signal.
    extras["framing_by_team"] = {
        team: (_framing.get(cid) or {}).get("Steal%")
        for team, cid in catcher_by_team.items()
        if cid in _framing and (_framing.get(cid) or {}).get("Steal%") is not None
    }

    # Who's on base ahead of a batter (more RBI opportunity) and how much of
    # a threat hits behind him (a pitcher can't as easily pitch around him).
    # mlb_daily.py's own report (compute_lineup_context) already computes
    # this for humans to read; it never reached scoring -- the CONTEXT
    # component's lineup_context signal in score_batter only ever used the
    # batter's own slot number, never who is actually hitting around him.
    # wOBA, not raw OBP: OBP is absent from the Statcast-fallback batter_lookup
    # shape (_fg_statcast_bat_fallback's own rename dict has no "OBP" mapping,
    # only "AVG"/"xBA"/"xwOBA"/"wOBA"), so scoring on OBP would silently go
    # dark on every FanGraphs-blocked run -- the exact "computed, then
    # discarded on the fallback path" failure this project keeps finding.
    # wOBA is present under both shapes and is arguably the better metric for
    # this anyway (folds in power, not just reaching base).
    # "Ahead" deliberately does NOT wrap (the 9-hole batter has no meaningful
    # "who's on base ahead of me" read from the prior inning's leadoff man --
    # three outs reset the bases in between). "Behind" DOES wrap: a pitcher
    # deciding whether to pitch around the 9-hole batter genuinely does face
    # the leadoff man next, so the wraparound is the real baseball question,
    # not an artifact -- this matches mlb_daily.py's own report, which wraps
    # "protection" the same way.
    # RECORDED via _sig() below, in score_batter -- NOT folded into score.
    # No measured effect size exists yet for either direction; giving it real
    # weight without that would repeat the exact mistake already learned once
    # in this file (signals given weight by judgement, later found not to
    # separate hits from misses at all). Uses only batter_lookup and the
    # lineup itself, both identical in shape between the live and backtest
    # paths, so this is fully measurable by backtest/signals.py from day one
    # -- unlike several other recorded-not-weighted signals in this function
    # (pull_park_synergy, park_hand_index, bvp_ops) whose inputs only exist
    # in the live extras fetch and are structurally invisible to any
    # backtest, found auditing this same signal.
    lineup_woba = {}
    for gm in game_meta:
        for side in ("away_lineup", "home_lineup"):
            lineup = gm.get(side, [])
            n = len(lineup)
            for i, p in enumerate(lineup):
                bid = p.get("id")
                if not bid:
                    continue
                prev_p = lineup[i - 1] if i > 0 else None
                next_p = lineup[(i + 1) % n] if n > 1 else None
                prev_row = lookup_player(batter_lookup, prev_p["name"], prev_p.get("id")) if prev_p else None
                next_row = lookup_player(batter_lookup, next_p["name"], next_p.get("id")) if next_p else None
                lineup_woba[bid] = {
                    "woba_ahead": (prev_row or {}).get("wOBA"),
                    "woba_behind": (next_row or {}).get("wOBA"),
                }
    extras["lineup_woba"] = lineup_woba

    for gm in game_meta:
        opp_sp_row_for_away_batters = lookup_player(pitcher_lookup, gm["home_sp"], gm.get("home_sp_id"), {})
        opp_sp_row_for_home_batters = lookup_player(pitcher_lookup, gm["away_sp"], gm.get("away_sp_id"), {})
        wx = park_wx.get(gm["matchup"])
        away_opp_catcher_pop = catcher_poptime.get(catcher_by_team.get(gm["home_team"]))
        home_opp_catcher_pop = catcher_poptime.get(catcher_by_team.get(gm["away_team"]))
        away_opp_bullpen = bullpen_scores.get(gm["home_team"])  # away batters face the home team's pen
        home_opp_bullpen = bullpen_scores.get(gm["away_team"])
        away_opp_bullpen_quality = bullpen_quality.get(gm["home_team"])
        home_opp_bullpen_quality = bullpen_quality.get(gm["away_team"])

        for batter in gm.get("away_lineup", []):
            batter["team"] = gm["away_team"]
            bseason = lookup_player(batter_lookup, batter["name"], batter.get("id"))
            candidates.append(score_batter(batter, gm, opp_sp_row_for_away_batters, gm.get("home_sp_id"), gm.get("home_sp_hand"),
                              wx, bseason, l7_form.get(batter.get("id")), bat_speed_trend, batter_arsenal, pitcher_arsenal,
                              away_opp_bullpen, sharp_bias.get(gm["away_team"]), away_opp_bullpen_quality,
                              extras=extras))
            # score_walk() is deliberately not called -- see its own docstring:
            # no "Player to Draw a Walk" market exists on FanDuel to bet it on.
            for c in (score_stolen_base(batter, gm, away_opp_catcher_pop, sprint_speed.get(batter.get("id")), bseason,
                                        opp_cs_pct=(extras or {}).get("cs_pct_by_team", {}).get(gm["home_team"])),
                      score_laser(batter, gm, (extras or {}).get("hard_hit") or {}),
                      score_moonshot(batter, gm, (extras or {}).get("moonshot") or {})):
                if c: candidates.append(c)
        for batter in gm.get("home_lineup", []):
            batter["team"] = gm["home_team"]
            bseason = lookup_player(batter_lookup, batter["name"], batter.get("id"))
            candidates.append(score_batter(batter, gm, opp_sp_row_for_home_batters, gm.get("away_sp_id"), gm.get("away_sp_hand"),
                              wx, bseason, l7_form.get(batter.get("id")), bat_speed_trend, batter_arsenal, pitcher_arsenal,
                              home_opp_bullpen, sharp_bias.get(gm["home_team"]), home_opp_bullpen_quality,
                              extras=extras))
            # score_walk() is deliberately not called -- see its own docstring:
            # no "Player to Draw a Walk" market exists on FanDuel to bet it on.
            for c in (score_stolen_base(batter, gm, home_opp_catcher_pop, sprint_speed.get(batter.get("id")), bseason,
                                        opp_cs_pct=(extras or {}).get("cs_pct_by_team", {}).get(gm["away_team"])),
                      score_laser(batter, gm, (extras or {}).get("hard_hit") or {}),
                      score_moonshot(batter, gm, (extras or {}).get("moonshot") or {})):
                if c: candidates.append(c)

        away_pitcher_c = home_pitcher_c = None
        if gm["away_sp"] != "TBD" and gm.get("away_sp_id"):
            opp_k = team_k_lookup.get(gm["home_team"])
            opp_k_source = team_k_source.get(gm["home_team"], "team")
            if opp_k is None:
                opp_k, n = estimate_lineup_k_pct(gm.get("home_lineup", []), batter_lookup)
                opp_k_source = n
            away_pitcher_c = score_pitcher(gm["away_sp"], gm["away_sp_id"], gm.get("away_sp_hand"),
                                             gm, "away", pitcher_lookup, l14_pitcher_form,
                                             gm.get("home_lineup", []), opp_k, ump_scores, opp_k_source,
                                             exp_k_form, extras.get("ump_kbb"), extras.get("il_returns"), extras.get("callups"))
            candidates.append(away_pitcher_c)
            fi = score_first_inning(gm["away_sp"], gm["away_sp_id"], gm, "away", fi_form,
                                    extras.get("ump_env"), park_wx)
            if fi: candidates.append(fi)
            po = score_pitcher_outs(gm["away_sp"], gm["away_sp_id"], gm, "away",
                                    (extras or {}).get("pitcher_outs") or {},
                                    po_prices=(extras or {}).get("pitcher_outs_prices"))
            if po: candidates.append(po)
        if gm["home_sp"] != "TBD" and gm.get("home_sp_id"):
            opp_k = team_k_lookup.get(gm["away_team"])
            opp_k_source = team_k_source.get(gm["away_team"], "team")
            if opp_k is None:
                opp_k, n = estimate_lineup_k_pct(gm.get("away_lineup", []), batter_lookup)
                opp_k_source = n
            home_pitcher_c = score_pitcher(gm["home_sp"], gm["home_sp_id"], gm.get("home_sp_hand"),
                                             gm, "home", pitcher_lookup, l14_pitcher_form,
                                             gm.get("away_lineup", []), opp_k, ump_scores, opp_k_source,
                                             exp_k_form, extras.get("ump_kbb"), extras.get("il_returns"), extras.get("callups"))
            candidates.append(home_pitcher_c)
            fi = score_first_inning(gm["home_sp"], gm["home_sp_id"], gm, "home", fi_form,
                                    extras.get("ump_env"), park_wx)
            if fi: candidates.append(fi)
            po = score_pitcher_outs(gm["home_sp"], gm["home_sp_id"], gm, "home",
                                    (extras or {}).get("pitcher_outs") or {},
                                    po_prices=(extras or {}).get("pitcher_outs_prices"))
            if po: candidates.append(po)
        if away_pitcher_c and home_pitcher_c:
            combo = score_combined_strikeouts(gm, away_pitcher_c, home_pitcher_c,
                                              (extras or {}).get("combined_k_prices"))
            if combo: candidates.append(combo)

    # Rain risk applies to every prop type in a game equally (a postponement
    # or delay affects the batter and the pitcher and the NRFI lean alike),
    # so it's applied once here as a uniform watchout rather than threaded
    # through every score_* signature separately. Informational only — not
    # folded into score, since "check before betting" is the honest framing
    # for a probability that's still hours out and can shift either way.
    # NEGATIVE LIFT IS A REAL WATCHOUT, and it is invisible in the headline
    # percentage. A pick can carry a high chance of cashing purely because its
    # market is easy, while the model holds no positive information about the
    # player at all. Live example from 2026-08-06: the board's #1 pick was
    # Cristopher Sanchez over 3.5 strikeouts at 91.3%, which is 0.6 points
    # BELOW the league rate for a starter clearing that line -- so the number
    # that put him first was the market, not the read.
    #
    # This is structural rather than incidental, because threshold selection
    # maximises probability and the easiest line is always the one with the
    # least room for skill to show. Sanchez's own alternatives run +2.2 at
    # over 4.5 and +7.6 at over 5.5 against -0.6 at the line chosen.
    #
    # The ranking is deliberately NOT changed here: chance of cashing is the
    # stated objective and it stays the sort key. But shipping a
    # negative-lift pick without saying so presents an easy market as a
    # strong read, which is the one thing the headline number cannot
    # distinguish on its own.
    for c in candidates:
        lift = c.get("lift")
        if lift is not None and lift < 0:
            base = c.get("base_rate")
            c["watchouts"].append(
                f"No positive read here: {abs(lift)*100:.1f} pts BELOW the "
                f"{base*100:.0f}% league base rate for this market"
                if base is not None else
                f"No positive read here: {abs(lift)*100:.1f} pts below this market's base rate")
        elif lift is not None and lift < 0.02:
            c["watchouts"].append(
                "Barely above this market's base rate — the high percentage is "
                "mostly the market being easy, not a strong read on this player")

    for c in candidates:
        wx = park_wx.get(c["matchup"])
        if wx and not wx.get("dome") and (wx.get("precip_prob") or 0) >= 50:
            c["watchouts"].append(f"Rain risk tonight ({wx['precip_prob']}% precipitation probability) — game could be delayed or postponed")
    return candidates


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

def score_slate():
    """Score every candidate on tonight's slate, with full game context.

    EXTRACTED SO THAT NOTHING ELSE HAS TO RE-DERIVE THIS. value_board.py
    originally read season game-log rates directly, which left the value
    screen blind to the opposing starter, the park, the weather, the
    batting-order slot and the bullpen -- every one of which the pipeline
    already knows and feeds into score_batter(). That was not a gap in the
    system's data; it was a second code path ignoring data the system
    already had, and it is why every price disagreement looked like "the
    market knows tonight and we do not".

    Returns (candidates, context), candidates carrying calibrated
    probabilities, reliability and lift exactly as the daily board sees them.
    """
    return _build_and_score()


def build_lineup_basis(game_meta, *, observed_at):
    """The immutable record of what batting order this board actually used.

    One entry per (game, side) -- never merged, because the two sides post
    independently and one being confirmed says nothing about the other.

    `provenance` is the fact that makes PROJECTED -> CONFIRMED meaningful
    even when the nine names and their order are identical: recommendation
    eligibility depends on whether a lineup is authoritative, not merely on
    whether it turned out to be right. Two boards with the same nine hitters
    and different provenance are genuinely different boards.
    """
    out = []
    for gm in game_meta or []:
        for side in ("away", "home"):
            rows = gm.get(f"{side}_lineup") or []
            slots = []
            for r in rows:
                order = r.get("order")
                if not order:
                    continue
                slots.append({"slot": int(order),
                              "player_id": int(r["id"]) if r.get("id") else None,
                              "name": r.get("name")})
            slots.sort(key=lambda x: x["slot"])
            assumed = any(bool(r.get("assumed")) for r in rows)
            out.append({
                "game_pk": gm.get("game_pk"),
                "side": side,
                "team": gm.get(f"{side}_team"),
                "matchup": gm.get("matchup"),
                "slots": slots,
                "provenance": "assumed" if assumed else ("confirmed" if slots else "none"),
                "observed_at": observed_at,
                "source": "mlb_daily.fetch_lineups",
            })
    return out


def _build_and_score():
    """The whole scoring pass: fetch, score, price, calibrate.

    Everything up to (but not including) ranking. Both the daily board
    and the value screen consume this, so neither can drift from the
    other's idea of what a candidate is worth.
    """

    # Captured at the ACTUAL lineup fetch, not at the end of the scoring
    # pass (2026-08-28 P0). The board conflated four different clocks under
    # one generated_at: when the model ran, when lineups were observed, when
    # prices were read, and when the game was last looked at. On 2026-08-28
    # a board built at 06:31 was still labelling Sal Stewart and Pete
    # Crow-Armstrong "lineup not confirmed" nine hours after MLB posted both
    # lineups, and nothing in the payload could express that -- the only
    # timestamp available said "board built 06:31", which is true and does
    # not answer "when did anyone last LOOK at the lineups".
    lineups_observed_at = datetime.now(timezone.utc).isoformat()
    lineup_text, game_meta, player_ids = m.fetch_lineups(m.TODAY)
    # Snapshot the EXACT lineup basis the model is about to consume
    # (2026-08-28 P0 follow-up). Reconciliation previously reconstructed
    # "what lineup did we publish" from the candidate rows, which is a
    # strictly smaller thing: a prop population is a subset of a batting
    # order. A starter who generated no candidate, a scratch affecting a
    # player with no prop, an order-only change, or a projected lineup that
    # happens to become confirmed with the same nine are all invisible to a
    # candidate-derived view. Captured here, from game_meta, at the moment
    # of the fetch and before any scoring or filtering touches it.
    lineup_basis = build_lineup_basis(game_meta, observed_at=lineups_observed_at)
    if not game_meta:
        with open(PICKS_FILE, "w", encoding="utf-8") as f:
            f.write(f"# MLB Top 10 Picks — {m.TODAY}\n\nNo games found today.\n")
        with open(PICKS_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": m.TODAY, "picks": []}, f, indent=2)
        print("No games today — wrote placeholder picks files.")
        return None

    # Drop games that can no longer be bet BEFORE any scoring work happens.
    allow_started = os.environ.get("ALLOW_STARTED_GAMES") == "1"
    game_meta, not_bettable = bettable_games(game_meta, allow_started)
    if not_bettable:
        print(f"    Skipping {len(not_bettable)} game(s) already underway or final:")
        for gm in not_bettable[:6]:
            print(f"      {gm['matchup']}  ({gm.get('status')})")
        if not game_meta:
            print("Every game on the slate has already started — nothing left to bet.")
            archive_existing_picks(m.TODAY)
            return None

    archived = archive_existing_picks(m.TODAY)
    if archived:
        print(f"    Archived the previous board to {archived}")

    print(f"{len(game_meta)} bettable game(s). Pulling scoring inputs...")
    bat_season_df = m.fg_bat(m.YEAR)
    pit_season_df = m.fg_pit(m.YEAR)
    team_bat_df = m.fg_team_bat(m.YEAR)
    park_wx = fetch_park_weather(game_meta)
    ump_scores = fetch_umpire_scores(game_meta)
    bullpen_scores = fetch_bullpen_scores(game_meta, pit_season_df)
    bullpen_quality = compute_bullpen_era(pit_season_df)
    sharp_bias = fetch_public_betting_bias(game_meta)
    l7_form = fetch_l7_batter_form()
    bat_speed_trend = fetch_bat_speed_trends()
    batter_arsenal, pitcher_arsenal = fetch_pitch_type_exploits()
    sprint_speed = fetch_sprint_speed()
    catcher_poptime = fetch_catcher_poptime()

    batter_lookup = name_lookup(bat_season_df)
    pitcher_lookup = name_lookup(pit_season_df)
    team_k_lookup = {}
    team_k_source = {}
    if team_bat_df is not None and not team_bat_df.empty and "K%" in team_bat_df.columns:
        name_col = "Team" if "Team" in team_bat_df.columns else team_bat_df.columns[0]
        team_k_lookup = dict(zip(team_bat_df[name_col].astype(str), team_bat_df["K%"]))
        team_k_source = {t: "team" for t in team_k_lookup}

    # Direct request, verbatim: "why do I still see 'Opposing team K% unavailable'?"
    # Real bug, found live 2026-08-15: FanGraphs' team-batting page 403s
    # independently of (and more often than) its INDIVIDUAL batting page --
    # and when the individual page ALSO falls back to Statcast expected-stats
    # (_fg_statcast_bat_fallback in mlb_daily.py), that fallback has no K%
    # column at all. So whenever FanGraphs' individual batting data was down
    # too, batter_lookup carried no K% for ANY player, which meant
    # estimate_lineup_k_pct()'s "derive it from tonight's confirmed lineup"
    # fallback below could never fire either -- every batter it checked came
    # back K%=None, every single time, regardless of whether the lineup was
    # confirmed. mlb_sources.team_batting_table() -- a real MLB Stats API
    # team-level K%, not FanGraphs, not Statcast -- was already being fetched
    # into extras["team_bat"] purely for backtest signal measurement and
    # never consulted here, sitting unused right next to the exact gap it
    # fills. Fetched once, reused below for extras["team_bat"] too so this
    # isn't a second network call for the same data.
    import mlb_sources as _src
    mlb_team_bat_rows = _src.team_batting_table()
    for r in mlb_team_bat_rows:
        team, k = r.get("Team"), r.get("K%")
        if team and k is not None and team not in team_k_lookup:
            team_k_lookup[team] = k
            team_k_source[team] = "mlb_team"

    starter_ids = {}
    for gm in game_meta:
        if gm.get("away_sp_id"): starter_ids[gm["away_sp"]] = gm["away_sp_id"]
        if gm.get("home_sp_id"): starter_ids[gm["home_sp"]] = gm["home_sp_id"]
    l14_pitcher_form = fetch_l14_pitcher_form(starter_ids)
    fi_form = fetch_first_inning_form(starter_ids)
    # Exponentially-decayed (halflife 30d) K rate over every real start this
    # season, from real MLB game logs -- replaces the hard L14 window as the
    # source the strikeout PROBABILITY model actually consumes. See
    # mlb_sources.exp_weighted_pitcher_k_rate's docstring for the out-of-
    # sample validation: the hard window it replaces measured WORSE than a
    # flat league-average K rate (RMSE 0.0993 vs 0.0807 on 147 real held-out
    # starts), while this measured statistically tied with the pitcher's own
    # flat season rate (0.0684 vs 0.0673) and beat every hard window tested.
    import odds_fanduel as _fd_early
    exp_k_form = _src.exp_weighted_pitcher_k_rate(list(starter_ids.values()))

    # ── Capabilities that existed but never reached a pick ────────────────
    #
    # An audit found 22 of 27 functions in mlb_sources.py were feeding the
    # readable report and nothing else. These six have a clear mechanism and
    # no existing proxy in scoring, so they are fetched here and threaded into
    # score_batter. Each is recorded in the candidate's `signals` dict so
    # backtest/signals.py can measure whether it actually separates hits from
    # misses -- adding an input is not the same as trusting it.
    #
    # platoon_quality_of_contact deserves a specific note. The existing platoon
    # signal measured AUC 0.500 on 5,077 backtested rows, i.e. pure noise, and
    # that was nearly taken as evidence that platoon does not matter. It is far
    # more likely evidence that a binary left-versus-right FLAG is too crude to
    # carry the effect. This measures exit velocity and barrel rate BY
    # handedness, which is the same concept measured properly, and it is the
    # test of whether the earlier null result was about baseball or about our
    # implementation.
    extras = {}
    for name, fn in (
        ("bvp", lambda: _src.bvp_table(game_meta)),
        ("platoon_qoc", lambda: _src.platoon_quality_of_contact()),
        ("park_hand", lambda: _src.park_hand_factors()),
        ("framing", lambda: _src.catcher_framing()),
        ("rest", lambda: _src.rest_and_usage(game_meta)),
        ("hard_hit", lambda: _src.hard_hit_game_rates()),
        # "To Hit a Moonshot (420+ FT)" -- a real market found live
        # 2026-08-14 from the user's own FanDuel screenshot, matched
        # exactly against a live API pull for the same slate/players. Same
        # shrunk-per-game-rate shape as hard_hit above; see score_moonshot's
        # own docstring for why it needed a new mlb_sources function rather
        # than reusing hard_hit_game_rates directly (a different Statcast
        # column, and the market only pays on an actual home run, not any
        # long batted ball).
        ("moonshot", lambda: _src.moonshot_rates()),
        ("pitcher_outs", lambda: _src.empirical_pitcher_outs_rates(
            [gm.get("away_sp_id") for gm in game_meta if gm.get("away_sp_id")] +
            [gm.get("home_sp_id") for gm in game_meta if gm.get("home_sp_id")])),
        # Fetched here, BEFORE scoring, so score_pitcher_outs can price the
        # real posted line directly instead of self-selecting a threshold
        # via _pick_line -- see that function's docstring for the mismatch
        # this fixes (recommending "Over 11.5 Outs" for a pitcher FanDuel
        # actually lines at 17.5). Reused below at the later attach_market_prices
        # call instead of fetching the same market twice.
        ("pitcher_outs_prices", lambda: _fd_early.fetch_pitcher_outs()),
        # SAME bug, SAME fix, for the standard strikeouts market. Found live
        # 2026-08-13: attach_market_prices' "strikeouts" branch only prices a
        # candidate when the model's chosen `needs` happens to equal
        # FanDuel's real posted line's `needs` -- but attach_hit_probabilities
        # picked that threshold via _pick_line (pure model probability/lift
        # among t in 4..8), never checking what FanDuel actually offers.
        # Real slate: k_prices had a genuine line for every one of 6
        # starters, yet only 1 matched. Fetched here so
        # attach_hit_probabilities can prefer the real line's needs the same
        # way score_pitcher_outs already does for pitcher_outs.
        ("strikeout_prices", lambda: _fd_early.fetch_pitcher_strikeouts()),
        # Starting Pitcher Combined Alt Strikeouts -- see score_combined_
        # strikeouts's own docstring. A real, priced ladder market with no
        # scorer until now, found sitting next to Pitcher Outs Recorded on
        # the exact same tab.
        ("combined_k_prices", lambda: _fd_early.fetch_combined_pitcher_strikeouts()),
        # Second batch, each verified against its real structure before use.
        ("team_field", lambda: _src.team_fielding_table()),
        # Reuses the same call already made above for team_k_lookup's
        # mlb_team fallback tier -- not fetched twice for the same data.
        ("team_bat", lambda: mlb_team_bat_rows),
        ("pull", lambda: _src.pull_rates()),
        ("pitch_q", lambda: _src.pitch_quality()),
        # Real IL activations (returns) in the last 21 days, via MLB's
        # transactions endpoint. Nothing else in this pipeline knows a
        # batter or pitcher is fresh off the injured list -- the injury
        # report only ever shows who's currently OUT. Surfaced as a
        # watchout on the affected candidate (see score_batter/score_pitcher),
        # not a scored signal: no measured effect size exists for how long
        # a return-from-IL dip actually lasts in this league, and inventing
        # one would be exactly the "plausible-looking number that isn't
        # real" this project exists to avoid.
        ("il_returns", lambda: _src.fetch_recent_il_returns()),
        # Same "fresh, uncertain track record" theme, a different cause: a
        # recent call-up from the minors (rookie debut, September call-up,
        # or optioned-and-back) has little or no MLB track record of his
        # own behind whatever season/rolling stat this pipeline shows for
        # him. Also informational only -- see fetch_recent_callups' own
        # docstring.
        ("callups", lambda: _src.fetch_recent_callups()),
        # The one input in this whole project that cannot be re-fetched later.
        # odds_snapshot.py has been writing hourly captures since the start
        # precisely so this would exist, and nothing has ever read them.
        ("line_move", lambda: _src.line_movement()),
        # Umpire K%/BB%, which no source publishes — built from the schedule's
        # officials hydrate joined to season Statcast. Reuses the cached
        # season pull, so it costs one extra HTTP request, not a download.
        ("ump_kbb", lambda: _src.umpire_k_bb_rates()),
        # Batter vs starters versus batter vs relievers. See the signal in
        # score_batter for why this is not a niche split.
        ("sp_rp", lambda: _src.batter_sp_rp_splits(game_meta)),
        # HP umpire run-environment volatility, wired into score_first_inning.
        # fetch_umpire_run_environment's own docstring is explicit that
        # run_impact_magnitude is unsigned (verified live: positive across
        # all 142 umpires) -- a volatility measure, not an over/under lean --
        # so it is recorded via _sig for future measurement and does not
        # touch the YRFI/NRFI score.
        ("ump_env", lambda: _src.fetch_umpire_run_environment(game_meta)),
    ):
        try:
            # NOT `fn() or {}`: several of these return DataFrames, and the
            # truthiness of a DataFrame raises rather than being falsy. That
            # exception was being swallowed by the handler below, silently
            # leaving pitch_quality empty on every run.
            got = fn()
            extras[name] = {} if got is None else got
        except Exception as e:
            m.warn(f"{name} unavailable ({e}) — scoring continues without it")
            extras[name] = {}
    # Both team tables arrive as lists of rows with SOME RATES AS STRINGS --
    # CS% comes back as '.267', not 0.267. Converting here rather than at each
    # use site, because a string silently compares false against every numeric
    # threshold and the signal would simply never fire.
    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    extras["bvp_by_pair"] = {(r.get("Batter"), r.get("Pitcher")): r
                             for r in (extras.get("bvp") or [])
                             if r.get("Batter") and r.get("Pitcher")}
    # batter_sp_rp_splits returns a LIST of rows keyed by 'id', not an id map
    # — the same shape that crashed a run when bvp_table was assumed to be a
    # dict. Re-keyed once here rather than at the use site.
    extras["sp_rp_by_id"] = {r["id"]: r for r in (extras.get("sp_rp") or [])
                             if r.get("id")}
    extras["cs_pct_by_team"] = {r["Team"]: _f(r.get("CS%"))
                                for r in (extras.get("team_field") or [])
                                if _f(r.get("CS%")) is not None}
    extras["team_k_pct"] = {r["Team"]: _f(r.get("K%"))
                            for r in (extras.get("team_bat") or [])
                            if _f(r.get("K%")) is not None}
    print("    Extra signals: " + ", ".join(
        f"{k} {len(v)}" for k, v in extras.items() if hasattr(v, "__len__")))

    candidates = build_candidates(
        game_meta, extras=extras,
        batter_lookup=batter_lookup, pitcher_lookup=pitcher_lookup,
        team_k_lookup=team_k_lookup, team_k_source=team_k_source, park_wx=park_wx, ump_scores=ump_scores,
        bullpen_scores=bullpen_scores, bullpen_quality=bullpen_quality,
        sharp_bias=sharp_bias, l7_form=l7_form, bat_speed_trend=bat_speed_trend,
        batter_arsenal=batter_arsenal, pitcher_arsenal=pitcher_arsenal,
        sprint_speed=sprint_speed, catcher_poptime=catcher_poptime,
        l14_pitcher_form=l14_pitcher_form, fi_form=fi_form, exp_k_form=exp_k_form)

    # Pure score ranking, no per-game or per-prop-type cap — per explicit
    # direction, the top 10 doesn't have to be diverse across categories or
    # games; forcing variety just to have variety would mean swapping out a
    # genuinely better pick for a worse one, which is the opposite of the
    # goal. If the 10 best-scoring picks all happen to be the same prop type
    # or the same game, that's what goes out. (This is why score_first_inning
    # carries a hard confidence cap on thin samples — with no cap here to
    # catch it, an inflated score from a weak signal would otherwise be free
    # to sweep the list on its own.)
    # ── Chance of cashing ────────────────────────────────────────────────
    # Fetched here rather than up front because both tables only need the
    # players who actually produced a candidate, which is a fraction of the
    # slate. Measured: 6 batters in 0.8s, so a full board is a few seconds.
    comp_table, emp_batters, emp_pitchers, league_rates = {}, {}, {}, {}
    try:
        import mlb_sources as _src
        comp_table = _src.batter_pa_composition()
        bat_ids = [c["player_id"] for c in candidates
                   if c.get("type") == "batter" and c.get("player_id")]
        pit_ids = [c["player_id"] for c in candidates
                   if c.get("type") == "pitcher" and c.get("player_id")]
        emp_batters = _src.empirical_batter_prop_rates(bat_ids)
        emp_pitchers = _src.empirical_pitcher_k_rates(pit_ids)
        league_rates = _src.league_base_rates(window_days=_src.LEAGUE_RATE_WINDOW_DAYS)
        if league_rates:
            print(f"    League base rates from {league_rates.get('_n_starts', 0)} starts / "
                  f"{league_rates.get('_n_batter_games', 0)} batter-games "
                  f"(K>=4 {league_rates.get('strikeouts_4plus')}, "
                  f"1+H {league_rates.get('hits_1plus')})")
        print(f"    Hit-probability inputs: {len(comp_table)} batter rate lines, "
              f"{len(emp_batters)} batter game logs, {len(emp_pitchers)} pitcher game logs")
    except Exception as e:
        m.warn(f"Hit-probability inputs unavailable ({e}) — "
               f"falling back to score-only ranking")

    attach_hit_probabilities(candidates, comp_table, emp_batters, emp_pitchers,
                             league_rates, k_prices=extras.get("strikeout_prices"))
    # Calibrate BEFORE ranking and before the positive-read floor, so both
    # operate on the honest number rather than the overstated one.
    apply_calibration(candidates, load_calibrator())
    attach_reliability(candidates, emp_batters, emp_pitchers)
    return candidates, {
        "game_meta": game_meta, "park_wx": park_wx,
        "lineups_observed_at": lineups_observed_at,
        "lineup_basis": lineup_basis,
        "bullpen_scores": bullpen_scores, "emp_batters": emp_batters,
        "emp_pitchers": emp_pitchers, "comp_table": comp_table,
        "league_rates": league_rates,
        # Fetched above so score_pitcher_outs could price the real line
        # directly; carried through so main()'s later attach_market_prices
        # pass can reuse it instead of sweeping FanDuel for the same market
        # twice (extras itself is local to this function).
        # Raw rest/usage, carried through for the PA-v1 historical-semantics
        # compatibility adapter (backtest/pa_v1_compat.py). PURELY ADDITIVE:
        # nothing in the scoring or recommendation path reads this key, and
        # the candidates' own signals are untouched. It is exposed because the
        # STORED days_rest signal is clamped and therefore not invertible, so
        # the raw days_since_last_game is required to reconstruct the frozen
        # artifact's own reference-clock semantics.
        "rest": extras.get("rest"),
        "po_prices": extras.get("pitcher_outs_prices"),
        # Same reasoning, same fetch-above-reuse-below pattern -- this one
        # was missing until attach_market_prices grew a combined_strikeouts
        # branch (added this pass; see its own docstring). Before that,
        # main()'s attach_market_prices call had no use for this key, so its
        # absence here was invisible.
        "combined_k_prices": extras.get("combined_k_prices"),
        # Same fetch-above-reuse-below pattern as po_prices/combined_k_prices,
        # now that attach_hit_probabilities' strikeouts branch needs the real
        # line to pick against (see strikeout_prices' own comment above).
        "k_prices": extras.get("strikeout_prices"),
        # Per-umpire K%/BB% tendency (see score_batter's own ump_kbb usage
        # above) -- not previously carried through ctx since nothing outside
        # scoring needed it. dashboard/build_dashboard.py's game-schedule
        # breakdown needs it to explain WHY a game's props lean a certain
        # way, not just score them with it.
        "ump_kbb": extras.get("ump_kbb"),
    }


# How much of a real HR-specific edge over the LEAGUE home-run base rate (NOT
# this player's own rate -- see base_rate's real definition, "his own" was
# corrected 2026-08-2X as part of the HR probability/base-rate semantics
# trace) a moonshot pick needs to earn "High" confidence. Direct request:
# "does the math support it? Or is it just because we have an edge?" --
# found live 2026-08-15 that select_moonshots() was labeling picks High off
# the batter's HITS/TOTAL-BASES confidence (his primary, most-likely
# projection), reused wholesale for the home-run entry even though nothing
# about it was computed for home runs specifically. A real Gleyber Torres
# case: 11.07% HR probability vs a 10.34% league base rate -- a 0.73-point
# lift, indistinguishable from noise -- carried a borrowed "High" tag with
# an empty why/reliability/sample_n, because none of that was ever computed
# for the HR read either. 3 points is a real, not-noise elevation for a
# single-digit-percent event (a double-digit relative lift over the league
# base rate), not an arbitrary round number picked to look scientific.
MOONSHOT_LOCK_LIFT = 0.03


def select_moonshots(candidates, prices, fd, n=5):
    """Best home-run bets by hit probability, priced against FanDuel's real
    line, for batters who already cleared quality_control but whose HR
    number never wins _pick_line -- hits and total bases are always more
    likely than a home run, so HR never becomes a batter's CHOSEN
    projection under the existing selection rule. It survives only in
    `line_options`, the full probability curve _keep_options preserves
    specifically so nothing computed gets thrown away. Ranked by hit
    probability, same principle as the main board -- "best chance of
    cashing," just drawn from the pool the floor excludes rather than the
    pool it allows.

    Deliberately OUTSIDE MIN_LINE_PROB. That floor (0.60) is what makes
    home runs and 2+ total bases unrecommendable on the main board -- 1+ HR
    runs 10-25%, 2+ TB ~40%, both permanently below it -- and that is the
    floor working as specified, not a bug. Raised as a real tension against
    liking HR bets and confirmed: a separate always-longshot category, not
    a lowered floor. This does not touch MIN_LINE_PROB or anything that
    feeds the main board -- only the quality gate (MIN_QUALITY_SCORE)
    still applies, same floor every other candidate has to clear.

    confidence/reliability/sample_n are computed HERE, independently of the
    batter's own (hits/total-bases) versions of those same fields -- see
    MOONSHOT_LOCK_LIFT's own comment for the real bug this replaces.
    reliability/sample_n still describe how much real MLB track record this
    player has (a legitimate, real thing to carry over -- it says nothing
    about home runs specifically, just how trustworthy ANY read on him is),
    and confidence additionally requires a real HR-specific lift.

    why/watchouts: real bug, found 2026-08-26 (market-specific-explanation
    audit, direct complaint -- a home-run detail view showed probability vs.
    league base rate and almost nothing about why that day was a favorable
    HR spot). This used to build its own single-sentence why from scratch,
    restating hr_opt['prob'] vs base_rate as a paragraph -- a plain
    restatement of hit_probability/base_rate/lift, all three of which are
    ALREADY on this row as their own fields (see below), not new information.
    Meanwhile it discarded the real batter-level evidence (platoon,
    opposing-SP quality, pitch-type exploit, recent/season power profile,
    park/weather/wind, bullpen, lineup slot) c["why"]/c["watchouts"] already
    computed one call up in score_batter() -- exactly the "computed, then
    discarded" failure this codebase keeps finding and fixing elsewhere.
    Reused directly here instead: dashboard/build_dashboard.py's
    _select_market_evidence() (structured evidence contract, Part 2 item 4)
    filters/reorders this same real list for the home_runs market
    specifically at the public-payload boundary, so nothing here needs its
    own HR-flavored copy of the same facts."""
    out = []
    for c in candidates:
        if c.get("type") != "batter":
            continue
        hr_opt = next((o for o in (c.get("line_options") or [])
                      if o.get("stat") == "home_runs" and o.get("needs") == 1), None)
        if not hr_opt or hr_opt.get("prob") is None:
            continue
        if c.get("score", 0) < MIN_QUALITY_SCORE:
            continue
        odds = (prices.get(fd.normalize_name(c.get("name"))) or {}).get(("home_runs", 1))
        implied = round(pp.implied_probability(odds), 4) if odds is not None else None
        lift = hr_opt.get("lift")
        reliability = c.get("reliability")
        # Half of MOONSHOT_LOCK_LIFT, not a fresh made-up number: any
        # positive lift at all (even 0.1 of a point, indistinguishable from
        # rounding noise) originally qualified for Medium, which is its own
        # version of the exact bug this whole fix exists to close -- a
        # label implying real support for a read that's actually just
        # noise around zero.
        if reliability in ("A", "B") and lift is not None and lift >= MOONSHOT_LOCK_LIFT:
            confidence = "High"
        elif reliability in ("A", "B", "C") and lift is not None and lift >= MOONSHOT_LOCK_LIFT / 2:
            confidence = "Medium"
        else:
            confidence = "Low"
        base_rate = hr_opt.get("base_rate")
        # 2026-08-2X data-integrity fix (HR probability/base-rate semantics
        # trace), still true: base_rate here is true_league_rates' league-
        # wide home_runs_1plus rate (or a slate-scoped fallback), NOT this
        # player's own rate -- hr_opt['prob'] is the one number that's
        # actually his. That comparison is real and stays on the row via
        # base_rate/lift/hit_probability/probability_basis below; it no
        # longer needs its own sentence duplicated into `why` (see this
        # function's own docstring for why -- the Evidence section already
        # renders it from those fields).
        why = list(c.get("why") or [])
        watchouts = list(c.get("watchouts") or [])
        # Full candidate shape, not a stripped-down dict -- write_json appends
        # these into the same `picks` list grade_results.py already knows how
        # to grade (it reads pick["type"], pick["projection"]["stat"]/["needs"]
        # by direct access), so the moonshot category gets tracked over time
        # through the exact same proven grading path instead of a second one.
        out.append({
            "type": "batter", "name": c["name"], "player_id": c.get("player_id"),
            "team": c.get("team"), "matchup": c.get("matchup"), "game_pk": c.get("game_pk"),
            "side": c.get("side"), "prop": "Home Run",
            "projection": {"stat": "home_runs", "value": 1, "needs": 1},
            "lean": None, "score": c.get("score"), "confidence": confidence,
            "notable_signals": c.get("notable_signals", 0),
            "hit_probability": hr_opt["prob"],
            "signals": c.get("signals") or {},
            "base_rate": base_rate, "lift": lift,
            "probability_basis": hr_opt.get("basis"),
            "probability_detail": {"empirical": hr_opt.get("empirical"), "modelled": hr_opt.get("modelled")},
            "market_odds": odds, "market_implied": implied,
            "market_edge": None if implied is None else round(hr_opt["prob"] - implied, 4),
            # market-edge-semantics fix (P0-6): one-sided market (home run
            # yes/no), never de-vigged -- same honesty fix as
            # odds_fanduel.attach_market_prices' own generic branch.
            "posted_implied": implied,
            "market_fair": None if implied is None else round(pp.devig(implied), 4),
            "market_fair_method": None if implied is None else "assumed_hold",
            "edge_vs_fair": (None if implied is None
                              else round(hr_opt["prob"] - pp.devig(implied), 4)),
            "price_clears": pp.price_is_acceptable(odds, hr_opt["prob"]),
            "category": "moonshot",
            "lineup_assumed": c.get("lineup_assumed"),
            "reliability": reliability, "sample_n": c.get("sample_n"),
            "why": why, "watchouts": watchouts,
        })
    out.sort(key=lambda o: o["hit_probability"], reverse=True)
    return out[:n]


def select_deep_moonshots(candidates, prices, fd, n=5):
    """Best "To Hit a Moonshot (420+ FT)" bets by hit probability, priced
    against FanDuel's real line -- built 2026-08-14 alongside score_moonshot,
    found live from the user's own FanDuel screenshot. A SEPARATE section
    from select_moonshots on purpose: that one is about hitting a home run
    AT ALL, this one is specifically about distance, and merging the two
    under one heading would blur a real distinction a bettor is paying to
    see (Joc Pederson at 17.4% to homer at all is a very different bet from
    Joc Pederson at 8% to hit one 420+ feet).

    NO MIN_QUALITY_SCORE gate, same reasoning select_moonshots gives for
    skipping it: score_moonshot's own `score` is already scaled off this
    exact rare probability (real per-game rates run 2-11%, verified live,
    similar rarity to hard_hit_110), so a quality floor built for the
    5-category batter formula would exclude every real candidate here, not
    just weak ones. Unlike select_moonshots, there is no line_options detour
    needed -- score_moonshot already creates its own standalone candidate,
    so this selects directly from `candidates`."""
    out = []
    for c in candidates:
        if c.get("type") != "batter":
            continue
        proj = c.get("projection") or {}
        if proj.get("stat") != f"moonshot_{MOONSHOT_THRESHOLD_FT}":
            continue
        if c.get("hit_probability") is None:
            continue
        odds = (prices.get(fd.normalize_name(c.get("name"))) or {}).get(
            (f"moonshot_{MOONSHOT_THRESHOLD_FT}", 1))
        implied = round(pp.implied_probability(odds), 4) if odds is not None else None
        row = dict(c)
        row["market_odds"] = odds
        row["market_implied"] = implied
        row["market_edge"] = None if implied is None else round(c["hit_probability"] - implied, 4)
        # market-edge-semantics fix (P0-6): one-sided market, never de-vigged.
        row["posted_implied"] = implied
        row["market_fair"] = None if implied is None else round(pp.devig(implied), 4)
        row["market_fair_method"] = None if implied is None else "assumed_hold"
        row["edge_vs_fair"] = (None if implied is None
                                else round(c["hit_probability"] - pp.devig(implied), 4))
        row["price_clears"] = pp.price_is_acceptable(odds, c["hit_probability"])
        row["category"] = "moonshot_420"
        out.append(row)
    out.sort(key=lambda o: o["hit_probability"], reverse=True)
    return out[:n]


# Every prop family this board can price, and the display label for each.
# home_runs WAS deliberately absent at one point (select_moonshots() already
# owns an HR category at n=5 -- "Moonshots" -- and listing it again here
# looked like the same players under a second heading), but that left home
# runs "structurally unable to appear here at all" (this function's own
# docstring names that as the exact failure it exists to prevent) for any
# batter outside select_moonshots' capped top-5-by-probability list -- a
# real silently-discarded-candidate bug, not a cosmetic one. Restored below.
# The two populations can legitimately show the SAME player with the SAME
# probability -- that is not a duplicate to dedupe away, since Moonshots
# (capped, ranked by raw probability across the whole slate) and this
# by-category board (uncapped, one best-of-family per player) answer
# different real questions ("best HR bets tonight" vs "this player's best
# read in every family"). What must NOT differ between them is the
# CONFIDENCE label for the identical read -- see the home_runs-specific
# confidence computation right below, which reuses select_moonshots' own
# rule so the two populations never disagree about the same number.
# "walks" and "first_inning_run" (the one-sided per-pitcher read) are
# deliberately absent -- verified live against FanDuel's raw API that
# neither corresponds to a real, bettable market (see score_walk's and
# _build_combined_nrfi's docstrings). first_inning_run still runs
# internally as nrfi_combined's input; it just never becomes a candidate
# a customer could see as a standalone pick.
# "home_runs" was missing here until this audit -- found by diffing this
# dict against render_board.py's and render_full_board.py's own copies
# (three independent copies of the same table, a drift risk by construction).
# The impact was real, not cosmetic: select_best_by_category's own docstring
# names home runs as the exact example of a family this function exists to
# stop from being structurally excluded ("the floor is what makes an entire
# family (home runs, 2+ total bases) structurally unable to appear here at
# all"), and a missing dict key did precisely that, silently, the whole time
# home_runs has existed as its own market.
CATEGORY_LABELS = {
    "hits": "Hits", "total_bases": "Total Bases", "home_runs": "Home Runs",
    "runs": "Runs", "rbis": "RBIs", "hits_runs_rbis": "Hits+Runs+RBIs",
    "singles": "Singles", "doubles": "Doubles", "triples": "Triples",
    "stolen_base": "Stolen Base", "strikeouts": "Strikeouts",
    "nrfi_combined": "NRFI/YRFI (Both Teams)",
    "hard_hit_105": "Laser (105+ MPH)", "hard_hit_110": "Laser (110+ MPH)",
    "pitcher_outs": "Pitcher Outs Recorded",
    "combined_strikeouts": "Combined Starter Strikeouts",
}


def select_best_by_category(candidates, prices, fd, n_per_category=1, k_prices=None, min_score=None):
    """The single best (by hit probability) candidate in EVERY prop family
    the pipeline can price, not just whichever ones happened to win the
    main board's overall ranking. Direct request: "the best available of
    EACH and EVERY prop type."

    Two different shapes of input, handled separately:

    - hits/total_bases/runs/rbis/hits_runs_rbis/singles/doubles/triples all
      live inside `line_options` -- the whole probability curve
      _keep_options preserves per batter, most of which a batter's own
      `projection` never becomes (hits/total_bases almost always beat the
      other seven on raw probability, same reason home_runs never wins
      _pick_line either -- see select_moonshots). Re-run _pick_line PER
      PLAYER PER FAMILY here so "this player's best total-bases line" is
      chosen the same way the main board chooses anything, then rank
      across players.
    - stolen_base/strikeouts/nrfi_combined are never re-priced at multiple
      thresholds -- each candidate already IS the one number for that
      player (or game, for nrfi_combined), so it's used directly.

    min_score defaults to MIN_QUALITY_SCORE (same floor as everything else
    on this board), and deliberately NO MIN_LINE_PROB floor -- the score
    floor is what makes an entire family (home runs, 2+ total bases)
    structurally unable to appear here at all some nights. A pick below
    that floor is still labelled with its real probability; nothing is
    hidden, only ranked honestly against however good the category's own
    ceiling is tonight.

    Pass min_score=0 for the companion "never silently drop a category"
    shadow-tracking call (see select_shadow_tracking / main()) -- direct
    request: "we should always track every prop to know if one sticks out
    randomly for some reason... We can't just throw them away." A category
    with zero candidates clearing MIN_QUALITY_SCORE tonight still deserves
    its best-available entry recorded and graded, even though it would
    never have been good enough to ship on the real card."""
    floor = MIN_QUALITY_SCORE if min_score is None else min_score
    by_category = defaultdict(list)
    for c in candidates:
        if c.get("score", 0) < floor:
            continue
        if c.get("type") == "batter" and c.get("line_options"):
            stats_here = {o["stat"] for o in c["line_options"] if o.get("stat") in CATEGORY_LABELS}
            for stat in stats_here:
                fam_opts = [o for o in c["line_options"] if o.get("stat") == stat]
                best = _pick_line(fam_opts)
                if not best or best.get("prob") is None:
                    continue
                # _keep_options' trimmed shape (stat/needs/line/prob/base_rate/
                # lift/basis only -- verified live, no "label"/"empirical"/
                # "modelled" keys here, unlike the richer dicts _batter_options
                # builds before trimming) is what line_options actually holds.
                # Assumed richer once, live run threw KeyError('label') on the
                # first real slate -- rebuilt the label from stat+line instead
                # of trusting the assumption.
                # Reliability/sample_n/prob_ci are PLAYER-level (from
                # emp_batters, keyed by player id) so they hold regardless of
                # which of his lines got picked here -- carried over.
                # raw_hit_probability/calibrated_by now describe THIS exact
                # alternate line's OWN calibration pass (apply_calibration()
                # calibrates every line_options entry against its own stat,
                # not just the primary hit_probability -- see that function's
                # docstring for the fix). best["prob"] is already the
                # calibrated value when a real calibrator applied; when none
                # did (no per-market fit and no pooled fallback), best["prob"]
                # is honestly still the raw one and raw_hit_probability/
                # calibrated_by are correctly absent below, never invented.
                # HR/moonshot population-consistency fix (P0-8 data-integrity
                # audit): home_runs is the one family here where c.get(
                # "confidence") is actively WRONG, not just imprecise --
                # verified with real code execution: the identical batter/
                # probability/lift/reliability run through select_moonshots()
                # (the dedicated, MOONSHOT_LOCK_LIFT-based HR confidence
                # rule -- see its own docstring for the real Gleyber Torres
                # bug this rule was built to fix) produced "High", while this
                # function's generic c.get("confidence") -- the batter's
                # OVERALL score-derived label, dominated by his hits/total-
                # bases read, unrelated to his HR-specific lift/reliability --
                # produced "Low" for the exact same read. Two structurally
                # different populations of "home_runs" candidates
                # (select_moonshots' n=5-capped Moonshots list, and this
                # function's uncapped by-category list) must not disagree
                # about the same player's same number. Reuses the identical
                # rule, not a new one.
                confidence = c.get("confidence")
                if stat == "home_runs":
                    hr_lift = best.get("lift")
                    hr_rel = c.get("reliability")
                    if hr_rel in ("A", "B") and hr_lift is not None and hr_lift >= MOONSHOT_LOCK_LIFT:
                        confidence = "High"
                    elif hr_rel in ("A", "B", "C") and hr_lift is not None and hr_lift >= MOONSHOT_LOCK_LIFT / 2:
                        confidence = "Medium"
                    else:
                        confidence = "Low"
                by_category[stat].append({
                    "type": "batter", "name": c["name"], "player_id": c.get("player_id"),
                    "team": c.get("team"), "matchup": c.get("matchup"), "game_pk": c.get("game_pk"),
                    "side": c.get("side"), "prop": f"Over {best['line']} {CATEGORY_LABELS.get(stat, stat)}",
                    "projection": {"stat": stat, "value": best["line"], "needs": best["needs"]},
                    "lean": None, "score": c.get("score"), "confidence": confidence,
                    "notable_signals": c.get("notable_signals", 0),
                    "hit_probability": best["prob"], "signals": c.get("signals") or {},
                    "base_rate": best.get("base_rate"), "lift": best.get("lift"),
                    "probability_basis": best.get("basis"),
                    "raw_hit_probability": best.get("raw_prob"),
                    "calibrated_by": best.get("calibrated_by"),
                    "probability_detail": {"empirical": None, "modelled": None},
                    # THE FIX: best["ci"] (this exact stat+threshold's own
                    # interval, or honestly None when no defensible one
                    # exists -- see _batter_options), never c["prob_ci"]
                    # (the candidate's PRIMARY line's interval, which is a
                    # different stat/threshold whenever this family isn't
                    # that primary line). sample_n/reliability stay
                    # player-level on purpose -- how much real MLB track
                    # record exists for this player at all is a fact that
                    # doesn't change per stat family, unlike a specific
                    # probability's own interval.
                    "prob_ci": best.get("ci"),
                    # CI-provenance-honesty fix (P0-7): same "computed, then
                    # discarded" boundary as prob_ci itself was fixed for --
                    # ci_source (player_empirical vs historical_reliability_
                    # band, see _batter_options/select_best_by_category's own
                    # comments) was always computed alongside prob_ci but
                    # never carried into this dict.
                    "prob_ci_source": best.get("ci_source"),
                    "sample_n": c.get("sample_n"),
                    "reliability": c.get("reliability"),
                    "why": c.get("why"), "watchouts": c.get("watchouts"),
                    # Real bug, found live 2026-08-15: this fixed field list
                    # silently dropped lineup_assumed, the exact tag
                    # quality_control() sets on an assumed-lineup candidate --
                    # a batter whose slot is a guess would come out of here
                    # looking identical to a fully confirmed one, with no way
                    # for a caller (the dashboard) to tell them apart. Absent
                    # (None) for every candidate that never had it set, which
                    # is every candidate the static/graded pipeline ever
                    # passes here -- inert there.
                    "lineup_assumed": c.get("lineup_assumed"),
                    "_needs_price_lookup": True,
                })
        elif c.get("type") in ("batter", "pitcher", "game", "pitcher_combo"):
            stat = (c.get("projection") or {}).get("stat")
            if stat not in CATEGORY_LABELS or c.get("hit_probability") is None:
                continue
            # This IS the exact same line c was already priced on (single-line
            # families never get re-priced at an alternate threshold, unlike
            # the line_options branch above) -- reuse attach_market_prices()'s
            # own result directly instead of recomputing it from a one-sided
            # feed that doesn't even cover strikeouts. Real bug found live:
            # this recompute used to run unconditionally against `prices`
            # only, so every strikeout candidate here showed market_odds=null
            # even when odds_fanduel.attach_market_prices had already found a
            # real two-sided price on `c` moments earlier in the same run.
            by_category[stat].append({
                "type": c["type"], "name": c["name"], "player_id": c.get("player_id"),
                # 2026-08-24 combined-strikeouts settlement investigation:
                # real bug, found live via 28/28 combined_strikeouts rows in
                # results/grades_*.json permanently stuck "ungraded: missing
                # combo_player_ids" -- this fixed field list silently dropped
                # combo_player_ids, the one field grade_pick()'s
                # combined_strikeouts branch requires to settle from two
                # starters' box scores. Every pitcher_combo candidate reaching
                # the board through this branch (the by-category/dashboard
                # path, not the primary top10 list) lost its combo identity
                # here and could never be graded -- same "computed, then
                # discarded" failure class as lineup_assumed below.
                "combo_player_ids": c.get("combo_player_ids"),
                "team": c.get("team"), "matchup": c.get("matchup"), "game_pk": c.get("game_pk"),
                "side": c.get("side"), "prop": c.get("prop"),
                "projection": c.get("projection"),
                "lean": c.get("lean"), "score": c.get("score"), "confidence": c.get("confidence"),
                "notable_signals": c.get("notable_signals", 0),
                "hit_probability": c["hit_probability"], "signals": c.get("signals") or {},
                "base_rate": c.get("base_rate"), "lift": c.get("lift"),
                "probability_basis": c.get("probability_basis"),
                "probability_detail": c.get("probability_detail"),
                "raw_hit_probability": c.get("raw_hit_probability"),
                "calibrated_by": c.get("calibrated_by"),
                "prob_ci": c.get("prob_ci"), "prob_ci_source": c.get("prob_ci_source"),
                "sample_n": c.get("sample_n"),
                "reliability": c.get("reliability"), "alternatives": c.get("alternatives"),
                "why": c.get("why"), "watchouts": c.get("watchouts"),
                "market_odds": c.get("market_odds"), "market_implied": c.get("market_implied"),
                "market_edge": c.get("market_edge"), "price_clears": c.get("price_clears"),
                # market-edge-semantics fix (P0-6): same "computed, then
                # discarded" failure class as combo_player_ids/lineup_assumed
                # above -- these four fields would otherwise silently vanish
                # for every candidate reaching the board through this
                # by-category/dashboard path.
                "posted_implied": c.get("posted_implied"), "market_fair": c.get("market_fair"),
                "market_fair_method": c.get("market_fair_method"),
                "edge_vs_fair": c.get("edge_vs_fair"),
                "lineup_assumed": c.get("lineup_assumed"),
                "_needs_price_lookup": False,
            })

    for stat, entries in by_category.items():
        for e in entries:
            if e.pop("_needs_price_lookup", True):
                needs = (e.get("projection") or {}).get("needs")
                market_stat = _fd_stat_alias(stat)
                # market-edge-semantics fix (P0-6): fair/method mirror
                # odds_fanduel.attach_market_prices' own two branches exactly
                # -- strikeouts is genuinely two-sided (exact no-vig via
                # true_over), everything else here is one-sided (assumed hold).
                if market_stat == "strikeouts" and k_prices is not None:
                    # Same two-sided lookup odds_fanduel.attach_market_prices
                    # uses -- FanDuel posts one line per starter, so this only
                    # hits when our recommended threshold is the one they
                    # offered.
                    k = k_prices.get(fd.normalize_name(e["name"]))
                    odds = k["over"] if (k and k.get("needs") == needs) else None
                    implied = round(k["true_over"], 4) if odds is not None else None
                    fair, fair_method = implied, ("exact_two_sided" if implied is not None else None)
                    posted = round(pp.implied_probability(odds), 4) if odds is not None else None
                else:
                    odds = (prices.get(fd.normalize_name(e["name"])) or {}).get((market_stat, needs))
                    implied = round(pp.implied_probability(odds), 4) if odds is not None else None
                    posted = implied
                    fair = round(pp.devig(implied), 4) if implied is not None else None
                    fair_method = "assumed_hold" if implied is not None else None
                e["market_odds"] = odds
                e["market_implied"] = implied
                e["market_edge"] = None if implied is None else round(e["hit_probability"] - implied, 4)
                e["posted_implied"] = posted
                e["market_fair"] = fair
                e["market_fair_method"] = fair_method
                e["edge_vs_fair"] = None if fair is None else round(e["hit_probability"] - fair, 4)
                e["price_clears"] = pp.price_is_acceptable(odds, e["hit_probability"])
            e["category"] = "best_of_category"
            e["clears_main_board_floor"] = e["hit_probability"] >= MIN_LINE_PROB

    out = {}
    for stat, entries in by_category.items():
        entries.sort(key=lambda e: e["hit_probability"], reverse=True)
        out[stat] = entries[:n_per_category]
    return out


def _fd_stat_alias(stat):
    """odds_fanduel.STAT_ALIASES, without importing odds_fanduel at module
    load (it's only ever imported lazily inside main(), same as everywhere
    else prices are looked up)."""
    return {"stolen_base": "stolen_bases"}.get(stat, stat)


def select_shadow_tracking(candidates, n_per_key=1):
    """Direct request, verbatim: "There should be no prop not rated and bet
    on to know the hit percentage. I understand if it isn't included in the
    final card but I still want to know." Specifically raised about lasers
    and hard-hit props.

    score_laser/score_pitcher_outs/score_combined_strikeouts already compute
    EVERY real threshold a batter/pitcher could be scored on (e.g. both
    105+ and 110+ MPH exit velocity), but _pick_line only ever keeps the
    single best one as the real candidate -- every other threshold is
    demoted to a supporting `alternatives` annotation on the winner and,
    until now, never became its own gradable pick. Concretely: hard_hit_110
    almost never wins _pick_line's selection against hard_hit_105 (110+ is
    a strict subset of 105+, so its raw probability is always lower), which
    meant this project could never actually measure hard_hit_110's real
    hit rate -- exactly the gap flagged when the "ALL PROPS" hit-rate
    breakdown came back with hard_hit_110 completely absent.

    This pulls every one of those demoted alternates back out and turns
    each into its own trackable pick, grouped by (stat, needs) so e.g. two
    different pitcher_outs thresholds (which share one stat name) don't
    collide into the same slot the way a plain groupby on `stat` alone
    would.

    Deliberately NOT priced against FanDuel odds like
    select_best_by_category -- these were never going to reach the card,
    so there is no betting decision to price, only a hit-rate question to
    answer. And deliberately tagged category="shadow", a name that must
    stay OUT of grade_results.py's normal by_category split -- mixing
    these into "best_of_category" would quietly inflate that bucket with
    picks nobody could have actually made, corrupting the one number
    (headline hit rate) this whole project is judged by."""
    groups = defaultdict(list)
    for c in candidates:
        if c.get("type") not in ("batter", "pitcher", "game", "pitcher_combo"):
            continue
        for alt in c.get("alternatives") or []:
            if alt.get("prob") is None or alt.get("stat") is None:
                continue
            key = (alt["stat"], alt.get("needs"))
            groups[key].append({
                "type": c["type"], "name": c["name"], "player_id": c.get("player_id"),
                "combo_player_ids": c.get("combo_player_ids"),
                "team": c.get("team"), "matchup": c.get("matchup"), "game_pk": c.get("game_pk"),
                "side": c.get("side"),
                "prop": f"[shadow] Over {alt.get('line')} {CATEGORY_LABELS.get(alt['stat'], alt['stat'])}",
                "projection": {"stat": alt["stat"], "value": alt.get("line"), "needs": alt.get("needs")},
                "lean": None, "score": c.get("score"), "confidence": c.get("confidence"),
                "notable_signals": 0,
                "hit_probability": alt["prob"], "signals": c.get("signals") or {},
                "base_rate": alt.get("base_rate"), "lift": alt.get("lift"),
                "probability_basis": "empirical_shrunk",
                # raw_hit_probability/calibrated_by: this alternate's OWN
                # apply_calibration() provenance (see that function), never
                # borrowed from the primary candidate. Honestly absent when
                # no calibrator applied to this exact market -- a future
                # signal-promotion analysis over shadow_tracking must be
                # able to tell a calibrated probability from a raw one, the
                # same discipline the real board's categories already get.
                "raw_hit_probability": alt.get("raw_prob"),
                "calibrated_by": alt.get("calibrated_by"),
                "why": ["Tracked for hit-rate measurement only -- an alternate threshold "
                        "that lost selection against the real candidate, never a live bet."],
                "watchouts": [],
                "market_odds": None, "market_implied": None, "market_edge": None,
                "posted_implied": None, "market_fair": None, "market_fair_method": None,
                "edge_vs_fair": None,
                "price_clears": None,
                "category": "shadow",
            })
    out = {}
    for key, entries in groups.items():
        entries.sort(key=lambda e: e["hit_probability"], reverse=True)
        out[key] = entries[:n_per_key]
    return out


_RELIABILITY_ORDER = {"A": 0, "B": 0, "C": 0, "D": 1}


def rank_for_board(gated):
    """Order an already-gated (MIN_QUALITY_SCORE + positive-lift-floor)
    candidate pool for the top10 board. A pure function of that pool so
    it's directly testable, unlike the rest of main()'s live orchestration.

    EVIDENCE BEFORE CONFIDENCE. Sorting on probability alone put a 12-start
    grade-D pick above a 107-game grade-A pick with sixteen times the lift,
    because the two probabilities were three points apart. A number resting
    on twelve observations should not outrank one resting on a hundred, so
    picks are grouped by whether their evidence is adequate first, and only
    then ordered within each group. Thin-sample picks are still shown --
    they are ranked, not hidden.

    REAL BUG, found 2026-08-13 checking the actual graded record: this used
    to rank every priced candidate by raw hit_probability alone, and
    price_clears (pp.price_is_acceptable, computed on every candidate since
    prop_probability.py was built) was never read anywhere in this
    function -- purely display metadata. Probability and "how short the
    market has already priced it" are highly correlated (the best hitters
    get the shortest odds), so ranking on probability alone systematically
    promotes heavily-juiced favorites the BOOK has already fully priced,
    not picks with a real edge over that price. Confirmed against the real
    graded record: 54 of the last 57 main-board picks (08-07..08-12)
    carried price_clears=False -- the board's own value check said "no" --
    and shipped anyway. Average price -254 (needs 71.3% to break even),
    actual hit rate 56.1%, real flat-stake ROI -22.1%. The positive-read
    floor (MIN_POSITIVE_LIFT, applied before this function is called) only
    compares a pick to its market's GENERIC base rate, not to the specific
    price FanDuel is offering tonight -- a real edge over the average is
    not the same thing as a real edge over this exact number, and only the
    latter is a bet worth making.

    Split into a real-edge tier (ranked by market_edge -- the dashboard's
    own Top Picks tab already does exactly this, "not just raw probability,
    which just rewards the easiest, most-chalk market every time") and
    everything else -- kept in this full ordering for diagnostics and the
    "what I'd skip tonight" list even though, as of 2026-08-13, the main
    board itself (select_main_board, below) no longer draws from the
    no-edge/unpriced tiers to pad itself out."""
    priced = [c for c in gated if c.get("hit_probability") is not None]
    unpriced = [c for c in gated if c.get("hit_probability") is None]
    clears = [c for c in priced if c.get("price_clears") is True]
    no_edge = [c for c in priced if c.get("price_clears") is not True]
    clears.sort(key=lambda c: (-_RELIABILITY_ORDER.get(c.get("reliability", "D"), 1),
                               c.get("market_edge") or 0, c["hit_probability"]), reverse=True)
    no_edge.sort(key=lambda c: (-_RELIABILITY_ORDER.get(c.get("reliability", "D"), 1),
                                c["hit_probability"], c["score"]), reverse=True)
    unpriced.sort(key=lambda c: c["score"], reverse=True)
    return clears + no_edge + unpriced


def select_main_board(ranked, n=10):
    """Select the main board from rank_for_board's full ordering: real edge
    over the market ONLY (price_clears is True), full stop -- up to n picks.

    Until 2026-08-13 this was simply `ranked[:n]`, unconditionally padding
    to n with price_clears=False fallbacks whenever fewer than n candidates
    actually cleared price -- the exact style of pick behind the -22.1% real
    flat-stake ROI documented in rank_for_board's docstring above. Jacob's
    call once that was shown to him: ship fewer than n picks on a thin night
    rather than dress a no-edge favorite up as a top-10 pick. price_clears
    is None (no market price to check at all, not a confirmed non-edge) is
    excluded for the same reason -- an unconfirmed edge is not a confirmed
    one. Board can legitimately come back empty on a night nothing clears."""
    return [c for c in ranked if c.get("price_clears") is True][:n]


_EARLY_PRICED_MARKETS = {"pitcher_outs", "combined_strikeouts"}


def pricing_freshness_warning(gated):
    """Returns a warning string, or None, checking for the exact bug class
    fixed 2026-08-13: market pricing running too late in main() for
    rank_for_board's sort / select_main_board's filter to see it.

    pitcher_outs and combined_strikeouts price themselves early, inside
    their own scoring function -- every OTHER market depends on the
    attach_market_prices() call in main() having already run by the time
    this is checked. If that call silently stopped running (or moved again,
    later, in some future edit) every general-market candidate would have
    price_clears=None here, and the board would go quiet on real edges
    exactly the way it did for 3 hours on 2026-08-13 (Brandon Marsh's Over
    0.5 Singles, +0.019 edge, sat unseen in price_clears=True while the
    main board carried only 1 pick, from the one early-priced market that
    still worked).

    A pool of ONLY early-priced-market candidates (or an empty pool) is not
    suspicious -- there is nothing else to check pricing freshness against,
    so returns None rather than a false alarm on a thin, legitimate slate."""
    general = [c for c in gated
               if (c.get("projection") or {}).get("stat") not in _EARLY_PRICED_MARKETS]
    if general and all(c.get("price_clears") is None for c in general):
        return ("SUSPICIOUS: every gated candidate outside pitcher_outs/combined_strikeouts "
                "has price_clears=None. Either the FanDuel price fetch failed (see any "
                "warning above) or market pricing is running too late in main() again -- "
                "see the fix and its own comment near attach_market_prices, above.")
    return None


def main() -> int:
    print("Generating top 10 picks (deterministic scoring, no LLM call)...")
    result = _build_and_score()
    if result is None:
        return 0
    candidates, ctx = result
    game_meta = ctx['game_meta']; park_wx = ctx['park_wx']
    bullpen_scores = ctx['bullpen_scores']
    emp_batters = ctx['emp_batters']; emp_pitchers = ctx['emp_pitchers']
    early_po_prices = ctx.get('po_prices')
    early_k_prices = ctx.get('k_prices')

    # RANKING. See rank_for_board's own docstring for the final ordering
    # (real edge over the market first, chance of cashing as the tiebreak
    # and the fallback for picks with no market edge to rank by -- not
    # "chance of cashing decides the order" outright, which is what shipped
    # until 2026-08-13 and is exactly what let heavily-juiced, no-edge
    # favorites dominate the board). The quality score below is a GATE
    # rather than part of that ordering. Both parts matter:
    #
    #   - Without the gate, this ranks a 70% prop on a player in an awful
    #     spot above a 68% prop with every signal behind it, purely because
    #     of the base rate. The score is what knows about tonight.
    #   - Without probability/edge ordering, the board ranks by a 0-100
    #     quality number that is not a probability and does not behave like
    #     one -- which is how a 28% stolen base finished #1 while a 79%
    #     hits prop went unranked.
    #
    # A candidate that could not be priced at all keeps its place in the
    # score order behind everything that could, rather than being dropped:
    # an unpriced pick is a gap in coverage, not evidence against the pick.
    # Untrustworthy INPUTS are rejected before anything is ranked -- that is a
    # different question from whether the model likes the pick.
    candidates, _qc_rejected, assumed_lineup = quality_control(candidates, game_meta, park_wx, emp_pitchers)
    if assumed_lineup:
        write_early_look(assumed_lineup)

    # Every recorded signal nudges the score now, in proportion to how much
    # it's actually earned so far -- see apply_signal_weights's own docstring.
    # Applied here, before the quality gate and ranking below, so the
    # adjustment is real input to which picks survive and how they're
    # ordered, not cosmetic decoration on a board already decided.
    signal_trust = load_signal_trust()
    apply_signal_weights(candidates, trust=signal_trust)
    if signal_trust:
        moved = sum(1 for c in candidates if c.get("signal_weight_adjustment"))
        print(f"    Signal-weighted adjustment applied to {moved} of {len(candidates)} "
              f"candidates ({len(signal_trust)} signals with any measured trust)")

    # THE REAL POSTED PRICE, which the board has never carried.
    #
    # Every pick shipped with `estimated_odds`, and that number is
    # pp.american_odds(hit_probability) — our own probability restated as a
    # price. It is circular by construction: it cannot disagree with us, so it
    # can never tell anyone whether a pick is good VALUE, only that the model
    # thinks it is likely. max_acceptable_price is derived from it too.
    #
    # attach_market_prices() was written to fix exactly this and had ZERO
    # callers, while FanDuel's prices were being fetched hourly and committed
    # to data/props. The information was free, already arriving, and never
    # reached the one document that gets read before betting.
    #
    # MOVED HERE 2026-08-13, from after `top10 = select_main_board(ranked)`.
    # REAL BUG, found live checking tonight's actual board against its own
    # by_category diagnostic: select_main_board (added earlier today, #15)
    # filters on price_clears is True, but this attach step -- the ONLY
    # thing that ever sets price_clears/market_odds/market_edge for the
    # general batter/pitcher markets -- ran AFTER select_main_board had
    # already run. attach_hit_probabilities (called from _build_and_score,
    # well before this point) never sets those fields; only score_pitcher_
    # outs/score_combined_strikeouts do, by pricing against an early-fetched
    # line directly inside their own scoring function. So every OTHER market
    # had price_clears=None (not False, ABSENT) at select-time, and `None is
    # True` is False -- meaning every hits/total_bases/home_runs/RBIs/runs/
    # singles/doubles/triples/hits_runs_rbis candidate was structurally
    # unable to ever appear on the main board, regardless of real edge,
    # while the two early-priced markets were the only ones that could.
    # Verified live: tonight's by_category diagnostic showed Brandon Marsh's
    # Over 0.5 Singles at price_clears=True, market_edge=+0.019 -- a real,
    # confirmed edge -- sitting in "best_of_category" while the main board
    # carried only 1 pick total (the one early-priced market). Moving this
    # block earlier, before the gate/rank/select pipeline runs, fixes it at
    # the source instead of teaching select_main_board to also poll a
    # market fetch itself.
    #
    # Never fatal: an unpriced prop leaves the fields absent rather than
    # guessing, and a failed fetch leaves the board exactly as it was.
    moonshots = []
    deep_moonshots = []
    by_category = {}
    # No pricing dependency (deliberately unpriced, see its own docstring),
    # so computed here rather than inside the pricing try-block below --
    # an odds-fetch failure must never also silently zero out shadow
    # tracking, the two are unrelated failure modes.
    shadow_tracking = select_shadow_tracking(candidates)
    shadow_n = sum(len(v) for v in shadow_tracking.values())
    print(f"    Shadow tracking: {shadow_n} alternate-threshold pick(s) across "
          f"{len(shadow_tracking)} (stat, threshold) combo(s) -- scored and will be "
          f"graded, never shown on the card")
    try:
        import odds_fanduel as _fd
        # Fetched once, explicitly, rather than left for attach_market_prices
        # to fetch internally -- the moonshot selection below needs this same
        # dict to look up HOME RUN prices, which live_options carries but the
        # chosen `projection` almost never does (see select_moonshots).
        # Fetching it twice would mean two full FanDuel sweeps of a 15-game
        # slate for the same data.
        prices = _fd.fetch_prop_prices()
        # Already fetched earlier (before scoring, so attach_hit_probabilities'
        # strikeouts branch could price against the real line directly) --
        # reused here rather than sweeping FanDuel for the same market twice,
        # same pattern as po_prices/combined_k_prices below.
        k_prices = early_k_prices or {}
        try:
            fi_prices = _fd.fetch_first_inning_totals()
        except Exception:
            fi_prices = {}
        # Already fetched earlier (before scoring, so score_pitcher_outs
        # could price the real line directly) -- reused here rather than
        # sweeping FanDuel for the same market twice.
        po_prices = early_po_prices or {}
        # Fetched early too (before scoring, so score_combined_strikeouts
        # could price the real ladder directly) -- reused here for the same
        # reason po_prices is: attach_market_prices now has a
        # combined_strikeouts branch (added this pass, see its own
        # docstring), and passing this avoids a second FanDuel sweep for a
        # market this function used to no-op on entirely.
        combined_k_prices = ctx.get("combined_k_prices") or {}
        _, n_priced = _fd.attach_market_prices(candidates, prices=prices, k_prices=k_prices,
                                               fi_prices=fi_prices, po_prices=po_prices,
                                               combined_k_prices=combined_k_prices)
        print(f"    Real market prices attached to {n_priced} of {len(candidates)} candidates")
        # PER-MARKET REAL-PRICE COVERAGE. The pool diagnostic above only ever
        # showed whether the MODEL had a probability, which is a different
        # question from whether FanDuel's price was actually found and
        # attached -- stolen_base sat at 141/0 real prices for weeks while
        # printing 141/138 "priced" above, because "priced" there means
        # hit_probability is not None. Printed separately, after attach
        # runs, since the pool table above is built before this call.
        price_pool = defaultdict(lambda: {"n": 0, "priced": 0})
        for c in candidates:
            st = (c.get("projection") or {}).get("stat") or "?"
            e = price_pool[st]
            e["n"] += 1
            if c.get("market_odds") is not None:
                e["priced"] += 1
        print("    Real market-price coverage by market (considered / priced):")
        for st, e in sorted(price_pool.items(), key=lambda kv: -kv[1]["n"]):
            flag = "" if e["priced"] == e["n"] else "   <-- UNPRICED CANDIDATES"
            print(f"      {st:18s} {e['n']:4d} / {e['priced']:4d}{flag}")
        moonshots = select_moonshots(candidates, prices, _fd, n=5)
        print(f"    {len(moonshots)} moonshot(s) selected (home runs, priced, ranked by hit probability)")
        deep_moonshots = select_deep_moonshots(candidates, prices, _fd, n=5)
        print(f"    {len(deep_moonshots)} deep moonshot(s) selected (420+ FT home runs, priced, "
              f"ranked by hit probability)")
        # n_per_category=5, min_score=0: direct report, verbatim -- "Even if
        # a prop doesn't make the main board I still want to show at least
        # something. I don't want to see NO lasers. I just want to see the
        # top 5 best of the slate. We aren't trying to prevent people from
        # betting we want them to at least have options." Previously floored
        # at MIN_QUALITY_SCORE and capped to 1, which could -- and did, the
        # night the Laser HR-conditioning fix landed and real Laser scores
        # dropped -- render "no candidate tonight" for a whole family even
        # though real candidates existed, just below the bar. write_markdown
        # flags anything below either floor with ⚠ rather than hiding it.
        by_category = select_best_by_category(candidates, prices, _fd, n_per_category=5,
                                               k_prices=k_prices, min_score=0)
        print(f"    Best-of-category board: {len(by_category)} of {len(CATEGORY_LABELS)} "
              f"families had a candidate tonight")
        # Shadow tracking: direct request, verbatim: "we should always track
        # every prop to know if one sticks out randomly for some reason. We
        # have to be prepared. We can't just throw them away." by_category
        # above is already unfloored (min_score=0), so its own top entry per
        # stat covers every CATEGORY_LABELS family without a second,
        # redundant select_best_by_category() re-run.
        for stat, entries in by_category.items():
            for e in entries[:1]:
                needs = (e.get("projection") or {}).get("needs")
                key = (stat, needs)
                if key in shadow_tracking:
                    continue  # an alternate-threshold entry already covers this exact slot
                e = dict(e)
                e["category"] = "shadow"
                shadow_tracking[key] = [e]
        shadow_n = sum(len(v) for v in shadow_tracking.values())
        print(f"    Shadow tracking (unfloored): {shadow_n} total tracked pick(s) across "
              f"{len(shadow_tracking)} (stat, threshold) combo(s)")
    except Exception as e:
        m.warn(f"Market prices unavailable ({e}) — board ships without them")

    # THE SAME recommendation/probability-integrity layer the live dashboard
    # runs (recommendation.classify_recommendation / attach_recommendations,
    # dashboard/build_dashboard.py's run_live_fetch and refresh_prices.py),
    # applied here so the static picks JSON grade_results.py reads carries a
    # real recommendation_status too -- otherwise the graded record could
    # never separate a genuine Top Pick's real performance from a longshot's
    # or a Lean's (see grade_results.py's by_recommendation_status). Run on
    # every pool that ends up in write_json's `picks` list: candidates
    # (covers top10/skipped by shared object identity, since select_main_board
    # filters `candidates` itself rather than copying), plus moonshots/
    # deep_moonshots/by_category/shadow_tracking, which are all built as
    # fresh dicts with no shared identity to `candidates` and would
    # otherwise never get classified at all.
    import recommendation as grec
    _board_generated_at = datetime.now(timezone.utc).isoformat()
    _rec_pool = (candidates + moonshots + deep_moonshots
                + [c for entries in by_category.values() for c in entries]
                + [c for entries in shadow_tracking.values() for c in entries])
    grec.attach_recommendations(_rec_pool, odds_fetched_at=_board_generated_at,
                                board_generated_at=_board_generated_at)
    n_top_pick = sum(1 for c in _rec_pool if c.get("status") == "top_pick")
    print(f"    Recommendation layer: {n_top_pick} of {len(_rec_pool)} scored candidates "
          f"classified as top_pick tonight")
    # PHASE 3, ITEM 3: computed ONCE here (one git subprocess call, one real
    # timestamp -- reused, not re-derived, so write_json's per-row copies
    # below can never drift a few seconds from what attach_recommendations
    # actually classified against) and threaded into write_json so every
    # SAVED recommendation is self-describing on its own, not only readable
    # via the board wrapper around it. Direct instruction: "Verify that
    # every future saved recommendation contains enough information to
    # reproduce what produced it" -- a single row in grades_*.json, read on
    # its own out of context, needs this on it.
    _rec_metadata = grec.build_metadata(odds_fetched_at=_board_generated_at,
                                        board_generated_at=_board_generated_at)

    gated = [c for c in candidates if c["score"] >= MIN_QUALITY_SCORE]

    # POSITIVE-READ FLOOR. A pick has to beat the league base rate for its own
    # market before it can be recommended at all -- only then does
    # rank_for_board's edge-first ordering (see its own docstring) decide
    # the rest.
    #
    # This exists because ranking on probability alone selects, systematically,
    # for picks the model knows nothing about. The easiest line in a market
    # always carries the highest probability AND the least room for skill to
    # show, so the sort key and the strength of the read pull in opposite
    # directions. Live on 2026-08-06: the #1 pick was Cristopher Sanchez over
    # 3.5 strikeouts at 91.3%, which is 0.6 points BELOW the league rate for a
    # starter clearing that line -- the model held no positive information
    # about him, and that is precisely why he ranked first. Dylan Cease at 6+
    # strikeouts, +16.9 points over base rate, ranked beneath him.
    #
    # This floor is independent of rank_for_board's own edge-first ordering
    # below -- it only removes bets where the number comes from the market
    # being easy rather than from anything known about the player.
    #
    # A pick whose lift cannot be computed is NOT dropped: an unknown base rate
    # is missing information about the market, not evidence against the pick.
    with_read = [c for c in gated
                 if c.get("lift") is None or c["lift"] >= MIN_POSITIVE_LIFT]
    no_read = [c for c in gated if c not in with_read]
    if with_read:
        gated = with_read
    else:
        m.warn("No candidate cleared the positive-read floor — falling back to "
               "the full pool so the board is not empty.")

    _pricing_warning = pricing_freshness_warning(gated)
    if _pricing_warning:
        m.warn(_pricing_warning)

    ranked = rank_for_board(gated)

    # CANDIDATE POOL DIAGNOSTIC. Prints what was actually CONSIDERED, not just
    # what won. Without it, a board that comes out all one market is
    # ambiguous: it could mean that market genuinely had the best picks, or
    # that another market's candidates were never priced and got silently
    # buried. That exact failure already happened once -- every walk prop
    # shipped with a null probability because score_walk omitted projected_pa,
    # and under probability ranking an unpriced candidate sorts behind
    # everything, so the whole prop type vanished with no error anywhere.
    pool = defaultdict(lambda: {"n": 0, "priced": 0, "best": 0.0})
    for c in candidates:
        st = (c.get("projection") or {}).get("stat") or "?"
        e = pool[st]
        e["n"] += 1
        if c.get("hit_probability") is not None:
            e["priced"] += 1
            e["best"] = max(e["best"], c["hit_probability"])
    if no_read:
        top_cut = sorted(no_read, key=lambda c: -(c.get("hit_probability") or 0))[:3]
        print(f"    Positive-read floor removed {len(no_read)} candidate(s) at or "
              f"below their market's base rate:")
        for c in top_cut:
            print(f"      {c['name'][:22]:22s} {c['prop'][:34]:34s} "
                  f"{(c.get('hit_probability') or 0)*100:5.1f}%  lift {c.get('lift'):+.3f}")
    print("    Candidate pool by market (considered / priced / best prob):")
    for st, e in sorted(pool.items(), key=lambda kv: -kv[1]["best"]):
        flag = "" if e["priced"] == e["n"] else "   <-- UNPRICED CANDIDATES"
        print(f"      {st:18s} {e['n']:4d} / {e['priced']:4d}   best={e['best']:.3f}{flag}")
    if not ranked:   # nothing cleared the gate — fall back rather than ship nothing
        ranked = sorted(candidates, key=lambda c: c["score"], reverse=True)

    # See select_main_board's own docstring for why this is no longer
    # `ranked[:10]` -- the main board only ships picks with a real,
    # confirmed edge over tonight's actual price now.
    top10 = select_main_board(ranked)
    _top10_ids = {id(c) for c in top10}
    skipped = [c for c in ranked if id(c) not in _top10_ids and c["score"] >= 55][:2]

    write_markdown(top10, skipped, game_meta, bullpen_scores, ranked, moonshots, by_category, deep_moonshots)
    write_json(top10, moonshots, by_category, deep_moonshots, shadow_tracking,
              recommendation_metadata=_rec_metadata)
    persist_player_snapshots(candidates)
    print(f"Wrote {len(top10)} picks to {PICKS_FILE} and {PICKS_JSON_FILE}")

    # Readable board + a couple of real example parlays, generated every run
    # instead of left as a manual step someone has to remember. Never fatal:
    # a rendering failure shouldn't take down a pipeline that already
    # successfully wrote the picks that actually matter.
    try:
        import render_board as rb
        board_path = rb.write_board(date=m.TODAY)
        print(f"Wrote readable board to {board_path}")
    except Exception as e:
        m.warn(f"Board rendering failed ({e}) — picks JSON/markdown are unaffected")

    try:
        import parlay_builder as pbuild
        import render_parlay as rparlay
        pool = pbuild.load_todays_pool(date=m.TODAY)
        # Two examples, not a claim these are "the" picks -- a concrete
        # preview of the parlay product (the "free picks" idea) built from
        # the exact same engine a real customer request would use.
        for label, risk_level in (("safest", 0), ("risky", 100)):
            # price_legs=True here is a deliberate small extra fetch (3-6
            # players, not the whole slate) -- load_todays_pool() reads
            # persisted snapshots, which never carry market_odds (see
            # persist_player_snapshots), so without this every example would
            # show no price and no payout at all.
            result = pbuild.build_best_available_parlay(pool=pool, n=3, risk_level=risk_level,
                                                         price_legs=True)
            out_path = os.path.join(OUTPUT_DIR, f"parlay_example_{label}_{m.TODAY}.html")
            html_out = rparlay.render(result, request_text=f"Today's best {label} 3-leg parlay")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(html_out)
            print(f"Wrote example {label} parlay ({len(result['legs'])} legs) to {out_path}")
    except Exception as e:
        m.warn(f"Example parlay generation failed ({e}) — picks JSON/markdown are unaffected")

    # Every real scored candidate, filterable by prop type, sorted high to
    # low confidence within each filter -- the curated board only ever
    # shows the single best pick per category, so a genuinely strong
    # candidate that wasn't #1 in its own category is otherwise invisible.
    try:
        import parlay_builder as pbuild
        import render_full_board as rfb
        full_pool = pbuild.load_todays_pool(date=m.TODAY)
        full_html = rfb.render(full_pool, m.TODAY)
        full_path = os.path.join(OUTPUT_DIR, f"full_board_{m.TODAY}.html")
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(full_html)
        print(f"Wrote full board ({len(full_pool)} candidates) to {full_path}")
    except Exception as e:
        m.warn(f"Full board rendering failed ({e}) — picks JSON/markdown are unaffected")

    return 0


def write_json(top10, moonshots=(), by_category=None, deep_moonshots=(), shadow_tracking=None,
              recommendation_metadata=None):
    """Structured pick data for grade_results.py — never parse the markdown
    back into data, same lesson learned from mlb_daily.py's report text.

    Moonshots, deep moonshots and the best-of-category board are appended
    into the SAME `picks` list, tagged category="moonshot" /
    "moonshot_420" / "best_of_category" (top10 entries carry no category
    key, i.e. the primary board), rank continuing past 10 rather than
    restarting. They ride the exact grading path grade_results.py already
    has -- a parallel path would be a second thing to keep correct forever
    for no reason, when this one is already proven.

    shadow_tracking is DELIBERATELY NOT in that same `picks` list -- see
    select_shadow_tracking's docstring for why blending it in would
    corrupt the headline hit rate. It gets its own top-level key and its
    own grading pass in grade_results.py (shadow_by_category), never
    touching hits/misses/by_category.

    recommendation_metadata: PHASE 3, ITEM 3. Computed exactly ONCE by
    main() (one git subprocess call, one real timestamp -- the same one
    attach_recommendations() actually classified against) and stamped onto
    EVERY row below, not just the board-level wrapper. Direct instruction:
    "every future saved recommendation [should] contain enough information
    to reproduce what produced it" -- a single row in grades_*.json, read
    on its own out of context (which is exactly how grade_results.py and
    every analysis script in results/ consumes it), needs this on it
    directly. The per-row repetition this used to avoid (see the prior
    version of this docstring) is real but small -- a handful of short
    string/timestamp fields -- and is worth it for a saved recommendation
    that can prove what produced it without cross-referencing a board file
    that may not even be the one it was graded from."""
    rm = recommendation_metadata or {}
    def _row(i, c):
        return {
            "rank": i, "type": c["type"], "name": c["name"], "player_id": c["player_id"],
            "team": c["team"], "matchup": c["matchup"], "game_pk": c["game_pk"], "side": c.get("side"),
            # Same failure mode this file already hit once before with NRFI's
            # side/lean fields: a field computed on the candidate and never
            # written to disk here is invisible to grade_results.py, which
            # reads THIS file, not the in-memory candidate. Without it, every
            # combined_strikeouts pick would grade "missing combo_player_ids"
            # tomorrow morning, defeating grade_pick's own combined_strikeouts
            # branch entirely -- caught before it ever shipped a real pick.
            "combo_player_ids": c.get("combo_player_ids"),
            "prop": c["prop"], "projection": c["projection"], "lean": c.get("lean"), "score": c["score"],
            "confidence": c["confidence"], "notable_signals": c["notable_signals"],
            "category": c.get("category"),
            "hit_probability": c.get("hit_probability"),
            "signals": c.get("signals") or {},
            "base_rate": c.get("base_rate"), "lift": c.get("lift"),
            # Additive, separate concept from base_rate/lift -- see
            # stable_base_rate.py. None on every stat except
            # hits_runs_rbis/runs/rbis where a real season-to-date
            # reference exists.
            "lift_reference_rate": c.get("lift_reference_rate"),
            "stable_lift": c.get("stable_lift"),
            "raw_hit_probability": c.get("raw_hit_probability"),
            "calibrated_by": c.get("calibrated_by"),
            "prob_ci": c.get("prob_ci"), "prob_ci_source": c.get("prob_ci_source"),
            "sample_n": c.get("sample_n"),
            "reliability": c.get("reliability"),
            # PHASE 3, ITEM 3: real gap found while wiring per-row versioning
            # -- quality_control() sets this on the candidate, classify_
            # recommendation() already reads it to decide Top Pick status,
            # but write_json never actually persisted it, so a graded pick
            # carried no visible record of whether its lineup slot was
            # confirmed or assumed. Same "computed, then discarded"
            # boundary as combo_player_ids/prob_ci before it.
            "lineup_assumed": c.get("lineup_assumed"),
            "max_acceptable_price": (pp.max_acceptable_price(c["hit_probability"])
                                     if c.get("hit_probability") is not None else None),
            # estimated_odds is OUR fair price, not the market's. Kept because
            # it is the number max_acceptable_price is measured against, but
            # the real posted price sits beside it now so the two can never be
            # confused for each other again.
            "estimated_odds": (pp.american_odds(c["hit_probability"])
                               if c.get("hit_probability") is not None else None),
            "market_odds": c.get("market_odds"),
            "market_implied": c.get("market_implied"),
            # market_hold: the REAL, exactly-measured hold on the two-sided
            # markets (strikeouts/pitcher_outs/nrfi_combined -- see
            # odds_fanduel.attach_market_prices), vs None on the one-sided
            # batter markets where FanDuel posts no opposite side to measure
            # a hold from at all. Computed on the candidate all along and
            # dropped at this exact boundary until now -- same "computed,
            # then discarded" shape as combo_player_ids/prob_ci before it.
            # Phase 3 item 5 needs this to tell an EXACT no-vig probability
            # (market_hold present) from an 8%-ASSUMED approximation
            # (market_hold absent) at analysis time, per pick.
            "market_hold": c.get("market_hold"),
            # market-edge-semantics fix (P0-6): same "computed, then
            # discarded" boundary as market_hold directly above it --
            # posted_implied/market_fair/market_fair_method/edge_vs_fair
            # make the exact-vs-assumed-hold distinction explicit and
            # comparable across every market family (see
            # odds_fanduel.attach_market_prices' own docstring for the
            # full rationale).
            "posted_implied": c.get("posted_implied"),
            "market_fair": c.get("market_fair"),
            "market_fair_method": c.get("market_fair_method"),
            "edge_vs_fair": c.get("edge_vs_fair"),
            "market_edge": c.get("market_edge"),
            "price_clears": c.get("price_clears"),
            "probability_basis": c.get("probability_basis"),
            "probability_detail": c.get("probability_detail"),
            "alternatives": c.get("alternatives"),
            # Readable reasoning, not just the raw signal dict -- these were
            # computed on every candidate all along but dropped at this exact
            # boundary, so nothing that read the JSON back (a future
            # dashboard, a customer-facing view) could ever show WHY a pick
            # was made without re-deriving it from raw signals by hand.
            "why": c.get("why"), "watchouts": c.get("watchouts"),
            # apply_signal_weights's own docstring promises "every adjustment
            # is recorded on the candidate, never silent" -- true in memory,
            # false the moment this function persisted a pick without it.
            # Found in the same sweep as combo_player_ids, same root cause.
            "signal_weight_adjustment": c.get("signal_weight_adjustment"),
            # The 2026-08-15 rebuild's recommendation layer -- same field
            # names dashboard/build_dashboard.py's clean() uses for the same
            # data, so grade_results.py can key on recommendation_status
            # (top_pick/lean/value/neutral) regardless of which pipeline
            # (live dashboard or this static JSON) produced the pick.
            # Computed once, in main(), by recommendation.attach_recommendations()
            # over this exact same candidate/moonshot/by_category/shadow
            # pool -- c.get("status") here, not "recommendation_status",
            # because that rename happens at THIS serialization boundary,
            # matching clean()'s own rename in build_dashboard.py.
            "recommendation_status": c.get("status"),
            "status_reasons": c.get("status_reasons"),
            "stale": c.get("stale", False),
            # PHASE 3, ITEM 3 -- stamped per row, not just once at the board
            # level (see this function's own docstring for why). Every
            # field below is the SAME value across every row of a single
            # run by construction (one run, one version state, one price
            # fetch), which is exactly what makes it safe to compute once
            # in main() and copy rather than re-derive per row.
            "model_version": rm.get("model_version"),
            "selection_policy_version": rm.get("selection_policy_version"),
            "calibration_version": rm.get("calibration_version"),
            "feature_version": rm.get("feature_version"),
            "git_sha": rm.get("git_sha"),
            "prediction_timestamp": rm.get("prediction_timestamp"),
            "odds_timestamp": rm.get("odds_fetched_at"),
            # No standalone lineup-fetch timestamp exists anywhere in this
            # pipeline yet (lineup confirmation and scoring both happen
            # inside this same run) -- honestly reusing the real run
            # timestamp rather than fabricating a separate one that was
            # never actually measured. lineup_assumed (above) is the actual
            # status; this is only WHEN that status was true as of.
            "lineup_checked_at": rm.get("board_generated_at"),
        }
    category_flat = [c for entries in (by_category or {}).values() for c in entries]
    picks = [_row(i, c) for i, c in enumerate(top10, 1)]
    picks += [_row(i, c) for i, c in enumerate(moonshots, len(picks) + 1)]
    picks += [_row(i, c) for i, c in enumerate(deep_moonshots, len(picks) + 1)]
    picks += [_row(i, c) for i, c in enumerate(category_flat, len(picks) + 1)]
    shadow_flat = [c for entries in (shadow_tracking or {}).values() for c in entries]
    payload = {
        "date": m.TODAY,
        "generated": datetime.now().isoformat(),
        "picks": picks,
        "shadow_tracking": [_row(i, c) for i, c in enumerate(shadow_flat, 1)],
        # Also kept at the board level for convenience (a quick "what
        # version wrote this file" check without reading a pick row) --
        # the SAME dict already stamped onto every row above, not a fresh
        # build_metadata() call. Two calls would mean two git subprocess
        # invocations and two independently-drifting timestamps for what
        # is supposed to be one run's one version state.
        "recommendation_metadata": rm,
    }
    with open(PICKS_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


EARLY_LOOK_FILE = os.path.join(OUTPUT_DIR, f"early_look_{m.TODAY}.md")


def write_early_look(assumed_lineup):
    """Prep-only board for batters whose lineup slot is ASSUMED (tier-4
    fallback: last known batting order, not a real posted lineup) --
    written by direct request, so there's something to look at hours before
    real lineups post, understanding it will change.

    Deliberately separate from PICKS_FILE/PICKS_JSON_FILE and never
    persisted through persist_player_snapshots: grade_results.py reads the
    picks JSON and grades every entry in it against the final box score,
    and a guessed batting slot is not a bet anyone could actually place --
    mixing it into the graded record would score the model against its own
    guess instead of a real decision. This file is not graded, not read by
    anything else in the pipeline, and is overwritten every run."""
    ranked = sorted(assumed_lineup,
                    key=lambda c: (c.get("hit_probability") or 0, c["score"]), reverse=True)
    lines = [f"# Early Look — {m.TODAY} (ASSUMED lineups, not yet posted)", "",
             "Every player below is projected into last night's/his last game's batting "
             "slot for his team, because no real lineup — not MLB's own API, not MLB.com, "
             "not Rotowire — has been posted for tonight yet. These are NOT picks: the "
             "batting order can and does change (a day off, a platoon swap, a late "
             "scratch), and none of this is graded or fed back into the accuracy record. "
             "Read it as \"who to watch once real lineups post,\" not as a board to bet.",
             ""]
    for c in ranked[:25]:
        p = c.get("hit_probability")
        p_str = f"{p*100:.1f}%" if p is not None else "unscored"
        lines.append(f"- **{c['name']}** ({_team_label(c)}) — {c['prop']} — {p_str} "
                    f"[{c['matchup']}]")
    if not ranked:
        lines.append("_(none)_")
    with open(EARLY_LOOK_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"    Wrote {len(ranked)} assumed-lineup candidate(s) to {EARLY_LOOK_FILE} (not graded)")


def persist_player_snapshots(candidates):
    """One JSON file per player (data/players/{id}.json), appended to daily —
    not just the top 10. This is the longitudinal dataset that goes beyond the
    current L7/L14 windows: an audit trail for any pick ("what did we know
    about this player on this date"), and the substrate for genuine multi-
    week trend detection in a future pass. Bounded to the last
    PLAYER_SNAPSHOT_HISTORY_DAYS entries per player so file size doesn't grow
    unbounded over a season."""
    os.makedirs(PLAYERS_DIR, exist_ok=True)
    by_player = defaultdict(list)
    for c in candidates:
        if c.get("player_id"):
            by_player[c["player_id"]].append(c)

    for pid, entries in by_player.items():
        path = os.path.join(PLAYERS_DIR, f"{pid}.json")
        history = {"player_id": pid, "name": entries[0]["name"], "snapshots": []}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        history["name"] = entries[0]["name"]  # keep the latest known name
        history["snapshots"] = [s for s in history.get("snapshots", []) if s["date"] != m.TODAY]
        history["snapshots"].append({
            "date": m.TODAY,
            # SIGNALS ARE PERSISTED, and this was the gap that made them
            # pointless. Ten capabilities were wired into scoring and recorded
            # onto each candidate via _sig, specifically so backtest/signals.py
            # could measure whether they separate hits from misses. But the
            # snapshot written to disk kept only the score and the converging-
            # signal count, so every individual signal was discarded the moment
            # the run ended. The measurement they exist for was impossible.
            #
            # Also carried: the calibrated probability and the market read, so
            # a graded pick can later be traced back to exactly what was known
            # and what was believed at the time it was made.
            "evaluations": [{"prop": c["prop"], "type": c["type"], "score": c["score"],
                             "notable_signals": c["notable_signals"], "matchup": c["matchup"],
                             # WITHOUT game_pk NOTHING HERE CAN EVER BE GRADED.
                             # grade_results.grade_pick() keys on (game_pk,
                             # player_id) to pull a box-score line, and the
                             # snapshot carried the projection but not the
                             # game — so the signals persisted above could be
                             # read back but never matched to what happened.
                             # team/side/lean come along for the same reason:
                             # first-inning props are graded off the linescore
                             # by side, not off a batting line.
                             "game_pk": c.get("game_pk"), "team": c.get("team"),
                             "side": c.get("side"), "lean": c.get("lean"),
                             # Same gap just found and fixed in write_json()'s
                             # _row(), this time in the OTHER path that reaches
                             # grade_pick: measure_signals.py reads these
                             # persisted evaluations and grades them directly.
                             # Without this, a combined_strikeouts evaluation
                             # here would fail "missing combo_player_ids" the
                             # same way, and its signals (combined_k_edge)
                             # could never be measured for trust.
                             "combo_player_ids": c.get("combo_player_ids"),
                             "signals": c.get("signals") or {},
                             "hit_probability": c.get("hit_probability"),
                             "raw_hit_probability": c.get("raw_hit_probability"),
                             "lift": c.get("lift"), "reliability": c.get("reliability"),
                             "sample_n": c.get("sample_n"),
                             "projection": c.get("projection")}
                            for c in entries],
        })
        history["snapshots"] = history["snapshots"][-PLAYER_SNAPSHOT_HISTORY_DAYS:]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)


# ══════════════════════════════════════════════════════════════════════════
#  HIT PROBABILITY — the number the board is actually ranked on
# ══════════════════════════════════════════════════════════════════════════
#
# The 0-100 score is a QUALITY ranking: it says a pick has good matchup,
# form and environment signals behind it. It is not, and never was, a
# probability. Those two things come apart badly, and the board shipped on
# 2026-08-05 is the proof:
#
#   #1  Bobby Witt Jr.  To Steal a Base   score 88.1   real chance ~28%
#   --  Yordan Alvarez  Over 0.5 Hits     unranked     real chance ~79%
#
# Both reads are defensible on their own terms. Witt really does have the
# best steal profile on the slate -- elite speed, a weak-armed catcher, a
# good on-base rate -- so the quality score is not wrong about what it
# measures. It is just answering a different question than "which of these
# bets is most likely to cash", and that is the question being asked.
#
# So the score keeps its job as a QUALITY GATE (a high-probability prop on a
# player in a terrible spot is still a bad bet), and the ordering within the
# gate is by probability of hitting.
#
# THRESHOLD SELECTION IS PART OF THIS. The old code chose a prop type from
# rules unrelated to the odds of it landing -- season K% under 18 got "Over
# 1.5 Hits" regardless of whether that player clears two hits. On the same
# board that shipped Kyle Schwarber at "Over 1.5 Total Bases (proj. 1.45
# TB)": the pipeline recommending a line its own projection does not reach.
# Thresholds are now chosen by which one has the best chance of cashing.

# Below this the score is too weak to trust regardless of how probable the
# prop is -- a near-certain prop on a player in a bad spot with no
# converging signals is how you end up betting -400 juice on a coin flip.
MIN_QUALITY_SCORE = 55.0

# A pick must clear its own market's league base rate by at least this much to
# be recommendable. Zero means "must simply be better than the market average"
# rather than an arbitrary cushion -- the point is to exclude picks carrying no
# positive read, not to demand a particular size of one.
# A pick has to beat its market's base rate by a REAL margin, not by a
# rounding error. At exactly 0.0 the floor let through picks at +0.3 and +0.7
# points -- arithmetically positive, indistinguishable from the base rate in
# any practical sense -- and because the board ranks by probability those
# no-information picks led it. Two points is the smallest margin that
# represents an actual read.
MIN_POSITIVE_LIFT = 0.02

# Sample sizes below which a pick may still be shown, but must not outrank
# picks resting on real evidence. Grade D is roughly "fewer than 25
# games/starts" -- see RELIABILITY_TIERS.
MIN_RELIABILITY_TO_LEAD = "C"

# Empirical rates need a real sample before they outrank a model that at
# least accounts for tonight. Below this the model carries the estimate.
MIN_EMPIRICAL_GAMES = 25

# How much weight the empirical (backward-looking, assumption-free) rate
# gets against the modelled (context-aware, assumption-heavy) one. The
# empirical read anchors because it is measured rather than derived, but it
# cannot see tonight's catcher, park or opposing starter, so the model keeps
# a real share. This split is a starting position, not a fitted result --
# backtest/signals.py exists to replace it with something measured.
#
# ── AUDIT, 2026-08-06: MEASURED, AND ACTED ON 2026-08-07. ─────────────────
# See _batter_options' MODEL_SHRINK_K block and the strikeouts loop's
# STRIKEOUT_SHRINK_K block below -- both replace this blend with "shrink
# modelled toward the true league rate, drop empirical" wherever a modelled
# term and a true league rate exist. EMPIRICAL_WEIGHT/_blend() below now only
# fire for props with no modelled counterpart (runs/rbis/hits_runs_rbis/
# singles/doubles/triples, where _blend(empirical, None) just returns
# empirical) or as a fallback when no true league rate is available for the
# specific key. Left below verbatim as the record of what was measured and
# why -- do not re-read this block as an open item.
# Out-of-sample test on 244 batters with 250+ PA: both inputs fitted on each
# batter's first 60% of games (chronological), scored on his last 40%.
# Held-out mean log loss, and every configuration below fixed a priori so
# none of these numbers carry grid-selection bias:
#
#   prop                league  model   empir.  SHIPPED   model shrunk 50%
#                       -only   only    only    .6/.4     toward league
#   hits_1plus          .66943  .66848  .66864  .66780    .66580
#   hits_2plus          .53246  .53265  .53135  .53117    .52916
#   total_bases_2plus   .65623  .65886  .65675  .65702    .65401
#   total_bases_3plus   .52483  .52942  .52584  .52653    .52294
#   home_runs_1plus     .37973  .39705  .37829  .38011    .37576
#
# THE FINDING: on all five props the shipped blend is statistically
# INDISTINGUISHABLE from predicting the league rate for every player. Paired
# bootstrap over players (600 resamples) puts SHIPPED - league_only between
# -0.0016 and +0.0017 with every 95% CI straddling zero. The blend of two
# inputs is not, as it stands, earning its keep over a constant.
#
# WHY: both inputs are individually OVERCONFIDENT -- they spread players too
# far apart. model_only is actually worse than the constant on three of five
# props (notably home runs, .39705 vs .37973). Averaging two overconfident
# estimates just yields a third overconfident estimate; arithmetic averaging
# reduces variance between the inputs, not the shared overconfidence.
#
# THE FIX THAT DOES WORK, and the recommendation: shrink the MODELLED
# probability toward the league rate for that same threshold, and drop the
# empirical term from the combination entirely. Sweeping the shrink fraction
# k, held-out loss is minimised at k = 0.5-0.6 on every prop independently
# (pooled optimum k=0.5, pooled loss .54953 at k=0.5 vs .55729 at k=0 and
# .55254 at k=1). Unlike the shipped blend this beats the constant with CIs
# that exclude zero on four of the five props.
#
# ON SAMPLE-SIZE-DEPENDENT WEIGHTS -- asked for, and the answer is NO. The
# empirical input is ALREADY sample-size adjusted by _apply_shrinkage, so a
# second n-dependent weight double-counts the same correction. Measured: the
# best w and the shrinkage prior n0 trade off along a flat ridge (at n0=0 the
# best w is 0.00; n0=10 -> 0.25; n0=20 -> 0.45; n0=35 -> 0.60; n0=90 -> 0.65;
# n0=1200 -> 0.45), i.e. w and n0 are not two knobs, they are one knob --
# total shrinkage toward the league rate. Bucketing players by training-sample
# size gave best w of 0.90 / 0.30 / 0.25 for 34-52 / 52-64 / 64-69 games, i.e.
# non-monotone and in the OPPOSITE direction to the intuition, on loss
# differences of 0.0003 that are inside the noise.
#
# ON LOG-ODDS AVERAGING -- also asked for, and arithmetic is right here.
# At equal weight the two agree to 1.8e-05 - 4.8e-04 in held-out log loss on
# every prop except home runs, because the two inputs are close to begin with
# (mean |empirical - modelled| is 0.015-0.032). Where they diverge, log-odds
# is WORSE, not better: pooled best 0.55030 vs 0.54953 arithmetic, and on
# home runs 0.37750 vs 0.37573. Log-odds averaging is multiplicative in the
# tails and drags low-probability props further down, which is the wrong
# direction for exactly the props where the model is already overconfident.
EMPIRICAL_WEIGHT = 0.6


def _blend(empirical, modelled):
    """Combine the measured rate and the modelled one, preferring whichever
    is actually available. Returns (probability, basis_label)."""
    if empirical is not None and modelled is not None:
        return (EMPIRICAL_WEIGHT * empirical + (1 - EMPIRICAL_WEIGHT) * modelled,
                "blended")
    if empirical is not None:
        return empirical, "empirical"
    if modelled is not None:
        return modelled, "modelled"
    return None, "unavailable"


# Minimum chance of cashing a recommended line must still carry. Above this
# floor the line is chosen by LIFT rather than by probability -- see
# _pick_line for why the old probability-maximising rule was selecting
# against the model's own information.
MIN_LINE_PROB = 0.60


def _keep_options(opts, default_stat=None):
    """Keep the WHOLE probability curve, not just the line we recommend.

    THE MODEL ALREADY COMPUTES THIS AND THREW IT AWAY. _pick_line() below
    chooses one threshold per player per stat, and everything else computed
    alongside it was discarded. That is right for the board — a board offers
    one recommendation — and quietly disastrous for pricing, because the
    market prices every threshold.

    Measured on the 2026-08-07 slate: FanDuel priced 440 lines across 245
    (player, stat) groups, and the value screen could attach a real model
    probability to SEVEN of them. All seven were `hits, needs=1`, the line
    the model happened to pick. Matt Olson's hits were priced at 1+ and 2+;
    the model had a number for both and offered only 1+. So 433 of 440 props
    were screened on season rates — blind to the opposing starter, the park,
    the weather and the batting-order slot, which is exactly the failure
    value_board's own docstring claims to have fixed.

    Nothing here is a new calculation. It is the same list _pick_line is
    handed, trimmed to what a consumer needs to price a market line.

    default_stat exists because the two callers build options differently:
    _batter_options() puts a 'stat' on every row, and the strikeout loop does
    not — it knows the stat from context. Checked rather than assumed, since
    a silently-None stat would key every strikeout option to (name, None,
    needs) and match nothing, reproducing the exact bug this fixes."""
    return [{"stat": o.get("stat") or default_stat,
             "needs": o.get("needs"), "line": o.get("line"),
             "prob": o.get("prob"), "base_rate": o.get("base_rate"),
             "lift": o.get("lift"), "basis": o.get("basis"),
             # Real bug, found 2026-08-15 audit: this fixed field list
             # dropped the per-line "ci" _batter_options() computes, which
             # is exactly the field that stops select_best_by_category()
             # from falling back to a stale, wrong-stat CI carried over from
             # the candidate's primary projection -- see _batter_options'
             # own comment on the same fix for the full story.
             "ci": o.get("ci"),
             # CI-provenance-honesty fix (P0-7): same "computed, then
             # discarded" boundary as ci directly above -- ci_source was
             # added to _batter_options'/apply_calibration's own opt dicts
             # but this exact trim silently dropped it too.
             "ci_source": o.get("ci_source")}
            for o in (opts or []) if o.get("needs") is not None
            and o.get("prob") is not None]


def _pick_line(opts):
    """Choose which line to recommend, from the lines available for a player.

    THE RULE THIS REPLACES SELECTED AGAINST ITS OWN INFORMATION. It took the
    highest-probability line inside a usable band. Within any market the
    easiest line always carries both the highest probability and the least
    room for a read to show, so maximising probability reliably picked the
    line the model knew least about. Measured on Cristopher Sanchez:

        over 3.5 K   91.5%   lift +20.0   <- what the old rule chose
        over 4.5 K   86.4%   lift +32.4
        over 5.5 K   80.6%   lift +42.9   <- what this rule chooses

    He remains an 80.6% bet. This is not trading probability away for
    information; it is collecting information that was being discarded for
    eleven points of probability. Every "the model has no read on this pick"
    complaint traced back to the old rule.

    The floor is what keeps the objective intact: the recommendation still has
    to be likely, and only among likely lines does the strength of the read
    break the tie. Lines whose lift cannot be computed fall back to
    probability order, since an unknown base rate is missing information about
    the market rather than evidence against the line."""
    if not opts:
        return None
    eligible = [o for o in opts
                if MIN_LINE_PROB <= o["prob"] <= pp.MAX_USEFUL_PROB]
    if not eligible:
        # Nothing clears the floor -- fall back to the most likely line
        # available rather than recommending nothing.
        return max(opts, key=lambda o: o["prob"])
    with_lift = [o for o in eligible if o.get("lift") is not None]
    if with_lift:
        return max(with_lift, key=lambda o: (o["lift"], o["prob"]))
    return max(eligible, key=lambda o: o["prob"])


def _batter_options(c, comp, emp, league=None):
    """Every standard prop this batter could be bet on tonight, each with its
    chance of hitting. Returns a list of option dicts, best first."""
    options = []
    pa = c.get("projected_pa")
    dist = None
    if comp:
        dist = pp.pa_outcome_distribution(
            singles_rate=comp.get("singles_rate"), double_rate=comp.get("double_rate"),
            triple_rate=comp.get("triple_rate"), hr_rate=comp.get("hr_rate"))
    rates = (emp or {}).get("rates") or {}
    enough = (emp or {}).get("games", 0) >= MIN_EMPIRICAL_GAMES

    base_rates = {}
    # Separate from base_rates on purpose. base_rates mixes the TRUE league
    # rate (mlb_sources.league_base_rates(), a real season-long measurement)
    # with r["league_p"] (mlb_sources._apply_shrinkage's target, computed
    # from whichever players were passed into empirical_batter_prop_rates --
    # tonight's ~250-300 batters, not the league) whenever the true rate is
    # unavailable for a given key. That mixing is fine for "lift", which is
    # display-only and always has a real player probability to compare
    # against. It is NOT fine for the league-only fallback below, which
    # MANUFACTURES a probability from nothing else -- letting that draw on
    # a slate-scoped average would repeat, in a smaller way, the exact
    # mistake already found and fixed once in this file: a "league" prior
    # computed from a narrow, non-representative sample (there, the first-
    # inning bug shipped 83.1% scoreless against a true season measurement
    # of 70.6%). true_league_rates holds ONLY entries sourced from the real
    # league dict, so the fallback can never draw on the circular one.
    true_league_rates = {}

    # Real bug, found 2026-08-15 audit: attach_reliability() computed ONE
    # prob_ci per candidate, keyed to whichever single stat/needs the
    # candidate's OWN _pick_line-chosen projection happened to be (almost
    # always hits or total_bases) -- and select_best_by_category() then
    # copied that SAME interval onto every OTHER family's line for that
    # player (his total-bases line, his home-run line, ...) verbatim. Live
    # data confirmed the real damage: Pete Crow-Armstrong's Total Bases line
    # showed 39.8% probability next to a "95% CI [59.6%, 75.9%]" that was
    # actually his HITS interval, and Munetaka Murakami's Home Run line
    # showed 19.1% next to the same borrowed [64.3%, 82.5%]. Fixed at the
    # root: every option below carries its OWN interval, computed from the
    # REAL empirical hit/n count for that EXACT stat+threshold, captured
    # here as emp_p runs over each family.
    raw_rates = {}

    def emp_p(key):
        r = rates.get(key)
        raw_rates[key] = r
        lg = (league or {}).get(key)
        if lg is not None:
            true_league_rates[key] = lg
        if lg is None and r is not None:
            lg = r.get("league_p")
        if lg is not None:
            base_rates[key] = lg
        if not r or not enough:
            return None
        # Shrunk toward the league rate for this same threshold -- not the
        # raw proportion (overfits a short sample) and not the confidence
        # bound (biased low at every sample size). See _apply_shrinkage.
        return r.get("p_hat", r["p"])

    # AUDIT, 2026-08-12: found by test_lookup_table_consistency.py cross-
    # checking every family's thresholds against MARKET_MAP -- hits 4+,
    # total_bases 5+, runs 3+, rbis 3+/4+ and hits_runs_rbis 4+ were all real,
    # currently-posted FanDuel markets (confirmed in MARKET_MAP) with a real
    # empirical rate already computed (_PROP_THRESHOLDS has carried all of
    # them all along), never offered here -- same "computed, then discarded"
    # failure as home_runs 2+/3+ above, just five more instances of it that
    # a manual read missed. total_bases 1+ deliberately NOT added: no such
    # MARKET_MAP entry exists (FanDuel does not post it), so a rate for it
    # would have no real price to ever attach to.
    families = [
        ("hits", "Hits", [(0.5, 1), (1.5, 2), (2.5, 3), (3.5, 4)],
         (lambda k: pp.p_at_least_hits(k, dist, pa)) if dist and pa else None),
        ("total_bases", "Total Bases", [(1.5, 2), (2.5, 3), (3.5, 4), (4.5, 5)],
         (lambda k: pp.p_at_least_total_bases(k, dist, pa)) if dist and pa else None),
        # 2+ and 3+ were dead on arrival before this: TO_HIT_2+_HOME_RUNS has
        # been in MARKET_MAP and home_runs_2plus in mlb_sources._PROP_THRESHOLDS
        # the whole time, but this family list only ever asked for 1+, so
        # neither threshold could ever become a candidate's projection. Found
        # live: FanDuel is currently posting TO_HIT_3+_HOME_RUNS too (verified
        # against a real pull, 4 occurrences across 8 games), not in
        # MARKET_MAP at all until this same pass added it. p_at_least_home_runs
        # is a plain binomial tail sum, so 2 and 3 need no new math, only
        # asking for them -- same "computed, then discarded" failure as the
        # six markets fixed above.
        ("home_runs", "Home Runs", [(0.5, 1), (1.5, 2), (2.5, 3)],
         (lambda k: pp.p_at_least_home_runs(k, dist, pa)) if dist and pa else None),
        # THE SIX MARKETS THE MODEL COULD NOT PRICE.
        #
        # FanDuel prices nine batter families; this list built three. Measured
        # on the 2026-08-07 slate: total_bases 114, hits_runs_rbis 62, hits 61,
        # rbis 57, runs 56, singles 31, home_runs 29, doubles 29, triples 2 —
        # so 237 of 441 priced props, 54%, could never receive a model
        # probability no matter how good the lineups were.
        #
        # The rates were never missing. mlb_sources._PROP_THRESHOLDS has
        # carried runs 1-3, rbis 1-4, singles/doubles/triples and
        # hits_runs_rbis 1-4 all along, and empirical_batter_prop_rates()
        # returns them shrunk toward the league rate like every other family.
        # This loop simply never asked for them. Same failure as everything
        # else found today: computed, then discarded.
        #
        # EMPIRICAL ONLY, and that is a statement of fact rather than a
        # shortcut. Hits, total bases and home runs derive from a batter's own
        # plate-appearance distribution, so they have an honest modelled
        # counterpart. Runs and RBIs do not — they depend on whether teammates
        # reach base and drive him in, which no per-batter distribution
        # contains. Passing None here means _blend() uses the empirical rate
        # and records basis accordingly, instead of inventing a model term to
        # fill the column.
        ("runs", "Runs", [(0.5, 1), (1.5, 2), (2.5, 3)], None),
        ("rbis", "RBIs", [(0.5, 1), (1.5, 2), (2.5, 3), (3.5, 4)], None),
        ("hits_runs_rbis", "Hits+Runs+RBIs", [(0.5, 1), (1.5, 2), (2.5, 3), (3.5, 4)], None),
        # 2026-08-19 accuracy investigation: singles is NOT like runs/rbis
        # above -- unlike those (which depend on teammates and genuinely
        # have no per-batter distribution to draw from), a single is just
        # "exactly 1 base" in the SAME pa_dist already built for hits/
        # total_bases/home_runs (dist[1], directly analogous to home_runs'
        # dist[4] -- see p_at_least_singles's own docstring). This was the
        # one real gap in the "computed, then discarded" family: the
        # modelled component was never built for this market at all, not
        # merely never called. Doubles/triples deliberately left as-is
        # (fn=None, empirical only) -- large-scale calibration measurement
        # found singles alone showing a real, reportable miscalibration
        # (694 rows/130 dates, predicted 35.9% vs actual 44.8%); doubles
        # and triples did not, so extending the same fix to them would be
        # unevidenced, not principled.
        ("singles", "Singles", [(0.5, 1)],
         (lambda k: pp.p_at_least_singles(k, dist, pa)) if dist and pa else None),
        ("doubles", "Doubles", [(0.5, 1)], None),
        ("triples", "Triples", [(0.5, 1)], None),
    ]
    for stat, label, lines, fn in families:
        for line, need in lines:
            modelled = None
            if fn is not None:
                try:
                    modelled = float(fn(need))
                except Exception:
                    modelled = None
            empirical = emp_p(f"{stat}_{need}plus")
            # SHIPPED 2026-08-07, replacing the empirical/modelled blend for
            # stats that HAVE a modelled component (hits, total_bases,
            # home_runs -- fn is not None). This was an env-gated A/B toggle
            # earlier today; promoted after verifying it on the metric that
            # actually matters, not just the held-out log loss the original
            # 2026-08-06 audit used.
            #
            # THAT AUDIT found the shipped 60/40 empirical/modelled blend was
            # statistically indistinguishable from predicting the league rate
            # for every player, and that shrinking modelled toward the league
            # rate (k=0.5-0.6) and dropping empirical beat the blend on
            # held-out log loss with CIs excluding zero on 4 of 5 props. That
            # is a calibration claim; it says nothing about whether ranking
            # survives.
            #
            # TODAY'S VERIFICATION closes that gap, on the metric this
            # project actually validated the board against (66.0% top-10 vs
            # 46.5% random vs 60.0%/46.5% unshrunk -- gap held, if anything
            # widened, on a 5-day backtest slice). Calibration improved
            # sharply on the same run: expected_calibration_error 0.031 ->
            # 0.015, and the two bins holding most of the population went
            # from a real overconfidence gap to within +/-0.003 of exact.
            #
            # SCOPE, deliberately narrow: only stats with a modelled term.
            # runs/rbis/hits_runs_rbis/doubles/triples have no `fn` here at
            # all (see the families list above), so there is nothing to
            # shrink FROM -- they keep using empirical exactly as before,
            # which is itself already shrunk toward the league rate by
            # mlb_sources._apply_shrinkage, a real and already-good mechanism
            # this change does not touch.
            #
            # true_league_rates only, never base_rates -- base_rates can
            # still hold the slate-scoped r["league_p"] when the true rate is
            # absent (see its definition above), and shrinking toward THAT
            # would reintroduce exactly the circularity Check 1 found and
            # fixed elsewhere in this file. Falls back to the untouched
            # empirical/modelled blend when no true league rate exists for
            # this exact key, rather than losing coverage.
            MODEL_SHRINK_K = 0.5
            if fn is not None and modelled is not None:
                lg = true_league_rates.get(f"{stat}_{need}plus")
                if lg is not None:
                    prob = MODEL_SHRINK_K * lg + (1 - MODEL_SHRINK_K) * modelled
                    basis = "modelled_shrunk"
                else:
                    prob, basis = _blend(empirical, modelled)
            else:
                prob, basis = _blend(empirical, modelled)
            base = base_rates.get(f"{stat}_{need}plus")
            if prob is None:
                # NO PROP GOES UNSCORED, but ONLY from a real season-long
                # measurement -- true_league_rates, never base_rates (which
                # can hold the slate-scoped r["league_p"] instead; see its
                # definition above). A batter with no Statcast composition
                # and fewer than MIN_EMPIRICAL_GAMES games this season used
                # to fall out of every family here, which is what produced
                # 9 unscored total_bases candidates on 2026-08-07. Restricted
                # to true_league_rates specifically covers hits, total_bases,
                # home_runs, singles, doubles and triples (all six are real
                # entries in mlb_sources.league_base_rates(), verified live).
                # runs, rbis and hits_runs_rbis have no entry there at all,
                # so this correctly leaves them unscored rather than
                # manufacturing a number from a ~250-batter slate average --
                # absent stays absent instead of becoming a fake neutral
                # reading, same principle as everywhere else _sig is used.
                true_base = true_league_rates.get(f"{stat}_{need}plus")
                if true_base is None:
                    continue
                prob, basis = true_base, "league_only"
            # Real, per-line confidence interval -- ONLY when a genuine
            # empirical count backs THIS exact threshold and the displayed
            # probability actually reflects it (basis is "empirical" or a
            # real blend of it). "modelled_shrunk"/"modelled"/"league_only"
            # have no per-player empirical count behind the number shown at
            # all (modelled_shrunk explicitly drops the empirical term --
            # see MODEL_SHRINK_K's own comment), so a Wilson interval on the
            # raw rate table would describe a DIFFERENT number than the one
            # on screen. Direct instruction: "If a defensible CI does not
            # exist for a particular line, show no CI rather than inventing
            # or borrowing one." None, honestly, is the correct answer there.
            ci = None
            if basis in ("empirical", "blended"):
                r = raw_rates.get(f"{stat}_{need}plus")
                if r and r.get("n"):
                    lo, hi = _wilson_interval(r.get("hit", 0), r["n"])
                    ci = [round(lo, 4), round(hi, 4)]
            # STABLE LIFT REFERENCE, separate from and additive to base_rate/
            # lift above -- see stable_base_rate.py's own docstring for the
            # full rationale. base_rate/lift/prob are computed exactly as
            # before this addition; this is a second, independent number,
            # not a replacement. hits_runs_rbis/runs/rbis only -- the three
            # markets backtest/stable_baseline_challenger.py actually
            # measured. None for every other stat (no ledger exists, and
            # none was ever validated for one).
            lift_reference_rate, _stable_n = (
                sbr.stable_base_rate(stat, need, m.TODAY) if stat in sbr.SUPPORTED_STATS
                else (None, 0))
            options.append({
                "stat": stat, "line": line, "needs": need,
                "label": f"Over {line} {label}",
                "prob": round(prob, 4), "basis": basis,
                # How far above the LEAGUE rate for this exact line this
                # player sits. See the note on lift in write_markdown: the
                # probability says how likely the bet is, the lift says
                # whether the model actually has an opinion about it.
                "base_rate": base,
                "lift": None if base is None else round(prob - base, 4),
                "lift_reference_rate": lift_reference_rate,
                "stable_lift": (None if lift_reference_rate is None
                                else round(prob - lift_reference_rate, 4)),
                "empirical": None if empirical is None else round(empirical, 4),
                "modelled": None if modelled is None else round(modelled, 4),
                "ci": ci,
                # CI-provenance-honesty fix (P0-7): this is a real per-line
                # Wilson interval off THIS player's own empirical hit/n
                # count (see raw_rates immediately above) -- same source as
                # attach_reliability's own primary-line "player_empirical"
                # label, applied here at the per-line-option level so it
                # survives into select_best_by_category()'s by-category
                # board, not just the primary candidate.
                "ci_source": "player_empirical" if ci is not None else None,
            })
    options.sort(key=lambda o: o["prob"], reverse=True)
    return options


# Starts of slate-average evidence mixed into each pitcher's own first-inning
# record. Deliberately heavier relative to sample size than the batter prior:
# the L14 window gives most starters only 2-3 first innings, so almost all of
# these rates are near-worthless on their own.
#
# ── AUDIT, 2026-08-06: THIS IS THE LARGEST REMAINING ERROR ON THE BOARD. ──
# This value was chosen when fetch_first_inning_form pulled the L14 window and
# a pitcher had 2-3 first innings, where a prior of 5 starts genuinely did
# dominate. That is no longer true: the fetcher now pulls SEASON data, so a
# starter arrives with 16-25 first innings and a prior of 5 carries only
# 5/(20+5) = 20% of the weight. The pitcher's own rate now drives the number
# almost entirely -- and measured against real results, that rate is worthless.
#
# MEASURED on 185 starters / 3,231 real 2026 starts, first-inning runs scored
# off season Statcast. Non-parametric: shrink each pitcher's odd-numbered
# starts, score his even-numbered ones. Held-out mean log loss:
#
#     n0=0    0.80384        n0=35    0.59597
#     n0=2    0.63312        n0=52    0.59535
#     n0=5    0.61507  <-- SHIPPED    n0=90    0.59513   <-- best
#     n0=10   0.60443        n0=150   0.59520
#     n0=20   0.59820        league rate only  0.59567
#
# The shipped prior scores 0.01940 WORSE than giving every starter the league
# rate and ignoring his record entirely -- 95% CI [+0.00480, +0.03467] over
# 600 bootstrap resamples, excluding zero. A starter's own first-inning record
# carries essentially no predictive signal even across a full season: the best
# n0 (90) beats league-only by 0.00054, which is nothing. Two independent
# parametric fits agree: beta-binomial MLE n0 = 52.3, method of moments 23.6.
#
# WHAT IT COSTS TONIGHT. The rates now spread 0-75% across the slate, so this
# is not a rounding matter -- it changes which side gets picked:
#
#   raw 50% over 20 starts:  shipped NRFI 54.3%  | at n0=52 NRFI 65.6%
#   raw 65% over 20 starts:  shipped YRFI 57.7%  | at n0=52 NRFI 61.5%  (FLIPS)
#   raw 79% over 24 starts:  shipped YRFI 70.3%  | at n0=52 NRFI 55.7%  (FLIPS)
#
# So the most confident first-inning pick the board can currently produce is
# advertised at 70.3% on the YRFI side when the measured-correct read is the
# NRFI side at 55.7% -- wrong side, and 26 points of overstatement.
#
# APPLIED. The measurement above was left unapplied as a caller's decision;
# this is that decision, and the board made it easy. Running with n0=5 put two
# first-inning picks at the top of the 2026-08-06 board on 12 and 24 starts,
# graded D for sample size and carrying +0.7 and +0.3 points of lift -- while
# genuinely strong hits reads on 107 games with +12.0 lift sat beneath them.
# The prior was manufacturing confidence out of small samples and the ranking
# was faithfully promoting it.
#
# 52.0 is the beta-binomial MLE. Anything from 50 to 90 scores identically to
# three decimals, so the exact value is not delicate.
#
# What this means in practice, stated plainly: a starter's own first-inning
# record is worth almost nothing. At the best possible prior it beats quoting
# the league rate for everyone by 0.00054 of log loss. These picks will now
# cluster near the league rate, carry almost no lift, and mostly be removed by
# the positive-read floor -- which is the correct outcome, not a regression.
FI_PRIOR_STARTS = 52.0

# The rate at which a team scores in its half of the first inning. MEASURED,
# not assumed: 3,670 game-halves from this season's Statcast pull gave 0.2940,
# i.e. a team is held scoreless in the first 70.6% of the time. Recompute this
# from data rather than nudging it by hand.
LEAGUE_YRFI_RATE = 0.294


# ── Calibration ───────────────────────────────────────────────────────────
#
# REFIT 2026-08-12 against the real 2026-07-10..08-08 backtest (14,124 real
# graded rows, produced after this session's fixes -- combo_player_ids
# persistence, value_board's missing quality_control(), grade_value's
# settlement bug, the hard_hit_105 fair_test bug, and the CURRENT_WEIGHTS/
# _batter_options coverage gaps). The PREVIOUS calibrators were fit
# 2026-05-17..06-03, on an earlier scorer version, over four markets (hits,
# walks, strikeouts, first_inning_run) -- walks is now retired entirely
# (score_walk is never called) and first_inning_run no longer ships as its
# own board entry, so half of that fit was calibrating markets that no
# longer exist in this form. STALENESS IS A REAL RISK for exactly this
# reason: refit whenever scoring changes materially, not on a schedule.
#
# ONE CURVE PER MARKET THAT CAN SUPPORT ONE, not a blanket policy. Every
# candidate market was checked -- reliability table (real miscalibration
# pattern present?) AND held-out time-based-split evaluation (does a fitted
# curve actually help on dates it wasn't fit on?) -- and only kept where
# both agreed or the evidence was unambiguous:
#
#     market            n      ECE(raw)   held-out result           kept?
#     hits            2960     0.008      neutral (already accurate)  yes
#     hits_runs_rbis  3480     0.017      real improvement            yes
#     strikeouts       725     0.073      real improvement (largest    yes
#                                          miscalibration measured --
#                                          overconfident 0.76->0.57 at
#                                          the top bin)
#     hard_hit_105    5914     0.018      tiny held-out regression on   yes
#                                          a large sample (noise-level,
#                                          not a real signal) against a
#                                          real, consistent raw pattern
#     pitcher_outs      625    0.023      held-out got WORSE on a thin  NO
#                                          train split (435 rows) --
#                                          real diagnostic pattern
#                                          exists but the fit is not
#                                          demonstrated stable; deferred
#                                          pending more backtest data
#     nrfi_combined     326    0.007      predictions cluster almost    NO
#                                          entirely in one 0.50-0.62
#                                          bin -- there is effectively
#                                          no variance for a curve to
#                                          learn from, matching this
#                                          market's own known behaviour
#                                          (_build_combined_nrfi's own
#                                          docstring: two already-shrunk
#                                          reads combine to ~coinflip
#                                          for nearly every game)
#     singles            94    0.005      too few rows (down to n=2 in  NO
#                                          some bins) to trust any fit
#
# RE-CHECKED, 2026-08-12, on a fresh 33-date backtest (2026-07-10..08-11,
# more than the run above) specifically to see whether pitcher_outs/singles
# now have enough data to fit -- per this project's own discipline, "more
# data available" is exactly the trigger that comment called for, not a
# reason to leave the question stale. Result: NEITHER changes.
#   pitcher_outs (695 rows, up from 625): held-out brier_improvement -0.00447,
#     log_loss_improvement -0.00865 -- both still NEGATIVE, i.e. the fitted
#     curve is still worse than raw on data it wasn't trained on. Same
#     verdict, now on a larger sample: still NO.
#   singles (4 rows in THIS run, even fewer than the 94 above -- this
#     backtest's candidate mix simply produces very few singles candidates)
#     -- nowhere close to fittable. Still NO.
# Re-check again once either market's row count grows by an order of
# magnitude, not on every backtest run.
#
# NO GLOBAL/POOLED FALLBACK, by choice, not oversight. backtest/
# calibrator.json (the old pooled curve) is deleted rather than refit: the
# 2026-08-05 audit that first built per-market curves already measured the
# pooled curve actively HARMING its two largest markets by averaging away
# corrections that run in opposite directions, and this refit's own global
# fit reproduced that same near-neutral-to-negative result on fresh data.
# Markets this backtest never scored a candidate for at all (runs, rbis,
# doubles, triples, home_runs, total_bases, combined_strikeouts -- see
# generate_picks.py's audit notes on _pick_line favouring hits/hits_runs_rbis
# in this window) now ship the raw, uncalibrated probability instead of a
# weak correction borrowed from unrelated markets. Raw is honest about what
# it is; a bad correction is not.
#
# PLATT RATHER THAN ISOTONIC, chosen deliberately against the scoreboard.
# Isotonic fit the training set marginally better but it is a step function
# defined only across the probabilities it was trained on, and it flatlines
# at the boundary outside that range. Platt is a smooth sigmoid that degrades
# gracefully to inputs the fit never saw, which matters here because scoring
# keeps changing. The raw uncalibrated figure is kept on every pick
# (raw_hit_probability) so the two can always be compared.
CALIBRATOR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "backtest", "calibrator.json")
CALIBRATORS_BY_MARKET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "backtest", "calibrators_by_market.json")
RELIABILITY_BANDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "backtest", "reliability_bands.json")

_RELIABILITY_BANDS_CACHE = None


def load_reliability_bands():
    """Load backtest/reliability_bands.json once per process, or None if it
    doesn't exist yet (a fresh checkout, or before the first
    reliability_bands.py build). Never fatal -- a board without historical
    reliability bands falls back to today's existing behavior (no prob_ci
    for modelled_shrunk/league_only/uncalibrated lines), exactly as before
    this existed."""
    global _RELIABILITY_BANDS_CACHE
    if _RELIABILITY_BANDS_CACHE is not None:
        return _RELIABILITY_BANDS_CACHE
    if not os.path.exists(RELIABILITY_BANDS_PATH):
        _RELIABILITY_BANDS_CACHE = {}
        return _RELIABILITY_BANDS_CACHE
    try:
        with open(RELIABILITY_BANDS_PATH) as f:
            data = json.load(f)
        _RELIABILITY_BANDS_CACHE = data.get("bands") or {}
    except Exception as e:
        m.warn(f"Reliability bands unreadable ({e}) — no historical prob_ci fallback")
        _RELIABILITY_BANDS_CACHE = {}
    return _RELIABILITY_BANDS_CACHE


# A cell needs at least this many real graded historical rows before its
# measured spread is trusted as a genuine market-level answer -- matches
# backtest/reliability_bands.py's own MIN_BAND_N, restated here rather than
# imported so this module never depends on importing something under
# backtest/ at runtime (mirrors load_calibrator()'s own sys.path dance,
# done deliberately rather than reused, for the same reason).
MIN_RELIABILITY_BAND_N = 150


def historical_prob_ci(stat, needs, prob):
    """A real, historically-measured confidence interval for a probability
    this pipeline could not otherwise build one for -- see backtest/
    reliability_bands.py's own module docstring for the full method and
    why it is a defensible answer, not an invented one.

    Returns [lo, hi] anchored to THIS candidate's own point estimate `prob`
    (never replacing it -- only bracketing it), using the REAL historical
    spread AND bias measured for real graded predictions that landed in the
    same (stat, needs, probability bucket) cell:

        halfwidth = half the Wilson interval width on the bucket's own
                    (actual hits, n) count -- real sampling uncertainty on
                    how well that bucket's true rate is actually known.
        bias      = bucket's actual hit rate minus its predicted mean --
                    when negative (the model has historically been
                    overconfident in this exact bucket), it pulls the
                    pessimistic end down further; when positive, it widens
                    the optimistic end instead of tightening the
                    pessimistic one, so a historically-UNDERconfident
                    bucket never gets a narrower interval than the honest
                    sampling noise alone would justify.

    Returns None when no cell exists for this (stat, needs) or the
    matching bucket never reached MIN_RELIABILITY_BAND_N real rows --
    failing exactly as closed as an absent prob_ci does today. A market
    with no backtest coverage yet (or not enough) gets no interval, same
    as before this function existed."""
    if prob is None or needs is None:
        return None
    # backtest/rows_backfill.jsonl (and therefore reliability_bands.json)
    # is keyed on backtest/engine.py's PROP_TYPE_BY_STAT schema vocabulary,
    # which differs from this module's own candidate stat name in exactly
    # one case: "home_runs" here is "home_run" there (singular -- matches
    # grade_results.py's own market vocabulary). Every other stat name is
    # identical in both places (verified directly against PROP_TYPE_BY_STAT).
    band_stat = "home_run" if stat == "home_runs" else stat
    bands = load_reliability_bands()
    cell_group = bands.get(f"{band_stat}_{int(needs)}")
    if not cell_group:
        return None
    bucket = round(min(max(int(float(prob) // 0.05) * 0.05, 0.0), 0.95), 2)
    cell = cell_group.get(f"{bucket:.2f}")
    if not cell or cell.get("n", 0) < MIN_RELIABILITY_BAND_N:
        return None
    halfwidth = (cell["wilson_hi"] - cell["wilson_lo"]) / 2.0
    bias = cell["bias"]
    lo = float(prob) + min(0.0, bias) - halfwidth
    hi = float(prob) + max(0.0, bias) + halfwidth
    return [round(max(0.0, lo), 4), round(min(1.0, hi), 4)]


def load_calibrator():
    """Load the calibrators, or None if unavailable. Never fatal: an
    uncalibrated board is worse than a calibrated one but far better than no
    board at all.

    Returns (per_market_dict, global_fallback). A market with its own fitted
    curve uses it; anything else falls back to the pooled curve, which is
    still better than nothing for a market that had too few rows to fit."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest"))
        import calibration as _cal
        per_market = {}
        if os.path.exists(CALIBRATORS_BY_MARKET_PATH):
            try:
                per_market = _cal.load_calibrators(CALIBRATORS_BY_MARKET_PATH) or {}
            except Exception as e:
                m.warn(f"Per-market calibrators unreadable ({e}) — using the pooled curve")
        glob = None
        if os.path.exists(CALIBRATOR_PATH):
            glob = _cal.Calibrator.load(CALIBRATOR_PATH)
        if not per_market and glob is None:
            return None
        return (per_market, glob)
    except Exception as e:
        m.warn(f"Calibrator unavailable ({e}) — shipping raw model probabilities")
        return None


def _in_calibrator_support(prob, fn):
    """Whether `prob` falls in a bin fn's own fitted support_bins marks
    supported. Returns None when fn carries no support_bins at all (an
    older calibrator artifact, or a bug -- absence of support information
    is not evidence either way, and the caller decides how to treat it,
    same discipline as calibration.in_support())."""
    support_bins = getattr(fn, "meta", {}).get("support_bins")
    if not support_bins:
        return None
    for b in support_bins:
        if b["lo"] <= prob < b["hi"] or (prob >= 1.0 and b["hi"] >= 1.0):
            return bool(b["supported"])
    return False


def _calibrate_one(prob, stat, per_market, glob):
    """The single calibration lookup+apply, factored out so the primary line
    and every alternate line in a candidate's own line_options use IDENTICAL
    logic keyed to their OWN stat -- never the candidate's primary stat.

    SUPPORT-BOUNDARY GATE, 2026-08-19 audit: a fitted curve is only real
    evidence where its own fitting data actually had rows (see
    backtest/calibration.py's compute_support_bins docstring for the full
    reasoning and the strikeouts-curve incident that found this). When
    `prob` falls in a bin the curve's own .meta["support_bins"] marks
    unsupported, this returns the probability UNCHANGED with
    calibrated_by=None -- honestly declining to transform it, never
    manufacturing a correction with zero evidence behind it, and never
    silently dropping the candidate either (raw stays a real, shippable
    number, matching this codebase's own established "raw is honest about
    what it is" convention for every other uncalibrated market). This is a
    real, distinguishable outcome from "no calibrator exists for this
    market at all" -- callers that set raw_hit_probability/calibrated_by
    unconditionally whenever this returns a non-None probability correctly
    record "evaluated, found unsupported" rather than silence.

    Returns (calibrated_prob, calibrated_by) where calibrated_by is None
    for BOTH "no calibrator applies" and "probability outside this curve's
    support" -- callers distinguish the two by whether the returned
    probability actually differs from the input (see apply_calibration's
    own comment on this)."""
    if prob is None:
        return None, None
    fn = per_market.get(stat) or glob
    if fn is None:
        return None, None
    if _in_calibrator_support(prob, fn) is False:
        return prob, None
    try:
        cp = float(fn(prob))
    except Exception:
        return None, None
    return round(cp, 4), (stat if stat in per_market else "pooled")


def apply_calibration(candidates, calibrator):
    """Replace each stated probability with its calibrated value, keeping the
    raw one alongside for comparison. Each market uses its own curve where one
    was fitted, falling back to the pooled curve otherwise.

    ALSO calibrates every alternate line in c["line_options"] AND
    c["alternatives"] -- the exact audit finding this fixes:
    select_best_by_category() (and select_moonshots and value_board.py, all
    real consumers of line_options) priced and classified every alternate
    line off its raw, never-calibrated probability, because this function
    used to touch only the primary line's hit_probability.
    select_shadow_tracking() reads c["alternatives"] the same way for its
    own recorded-but-never-bet probabilities -- a SEPARATE list of dict
    objects from line_options (see _keep_options() vs the raw `opts` slice),
    so it needs its own pass, not just line_options' fix incidentally
    covering it. Leaving it raw would have meant every OTHER category
    (main, moonshot, best_of_category) got corrected while shadow-tracking's
    own recorded probability silently stayed on the old, biased number --
    exactly the apples-to-oranges comparison this project's own
    record/measure/promote discipline exists to prevent when a shadow
    signal is later evaluated for promotion.

    Each option/alternative is calibrated against its OWN stat (a Home Runs
    alternate on a Hits-primary candidate must use the Home Runs curve,
    never Hits'), never the candidate's primary stat, and never borrows the
    primary line's raw_hit_probability/calibrated_by. This is the ONLY call
    site of apply_calibration() in the whole codebase (see
    score_slate()/_build_and_score()), so every consumer -- live dashboard,
    static pipeline, value_board.py, moonshots, shadow tracking -- sees the
    same corrected data in one pass; nothing here can calibrate a
    probability twice."""
    if calibrator is None:
        return candidates
    per_market, glob = calibrator
    used = defaultdict(int)

    def _calibrate_option_list(opts):
        for opt in opts or []:
            ocp, oby = _calibrate_one(opt.get("prob"), opt.get("stat"), per_market, glob)
            if ocp is None:
                continue
            opt["raw_prob"] = opt["prob"]
            opt["prob"] = ocp
            opt["calibrated_by"] = oby
            if opt.get("base_rate") is not None:
                opt["lift"] = round(opt["prob"] - opt["base_rate"], 4)
            # oby is None both when no calibrator applies to this stat AND
            # when the calibrator applies but this exact probability sat
            # outside its support region (_calibrate_one returns the prob
            # UNCHANGED in that case, 2026-08-19 support-boundary audit) --
            # in neither case did the point estimate actually move, so the
            # pre-existing ci is still describing the same number shown on
            # screen and must NOT be suppressed. Only a REAL transform
            # (oby is not None) invalidates it. Guards against the exact
            # regression this would otherwise be: an out-of-support
            # evaluation silently nulling out a perfectly defensible CI for
            # a probability that never actually changed.
            if oby is not None:
                # H1, 2026-08-19: opt["ci"] (when present) was computed by
                # _batter_options BEFORE this function ever runs -- a Wilson
                # interval on this exact line's raw empirical count, honest for
                # the RAW probability it sat beside at the time. Now that this
                # option's probability has been replaced with a Platt-calibrated
                # value, that interval describes a different number than
                # opt["prob"]. Same rule as attach_reliability's primary-line
                # fix, applied identically here for parity: no defensible
                # calibrated-interval method exists yet, so withhold rather than
                # keep a scale-mismatched interval or fabricate one by pushing
                # the endpoints through a curve whose own uncertainty (severe in
                # under-supported regions, e.g. the strikeouts tail) this would
                # silently ignore.
                #
                # 2026-08-24 second-CI-path fix: this is the OTHER call site
                # (besides attach_reliability's primary line) that nulls a CI
                # at exactly this same calibration-scale boundary -- and it
                # was missed when the historical-band fallback was first
                # added, which is why hits/hits_runs_rbis/strikeouts stayed
                # 86-100% CI-less on select_best_by_category()'s per-line
                # path (the one that actually populates most of the live
                # board) even after that fix merged. Same fallback, same
                # fail-closed default (None) when no reportable band exists
                # yet -- this does not lower the MIN_RELIABILITY_BAND_N floor
                # or change anything else about what counts as "supported."
                opt["ci"] = historical_prob_ci(opt.get("stat"), opt.get("needs"), opt["prob"])
                # CI-provenance-honesty fix (P0-7): this replaces whatever
                # ci_source the option carried (a player_empirical interval
                # describing the RAW probability, now stale post-calibration)
                # with the real source of the new one -- a market/bucket-level
                # historical band, same label attach_reliability's own
                # fallback uses.
                opt["ci_source"] = "historical_reliability_band" if opt["ci"] is not None else None
                used[oby] += 1

    for c in candidates:
        stat = (c.get("projection") or {}).get("stat")
        cp, by = _calibrate_one(c.get("hit_probability"), stat, per_market, glob)
        if cp is not None:
            c["raw_hit_probability"] = c["hit_probability"]
            c["hit_probability"] = cp
            c["calibrated_by"] = by
            # Lift has to move with it, or the two disagree about the same pick.
            if c.get("base_rate") is not None:
                c["lift"] = round(c["hit_probability"] - c["base_rate"], 4)
            if by is not None:
                used[by] += 1
        _calibrate_option_list(c.get("line_options"))
        _calibrate_option_list(c.get("alternatives"))
    if used:
        detail = ", ".join(f"{k}:{v}" for k, v in sorted(used.items()))
        print(f"    Calibration applied ({detail})")
    return candidates

def attach_hit_probabilities(candidates, comp_table, emp_batters, emp_pitchers,
                             league_rates=None, k_prices=None):
    """Give every candidate a real chance-of-cashing number, and let the
    batter props re-choose their threshold to maximise it.

    Anything this cannot price keeps its original prop and gets a null
    probability rather than a guess -- an invented number here would rank
    against real ones, which is worse than an honest gap."""
    # The slate's own average first-inning scoring rate, start-weighted. Using
    # tonight's starters rather than a hardcoded constant keeps the prior
    # tracking the real run environment.
    # THE PRIOR MUST NOT BE COMPUTED FROM THE THING IT IS CORRECTING.
    #
    # This used to average the candidates' own first-inning rates and shrink
    # them toward that. Those rates came from 2-start samples, and
    # score_first_inning only emits a candidate when the read already looks
    # good, so the "league average" was a selection-biased average of the very
    # numbers it was supposed to discipline. It reported that a team is held
    # scoreless in the first 83.1% of the time.
    #
    # Measured from the real season pull (3,670 game-halves): a team scores in
    # its half of the first 29.40% of the time, so the true scoreless rate is
    # 70.60%. The self-referential prior overstated every first-inning pick by
    # 12.5 points, and eight of them were on the board.
    slate_yrfi = LEAGUE_YRFI_RATE

    for c in candidates:
        pid = c.get("player_id")
        stat = (c.get("projection") or {}).get("stat")

        if c.get("type") == "batter" and stat == "total_bases":
            opts = _batter_options(c, comp_table.get(pid), emp_batters.get(pid),
                                   league_rates)
            best = _pick_line(opts)
            if not best:
                c["hit_probability"] = None
                continue
            # The projection field is what grade_results.py grades against, so
            # it has to be the line we are actually recommending. Previously it
            # held a continuous projection while the label held a fixed line,
            # and the two could disagree -- a double could be graded a miss
            # against a 2.11 threshold on an "Over 1.5" recommendation.
            c["prop"] = best["label"]
            c["projection"] = {"stat": best["stat"], "value": best["line"],
                               "needs": best["needs"]}
            c["line_options"] = _keep_options(opts, best["stat"])
            c["hit_probability"] = best["prob"]
            c["probability_basis"] = best["basis"]
            c["base_rate"] = best.get("base_rate")
            c["lift"] = best.get("lift")
            # Additive, separate from base_rate/lift above -- see
            # stable_base_rate.py's own docstring. Present (non-None) only
            # for hits_runs_rbis/runs/rbis when a real season-to-date
            # reference exists; None otherwise, which recommendation.py
            # treats as "unavailable, fall back to current behavior."
            c["lift_reference_rate"] = best.get("lift_reference_rate")
            c["stable_lift"] = best.get("stable_lift")
            c["probability_detail"] = {"empirical": best["empirical"],
                                       "modelled": best["modelled"]}
            c["alternatives"] = [o for o in opts if o is not best][:3]

        elif stat == "stolen_base":
            emp = emp_batters.get(pid) or {}
            comp = comp_table.get(pid) or {}
            empirical = None
            r = (emp.get("rates") or {}).get("stolen_bases_1plus")
            if r and emp.get("games", 0) >= MIN_EMPIRICAL_GAMES:
                empirical = r.get("p_hat", r["p"])
            modelled = None
            if comp.get("attempt_rate") and c.get("projected_pa"):
                tob = (comp.get("obp") or 0.31) * c["projected_pa"]
                modelled = pp.p_stolen_base(tob, comp["attempt_rate"], comp["success_rate"])
            # AUDIT, 2026-08-12: CHECKED against the same held-out methodology
            # that found hits/TB/HR/strikeouts broken -- stolen_base is NOT in
            # the same state, so it is NOT changed. 253 regulars (40+ real
            # games), each split 60% train / 40% test chronologically by their
            # own games, 10,061 real held-out test games. Held-out mean log
            # loss: shipped 60/40 blend 0.22329, empirical-only 0.22258,
            # model-only 0.26824 (confirms the model alone IS overconfident,
            # matching p_stolen_base's own David-Hamilton-+7.2-points note),
            # league-only 0.23960, model-shrunk-toward-league (k=0.5, the fix
            # that worked for hits/TB/HR) 0.22389. Unlike hits/TB/HR, the
            # shipped blend clearly and significantly beats league-only
            # (paired bootstrap, 600 resamples: -0.01631, 95% CI [-0.02054,
            # -0.01145], excluding zero) -- this is a real signal, not a
            # coin flip dressed up as one. The hits/TB/HR fix (shrink model,
            # drop empirical) does NOT help here (0.22389 vs 0.22329, i.e.
            # slightly worse) because the failure mode is different: there
            # the BLEND was the problem; here the MODEL alone is the weak
            # input but the blend's 60% empirical weighting already mostly
            # compensates. Left as-is -- not every checked signal needs a
            # fix, and shipping one anyway on a difference this small (within
            # a single split's noise) would be exactly the kind of change
            # this project's own discipline warns against.
            prob, basis = _blend(empirical, modelled)
            c["hit_probability"] = None if prob is None else round(prob, 4)
            c["probability_basis"] = basis
            c["probability_detail"] = {
                "empirical": None if empirical is None else round(empirical, 4),
                "modelled": None if modelled is None else round(modelled, 4)}
            # attach_market_prices() keys on (stat, needs); this projection
            # has carried "value" since it was created (score_stolen_base,
            # above) but never "needs", so the lookup key was always
            # (stat, None) and every one of these candidates was skipped
            # before a price could ever be checked. p_stolen_base models
            # P(>=1 steal), which is FanDuel's TO_RECORD_A_STOLEN_BASE line --
            # needs=1, not the 2+ market.
            c["projection"]["needs"] = 1

        elif stat == "strikeouts":
            emp = emp_pitchers.get(pid) or {}
            rates = emp.get("rates") or {}
            bf, kr = c.get("expected_bf"), c.get("k_rate")
            opts = []
            for t in (4, 5, 6, 7, 8):
                empirical = None
                r = rates.get(f"strikeouts_{t}plus")
                if r and emp.get("starts", 0) >= 5:
                    empirical = r.get("p_hat", r["p"])
                modelled = None
                if bf and kr:
                    try:
                        modelled = float(pp.p_at_least_strikeouts(t, bf, kr))
                    except Exception:
                        modelled = None
                base = ((league_rates or {}).get(f"strikeouts_{t}plus")
                        or (r or {}).get("league_p"))
                # Checked live against the same 5-day backtest slice used to
                # validate the hits/total_bases/home_runs fix: strikeouts'
                # shipped 60/40 blend has a NEGATIVE Brier skill score
                # (-0.144 -- worse than always predicting the base rate) and
                # an expected calibration error of 0.184, both far worse than
                # anything found in the batter props. [0.6-0.7) and
                # [0.7-0.8) are large enough samples (n=34, n=54) to trust,
                # and both show real overconfidence (gaps of -0.16, -0.20).
                # Same fix, same reasoning as the batter one: shrink modelled
                # toward the TRUE league rate (league_rates specifically,
                # never r["league_p"] -- see Check 1) and drop empirical.
                # Pitcher game logs are thin (starts, not games), which is
                # likely exactly why empirical hurts more here than it did
                # for batters.
                STRIKEOUT_SHRINK_K = 0.5
                lg = (league_rates or {}).get(f"strikeouts_{t}plus")
                if modelled is not None and lg is not None:
                    prob = STRIKEOUT_SHRINK_K * lg + (1 - STRIKEOUT_SHRINK_K) * modelled
                    basis = "modelled_shrunk"
                else:
                    prob, basis = _blend(empirical, modelled)
                if prob is None:
                    continue
                opts.append({"stat": "strikeouts", "line": t - 0.5, "needs": t,
                             "prob": round(prob, 4),
                             "basis": basis,
                             "base_rate": base,
                             "lift": None if base is None else round(prob - base, 4),
                             "empirical": None if empirical is None else round(empirical, 4),
                             "modelled": None if modelled is None else round(modelled, 4)})
            opts.sort(key=lambda o: o["prob"], reverse=True)
            # THE SAME LINE-SELECTION BUG score_pitcher_outs already fixed
            # (see its own docstring), never generalized here. attach_
            # market_prices' "strikeouts" branch only attaches a real price
            # when the recommended `needs` equals FanDuel's own posted
            # line's `needs` -- but _pick_line chooses purely by model
            # probability/lift among t in 4..8, with zero awareness of what
            # FanDuel actually offers. Verified live 2026-08-13: k_prices
            # had a real line for all 6 of that night's starters, yet only
            # 1 matched, because the model's chosen threshold rarely lined
            # up with the book's. When the real line is available and one
            # of our computed thresholds matches it, use that -- there is
            # only one number FanDuel is actually offering money on, same
            # reasoning as pitcher_outs. Only fall back to _pick_line's
            # search when no real line can be matched (odds not posted, a
            # name-match miss, or a line outside 4..8).
            real_line = None
            if k_prices:
                import odds_fanduel as _fd
                real_line = k_prices.get(_fd.normalize_name(c.get("name")))
            best = None
            if real_line and real_line.get("needs") is not None:
                best = next((o for o in opts if o["needs"] == real_line["needs"]), None)
            if best is None:
                best = _pick_line(opts)
            if best:
                c["prop"] = f"Over {best['line']} Strikeouts"
                c["projection"] = {"stat": "strikeouts", "value": best["line"],
                                   "needs": best["needs"]}
                c["line_options"] = _keep_options(opts, "strikeouts")
                c["hit_probability"] = best["prob"]
                c["probability_basis"] = best["basis"]
                # Strikeout props were the one market shipping without a base
                # rate, which made them incomparable to everything else on the
                # board -- and they were ranked 1st, 2nd and 5th. The top pick
                # sat at 91.3% with no indication of how much of that was the
                # market being easy: a starter clearing 3.5 strikeouts is a
                # high base rate before anyone looks at WHICH starter.
                c["base_rate"] = best.get("base_rate")
                c["lift"] = best.get("lift")
                c["probability_detail"] = {"empirical": best["empirical"],
                                           "modelled": best["modelled"]}
                c["alternatives"] = [o for o in opts if o is not best][:3]
            else:
                c["hit_probability"] = None

        elif stat == "first_inning_run":
            # NRFI/YRFI is already a frequency by construction -- score_first_inning
            # counts real first innings from real starts. Two things still have to
            # be done to it before it is a probability.
            #
            # SCALE. yrfi_rate is stored as a PERCENTAGE (100.0, not 1.0). Reading
            # it as a fraction produced a pick advertised at "10000% to hit", and
            # the NRFI side computed 1 - 100 = -99, which sorted last instead of
            # first. Both were visible the moment the board was ranked on it.
            #
            # SAMPLE. A starter with 2 first innings and no runs allowed has a
            # measured YRFI rate of exactly 0%, which reads as a 100% certain
            # NRFI. That is not a strong pick, it is an absence of evidence, and
            # ranking on probability puts it straight at the top of the board --
            # five of tonight's first six picks were 2-start NRFI leans at a
            # literal 100%. Shrunk toward the slate's own average the same way
            # every other empirical rate here is.
            yrfi_pct = (c.get("projection") or {}).get("value")
            n_starts = (c.get("signals") or {}).get("fi_n_starts") or 0
            if yrfi_pct is None:
                c["hit_probability"] = None
            else:
                raw = max(0.0, min(1.0, float(yrfi_pct) / 100.0))
                shrunk = ((raw * n_starts + FI_PRIOR_STARTS * slate_yrfi)
                          / (n_starts + FI_PRIOR_STARTS)) if (n_starts + FI_PRIOR_STARTS) else slate_yrfi
                # PICK THE SIDE FROM THE SHRUNK RATE, NOT THE RAW ONE.
                #
                # score_first_inning commits to a side at raw yrfi_rate >= 38%,
                # but the number this board is RANKED on is the shrunk rate, and
                # shrinkage moves the 50/50 point a long way. Solving
                # (raw*n + 5*slate)/(n+5) = 0.5 at slate=0.27, the raw rate the
                # YRFI side actually needs is:
                #     n=2 starts 107.5%  n=3 88.3%  n=4 78.8%  n=5 73.0%  n=8 64.4%
                # against a lean that flips at 38%. Every raw rate in between
                # produced a pick labelled YRFI and advertised at UNDER 50% while
                # the NRFI side of the identical number sat above it. Enumerated
                # over raw in {0,20,33,38,40,50,60,67,75,80,100}% x n in 2..6:
                # 31 of 55 combinations recommended the losing side -- e.g. 2 of
                # 4 starts scored on shipped as "YRFI, 37.2%" when "NRFI, 62.8%"
                # was the same evidence read the right way round. At n=2 the YRFI
                # side is unreachable at ANY raw rate, so every 2-start YRFI lean
                # was wrong by construction.
                is_yrfi = shrunk >= 0.5
                c["lean"] = "YRFI" if is_yrfi else "NRFI"
                opp = c.get("fi_opp_team")
                if opp:
                    c["prop"] = (f"{opp} to score in the 1st" if is_yrfi
                                 else f"{opp} scoreless in the 1st")
                c["hit_probability"] = round(shrunk if is_yrfi else 1.0 - shrunk, 4)
                c["base_rate"] = round(LEAGUE_YRFI_RATE if is_yrfi
                                       else 1.0 - LEAGUE_YRFI_RATE, 4)
                c["lift"] = round(c["hit_probability"] - c["base_rate"], 4)
                c["probability_basis"] = "empirical"
                c["probability_detail"] = {
                    "empirical": round(raw if is_yrfi else 1.0 - raw, 4),
                    "modelled": None}
        else:
            c.setdefault("hit_probability", None)

    combined_nrfi = _build_combined_nrfi(candidates)
    # first_inning_run candidates never become a standalone board pick --
    # verified live against FanDuel's raw API (every tab, two different
    # games) that no one-sided "Team X scoreless in the 1st" market exists;
    # see _build_combined_nrfi's own docstring. Their shrunk hit_probability
    # was needed as nrfi_combined's input (just computed above, reading the
    # candidates list before this filter runs) -- that's the only thing
    # they're for now, so they're dropped here rather than ever reaching
    # ranking, the category board, persistence, or grading as their own pick.
    candidates[:] = [c for c in candidates
                     if (c.get("projection") or {}).get("stat") != "first_inning_run"]
    candidates.extend(combined_nrfi)
    return candidates


def _build_combined_nrfi(candidates):
    """The REAL, books-comparable NRFI/YRFI -- BOTH starters' halves of the
    1st combined, not the one-sided "this team scores off this pitcher" read
    score_first_inning reports per starter (see its own docstring: the prop
    text is deliberately "Team X to/scoreless in the 1st", never "NRFI",
    for exactly this reason -- real books require BOTH teams held scoreless).

    Built here, AFTER the per-side first_inning_run candidates above already
    have their properly shrunk hit_probability (FI_PRIOR_STARTS=52, see the
    2026-08-06 audit above this function) -- this reuses that shrunk number
    rather than recomputing from the raw rate, so the combined read inherits
    the same sample-size discipline instead of a second, looser one.

    HONEST EXPECTATION, STATED UP FRONT because it will look strange
    otherwise: that same audit already measured that a starter's own
    first-inning record carries close to zero predictive signal even across
    a full season (the best possible shrinkage prior beats "ignore the
    pitcher, use the league rate" by 0.00054 of log loss -- nothing).
    Combining two numbers that are each already shrunk almost to the league
    rate produces a combined number that will itself sit close to
    (1 - LEAGUE_YRFI_RATE) ** 2 ~= 0.498 for nearly every game, regardless of
    which two starters are on the mound. That is not a bug introduced here --
    it is the honest, correct consequence of what was already measured, and
    it is a large part of why real books price NRFI close to a coinflip too.
    This function exists so a customer asking for "the NRFI" gets a real,
    correctly-computed number for the market they actually mean, not to
    manufacture a strong pick where the evidence has none.

    Needs both starters confirmed with a real first-inning read; a TBD
    starter or a pitcher with no first-inning form leaves this game out
    rather than inventing a rate for the missing half."""
    by_game = defaultdict(dict)
    for c in candidates:
        stat = (c.get("projection") or {}).get("stat")
        if stat != "first_inning_run" or c.get("hit_probability") is None:
            continue
        if c.get("side") in ("away", "home"):
            by_game[c.get("game_pk")][c["side"]] = c

    def _p_opp_scores(c):
        # hit_probability is for whichever side (YRFI/NRFI) score_first_inning
        # picked; reconstruct P(the opposing lineup scores) independent of
        # which side that happened to be.
        return c["hit_probability"] if c.get("lean") == "YRFI" else 1.0 - c["hit_probability"]

    combined = []
    for game_pk, sides in by_game.items():
        away_c, home_c = sides.get("away"), sides.get("home")
        if not away_c or not home_c:
            continue  # both starters need a real read -- no guessing the other half

        # away_c is the AWAY starter's own read -- he pitches to HOME batters
        # in the bottom of the 1st, so this is P(home team scores). home_c is
        # the HOME starter's read -- he pitches to AWAY batters in the top of
        # the 1st, so this is P(away team scores).
        n_away_sp_starts = int((away_c.get("signals") or {}).get("fi_n_starts") or 0)
        n_home_sp_starts = int((home_c.get("signals") or {}).get("fi_n_starts") or 0)
        p_home_team_scores = _p_opp_scores(away_c)
        p_away_team_scores = _p_opp_scores(home_c)
        p_nrfi = (1.0 - p_home_team_scores) * (1.0 - p_away_team_scores)
        p_yrfi = 1.0 - p_nrfi
        lean = "YRFI" if p_yrfi >= 0.5 else "NRFI"
        hit_probability = p_yrfi if lean == "YRFI" else p_nrfi

        n_min = min(n_away_sp_starts, n_home_sp_starts)
        sample_penalty = max(0, (5 - n_min) * 15)
        score = clamp(hit_probability * 100 - sample_penalty)
        if n_min < 3:
            score = min(score, 55)
        base_rate = round((1 - LEAGUE_YRFI_RATE) ** 2 if lean == "NRFI"
                          else 1 - (1 - LEAGUE_YRFI_RATE) ** 2, 4)

        combined.append({
            "type": "game",
            "name": f"{away_c['team']} @ {home_c['team']} — 1st Inning (Both Teams)",
            "player_id": f"nrfi_{game_pk}", "team": None, "side": "both",
            "matchup": away_c.get("matchup"), "game_pk": game_pk,
            "prop": ("A run scores in the 1st (either team)" if lean == "YRFI"
                     else "No runs in the 1st (both teams)"),
            "projection": {"stat": "nrfi_combined", "value": round(hit_probability * 100, 1)},
            "lean": lean, "hit_probability": round(hit_probability, 4),
            "score": round(score, 1),
            "confidence": "High" if score >= 70 and n_min >= 3 else ("Medium" if score >= 55 else "Low"),
            "notable_signals": 1 if (hit_probability >= 0.65 and n_min >= 3) else 0,
            "signals": {"home_team_scores_p": round(p_home_team_scores, 4),
                       "away_team_scores_p": round(p_away_team_scores, 4),
                       "fi_n_starts": float(n_min)},
            "why": [f"{home_c['team']} scores off {away_c['name']} (away SP) in the bottom 1st: "
                    f"{round(p_home_team_scores * 100, 1)}% (shrunk, {n_away_sp_starts} starts)",
                    f"{away_c['team']} scores off {home_c['name']} (home SP) in the top 1st: "
                    f"{round(p_away_team_scores * 100, 1)}% (shrunk, {n_home_sp_starts} starts)",
                    "This is the real both-teams NRFI/YRFI market -- combines both "
                    "starters' halves of the 1st, not a one-sided team read."],
            "watchouts": (["Thin first-inning sample on at least one starter"]
                         if n_min < 3 else []),
            "base_rate": base_rate, "lift": round(hit_probability - base_rate, 4),
            "probability_basis": "combined_shrunk",
            "probability_detail": {"empirical": None, "modelled": None},
            "raw_hit_probability": None, "calibrated_by": None, "prob_ci": None,
            "prob_ci_source": None,
            "sample_n": n_min, "reliability": None,
            "market_odds": None, "market_implied": None, "market_edge": None,
            "posted_implied": None, "market_fair": None, "market_fair_method": None,
            "edge_vs_fair": None,
            "price_clears": None, "alternatives": None,
        })
    return combined


# ── How much to trust each number ─────────────────────────────────────────
#
# A probability with no interval around it invites being read as precise when
# it is not. Two picks can both say 68% while one rests on 110 games and the
# other on 22, and nothing in the headline number distinguishes them.
#
# The interval reported is the one around the EMPIRICAL component, which is
# the part with a real sample size behind it. The modelled component's
# uncertainty is not included and cannot honestly be folded in without
# assumptions about its own error, so the interval is presented as what it is:
# a lower bound on the true uncertainty, not the whole of it.
RELIABILITY_TIERS = [
    (80, "A", "season-long sample"),
    (45, "B", "solid sample"),
    (25, "C", "thin sample — treat the number as approximate"),
    (0,  "D", "very thin sample — the number is barely more than a base rate"),
]

# Direct request, verbatim: "I guess I don't like how mcgreevy outs is a high
# model % but not a lock... maybe we need to lessen the constraints you
# mentioned about his starts." Investigated rather than just loosened blindly:
# RELIABILITY_TIERS above was being applied UNCHANGED to pitcher STARTS
# counts, not just batter GAMES counts -- but a starting pitcher makes
# roughly 30-33 starts across a full, healthy 162-game season (one every
# 5th game), against a batter's ~150-162 games. Under the batter-games
# scale, tier "A" (80) and even "B" (45) are structurally impossible for ANY
# starting pitcher to ever reach within a single season -- Mitch McGreevy's
# 23 real starts by mid-August, arguably a genuinely deep sample for a
# starter this point in the year, was capped at reliability D (very thin)
# purely because the yardstick built for a 150+-game batter season was
# reused verbatim for a ~32-start pitcher season. Rescaled here to the same
# FRACTIONS of a season (80/162≈49%, 45/162≈28%, 25/162≈15%) applied to a
# pitcher's real ~32-start season instead: 16/9/5. McGreevy's 23 starts
# grades "A" under this scale -- a real, defensible recalibration of what
# "season-long" means for a market that only produces one data point every
# five days, not an arbitrary loosening of the bar itself (the score/edge
# gate is untouched).
PITCHER_STARTS_RELIABILITY_TIERS = [
    (16, "A", "season-long sample (starts)"),
    (9,  "B", "solid sample (starts)"),
    (5,  "C", "thin sample (starts) — treat the number as approximate"),
    (0,  "D", "very thin sample (starts) — the number is barely more than a base rate"),
]

# Every stat whose sample_n above is counted in STARTS, not games -- the
# generic branch routes "strikeouts" through emp_pitchers['starts'], and
# pitcher_outs/combined_strikeouts/first_inning_run/nrfi_combined all set
# n from a starts-based source of their own (see the branches below).
PITCHER_STARTS_STATS = {"strikeouts", "pitcher_outs", "combined_strikeouts",
                        "first_inning_run", "nrfi_combined"}


def _wilson_interval(hits, n, z=1.96):
    """Wilson score interval — stays sensible at the extremes, where the
    normal approximation collapses to zero width."""
    if n <= 0:
        return (0.0, 1.0)
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def attach_reliability(candidates, emp_batters, emp_pitchers):
    """Give every pick a sample size, a confidence interval and a letter grade."""
    for c in candidates:
        pid = c.get("player_id")
        stat = (c.get("projection") or {}).get("stat")
        needs = (c.get("projection") or {}).get("needs")
        emp = (emp_pitchers if stat == "strikeouts" else emp_batters).get(pid) or {}
        n = emp.get("games") or emp.get("starts") or 0
        rate = None
        if needs is not None and emp.get("rates"):
            key = ("strikeouts_%dplus" % needs if stat == "strikeouts"
                   else "%s_%dplus" % (stat, needs))
            rate = (emp["rates"] or {}).get(key)
        elif stat in ("first_inning_run", "nrfi_combined"):
            n = int((c.get("signals") or {}).get("fi_n_starts") or 0)
        elif stat in ("hard_hit_105", "hard_hit_110"):
            # score_laser already set sample_n from mlb_sources.hard_hit_game_rates
            # -- its own real sample size, not emp_batters' (a different rate
            # table entirely; looking it up there would silently overwrite a
            # correct number with an unrelated one).
            n = c.get("sample_n") or 0
        elif stat == "pitcher_outs":
            # Same bug class, pitcher side: this stat isn't "strikeouts" so
            # the generic path above would look it up in emp_batters (wrong
            # table for a pitcher, and pid won't even be in it) and silently
            # zero out score_pitcher_outs's real sample_n from
            # mlb_sources.empirical_pitcher_outs_rates.
            n = c.get("sample_n") or 0
        elif stat == "combined_strikeouts":
            # Same bug class again, and found the same way: pid here is
            # score_combined_strikeouts's player_id (the away starter,
            # kept for persistence), so the generic path would look him up
            # in emp_batters -- wrong table, wrong player type, matches
            # nothing -- and silently report 0 real starts of evidence for
            # a pick built on two starters' real K rates. There is no
            # emp_batters/emp_pitchers-style table for this market yet (it's
            # brand new), so 0 stays 0 here, honestly: this pick genuinely
            # has no independent sample size of its own to report, not a
            # lookup that quietly went to the wrong place.
            n = c.get("sample_n") or 0
        # Same fix as _batter_options' per-line CI, applied here for the
        # candidate's own primary (top10-board) line: a Wilson interval on
        # the raw empirical rate table only describes the displayed
        # hit_probability when that probability actually came from the
        # empirical rate. "modelled_shrunk" (hits/total_bases/home_runs
        # with a true league rate available) and "league_only" explicitly
        # drop or never touch the empirical term -- computing a CI from
        # `rate` there would describe a different number than the one on
        # screen, the exact mismatch the 2026-08-15 audit found live.
        #
        # H1, 2026-08-19 structural audit: the same borrowed-CI failure also
        # crosses the CALIBRATION boundary, not just the basis boundary
        # above. apply_calibration() runs BEFORE this function (see
        # score_slate()'s call order) and replaces c["hit_probability"] with
        # a Platt-scaled value for any candidate whose market has a fitted
        # curve (currently hits/hits_runs_rbis/strikeouts) -- but the Wilson
        # interval below is still computed from the RAW pre-calibration
        # rate table. A raw-scale interval no longer describes the
        # calibrated number displayed next to it: Platt scaling is a
        # population-level correction, not a transformation of one player's
        # own sampling uncertainty, and mechanically pushing the interval
        # endpoints through the same sigmoid would ALSO ignore the fitted
        # curve's own uncertainty (real, and in the strikeouts market's
        # unsupported low-probability tail, severe -- see the 2026-08-19
        # calibrator-tail investigation). No defensible calibrated-interval
        # method exists yet. Per this codebase's own standing rule ("if a
        # defensible CI does not exist for a particular line, show no CI
        # rather than inventing or borrowing one" -- _batter_options, same
        # rule applied one boundary over), the honest answer is to withhold
        # prob_ci entirely for a calibrated line, not transform or keep the
        # mismatched one. This can and does cost real Top Picks/Value picks
        # via classify_recommendation's require_robust=True gate (A1) --
        # accepted: a missing interval failing closed is correct; a
        # mismatched-scale interval passing was not.
        if (rate and c.get("probability_basis") not in ("modelled_shrunk", "league_only")
                and not c.get("calibrated_by")):
            lo, hi = _wilson_interval(rate.get("hit", 0), rate.get("n", n) or 1)
            c["prob_ci"] = [round(lo, 4), round(hi, 4)]
            # 2026-08-2X CI-provenance-honesty fix (data-integrity audit):
            # this is a real per-PLAYER Wilson interval off his own
            # empirical hit/n count -- a materially different, more direct
            # kind of evidence than the historical_reliability_band path
            # below (a market/bucket-level backtest measurement, not this
            # player's own record). prob_ci_source was previously only ever
            # set on the historical-band path, leaving this one implicitly
            # unlabeled (None) even though a real, named source exists.
            c["prob_ci_source"] = "player_empirical"
        # 2026-08-24 accuracy investigation: the branch above is the only
        # per-PLAYER interval this pipeline can build, and it structurally
        # cannot cover modelled_shrunk/league_only/calibrated lines (see the
        # long comment above explaining exactly why not). That is correct
        # as far as it goes, but it made every one of those bases
        # PERMANENTLY ineligible for Top Pick/Value regardless of how much
        # real evidence accumulated -- classify_recommendation's
        # require_robust=True gate has no other way to pass. Real historical
        # evidence now exists for some of these markets (backtest/
        # reliability_bands.py, built from backtest/rows_backfill.jsonl's
        # real graded point-in-time predictions) -- try it whenever the
        # per-player interval above didn't fire, and use it only when a
        # market/bucket has actually earned real backtest coverage
        # (MIN_RELIABILITY_BAND_N rows; see historical_prob_ci's own
        # docstring). A market/bucket without that coverage yet gets
        # nothing here either, same fail-closed behavior as before this
        # existed -- this adds a real path to eligibility, it does not
        # remove the requirement to earn it.
        if c.get("prob_ci") is None:
            hci = historical_prob_ci(stat, needs, c.get("hit_probability"))
            if hci is not None:
                c["prob_ci"] = hci
                c["prob_ci_source"] = "historical_reliability_band"
        c["sample_n"] = int(n)
        tiers = PITCHER_STARTS_RELIABILITY_TIERS if stat in PITCHER_STARTS_STATS else RELIABILITY_TIERS
        for floor, grade, blurb in tiers:
            if n >= floor:
                c["reliability"] = grade
                c["reliability_note"] = blurb
                break
    return candidates


SIGNAL_MEASUREMENT_FILE = os.path.join(
    os.environ.get("RESULTS_DIR", "results"), "signal_measurement.json")


def load_signal_trust(path=SIGNAL_MEASUREMENT_FILE):
    """{signal_name: trust_multiplier}, replacing measure_signals.py's old
    binary gate (n>=100 or the signal counts for nothing) with a continuous
    one Jacob asked for directly: "give every signal a weight that scales
    with its sample size and how far its measured effect is from zero...
    nothing sits at exactly zero just because it hasn't hit an arbitrary n
    line yet."

    trust = tanh(z / 2), where z = (auc - 0.5) / se is how many standard
    errors the measured AUC sits from a coin flip. This one number already
    does everything the ask requires without a hand-picked cutoff:
      - n small -> se large -> z near 0 -> trust near 0 (barely nudges)
      - n large AND a real effect -> z large -> trust approaches +-1 (the
        signal's own measured direction and strength, fully applied)
      - n large but auc stays near 0.5 -> z near 0 regardless of n -> trust
        stays near 0 (more data confirming "no effect" should not start
        creating one)
      - auc BELOW 0.5 (a signal measured running backwards, like the two
        that "scored below random" the last time this project hand-weighted
        signals) -> negative z -> negative trust -> the delta is applied in
        the OPPOSITE direction from how the signal's own formula intended,
        because the measured relationship is what's trusted, not the
        original hypothesis about which way it should point.
    tanh keeps any single signal from ever dominating the score outright,
    the same role the -4/-5/-6 style clamps already play on each raw delta.

    Missing file (fresh clone, measure_signals.py never run) or a signal
    absent from it both mean "no information yet" -- trust 0, not a crash
    and not an assumed direction."""
    try:
        with open(path, encoding="utf-8") as f:
            table = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for row in table.get("signals", []):
        name = row.get("signal")
        auc, se = row.get("auc"), row.get("se")
        if not name or auc is None or not se:
            continue
        z = (auc - 0.5) / se
        out[name] = math.tanh(z / 2)
    return out


# Total adjustment a candidate's score can receive from signal weighting,
# bounded for the same reason every individual _sig delta already is (see
# score_pitcher's clamp(..., -4, 4) etc.) -- with ~20 candidate signals each
# individually bounded to roughly +-6, an unclamped sum could in principle
# swing a score further than any single weighted COMPONENT (matchup 35%,
# form 25%, ...) is allowed to. This keeps "everything counts a little" from
# becoming "everything at once outweighs the model."
MAX_SIGNAL_WEIGHT_ADJUSTMENT = 12.0


def apply_signal_weights(candidates, trust=None):
    """Give every recorded _sig() value the influence load_signal_trust()
    says it has earned -- LIVE ONLY. Deliberately never called from
    build_candidates() or any score_* function, and never from
    backtest/engine.py: results/signal_measurement.json is built from
    outcomes settled as of TODAY, so applying it inside a backtest
    simulating a past date would leak future knowledge into that date's
    inputs -- exactly what PointInTime exists to prevent. Called from
    main() only, strictly after scoring, calibration and reliability are
    already final.

    Every adjustment is recorded on the candidate (never silent), so a
    picked line can always be traced back to what nudged it and by how
    much, the same audit-trail discipline `why`/`signals` already carry."""
    if trust is None:
        trust = load_signal_trust()
    if not trust:
        return candidates
    for c in candidates:
        sigs = c.get("signals") or {}
        if not sigs:
            continue
        total = sum(trust.get(name, 0.0) * value for name, value in sigs.items())
        total = clamp(total, -MAX_SIGNAL_WEIGHT_ADJUSTMENT, MAX_SIGNAL_WEIGHT_ADJUSTMENT)
        if total == 0:
            continue
        c["signal_weight_adjustment"] = round(total, 2)
        c["score"] = round(clamp(c["score"] + total), 1)
        # A thin-sample pick (grade C/D) doesn't get promoted to High off the
        # strength of signal nudges alone -- the same discipline
        # score_laser/score_pitcher_outs already apply to their own sample
        # floor, extended here so this step can't undo it.
        reliable = c.get("reliability") in ("A", "B")
        c["confidence"] = ("High" if c["score"] >= 70 and reliable
                            else ("Medium" if c["score"] >= 55 else "Low"))
    return candidates

# ── Quality control ───────────────────────────────────────────────────────
#
# Rejecting a pick for a reason that has nothing to do with the model. These
# are conditions under which the INPUTS are untrustworthy, which is different
# from the model being unconvinced, and they have to be checked separately
# because a confident number built on a stale lineup is still wrong.
#
# Every rejection is reported rather than silently applied. A filter that
# quietly removes candidates is how an entire prop type once vanished with no
# error anywhere.

# A "starter" who faces this few batters per outing is being used as an
# opener, and a strikeout prop on him is a different bet than the model
# thinks. Measured on the season pull: of appearances beginning in the first
# inning, the 5th percentile faces 8 batters and the median 22.
OPENER_BF_THRESHOLD = 15

# Rain risk above which a game's props carry real postponement/shortening
# risk rather than a note.
QC_PRECIP_REJECT = 70


def quality_control(candidates, game_meta, park_wx, emp_pitchers):
    """Reject candidates whose inputs cannot be trusted, and say why.

    Returns (kept, rejected, assumed_lineup). `assumed_lineup` is new: a
    batter whose lineup came from fetch_last_known_lineup (mlb_daily.py's
    tier-4 fallback, tagged assumed=True on every entry) is neither a real
    read (kept) nor a genuine reject (rejected alongside rain/openers) --
    it is a real player and a real matchup with a GUESSED batting slot,
    kept ONLY out of the graded board (grade_results.py must never score a
    guess as if it were a real bet) and surfaced separately instead, per
    direct request: an early, clearly-labelled look is worth more than
    nothing while real lineups are still hours from posting."""
    lineup_state = {}
    for gm in game_meta:
        for side in ("away", "home"):
            lu = gm.get(f"{side}_lineup") or []
            # A real posted lineup is nine hitters. Anything shorter is a
            # partial scrape and gets the same "missing" treatment as no
            # lineup at all -- only a full 9-entry lineup is ever assumed=True
            # (see fetch_last_known_lineup), so a short list here is never a
            # partially-assumed one, just an incomplete real one.
            if len(lu) >= 9 and any(e.get("assumed") for e in lu):
                lineup_state[(gm.get("game_pk"), side)] = "assumed"
            elif len(lu) >= 9:
                lineup_state[(gm.get("game_pk"), side)] = "confirmed"
            else:
                lineup_state[(gm.get("game_pk"), side)] = "missing"

    kept, rejected, assumed_lineup = [], [], []
    for c in candidates:
        stat = (c.get("projection") or {}).get("stat")
        reason = None
        is_assumed = False

        # pitcher_outs and combined_strikeouts are just as vulnerable to the
        # opener trap as strikeouts is -- arguably more so, since "Over 17.5
        # Outs" assumes a normal-length start even more directly than a K
        # total does. Found missing during a sweep: this check only ever
        # covered strikeouts, so a pitcher who'd shifted into an opener role
        # could still ship an Outs Recorded or Combined Strikeouts pick built
        # on a season average that no longer reflects how he's being used.
        if stat in ("strikeouts", "pitcher_outs"):
            emp = emp_pitchers.get(c.get("player_id")) or {}
            starts = emp.get("starts", 0)
            avg_bf = emp.get("avg_bf")
            label = CATEGORY_LABELS.get(stat, stat)
            if avg_bf is not None and avg_bf < OPENER_BF_THRESHOLD:
                reason = (f"used as an opener ({avg_bf:.0f} batters faced per outing) — "
                          f"a {label} prop on him is not the bet the model priced")
            elif starts and starts < 3:
                reason = f"only {starts} start(s) of evidence"
        elif stat == "combined_strikeouts":
            for pid in (c.get("combo_player_ids") or []):
                emp = emp_pitchers.get(pid) or {}
                avg_bf = emp.get("avg_bf")
                if avg_bf is not None and avg_bf < OPENER_BF_THRESHOLD:
                    reason = (f"one of the two starters is used as an opener "
                              f"({avg_bf:.0f} batters faced per outing) — a combined "
                              f"strikeouts prop assumes normal starter workload from both")
                    break

        # type == "batter" ONLY, verified deliberate 2026-08-24 accuracy
        # investigation (not an accidental gap): a pitcher candidate's own
        # batting-order slot is irrelevant to a strikeout/outs prop -- what
        # matters is (a) a REAL, named starter, which already can't exist
        # here at all until MLB confirms it (gm["away_sp"]/["home_sp"] !=
        # "TBD" gates candidate creation itself, well before batting
        # lineups post) and (b) the OPPOSING team's season-long aggregate
        # rate, not its exact 1-9 order. So a pitcher candidate correctly
        # never reaches the is_assumed branch below -- lineup_assumed stays
        # unset regardless of either team's batting-lineup state. See
        # test_quality_control.py's own coverage for this (check 8).
        if reason is None and c.get("type") == "batter":
            gp = c.get("game_pk")
            side = "away" if c.get("team") == next(
                (g.get("away_team") for g in game_meta if g.get("game_pk") == gp), None) else "home"
            # REAL BUG, found by test_quality_control.py: .get((gp, side)) with
            # no default returns None for a candidate whose game_pk isn't in
            # game_meta at all (a stale candidate, or a game that dropped off
            # the schedule between generation and this check). None matches
            # neither "missing" nor "assumed" below, so the candidate fell
            # through BOTH branches and reached `kept` -- a batter with ZERO
            # lineup information sailing through as if fully confirmed, the
            # exact thing this function exists to prevent. Defaulting to
            # "missing" here makes that case reject the same way a genuinely
            # unconfirmed lineup already does, instead of silently passing.
            state = lineup_state.get((gp, side), "missing")
            if state == "missing":
                reason = ("lineup not confirmed — the batting-order slot is a guess, "
                          "and slot is the strongest single signal in the model")
            elif state == "assumed":
                is_assumed = True

        if reason is None:
            wx = park_wx.get(c.get("matchup")) or {}
            if not wx.get("dome") and (wx.get("precip_prob") or 0) >= QC_PRECIP_REJECT:
                reason = (f"{wx['precip_prob']}% rain risk — real chance of a "
                          f"postponement or a shortened game")

        if reason:
            c["qc_reason"] = reason
            rejected.append(c)
        elif is_assumed:
            c["lineup_assumed"] = True
            assumed_lineup.append(c)
        else:
            kept.append(c)

    if rejected:
        print(f"    Quality control rejected {len(rejected)} candidate(s):")
        by_reason = defaultdict(int)
        for c in rejected:
            by_reason[c["qc_reason"].split(" — ")[0].split(" (")[0]] += 1
        for r, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            print(f"      {n:4d}  {r}")
    if assumed_lineup:
        print(f"    {len(assumed_lineup)} candidate(s) held out to the early-look board "
              f"(lineup ASSUMED from last known batting order, not yet posted for tonight)")
    return kept, rejected, assumed_lineup

# ── Safe to run at any hour ───────────────────────────────────────────────
#
# The pipeline can be run mid-slate, after some games have started or
# finished. Three separate things go wrong if that is not handled, and only
# one of them is obvious.
#
# 1. UNBETTABLE PICKS. A game already underway cannot be bet at the pregame
#    price, and a finished one cannot be bet at all. Recommending either is
#    noise at best.
#
# 2. A CORRUPTED ACCURACY RECORD, which is the serious one. grade_results.py
#    grades every pick in the day's file against the final box score. A pick
#    generated for a game that had ALREADY FINISHED would be graded as a hit
#    or a miss exactly like a real one -- scoring the model on a bet nobody
#    could have placed, with the outcome already known when the "prediction"
#    was made. That is not a small bias; it is the model marking its own
#    homework with the answers in front of it.
#
# 3. DESTROYED PICKS. Each run overwrites the day's picks file. Re-running at
#    8pm silently replaces the board generated at 11am -- the one that was
#    actually bet -- so the record no longer reflects what was wagered.
#
# Games are considered bettable only in a genuine pregame state. Delays and
# postponements are deliberately included: a delayed game has not started, so
# its props are still live.
BETTABLE_STATES = {
    "scheduled", "pre-game", "warmup", "delayed start", "postponed",
    "delayed", "pre game",
}


def bettable_games(game_meta, allow_started=False):
    """Split tonight's games into those still bettable and those that are not."""
    live, done = [], []
    for gm in game_meta:
        state = (gm.get("status") or "").strip().lower()
        # An unknown or missing status is treated as bettable rather than
        # dropped: the schedule endpoint occasionally omits it, and silently
        # discarding a whole game over a missing field would be a worse
        # failure than including one that has just started.
        if allow_started or not state or state in BETTABLE_STATES:
            live.append(gm)
        else:
            done.append(gm)
    return live, done


def archive_existing_picks(date):
    """Preserve any picks already written for this date before overwriting.

    A re-run must never destroy the board that was actually bet. Archived
    copies are timestamped and kept alongside the live file so the day's
    history is recoverable and grading can be pointed at the right one."""
    if not os.path.exists(PICKS_JSON_FILE):
        return None
    try:
        with open(PICKS_JSON_FILE, encoding="utf-8") as f:
            prior = json.load(f)
        stamp = (prior.get("generated") or datetime.now().isoformat())[:19].replace(":", "")
        archive = os.path.join(OUTPUT_DIR, f"picks_{date}_{stamp}.json")
        if not os.path.exists(archive):
            with open(archive, "w", encoding="utf-8") as f:
                json.dump(prior, f, indent=2)
            return archive
    except (json.JSONDecodeError, OSError) as e:
        m.warn(f"Could not archive prior picks ({e}) — continuing")
    return None

def write_markdown(top10, skipped, game_meta, bullpen_scores, all_ranked=(), moonshots=(), by_category=None,
                   deep_moonshots=()):
    lines = [f"# MLB Top 10 Picks — {m.TODAY}", "",
             "_Generated by deterministic scoring over today's research pull — no LLM "
             "in the loop. No sportsbook odds were used: these are ranked purely by "
             "how likely each bet is to CASH, with no consideration of price or edge. "
             "A high percentage here does not mean good value — the book prices the "
             "likely ones short. **Check the current line and availability before "
             "betting.** Every pick is graded against the actual box score the next "
             "morning — see results/history.json for the running accuracy record._", ""]

    lines.append(f"**Slate:** {len(game_meta)} games. "
                 f"**Ranked by chance of cashing**, not by edge. Each pick's "
                 f"percentage blends how often the player has actually cleared that "
                 f"line in real games this season (weighted "
                 f"{int(EMPIRICAL_WEIGHT*100)}%) with a model of tonight's specific "
                 f"matchup ({100-int(EMPIRICAL_WEIGHT*100)}%). The 0-100 quality "
                 f"score (35% matchup / 25% recent form / 15% environment / 15% "
                 f"baseline skill / 10% context) is used only as a floor — a pick "
                 f"must score {MIN_QUALITY_SCORE:.0f}+ to make the board at all. "
                 f"A pick must ALSO beat its own market's league base rate, so "
                 f"nothing is recommended purely because its market is easy. "
                 f"**Lift** shows by how much. Lines are capped at "
                 f"{pp.MAX_USEFUL_PROB*100:.0f}% — the ~{pp.format_odds(pp.MAX_USEFUL_PROB)} "
                 f"equivalent — because a prop priced shorter than that needs a "
                 f"hit rate above what any model can reliably deliver just to "
                 f"break even.")
    lines.append("")

    if not top10:
        lines.append("No candidate genuinely cleared the market's price today — real, not a "
                     "bug: FanDuel's lines were sharp enough tonight that nothing had a "
                     "confirmed edge. (Could also mean a thin slate, lineups mostly "
                     "unconfirmed, or data pulls came back empty — check the run log.)")
    for i, c in enumerate(top10, 1):
        hp = c.get("hit_probability")
        head = f"### {i}. {c['name']} ({_team_label(c)}) — {c['prop']}"
        if hp is not None:
            head += f"  ·  **{hp*100:.0f}% to hit**"
        lines.append(head)
        lines.append(f"- **Matchup:** {c['matchup']}")
        if hp is not None:
            det = c.get("probability_detail") or {}
            parts = []
            if det.get("empirical") is not None:
                parts.append(f"cleared it in {det['empirical']*100:.0f}% of his games")
            if det.get("modelled") is not None:
                parts.append(f"model says {det['modelled']*100:.0f}% tonight")
            basis = f" ({'; '.join(parts)})" if parts else ""
            lift = c.get("lift"); base = c.get("base_rate")
            lift_s = ""
            if lift is not None and base is not None:
                lift_s = (f" — **{lift*100:+.1f} pts** vs the {base*100:.0f}% league base "
                          f"rate for this market")
            lines.append(f"- **Chance of hitting:** {hp*100:.1f}%{basis}{lift_s}")
            ci = c.get("prob_ci"); grade = c.get("reliability")
            if ci or grade:
                bits = []
                if ci:
                    bits.append(f"95% CI {ci[0]*100:.0f}–{ci[1]*100:.0f}%")
                if grade:
                    bits.append(f"data grade **{grade}** ({c.get('sample_n')} "
                                f"games/starts — {c.get('reliability_note')})")
                lines.append(f"- **Reliability:** {' · '.join(bits)}")
            lines.append(f"- **Price to beat:** {pp.max_acceptable_price(hp):+d} "
                          f"— bet only if the book is this price or better "
                          f"(fair value {pp.american_odds(hp, include_vig=False):+d}, "
                          f"your limit {pp.USER_MAX_PRICE:+d})")
            lines.append(f"- **Estimated price:** ~{pp.format_odds(hp)} "
                          f"(no free source for prop prices exists, so this is derived "
                          f"from the probability plus a typical prop hold — treat it as "
                          f"a band, not a quote, and check the book)")
            alts = c.get("alternatives") or []
            if alts:
                alt_s = ", ".join(f"{a['label'] if 'label' in a else 'Over ' + str(a['line']) + ' Ks'}"
                                  f" {a['prob']*100:.0f}%" for a in alts)
                lines.append(f"- **Other lines on this player:** {alt_s} "
                             f"— the one above was chosen because it cashes most often.")
        else:
            lines.append("- **Chance of hitting:** not priced — no game-log or rate data "
                          "for this player, so this pick is ranked on quality score alone.")
        lines.append(f"- **Quality score:** {c['score']}/100  |  **Confidence:** {c['confidence']}  |  "
                     f"**Converging signals:** {c['notable_signals']}")
        rec_status = c.get("status")
        if rec_status:
            lines.append(f"- **Recommendation:** {rec_status.replace('_', ' ').title()} "
                         f"— {(c.get('status_reasons') or [''])[0]}")
        lines.append(f"- **Why:** {'; '.join(c['why'])}.")
        if c["watchouts"]:
            lines.append(f"- **Watch-outs:** {'; '.join(c['watchouts'])}.")
        lines.append("- **Line check:** verify the current line/availability before betting — "
                      "no live odds were used to generate this pick.")
        lines.append("")

    # ── Best in each market ──────────────────────────────────────────────
    # WHY THIS SECTION EXISTS. Ranking strictly by chance of cashing does
    # exactly what it says, and that has a structural consequence: prop types
    # with a high BASE RATE crowd out everything else, every single day. A
    # team is held scoreless in the first 70.6% of the time before anyone
    # looks at the pitcher, so first-inning picks start 70 points ahead of a
    # coin flip and a hitter's best line cannot catch them. The 2026-08-06
    # board came out seven first-inning picks and three strikeout props, with
    # no batter prop at all -- not because the model disliked the hitters, but
    # because it was ranking markets rather than picks.
    #
    # This is NOT the forced-diversity rule that was deliberately removed. The
    # top 10 above is still a pure probability ranking with no caps, and if it
    # is all one market that is what ships. This section is additional: the
    # best available pick WITHIN each market, so a day's board is never
    # silently reduced to one bet type.
    #
    # LIFT is the number to read here. Probability says how likely the bet is;
    # lift says how much of that is the market being easy versus this pick
    # being good. An 80% first-inning pick is +9 over its base rate, while a
    # 72% hits prop is +7 over its own -- far closer than the raw percentages
    # suggest, and the comparison the percentages alone actively hide.
    by_market = defaultdict(list)
    for c in all_ranked:
        stat = (c.get("projection") or {}).get("stat")
        if stat and c.get("hit_probability") is not None:
            by_market[stat].append(c)
    shown = {id(c) for c in top10}
    market_names = {"hits": "Hits", "total_bases": "Total Bases",
                    "home_runs": "Home Runs", "strikeouts": "Strikeouts",
                    "stolen_base": "Stolen Bases"}
    extra = []
    for stat, group in by_market.items():
        best = [c for c in group if id(c) not in shown][:2]
        if best:
            extra.append((market_names.get(stat, stat), best))
    if extra:
        lines.append("## Best in each market")
        lines.append("")
        lines.append("_The top 10 above is a pure probability ranking, so the markets with "
                     "the highest natural base rates dominate it. These are the best "
                     "picks in every OTHER market, for when you don't want the whole card "
                     "on one bet type. **Lift** is how far above that market's league base "
                     "rate the pick sits — it's the part that reflects an actual read, "
                     "rather than the market simply being easy._")
        lines.append("")
        for market, picks in sorted(extra):
            lines.append(f"**{market}**")
            for c in picks:
                hp = c["hit_probability"]
                lift = c.get("lift")
                lift_s = ""
                if lift is not None:
                    base = c.get("base_rate")
                    lift_s = (f"  ·  lift {lift*100:+.1f} pts"
                              + (f" over a {base*100:.0f}% base rate" if base is not None else ""))
                lines.append(f"- {c['name']} ({_team_label(c)}) — {c['prop']} — "
                             f"**{hp*100:.0f}%** (~{pp.format_odds(hp)}){lift_s}")
            lines.append("")

    if skipped:
        lines.append("**What I'd skip tonight:**")
        for c in skipped:
            reason = "; ".join(c["watchouts"]) if c["watchouts"] else \
                "strong on one signal but did not converge with the others"
            hp = c.get("hit_probability")
            pct = f", {hp*100:.0f}% to hit" if hp is not None else ""
            lines.append(f"- {c['name']} ({c['prop']}{pct}, score {c['score']}) — {reason}.")
        lines.append("")

    if moonshots:
        lines.append("## 🎰 Moonshots")
        lines.append("_Home runs never clear the main board's floor (MIN_LINE_PROB=60%) --"
                     " 1+ HR runs 10-25% even for the best hitters alive, so it can't be"
                     " reached by the same rule that governs everything else here. This"
                     " section exists on purpose, separately, still ranked by chance of"
                     " cashing among home-run bets specifically, not by price or edge._")
        lines.append("")
        for i, c in enumerate(moonshots, 1):
            hp = c.get("hit_probability")
            odds = c.get("market_odds")
            odds_s = f" · **{odds:+d}** at FanDuel" if odds is not None else " · unpriced"
            lines.append(f"{i}. **{c['name']}** ({_team_label(c)}) — {c['matchup']} — "
                         f"**{hp*100:.1f}%** to hit a HR{odds_s}")
        lines.append("")

    if deep_moonshots:
        lines.append(f"## 🚀 Deep Moonshots ({MOONSHOT_THRESHOLD_FT}+ FT)")
        lines.append("_A different market from the Moonshots above -- this one pays only on a home run"
                     f" that actually travels {MOONSHOT_THRESHOLD_FT}+ feet, not any home run. Real"
                     " per-game rates run 2-11% even for the game's biggest sluggers (verified live"
                     " against real Statcast distance data), so like the HR-at-all market above it"
                     " can't be reached by the main board's usual floor. Ranked by chance of cashing,"
                     " not price or edge._")
        lines.append("")
        for i, c in enumerate(deep_moonshots, 1):
            hp = c.get("hit_probability")
            odds = c.get("market_odds")
            odds_s = f" · **{odds:+d}** at FanDuel" if odds is not None else " · unpriced"
            lines.append(f"{i}. **{c['name']}** ({_team_label(c)}) — {c['matchup']} — "
                         f"**{hp*100:.1f}%** to hit a {MOONSHOT_THRESHOLD_FT}+ ft HR{odds_s}")
        lines.append("")

    if by_category:
        lines.append("## Best of Every Category")
        lines.append("_Up to 5 real candidates in every prop family this pipeline can price tonight -- "
                     "not just the ones that clear the main board's bars. We want you to have options, "
                     "not just an empty category. Score is the 0-100 quality rating (35% matchup / 25% "
                     "recent form / 15% environment / 15% baseline skill / 10% context); Prob is chance "
                     "of cashing. Neither is floored here. Entries marked ⚠ sit below the 60% "
                     f"cash-probability floor and/or the {MIN_QUALITY_SCORE:.0f}+ quality floor that "
                     "gate the main board -- still real, still ranked honestly by chance of cashing, "
                     "just not a recommendation at the same bar as the top 10._")
        lines.append("")
        for stat in sorted(CATEGORY_LABELS, key=lambda s: CATEGORY_LABELS[s]):
            entries = by_category.get(stat)
            lines.append(f"**{CATEGORY_LABELS[stat]}**")
            if not entries:
                lines.append("- _no candidate tonight_")
                lines.append("")
                continue
            for c in entries[:5]:
                hp = c.get("hit_probability")
                odds = c.get("market_odds")
                below_floor = (not c.get("clears_main_board_floor")) or (c.get("score") or 0) < MIN_QUALITY_SCORE
                flag = " ⚠" if below_floor else ""
                odds_s = f"{odds:+d}" if odds is not None else "unpriced"
                lines.append(f"- {c['name']} ({_team_label(c)}) — {c['prop']} — "
                             f"**{hp*100:.1f}%**{flag} (score {c.get('score', '?')}, {odds_s})")
            lines.append("")

    with open(PICKS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())

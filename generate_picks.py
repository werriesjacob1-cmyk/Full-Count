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
import os, sys, json, re, unicodedata
from datetime import datetime
from collections import defaultdict
import pandas as pd

import mlb_daily as m
import prop_probability as pp

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

NWS_UA = {"User-Agent": "(project-gridiron-mlb-pipeline, contact: github.com/werriesjacob1-cmyk/PROJECT-GRIDIRON)"}

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
        if dome:
            out[gm["matchup"]] = {"dome": True, "park_hr_index": 50, "wind_effect": "dome", "temp": None}
            continue
        try:
            r = m.retry_get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon,
                "hourly": "temperature_2m,windspeed_10m,winddirection_10m,relativehumidity_2m,precipitation_probability",
                "temperature_unit": "fahrenheit", "windspeed_unit": "mph",
                "timezone": "auto", "forecast_days": 1,
            }, timeout=20, retries=2)
            r.raise_for_status()
            h = r.json()["hourly"]
            idx = min(max(gm["hour"], 0), 23)
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

            idx_score, wind_effect = park_hr_index(temp, wsp, wdir, humid, cf_deg, elev, dome)
            out[gm["matchup"]] = {"dome": False, "park_hr_index": idx_score,
                                   "wind_effect": wind_effect, "temp": temp, "wind_mph": wsp,
                                   "wx_disagreement": wx_disagreement, "precip_prob": precip_prob}
        except Exception as e:
            m.warn(f"Picks weather {sk}: {e}")
            out[gm["matchup"]] = {"dome": False, "park_hr_index": 50, "wind_effect": "unknown", "temp": None}
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


def fetch_bullpen_scores(game_meta):
    """Reuses mlb_daily.py's already-fixed, parallelized bullpen fetch directly."""
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
        with m.ThreadPoolExecutor(max_workers=10) as ex:
            for team_name, usage, err in ex.map(m._bullpen_fetch_one, jobs):
                if usage:
                    fatigued = sum(1 for u in usage.values() if u["pitches"] > 60)
                    out[team_name] = {"fatigued_relievers": fatigued, "tracked": len(usage)}
    return out


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

    score = matchup * 0.35 + form * 0.25 + env * 0.15 + skill * 0.15 + context * 0.10

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
    why.append(f"Projected {projected_pa} PA (slot {order}"
                + (f", {implied_total}-run implied team total)" if implied_total is not None
                   else ", league-average run environment — no market total available)"))
    why.append(f"Platoon: {bats} bat vs {opp_sp_hand or '?'}HP ({'favorable' if platoon>=65 else 'unfavorable'})")
    if exploit:
        why.append(f"Pitch-type exploit: RV/100 {exploit['run_value_per_100']:+.1f} vs {exploit['pitch_type']} "
                    f"(opposing SP throws it {exploit['usage_pct']}% of the time)")
    if sp_era is not None: why.append(f"Opposing SP ERA {sp_era:.2f}")
    if l7.get("avg_EV"): why.append(f"L7 avg EV {l7['avg_EV']:.1f}mph (league ~{LEAGUE_AVG_EV})")
    if l7.get("barrel_pct") is not None: why.append(f"L7 barrel% {l7['barrel_pct']}")
    if bs_trend is not None and bs_trend >= 1.0: why.append(f"Bat speed trending up L14 ({bs_trend:+.1f}mph 2nd-half vs 1st-half)")
    if bs.get("wRC+"): why.append(f"Season wRC+ {bs['wRC+']:.0f}")
    if implied_total is not None:
        why.append(f"Market implied team total {implied_total} runs "
                    f"(league avg {LEAGUE_TEAM_RUNS_MEAN}; line {(sharp_bias or {}).get('implied_total_line')}, "
                    f"game total {(sharp_bias or {}).get('game_total')})")
    if not park_wx or park_wx.get("dome"): why.append("Dome — weather neutral")
    elif park_wx.get("wind_effect") == "out": why.append(f"Wind blowing OUT ({park_wx.get('wind_mph',0):.0f}mph) — HR boost")
    elif park_wx.get("wind_effect") == "in": why.append("Wind blowing IN — power suppressed")
    if bullpen_fatigue_pct is not None:
        why.append(f"Opposing bullpen fatigue: {fatigued}/{tracked} relievers over 60 pitches in L7 "
                    f"({'tired pen — favorable late' if bullpen_fatigue_pct >= 40 else 'fresh pen'})")
    if bullpen_era_diff is not None and abs(bullpen_era_diff) >= 0.5:
        why.append(f"Opposing bullpen ERA {bp_era} (league ~{LEAGUE_AVG_BULLPEN_ERA}, "
                    f"{'shaky' if bullpen_era_diff > 0 else 'elite'} pen)")
    if sharp_divergence is not None and abs(sharp_divergence) >= 10:
        why.append(f"Sharp money {'backing' if sharp_divergence > 0 else 'fading'} {batter.get('team')} "
                    f"(money% {'+' if sharp_divergence>0 else ''}{sharp_divergence} pts vs ticket%)")

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
            watchouts.append(f"BvP: {bvp.get('H','?')}-for-{bvp.get('AB','?')} "
                             f"vs {opp_sp_name} (small sample, weighted lightly)")

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
    hh = (ex.get("hard_hit") or {}).get(bid) if bid else None
    if hh:
        r105 = (hh.get("rates") or {}).get("hard_hit_105_1plus")
        if r105 and r105.get("hit", 0) >= 4:
            _sig(signals, "hard_hit_105_rate", r105["p_hat"],
                 clamp((r105["p_hat"] - 0.215) * 25, -5, 5))

    return {
        "type": "batter", "name": name, "player_id": bid, "team": batter.get("team"), "matchup": gm["matchup"],
        "game_pk": gm.get("game_pk"), "prop": prop, "projection": {"stat": "total_bases", "value": projected_tb},
        "projected_pa": projected_pa, "projected_tb": projected_tb, "signals": signals,
        "score": round(score, 1), "why": why, "watchouts": watchouts, "notable_signals": notable_signals,
        "confidence": "High" if score >= 70 and not low_sample else ("Medium" if score >= 55 else "Low"),
    }


def score_pitcher(sp_name, sp_id, sp_hand, gm, side, pit_season_lookup, l14_form,
                   opp_lineup, opp_team_k_pct, ump_scores, opp_k_source=None, exp_k_form=None):
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

    score = matchup * 0.35 + form * 0.25 + env * 0.15 + skill * 0.15 + context * 0.10

    watchouts = []
    if low_sample_form:
        watchouts.append("L14 Statcast sample too thin — recent-form read falls back to season K%")
    if era and k_pct and era > 4.5 and k_pct > 25:
        watchouts.append(f"High K% ({k_pct}%) paired with a shaky ERA ({era:.2f}) — command may be inconsistent start-to-start")
    if tto_note: watchouts.append(tto_note) if "drop-off" in tto_note else None

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

    if opp_team_k_pct is not None:
        k_note = (f"Opposing team K% {opp_team_k_pct:.1f}" if opp_k_source == "team"
                   else f"Opposing lineup K% {opp_team_k_pct:.1f} (avg of {opp_k_source} confirmed batters — FanGraphs team page unreachable)")
    else:
        k_note = "Opposing team K% unavailable (FanGraphs team page down and no confirmed lineup batters matched)"
    why = [k_note]
    if workload_note: why.append(workload_note)
    why.append(f"{same_hand}/{known} known-hand opposing batters same-handed" if known else "Opposing lineup handedness mostly unknown")
    if k_pct: why.append(f"Season K% {k_pct}")
    if csw: why.append(f"CSW% {csw}")
    if stuff: why.append(f"Stuff+ {stuff}")
    if l14.get("l14_k_pct") is not None: why.append(f"L14 K% {l14['l14_k_pct']} ({l14.get('l14_pa')} PA)")
    if exp_k and exp_k.get("k_rate") is not None:
        why.append(f"Recency-weighted K rate {exp_k['k_rate']*100:.1f}% (exp. decay, halflife 30d, "
                    f"{exp_k['n_starts']} real starts / {exp_k['raw_bf']} BF) — drives the strikeout probability model")
    if tto_note and "Maintains" in tto_note: why.append(tto_note)
    if ump.get("accuracy"): why.append(f"HP ump accuracy {ump['accuracy']:.1f}%")

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
    matchup = scale(opp_catcher_poptime, 2.25, 1.90) if opp_catcher_poptime else 50
    if opp_catcher_poptime and opp_catcher_poptime >= 2.10: notable_signals += 1
    bs = batter_season or {}
    season_sb = bs.get("SB")
    on_base, on_base_note = _on_base_score(bs)
    context = on_base if on_base is not None else 50

    score = skill * 0.50 + matchup * 0.28 + context * 0.22
    if season_sb and season_sb >= 15: notable_signals += 1
    if on_base is not None and on_base >= 75: notable_signals += 1

    why = [f"Sprint speed {sprint_speed:.1f}ft/s (league ~{LEAGUE_AVG_SPRINT})"]
    if opp_catcher_poptime: why.append(f"Opposing catcher pop time {opp_catcher_poptime:.2f}s to 2B (league ~{LEAGUE_AVG_POPTIME}s)")
    if on_base_note: why.append(f"On-base ability: {on_base_note} — gates how often he's on first to run at all")
    if season_sb is not None: why.append(f"Season SB: {season_sb}")
    watchouts = []
    if opp_cs_pct is not None and opp_cs_pct >= 0.30:
        watchouts.append(f"Opposing team throws out {opp_cs_pct*100:.0f}% of runners "
                          f"(league ~25%) — a genuinely hard team to run on")
    if not opp_catcher_poptime: watchouts.append("Opposing catcher pop time unavailable — matchup component defaulted to neutral")
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
    }


def score_walk(batter, gm, opp_sp_row, ump_scores, batter_season):
    """A patient hitter facing a wild pitcher and a loose-zone umpire — a
    genuine convergent signal most bettors don't compute, since none of the
    three inputs alone screams "walk prop." """
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


def score_first_inning(sp_name, sp_id, gm, side, fi_form):
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
        "signals": {"yrfi_rate": round(float(yrfi_rate), 4), "fi_n_starts": float(n_starts)},
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
                     l14_pitcher_form, fi_form, exp_k_form=None):
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
            for c in (score_stolen_base(batter, gm, away_opp_catcher_pop, sprint_speed.get(batter.get("id")), bseason,
                                        opp_cs_pct=(extras or {}).get("cs_pct_by_team", {}).get(gm["home_team"])),
                      score_walk(batter, gm, opp_sp_row_for_away_batters, ump_scores, bseason)):
                if c: candidates.append(c)
        for batter in gm.get("home_lineup", []):
            batter["team"] = gm["home_team"]
            bseason = lookup_player(batter_lookup, batter["name"], batter.get("id"))
            candidates.append(score_batter(batter, gm, opp_sp_row_for_home_batters, gm.get("away_sp_id"), gm.get("away_sp_hand"),
                              wx, bseason, l7_form.get(batter.get("id")), bat_speed_trend, batter_arsenal, pitcher_arsenal,
                              home_opp_bullpen, sharp_bias.get(gm["home_team"]), home_opp_bullpen_quality,
                              extras=extras))
            for c in (score_stolen_base(batter, gm, home_opp_catcher_pop, sprint_speed.get(batter.get("id")), bseason,
                                        opp_cs_pct=(extras or {}).get("cs_pct_by_team", {}).get(gm["away_team"])),
                      score_walk(batter, gm, opp_sp_row_for_home_batters, ump_scores, bseason)):
                if c: candidates.append(c)

        if gm["away_sp"] != "TBD" and gm.get("away_sp_id"):
            opp_k, opp_k_source = team_k_lookup.get(gm["home_team"]), "team"
            if opp_k is None:
                opp_k, n = estimate_lineup_k_pct(gm.get("home_lineup", []), batter_lookup)
                opp_k_source = n
            candidates.append(score_pitcher(gm["away_sp"], gm["away_sp_id"], gm.get("away_sp_hand"),
                                             gm, "away", pitcher_lookup, l14_pitcher_form,
                                             gm.get("home_lineup", []), opp_k, ump_scores, opp_k_source,
                                             exp_k_form))
            fi = score_first_inning(gm["away_sp"], gm["away_sp_id"], gm, "away", fi_form)
            if fi: candidates.append(fi)
        if gm["home_sp"] != "TBD" and gm.get("home_sp_id"):
            opp_k, opp_k_source = team_k_lookup.get(gm["away_team"]), "team"
            if opp_k is None:
                opp_k, n = estimate_lineup_k_pct(gm.get("away_lineup", []), batter_lookup)
                opp_k_source = n
            candidates.append(score_pitcher(gm["home_sp"], gm["home_sp_id"], gm.get("home_sp_hand"),
                                             gm, "home", pitcher_lookup, l14_pitcher_form,
                                             gm.get("away_lineup", []), opp_k, ump_scores, opp_k_source,
                                             exp_k_form))
            fi = score_first_inning(gm["home_sp"], gm["home_sp_id"], gm, "home", fi_form)
            if fi: candidates.append(fi)

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


def _build_and_score():
    """The whole scoring pass: fetch, score, price, calibrate.

    Everything up to (but not including) ranking. Both the daily board
    and the value screen consume this, so neither can drift from the
    other's idea of what a candidate is worth.
    """

    lineup_text, game_meta, player_ids = m.fetch_lineups(m.TODAY)
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
    bullpen_scores = fetch_bullpen_scores(game_meta)
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
    if team_bat_df is not None and not team_bat_df.empty and "K%" in team_bat_df.columns:
        name_col = "Team" if "Team" in team_bat_df.columns else team_bat_df.columns[0]
        team_k_lookup = dict(zip(team_bat_df[name_col].astype(str), team_bat_df["K%"]))

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
    import mlb_sources as _src
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
        # Second batch, each verified against its real structure before use.
        ("team_field", lambda: _src.team_fielding_table()),
        ("team_bat", lambda: _src.team_batting_table()),
        ("pull", lambda: _src.pull_rates()),
        ("pitch_q", lambda: _src.pitch_quality()),
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
        team_k_lookup=team_k_lookup, park_wx=park_wx, ump_scores=ump_scores,
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
        league_rates = _src.league_base_rates()
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
                             league_rates)
    # Calibrate BEFORE ranking and before the positive-read floor, so both
    # operate on the honest number rather than the overstated one.
    apply_calibration(candidates, load_calibrator())
    attach_reliability(candidates, emp_batters, emp_pitchers)
    return candidates, {
        "game_meta": game_meta, "park_wx": park_wx,
        "bullpen_scores": bullpen_scores, "emp_batters": emp_batters,
        "emp_pitchers": emp_pitchers, "comp_table": comp_table,
        "league_rates": league_rates,
    }


def main() -> int:
    print("Generating top 10 picks (deterministic scoring, no LLM call)...")
    result = _build_and_score()
    if result is None:
        return 0
    candidates, ctx = result
    game_meta = ctx['game_meta']; park_wx = ctx['park_wx']
    bullpen_scores = ctx['bullpen_scores']
    emp_batters = ctx['emp_batters']; emp_pitchers = ctx['emp_pitchers']

    # RANKING. The board is sorted by chance of cashing, which is the stated
    # objective, with the quality score demoted to a GATE rather than the
    # ordering. Both parts matter:
    #
    #   - Without the gate, this ranks a 70% prop on a player in an awful
    #     spot above a 68% prop with every signal behind it, purely because
    #     of the base rate. The score is what knows about tonight.
    #   - Without probability ordering, the board ranks by a 0-100 quality
    #     number that is not a probability and does not behave like one --
    #     which is how a 28% stolen base finished #1 while a 79% hits prop
    #     went unranked.
    #
    # A candidate that could not be priced at all keeps its place in the
    # score order behind everything that could, rather than being dropped:
    # an unpriced pick is a gap in coverage, not evidence against the pick.
    # Untrustworthy INPUTS are rejected before anything is ranked -- that is a
    # different question from whether the model likes the pick.
    candidates, _qc_rejected = quality_control(candidates, game_meta, park_wx, emp_pitchers)

    gated = [c for c in candidates if c["score"] >= MIN_QUALITY_SCORE]

    # POSITIVE-READ FLOOR. A pick has to beat the league base rate for its own
    # market before it can be recommended at all, and only then does chance of
    # cashing decide the order.
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
    # The ordering is still purely by chance of cashing, as intended. The floor
    # only removes bets where the number comes from the market being easy
    # rather than from anything known about the player.
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

    priced = [c for c in gated if c.get("hit_probability") is not None]
    unpriced = [c for c in gated if c.get("hit_probability") is None]
    # EVIDENCE BEFORE CONFIDENCE. Sorting on probability alone put a 12-start
    # grade-D pick above a 107-game grade-A pick with sixteen times the lift,
    # because the two probabilities were three points apart. A number resting
    # on twelve observations should not outrank one resting on a hundred, so
    # picks are grouped by whether their evidence is adequate first, and only
    # then ordered by chance of cashing within each group. Thin-sample picks
    # are still shown -- they are ranked, not hidden.
    _order = {"A": 0, "B": 0, "C": 0, "D": 1}
    priced.sort(key=lambda c: (-_order.get(c.get("reliability", "D"), 1),
                               c["hit_probability"], c["score"]), reverse=True)
    unpriced.sort(key=lambda c: c["score"], reverse=True)
    ranked = priced + unpriced

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
    top10 = ranked[:10]
    skipped = [c for c in ranked[10:13] if c["score"] >= 55][:2]

    write_markdown(top10, skipped, game_meta, bullpen_scores, ranked)
    write_json(top10)
    persist_player_snapshots(candidates)
    print(f"Wrote {len(top10)} picks to {PICKS_FILE} and {PICKS_JSON_FILE}")
    return 0


def write_json(top10):
    """Structured pick data for grade_results.py — never parse the markdown
    back into data, same lesson learned from mlb_daily.py's report text."""
    payload = {
        "date": m.TODAY,
        "generated": datetime.now().isoformat(),
        "picks": [{
            "rank": i, "type": c["type"], "name": c["name"], "player_id": c["player_id"],
            "team": c["team"], "matchup": c["matchup"], "game_pk": c["game_pk"], "side": c.get("side"),
            "prop": c["prop"], "projection": c["projection"], "lean": c.get("lean"), "score": c["score"],
            "confidence": c["confidence"], "notable_signals": c["notable_signals"],
            "hit_probability": c.get("hit_probability"),
            "base_rate": c.get("base_rate"), "lift": c.get("lift"),
            "raw_hit_probability": c.get("raw_hit_probability"),
            "calibrated_by": c.get("calibrated_by"),
            "prob_ci": c.get("prob_ci"), "sample_n": c.get("sample_n"),
            "reliability": c.get("reliability"),
            "max_acceptable_price": (pp.max_acceptable_price(c["hit_probability"])
                                     if c.get("hit_probability") is not None else None),
            "estimated_odds": (pp.american_odds(c["hit_probability"])
                               if c.get("hit_probability") is not None else None),
            "probability_basis": c.get("probability_basis"),
            "probability_detail": c.get("probability_detail"),
            "alternatives": c.get("alternatives"),
        } for i, c in enumerate(top10, 1)],
    }
    with open(PICKS_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


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
            "evaluations": [{"prop": c["prop"], "type": c["type"], "score": c["score"],
                             "notable_signals": c["notable_signals"], "matchup": c["matchup"]}
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
# ── AUDIT, 2026-08-06: MEASURED. NOT YET ACTED ON. ────────────────────────
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

    def emp_p(key):
        r = rates.get(key)
        # TRUE league rate first. r["league_p"] is computed by
        # _apply_shrinkage from whatever players were passed in -- tonight's
        # slate -- so it is the slate's own average, not the league's. Falling
        # back to it is better than having no base rate at all, but it must
        # never win over a real measurement. See mlb_sources.league_base_rates.
        lg = (league or {}).get(key)
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

    families = [
        ("hits", "Hits", [(0.5, 1), (1.5, 2), (2.5, 3)],
         (lambda k: pp.p_at_least_hits(k, dist, pa)) if dist and pa else None),
        ("total_bases", "Total Bases", [(1.5, 2), (2.5, 3), (3.5, 4)],
         (lambda k: pp.p_at_least_total_bases(k, dist, pa)) if dist and pa else None),
        ("home_runs", "Home Runs", [(0.5, 1)],
         (lambda k: pp.p_at_least_home_runs(k, dist, pa)) if dist and pa else None),
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
            prob, basis = _blend(empirical, modelled)
            if prob is None:
                continue
            base = base_rates.get(f"{stat}_{need}plus")
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
                "empirical": None if empirical is None else round(empirical, 4),
                "modelled": None if modelled is None else round(modelled, 4),
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
# The model's stated probabilities were measured against 12,582 backtested
# picks and they are badly overconfident at exactly the end of the range the
# board selects from:
#
#     stated 0.3 -> actual 0.296   (well calibrated)
#     stated 0.5 -> actual 0.490   (well calibrated)
#     stated 0.6 -> actual 0.606   (well calibrated)
#     stated 0.7 -> actual 0.644
#     stated 0.8 -> actual 0.676
#     stated 0.9 -> actual 0.684   <- a "90%" pick hits 68% of the time
#
# Below about 0.65 the numbers mean what they say. Above it they do not, and
# the real hit rate ceilings near 0.68 no matter how confident the model gets.
#
# The consequence is worse than a cosmetic overstatement. Because the board
# RANKS by probability, and the overstatement GROWS with the number, the sort
# key was systematically selecting the most inflated picks. Ranking by
# probability was, in part, ranking by overconfidence.
#
# PLATT RATHER THAN ISOTONIC, chosen deliberately against the scoreboard.
# Isotonic fit the training set marginally better (held-out Brier 0.2227 vs
# 0.2232) but it is a step function defined only across the probabilities it
# was trained on, and it flatlines at the boundary outside that range. This
# calibrator was fitted before several scoring changes that shift the
# distribution of predicted probabilities, so out-of-domain inputs are
# expected rather than hypothetical. Platt is a smooth sigmoid that degrades
# gracefully. The 0.0005 of Brier is worth paying for that.
#
# STALENESS IS A REAL CAVEAT. This was fitted on backtested picks generated by
# an earlier version of the scorer. It should be refitted whenever scoring
# changes materially, and the raw uncalibrated figure is kept on every pick so
# the two can always be compared.
# ONE CURVE PER MARKET, not one curve for everything. Measured on held-out
# later dates, a dedicated calibrator beat the pooled one in all four markets:
#
#     market              raw     pooled      own
#     hits             0.2363     0.2371   0.2329
#     walks            0.2077     0.2067   0.2063
#     strikeouts       0.2802     0.2606   0.2394
#     first_inning     0.2047     0.2056   0.2044
#
# The pooled curve was actively HARMING the two largest markets: on hits and
# on first-inning props it scored worse than applying no calibration at all,
# because it was averaging away corrections that run in opposite directions.
# Strikeouts gain most, 15% over raw, which makes sense -- a starter's
# strikeout distribution has nothing in common with a batter's chance of a
# hit, and forcing them through one curve fits neither.
CALIBRATOR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "backtest", "calibrator.json")
CALIBRATORS_BY_MARKET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "backtest", "calibrators_by_market.json")


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


def apply_calibration(candidates, calibrator):
    """Replace each stated probability with its calibrated value, keeping the
    raw one alongside for comparison. Each market uses its own curve where one
    was fitted, falling back to the pooled curve otherwise."""
    if calibrator is None:
        return candidates
    per_market, glob = calibrator
    used = defaultdict(int)
    for c in candidates:
        p = c.get("hit_probability")
        if p is None:
            continue
        stat = (c.get("projection") or {}).get("stat")
        fn = per_market.get(stat) or glob
        if fn is None:
            continue
        try:
            cp = float(fn(p))
        except Exception:
            continue
        c["raw_hit_probability"] = p
        c["hit_probability"] = round(cp, 4)
        c["calibrated_by"] = stat if stat in per_market else "pooled"
        # Lift has to move with it, or the two disagree about the same pick.
        if c.get("base_rate") is not None:
            c["lift"] = round(c["hit_probability"] - c["base_rate"], 4)
        used[c["calibrated_by"]] += 1
    if used:
        detail = ", ".join(f"{k}:{v}" for k, v in sorted(used.items()))
        print(f"    Calibration applied ({detail})")
    return candidates

def attach_hit_probabilities(candidates, comp_table, emp_batters, emp_pitchers,
                             league_rates=None):
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
            c["hit_probability"] = best["prob"]
            c["probability_basis"] = best["basis"]
            c["base_rate"] = best.get("base_rate")
            c["lift"] = best.get("lift")
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
            prob, basis = _blend(empirical, modelled)
            c["hit_probability"] = None if prob is None else round(prob, 4)
            c["probability_basis"] = basis
            c["probability_detail"] = {
                "empirical": None if empirical is None else round(empirical, 4),
                "modelled": None if modelled is None else round(modelled, 4)}

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
                prob, basis = _blend(empirical, modelled)
                if prob is None:
                    continue
                base = ((league_rates or {}).get(f"strikeouts_{t}plus")
                        or (r or {}).get("league_p"))
                opts.append({"line": t - 0.5, "needs": t, "prob": round(prob, 4),
                             "basis": basis,
                             "base_rate": base,
                             "lift": None if base is None else round(prob - base, 4),
                             "empirical": None if empirical is None else round(empirical, 4),
                             "modelled": None if modelled is None else round(modelled, 4)})
            opts.sort(key=lambda o: o["prob"], reverse=True)
            best = _pick_line(opts)
            if best:
                c["prop"] = f"Over {best['line']} Strikeouts"
                c["projection"] = {"stat": "strikeouts", "value": best["line"],
                                   "needs": best["needs"]}
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

        elif stat == "walks":
            emp = emp_batters.get(pid) or {}
            comp = comp_table.get(pid) or {}
            empirical = None
            r = (emp.get("rates") or {}).get("walks_1plus")
            if r and emp.get("games", 0) >= MIN_EMPIRICAL_GAMES:
                empirical = r.get("p_hat", r["p"])
            modelled = None
            if comp.get("bb_rate") and c.get("projected_pa"):
                modelled = pp.p_at_least_walks(1, c["projected_pa"], comp["bb_rate"])
            prob, basis = _blend(empirical, modelled)
            c["hit_probability"] = None if prob is None else round(prob, 4)
            c["probability_basis"] = basis

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
    return candidates


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
        elif stat == "first_inning_run":
            n = int((c.get("signals") or {}).get("fi_n_starts") or 0)
        if rate:
            lo, hi = _wilson_interval(rate.get("hit", 0), rate.get("n", n) or 1)
            c["prob_ci"] = [round(lo, 4), round(hi, 4)]
        c["sample_n"] = int(n)
        for floor, grade, blurb in RELIABILITY_TIERS:
            if n >= floor:
                c["reliability"] = grade
                c["reliability_note"] = blurb
                break
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

    Returns (kept, rejected)."""
    lineups_confirmed = {}
    for gm in game_meta:
        for side in ("away", "home"):
            lu = gm.get(f"{side}_lineup") or []
            # A real posted lineup is nine hitters. Anything shorter is a
            # projection or a partial scrape, and a batter prop resting on a
            # guessed lineup slot is resting on the single strongest signal
            # in the whole model being invented.
            lineups_confirmed[(gm.get("game_pk"), side)] = len(lu) >= 9

    kept, rejected = [], []
    for c in candidates:
        stat = (c.get("projection") or {}).get("stat")
        reason = None

        if stat == "strikeouts":
            emp = emp_pitchers.get(c.get("player_id")) or {}
            starts = emp.get("starts", 0)
            avg_bf = emp.get("avg_bf")
            if avg_bf is not None and avg_bf < OPENER_BF_THRESHOLD:
                reason = (f"used as an opener ({avg_bf:.0f} batters faced per outing) — "
                          f"a strikeout prop on him is not the bet the model priced")
            elif starts and starts < 3:
                reason = f"only {starts} start(s) of evidence"

        if reason is None and c.get("type") == "batter":
            gp = c.get("game_pk")
            side = "away" if c.get("team") == next(
                (g.get("away_team") for g in game_meta if g.get("game_pk") == gp), None) else "home"
            if lineups_confirmed.get((gp, side)) is False:
                reason = ("lineup not confirmed — the batting-order slot is a guess, "
                          "and slot is the strongest single signal in the model")

        if reason is None:
            wx = park_wx.get(c.get("matchup")) or {}
            if not wx.get("dome") and (wx.get("precip_prob") or 0) >= QC_PRECIP_REJECT:
                reason = (f"{wx['precip_prob']}% rain risk — real chance of a "
                          f"postponement or a shortened game")

        if reason:
            c["qc_reason"] = reason
            rejected.append(c)
        else:
            kept.append(c)

    if rejected:
        print(f"    Quality control rejected {len(rejected)} candidate(s):")
        by_reason = defaultdict(int)
        for c in rejected:
            by_reason[c["qc_reason"].split(" — ")[0].split(" (")[0]] += 1
        for r, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            print(f"      {n:4d}  {r}")
    return kept, rejected

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

def write_markdown(top10, skipped, game_meta, bullpen_scores, all_ranked=()):
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
        lines.append("No candidates scored high enough today (thin slate, lineups mostly "
                     "unconfirmed, or data pulls came back empty) — check the run log.")
    for i, c in enumerate(top10, 1):
        hp = c.get("hit_probability")
        head = f"### {i}. {c['name']} ({c['team']}) — {c['prop']}"
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
                    "stolen_base": "Stolen Bases", "walks": "Walks",
                    "first_inning_run": "First Inning"}
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
                lines.append(f"- {c['name']} ({c['team']}) — {c['prop']} — "
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

    with open(PICKS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())

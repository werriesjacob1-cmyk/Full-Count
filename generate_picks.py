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
import os, sys, json, re
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
    """Real per-start first-inning results (not season-wide) for tonight's
    starters, via the same targeted Statcast pull mlb_daily.py's Section 38
    uses — reused here in structured form instead of parsing that section's
    text. Keyed by pitcher name (consistent with fetch_l14_pitcher_form)."""
    out = {}
    for name, pid in pitcher_ids.items():
        if not pid: continue
        try:
            df = m.pyb.statcast_pitcher(start_dt=m.L14_START, end_dt=m.L14_END, player_id=pid)
            if df is None or df.empty or "inning" not in df.columns: continue
            i1 = df[df["inning"] == 1].copy()
            if i1.empty: continue
            n_starts = i1["game_date"].nunique()
            if n_starts < 2: continue
            if all(c in i1.columns for c in ["bat_score", "post_bat_score"]):
                runs_per_game = i1.groupby("game_date").apply(
                    lambda g: g["post_bat_score"].max() - g["bat_score"].min())
                runs_per_game = runs_per_game.dropna()
                if len(runs_per_game) >= 2:
                    out[name] = {"n_starts": int(n_starts),
                                 "runs_per_1st_inning": round(runs_per_game.mean(), 2),
                                 "yrfi_rate": round((runs_per_game > 0).mean() * 100, 1)}
        except Exception:
            continue
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
            if (row := batter_lookup.get(b.get("name"))) and row.get("K%") is not None]
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


def name_lookup(df, name_col_candidates=("Name", "last_name, first_name")):
    """Build a {player_name: row_dict} lookup from a FanGraphs/Statcast DataFrame,
    handling the "Last, First" format Statcast endpoints use vs FanGraphs "First Last"."""
    if df is None or df.empty: return {}
    name_col = next((c for c in name_col_candidates if c in df.columns), None)
    if not name_col: return {}
    out = {}
    for _, row in df.iterrows():
        n = row[name_col]
        if name_col == "last_name, first_name" and isinstance(n, str) and "," in n:
            last, first = [p.strip() for p in n.split(",", 1)]
            n = f"{first} {last}"
        out[n] = row.to_dict()
    return out


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


def project_pitcher_ks(ps, l14):
    """Projected strikeouts for tonight's start: K rate x expected batters
    faced, where the workload is now a real per-pitcher estimate rather than
    a flat league constant (see project_pitcher_workload).

    This gates every strikeout prop. A pitcher averaging 19.5 batters faced
    cannot reach the same K total as one averaging 27.5 at the same K rate,
    and the old flat 22 asserted that he could."""
    l14 = l14 or {}
    if l14.get("l14_pa", 0) >= 15:
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
                  opp_bullpen_quality=None):
    name = batter["name"]
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

    return {
        "type": "batter", "name": name, "player_id": bid, "team": batter.get("team"), "matchup": gm["matchup"],
        "game_pk": gm.get("game_pk"), "prop": prop, "projection": {"stat": "total_bases", "value": projected_tb},
        "projected_pa": projected_pa, "projected_tb": projected_tb, "signals": signals,
        "score": round(score, 1), "why": why, "watchouts": watchouts, "notable_signals": notable_signals,
        "confidence": "High" if score >= 70 and not low_sample else ("Medium" if score >= 55 else "Low"),
    }


def score_pitcher(sp_name, sp_id, sp_hand, gm, side, pit_season_lookup, l14_form,
                   opp_lineup, opp_team_k_pct, ump_scores, opp_k_source=None):
    ps = pit_season_lookup.get(sp_name, {})
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

    projected_ks = project_pitcher_ks(ps, l14)
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
        "k_rate": (l14["l14_k_pct"] if l14.get("l14_pa", 0) >= 15 else (k_pct or 22.5)) / 100,
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


def score_stolen_base(batter, gm, opp_catcher_poptime, sprint_speed, batter_season):
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
    if not opp_catcher_poptime: watchouts.append("Opposing catcher pop time unavailable — matchup component defaulted to neutral")
    if on_base is None:
        watchouts.append("No usable on-base rate (no OBP, and wOBA sample under 40 PA) — "
                          "steal-opportunity component defaulted to neutral")
    elif on_base <= 25:
        watchouts.append("Fast, but a weak on-base rate means materially fewer times on first to steal from")

    signals = {}
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

    score = skill * 0.4 + matchup * 0.4 + context * 0.2
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
        "signals": {"yrfi_rate": round(float(yrfi_rate), 4), "fi_n_starts": float(n_starts)},
        "lean": lean, "score": round(score, 1), "why": why, "watchouts": watchouts,
        "notable_signals": notable_signals,
        "confidence": "High" if score >= 70 and n_starts >= 3 else ("Medium" if score >= 55 else "Low"),
    }


# ══════════════════════════════════════════════════════════════════════════
#  CANDIDATE ASSEMBLY
# ══════════════════════════════════════════════════════════════════════════

def build_candidates(game_meta, *, batter_lookup, pitcher_lookup, team_k_lookup,
                     park_wx, ump_scores, bullpen_scores, bullpen_quality,
                     sharp_bias, l7_form, bat_speed_trend, batter_arsenal,
                     pitcher_arsenal, sprint_speed, catcher_poptime,
                     l14_pitcher_form, fi_form):
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

    for gm in game_meta:
        opp_sp_row_for_away_batters = pitcher_lookup.get(gm["home_sp"], {})
        opp_sp_row_for_home_batters = pitcher_lookup.get(gm["away_sp"], {})
        wx = park_wx.get(gm["matchup"])
        away_opp_catcher_pop = catcher_poptime.get(catcher_by_team.get(gm["home_team"]))
        home_opp_catcher_pop = catcher_poptime.get(catcher_by_team.get(gm["away_team"]))
        away_opp_bullpen = bullpen_scores.get(gm["home_team"])  # away batters face the home team's pen
        home_opp_bullpen = bullpen_scores.get(gm["away_team"])
        away_opp_bullpen_quality = bullpen_quality.get(gm["home_team"])
        home_opp_bullpen_quality = bullpen_quality.get(gm["away_team"])

        for batter in gm.get("away_lineup", []):
            batter["team"] = gm["away_team"]
            bseason = batter_lookup.get(batter["name"])
            candidates.append(score_batter(batter, gm, opp_sp_row_for_away_batters, gm.get("home_sp_id"), gm.get("home_sp_hand"),
                              wx, bseason, l7_form.get(batter.get("id")), bat_speed_trend, batter_arsenal, pitcher_arsenal,
                              away_opp_bullpen, sharp_bias.get(gm["away_team"]), away_opp_bullpen_quality))
            for c in (score_stolen_base(batter, gm, away_opp_catcher_pop, sprint_speed.get(batter.get("id")), bseason),
                      score_walk(batter, gm, opp_sp_row_for_away_batters, ump_scores, bseason)):
                if c: candidates.append(c)
        for batter in gm.get("home_lineup", []):
            batter["team"] = gm["home_team"]
            bseason = batter_lookup.get(batter["name"])
            candidates.append(score_batter(batter, gm, opp_sp_row_for_home_batters, gm.get("away_sp_id"), gm.get("away_sp_hand"),
                              wx, bseason, l7_form.get(batter.get("id")), bat_speed_trend, batter_arsenal, pitcher_arsenal,
                              home_opp_bullpen, sharp_bias.get(gm["home_team"]), home_opp_bullpen_quality))
            for c in (score_stolen_base(batter, gm, home_opp_catcher_pop, sprint_speed.get(batter.get("id")), bseason),
                      score_walk(batter, gm, opp_sp_row_for_home_batters, ump_scores, bseason)):
                if c: candidates.append(c)

        if gm["away_sp"] != "TBD" and gm.get("away_sp_id"):
            opp_k, opp_k_source = team_k_lookup.get(gm["home_team"]), "team"
            if opp_k is None:
                opp_k, n = estimate_lineup_k_pct(gm.get("home_lineup", []), batter_lookup)
                opp_k_source = n
            candidates.append(score_pitcher(gm["away_sp"], gm["away_sp_id"], gm.get("away_sp_hand"),
                                             gm, "away", pitcher_lookup, l14_pitcher_form,
                                             gm.get("home_lineup", []), opp_k, ump_scores, opp_k_source))
            fi = score_first_inning(gm["away_sp"], gm["away_sp_id"], gm, "away", fi_form)
            if fi: candidates.append(fi)
        if gm["home_sp"] != "TBD" and gm.get("home_sp_id"):
            opp_k, opp_k_source = team_k_lookup.get(gm["away_team"]), "team"
            if opp_k is None:
                opp_k, n = estimate_lineup_k_pct(gm.get("away_lineup", []), batter_lookup)
                opp_k_source = n
            candidates.append(score_pitcher(gm["home_sp"], gm["home_sp_id"], gm.get("home_sp_hand"),
                                             gm, "home", pitcher_lookup, l14_pitcher_form,
                                             gm.get("away_lineup", []), opp_k, ump_scores, opp_k_source))
            fi = score_first_inning(gm["home_sp"], gm["home_sp_id"], gm, "home", fi_form)
            if fi: candidates.append(fi)

    # Rain risk applies to every prop type in a game equally (a postponement
    # or delay affects the batter and the pitcher and the NRFI lean alike),
    # so it's applied once here as a uniform watchout rather than threaded
    # through every score_* signature separately. Informational only — not
    # folded into score, since "check before betting" is the honest framing
    # for a probability that's still hours out and can shift either way.
    for c in candidates:
        wx = park_wx.get(c["matchup"])
        if wx and not wx.get("dome") and (wx.get("precip_prob") or 0) >= 50:
            c["watchouts"].append(f"Rain risk tonight ({wx['precip_prob']}% precipitation probability) — game could be delayed or postponed")
    return candidates


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

def main() -> int:
    print("Generating top 10 picks (deterministic scoring, no LLM call)...")

    lineup_text, game_meta, player_ids = m.fetch_lineups(m.TODAY)
    if not game_meta:
        with open(PICKS_FILE, "w", encoding="utf-8") as f:
            f.write(f"# MLB Top 10 Picks — {m.TODAY}\n\nNo games found today.\n")
        with open(PICKS_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": m.TODAY, "picks": []}, f, indent=2)
        print("No games today — wrote placeholder picks files.")
        return 0

    print(f"{len(game_meta)} games found. Pulling scoring inputs...")
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

    candidates = build_candidates(
        game_meta,
        batter_lookup=batter_lookup, pitcher_lookup=pitcher_lookup,
        team_k_lookup=team_k_lookup, park_wx=park_wx, ump_scores=ump_scores,
        bullpen_scores=bullpen_scores, bullpen_quality=bullpen_quality,
        sharp_bias=sharp_bias, l7_form=l7_form, bat_speed_trend=bat_speed_trend,
        batter_arsenal=batter_arsenal, pitcher_arsenal=pitcher_arsenal,
        sprint_speed=sprint_speed, catcher_poptime=catcher_poptime,
        l14_pitcher_form=l14_pitcher_form, fi_form=fi_form)

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
    comp_table, emp_batters, emp_pitchers = {}, {}, {}
    try:
        import mlb_sources as _src
        comp_table = _src.batter_pa_composition()
        bat_ids = [c["player_id"] for c in candidates
                   if c.get("type") == "batter" and c.get("player_id")]
        pit_ids = [c["player_id"] for c in candidates
                   if c.get("type") == "pitcher" and c.get("player_id")]
        emp_batters = _src.empirical_batter_prop_rates(bat_ids)
        emp_pitchers = _src.empirical_pitcher_k_rates(pit_ids)
        print(f"    Hit-probability inputs: {len(comp_table)} batter rate lines, "
              f"{len(emp_batters)} batter game logs, {len(emp_pitchers)} pitcher game logs")
    except Exception as e:
        m.warn(f"Hit-probability inputs unavailable ({e}) — "
               f"falling back to score-only ranking")

    attach_hit_probabilities(candidates, comp_table, emp_batters, emp_pitchers)

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
    gated = [c for c in candidates if c["score"] >= MIN_QUALITY_SCORE]
    priced = [c for c in gated if c.get("hit_probability") is not None]
    unpriced = [c for c in gated if c.get("hit_probability") is None]
    priced.sort(key=lambda c: (c["hit_probability"], c["score"]), reverse=True)
    unpriced.sort(key=lambda c: c["score"], reverse=True)
    ranked = priced + unpriced
    if not ranked:   # nothing cleared the gate — fall back rather than ship nothing
        ranked = sorted(candidates, key=lambda c: c["score"], reverse=True)
    top10 = ranked[:10]
    skipped = [c for c in ranked[10:13] if c["score"] >= 55][:2]

    write_markdown(top10, skipped, game_meta, bullpen_scores)
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

# Empirical rates need a real sample before they outrank a model that at
# least accounts for tonight. Below this the model carries the estimate.
MIN_EMPIRICAL_GAMES = 25

# How much weight the empirical (backward-looking, assumption-free) rate
# gets against the modelled (context-aware, assumption-heavy) one. The
# empirical read anchors because it is measured rather than derived, but it
# cannot see tonight's catcher, park or opposing starter, so the model keeps
# a real share. This split is a starting position, not a fitted result --
# backtest/signals.py exists to replace it with something measured.
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


def _batter_options(c, comp, emp):
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

    def emp_p(key):
        r = rates.get(key)
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
            options.append({
                "stat": stat, "line": line, "needs": need,
                "label": f"Over {line} {label}",
                "prob": round(prob, 4), "basis": basis,
                "empirical": None if empirical is None else round(empirical, 4),
                "modelled": None if modelled is None else round(modelled, 4),
            })
    options.sort(key=lambda o: o["prob"], reverse=True)
    return options


# Starts of slate-average evidence mixed into each pitcher's own first-inning
# record. Deliberately heavier relative to sample size than the batter prior:
# the L14 window gives most starters only 2-3 first innings, so almost all of
# these rates are near-worthless on their own.
FI_PRIOR_STARTS = 5.0

# Fallback only, used when the slate has no first-inning data at all to
# average. Roughly the league rate at which a starting pitcher allows a run
# in the first.
LEAGUE_YRFI_RATE = 0.27


def attach_hit_probabilities(candidates, comp_table, emp_batters, emp_pitchers):
    """Give every candidate a real chance-of-cashing number, and let the
    batter props re-choose their threshold to maximise it.

    Anything this cannot price keeps its original prop and gets a null
    probability rather than a guess -- an invented number here would rank
    against real ones, which is worse than an honest gap."""
    # The slate's own average first-inning scoring rate, start-weighted. Using
    # tonight's starters rather than a hardcoded constant keeps the prior
    # tracking the real run environment.
    fi_runs = fi_starts = 0.0
    for c in candidates:
        if (c.get("projection") or {}).get("stat") == "first_inning_run":
            n = (c.get("signals") or {}).get("fi_n_starts") or 0
            v = (c.get("projection") or {}).get("value")
            if n and v is not None:
                fi_runs += max(0.0, min(1.0, float(v) / 100.0)) * n
                fi_starts += n
    slate_yrfi = (fi_runs / fi_starts) if fi_starts else LEAGUE_YRFI_RATE

    for c in candidates:
        pid = c.get("player_id")
        stat = (c.get("projection") or {}).get("stat")

        if c.get("type") == "batter" and stat == "total_bases":
            opts = _batter_options(c, comp_table.get(pid), emp_batters.get(pid))
            usable = [o for o in opts
                      if pp.MIN_USEFUL_PROB <= o["prob"] <= pp.MAX_USEFUL_PROB]
            best = (usable or opts or [None])[0]
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
                opts.append({"line": t - 0.5, "needs": t, "prob": round(prob, 4),
                             "basis": basis,
                             "empirical": None if empirical is None else round(empirical, 4),
                             "modelled": None if modelled is None else round(modelled, 4)})
            opts.sort(key=lambda o: o["prob"], reverse=True)
            usable = [o for o in opts
                      if pp.MIN_USEFUL_PROB <= o["prob"] <= pp.MAX_USEFUL_PROB]
            best = (usable or opts or [None])[0]
            if best:
                c["prop"] = f"Over {best['line']} Strikeouts"
                c["projection"] = {"stat": "strikeouts", "value": best["line"],
                                   "needs": best["needs"]}
                c["hit_probability"] = best["prob"]
                c["probability_basis"] = best["basis"]
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
                is_yrfi = (c.get("lean") or "").upper().startswith("Y")
                c["hit_probability"] = round(shrunk if is_yrfi else 1.0 - shrunk, 4)
                c["probability_basis"] = "empirical"
                c["probability_detail"] = {
                    "empirical": round(raw if is_yrfi else 1.0 - raw, 4),
                    "modelled": None}
        else:
            c.setdefault("hit_probability", None)
    return candidates


def write_markdown(top10, skipped, game_meta, bullpen_scores):
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
                 f"must score {MIN_QUALITY_SCORE:.0f}+ to make the board at all.")
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
            lines.append(f"- **Chance of hitting:** {hp*100:.1f}%{basis}")
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

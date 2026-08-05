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

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)
PICKS_FILE = os.path.join(OUTPUT_DIR, f"top10_picks_{m.TODAY}.md")
PICKS_JSON_FILE = os.path.join(OUTPUT_DIR, f"picks_{m.TODAY}.json")
PLAYERS_DIR = os.environ.get("PLAYERS_DIR", "data/players")
PLAYER_SNAPSHOT_HISTORY_DAYS = 60  # bounds each player file's growth over a season
LEAGUE_AVG_TB_PA = 0.38     # league-average total bases per PA, used when no player data is available
LEAGUE_AVG_BF_PER_START = 22  # league-average batters faced per start, used to convert K% into a projected K count

# ══════════════════════════════════════════════════════════════════════════
#  SCORING HELPERS
# ══════════════════════════════════════════════════════════════════════════

def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))

def scale(value, lo, hi, out_lo=0, out_hi=100):
    """Linear map value in [lo,hi] to [out_lo,out_hi], clamped at the ends."""
    if value is None: return (out_lo + out_hi) / 2
    try: value = float(value)
    except (TypeError, ValueError): return (out_lo + out_hi) / 2
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
                "hourly": "temperature_2m,windspeed_10m,winddirection_10m,relativehumidity_2m",
                "temperature_unit": "fahrenheit", "windspeed_unit": "mph",
                "timezone": "auto", "forecast_days": 1,
            }, timeout=20, retries=2)
            r.raise_for_status()
            h = r.json()["hourly"]
            idx = min(max(gm["hour"], 0), 23)
            temp = h["temperature_2m"][idx]; wsp = h["windspeed_10m"][idx]
            wdir = h["winddirection_10m"][idx]; humid = h["relativehumidity_2m"][idx]

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

            wvf = m.wind_vs_field(wdir, cf_deg, dome)
            dens = m.air_density_pct(elev, temp, humid)
            idx_score = 50
            if "OUT" in wvf.upper(): idx_score += min(wsp * 2.5, 30)
            elif "IN" in wvf.upper(): idx_score -= min(wsp * 2.5, 25)
            if temp >= 85: idx_score += 8
            elif temp <= 45: idx_score -= 10
            idx_score += (1.0 - dens) * 100 * 0.3
            wind_effect = "out" if "OUT" in wvf.upper() else ("in" if "IN" in wvf.upper() else "neutral")
            out[gm["matchup"]] = {"dome": False, "park_hr_index": round(clamp(idx_score), 1),
                                   "wind_effect": wind_effect, "temp": temp, "wind_mph": wsp,
                                   "wx_disagreement": wx_disagreement}
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
    filter — this is not folded into the weighted formula's core components."""
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
            ml = g["markets"]["15"]["event"]["moneyline"]
        except (KeyError, TypeError):
            continue
        for entry in ml:
            team_name = teams.get(entry.get("team_id"))
            bi = entry.get("bet_info", {})
            tickets = bi.get("tickets", {}).get("percent")
            money = bi.get("money", {}).get("percent")
            if team_name and tickets is not None and money is not None:
                out[team_name] = {"tickets_pct": tickets, "money_pct": money,
                                   "sharp_divergence": money - tickets}
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
    already carry from fetch_lineups()."""
    try:
        df = m.pyb.statcast(start_dt=m.L7_START, end_dt=m.L7_END)
        if df is None or df.empty: return {}
        batted = df[df["launch_speed"].notna()].copy()
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

def project_batter_tb(bs, l7, order):
    """Projected total bases for tonight, blending L7 form and season skill.
    TB/AB = AVG + ISO is a standard sabermetric identity; when ISO isn't
    available (Statcast-fallback season data has no ISO column), approximate
    from AVG with a league-average power multiplier instead of silently
    defaulting to a flat rate."""
    season_rate = None
    if bs:
        avg = bs.get("AVG"); iso = bs.get("ISO")
        if avg is not None and iso is not None:
            season_rate = avg + iso
        elif avg is not None:
            season_rate = avg * 1.35  # approximation when ISO isn't available
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
    pa_est = m.ORDER_PA.get(min(order or 9, 9), 630) / 162
    return round(rate * pa_est, 2)


def project_pitcher_ks(ps, l14):
    """Projected strikeouts for tonight's start, blending L14 form and season
    K%, scaled by a league-average batters-faced-per-start estimate (Statcast-
    fallback season data doesn't carry innings/BF, so this is an explicit
    approximation, not a precise per-pitcher workload model)."""
    l14 = l14 or {}
    if l14.get("l14_pa", 0) >= 15:
        k_pct = l14["l14_k_pct"]
    elif ps and ps.get("K%"):
        k_pct = ps["K%"]
    else:
        k_pct = 22.5
    return round(k_pct / 100 * LEAGUE_AVG_BF_PER_START, 1)


# ══════════════════════════════════════════════════════════════════════════
#  SCORING
# ══════════════════════════════════════════════════════════════════════════

LEAGUE_AVG_EV = 88.5

def score_batter(batter, gm, opp_sp_row, opp_sp_id, opp_sp_hand, park_wx, batter_season, batter_l7,
                  bat_speed_trend, batter_arsenal, pitcher_arsenal, opp_bullpen=None, sharp_bias=None):
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
    form = scale(l7.get("avg_EV"), 85, 93) * 0.6 + scale(l7.get("barrel_pct"), 2, 16) * 0.4
    low_sample = l7_pa < 8
    bs_trend = bat_speed_trend.get(bid) if bid else None
    if bs_trend is not None and bs_trend >= 1.0:
        form = clamp(form + scale(bs_trend, 1.0, 3.0, 0, 15))
        notable_signals += 1

    # ENVIRONMENT (15%)
    env = park_wx.get("park_hr_index", 50) if park_wx else 50
    if env >= 70 or env <= 30: notable_signals += 1

    # BASELINE SKILL (15%)
    bs = batter_season or {}
    skill = (scale(bs.get("wRC+"), 70, 140) * 0.4 + scale(bs.get("ISO"), 0.10, 0.28) * 0.3
             + scale(bs.get("Barrel%"), 3, 18) * 0.3)
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
    if bullpen_fatigue_pct is not None:
        context = clamp(lineup_context * 0.7 + scale(bullpen_fatigue_pct, 0, 60) * 0.3)
        if bullpen_fatigue_pct >= 40:
            notable_signals += 1
    else:
        context = lineup_context

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

    # Public-awareness discount: a pick leaning entirely on "star + high average,"
    # with no other converging signal, is exactly what the market already prices —
    # not useful. Downweight it. A pick with 2+ non-obvious signals gets a small
    # boost instead, even on a more middling player.
    if star_profile and notable_signals == 0:
        score -= 10
        watchouts.append("Built mainly on season-long star power with no additional converging signal — likely already priced by the market")
    elif notable_signals >= 2:
        score = clamp(score + 5)

    projected_tb = project_batter_tb(bs, l7, order)
    if exploit and exploit["hard_hit_percent"] and (exploit["hard_hit_percent"] >= 45 or projected_tb >= 1.8):
        prop = f"Home Run / 2+ Total Bases (proj. {projected_tb} TB)"
    elif (bs.get("K%") or 30) <= 18:
        prop = f"Over 1.5 Hits (proj. {projected_tb} TB)"
    else:
        prop = f"Over 1.5 Total Bases (proj. {projected_tb} TB)"

    why = []
    why.append(f"Platoon: {bats} bat vs {opp_sp_hand or '?'}HP ({'favorable' if platoon>=65 else 'unfavorable'})")
    if exploit:
        why.append(f"Pitch-type exploit: RV/100 {exploit['run_value_per_100']:+.1f} vs {exploit['pitch_type']} "
                    f"(opposing SP throws it {exploit['usage_pct']}% of the time)")
    if sp_era is not None: why.append(f"Opposing SP ERA {sp_era:.2f}")
    if l7.get("avg_EV"): why.append(f"L7 avg EV {l7['avg_EV']:.1f}mph (league ~{LEAGUE_AVG_EV})")
    if l7.get("barrel_pct") is not None: why.append(f"L7 barrel% {l7['barrel_pct']}")
    if bs_trend is not None and bs_trend >= 1.0: why.append(f"Bat speed trending up L14 ({bs_trend:+.1f}mph 2nd-half vs 1st-half)")
    if bs.get("wRC+"): why.append(f"Season wRC+ {bs['wRC+']:.0f}")
    if not park_wx or park_wx.get("dome"): why.append("Dome — weather neutral")
    elif park_wx.get("wind_effect") == "out": why.append(f"Wind blowing OUT ({park_wx.get('wind_mph',0):.0f}mph) — HR boost")
    elif park_wx.get("wind_effect") == "in": why.append("Wind blowing IN — power suppressed")
    if bullpen_fatigue_pct is not None:
        why.append(f"Opposing bullpen fatigue: {fatigued}/{tracked} relievers over 60 pitches in L7 "
                    f"({'tired pen — favorable late' if bullpen_fatigue_pct >= 40 else 'fresh pen'})")
    if sharp_divergence is not None and abs(sharp_divergence) >= 10:
        why.append(f"Sharp money {'backing' if sharp_divergence > 0 else 'fading'} {batter.get('team')} "
                    f"(money% {'+' if sharp_divergence>0 else ''}{sharp_divergence} pts vs ticket%)")

    return {
        "type": "batter", "name": name, "player_id": bid, "team": batter.get("team"), "matchup": gm["matchup"],
        "game_pk": gm.get("game_pk"), "prop": prop, "projection": {"stat": "total_bases", "value": projected_tb},
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
    matchup = scale(opp_team_k_pct, 17, 27) * 0.65 + scale(same_hand_ratio * 100, 20, 60) * 0.35

    # RECENT FORM (25%) — L14 K rate vs season K rate (trend) + TTO-specific exploit
    l14 = l14_form.get(sp_name, {})
    if l14 and l14.get("l14_pa", 0) >= 15:
        form = scale(l14.get("l14_k_pct"), 15, 32)
        low_sample_form = False
    else:
        form = scale(k_pct, 15, 32) if k_pct else 50
        low_sample_form = True
    tto_note = None
    if l14.get("tto3_k_pct") is not None and l14.get("tto1_k_pct") is not None:
        tto_penalty = l14["tto1_k_pct"] - l14["tto3_k_pct"]
        if tto_penalty <= -3:  # maintains or improves K rate deep into starts
            form = clamp(form + 8)
            notable_signals += 1
            tto_note = f"Maintains K rate through the order (TTO1 {l14['tto1_k_pct']}% -> TTO3 {l14['tto3_k_pct']}%)"
        elif tto_penalty >= 8:
            form = clamp(form - 8)
            tto_note = f"Significant TTO K% drop-off (TTO1 {l14['tto1_k_pct']}% -> TTO3 {l14['tto3_k_pct']}%) — caution deep into the start"

    # ENVIRONMENT (15%) — minor for Ks; kept neutral
    env = 50

    # BASELINE SKILL (15%)
    skill = scale(k_pct, 15, 32) * 0.4 + scale(csw, 24, 34) * 0.3 + scale(stuff, 80, 130) * 0.3
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

    if opp_team_k_pct is not None:
        k_note = (f"Opposing team K% {opp_team_k_pct:.1f}" if opp_k_source == "team"
                   else f"Opposing lineup K% {opp_team_k_pct:.1f} (avg of {opp_k_source} confirmed batters — FanGraphs team page unreachable)")
    else:
        k_note = "Opposing team K% unavailable (FanGraphs team page down and no confirmed lineup batters matched)"
    why = [k_note]
    why.append(f"{same_hand}/{known} known-hand opposing batters same-handed" if known else "Opposing lineup handedness mostly unknown")
    if k_pct: why.append(f"Season K% {k_pct}")
    if csw: why.append(f"CSW% {csw}")
    if stuff: why.append(f"Stuff+ {stuff}")
    if l14.get("l14_k_pct") is not None: why.append(f"L14 K% {l14['l14_k_pct']} ({l14.get('l14_pa')} PA)")
    if tto_note and "Maintains" in tto_note: why.append(tto_note)
    if ump.get("accuracy"): why.append(f"HP ump accuracy {ump['accuracy']:.1f}%")

    return {
        "type": "pitcher", "name": sp_name, "player_id": sp_id,
        "team": gm["away_team"] if side == "away" else gm["home_team"],
        "matchup": gm["matchup"], "game_pk": gm.get("game_pk"),
        "prop": f"Over {max(projected_ks - 0.5, 0.5):.1f} Strikeouts (proj. {projected_ks})",
        "projection": {"stat": "strikeouts", "value": projected_ks},
        "score": round(score, 1), "why": why, "watchouts": watchouts, "notable_signals": notable_signals,
        "confidence": "High" if score >= 70 and not low_sample_form else ("Medium" if score >= 55 else "Low"),
    }


LEAGUE_AVG_SPRINT = 27.0     # ft/s
LEAGUE_AVG_POPTIME = 2.0     # seconds, catcher pop time to 2B
LEAGUE_AVG_BB_PCT = 8.5

def score_stolen_base(batter, gm, opp_catcher_poptime, sprint_speed, batter_season):
    """Speed (skill) is the dominant signal here — a player has to be a real
    stolen-base threat before the matchup context matters at all. Verified
    live against Statcast sprint speed + catcher pop-time data."""
    bid = batter.get("id")
    if not sprint_speed or sprint_speed < 27.3:
        return None  # not a plausible SB threat regardless of matchup
    notable_signals = 0
    skill = scale(sprint_speed, 27.3, 30.5)
    matchup = scale(opp_catcher_poptime, 2.25, 1.90) if opp_catcher_poptime else 50
    if opp_catcher_poptime and opp_catcher_poptime >= 2.10: notable_signals += 1
    bs = batter_season or {}
    season_sb = bs.get("SB")
    context = scale(season_sb, 3, 25) if season_sb is not None else 50
    if season_sb and season_sb >= 15: notable_signals += 1

    score = skill * 0.55 + matchup * 0.30 + context * 0.15
    why = [f"Sprint speed {sprint_speed:.1f}ft/s (league ~{LEAGUE_AVG_SPRINT})"]
    if opp_catcher_poptime: why.append(f"Opposing catcher pop time {opp_catcher_poptime:.2f}s to 2B (league ~{LEAGUE_AVG_POPTIME}s)")
    if season_sb is not None: why.append(f"Season SB: {season_sb}")
    watchouts = []
    if not opp_catcher_poptime: watchouts.append("Opposing catcher pop time unavailable — matchup component defaulted to neutral")

    return {
        "type": "batter", "name": batter["name"], "player_id": bid, "team": batter.get("team"),
        "matchup": gm["matchup"], "game_pk": gm.get("game_pk"), "prop": "To Steal a Base",
        "projection": {"stat": "stolen_base", "value": 1}, "score": round(score, 1),
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

    return {
        "type": "batter", "name": batter["name"], "player_id": batter.get("id"), "team": batter.get("team"),
        "matchup": gm["matchup"], "game_pk": gm.get("game_pk"), "prop": "Over 0.5 Walks",
        "projection": {"stat": "walks", "value": 0.7}, "score": round(score, 1),
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
    lean = "YRFI" if yrfi_rate >= 38 else "NRFI"
    score = scale(yrfi_rate, 0, 100) if lean == "YRFI" else scale(yrfi_rate, 100, 0)
    sample_penalty = max(0, (5 - n_starts) * 15)  # 2 starts: -45; 3: -30; 4: -15; 5+: none
    score = clamp(score - sample_penalty)
    if n_starts < 3:
        score = min(score, 55)  # a 2-start read is never more than a low/medium-confidence lean
    notable_signals = 1 if (yrfi_rate >= 55 or yrfi_rate <= 10) and n_starts >= 3 else 0
    why = [f"1st-inning runs/start {fi['runs_per_1st_inning']} across {n_starts} starts (L14)",
           f"YRFI rate {yrfi_rate}%"]
    watchouts = []
    if n_starts < 3: watchouts.append(f"Only {n_starts} starts in the L14 window — thin sample for a first-inning read")
    return {
        "type": "pitcher", "name": sp_name, "player_id": sp_id,
        "team": gm["away_team"] if side == "away" else gm["home_team"],
        "matchup": gm["matchup"], "game_pk": gm.get("game_pk"),
        "prop": f"{lean} lean (his starts)", "projection": {"stat": "first_inning_run", "value": yrfi_rate},
        "score": round(score, 1), "why": why, "watchouts": watchouts, "notable_signals": notable_signals,
        "confidence": "High" if score >= 70 and n_starts >= 3 else ("Medium" if score >= 55 else "Low"),
    }


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
    sharp_bias = fetch_public_betting_bias(game_meta)
    l7_form = fetch_l7_batter_form()
    bat_speed_trend = fetch_bat_speed_trends()
    batter_arsenal, pitcher_arsenal = fetch_pitch_type_exploits()
    sprint_speed = fetch_sprint_speed()
    catcher_poptime = fetch_catcher_poptime()

    catcher_by_team = {}
    for gm in game_meta:
        for side, team_key in [("away_lineup", "away_team"), ("home_lineup", "home_team")]:
            for p in gm.get(side, []):
                if p.get("pos") == "C" and p.get("id"):
                    catcher_by_team[gm[team_key]] = p["id"]

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

    candidates = []

    for gm in game_meta:
        opp_sp_row_for_away_batters = pitcher_lookup.get(gm["home_sp"], {})
        opp_sp_row_for_home_batters = pitcher_lookup.get(gm["away_sp"], {})
        wx = park_wx.get(gm["matchup"])
        away_opp_catcher_pop = catcher_poptime.get(catcher_by_team.get(gm["home_team"]))
        home_opp_catcher_pop = catcher_poptime.get(catcher_by_team.get(gm["away_team"]))
        away_opp_bullpen = bullpen_scores.get(gm["home_team"])  # away batters face the home team's pen
        home_opp_bullpen = bullpen_scores.get(gm["away_team"])

        for batter in gm.get("away_lineup", []):
            batter["team"] = gm["away_team"]
            bseason = batter_lookup.get(batter["name"])
            candidates.append(score_batter(batter, gm, opp_sp_row_for_away_batters, gm.get("home_sp_id"), gm.get("home_sp_hand"),
                              wx, bseason, l7_form.get(batter.get("id")), bat_speed_trend, batter_arsenal, pitcher_arsenal,
                              away_opp_bullpen, sharp_bias.get(gm["away_team"])))
            for c in (score_stolen_base(batter, gm, away_opp_catcher_pop, sprint_speed.get(batter.get("id")), bseason),
                      score_walk(batter, gm, opp_sp_row_for_away_batters, ump_scores, bseason)):
                if c: candidates.append(c)
        for batter in gm.get("home_lineup", []):
            batter["team"] = gm["home_team"]
            bseason = batter_lookup.get(batter["name"])
            candidates.append(score_batter(batter, gm, opp_sp_row_for_home_batters, gm.get("away_sp_id"), gm.get("away_sp_hand"),
                              wx, bseason, l7_form.get(batter.get("id")), bat_speed_trend, batter_arsenal, pitcher_arsenal,
                              home_opp_bullpen, sharp_bias.get(gm["home_team"])))
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

    # Pure score ranking, no per-game or per-prop-type cap — per explicit
    # direction, the top 10 doesn't have to be diverse across categories or
    # games; forcing variety just to have variety would mean swapping out a
    # genuinely better pick for a worse one, which is the opposite of the
    # goal. If the 10 best-scoring picks all happen to be the same prop type
    # or the same game, that's what goes out. (This is why score_first_inning
    # carries a hard confidence cap on thin samples — with no cap here to
    # catch it, an inflated score from a weak signal would otherwise be free
    # to sweep the list on its own.)
    candidates.sort(key=lambda c: c["score"], reverse=True)
    top10 = candidates[:10]
    skipped = [c for c in candidates[10:12] if c["score"] >= 55]

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
            "team": c["team"], "matchup": c["matchup"], "game_pk": c["game_pk"],
            "prop": c["prop"], "projection": c["projection"], "score": c["score"],
            "confidence": c["confidence"], "notable_signals": c["notable_signals"],
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


def write_markdown(top10, skipped, game_meta, bullpen_scores):
    lines = [f"# MLB Top 10 Picks — {m.TODAY}", "",
             "_Generated by deterministic weighted scoring over today's research pull — "
             "no LLM in the loop. No live sportsbook odds were fetched: these are "
             "direction/confidence calls grounded in stats and trends, not priced bets. "
             "**Check the current line and availability before betting.** Every pick's "
             "projection is graded against the actual box score the next morning — see "
             "results/history.json for the running accuracy record._", ""]

    lines.append(f"**Slate:** {len(game_meta)} games. "
                 f"Methodology: 35% matchup / 25% recent form / 15% environment / "
                 f"15% baseline skill / 10% context, with a public-awareness discount "
                 f"applied against star-power-only picks (see README).")
    lines.append("")

    if not top10:
        lines.append("No candidates scored high enough today (thin slate, lineups mostly "
                     "unconfirmed, or data pulls came back empty) — check the run log.")
    for i, c in enumerate(top10, 1):
        lines.append(f"### {i}. {c['name']} ({c['team']}) — {c['prop']}")
        lines.append(f"- **Matchup:** {c['matchup']}")
        lines.append(f"- **Score:** {c['score']}/100  |  **Confidence:** {c['confidence']}  |  "
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
            lines.append(f"- {c['name']} ({c['prop']}, score {c['score']}) — {reason}.")
        lines.append("")

    with open(PICKS_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())

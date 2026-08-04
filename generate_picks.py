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
MAX_PICKS_PER_GAME = 3
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

def fetch_park_weather(game_meta):
    """Per-matchup weather + park HR index. Same sources/logic as mlb_daily.py's
    Section 5, kept independent here rather than parsing that section's text."""
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
                                   "wind_effect": wind_effect, "temp": temp, "wind_mph": wsp}
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
                  bat_speed_trend, batter_arsenal, pitcher_arsenal):
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

    # CONTEXT (10%) — lineup slot
    context = scale(10 - order, 1, 9)

    score = matchup * 0.35 + form * 0.25 + env * 0.15 + skill * 0.15 + context * 0.10

    watchouts = []
    if low_sample:
        watchouts.append(f"L7 sample is thin ({l7_pa} PA) — treat recent-form read with caution")
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

    return {
        "type": "batter", "name": name, "player_id": bid, "team": batter.get("team"), "matchup": gm["matchup"],
        "game_pk": gm.get("game_pk"), "prop": prop, "projection": {"stat": "total_bases", "value": projected_tb},
        "score": round(score, 1), "why": why, "watchouts": watchouts, "notable_signals": notable_signals,
        "confidence": "High" if score >= 70 and not low_sample else ("Medium" if score >= 55 else "Low"),
    }


def score_pitcher(sp_name, sp_id, sp_hand, gm, side, pit_season_lookup, l14_form,
                   opp_lineup, opp_team_k_pct, ump_scores):
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

    why = [f"Opposing team K% {opp_team_k_pct:.1f}" if opp_team_k_pct else "Opposing team K% unavailable"]
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
    l7_form = fetch_l7_batter_form()
    bat_speed_trend = fetch_bat_speed_trends()
    batter_arsenal, pitcher_arsenal = fetch_pitch_type_exploits()

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

    candidates = []

    for gm in game_meta:
        opp_sp_row_for_away_batters = pitcher_lookup.get(gm["home_sp"], {})
        opp_sp_row_for_home_batters = pitcher_lookup.get(gm["away_sp"], {})
        wx = park_wx.get(gm["matchup"])

        for batter in gm.get("away_lineup", []):
            batter["team"] = gm["away_team"]
            c = score_batter(batter, gm, opp_sp_row_for_away_batters, gm.get("home_sp_id"), gm.get("home_sp_hand"),
                              wx, batter_lookup.get(batter["name"]), l7_form.get(batter.get("id")),
                              bat_speed_trend, batter_arsenal, pitcher_arsenal)
            candidates.append(c)
        for batter in gm.get("home_lineup", []):
            batter["team"] = gm["home_team"]
            c = score_batter(batter, gm, opp_sp_row_for_home_batters, gm.get("away_sp_id"), gm.get("away_sp_hand"),
                              wx, batter_lookup.get(batter["name"]), l7_form.get(batter.get("id")),
                              bat_speed_trend, batter_arsenal, pitcher_arsenal)
            candidates.append(c)

        if gm["away_sp"] != "TBD" and gm.get("away_sp_id"):
            opp_k = team_k_lookup.get(gm["home_team"])
            candidates.append(score_pitcher(gm["away_sp"], gm["away_sp_id"], gm.get("away_sp_hand"),
                                             gm, "away", pitcher_lookup, l14_pitcher_form,
                                             gm.get("home_lineup", []), opp_k, ump_scores))
        if gm["home_sp"] != "TBD" and gm.get("home_sp_id"):
            opp_k = team_k_lookup.get(gm["away_team"])
            candidates.append(score_pitcher(gm["home_sp"], gm["home_sp_id"], gm.get("home_sp_hand"),
                                             gm, "home", pitcher_lookup, l14_pitcher_form,
                                             gm.get("away_lineup", []), opp_k, ump_scores))

    candidates.sort(key=lambda c: c["score"], reverse=True)

    top10 = []
    per_game_count = defaultdict(int)
    skipped = []
    for c in candidates:
        if len(top10) >= 10: break
        if per_game_count[c["matchup"]] >= MAX_PICKS_PER_GAME:
            continue
        top10.append(c)
        per_game_count[c["matchup"]] += 1
    for c in candidates:
        if len(skipped) >= 2: break
        if c not in top10 and c["score"] >= 55:
            skipped.append(c)

    write_markdown(top10, skipped, game_meta, bullpen_scores)
    write_json(top10)
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

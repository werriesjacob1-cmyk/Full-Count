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
  25% RECENT FORM     — L7/L14 rolling performance
  15% ENVIRONMENT     — weather, wind vs. park orientation, park HR factor
  15% BASELINE SKILL   — season-long established skill level
  10% CONTEXT         — lineup slot / umpire zone / platoon lineup composition

Weighted toward trend/data convergence (how many independent signals agree)
rather than a single computed statistical edge, per explicit direction — and
negative-edge patterns (hot form contradicted by weak underlying batted-ball
quality) are actively penalized, not ignored.

No live sportsbook odds are fetched (deliberately out of scope — see README),
so every pick is a direction/confidence call, not a priced bet: always check
the current Fanatics line before betting.

Runs after mlb_daily.py in the same job. Thanks to pybaseball's on-disk
cache (already enabled, same cache dir within one job run), re-calling shared
fetchers here does not mean a second round of network traffic for anything
mlb_daily.py already pulled.
"""
import os, sys, json, re
from datetime import datetime
from collections import defaultdict

import mlb_daily as m

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)
PICKS_FILE = os.path.join(OUTPUT_DIR, f"top10_picks_{m.TODAY}.md")
MAX_PICKS_PER_GAME = 3

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
            # Park HR index: baseline 50, pushed up/down by wind direction/speed,
            # temperature, altitude/air-density, and known short-porch parks.
            idx_score = 50
            if "OUT" in wvf.upper(): idx_score += min(wsp * 2.5, 30)
            elif "IN" in wvf.upper(): idx_score -= min(wsp * 2.5, 25)
            if temp >= 85: idx_score += 8
            elif temp <= 45: idx_score -= 10
            idx_score += (1.0 - dens) * 100 * 0.3  # thinner air (Coors-like) raises it
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
        form = batted.groupby("batter").agg(
            PA=("at_bat_number", "count"),
            H=("events", lambda x: x.isin(["single", "double", "triple", "home_run"]).sum()),
            avg_EV=("launch_speed", "mean"),
            barrel_cnt=("launch_speed", lambda x: (x >= 98).sum()),
        )
        form["AVG"] = (form["H"] / form["PA"]).round(3)
        form["barrel_pct"] = (form["barrel_cnt"] / form["PA"] * 100).round(1)
        return form.to_dict("index")  # keyed by batter MLBAM id
    except Exception as e:
        m.warn(f"Picks L7 batter form: {e}")
        return {}


def fetch_l14_pitcher_form(pitcher_ids):
    """L14 K rate per tonight's starters, via targeted per-pitcher Statcast pulls."""
    out = {}
    for name, pid in pitcher_ids.items():
        if not pid: continue
        try:
            df = m.pyb.statcast_pitcher(start_dt=m.L14_START, end_dt=m.L14_END, player_id=pid)
            if df is None or df.empty: continue
            pa = df[df["events"].notna()]
            if len(pa) == 0: continue
            k_pct = round((pa["events"] == "strikeout").sum() / len(pa) * 100, 1)
            out[name] = {"l14_k_pct": k_pct, "l14_pa": len(pa)}
        except Exception:
            continue
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
#  SCORING
# ══════════════════════════════════════════════════════════════════════════

LEAGUE_AVG_EV = 88.5
LEAGUE_AVG_BARREL_PCT = 8.0
LEAGUE_AVG_K_PCT = 22.5

def score_batter(batter, gm, opp_sp_row, opp_sp_hand, park_wx, batter_season, batter_l7):
    name = batter["name"]
    order = batter.get("order") or 9
    bats = batter.get("bats", "?")

    # MATCHUP (35%) — platoon edge + opposing pitcher quality
    if bats in ("L", "R") and opp_sp_hand in ("L", "R"):
        platoon = 80 if bats != opp_sp_hand else 35
    else:
        platoon = 65  # switch-hitter or unknown hand — treat as mild edge
    sp_era = opp_sp_row.get("ERA") if opp_sp_row else None
    sp_weak = scale(sp_era, 2.5, 6.0)  # worse (higher) ERA -> higher score for the hitter
    matchup = platoon * 0.6 + sp_weak * 0.4

    # RECENT FORM (25%)
    l7 = batter_l7 or {}
    l7_pa = l7.get("PA", 0)
    form = scale(l7.get("avg_EV"), 85, 93) * 0.6 + scale(l7.get("barrel_pct"), 2, 16) * 0.4
    low_sample = l7_pa < 8

    # ENVIRONMENT (15%)
    env = park_wx.get("park_hr_index", 50) if park_wx else 50

    # BASELINE SKILL (15%)
    bs = batter_season or {}
    skill = (scale(bs.get("wRC+"), 70, 140) * 0.4 + scale(bs.get("ISO"), 0.10, 0.28) * 0.3
             + scale(bs.get("Barrel%"), 3, 18) * 0.3)

    # CONTEXT (10%) — lineup slot (more PAs/RBI opportunity higher in the order)
    context = scale(10 - order, 1, 9)

    score = matchup * 0.35 + form * 0.25 + env * 0.15 + skill * 0.15 + context * 0.10

    watchouts = []
    if low_sample:
        watchouts.append(f"L7 sample is thin ({l7_pa} PA) — treat recent-form read with caution")
    # Negative-edge screen: hot average unconfirmed by contact quality = BABIP luck
    if l7.get("AVG", 0) and l7.get("AVG", 0) > 0.320 and l7.get("barrel_pct", 0) < 6 and l7_pa >= 8:
        score -= 12
        watchouts.append(f"L7 AVG {l7.get('AVG')} isn't backed by barrel rate ({l7.get('barrel_pct')}%) — likely BABIP-driven, due to cool off")

    # Prop-type heuristic (no live odds, so this is a direction/prop-type call, not a line)
    barrel_signal = (batter_l7 or {}).get("barrel_pct", 0) or 0
    if barrel_signal >= 10 or (bs.get("Barrel%") or 0) >= 12:
        prop = "Home Run / 2+ Total Bases"
    elif (bs.get("K%") or 30) <= 18:
        prop = "Over 1.5 Hits"
    else:
        prop = "Over 1.5 Total Bases"

    why = []
    why.append(f"Platoon: {bats} bat vs {opp_sp_hand or '?'}HP ({'favorable' if platoon>=65 else 'unfavorable'})")
    if sp_era is not None: why.append(f"Opposing SP ERA {sp_era:.2f}")
    if l7.get("avg_EV"): why.append(f"L7 avg EV {l7['avg_EV']:.1f}mph (league ~{LEAGUE_AVG_EV})")
    if l7.get("barrel_pct") is not None: why.append(f"L7 barrel% {l7['barrel_pct']}")
    if bs.get("wRC+"): why.append(f"Season wRC+ {bs['wRC+']:.0f}")
    if not park_wx or park_wx.get("dome"): why.append("Dome — weather neutral")
    elif park_wx.get("wind_effect") == "out": why.append(f"Wind blowing OUT ({park_wx.get('wind_mph',0):.0f}mph) — HR boost")
    elif park_wx.get("wind_effect") == "in": why.append("Wind blowing IN — power suppressed")

    return {
        "type": "batter", "name": name, "team": batter.get("team"), "matchup": gm["matchup"],
        "prop": prop, "score": round(score, 1), "why": why, "watchouts": watchouts,
        "confidence": "High" if score >= 70 and not low_sample else ("Medium" if score >= 55 else "Low"),
    }


def score_pitcher(sp_name, sp_id, sp_hand, gm, side, pit_season_lookup, l14_form,
                   opp_lineup, opp_team_k_pct, ump_scores):
    ps = pit_season_lookup.get(sp_name, {})
    k_pct = ps.get("K%")
    csw = ps.get("CSW%")
    stuff = ps.get("Stuff+")
    era = ps.get("ERA")

    # MATCHUP (35%) — opposing team K% + same-hand batter share
    same_hand = 0; known = 0
    for b in opp_lineup:
        if b.get("bats") in ("L", "R") and sp_hand in ("L", "R"):
            known += 1
            if b["bats"] == sp_hand: same_hand += 1
    same_hand_ratio = (same_hand / known) if known else 0.4
    matchup = scale(opp_team_k_pct, 17, 27) * 0.65 + scale(same_hand_ratio * 100, 20, 60) * 0.35

    # RECENT FORM (25%) — L14 K rate vs season K rate (trend)
    l14 = l14_form.get(sp_name, {})
    if l14 and l14.get("l14_pa", 0) >= 15:
        form = scale(l14.get("l14_k_pct"), 15, 32)
        low_sample_form = False
    else:
        form = scale(k_pct, 15, 32) if k_pct else 50
        low_sample_form = True

    # ENVIRONMENT (15%) — minor for Ks; cold/dry air a small boost, otherwise neutral
    env = 50

    # BASELINE SKILL (15%)
    skill = scale(k_pct, 15, 32) * 0.4 + scale(csw, 24, 34) * 0.3 + scale(stuff, 80, 130) * 0.3

    # CONTEXT (10%) — tight-zone umpire favors called strikes -> more Ks
    ump = ump_scores.get(gm["matchup"], {})
    context = scale(ump.get("accuracy"), 90, 96) if ump.get("accuracy") else 50

    score = matchup * 0.35 + form * 0.25 + env * 0.15 + skill * 0.15 + context * 0.10

    watchouts = []
    if low_sample_form:
        watchouts.append("L14 Statcast sample too thin — recent-form read falls back to season K%")
    if era and k_pct and era > 4.5 and k_pct and k_pct > 25:
        watchouts.append(f"High K% ({k_pct}%) paired with a shaky ERA ({era:.2f}) — command may be inconsistent start-to-start")

    why = [f"Opposing team K% {opp_team_k_pct:.1f}" if opp_team_k_pct else "Opposing team K% unavailable"]
    why.append(f"{same_hand}/{known} known-hand opposing batters same-handed" if known else "Opposing lineup handedness mostly unknown")
    if k_pct: why.append(f"Season K% {k_pct}")
    if csw: why.append(f"CSW% {csw}")
    if stuff: why.append(f"Stuff+ {stuff}")
    if l14.get("l14_k_pct") is not None: why.append(f"L14 K% {l14['l14_k_pct']} ({l14.get('l14_pa')} PA)")
    if ump.get("accuracy"): why.append(f"HP ump accuracy {ump['accuracy']:.1f}%")

    return {
        "type": "pitcher", "name": sp_name, "team": gm["away_team"] if side == "away" else gm["home_team"],
        "matchup": gm["matchup"], "prop": "Over X.5 Strikeouts (check current line)",
        "score": round(score, 1), "why": why, "watchouts": watchouts,
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
        print("No games today — wrote placeholder picks file.")
        return 0

    print(f"{len(game_meta)} games found. Pulling scoring inputs...")
    bat_season_df = m.fg_bat(m.YEAR)
    pit_season_df = m.fg_pit(m.YEAR)
    team_bat_df = m.fg_team_bat(m.YEAR)
    park_wx = fetch_park_weather(game_meta)
    ump_scores = fetch_umpire_scores(game_meta)
    bullpen_scores = fetch_bullpen_scores(game_meta)
    l7_form = fetch_l7_batter_form()

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
        opp_sp_row_for_away_batters = pitcher_lookup.get(gm["home_sp"], {})  # away batters face home SP
        opp_sp_row_for_home_batters = pitcher_lookup.get(gm["away_sp"], {})
        wx = park_wx.get(gm["matchup"])

        for batter in gm.get("away_lineup", []):
            batter["team"] = gm["away_team"]
            c = score_batter(batter, gm, opp_sp_row_for_away_batters, gm.get("home_sp_hand"),
                              wx, batter_lookup.get(batter["name"]), l7_form.get(batter.get("id")))
            candidates.append(c)
        for batter in gm.get("home_lineup", []):
            batter["team"] = gm["home_team"]
            c = score_batter(batter, gm, opp_sp_row_for_home_batters, gm.get("away_sp_hand"),
                              wx, batter_lookup.get(batter["name"]), l7_form.get(batter.get("id")))
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
    # "What I'd skip" — next few highest-scoring candidates that didn't make the cut
    for c in candidates:
        if len(skipped) >= 2: break
        if c not in top10 and c["score"] >= 55:
            skipped.append(c)

    write_markdown(top10, skipped, game_meta, bullpen_scores)
    print(f"Wrote {len(top10)} picks to {PICKS_FILE}")
    return 0


def write_markdown(top10, skipped, game_meta, bullpen_scores):
    lines = [f"# MLB Top 10 Picks — {m.TODAY}", "",
             "_Generated by deterministic weighted scoring over today's research pull — "
             "no LLM in the loop. No live sportsbook odds were fetched: these are "
             "direction/confidence calls grounded in stats and trends, not priced bets. "
             "**Check the current line and availability on Fanatics before betting.**_", ""]

    lines.append(f"**Slate:** {len(game_meta)} games. "
                 f"Methodology: 35% matchup / 25% recent form / 15% environment / "
                 f"15% baseline skill / 10% context (see README for the full weighting).")
    lines.append("")

    if not top10:
        lines.append("No candidates scored high enough today (thin slate, lineups mostly "
                     "unconfirmed, or data pulls came back empty) — check the run log.")
    for i, c in enumerate(top10, 1):
        lines.append(f"### {i}. {c['name']} ({c['team']}) — {c['prop']}")
        lines.append(f"- **Matchup:** {c['matchup']}")
        lines.append(f"- **Score:** {c['score']}/100  |  **Confidence:** {c['confidence']}")
        lines.append(f"- **Why:** {'; '.join(c['why'])}.")
        if c["watchouts"]:
            lines.append(f"- **Watch-outs:** {'; '.join(c['watchouts'])}.")
        lines.append("- **Line check:** verify the current line/availability on Fanatics — "
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

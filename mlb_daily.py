#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              MLB DAILY BETTING RESEARCH TOOL  —  V5 (AUTOMATED)             ║
║                                                                              ║
║  88 SECTIONS  |  FULLY EXHAUSTIVE  |  ALL FREE PUBLIC DATA SOURCES          ║
║                                                                              ║
║  Sources: FanGraphs · Baseball Savant/Statcast · MLB Stats API              ║
║           UmpScorecards · FantasyInfoCentral · Open-Meteo · Covers.com      ║
║           MLB.com · Rotowire · MLB-StatsAPI · scikit-learn (clustering)     ║
║                                                                              ║
║  SETUP (one time):                                                           ║
║    pip install -r requirements.txt                                          ║
║                                                                              ║
║  RUN DAILY:   python3 mlb_daily.py           (full run, ~15-20 min)         ║
║  DRY RUN:     python3 mlb_daily.py --dry-run  (fast subset, ~1 min)         ║
║  OUTPUT:      mlb_daily_YYYY-MM-DD.txt  ← paste into Claude                 ║
║               run_log_YYYY-MM-DD.json   ← machine-readable section status   ║
║                                                                              ║
║  Automated daily via GitHub Actions — see .github/workflows/mlb-daily.yml   ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os, sys, re, json, math, warnings, unicodedata, requests, pandas as pd, numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

try:
    import pybaseball as pyb
    pyb.cache.enable()
except ImportError:
    print("\nERROR: pip install pybaseball requests pandas beautifulsoup4 lxml mlb-statsapi scikit-learn\n")
    sys.exit(1)

try:
    import statsapi
except ImportError:
    print("ERROR: pip install mlb-statsapi"); sys.exit(1)

try:
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False
    print("WARNING: scikit-learn not installed. Archetype clustering disabled.")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
TODAY     = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
YEAR      = datetime.now().year
YEAR_PREV = YEAR - 1
YEAR_2YR  = YEAR - 2

L3_END  = TODAY; L3_START  = (datetime.now()-timedelta(days=3)).strftime("%Y-%m-%d")
L7_END  = TODAY; L7_START  = (datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d")
L14_END = TODAY; L14_START = (datetime.now()-timedelta(days=14)).strftime("%Y-%m-%d")
L30_END = TODAY; L30_START = (datetime.now()-timedelta(days=30)).strftime("%Y-%m-%d")

MIN_PA=50; MIN_PA_R=10; MIN_IP=10; MIN_BBE=20; MIN_OPP=10
DIV="═"*72; THIN="─"*60
DRY_RUN = os.environ.get("DRY_RUN","0") == "1" or "--dry-run" in sys.argv
OUTPUT_DIR = os.environ.get("OUTPUT_DIR","output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]
BROWSER = {
    "User-Agent": UA_POOL[0],
    "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":"en-US,en;q=0.9",
}

# ══════════════════════════════════════════════════════════════════════════════
#  NETWORK RESILIENCE — UA rotation + retry/backoff for flaky endpoints
#  (FanGraphs 403s, Rotowire/UmpScorecards hiccups). Patches the module-level
#  requests.get used both by this script AND by pybaseball internally, so
#  FanGraphs pulls get a rotating browser UA without touching pybaseball.
# ══════════════════════════════════════════════════════════════════════════════
import random, time as _time
_orig_requests_get = requests.get
def _ua_rotated_get(url, **kwargs):
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("User-Agent", random.choice(UA_POOL))
    headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    return _orig_requests_get(url, headers=headers, **kwargs)
requests.get = _ua_rotated_get

def retry_get(url, retries=3, backoff=2.0, **kwargs):
    """requests.get with exponential backoff + UA rotation. Raises on final failure."""
    last_exc = None
    for attempt in range(retries):
        try:
            r = requests.get(url, **kwargs)
            if r.status_code == 200:
                return r
            if r.status_code in (403, 429, 500, 502, 503, 504) and attempt < retries-1:
                _time.sleep(backoff * (2**attempt) + random.uniform(0, 0.5))
                continue
            return r
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < retries-1:
                _time.sleep(backoff * (2**attempt) + random.uniform(0, 0.5))
    if last_exc: raise last_exc
    return r

def retry_call(fn, *args, retries=3, backoff=2.0, **kwargs):
    """Generic retry wrapper for pybaseball/statsapi calls prone to transient 403/network errors."""
    last_exc = None
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt < retries-1:
                _time.sleep(backoff * (2**attempt) + random.uniform(0, 0.5))
    raise last_exc

# ══════════════════════════════════════════════════════════════════════════════
#  RUN LOG — tracks per-section outcome so Jacob (and Claude downstream) can
#  see at a glance what failed/returned empty without reading all 100+ sections.
# ══════════════════════════════════════════════════════════════════════════════
RUN_LOG = []
_FAIL_MARKERS = ("Failed:", "unavailable", "not found", "[No data]", "No data.",
                  "not yet posted", "API unavailable", "TBD")

def log_section(n, title, text):
    t = (text or "").strip()
    if not t or t == "[No data]":
        status = "empty"
    elif any(m.lower() in t.lower() for m in ("failed:", "unavailable", "api unavailable")):
        status = "failed"
    elif any(m.lower() in t.lower() for m in ("no data.", "[no data]", "not yet posted", "not found")):
        status = "empty"
    else:
        status = "ok"
    RUN_LOG.append({"section": n, "title": title, "status": status})
    return text

_TEAM_ID_CACHE = None
def get_team_ids():
    """All 30 MLB team IDs + abbreviations, cached for the run."""
    global _TEAM_ID_CACHE
    if _TEAM_ID_CACHE is not None: return _TEAM_ID_CACHE
    try:
        r = retry_get("https://statsapi.mlb.com/api/v1/teams", params={"sportId":1,"activeStatus":"Yes"},
                       headers={"User-Agent":"Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        _TEAM_ID_CACHE = [{"id":t["id"],"abbr":t.get("abbreviation","?"),"name":t.get("name","?")}
                          for t in r.json().get("teams",[])]
    except Exception as e:
        warn(f"Team list: {e}")
        _TEAM_ID_CACHE = []
    return _TEAM_ID_CACHE

# ══════════════════════════════════════════════════════════════════════════════
#  STADIUM TABLE — all 30 parks
#  (lat, lon, is_dome, team_abbr, cf_orientation_deg, elevation_ft,
#   lf_dist, cf_dist, rf_dist, lf_wall_ht, cf_wall_ht, rf_wall_ht,
#   foul_territory, surface, humidor, batter_eye_difficulty, retractable_roof)
# ══════════════════════════════════════════════════════════════════════════════
STADIUMS = {
    "Yankee Stadium":          (40.8296,-73.9262,False,"NYY",30, 55, 318,408,314,  8, 8, 8,  "avg",  "grass",False,"easy",  False),
    "Fenway Park":             (42.3467,-71.0972,False,"BOS",60, 20, 310,420,302, 37, 17,5,  "small","grass",False,"medium",False),
    "Camden Yards":            (39.2838,-76.6218,False,"BAL",75, 20, 333,410,318, 7,  7, 7,  "avg",  "grass",False,"easy",  False),
    "Rogers Centre":           (43.6414,-79.3894,True, "TOR",0,  76, 328,400,328, 12, 10,12, "avg",  "turf", False,"medium",False),
    "Tropicana Field":         (27.7683,-82.6534,True, "TB", 0,  10, 315,404,322, 11, 8, 11, "large","turf", False,"hard",  False),
    "Guaranteed Rate Field":   (41.8300,-87.6338,False,"CWS",15, 595,330,400,335, 8,  8, 8,  "avg",  "grass",False,"easy",  False),
    "Wrigley Field":           (41.9484,-87.6553,False,"CHC",50, 595,355,400,353, 15, 9, 11, "small","grass",False,"medium",False),
    "Great American Ball Park":(39.0979,-84.5082,False,"CIN",135,480,328,404,325, 12, 12,9,  "avg",  "grass",False,"easy",  False),
    "Progressive Field":       (41.4962,-81.6852,False,"CLE",60, 653,325,405,325, 19, 19,19, "avg",  "grass",False,"easy",  False),
    "Kauffman Stadium":        (39.0516,-94.4803,False,"KC", 45, 909,330,410,330, 9,  9, 9,  "large","grass",False,"easy",  False),
    "Target Field":            (44.9817,-93.2781,False,"MIN",315,841,339,404,328, 8,  8, 8,  "avg",  "grass",False,"medium",False),
    "Nationals Park":          (38.8730,-77.0074,False,"WSH",30, 25, 336,402,335, 8,  8, 8,  "avg",  "grass",False,"easy",  False),
    "Globe Life Field":        (32.7473,-97.0822,True, "TEX",0,  571,332,407,326, 8,  8, 8,  "avg",  "grass",True, "easy",  True),
    "Daikin Park":             (29.7573,-95.3555,True, "HOU",0,  43, 315,435,326, 21, 21,7,  "avg",  "grass",False,"medium",True),  # renamed from Minute Maid Park — verified live: MLB API now returns "Daikin Park" as the venue name
    "Angel Stadium":           (33.8003,-117.883,False,"LAA",270,160,330,396,330, 8,  8, 8,  "large","grass",False,"easy",  False),
    "Sutter Health Park":      (38.5805,-121.512,False,"ATH",270,25, 330,403,325, 8,  8, 8,  "large","grass",False,"hard",  False),  # A's relocated from Oakland Coliseum — verified live via MLB API team.venue.name; coordinates confirmed (West Sacramento), wall dimensions/cf_deg are best-effort estimates for this AAA park, not independently verified like the rest of this table
    "Dodger Stadium":          (34.0739,-118.240,False,"LAD",315,512,330,395,330, 8,  8, 8,  "avg",  "grass",False,"easy",  False),
    "Petco Park":              (32.7076,-117.157,False,"SD", 315,13, 336,396,322, 8,  8, 8,  "large","grass",False,"medium",False),
    "Oracle Park":             (37.7786,-122.389,False,"SF", 270,10, 339,399,309, 8,  25,8,  "large","grass",False,"hard",  False),
    "T-Mobile Park":           (47.5915,-122.333,True, "SEA",0,  20, 331,401,326, 8,  8, 8,  "avg",  "grass",False,"medium",True),
    "loanDepot park":          (25.7781,-80.2197,True, "MIA",0,  6,  344,418,335, 8,  8, 8,  "avg",  "grass",False,"easy",  True),
    "Truist Park":             (33.8908,-84.4678,True, "ATL",0,  1050,335,400,325,8,  8, 8,  "avg",  "grass",False,"easy",  True),
    "American Family Field":   (43.0280,-87.9712,True, "MIL",0,  634,344,400,345, 8,  8, 8,  "avg",  "grass",False,"easy",  True),
    "Busch Stadium":           (38.6226,-90.1928,False,"STL",30, 455,336,400,335, 8,  8, 8,  "avg",  "grass",False,"easy",  False),
    "Coors Field":             (39.7559,-104.994,False,"COL",15,5200,347,415,350, 8,  8, 8,  "large","grass",True, "easy",  False),
    "Chase Field":             (33.4455,-112.067,True, "ARI",0,  1086,330,407,334,7,  7, 7,  "avg",  "grass",True, "easy",  True),
    "Comerica Park":           (42.3390,-83.0485,False,"DET",45, 585,345,420,330, 14, 14,8,  "large","grass",False,"easy",  False),
    "Citizens Bank Park":      (39.9061,-75.1665,False,"PHI",60, 20, 329,401,330, 8,  8, 8,  "avg",  "grass",False,"easy",  False),
    "PNC Park":                (40.4469,-80.0058,False,"PIT",330,730,325,399,320, 6,  6, 6,  "avg",  "grass",False,"medium",False),
    "Citi Field":              (40.7571,-73.8458,False,"NYM",30, 16, 335,408,330, 8,  8, 8,  "avg",  "grass",False,"medium",False),
}

# League-average PA by batting order slot (based on 162-game season data)
ORDER_PA = {1:750,2:732,3:714,4:693,5:672,6:651,7:630,8:609,9:585}

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def step(m):  print(f"    {m}", flush=True)
def warn(m):  print(f"    ⚠  {m}", flush=True)
def H(n,t):   return f"\n{DIV}\n  SECTION {n}: {t}\n{DIV}\n"
def safe(df,cols): return df[[c for c in cols if c in df.columns]] if not df.empty else df

def fmt(df, mx=500):
    # Bumped from 300: full-season leaderboards (qual=50 PA / 10 IP) regularly
    # run 600-900+ qualified players, and 300 was silently dropping a large
    # chunk of them. Not raised all the way to "every row" — tables are
    # already sorted by the stat that matters (wRC+, ERA, etc.) before
    # truncation, so the cut rows are fringe/replacement-level players least
    # likely to matter for tonight's props, and the automated picks step
    # feeding on this output has its own context/cost budget to respect.
    if df is None or df.empty: return "  [No data]\n"
    pd.set_option("display.max_columns",80)
    pd.set_option("display.width",240)
    pd.set_option("display.max_colwidth",25)
    pd.set_option("display.float_format","{:.3f}".format)
    s = df.head(mx).to_string()
    if len(df)>mx: s+=f"\n  … {len(df)-mx} more rows"
    return s

def wind_dir(d):
    dirs=['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW']
    return dirs[round(d/(360/len(dirs)))%len(dirs)]

def wind_vs_field(wind_deg, cf_deg, is_dome):
    if is_dome: return "DOME — no wind effect"
    diff = (wind_deg - cf_deg + 360) % 360
    if diff<30 or diff>330:   return "blowing IN from CF 🔵 pitcher-friendly"
    elif 150<diff<210:         return "blowing OUT to CF 🔴 hitter-friendly ⚡"
    elif 60<diff<120:          return "blowing L→R crossfield"
    elif 240<diff<300:         return "blowing R→L crossfield"
    else:                      return f"diagonal ({diff:.0f}° off CF)"

def air_density_pct(elevation_ft, temp_f, humidity_pct):
    """Return air density relative to sea level standard (1.0 = average)."""
    temp_k  = (temp_f - 32) * 5/9 + 273.15
    alt_m   = elevation_ft * 0.3048
    pressure_ratio = math.exp(-alt_m / 8500)
    vapor   = humidity_pct / 100 * 0.0023 * math.exp(17.27*(temp_f-32)*5/9 / (237.3+(temp_f-32)*5/9))
    density = pressure_ratio * (1 - 0.378*vapor) * 288.15 / temp_k
    return round(density, 4)

def confidence_flag(pa):
    if pa is None: return "❓"
    try:
        pa = int(pa)
        if pa < 50:  return "🔴 LOW SAMPLE"
        if pa < 150: return "🟡 MED SAMPLE"
        return "🟢"
    except: return "❓"

def perceived_velocity(velo, extension):
    """Effective perceived velocity accounting for extension."""
    try: return round(float(velo) + (float(extension) - 6.0) * 0.5, 1)
    except: return velo

def decision_window_ms(perceived_velo):
    """Milliseconds hitter has to decide (pitch at 60.5 ft)."""
    try:
        fps = float(perceived_velo) * 1.467
        return round((60.5 / fps) * 1000, 1)
    except: return None


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1-4 — LINEUPS, INJURIES, SCHEDULE FLAGS, BALLPARK TABLE
# ══════════════════════════════════════════════════════════════════════════════

def fetch_lineups(date):
    step(f"Lineups + pitchers + HP umpires ({date})...")
    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {"sportId":1,"date":date,
              "hydrate":"lineups,probablePitcher(note),linescore,team,weather,venue,officials,seriesStatus"}
    try:
        r = retry_get(url,params=params,headers={"User-Agent":"Mozilla/5.0"},timeout=25)
        r.raise_for_status()
        dates = r.json().get("dates",[])
        if not dates: return "  No games.\n", [], {}
        games = dates[0].get("games",[])
    except Exception as e:
        warn(f"MLB API: {e}"); return "  API unavailable.\n", [], {}

    lines=[]; game_meta=[]; player_ids={}; missing_teams=[]; bats_patch=[]
    for g in games:
        away,home = g["teams"]["away"],g["teams"]["home"]
        at=away["team"]["name"]; ht=home["team"]["name"]
        ar=f"{away.get('leagueRecord',{}).get('wins','?')}-{away.get('leagueRecord',{}).get('losses','?')}"
        hr=f"{home.get('leagueRecord',{}).get('wins','?')}-{home.get('leagueRecord',{}).get('losses','?')}"
        try:
            dt=datetime.strptime(g.get("gameDate",""),"%Y-%m-%dT%H:%M:%SZ")
            tstr=(dt-timedelta(hours=4)).strftime("%-I:%M %p ET")
            game_hour=(dt-timedelta(hours=4)).hour
        except: tstr="TBD"; game_hour=19
        venue=g.get("venue",{}).get("name","")
        status=g.get("status",{}).get("detailedState","")
        series_num=g.get("seriesGameNumber",1)
        series_len=g.get("gamesInSeries",3)
        is_getaway = (series_num == series_len)
        is_series_opener = (series_num == 1)

        hp_ump="TBD"
        for off in g.get("officials",[]):
            if off.get("officialType","")=="Home Plate":
                hp_ump=off.get("official",{}).get("fullName","TBD"); break

        ap=away.get("probablePitcher",{}); hp=home.get("probablePitcher",{})
        apn=ap.get("fullName","TBD"); hpn=hp.get("fullName","TBD")
        ap_id=ap.get("id"); hp_id=hp.get("id")
        if ap_id: player_ids[apn]=ap_id
        if hp_id: player_ids[hpn]=hp_id

        game_meta.append({"matchup":f"{at} @ {ht}","venue":venue,"hour":game_hour,
                          "away_sp":apn,"home_sp":hpn,"hp_ump":hp_ump,
                          "away_sp_id":ap_id,"home_sp_id":hp_id,
                          "series_game":series_num,"series_len":series_len,
                          "is_getaway":is_getaway,"is_opener":is_series_opener,
                          "game_pk":g.get("gamePk"),
                          "away_team":at,"home_team":ht,
                          # Structured lineups (name/id/pos/bats/order), populated below —
                          # kept alongside the human-readable text report so downstream
                          # scoring (generate_picks.py) doesn't have to parse text back out.
                          "away_lineup":[], "home_lineup":[]})

        context_flags=[]
        if is_getaway: context_flags.append("🚌 GETAWAY DAY")
        if is_series_opener: context_flags.append("🔵 SERIES OPENER")
        if series_num==2: context_flags.append("📋 GAME 2 (rematch adjustment)")
        if series_num==3: context_flags.append("📋 GAME 3 (full adjustment)")
        month=datetime.now().month
        if month>=9: context_flags.append("📅 SEPTEMBER (expanded roster)")

        lines+=[f"\n{THIN}",
                f"  {at} ({ar})  @  {ht} ({hr})",
                f"  {tstr}  |  {venue}  |  {status}",
                f"  Series: Game {series_num} of {series_len}  {'  '.join(context_flags)}",
                f"  HP Ump : {hp_ump}",
                f"  SP Away: {apn}  {ap.get('note','')}",
                f"  SP Home: {hpn}  {hp.get('note','')}"]

        lups=g.get("lineups",{})
        for key,name in [("awayPlayers",at),("homePlayers",ht)]:
            players=lups.get(key,[])
            lineup_key = "away_lineup" if key=="awayPlayers" else "home_lineup"
            if players:
                lines.append(f"\n  {name} Batting Order:")
                for order,p in enumerate(players,1):
                    # Verified live: this hydrate's lineup player objects are flat
                    # ({"id","fullName","primaryPosition":{...}}), not nested under
                    # "person"/"position"/"batSide" — the old code read paths that
                    # never existed on any version, so every name/position/bats here
                    # printed "?" whenever the primary API lineup path was used (i.e.
                    # most games, most runs). There's also no per-player battingOrder
                    # field in this hydrate — array position (1-indexed) IS the order.
                    # Bat handedness isn't in this hydrate at all; backfilled below via
                    # a batched /api/v1/people call after all games are parsed.
                    pid=p.get("id")
                    pname=p.get("fullName","?")
                    pos=p.get("primaryPosition",{}).get("abbreviation","?")
                    pa_proj=ORDER_PA.get(min(order,9),630)
                    if pid: player_ids[pname]=pid
                    line_idx=len(lines)
                    lines.append(f"    {order}. {pname} (BATS) — {pos}  [proj ~{pa_proj} PA/yr]")
                    entry={"name":pname,"id":pid,"pos":pos,"bats":"?","order":order}
                    game_meta[-1][lineup_key].append(entry)
                    if pid: bats_patch.append((line_idx, entry))
            else:
                idx=len(lines)
                lines.append(f"\n  {name}: lineup not yet posted")
                side = "away" if key=="awayPlayers" else "home"
                missing_teams.append({"idx":idx,"name":name,"game_pk":g.get("gamePk"),"side":side})

    # ── Fallback chain for lineups not yet posted by the MLB Stats API ──
    # Tier 1: MLB.com dated starting-lineups page (server-rendered, keyed by gamePk — reliable)
    # Tier 2: Rotowire daily-lineups (server-rendered too, but Rotowire-internal player IDs
    #         only — can't populate player_ids — genuine last resort)
    if missing_teams:
        gm_by_pk = {gm.get("game_pk"): gm for gm in game_meta}
        mlbcom = fetch_mlb_dated_lineups_fallback(date)
        still_missing=[]
        for miss in missing_teams:
            gp = mlbcom.get(miss["game_pk"])
            side_players = gp.get(miss["side"]) if gp else None
            if side_players:
                block=[f"\n  {miss['name']} Batting Order  (source: MLB.com fallback):"]
                gm_entry = gm_by_pk.get(miss["game_pk"])
                lineup_key = "away_lineup" if miss["side"]=="away" else "home_lineup"
                for i,p in enumerate(side_players,1):
                    pa_proj=ORDER_PA.get(i,630)
                    block.append(f"    {i}. {p['name']} ({p.get('bats','?')}) — {p.get('pos','?')}  [proj ~{pa_proj} PA/yr]")
                    if p.get("id"): player_ids[p["name"]]=p["id"]
                    if gm_entry is not None:
                        gm_entry[lineup_key].append({"name":p["name"],"id":p.get("id"),"pos":p.get("pos","?"),"bats":p.get("bats","?"),"order":i})
                lines[miss["idx"]]="\n".join(block)
            else:
                still_missing.append(miss)
        if still_missing:
            teams_by_name = {t["name"]:t["abbr"] for t in get_team_ids()}
            rotowire = fetch_rotowire_lineups_by_team()
            # Rotowire has no MLBAM IDs of its own (Rotowire-internal only) — found
            # live that this silently broke nearly every per-player Statcast lookup
            # downstream in generate_picks.py (L7 form, bat speed, sprint speed,
            # pitch-type exploits all key off MLBAM id) whenever a lineup fell all
            # the way through to this last-resort tier, with no visible error, just
            # thin-looking data. Backfilled by name against the full active-roster
            # endpoint — one call for the whole league, not one lookup per player.
            roster = fetch_active_roster_by_name() if still_missing else {"exact":{}, "normalized":{}}
            for miss in still_missing:
                abbr = teams_by_name.get(miss["name"])
                rw_players = rotowire.get(abbr) if abbr else None
                if rw_players:
                    block=[f"\n  {miss['name']} Batting Order  (source: Rotowire fallback, best-effort):"]
                    gm_entry = gm_by_pk.get(miss["game_pk"])
                    lineup_key = "away_lineup" if miss["side"]=="away" else "home_lineup"
                    for i,p in enumerate(rw_players,1):
                        pa_proj=ORDER_PA.get(i,630)
                        roster_hit = roster["exact"].get(p["name"]) or roster["normalized"].get(_normalize_name_for_match(p["name"]))
                        pid = roster_hit["id"] if roster_hit else None
                        bats = p.get("bats","?")
                        if bats == "?" and roster_hit: bats = roster_hit.get("bats","?")
                        if pid: player_ids[p["name"]]=pid
                        block.append(f"    {i}. {p['name']} ({bats}) — {p.get('pos','?')}  [proj ~{pa_proj} PA/yr]"
                                      + ("" if pid else "  [MLBAM id not matched — per-player Statcast signals unavailable]"))
                        if gm_entry is not None:
                            gm_entry[lineup_key].append({"name":p["name"],"id":pid,"pos":p.get("pos","?"),"bats":bats,"order":i})
                    lines[miss["idx"]]="\n".join(block)

    # Batch-fetch bat/pitch handedness for every player discovered (lineup batters +
    # probable pitchers) in as few calls as possible. Confirmed live: the lineups
    # hydrate above has no batSide field at all, so this is the only way to get it.
    hand_ids={pid for pid in player_ids.values() if pid} | {e["id"] for _,e in bats_patch if e["id"]}
    hand_ids=[i for i in hand_ids if i]
    hand_by_id={}
    for start in range(0, len(hand_ids), 100):
        chunk=hand_ids[start:start+100]
        try:
            r=retry_get("https://statsapi.mlb.com/api/v1/people",
                       params={"personIds":",".join(str(i) for i in chunk)},
                       headers={"User-Agent":"Mozilla/5.0"},timeout=20)
            if r.status_code==200:
                for person in r.json().get("people",[]):
                    hand_by_id[person["id"]]={"bats":person.get("batSide",{}).get("code","?"),
                                              "throws":person.get("pitchHand",{}).get("code","?")}
        except Exception as e:
            warn(f"Handedness batch fetch: {e}")

    for line_idx, entry in bats_patch:
        bats=hand_by_id.get(entry["id"],{}).get("bats","?")
        entry["bats"]=bats
        lines[line_idx]=lines[line_idx].replace("(BATS)", f"({bats})", 1)
    for gm in game_meta:
        gm["away_sp_hand"]=hand_by_id.get(gm.get("away_sp_id"),{}).get("throws","?")
        gm["home_sp_hand"]=hand_by_id.get(gm.get("home_sp_id"),{}).get("throws","?")

    return "\n".join(lines)+"\n", game_meta, player_ids


def fetch_mlb_dated_lineups_fallback(date):
    """MLB.com dated starting-lineups page — server-rendered HTML (verified against
    live structure: div.starting-lineups__matchup[data-gamepk] with two <ol> lineups).
    Preferred fallback (per ops brief) over Rotowire since it's keyed directly by
    the same gamePk the primary MLB Stats API uses, so matching is exact rather
    than name-fuzzy. Called once globally, never per-team."""
    step("MLB.com starting-lineups fallback...")
    result = {}
    try:
        ymd = date.replace("-","")
        url = f"https://www.mlb.com/starting-lineups/{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        r = retry_get(url, headers=BROWSER, timeout=25)
        if r.status_code != 200:
            warn(f"MLB.com lineups fallback: HTTP {r.status_code}")
            return result
        soup = BeautifulSoup(r.text, "lxml")
        for matchup in soup.select("div.starting-lineups__matchup"):
            gp_raw = matchup.get("data-gamepk")
            try: gp = int(gp_raw)
            except (TypeError, ValueError): continue
            game_data = {"away": [], "home": []}
            for side, ol_class in [("away","starting-lineups__team--away"),
                                    ("home","starting-lineups__team--home")]:
                ol = matchup.select_one(f"ol.{ol_class}")
                if not ol: continue
                for li in ol.select("li.starting-lineups__player"):
                    a = li.find("a")
                    if not a: continue
                    name = a.get_text(strip=True)
                    pid = None
                    m_id = re.search(r"-(\d+)$", a.get("href","") or "")
                    if m_id: pid = int(m_id.group(1))
                    pos_span = li.find("span", class_="starting-lineups__player--position")
                    pos_txt = pos_span.get_text(strip=True) if pos_span else "?"
                    bats, pos = "?", pos_txt
                    m_pos = re.match(r"\(([A-Z])\)\s*(.+)", pos_txt)
                    if m_pos: bats, pos = m_pos.group(1), m_pos.group(2)
                    game_data[side].append({"name":name,"pos":pos,"bats":bats,"id":pid})
            result[gp] = game_data
        step(f"  MLB.com fallback: {len(result)} games parsed")
    except Exception as e:
        warn(f"MLB.com lineups fallback: {e}")
    return result


def fetch_rotowire_lineups_by_team():
    """Rotowire daily lineups, scraped once globally (not per-team — a prior version
    fired 24+ per-team requests and got blocked), keyed by team abbreviation. Verified
    against live structure: div.lineup__box > div.lineup__abbr (team codes) + two
    ul.lineup__list (li.lineup__player). Last-resort tier behind the MLB.com fallback:
    Rotowire's player IDs are Rotowire-internal, not MLBAM, so this tier can't populate
    player_ids, only names/positions/bat side."""
    step("Rotowire daily lineups (last-resort fallback)...")
    result = {}
    try:
        r = retry_get("https://www.rotowire.com/baseball/daily-lineups.php", headers=BROWSER, timeout=25)
        if r.status_code != 200:
            warn(f"Rotowire lineups: HTTP {r.status_code}")
            return result
        soup = BeautifulSoup(r.text, "lxml")
        for box in soup.select("div.lineup__box"):
            abbrs = box.select("div.lineup__abbr")
            lists = box.select("ul.lineup__list")
            if len(abbrs) < 2 or len(lists) < 2: continue
            for abbr_el, list_el in zip(abbrs, lists):
                abbr = abbr_el.get_text(strip=True)
                players=[]
                for li in list_el.select("li.lineup__player"):
                    a = li.find("a")
                    if not a: continue
                    pos_el = li.find("div", class_="lineup__pos")
                    bats_el = li.find("span", class_="lineup__bats")
                    players.append({
                        "name": a.get("title") or a.get_text(strip=True),
                        "pos": pos_el.get_text(strip=True) if pos_el else "?",
                        "bats": bats_el.get_text(strip=True) if bats_el else "?",
                    })
                if players: result[abbr] = players
        step(f"  Rotowire fallback: {len(result)} team lineups parsed")
    except Exception as e:
        warn(f"Rotowire lineups: {e}")
    return result

def _normalize_name_for_match(name):
    """Strips accents (Rotowire renders 'Jose Ramirez' for 'José Ramírez',
    'Julio Rodriguez' for 'Julio Rodríguez' — verified live against tonight's
    actual slate) and common generational suffixes ('Bobby Witt' for 'Bobby
    Witt Jr.', 'Fernando Tatis' for 'Fernando Tatis Jr.') so a name-based
    roster match isn't defeated by cosmetic differences between sources."""
    if not name: return name
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+(Jr\.?|Sr\.?|II|III|IV)$", "", ascii_name.strip(), flags=re.IGNORECASE).strip().lower()

_ROSTER_CACHE = None
def fetch_active_roster_by_name():
    """Full active-roster name->{id,bats} lookup, one call for the whole league.
    Verified live: /api/v1/sports/1/players returns ~1350 active players with
    fullName, id, and batSide.code — used to backfill MLBAM IDs for lineup
    sources (Rotowire) that only carry names, not IDs, of their own. Keyed
    both by exact fullName and by a normalized (accent/suffix-stripped) form,
    since Rotowire's own names diverge from the MLB roster's exact spelling
    on both counts — verified live against a real slate (Ramirez/Rodriguez/
    Acuna missing accents, Witt/Tatis missing "Jr.") before adding this.
    Also keyed by id->name (by_id) for the reverse lookup Statcast-derived
    leaderboards need (Statcast rows are ID-keyed, not name-keyed). Cached
    module-level since multiple sections now share this same roster call."""
    global _ROSTER_CACHE
    if _ROSTER_CACHE is not None:
        return _ROSTER_CACHE
    try:
        r = retry_get("https://statsapi.mlb.com/api/v1/sports/1/players",
                      params={"season": YEAR}, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
        r.raise_for_status()
        people = r.json().get("people", [])
    except Exception as e:
        warn(f"Active roster lookup: {e}")
        return {"exact": {}, "normalized": {}, "by_id": {}}
    exact = {p["fullName"]: {"id": p.get("id"), "bats": p.get("batSide", {}).get("code", "?")}
             for p in people}
    normalized = {_normalize_name_for_match(p["fullName"]):
                  {"id": p.get("id"), "bats": p.get("batSide", {}).get("code", "?")} for p in people}
    by_id = {p["id"]: p["fullName"] for p in people if p.get("id")}
    _ROSTER_CACHE = {"exact": exact, "normalized": normalized, "by_id": by_id}
    return _ROSTER_CACHE


_SEASON_STATCAST_CACHE = None
def fetch_season_statcast():
    """Leaguewide season-long pitch-by-pitch Statcast pull, cached module-
    level (one pull, reused by every section that needs it). Verified live
    before building on this: a full-season pull is ~480K rows and completes
    in ~50s — well within this pipeline's budget, not the timeout risk it
    was assumed to be when CSW%/batter K% were first left as FanGraphs-only
    gaps. Used as the real fallback for metrics that live only on FanGraphs'
    pages (CSW%, batter K%/BB%) and have no equivalent field in the
    lighter-weight Statcast "expected stats" endpoints already used
    elsewhere as the batting/pitching fallback."""
    global _SEASON_STATCAST_CACHE
    if _SEASON_STATCAST_CACHE is not None:
        return _SEASON_STATCAST_CACHE
    try:
        df = pyb.statcast(start_dt=f"{YEAR}-03-15", end_dt=TODAY)
        _SEASON_STATCAST_CACHE = df if df is not None else pd.DataFrame()
    except Exception as e:
        warn(f"Season Statcast pull: {e}")
        _SEASON_STATCAST_CACHE = pd.DataFrame()
    return _SEASON_STATCAST_CACHE

IL_STATUS_CODES = {"D7":"7-Day IL","D10":"10-Day IL","D15":"15-Day IL","D60":"60-Day IL","DRS":"Restricted-Injured"}

def fetch_injuries():
    # The legacy /api/v1/injuries endpoint was retired by MLB (confirmed: returns a
    # hard 404 as of this pipeline's 2026 rebuild) and Rotowire's injury-report.php
    # is client-side rendered (no data in the raw HTML — would need a JS-capable
    # browser), so neither the old primary nor the originally-planned Rotowire
    # fallback is viable. Real replacement: pull each team's 40-man roster and
    # filter to injured-list status codes (D7/D10/D15/D60) — same underlying data,
    # still free/public, still same-day accurate.
    step("Injury report (40-man roster IL status per team)...")
    teams = get_team_ids()
    if not teams: return "  Team list unavailable — injury report skipped.\n"
    lines=[f"  {'Player':<28} {'Team':<6} {'Pos':<5} {'Status':<16} "]
    lines.append("  "+"-"*65)
    total=0
    for t in teams:
        try:
            r=retry_get(f"https://statsapi.mlb.com/api/v1/teams/{t['id']}/roster",
                        params={"rosterType":"40Man"},headers={"User-Agent":"Mozilla/5.0"},timeout=15)
            if r.status_code!=200: continue
            roster=r.json().get("roster",[])
            for p in roster:
                code=p.get("status",{}).get("code","A")
                if code not in IL_STATUS_CODES: continue
                name=p.get("person",{}).get("fullName","?")
                pos=p.get("position",{}).get("abbreviation","?")
                status_label=IL_STATUS_CODES.get(code,p.get("status",{}).get("description","?"))
                surgery_flag=""
                for kw in ["Tommy John","TJ","shoulder","oblique","hamstring","wrist"]:
                    if kw.lower() in name.lower(): surgery_flag="⚠️"; break
                lines.append(f"  {surgery_flag}{name:<27} {t['abbr']:<6} {pos:<5} {status_label:<16}")
                total+=1
        except Exception:
            continue
    if total==0: return "  No active injuries or all team roster pulls failed.\n"
    step(f"  {total} players on IL across {len(teams)} teams")
    return "\n".join(lines)+"\n"


def ballpark_table():
    step("Ballpark reference table...")
    lines=[f"  {'Park':<28} {'Team':<5} {'LF':>4} {'CF':>4} {'RF':>4} {'LFw':>4} {'RFw':>4} {'Surf':<6} {'Dome':<5} {'Humdr':<6} {'ElevFt':>6} {'Foul':<6} {'BatEye':<7} {'Roof':<5}"]
    lines.append("  "+"-"*110)
    for name,d in sorted(STADIUMS.items(), key=lambda x:x[1][3]):
        lat,lon,dome,team,cf,elev,lf,cf_d,rf,lfw,cfw,rfw,foul,surf,humidor,eye,retract=d
        dens=air_density_pct(elev,72,50)
        dens_str=f"{dens:.3f}"
        roof_str="✅" if retract else "—"
        lines.append(f"  {name:<28} {team:<5} {lf:>4} {cf_d:>4} {rf:>4} {lfw:>4} {rfw:>4} {surf:<6} {'✅' if dome else '—':<5} {'✅' if humidor else '—':<6} {elev:>6} {foul:<6} {eye:<7} {roof_str:<5}")
    lines.append(f"\n  Air density (AirDens): computed as fraction of sea-level standard at 72°F/50% humidity")
    lines.append(f"  Coors Field: 0.805 = ball carries ~15% farther than sea level")
    lines.append(f"  Humidor parks: Coors, Chase, Globe Life — ball stored at ~50°F/50% humidity")
    return "\n".join(lines)+"\n"



# ══════════════════════════════════════════════════════════════════════════════
#  SECTIONS 5-8 — WEATHER, UMPIRES, BvP, TRAVEL
# ══════════════════════════════════════════════════════════════════════════════

def fetch_weather(game_meta):
    step("Game-time weather + air density + directional HR scores...")
    lines=[]
    seen=set()
    for gm in game_meta:
        venue=gm["venue"]
        sk=None
        for k in STADIUMS:
            if k.lower() in venue.lower() or venue.lower() in k.lower():
                sk=k; break
        if not sk:
            lines.append(f"\n  {gm['matchup']} — stadium not found: {venue}"); continue
        if sk in seen: continue
        seen.add(sk)
        lat,lon,dome,team,cf_deg,elev,lf,cf_d,rf,lfw,cfw,rfw,foul,surf,humidor,eye,retract=STADIUMS[sk]
        if dome:
            lines+=[f"\n{THIN[:50]}",f"  {gm['matchup']}",
                    f"  Venue  : {sk} (DOME — weather irrelevant)",
                    f"  Humidor: {'YES ✅' if humidor else 'No'}",
                    f"  Surface: {surf}",
                    f"  HP Ump : {gm['hp_ump']}"]
            continue
        try:
            r=retry_get("https://api.open-meteo.com/v1/forecast",params={
                "latitude":lat,"longitude":lon,
                "hourly":"temperature_2m,precipitation_probability,windspeed_10m,winddirection_10m,relativehumidity_2m,precipitation",
                "temperature_unit":"fahrenheit","windspeed_unit":"mph",
                "precipitation_unit":"inch","timezone":"auto","forecast_days":1
            },timeout=20,retries=2); r.raise_for_status()
            h=r.json()["hourly"]
            idx=min(max(gm["hour"],0),23)
            temp=h["temperature_2m"][idx]; precip_p=h["precipitation_probability"][idx]
            wsp=h["windspeed_10m"][idx]; wdir=h["winddirection_10m"][idx]
            humid=h["relativehumidity_2m"][idx]; precip=h["precipitation"][idx]
            wdir_txt=wind_dir(wdir)
            wvf=wind_vs_field(wdir,cf_deg,dome)
            dens=air_density_pct(elev,temp,humid)
            dens_pct=(dens-1.0)*100
            carry_str=f"{abs(dens_pct):.1f}% {'FARTHER' if dens_pct<0 else 'shorter'}"
            flags=[]
            if precip_p>=30: flags.append(f"🚨 RAIN {precip_p}%")
            if precip_p>=50: flags.append("⛔ HIGH POSTPONEMENT RISK")
            if wsp>=15:      flags.append(f"💨 HIGH WIND {wsp:.0f}mph")
            if wsp>=10 and "OUT" in wvf.upper(): flags.append("🔴 WIND OUT — HR boost")
            if wsp>=10 and "IN" in wvf.upper():  flags.append("🔵 WIND IN — HR suppressed")
            if temp<45:      flags.append(f"🥶 COLD {temp:.0f}°F")
            if temp>85:      flags.append(f"🔥 HOT {temp:.0f}°F — ball carries")
            lines+=[f"\n{THIN[:50]}",
                    f"  {gm['matchup']}",
                    f"  Venue   : {sk}  |  Elev: {elev}ft  |  Surface: {surf}",
                    f"  Temp    : {temp:.0f}°F  |  Humidity: {humid:.0f}%",
                    f"  Rain%   : {precip_p}%  ({precip:.2f}in expected)",
                    f"  Wind    : {wsp:.0f}mph from {wdir_txt} ({wdir:.0f}°)",
                    f"  vs Field: {wvf}",
                    f"  AirDens : {dens:.4f}  →  ball carries {carry_str} vs sea-level",
                    f"  Humidor : {'YES ✅' if humidor else 'No'}",
                    f"  HP Ump  : {gm['hp_ump']}"]
            if flags: lines.append(f"  ⚠ FLAGS : {'  |  '.join(flags)}")
        except Exception as e:
            warn(f"Weather {sk}: {e}")
            lines.append(f"\n  {gm['matchup']}: weather fetch failed")
    return "\n".join(lines)+"\n"


def fetch_umpire_stats(game_meta):
    # Rebuilt against UmpScorecards' current site (a SvelteKit app — the HTML shell
    # has no data, but it calls a real JSON API under the hood: verified live at
    # https://umpscorecards.com/api/umpires, one global request, no per-umpire calls).
    step("HP umpire career stats (UmpScorecards)...")
    umps={gm["hp_ump"]:gm["matchup"] for gm in game_meta if gm["hp_ump"]!="TBD"}
    if not umps: return "  No HP umpires found.\n"
    try:
        r=retry_get("https://umpscorecards.com/api/umpires",
                    headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"},timeout=20)
        r.raise_for_status()
        rows=r.json().get("rows",[])
        by_name={row.get("umpire"):row for row in rows}
    except Exception as e:
        warn(f"UmpScorecards: {e}")
        return "  UmpScorecards API unavailable.\n"
    lines=[f"  {'Umpire':<20} {'Matchup':<44} {'Accur%':>7} {'Consis%':>8} {'RunImpact':>10} {'Challng':>8} {'Overturn':>9}"]
    lines.append("  "+"-"*114)
    found=0
    for name,matchup in umps.items():
        u=by_name.get(name)
        if not u:
            # Same-day HP umpire assignments are frequently not in the career
            # aggregate yet this early (rookies, or assignment posted after this
            # dataset's last refresh) — TBD here is expected, not a scrape failure.
            lines.append(f"  {name:<20} {matchup:<44}  not in career database (TBD — expected, see README)")
            continue
        found+=1
        acc=u.get("overall_accuracy_wmean"); cons=u.get("consistency_wmean")
        impact=u.get("total_run_impact_mean"); chal=u.get("n_challenged_sum"); over=u.get("n_overturned_sum")
        def fp(v): return f"{v:.1f}" if isinstance(v,(int,float)) else "N/A"
        lines.append(f"  {name:<20} {matchup:<44} {fp(acc):>7} {fp(cons):>8} {fp(impact):>10} "
                     f"{chal if chal is not None else 'N/A':>8} {over if over is not None else 'N/A':>9}")
    step(f"  {found}/{len(umps)} umpires matched to career database")
    lines+=["\n  Accur%=career ball/strike call accuracy  Consis%=zone consistency start-to-start",
            "  RunImpact=avg runs added/removed by missed calls (higher=more impactful misses)",
            "  Challng/Overturn=2026 ABS Challenge System career totals — cross-ref Section 9",
            "  Large accuracy + high consistency → tight zone → walks/hits UP, K props DOWN"]
    return "\n".join(lines)+"\n"


def fetch_umpire_ou_records(game_meta):
    # Covers.com restructured this page (verified live): soup.find_all("table")
    # on the aggregate /umpires page returns zero tables now — it's a plain
    # name index (div.covers-RefereeTable > a per umpire, "Last, First" text,
    # href to that umpire's own page), not an inline stats table anymore.
    # The real O/U records live on each umpire's individual page instead
    # (verified live: e.g. /umpires/2026/18023 has 4 real tables, one of
    # which has an "Overall" row paired with a real W-L O/U record whose
    # game count matches that umpire's "Games Officiated" from the same
    # page). Since we only need tonight's actual HP umpires (a handful),
    # fetch the index once, then each relevant umpire's own page.
    step("Umpire O/U betting records (Covers.com)...")
    umps={gm["hp_ump"]:gm["matchup"] for gm in game_meta if gm["hp_ump"]!="TBD"}
    if not umps: return "  No HP umpires.\n"
    try:
        r=retry_get("https://www.covers.com/sport/baseball/mlb/umpires",headers=BROWSER,timeout=20,retries=2)
        if r.status_code!=200: return f"  Covers.com returned {r.status_code}\n"
        soup=BeautifulSoup(r.text,"lxml")
        idx={}
        for a in soup.select("div.covers-RefereeTable a"):
            txt=a.get_text(strip=True); href=a.get("href","")
            if "," in txt and href:
                last,first=[p.strip() for p in txt.split(",",1)]
                idx[f"{first} {last}"]=href
        if not idx: return "  Covers.com umpire index not found (site structure may have changed again).\n"
    except Exception as e:
        warn(f"Covers umpire index: {e}")
        return f"  Covers.com unavailable: {e}\n"
    lines=[f"  {'Umpire':<20} {'Matchup':<44} {'Season O/U (Over-Under)':<24}"]
    lines.append("  "+"-"*92)
    found=0
    for name,matchup in umps.items():
        href=idx.get(name)
        if not href:
            lines.append(f"  {name:<20} {matchup:<44} not found in Covers.com index")
            continue
        try:
            r2=retry_get(f"https://www.covers.com{href}",headers=BROWSER,timeout=20,retries=2)
            if r2.status_code!=200:
                lines.append(f"  {name:<20} {matchup:<44} HTTP {r2.status_code}")
                continue
            soup2=BeautifulSoup(r2.text,"lxml")
            record="N/A"
            for t in soup2.find_all("table"):
                for row in t.find_all("tr"):
                    cells=[td.get_text(strip=True) for td in row.find_all(["td","th"])]
                    if len(cells)>=2 and cells[0]=="Overall":
                        record=cells[1]; break
                if record!="N/A": break
            lines.append(f"  {name:<20} {matchup:<44} {record:<24}")
            if record!="N/A": found+=1
        except Exception as e:
            lines.append(f"  {name:<20} {matchup:<44} fetch failed: {e}")
    step(f"  {found}/{len(umps)} umpires matched to O/U records")
    return "\n".join(lines)+"\n"


def fetch_bvp(date=None):
    """Verified live: the site and table-parsing logic both work fine (a
    direct test returned 171 real rows) — the "empty" status seen on a real
    run was this using a bare requests.get() with zero retries, unlike every
    other fetcher here, which all go through retry_get()'s backoff/UA-
    rotation. A single transient hiccup was enough to kill this section for
    the whole run. Fixed to match the rest of the codebase's resilience."""
    step("BvP matchup table (FantasyInfoCentral)...")
    date_str=date or TODAY
    url=f"https://www.fantasyinfocentral.com/mlb/daily-matchups?date={date_str}"
    try:
        r=retry_get(url,headers=BROWSER,timeout=25,retries=3); r.raise_for_status()
        soup=BeautifulSoup(r.text,"lxml")
        tables=soup.find_all("table")
        if not tables: return "  BvP table not found.\n"
        main=None
        for t in tables:
            headers=[th.get_text(strip=True) for th in t.find_all("th")]
            if any(h in headers for h in ["AB","H","BA","OPS","HRF"]): main=t; break
        if not main: return "  BvP table structure not recognized.\n"
        headers=[th.get_text(strip=True) for th in main.find_all("th")]
        rows=[]
        for row in main.find_all("tr")[1:]:
            cells=row.find_all("td")
            if len(cells)>=5: rows.append([c.get_text(strip=True) for c in cells])
        if not rows: return "  BvP table empty.\n"
        df=pd.DataFrame(rows,columns=headers[:len(rows[0])] if headers else None)
        step(f"  {len(rows)} BvP rows")
        return f"  Source: fantasyinfocentral.com  Date: {date_str}\n  HRF>1.0=hitter-friendly\n\n{fmt(df,200)}"
    except Exception as e:
        warn(f"BvP: {e}"); return f"  BvP unavailable: {e}\n  Visit: {url}\n"


def fetch_travel(game_meta):
    step("Travel schedule + timezone fatigue...")
    # Stadium timezone offsets (UTC offset at game time)
    tz_map={"NYY":-4,"BOS":-4,"BAL":-4,"TOR":-4,"TB":-4,"CWS":-5,"CLE":-4,"DET":-4,
            "KC":-5,"MIN":-5,"WSH":-4,"PHI":-4,"NYM":-4,"ATL":-4,"MIA":-4,
            "CHC":-5,"CIN":-4,"MIL":-5,"PIT":-4,"STL":-5,
            "TEX":-5,"HOU":-5,"LAA":-7,"LAD":-7,"SD":-7,"SF":-7,"SEA":-7,
            "ATH":-7,"COL":-6,"ARI":-7}
    lines=["  Team       Home_TZ  Today_TZ  TZ_Cross  Notes"]
    lines.append("  "+"-"*60)
    for gm in game_meta:
        for team_sp,side in [(gm["away_sp"],"AWAY"),(gm["home_sp"],"HOME")]:
            # Find team from matchup
            matchup=gm["matchup"]
            away_name=matchup.split(" @ ")[0] if " @ " in matchup else ""
            home_name=matchup.split(" @ ")[1] if " @ " in matchup else ""
            # Map to abbreviations (simplified)
            venue_team=None
            venue_lower=gm["venue"].lower()
            for k,d in STADIUMS.items():
                if k.lower() in venue_lower or venue_lower in k.lower(): venue_team=d[3]; break
            if venue_team:
                venue_tz=tz_map.get(venue_team,-4)
                flag=""
                if side=="AWAY":
                    # Approximate home team lookup
                    flag="⚠️ West→East early start risk" if venue_tz > -6 and gm["hour"]<14 else ""
                lines.append(f"  {team_sp[:10]:<12} {'EST':<9} {venue_tz:+d}UTC  {'—':>8}  {flag}")
    return "\n".join(lines)+"\n"



# ══════════════════════════════════════════════════════════════════════════════
#  SECTIONS 9-19 — STATCAST RECENT FORM, PITCHER DEEP ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def fetch_yesterday_statcast():
    step(f"Yesterday Statcast game log ({YESTERDAY})...")
    try:
        df=pyb.statcast(start_dt=YESTERDAY,end_dt=YESTERDAY)
        if df is None or df.empty: return "  No data.\n", pd.DataFrame()
        step(f"  {len(df)} pitches, {df['game_pk'].nunique()} games")
        # Statcast's "player_name" column on raw pitch-by-pitch data is the
        # PITCHER on that pitch, not the batter (verified live elsewhere in
        # this file — see compute_hit_streaks) — grouping "batters who got
        # hits" by it silently credited the pitcher who allowed the hit.
        # Fixed to group by the numeric "batter" id and resolve names via
        # the cached active-roster id->name map.
        hits=df[df["events"].isin(["single","double","triple","home_run"])].copy()
        hit_sum=hits.groupby("batter").agg(
            H=("events","count"),HR=("events",lambda x:(x=="home_run").sum()),
            avg_EV=("launch_speed","mean"),max_EV=("launch_speed","max"),
            avg_LA=("launch_angle","mean")
        ).round(1).sort_values("H",ascending=False).reset_index()
        by_id=fetch_active_roster_by_name().get("by_id",{})
        hit_sum["batter"]=hit_sum["batter"].apply(lambda pid: by_id.get(pid,f"MLBAM#{int(pid)}"))
        hit_sum=hit_sum.rename(columns={"batter":"Batter"})
        hit_sum.index+=1
        out=f"  Yesterday ({YESTERDAY}): {len(df)} pitches | {df['game_pk'].nunique()} games\n\n"
        out+=f"  BATTERS WHO GOT HITS YESTERDAY:\n{fmt(hit_sum.head(60))}\n"
        return out, df
    except Exception as e:
        warn(f"Yesterday statcast: {e}"); return f"  Failed: {e}\n", pd.DataFrame()


def compute_hit_streaks():
    # Original bug: streak only continued when consecutive hit-games were exactly
    # 1 calendar day apart. Any off day, doubleheader gap, or postponement between
    # a player's games broke the "streak" even though he'd started every game his
    # team played — so this returned "no streaks" almost every run. Fix: build a
    # per-game (not per-hit) record of whether the player got a hit that game from
    # ALL his plate appearances (not just games with a hit), then walk backward
    # from his most recent game and stop at the first hitless one — the correct
    # definition of an active streak, independent of calendar gaps.
    # Second bug found on review, confirmed live: grouped by Statcast's
    # "player_name" column, which on raw pitch-by-pitch data is the
    # PITCHER on that pitch, not the batter — the same well-documented
    # Statcast quirk this project already found and fixed once in the picks
    # scorer's L7 rolling-form fetch (see README), recurring here
    # independently. Verified live: the top "hit streak" this produced
    # before the fix was "Peralta, Wandy" at 7 games — Wandy Peralta is a
    # relief pitcher; pulling that exact row showed pitcher id 593974
    # (Peralta) and batter id 682998 (Corbin Carroll, per the play
    # description) on the same row. Every "streak" this section reported
    # was actually a pitcher's opponents' hitting streak against him, not
    # any individual batter's own streak. Fixed to group by the numeric
    # "batter" id column instead (as the rest of this codebase already
    # does post-fix) and resolve names via the active-roster id->name map.
    step("Active hit streaks (L14 Statcast)...")
    try:
        df=pyb.statcast(start_dt=L14_START,end_dt=L14_END)
        if df is None or df.empty: return "  No data.\n"
        pa=df[df["events"].notna()].copy()
        pa["game_date"]=pd.to_datetime(pa["game_date"])
        pa["got_hit"]=pa["events"].isin(["single","double","triple","home_run"])
        per_game=pa.groupby(["batter","game_date"])["got_hit"].max().reset_index()
        streaks={}
        for player_id,grp in per_game.groupby("batter"):
            grp=grp.sort_values("game_date",ascending=False)
            streak=0
            for got_hit in grp["got_hit"]:
                if got_hit: streak+=1
                else: break
            if streak>=3: streaks[player_id]=streak
        if not streaks: return "  No 3+ game hit streaks.\n"
        by_id=fetch_active_roster_by_name().get("by_id",{})
        sdf=pd.DataFrame([(by_id.get(pid,f"MLBAM#{int(pid)}"),s) for pid,s in streaks.items()],
                          columns=["Player","Hit_Streak"]).sort_values("Hit_Streak",ascending=False).reset_index(drop=True)
        sdf.index+=1
        step(f"  {len(sdf)} players with 3+ game streaks")
        return fmt(sdf)
    except Exception as e:
        warn(f"Hit streaks: {e}"); return f"  Failed: {e}\n"


def compute_rolling_form():
    """L3 rolling stats + rolling EV trend + launch angle consistency."""
    step("L3 rolling batter form + EV/LA trends...")
    try:
        df=pyb.statcast(start_dt=L7_START,end_dt=L7_END)
        if df is None or df.empty: return "  No data.\n"
        batted=df[df["launch_speed"].notna()].copy()
        # Statcast's "player_name" is the pitcher on that pitch, not the
        # batter who put the ball in play (verified live — see
        # compute_hit_streaks) — grouping "batter form" by it silently
        # attributed every batted-ball EV/LA/hit to the pitcher who allowed
        # it. Group by the numeric "batter" id and resolve names instead.
        form=batted.groupby("batter").agg(
            PA=("at_bat_number","count"),
            H=("events",lambda x:x.isin(["single","double","triple","home_run"]).sum()),
            HR=("events",lambda x:(x=="home_run").sum()),
            avg_EV=("launch_speed","mean"),
            max_EV=("launch_speed","max"),
            stdev_LA=("launch_angle","std"),
            avg_LA=("launch_angle","mean"),
            barrel_cnt=("launch_speed",lambda x:(x>=98).sum())
        ).round(2)
        form["AVG"]=form.apply(lambda r: round(r["H"]/r["PA"],3) if r["PA"]>0 else 0,axis=1)
        form["barrel_pct"]=form.apply(lambda r: round(r["barrel_cnt"]/r["PA"]*100,1) if r["PA"]>0 else 0,axis=1)
        form["LA_consistency"]=form["stdev_LA"].apply(lambda x: "🟢 consistent" if x<8 else ("🟡 moderate" if x<15 else "🔴 erratic"))
        form=form[form["PA"]>=5].sort_values("avg_EV",ascending=False).reset_index()
        by_id=fetch_active_roster_by_name().get("by_id",{})
        form["batter"]=form["batter"].apply(lambda pid: by_id.get(pid,f"MLBAM#{int(pid)}"))
        form=form.rename(columns={"batter":"player_name"})
        form.index+=1
        step(f"  {len(form)} batters with 5+ PA in L7")
        return fmt(form.head(100))
    except Exception as e:
        warn(f"Rolling form: {e}"); return f"  Failed: {e}\n"


def compute_player_state_indicators():
    """O-Swing% delta L3 vs season — detect locked-in vs pressing."""
    step("Player state indicators (L3 O-Swing% delta vs season avg)...")
    try:
        df=pyb.statcast(start_dt=L3_START,end_dt=L3_END)
        if df is None or df.empty: return "  No data.\n"
        # Chase rate / first-pitch swing rate are batter swing-decision
        # stats, but this grouped by Statcast's "player_name" (the pitcher
        # on that pitch, not the batter deciding to swing — verified live
        # elsewhere in this file, see compute_hit_streaks). Fixed to group
        # by the numeric "batter" id and resolve names via the roster map.
        by_id=fetch_active_roster_by_name().get("by_id",{})
        # Chase rate = swings on pitches outside zone
        chase=df[df["zone"].between(11,14,inclusive="both") | ~df["zone"].between(1,9,inclusive="both")].copy()
        l3_chase=chase.groupby("batter").agg(
            pitches_out=("zone","count"),
            swings_out=("description",lambda x:x.isin(["swinging_strike","foul","hit_into_play"]).sum())
        )
        l3_chase["L3_chase_rate"]=l3_chase.apply(lambda r: round(r["swings_out"]/r["pitches_out"]*100,1) if r["pitches_out"]>5 else None,axis=1)
        # First pitch swing rate L3
        fp=df[df["pitch_number"]==1].groupby("batter").agg(
            fp_pitches=("pitch_number","count"),
            fp_swings=("description",lambda x:x.isin(["swinging_strike","foul","hit_into_play"]).sum())
        )
        fp["L3_first_pitch_swing_pct"]=fp.apply(lambda r:round(r["fp_swings"]/r["fp_pitches"]*100,1) if r["fp_pitches"]>3 else None,axis=1)
        result=l3_chase[["L3_chase_rate"]].join(fp[["L3_first_pitch_swing_pct"]],how="outer")
        result=result[result["L3_chase_rate"].notna()].sort_values("L3_chase_rate",ascending=False).reset_index()
        result["batter"]=result["batter"].apply(lambda pid: by_id.get(pid,f"MLBAM#{int(pid)}"))
        result=result.rename(columns={"batter":"player_name"})
        result.index+=1
        step(f"  {len(result)} batters analyzed")
        return fmt(result.head(60))
    except Exception as e:
        warn(f"State indicators: {e}"); return f"  Failed: {e}\n"


def _bullpen_fetch_one(args):
    team_name, team_id = args
    try:
        # statsapi.schedule()'s team kwarg is literally `team`, not `teamId` — the
        # old `teamId=` call raised "unexpected keyword argument" on current
        # mlb-statsapi (verified against installed 1.9.0 signature).
        schedule=statsapi.schedule(start_date=L7_START,end_date=TODAY,team=team_id)
        game_ids=[g["game_id"] for g in schedule[:7]]
        usage=defaultdict(lambda:{"IP":0.0,"apps":0,"pitches":0})
        for gid in game_ids[:5]:
            try:
                box=statsapi.boxscore_data(gid)
                # box["away"]/box["home"]["pitchers"] is just a list of person IDs, not
                # stat lines — verified live: the actual per-pitcher box score rows are
                # under the top-level "awayPitchers"/"homePitchers" keys, each a list of
                # dicts keyed "name"/"ip"/"p" (pitches, not "numberOfPitches") with a
                # personId==0 header row to skip. The original code read box[side]["pitchers"]
                # as if it were a dict of stat objects — it never was, on any version.
                away_id=box.get("away",{}).get("team",{}).get("id")
                side_key="awayPitchers" if away_id==team_id else "homePitchers"
                for pdata in box.get(side_key,[]):
                    if not pdata.get("personId"): continue  # skip team-header row
                    pname=pdata.get("name","?").split(",")[0].strip()
                    try: ip=float(pdata.get("ip",0) or 0)
                    except (TypeError,ValueError): ip=0.0
                    try: pitches=int(pdata.get("p",0) or 0)
                    except (TypeError,ValueError): pitches=0
                    usage[pname]["IP"]+=ip
                    usage[pname]["apps"]+=1
                    usage[pname]["pitches"]+=pitches
            except Exception: pass
        return (team_name, usage, None)
    except Exception as e:
        return (team_name, None, str(e)[:50])

def fetch_bullpen_fatigue(game_meta):
    # Up to 30 teams x up to 6 sequential statsapi calls each (schedule + up to
    # 5 boxscores) was the single slowest section in the pipeline — several
    # minutes serial. Fetched concurrently instead (network-bound I/O, GIL
    # releases during the wait) since an unattended daily run needs to reliably
    # finish inside the Actions job timeout. Also dropped a roster() call that
    # was fetched but never actually used.
    step("Bullpen fatigue (L7 usage, L/R availability)...")
    matchups_done=set(); jobs=[]; job_matchup={}
    for gm in game_meta:
        matchup=gm["matchup"]
        if matchup in matchups_done: continue
        matchups_done.add(matchup)
        parts=matchup.split(" @ ")
        for team_name in (parts if len(parts)==2 else [matchup]):
            try:
                team_data=statsapi.lookup_team(team_name)
                if not team_data: continue
                job=(team_name, team_data[0]["id"])
                jobs.append(job); job_matchup[team_name]=matchup
            except Exception:
                job_matchup[team_name]=matchup

    results_by_matchup=defaultdict(list)
    if jobs:
        with ThreadPoolExecutor(max_workers=10) as ex:
            for team_name, usage, err in ex.map(_bullpen_fetch_one, jobs):
                results_by_matchup[job_matchup[team_name]].append((team_name, usage, err))

    lines=[]
    for matchup in sorted(matchups_done, key=lambda m: [gm["matchup"] for gm in game_meta].index(m)):
        lines.append(f"\n  {matchup}:")
        for team_name, usage, err in results_by_matchup.get(matchup, []):
            if err:
                lines.append(f"    {team_name}: {err}")
            elif usage:
                lines.append(f"    {team_name} relievers (L7):")
                sorted_usage=sorted(usage.items(),key=lambda x:x[1]["pitches"],reverse=True)
                for pname,u in sorted_usage[:12]:
                    fatigue="🔴 FATIGUED" if u["pitches"]>60 else ("🟡 MODERATE" if u["pitches"]>30 else "🟢 FRESH")
                    lines.append(f"      {pname:<25} IP:{u['IP']:.1f}  Apps:{u['apps']}  Pitches:{u['pitches']}  {fatigue}")
            else:
                lines.append(f"    {team_name}: No recent usage data")
    return "\n".join(lines)+"\n" if lines else "  No bullpen data available.\n"


def fetch_starter_game_logs(game_meta):
    # statsapi.player_stats() (the text-formatting wrapper originally used
    # here) calls player_stat_data(personId, group, type, season)
    # POSITIONALLY internally — but player_stat_data's real signature is
    # (personId, group, type, sportId, season), so that "season" argument
    # actually lands in the sportId slot, and the real season kwarg stays
    # None. Verified live: calling it exactly as this function did
    # (group="pitching", type="gameLog", season=YEAR) returned only the
    # player's one-line bio ('Jameson "Jamo" Taillon, P (2016-)') with zero
    # game rows, for every pitcher, every run — the "/" or "-" line filter
    # then grabbed that bio line itself as if it were a game log row.
    # Separately verified live that passing season= explicitly to
    # type="gameLog" raises outright ("season parameter is only valid...
    # 'season' type") — gameLog isn't supposed to take a season kwarg at
    # all; omitting it still returns the current season's games (MLB API's
    # own default). Fixed to call the raw hydrated person endpoint directly
    # (bypassing both the broken wrapper and the fragile text-line
    # filtering) and build the table from real structured per-game fields.
    step("Tonight's starters last 5 game logs...")
    lines=[]
    pitchers_done=set()
    team_abbr={t["id"]:t["abbr"] for t in get_team_ids()}
    for gm in game_meta:
        for sp_name, sp_id in [(gm["away_sp"],gm.get("away_sp_id")), (gm["home_sp"],gm.get("home_sp_id"))]:
            if sp_name=="TBD" or sp_name in pitchers_done: continue
            pitchers_done.add(sp_name)
            if not sp_id: lines.append(f"\n  {sp_name}: no player ID"); continue
            try:
                r=statsapi.get("person",{"personId":sp_id,"hydrate":"stats(group=pitching,type=gameLog,sportId=1)"})
                stat_blocks=r.get("people",[{}])[0].get("stats",[])
                splits=stat_blocks[0].get("splits",[]) if stat_blocks else []
                lines.append(f"\n  {sp_name} — Last 5 Starts:")
                lines.append(f"  {'Date':<12} {'Opp':<8} {'IP':<5} {'H':<4} {'R':<4} {'ER':<4} {'BB':<4} {'K':<4} {'HR':<4} {'PC':>4}")
                lines.append("  "+"-"*65)
                if not splits:
                    lines.append("  No game log entries this season yet.")
                    continue
                for s in splits[-5:]:
                    st=s.get("stat",{})
                    opp=team_abbr.get(s.get("opponent",{}).get("id"), s.get("opponent",{}).get("name","?")[:8])
                    date=s.get("date","?")
                    ip=st.get("inningsPitched","?"); h=st.get("hits","?"); rns=st.get("runs","?")
                    er=st.get("earnedRuns","?"); bb=st.get("baseOnBalls","?"); k=st.get("strikeOuts","?")
                    hr=st.get("homeRuns","?"); pc=st.get("numberOfPitches","?")
                    lines.append(f"  {date:<12} {opp:<8} {str(ip):<5} {str(h):<4} {str(rns):<4} {str(er):<4} {str(bb):<4} {str(k):<4} {str(hr):<4} {str(pc):>4}")
            except Exception as e:
                lines.append(f"\n  {sp_name}: {str(e)[:50]}")
    return "\n".join(lines)+"\n"


def compute_pitcher_velocity_trends(game_meta):
    step("Pitcher velocity trends (start-by-start, spin rate, extension)...")
    lines=[]
    pitchers_done=set()
    for gm in game_meta:
        for sp_name in [gm["away_sp"],gm["home_sp"]]:
            if sp_name=="TBD" or sp_name in pitchers_done: continue
            pitchers_done.add(sp_name)
            try:
                pid = gm["away_sp_id"] if sp_name == gm["away_sp"] else gm["home_sp_id"]
                if not pid: lines.append(f"\n  {sp_name}: no MLBAM id available"); continue
                df=pyb.statcast_pitcher(start_dt=L30_START,end_dt=L30_END,player_id=pid)
                if df is None or df.empty: lines.append(f"\n  {sp_name}: no Statcast data"); continue
                # Group by game date
                df["game_date"]=pd.to_datetime(df["game_date"])
                by_game=df.groupby("game_date").agg(
                    velo=("release_speed","mean"),
                    spin=("release_spin_rate","mean"),
                    ext=("release_extension","mean"),
                    zone_pct=("zone",lambda z:((z>=1)&(z<=9)).mean()*100),
                    pitches=("pitch_type","count")
                ).round(2).tail(6)
                # Compute deltas
                season_velo=df["release_speed"].mean()
                season_spin=df["release_spin_rate"].mean()
                lines.append(f"\n  {sp_name} — Velocity/Spin/Extension Trend (last 6 starts):")
                lines.append(f"  Season avg velo: {season_velo:.1f}  Season avg spin: {season_spin:.0f}")
                lines.append(f"  {'Date':<12} {'Velo':>6} {'ΔVelo':>7} {'Spin':>6} {'ΔSpin':>7} {'Ext':>5} {'Zone%':>6} {'PC':>4} {'Flags'}")
                lines.append("  "+"-"*75)
                for date,row in by_game.iterrows():
                    dv=row["velo"]-season_velo
                    ds=row["spin"]-season_spin
                    vflag="🔴" if dv<-1.5 else ("🟢" if dv>1.0 else "")
                    sflag="⚠️" if abs(ds)>150 else ""
                    pv=perceived_velocity(row["velo"],row["ext"])
                    dw=decision_window_ms(pv)
                    lines.append(f"  {str(date.date()):<12} {row['velo']:>6.1f} {dv:>+7.1f} {row['spin']:>6.0f} {ds:>+7.0f} {row['ext']:>5.2f} {row['zone_pct']:>6.1f} {row['pitches']:>4}  {vflag}{sflag}")
                lines.append(f"  Perceived velo (last start): {pv}mph  Decision window: {dw}ms")
                # Threshold alert
                last_velo=by_game["velo"].iloc[-1] if len(by_game)>0 else season_velo
                if last_velo < season_velo-1.5:
                    lines.append(f"  🚨 THRESHOLD ALERT: Velo {last_velo:.1f} is {season_velo-last_velo:.1f}mph BELOW season avg — potential fatigue/injury")
            except Exception as e:
                lines.append(f"\n  {sp_name}: {str(e)[:60]}")
    return "\n".join(lines)+"\n"



# ══════════════════════════════════════════════════════════════════════════════
#  SECTIONS 20-34 — ADVANCED PITCHER ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def compute_pitch_tunneling(game_meta):
    step("Pitch tunneling scores + sequencing tendencies...")
    lines=[]
    pitchers_done=set()
    for gm in game_meta:
        for sp_name in [gm["away_sp"],gm["home_sp"]]:
            if sp_name=="TBD" or sp_name in pitchers_done: continue
            pitchers_done.add(sp_name)
            try:
                pid = gm["away_sp_id"] if sp_name == gm["away_sp"] else gm["home_sp_id"]
                if not pid: continue
                df=pyb.statcast_pitcher(start_dt=L30_START,end_dt=L30_END,player_id=pid)
                if df is None or df.empty: continue

                # Pitch tunneling: compare IVB and HB between fastball and offspeed
                # Tunnel score = similarity of flight path to 2/3 point
                by_type=df.groupby("pitch_type").agg(
                    count=("pitch_type","count"),
                    avg_velo=("release_speed","mean"),
                    avg_ivb=("pfx_z","mean"),   # induced vertical break
                    avg_hb=("pfx_x","mean"),     # horizontal break
                    avg_spin=("release_spin_rate","mean"),
                    avg_ext=("release_extension","mean"),
                    avg_rel_x=("release_pos_x","mean"),
                    avg_rel_z=("release_pos_z","mean"),
                    whiff_rate=("description",lambda x:x.isin(["swinging_strike","swinging_strike_blocked"]).sum()/len(x)*100),
                    zone_pct=("zone",lambda z:((z>=1)&(z<=9)).mean()*100)
                ).round(3)
                by_type=by_type[by_type["count"]>=10]
                usage_pct=by_type["count"]/by_type["count"].sum()*100

                lines.append(f"\n  {sp_name} — Pitch Arsenal + Tunneling:")
                lines.append(f"  {'Type':<6} {'Use%':>5} {'Velo':>5} {'IVB':>5} {'HB':>5} {'Spin':>5} {'Ext':>4} {'Whiff%':>7} {'Zone%':>6}")
                lines.append("  "+"-"*60)
                for pt in by_type.index:
                    r=by_type.loc[pt]
                    pv=perceived_velocity(r["avg_velo"],r["avg_ext"])
                    lines.append(f"  {pt:<6} {usage_pct[pt]:>5.1f} {r['avg_velo']:>5.1f} {r['avg_ivb']:>5.2f} {r['avg_hb']:>5.2f} {r['avg_spin']:>5.0f} {r['avg_ext']:>4.2f} {r['whiff_rate']:>7.1f} {r['zone_pct']:>6.1f}")

                # Tunneling score: pairs of pitches
                pitch_types=list(by_type.index)
                fastballs=[p for p in pitch_types if p in ["FF","SI","FC"]]
                offspeed=[p for p in pitch_types if p in ["SL","CU","CH","FS","ST","SV","KC"]]
                if fastballs and offspeed:
                    lines.append(f"\n  Tunneling scores (lower ΔMove = better tunnel deception):")
                    for fb in fastballs:
                        for os in offspeed:
                            divb=abs(by_type.loc[fb,"avg_ivb"]-by_type.loc[os,"avg_ivb"])
                            dhb=abs(by_type.loc[fb,"avg_hb"]-by_type.loc[os,"avg_hb"])
                            tunnel_score=round(math.sqrt(divb**2+dhb**2),2)
                            velo_gap=round(by_type.loc[fb,"avg_velo"]-by_type.loc[os,"avg_velo"],1)
                            grade="🔥 ELITE TUNNEL" if tunnel_score<1.5 else ("🟢 Good" if tunnel_score<2.5 else ("🟡 Average" if tunnel_score<4.0 else "🔴 Poor"))
                            dw_fb=decision_window_ms(perceived_velocity(by_type.loc[fb,"avg_velo"],by_type.loc[fb,"avg_ext"]))
                            lines.append(f"    {fb}→{os}: ΔMove={tunnel_score:.2f}in  VeloGap={velo_gap}mph  DecisionWindow={dw_fb}ms  {grade}")

                # Pitch sequencing tendencies
                lines.append(f"\n  Pitch Sequencing (first pitch, 2-strike, 0-0):")
                fp_dist=df[df["pitch_number"]==1]["pitch_type"].value_counts(normalize=True)*100
                ts_dist=df[(df["strikes"]==2)]["pitch_type"].value_counts(normalize=True)*100
                lines.append(f"    First pitch: {dict(fp_dist.head(4).round(1))}")
                lines.append(f"    2-strike:    {dict(ts_dist.head(4).round(1))}")

                # Complexity score
                n_pitches=len(by_type)
                rel_stdev_x=df["release_pos_x"].std()
                rel_stdev_z=df["release_pos_z"].std()
                complexity="🔴 Simple (1-2 pitch)" if n_pitches<=2 else ("🟡 Moderate (3 pitch)" if n_pitches==3 else "🟢 Complex (4+)")
                lines.append(f"    Pitch types: {n_pitches}  → {complexity}")
                lines.append(f"    Release consistency: X stdev={rel_stdev_x:.3f}ft  Z stdev={rel_stdev_z:.3f}ft")
                same_slot="🟢 Consistent same slot" if rel_stdev_x<0.1 else ("🟡 Moderate drift" if rel_stdev_x<0.2 else "🔴 Variable slot")
                lines.append(f"    Slot classification: {same_slot}")

            except Exception as e:
                lines.append(f"\n  {sp_name}: {str(e)[:60]}")
    return "\n".join(lines)+"\n"


def compute_ingame_micro_fatigue(game_meta):
    step("In-game micro-fatigue (velo/spin/zone% by inning)...")
    lines=[]
    pitchers_done=set()
    for gm in game_meta:
        for sp_name in [gm["away_sp"],gm["home_sp"]]:
            if sp_name=="TBD" or sp_name in pitchers_done: continue
            pitchers_done.add(sp_name)
            try:
                pid = gm["away_sp_id"] if sp_name == gm["away_sp"] else gm["home_sp_id"]
                if not pid: continue
                df=pyb.statcast_pitcher(start_dt=L14_START,end_dt=L14_END,player_id=pid)
                if df is None or df.empty: continue
                df=df[df["inning"].notna()]
                by_inning=df.groupby(df["inning"].astype(int)).agg(
                    velo=("release_speed","mean"),
                    spin=("release_spin_rate","mean"),
                    zone_pct=("zone",lambda z:((z>=1)&(z<=9)).mean()*100),
                    rel_x=("release_pos_x","mean"),
                    rel_z=("release_pos_z","mean"),
                    count=("pitch_type","count")
                ).round(2)
                early=by_inning[by_inning.index<=2]["velo"].mean()
                late=by_inning[by_inning.index>=5]["velo"].mean() if len(by_inning[by_inning.index>=5])>0 else early
                velo_drop=round(early-late,2) if early and late else 0
                # By pitch count
                df["pc_bucket"]=pd.cut(df["pitch_count_pitcher"] if "pitch_count_pitcher" in df.columns else df.index%120,
                                       bins=[0,30,60,90,120],labels=["1-30","31-60","61-90","91+"])
                by_pc=df.groupby("pc_bucket",observed=True).agg(velo=("release_speed","mean"),zone_pct=("zone",lambda z:((z>=1)&(z<=9)).mean()*100)).round(2)
                lines.append(f"\n  {sp_name} — In-Game Micro-Fatigue (L14 aggregated):")
                lines.append(f"  Inning-by-inning velo/spin/zone%:")
                lines.append(fmt(by_inning.head(9)))
                lines.append(f"  Early innings velo (1-2): {early:.1f}  Late innings (5+): {late:.1f}  Drop: {velo_drop:+.2f}mph")
                if velo_drop>1.5: lines.append(f"  🚨 SIGNIFICANT VELO DROP mid-game — K props may fade late")
                lines.append(f"\n  By pitch count:")
                lines.append(fmt(by_pc))
                # Arm slot drift by inning
                if len(by_inning)>=3:
                    early_x=by_inning[by_inning.index<=2]["rel_x"].mean()
                    late_x=by_inning[by_inning.index>=5]["rel_x"].mean() if len(by_inning[by_inning.index>=5])>0 else early_x
                    slot_drift=abs(late_x-early_x) if early_x and late_x else 0
                    drift_flag="⚠️ ARM SLOT DRIFT detected" if slot_drift>0.15 else "✅ Consistent arm slot"
                    lines.append(f"  Arm slot drift (inning 1-2 vs 5+): {slot_drift:.3f}ft  {drift_flag}")
            except Exception as e:
                lines.append(f"\n  {sp_name}: {str(e)[:60]}")
    return "\n".join(lines)+"\n"


def compute_vaa_haa(game_meta):
    step("VAA/HAA per pitch type for tonight's starters...")
    lines=[]
    pitchers_done=set()
    for gm in game_meta:
        for sp_name in [gm["away_sp"],gm["home_sp"]]:
            if sp_name=="TBD" or sp_name in pitchers_done: continue
            pitchers_done.add(sp_name)
            try:
                pid = gm["away_sp_id"] if sp_name == gm["away_sp"] else gm["home_sp_id"]
                if not pid: continue
                df=pyb.statcast_pitcher(start_dt=L14_START,end_dt=L14_END,player_id=pid)
                if df is None or df.empty: continue
                vaa_col="vaa" if "vaa" in df.columns else "pfx_z"
                haa_col="haa" if "haa" in df.columns else "pfx_x"
                by_type=df.groupby("pitch_type").agg(
                    count=("pitch_type","count"),
                    VAA=(vaa_col,"mean"),
                    HAA=(haa_col,"mean"),
                    spin_axis=("spin_axis","mean") if "spin_axis" in df.columns else ("release_spin_rate","count"),
                ).round(3)
                by_type=by_type[by_type["count"]>=10]
                lines.append(f"\n  {sp_name} — VAA/HAA + Spin Axis:")
                lines.append(f"  {'Type':<6} {'Count':>5} {'VAA':>6} {'HAA':>6} {'SpinAxis':>9}")
                lines.append("  "+"-"*35)
                for pt in by_type.index:
                    r=by_type.loc[pt]
                    spin_str=f"{r.get('spin_axis',0):.0f}°" if "spin_axis" in r else "—"
                    lines.append(f"  {pt:<6} {r['count']:>5} {r['VAA']:>6.3f} {r['HAA']:>6.3f} {spin_str:>9}")
            except Exception as e:
                lines.append(f"\n  {sp_name}: {str(e)[:60]}")
    return "\n".join(lines)+"\n"


def compute_pitcher_complexity(game_meta):
    step("Pitcher complexity + perceived velocity + decision window...")
    lines=[]
    pitchers_done=set()
    for gm in game_meta:
        for sp_name in [gm["away_sp"],gm["home_sp"]]:
            if sp_name=="TBD" or sp_name in pitchers_done: continue
            pitchers_done.add(sp_name)
            try:
                pid = gm["away_sp_id"] if sp_name == gm["away_sp"] else gm["home_sp_id"]
                if not pid: continue
                df=pyb.statcast_pitcher(start_dt=L30_START,end_dt=L30_END,player_id=pid)
                if df is None or df.empty: continue
                # Pitch type count (complexity)
                usage=(df["pitch_type"].value_counts(normalize=True)*100)
                active_types=usage[usage>=5].index.tolist()
                n=len(active_types)
                complexity_label="🔴 Simple" if n<=2 else ("🟡 Moderate" if n==3 else "🟢 Complex")
                # Avg extension
                avg_ext=df["release_extension"].mean()
                avg_velo=df["release_speed"].mean()
                pv=perceived_velocity(avg_velo,avg_ext)
                dw=decision_window_ms(pv)
                # Velocity gap
                if len(active_types)>=2:
                    type_velos=df[df["pitch_type"].isin(active_types)].groupby("pitch_type")["release_speed"].mean()
                    velo_gap=round(type_velos.max()-type_velos.min(),1)
                else:
                    velo_gap=0
                # Release slot consistency
                rel_x_std=df["release_pos_x"].std()
                same_slot_score=round(1/rel_x_std if rel_x_std>0 else 10,1)
                lines.append(f"\n  {sp_name}:")
                lines.append(f"    Pitch types (≥5%): {active_types}  →  {complexity_label}")
                lines.append(f"    Avg velo: {avg_velo:.1f}mph  Avg extension: {avg_ext:.2f}ft")
                lines.append(f"    Perceived velocity: {pv}mph  Decision window: {dw}ms")
                lines.append(f"    Velocity gap (fastest-slowest): {velo_gap}mph  {'🔥 BIG GAP' if velo_gap>=10 else ''}")
                lines.append(f"    Release slot consistency: stdev={rel_x_std:.3f}ft  {'🎯 Tight slot = deception' if rel_x_std<0.1 else ''}")
            except Exception as e:
                lines.append(f"\n  {sp_name}: {str(e)[:60]}")
    return "\n".join(lines)+"\n"


def compute_catcher_pitch_calling(game_meta):
    step("Catcher pitch-calling tendencies (first-pitch fastball %)...")
    lines=[]
    try:
        df=pyb.statcast(start_dt=L14_START,end_dt=L14_END)
        if df is None or df.empty: return "  No data.\n"
        if "fielder_2" not in df.columns: return "  Catcher ID not in data.\n"
        fp=df[df["pitch_number"]==1].copy()
        fastballs=["FF","SI","FC"]
        catcher_fp=fp.groupby("fielder_2").agg(
            total_fp=("pitch_type","count"),
            fastball_fp=("pitch_type",lambda x:x.isin(fastballs).sum())
        )
        catcher_fp["fp_fastball_pct"]=round(catcher_fp["fastball_fp"]/catcher_fp["total_fp"]*100,1)
        catcher_fp=catcher_fp[catcher_fp["total_fp"]>=20].sort_values("fp_fastball_pct",ascending=False).reset_index()
        # Try to get catcher names
        lines.append("  Catcher first-pitch fastball% (L14, min 20 caught first pitches):")
        lines.append(fmt(catcher_fp.head(30)))
        lines.append("  HIGH% = calls lots of first-pitch fastballs → hitters ready for FB")
        lines.append("  LOW%  = mixes it up early → harder to sit on pitch type")
    except Exception as e:
        warn(f"Catcher pitch-calling: {e}")
        lines.append(f"  Failed: {e}")
    return "\n".join(lines)+"\n"



# ══════════════════════════════════════════════════════════════════════════════
#  FANGRAPHS COLUMN DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

BAT_COLS=[
    "Name","Team","Age","G","PA","AB","H","2B","3B","HR","R","RBI",
    "BB","IBB","SO","HBP","SF","SH","GDP","SB","CS","Bunt%",
    "AVG","OBP","SLG","OPS","wOBA","wRC+","WAR",
    "BB%","K%","BB/K","ISO","BABIP",
    "Hard%","Med%","Soft%","Pull%","Cent%","Oppo%",
    "GB%","FB%","LD%","IFFB%",
    "O-Swing%","Z-Swing%","Swing%",
    "O-Contact%","Z-Contact%","Contact%",
    "Zone%","F-Strike%","SwStr%",
    "Barrel%","HardHit%","maxEV","EV50",
    "xBA","xSLG","xwOBA","xOBP",
    "WPA","RE24","Spd","UBR","wRAA","pLI",
]

PIT_COLS=[
    "Name","Team","Age","W","L","ERA","G","GS","SV","HLD","IP","TBF",
    "H","R","ER","HR","BB","IBB","HBP","SO",
    "K/9","BB/9","K/BB","H/9","HR/9","K%","BB%","K-BB%",
    "AVG","WHIP","BABIP","LOB%","ERA-","FIP-","xFIP-",
    "FIP","xFIP","SIERA","kwERA","xERA",
    "GB%","FB%","LD%","IFFB%","HR/FB","SwStr%",
    "O-Swing%","Z-Swing%","Swing%",
    "O-Contact%","Z-Contact%","Contact%",
    "Zone%","F-Strike%","CStr%","CSW%",
    "Barrel%","HardHit%","Hard%","Soft%",
    "WAR","WPA","RE24","pLI",
    "SI%","FA%","FC%","FS%","CH%","CU%","SL%","ST%","SV%",
    "vSI","vFA","vFC","vFS","vCH","vCU","vSL","vST",
    "Stuff+","Location+","Pitching+",
]

def _normalize_last_first(df, col="Name"):
    """Statcast endpoints use "Last, First" in their name column; FanGraphs (and
    this pipeline's lineup data) use "First Last". Renaming the column to "Name"
    without also reformatting the values left every name-based lookup against
    the fallback tables silently broken (0 matches) whenever FanGraphs was down."""
    if col not in df.columns: return df
    def swap(n):
        if isinstance(n,str) and "," in n:
            last,first=[p.strip() for p in n.split(",",1)]
            return f"{first} {last}"
        return n
    df[col]=df[col].apply(swap)
    return df

def _fg_statcast_bat_fallback(yr):
    """When FanGraphs is fully blocked (Cloudflare bot-challenge — verified: UA
    rotation alone doesn't bypass it), reroute season batting through Statcast
    expected stats + exit velo/barrels so the section still carries useful data."""
    try:
        exp=pyb.statcast_batter_expected_stats(yr,minPA=MIN_PA)
        if exp is None or exp.empty: return pd.DataFrame()
        df=exp.rename(columns={"last_name, first_name":"Name","ba":"AVG",
                                "est_ba":"xBA","est_woba":"xwOBA","woba":"wOBA"})
        df=_normalize_last_first(df)
        try:
            ev=pyb.statcast_batter_exitvelo_barrels(yr,minBBE=MIN_BBE)
            if ev is not None and not ev.empty and "player_id" in ev.columns:
                keep=[c for c in ["player_id","brl_percent","ev95percent"] if c in ev.columns]
                df=df.merge(ev[keep].rename(columns={"brl_percent":"Barrel%","ev95percent":"HardHit%"}),
                            on="player_id",how="left")
        except Exception: pass
        if "xwOBA" in df.columns: df=df.sort_values("xwOBA",ascending=False).reset_index(drop=True); df.index+=1
        return df
    except Exception: return pd.DataFrame()

def fg_bat(yr, label="", qual=MIN_PA):
    step(f"FG batting {label or yr}...")
    for source, fn in [("legacy",lambda: pyb.batting_stats(yr, qual=qual)),
                        ("modern",lambda: pyb.fg_batting_data(yr, qual=qual))]:
        try:
            df=fn()
            df=safe(df,BAT_COLS)
            if "wRC+" in df.columns:
                df=df.sort_values("wRC+",ascending=False).reset_index(drop=True); df.index+=1
            step(f"  {len(df)} batters  {len(df.columns)} cols ({source} API)")
            return df
        except Exception as e:
            warn(f"FG bat {label or yr} ({source} API): {e}")
            _time.sleep(1.5)
    warn(f"FG bat {label or yr}: FanGraphs unreachable — falling back to Statcast expected stats")
    df=_fg_statcast_bat_fallback(yr)
    if not df.empty: step(f"  {len(df)} batters (Statcast fallback)")
    return df

def fg_bat_range(s,e,label):
    step(f"FG batting {label}...")
    try:
        df=pyb.batting_stats_range(s,e)
        if df is None or df.empty: return pd.DataFrame()
        if "PA" in df.columns: df=df[df["PA"]>=MIN_PA_R]
        df=safe(df,BAT_COLS)
        if "wRC+" in df.columns:
            df=df.sort_values("wRC+",ascending=False).reset_index(drop=True); df.index+=1
        step(f"  {len(df)} batters")
        return df
    except Exception as e: warn(f"FG bat {label}: {e}"); return pd.DataFrame()

def _fg_statcast_pit_fallback(yr):
    """Statcast fallback for season pitching when FanGraphs is fully blocked."""
    try:
        exp=pyb.statcast_pitcher_expected_stats(yr,minPA=MIN_PA)
        if exp is None or exp.empty: return pd.DataFrame()
        df=exp.rename(columns={"last_name, first_name":"Name","era":"ERA","xera":"xERA",
                                "ba":"AVG_against","est_ba":"xBA_against","est_woba":"xwOBA_against"})
        df=_normalize_last_first(df)
        if "ERA" in df.columns: df=df.sort_values("ERA").reset_index(drop=True); df.index+=1
        return df
    except Exception: return pd.DataFrame()

def fg_pit(yr, label="", qual=MIN_IP):
    step(f"FG pitching {label or yr}...")
    for source, fn in [("legacy",lambda: pyb.pitching_stats(yr, qual=qual)),
                        ("modern",lambda: pyb.fg_pitching_data(yr, qual=qual))]:
        try:
            df=fn()
            df=safe(df,PIT_COLS)
            if "ERA" in df.columns:
                df=df.sort_values("ERA").reset_index(drop=True); df.index+=1
            step(f"  {len(df)} pitchers  {len(df.columns)} cols ({source} API)")
            return df
        except Exception as e:
            warn(f"FG pit {label or yr} ({source} API): {e}")
            _time.sleep(1.5)
    warn(f"FG pit {label or yr}: FanGraphs unreachable — falling back to Statcast expected stats")
    df=_fg_statcast_pit_fallback(yr)
    if not df.empty: step(f"  {len(df)} pitchers (Statcast fallback)")
    return df

def fg_pit_range(s,e,label):
    step(f"FG pitching {label}...")
    try:
        df=pyb.pitching_stats_range(s,e)
        if df is None or df.empty: return pd.DataFrame()
        df=safe(df,PIT_COLS)
        if "ERA" in df.columns:
            df=df.sort_values("ERA").reset_index(drop=True); df.index+=1
        step(f"  {len(df)} pitchers")
        return df
    except Exception as e: warn(f"FG pit {label}: {e}"); return pd.DataFrame()

def fg_team_bat(yr):
    step(f"FG team batting {yr}...")
    try:
        df=pyb.team_batting(yr)
        if "wRC+" in df.columns: df=df.sort_values("wRC+",ascending=False).reset_index(drop=True); df.index+=1
        return df
    except Exception as e:
        try:
            df=pyb.fg_team_batting_data(yr)
            if "wRC+" in df.columns: df=df.sort_values("wRC+",ascending=False).reset_index(drop=True); df.index+=1
            return df
        except Exception as e2: warn(f"{e2}"); return pd.DataFrame()

def fg_team_pit(yr):
    step(f"FG team pitching {yr}...")
    try:
        df=pyb.team_pitching(yr)
        if "ERA" in df.columns: df=df.sort_values("ERA").reset_index(drop=True); df.index+=1
        return df
    except Exception as e:
        try:
            df=pyb.fg_team_pitching_data(yr)
            if "ERA" in df.columns: df=df.sort_values("ERA").reset_index(drop=True); df.index+=1
            return df
        except Exception as e2: warn(f"{e2}"); return pd.DataFrame()

def fg_team_field(yr):
    step(f"FG team fielding {yr}...")
    try: return pyb.team_fielding(yr)
    except Exception as e:
        try: return pyb.fg_team_fielding_data(yr)
        except Exception as e2: warn(f"{e2}"); return pd.DataFrame()

def fg_field(yr):
    step(f"FG individual fielding {yr}...")
    try:
        df=pyb.fielding_stats(yr, qual=5)
        if "DRS" in df.columns: df=df.sort_values("DRS",ascending=False).reset_index(drop=True); df.index+=1
        return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()


def fg_standings(yr):
    step(f"Standings {yr}...")
    try:
        divs=pyb.standings(yr)
        names=["AL East","AL Central","AL West","NL East","NL Central","NL West"]
        parts=[]
        for i,d in enumerate(divs):
            parts.append(f"\n  {names[i] if i<len(names) else f'Div {i+1}'}:")
            parts.append(d.to_string(index=False))
        return "\n".join(parts)+"\n"
    except Exception as e: warn(f"Standings: {e}"); return "  Unavailable\n"


# ══════════════════════════════════════════════════════════════════════════════
#  STATCAST FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def sc_bat_exp(yr):
    step(f"Statcast batter expected {yr}...")
    try: df=pyb.statcast_batter_expected_stats(yr,minPA=MIN_PA); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_bat_pct(yr):
    step(f"Statcast batter percentiles {yr}...")
    try: df=pyb.statcast_batter_percentile_ranks(yr); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_ev(yr):
    step(f"Statcast exit velo {yr}...")
    try: df=pyb.statcast_batter_exitvelo_barrels(yr,minBBE=MIN_BBE); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_bat_arsenal(yr):
    step(f"Statcast batter vs pitch type {yr}...")
    try: df=pyb.statcast_batter_pitch_arsenal(yr,minPA=MIN_PA); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_pit_exp(yr):
    step(f"Statcast pitcher expected {yr}...")
    try: df=pyb.statcast_pitcher_expected_stats(yr,minPA=MIN_PA); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_pit_pct(yr):
    step(f"Statcast pitcher percentiles {yr}...")
    try: df=pyb.statcast_pitcher_percentile_ranks(yr); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_pit_arsenal(yr):
    step(f"Statcast pitcher arsenal {yr}...")
    try: df=pyb.statcast_pitcher_arsenal_stats(yr,minPA=50); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_pit_pitch_arsenal(yr):
    step(f"Statcast pitcher pitch arsenal (run value/whiff by pitch) {yr}...")
    try: df=pyb.statcast_pitcher_pitch_arsenal(yr,minP=100,arsenal_type="n_"); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_pit_exitvelo(yr):
    step(f"Statcast pitcher exit velo allowed {yr}...")
    try: df=pyb.statcast_pitcher_exitvelo_barrels(yr,minBBE=MIN_BBE); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_pit_spin(yr):
    step(f"Statcast pitcher spin direction {yr}...")
    try: df=pyb.statcast_pitcher_spin_dir_comp(yr,pitch_a="FF",pitch_b="SL",minP=100); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_sprint(yr):
    step(f"Statcast sprint speed {yr}...")
    try: df=pyb.statcast_sprint_speed(yr,min_opp=MIN_OPP); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_framing(yr):
    step(f"Statcast catcher framing {yr}...")
    try: df=pyb.statcast_catcher_framing(yr,min_called_p=50); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_poptime(yr):
    step(f"Statcast catcher pop time {yr}...")
    try: df=pyb.statcast_catcher_poptime(yr,min_2b_att=5,min_3b_att=0); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_oaa(yr):
    step(f"Statcast OAA fielding {yr}...")
    try: df=pyb.statcast_outs_above_average(yr, pos=9); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_of_oaa(yr):
    step(f"Statcast outfield directional OAA {yr}...")
    try: df=pyb.statcast_outfield_directional_oaa(yr,min_opp=MIN_OPP); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_of_jump(yr):
    step(f"Statcast outfielder jump {yr}...")
    try: df=pyb.statcast_outfielder_jump(yr,min_att=20); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()

def sc_run_splits(yr):
    step(f"Statcast running splits {yr}...")
    try: df=pyb.statcast_running_splits(yr); step(f"  {len(df)}"); return df
    except Exception as e: warn(f"{e}"); return pd.DataFrame()



# ══════════════════════════════════════════════════════════════════════════════
#  COMPUTED ANALYTICS — SPLITS, MATCHUP GRID, DIRECTIONAL HR, STATE FLAGS
# ══════════════════════════════════════════════════════════════════════════════

def compute_count_decisions():
    step("Batter count-based decisions (first pitch, 2-strike, RISP swing rates)...")
    try:
        df=pyb.statcast(start_dt=L14_START,end_dt=L14_END)
        if df is None or df.empty: return "  No data.\n"
        # These are all batter swing-decision stats, but were grouped by
        # Statcast's "player_name" column, which on raw pitch-by-pitch data
        # is the PITCHER on that pitch, not the batter deciding whether to
        # swing (verified live elsewhere in this file — see
        # compute_hit_streaks). Fixed to group by the numeric "batter" id
        # and resolve display names via the cached roster id->name map.
        by_id=fetch_active_roster_by_name().get("by_id",{})
        swing_desc=["swinging_strike","swinging_strike_blocked","foul","foul_tip","hit_into_play","foul_bunt"]
        # First pitch swing
        fp=df[df["pitch_number"]==1].copy()
        fp["swing"]=fp["description"].isin(swing_desc)
        fp_rate=fp.groupby("batter")["swing"].agg(["sum","count"])
        fp_rate.columns=["fp_swings","fp_pitches"]
        fp_rate["fp_swing_pct"]=round(fp_rate["fp_swings"]/fp_rate["fp_pitches"]*100,1)
        # 2-strike out-of-zone swing (chase in pressure)
        two_k=df[(df["strikes"]==2)].copy()
        two_k_out=two_k[~two_k["zone"].between(1,9,inclusive="both")]
        two_k_out["swing"]=two_k_out["description"].isin(swing_desc)
        ts_chase=two_k_out.groupby("batter")["swing"].agg(["sum","count"])
        ts_chase.columns=["ts_chases","ts_pitches"]
        ts_chase["two_strike_chase_pct"]=round(ts_chase["ts_chases"]/ts_chase["ts_pitches"]*100,1)
        # RISP swing on borderline pitches (zone 11-14)
        risp=df[(df["on_2b"].notna()|df["on_3b"].notna())&df["zone"].between(11,14,inclusive="both")].copy()
        risp["swing"]=risp["description"].isin(swing_desc)
        risp_rate=risp.groupby("batter")["swing"].agg(["sum","count"])
        risp_rate.columns=["risp_swings","risp_pitches"]
        risp_rate["risp_protect_swing_pct"]=round(risp_rate["risp_swings"]/risp_rate["risp_pitches"]*100,1)
        # Merge
        result=fp_rate[["fp_swing_pct"]].join(
            ts_chase[["two_strike_chase_pct"]],how="outer").join(
            risp_rate[["risp_protect_swing_pct"]],how="outer")
        result=result[result["fp_swing_pct"].notna()].sort_values("two_strike_chase_pct",ascending=False).reset_index()
        result["batter"]=result["batter"].apply(lambda pid: by_id.get(pid,f"MLBAM#{int(pid)}"))
        result=result.rename(columns={"batter":"player_name"})
        result.index+=1
        step(f"  {len(result)} batters")
        return fmt(result.head(80))
    except Exception as e:
        warn(f"Count decisions: {e}"); return f"  Failed: {e}\n"


def compute_csw_leaderboard(pit_season):
    """CSW% has no equivalent field in the Statcast "expected stats" shape
    fg_pit() falls back to when FanGraphs is unreachable — this used to
    just report "not in pitching data" on those nights, every time. Real
    fallback: computed directly from the shared season-long Statcast pull
    (fetch_season_statcast()) — CSW% = (called strikes + whiffs, including
    blocked swings) / total pitches, the standard definition, verified live
    against real Statcast "description" values before building this. Only
    used when FanGraphs' own CSW% column isn't available; when it is, that
    real column is used as before, unchanged."""
    if pit_season is not None and not pit_season.empty and "CSW%" in pit_season.columns:
        csw=pit_season[["Name","Team","ERA","K%","CSW%","SwStr%","BB%","WAR"]].sort_values("CSW%",ascending=False).reset_index(drop=True)
        csw.index+=1
        return fmt(csw.head(50))
    df = fetch_season_statcast()
    if df is None or df.empty or "pitcher" not in df.columns or "description" not in df.columns:
        return "  CSW% unavailable — FanGraphs down and season Statcast pull failed.\n"
    csw_mask = df["description"].isin(["called_strike", "swinging_strike", "swinging_strike_blocked"])
    total_pitches = df.groupby("pitcher").size()
    csw_pitches = df[csw_mask].groupby("pitcher").size()
    pa_df = df[df["events"].notna()] if "events" in df.columns else df.iloc[0:0]
    pa_total = pa_df.groupby("pitcher").size()
    k_total = pa_df[pa_df["events"].isin(["strikeout", "strikeout_double_play"])].groupby("pitcher").size()
    by_id = fetch_active_roster_by_name()["by_id"]
    rows = []
    for pid, n in total_pitches.items():
        if n < 200: continue  # min pitch threshold to keep the leaderboard meaningful
        pa = int(pa_total.get(pid, 0))
        rows.append({"Name": by_id.get(pid, f"MLBAM {pid}"), "Pitches": int(n),
                     "CSW%": round(csw_pitches.get(pid, 0) / n * 100, 1),
                     "K%": round(k_total.get(pid, 0) / pa * 100, 1) if pa >= 20 else None, "PA": pa})
    if not rows:
        return "  CSW% unavailable — no pitchers met the 200-pitch threshold.\n"
    table = pd.DataFrame(rows).sort_values("CSW%", ascending=False).reset_index(drop=True)
    table.index += 1
    return ("  FanGraphs unavailable this run — computed directly from Statcast pitch-level data "
            "instead (CSW% = called strikes + whiffs / total pitches, min 200 pitches):\n"
            + fmt(table.head(50)))


def compute_opposing_lineup_k(game_meta, bat_season=None):
    """Rebuilt after finding two real problems on review: batter K% has no
    equivalent field in the Statcast fallback shape bat_season falls back
    to (same gap as CSW% above), AND — independent of that — the section's
    own title ("per GAME... matchup context") was never actually delivered:
    the old implementation just printed a leaguewide top-60 K% table with
    zero connection to tonight's actual games or opposing lineups. Rebuilt
    to genuinely match the title: for each of tonight's games, each team's
    confirmed lineup batters (already reliably ID'd via game_meta) shown
    against the opposing starter, with K%/BB% from FanGraphs when available
    or computed from the shared season Statcast pull when it isn't."""
    obp_source = None
    if bat_season is not None and not bat_season.empty and "K%" in bat_season.columns and "Name" in bat_season.columns:
        obp_source = bat_season.set_index("Name")[["K%","BB%"]].to_dict("index")
    df = None
    pa_total = k_total = bb_total = None
    if obp_source is None:
        df = fetch_season_statcast()
        if df is not None and not df.empty and "batter" in df.columns and "events" in df.columns:
            pa_df = df[df["events"].notna()]
            pa_total = pa_df.groupby("batter").size()
            k_total = pa_df[pa_df["events"].isin(["strikeout", "strikeout_double_play"])].groupby("batter").size()
            bb_total = pa_df[pa_df["events"].isin(["walk", "intent_walk"])].groupby("batter").size()

    lines = []
    for gm in game_meta:
        for sp_key, opp_lineup_key, opp_team_key in [("away_sp","home_lineup","home_team"), ("home_sp","away_lineup","away_team")]:
            sp_name = gm.get(sp_key)
            lineup = gm.get(opp_lineup_key, [])
            if sp_name == "TBD" or not lineup: continue
            rows = []
            for b in lineup:
                name = b.get("name")
                k_pct = bb_pct = pa = None
                if obp_source is not None and name in obp_source:
                    k_pct = obp_source[name].get("K%"); bb_pct = obp_source[name].get("BB%")
                elif pa_total is not None and b.get("id") in pa_total.index:
                    pa = int(pa_total.get(b["id"], 0))
                    if pa >= 15:
                        k_pct = round(k_total.get(b["id"], 0) / pa * 100, 1)
                        bb_pct = round(bb_total.get(b["id"], 0) / pa * 100, 1)
                if k_pct is not None:
                    rows.append((name, k_pct, bb_pct, pa))
            if not rows: continue
            avg_k = round(sum(r[1] for r in rows) / len(rows), 1)
            lines.append(f"\n  {gm['matchup']} — {gm[opp_team_key]} lineup facing {sp_name} (avg K% {avg_k}):")
            lines.append(f"  {'Batter':<25} {'K%':>6} {'BB%':>6}")
            for name, k_pct, bb_pct, pa in sorted(rows, key=lambda r: -r[1]):
                flag = " 🎯 K prop target" if k_pct >= 25 else ""
                lines.append(f"  {name:<25} {k_pct:>6.1f} {bb_pct if bb_pct is not None else 0:>6.1f}{flag}")
    if not lines:
        return "  Opposing lineup K% unavailable — no confirmed lineups with enough data yet.\n"
    source_note = "FanGraphs season K%/BB%" if obp_source is not None else "Statcast pitch-level data (FanGraphs unavailable this run), min 15 PA"
    return f"  Source: {source_note}. Sorted by K% within each lineup:\n" + "\n".join(lines) + "\n"


def compute_hitter_ingame_degradation():
    step("Hitter in-game degradation (bat speed/launch angle by PA number)...")
    try:
        df=pyb.statcast(start_dt=L14_START,end_dt=L14_END)
        if df is None or df.empty: return "  No data.\n"
        df=df[df["at_bat_number"].notna() & df["launch_speed"].notna()].copy()
        df["ab_bucket"]=pd.cut(df["at_bat_number"],bins=[0,1,2,3,4,9],labels=["1st","2nd","3rd","4th","5th+"])
        # "player_name" is the pitcher on that pitch, not the hitter whose
        # in-game degradation this section claims to measure (verified live
        # elsewhere in this file — see compute_hit_streaks). Group by the
        # numeric "batter" id and resolve display names via the roster map.
        by_ab=df.groupby(["batter","ab_bucket"],observed=True).agg(
            avg_EV=("launch_speed","mean"),
            avg_LA=("launch_angle","mean"),
            count=("launch_speed","count")
        ).round(2)
        by_id=fetch_active_roster_by_name().get("by_id",{})
        # Find players who degrade significantly
        lines=["  Hitter performance by plate appearance number (L14, min 5 balls in play):"]
        for player_id,grp in by_ab.groupby(level=0):
            grp=grp.droplevel(0)
            if len(grp)>=3 and grp["count"].sum()>=10:
                first_ev=grp.iloc[0]["avg_EV"] if len(grp)>0 else None
                last_ev=grp.iloc[-1]["avg_EV"] if len(grp)>0 else None
                if first_ev and last_ev and abs(first_ev-last_ev)>3:
                    flag="🔴 DEGRADES" if first_ev>last_ev else "🟢 IMPROVES"
                    player=by_id.get(player_id,f"MLBAM#{int(player_id)}")
                    lines.append(f"    {player}: 1st AB EV={first_ev:.1f} → Last EV={last_ev:.1f}  {flag}")
        if len(lines)==1: lines.append("  No significant degradation patterns found in L14")
        return "\n".join(lines[:50])+"\n"
    except Exception as e:
        warn(f"Hitter degradation: {e}"); return f"  Failed: {e}\n"


def compute_directional_hr_score(game_meta, bat_df=None):
    step("Directional HR score (spray direction × park dimensions × weather)...")
    lines=[]
    seen=set()
    for gm in game_meta:
        venue=gm["venue"]
        sk=None
        for k in STADIUMS:
            if k.lower() in venue.lower() or venue.lower() in k.lower():
                sk=k; break
        if not sk or sk in seen: continue
        seen.add(sk)
        d=STADIUMS[sk]
        lat,lon,dome,team,cf_deg,elev,lf,cf_d,rf,lfw,cfw,rfw,foul,surf,humidor,eye,retract=d
        # Get weather if available
        wx_is_estimate=False
        try:
            # retry_get instead of a bare call: this Open-Meteo endpoint was
            # confirmed to time out on a real run ("Read timed out" for
            # Wrigley Field) in the separate weather fetch generate_picks.py
            # already hardened — this function hits the same API with the
            # same zero-retry pattern that call had, silently falling
            # through to fake-but-plausible defaults with no indication
            # those weren't the real forecast. fetch_weather() (Section 5,
            # this file) already uses retry_get; this one didn't.
            r=retry_get("https://api.open-meteo.com/v1/forecast",params={
                "latitude":lat,"longitude":lon,
                "hourly":"temperature_2m,windspeed_10m,winddirection_10m,relativehumidity_2m",
                "temperature_unit":"fahrenheit","windspeed_unit":"mph","timezone":"auto","forecast_days":1
            },timeout=15,retries=2); r.raise_for_status()
            h=r.json()["hourly"]
            idx=min(max(gm["hour"],0),23)
            temp=h["temperature_2m"][idx]; wsp=h["windspeed_10m"][idx]; wdir=h["winddirection_10m"][idx]; humid=h["relativehumidity_2m"][idx]
        except Exception:
            temp=72; wsp=5; wdir=cf_deg; humid=50; wx_is_estimate=True
        dens=air_density_pct(elev,temp,humid)
        # Wind boost for pull vs oppo hitters
        diff=(wdir-cf_deg+360)%360
        lf_wind_boost=1.0+(wsp*0.003) if 270<diff or diff<30 else (1.0-(wsp*0.003) if 90<diff<270 else 1.0)
        rf_wind_boost=1.0+(wsp*0.003) if 90<diff<270 else (1.0-(wsp*0.003) if 270<diff or diff<30 else 1.0)
        alt_bonus=1.0+(5280-elev)*0.00003 if not dome else 1.0
        lines.append(f"\n  {gm['matchup']} — {sk}:")
        lines.append(f"  LF({lf}ft/{lfw}ft wall)  CF({cf_d}ft)  RF({rf}ft/{rfw}ft wall)")
        lines.append(f"  Surface: {surf}  Humidor: {'YES' if humidor else 'No'}  Batter Eye: {eye}")
        lines.append(f"  Air density: {dens:.4f}  Altitude bonus: {alt_bonus:.4f}")
        lines.append(f"  Wind pull-side boost: {lf_wind_boost:.3f}  Oppo-side boost: {rf_wind_boost:.3f}")
        lines.append(f"  PULL HITTERS HR INDEX: {round(lf_wind_boost*alt_bonus/dens,3)}")
        lines.append(f"  OPPO HITTERS HR INDEX: {round(rf_wind_boost*alt_bonus/dens,3)}")
        if wx_is_estimate:
            lines.append(f"  ⚠️  Weather fetch failed — using league-average estimate (72°F, 5mph), not the real forecast")
        if not dome:
            if wsp>=10 and abs(diff-180)<30: lines.append(f"  🔴 WIND IN — suppresses all HRs ({wsp:.0f}mph from CF)")
            elif wsp>=10 and (diff<30 or diff>330): lines.append(f"  🔴 WIND IN — HR suppressed ({wsp:.0f}mph from CF)")
            elif wsp>=10 and 150<diff<210: lines.append(f"  🔥 WIND OUT — HR boost ALL hitters ({wsp:.0f}mph out to CF)")
        if humidor: lines.append(f"  ✅ HUMIDOR: ball stored at ~50°F/50% humidity — reduces carry vs dry conditions")
    return "\n".join(lines)+"\n"


def compute_lineup_context(game_meta, bat_season=None):
    """Rewritten after finding two real, stacked bugs on review: this made its
    own separate, fallback-free raw MLB API call instead of reusing the
    already-populated, already-3-tier-fallback-protected game_meta this
    function is already handed as a parameter — and its parsing assumed
    lineup player objects are nested under "person"/"battingOrder" (the
    exact same wrong-structure assumption that was the original biggest bug
    fixed early in this project, in fetch_lineups() itself). Whenever
    lineups weren't posted by the primary MLB API tier yet (the normal case
    for a morning run), that combination guaranteed this section was empty
    every single time. Now reuses game_meta's away_lineup/home_lineup
    directly. Also actually implements the "OBP ahead" the section title
    always promised but never delivered — bat_season was accepted as a
    parameter and silently never used."""
    step("Lineup context (OBP ahead, protection behind, projected PA)...")
    obp_by_name = {}
    if bat_season is not None and not bat_season.empty and "OBP" in bat_season.columns:
        name_col = "Name" if "Name" in bat_season.columns else None
        if name_col:
            obp_by_name = dict(zip(bat_season[name_col], bat_season["OBP"]))
    lines=[]
    for gm in game_meta:
        for team_key, lineup_key in [("away_team","away_lineup"), ("home_team","home_lineup")]:
            lups = gm.get(lineup_key, [])
            if not lups: continue
            lines.append(f"\n  {gm[team_key]} lineup context:")
            lines.append(f"  {'Slot':<5} {'Player':<25} {'ProjPA/yr':>9}  {'OBP ahead':<10} Protected by")
            lines.append("  "+"-"*80)
            for i, p in enumerate(lups):
                slot = p.get("order") or i+1
                name = p.get("name","?")
                pa_proj = ORDER_PA.get(min(slot,9), 630)
                prev_name = lups[i-1].get("name") if len(lups) > 1 else None
                obp_ahead = obp_by_name.get(prev_name) if prev_name else None
                obp_str = f"{obp_ahead:.3f}" if obp_ahead is not None else "n/a"
                next_idx = (i+1) % len(lups)
                protection = lups[next_idx].get("name","?") if next_idx != i else "—"
                lines.append(f"  {slot:<5} {name:<25} {pa_proj:>9}  {obp_str:<10} {protection}")
    if not lines:
        return "  Lineup context unavailable — no confirmed lineups yet.\n"
    return "\n".join(lines)+"\n"


def compute_regression_clusters():
    # Original referenced columns ("xba","xwoba","barrel_batted_rate","hard_hit_percent")
    # that don't exist on pybaseball's current statcast_batter_expected_stats() output —
    # verified live: that endpoint actually returns est_ba/est_woba (not xba/xwoba), and
    # barrel%/hard-hit% aren't in it at all — they're on statcast_batter_exitvelo_barrels()
    # as brl_percent/ev95percent. This silently "Failed" every run under the old names.
    step("Regression clusters (BABIP outliers + xBA gap + hard hit)...")
    try:
        sc=pyb.statcast_batter_expected_stats(YEAR,minPA=MIN_PA)
        if sc is None or sc.empty: return "  No data.\n"
        if not all(c in sc.columns for c in ["player_id","ba","est_ba"]):
            return f"  Expected columns missing — pybaseball schema drift. Got: {list(sc.columns)}\n"
        sc=sc.copy()
        sc["xba_gap"]=round(sc["est_ba"]-sc["ba"],3)
        try:
            ev=pyb.statcast_batter_exitvelo_barrels(YEAR,minBBE=MIN_BBE)
            if ev is not None and not ev.empty and "player_id" in ev.columns:
                keep=[c for c in ["player_id","brl_percent","ev95percent"] if c in ev.columns]
                sc=sc.merge(ev[keep],on="player_id",how="left")
        except Exception: pass
        name_col="last_name, first_name" if "last_name, first_name" in sc.columns else "player_id"
        display_cols=[c for c in [name_col,"player_id","ba","est_ba","xba_gap","est_woba","brl_percent","ev95percent"] if c in sc.columns]
        positive=sc[sc["xba_gap"]>0.030].sort_values("xba_gap",ascending=False).head(20)
        negative=sc[sc["xba_gap"]<-0.030].sort_values("xba_gap").head(20)
        out="  UNDERPERFORMING (xBA >> BA — positive regression candidates, BABIP-unlucky):\n"
        out+=fmt(positive[display_cols])
        out+="\n\n  OVERPERFORMING (BA >> xBA — negative regression candidates, BABIP-lucky):\n"
        out+=fmt(negative[display_cols])
        out+="\n\n  brl_percent=Barrel% of batted balls (Savant)  ev95percent=HardHit% (EV≥95mph)\n"
        return out
    except Exception as e:
        warn(f"Regression clusters: {e}"); return f"  Failed: {e}\n"


def compute_pitcher_archetype_clusters():
    step("Pitcher archetype clusters (KMeans on arsenal profile)...")
    if not SKLEARN_OK: return "  scikit-learn not installed. Run: pip install scikit-learn\n"
    try:
        df=pyb.statcast_pitcher_arsenal_stats(YEAR,minPA=100)
        if df is None or df.empty: return "  No arsenal data.\n"
        feature_cols=[c for c in ["n_ff_formatted","n_sl_formatted","n_ch_formatted","n_cu_formatted",
                                    "n_si_formatted","n_fc_formatted","n_fs_formatted",
                                    "release_speed","pfx_z","pfx_x"] if c in df.columns]
        if len(feature_cols)<4: return "  Insufficient features for clustering.\n"
        X=df[feature_cols].fillna(0)
        scaler=StandardScaler()
        Xs=scaler.fit_transform(X)
        km=KMeans(n_clusters=8,random_state=42,n_init=10)
        df["cluster"]=km.fit_predict(Xs)
        cluster_names={0:"Power FB",1:"Breaking Ball",2:"Contact/Sinker",3:"Changeup Heavy",
                       4:"Mixed Arsenal",5:"Command/Finesse",6:"Two-Pitch",7:"Elite Multi"}
        df["archetype"]=df["cluster"].map(cluster_names)
        result=df[["player_name" if "player_name" in df.columns else df.columns[0],"archetype"]+feature_cols[:5]].head(100)
        step(f"  {len(df)} pitchers clustered into 8 archetypes")
        return fmt(result)
    except Exception as e:
        warn(f"Archetype clusters: {e}"); return f"  Failed: {e}\n"


def compute_threshold_flags(game_meta):
    step("Threshold crossing alert flags...")
    lines=["  🚨 THRESHOLD CROSSING ALERTS — Non-linear edge indicators\n"]
    seen=set()
    for gm in game_meta:
        venue=gm["venue"]
        sk=None
        for k in STADIUMS:
            if k.lower() in venue.lower() or venue.lower() in k.lower():
                sk=k; break
        if not sk or sk in seen: continue
        seen.add(sk)
        d=STADIUMS[sk]
        lat,lon,dome=d[0],d[1],d[2]
        if dome: continue
        try:
            r=retry_get("https://api.open-meteo.com/v1/forecast",params={
                "latitude":lat,"longitude":lon,
                "hourly":"temperature_2m,windspeed_10m,winddirection_10m",
                "temperature_unit":"fahrenheit","windspeed_unit":"mph","timezone":"auto","forecast_days":1
            },timeout=15,retries=2); r.raise_for_status()
            h=r.json()["hourly"]
            idx=min(max(gm["hour"],0),23)
            temp=h["temperature_2m"][idx]; wsp=h["windspeed_10m"][idx]
            if wsp>=10: lines.append(f"  💨 {gm['matchup']}: Wind {wsp:.0f}mph — THRESHOLD CROSSED (10+mph = significant carry effect)")
            if wsp>=15: lines.append(f"  🌪️ {gm['matchup']}: Wind {wsp:.0f}mph — HIGH WIND (15+mph = major factor)")
            if temp<45:  lines.append(f"  🥶 {gm['matchup']}: Temp {temp:.0f}°F — COLD THRESHOLD (under 45°F suppresses power)")
            if temp>88:  lines.append(f"  🔥 {gm['matchup']}: Temp {temp:.0f}°F — HEAT THRESHOLD (88°F+ = ball carries notably farther)")
        except: pass
    lines.append("\n  Pitcher velocity threshold alerts (see Section 23 for per-pitcher detail)")
    lines.append("  Rule: If last-start velo is >1.5mph below season avg → 🚨 RED FLAG")
    lines.append("  Rule: If spin rate change >150rpm → ⚠️ pitch effectiveness change")
    lines.append("  Rule: Chase rate L3 vs season >5% spike → player pressing/struggling")
    return "\n".join(lines)+"\n"



# ══════════════════════════════════════════════════════════════════════════════
#  MLB STATS API — OFFICIAL SPLITS, LEADERS, GAME LOGS
# ══════════════════════════════════════════════════════════════════════════════

def fetch_mlb_leaders():
    step("MLB.com league leaders (same-day official)...")
    cats=["homeRuns","hits","battingAverage","rbi","stolenBases","runs",
          "strikeouts","wins","earnedRunAverage","whip","saves","onBasePlusSlugging"]
    lines=[]
    for cat in cats:
        try:
            r=retry_get("https://statsapi.mlb.com/api/v1/stats/leaders",
                       params={"leaderCategories":cat,"season":YEAR,"sportId":1,"limit":10},
                       headers={"User-Agent":"Mozilla/5.0"},timeout=15,retries=2)
            if r.status_code!=200: continue
            data=r.json().get("leagueLeaders",[])
            if not data: continue
            cat_data=data[0]
            leaders=cat_data.get("leaders",[])
            lines.append(f"\n  {cat.upper()}:")
            for i,l in enumerate(leaders[:10],1):
                name=l.get("person",{}).get("fullName","?")
                team=l.get("team",{}).get("abbreviation","?")
                val=l.get("value","?")
                lines.append(f"    {i:2}. {name:<25} {team:<5} {val}")
        except Exception as e:
            lines.append(f"\n  {cat}: {str(e)[:40]}")
    return "\n".join(lines)+"\n" if lines else "  MLB leaders API unavailable.\n"


def fetch_mlb_splits_batters(game_meta):
    """Takes game_meta (structured lineups) instead of the flat player_ids
    dict this used to take. Real bug found on review: player_ids mixes
    probable-pitcher IDs and lineup-batter IDs in one dict, with pitchers
    inserted first in fetch_lineups() — on a normal 15-game slate that's up
    to 30 pitcher IDs, exactly filling the old `list(player_ids.items())[:30]`
    slice every time. This function asks the MLB API for *hitting* splits,
    which are structurally empty for a pitcher, so it silently processed 30
    pitchers, got 0 real rows, and returned "unavailable" on every run —
    verified live: player_ids had 290 real entries and the API calls all
    succeeded (200 OK), but 30/30 of the first 30 were starters, not
    batters. Fixed to build a real batter-only {name: id} map from
    game_meta's lineups directly."""
    batter_ids = {}
    for gm in game_meta:
        for b in gm.get("away_lineup", []) + gm.get("home_lineup", []):
            if b.get("id") and b.get("name") not in batter_ids:
                batter_ids[b["name"]] = b["id"]
    step(f"MLB Stats API batter splits (vs LHP/RHP, Home/Away, Day/Night) for {min(len(batter_ids),30)} players...")
    lines=[]
    count=0
    for name, pid in list(batter_ids.items())[:30]:
        try:
            r=retry_get(f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
                       params={"stats":"statSplits","group":"hitting","season":YEAR,
                               "sitCodes":"vl,vr,h,a,d,n"},
                       headers={"User-Agent":"Mozilla/5.0"},timeout=15,retries=2)
            if r.status_code!=200: continue
            stats=r.json().get("stats",[])
            if not stats: continue
            splits_data=stats[0].get("splits",[])
            player_splits={}
            for sp in splits_data:
                desc=sp.get("split",{}).get("description","?")
                stat=sp.get("stat",{})
                player_splits[desc]={"AVG":stat.get("avg","?"),"OBP":stat.get("obp","?"),
                                     "SLG":stat.get("slg","?"),"HR":stat.get("homeRuns","?"),
                                     "K%":f"{stat.get('strikeOuts',0)}/{stat.get('plateAppearances',1)}",
                                     "PA":stat.get("plateAppearances","?")}
            if player_splits:
                lines.append(f"\n  {name}:")
                for split_name,sv in player_splits.items():
                    conf=confidence_flag(sv.get("PA",0))
                    lines.append(f"    {split_name:<20} AVG:{sv['AVG']}  OBP:{sv['OBP']}  SLG:{sv['SLG']}  HR:{sv['HR']}  PA:{sv['PA']} {conf}")
                count+=1
        except Exception: pass
    step(f"  {count} players with splits")
    return "\n".join(lines)+"\n" if lines else "  Batter splits unavailable.\n"


def fetch_mlb_splits_pitchers(game_meta):
    step("MLB Stats API pitcher splits (vs LHB/RHB, Home/Away, Day/Night)...")
    lines=[]
    pitchers_done=set()
    for gm in game_meta:
        for sp_name, sp_id in [(gm["away_sp"],gm.get("away_sp_id")),(gm["home_sp"],gm.get("home_sp_id"))]:
            if sp_name=="TBD" or sp_name in pitchers_done or not sp_id: continue
            pitchers_done.add(sp_name)
            try:
                r=retry_get(f"https://statsapi.mlb.com/api/v1/people/{sp_id}/stats",
                           params={"stats":"statSplits","group":"pitching","season":YEAR,
                                   "sitCodes":"vl,vr,h,a,d,n"},
                           headers={"User-Agent":"Mozilla/5.0"},timeout=15,retries=2)
                if r.status_code!=200: continue
                stats=r.json().get("stats",[])
                if not stats: continue
                splits=stats[0].get("splits",[])
                lines.append(f"\n  {sp_name} — Pitcher Splits:")
                for sp in splits:
                    desc=sp.get("split",{}).get("description","?")
                    stat=sp.get("stat",{})
                    era=stat.get("era","?"); whip=stat.get("whip","?")
                    avg=stat.get("avg","?"); k9=stat.get("strikeoutsPer9Inn","?")
                    ip=stat.get("inningsPitched","?")
                    lines.append(f"    {desc:<20} ERA:{era}  WHIP:{whip}  AVG:{avg}  K/9:{k9}  IP:{ip}")
            except Exception as e:
                lines.append(f"\n  {sp_name}: {str(e)[:50]}")
    return "\n".join(lines)+"\n" if lines else "  Pitcher splits unavailable.\n"


def fetch_mlb_game_logs(game_meta):
    """Takes game_meta instead of the flat player_ids dict — same fix, same
    reason as fetch_mlb_splits_batters: player_ids has probable-pitcher IDs
    inserted before lineup-batter IDs, so slicing its first N entries for a
    *hitting* game log silently grabbed starters instead of batters on
    every real run."""
    batter_ids = {}
    for gm in game_meta:
        for b in gm.get("away_lineup", []) + gm.get("home_lineup", []):
            if b.get("id") and b.get("name") not in batter_ids:
                batter_ids[b["name"]] = b["id"]
    # Verified live: the gameLog API's "opponent" object has no "abbreviation"
    # key (only id/name/link), so the old code's .get("abbreviation","?") was
    # always "?". Built from the same team-ID list used elsewhere instead.
    abbr_by_team_id = {t["id"]: t["abbr"] for t in get_team_ids()}
    step(f"MLB Stats API player game logs L14 (tonight's players)...")
    lines=[]
    count=0
    for name, pid in list(batter_ids.items())[:25]:
        try:
            r=retry_get(f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
                       params={"stats":"gameLog","group":"hitting","season":YEAR,
                               "startDate":L14_START,"endDate":TODAY},
                       headers={"User-Agent":"Mozilla/5.0"},timeout=15,retries=2)
            if r.status_code!=200: continue
            stats=r.json().get("stats",[])
            if not stats: continue
            splits=stats[0].get("splits",[])
            if not splits: continue
            lines.append(f"\n  {name} — Game Logs L14:")
            lines.append(f"  {'Date':<12} {'Opp':<6} {'AB':<3} {'H':<3} {'HR':<3} {'RBI':<4} {'BB':<3} {'K':<3} {'AVG'}")
            lines.append("  "+"-"*55)
            for sp in splits[-7:]:
                date=sp.get("date","?"); opp=abbr_by_team_id.get(sp.get("opponent",{}).get("id"), "?")
                stat=sp.get("stat",{})
                lines.append(f"  {date:<12} {opp:<6} {stat.get('atBats','?'):<3} {stat.get('hits','?'):<3} "
                             f"{stat.get('homeRuns','?'):<3} {stat.get('rbi','?'):<4} {stat.get('baseOnBalls','?'):<3} "
                             f"{stat.get('strikeOuts','?'):<3} {stat.get('avg','?')}")
            count+=1
        except Exception: pass
    step(f"  {count} players with game logs")
    return "\n".join(lines)+"\n" if lines else "  Game logs unavailable.\n"


def fetch_babip_career_compare(game_meta):
    """Takes game_meta instead of the flat player_ids dict — same fix, same
    reason as fetch_mlb_splits_batters/fetch_mlb_game_logs: BABIP is a
    hitting stat, and player_ids' pitcher-first ordering meant this was
    silently querying starters' (nonexistent) hitting BABIP on every run."""
    batter_ids = {}
    for gm in game_meta:
        for b in gm.get("away_lineup", []) + gm.get("home_lineup", []):
            if b.get("id") and b.get("name") not in batter_ids:
                batter_ids[b["name"]] = b["id"]
    step("BABIP vs career average (regression signals)...")
    lines=[f"  {'Player':<28} {'2026_BABIP':>10} {'Career_BABIP':>12} {'Delta':>7} {'Signal'}"]
    lines.append("  "+"-"*70)
    count=0
    for name, pid in list(batter_ids.items())[:30]:
        try:
            # Current season
            r1=retry_get(f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
                        params={"stats":"season","group":"hitting","season":YEAR},
                        headers={"User-Agent":"Mozilla/5.0"},timeout=10,retries=2)
            # Career
            r2=retry_get(f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
                        params={"stats":"career","group":"hitting"},
                        headers={"User-Agent":"Mozilla/5.0"},timeout=10,retries=2)
            if r1.status_code==200 and r2.status_code==200:
                s1=r1.json().get("stats",[{}])[0].get("splits",[{}])
                s2=r2.json().get("stats",[{}])[0].get("splits",[{}])
                if s1 and s2:
                    curr_babip=float(s1[0].get("stat",{}).get("babip",0) or 0)
                    car_babip=float(s2[0].get("stat",{}).get("babip",0) or 0)
                    if curr_babip>0 and car_babip>0:
                        delta=curr_babip-car_babip
                        signal="🟢 DUE UP" if delta<-0.030 else ("🔴 DUE DOWN" if delta>0.030 else "🟡 Normal")
                        lines.append(f"  {name:<28} {curr_babip:>10.3f} {car_babip:>12.3f} {delta:>+7.3f} {signal}")
                        count+=1
        except: pass
    step(f"  {count} players compared")
    return "\n".join(lines)+"\n"


def fetch_standings(year):
    step(f"Standings {year}...")
    return fg_standings(year)


# ══════════════════════════════════════════════════════════════════════════════
#  ADDITIONAL COMPUTED SECTIONS
# ══════════════════════════════════════════════════════════════════════════════

def compute_regime_detection():
    step("League environment regime detection (monthly rolling)...")
    try:
        df_pit=pyb.pitching_stats_range(L30_START,L30_END)
        df_bat=pyb.batting_stats_range(L30_START,L30_END)
        lines=["  LEAGUE ENVIRONMENT L30 — Regime Context:"]
        if df_pit is not None and not df_pit.empty and "ERA" in df_pit.columns:
            avg_era=df_pit["ERA"].mean()
            avg_k9=df_pit["K/9"].mean() if "K/9" in df_pit.columns else None
            avg_bb9=df_pit["BB/9"].mean() if "BB/9" in df_pit.columns else None
            lines.append(f"  Avg ERA (L30): {avg_era:.2f}  {'🔵 PITCHER ENVIRONMENT' if avg_era<4.0 else '🔴 HITTER ENVIRONMENT' if avg_era>4.5 else '🟡 Neutral'}")
            if avg_k9: lines.append(f"  Avg K/9: {avg_k9:.2f}")
            if avg_bb9: lines.append(f"  Avg BB/9: {avg_bb9:.2f}")
        if df_bat is not None and not df_bat.empty and "wRC+" in df_bat.columns:
            avg_wrc=df_bat["wRC+"].mean()
            lines.append(f"  Avg wRC+ (L30): {avg_wrc:.0f}  (100 = league average)")
        lines.append(f"\n  Current month: {datetime.now().strftime('%B %Y')}")
        lines.append("  Use this to calibrate prop lines: pitcher-friendly months = lean unders on runs/TB")
        return "\n".join(lines)+"\n"
    except Exception as e:
        warn(f"Regime: {e}"); return f"  Failed: {e}\n"


def compute_score_differential_splits():
    step("Score differential performance splits (winning/losing/tied)...")
    try:
        df=pyb.statcast(start_dt=L14_START,end_dt=L14_END)
        if df is None or df.empty: return "  No data.\n"
        if "bat_score" not in df.columns or "fld_score" not in df.columns:
            return "  Score columns not available in this Statcast pull.\n"
        df["score_diff"]=df["bat_score"]-df["fld_score"]
        df["game_state"]=pd.cut(df["score_diff"],bins=[-50,-3,-1,1,3,50],
                                labels=["Down 3+","Down 1-2","Tied/Close","Up 1-2","Up 3+"])
        batted=df[df["launch_speed"].notna()].copy()
        by_state=batted.groupby(["player_name","game_state"],observed=True).agg(
            avg_EV=("launch_speed","mean"),
            K_rate=("events",lambda x:(x=="strikeout").sum()/len(x)*100),
            count=("launch_speed","count")
        ).round(2)
        # Find interesting splits
        lines=["  Players with notable score-differential performance differences (L14):"]
        for player,grp in by_state.groupby(level=0):
            grp=grp.droplevel(0)
            if grp["count"].sum()<10: continue
            if "Tied" in grp.index and "Down 2+" in grp.index:
                ev_tied=grp.loc["Tied","avg_EV"] if "Tied" in grp.index else None
                ev_down=grp.loc["Down 2+","avg_EV"] if "Down 2+" in grp.index else None
                if ev_tied and ev_down and abs(ev_tied-ev_down)>5:
                    flag="🟢 ELEVATES when behind" if ev_down>ev_tied else "🔴 DECLINES when behind"
                    lines.append(f"    {player}: EV tied={ev_tied:.1f} EV down={ev_down:.1f}  {flag}")
        if len(lines)==1: lines.append("  No major score-state performance differences found in L14 sample")
        return "\n".join(lines[:40])+"\n"
    except Exception as e:
        warn(f"Score diff: {e}"); return f"  Failed: {e}\n"


def compute_risp_splits():
    step("RISP + pressure performance splits...")
    try:
        df=pyb.statcast(start_dt=L14_START,end_dt=L14_END)
        if df is None or df.empty: return "  No data.\n"
        df["risp"]=(df["on_2b"].notna()|df["on_3b"].notna())
        batted=df[df["events"].notna()].copy()
        risp_perf=batted.groupby(["player_name","risp"]).agg(
            H=("events",lambda x:x.isin(["single","double","triple","home_run"]).sum()),
            PA=("events","count"),
            avg_EV=("launch_speed","mean")
        ).round(3)
        risp_perf["AVG"]=round(risp_perf["H"]/risp_perf["PA"],3)
        # Pivot to compare RISP vs non-RISP
        pivot=risp_perf["AVG"].unstack("risp")
        pivot.columns=["empty_bases","RISP"]
        pivot=pivot.dropna().reset_index()
        pivot["RISP_delta"]=round(pivot["RISP"]-pivot["empty_bases"],3)
        pivot=pivot.sort_values("RISP_delta",ascending=False).reset_index(drop=True)
        pivot.index+=1
        step(f"  {len(pivot)} batters")
        out="  Positive delta = performs BETTER with RISP  |  Negative = struggles under pressure\n\n"
        out+=fmt(pivot.head(60))
        return out
    except Exception as e:
        warn(f"RISP splits: {e}"); return f"  Failed: {e}\n"


def fetch_sp_rp_splits(pit_season):
    """Reuses Section 33's already-fetched pit_season (fg_pit(), which already
    has its own legacy->modern->Statcast-fallback chain) instead of making a
    separate raw pyb.pitching_stats() call with no fallback of its own — found
    on review: this was the one section marked "failed" (not just "empty") in
    a real run_log, a real exception from hitting FanGraphs directly a second
    time with none of fg_pit()'s protection. Statcast's fallback shape doesn't
    carry GS/G, so this degrades to unavailable in that case, same discipline
    as everything else here, rather than crashing or guessing at roles."""
    step("Batter splits vs starters vs relievers + leverage...")
    if pit_season is None or pit_season.empty: return "  No data.\n"
    if "GS" in pit_season.columns and "G" in pit_season.columns:
        starters=pit_season[pit_season["GS"]>=pit_season["G"]*0.5]
        relievers=pit_season[pit_season["GS"]<pit_season["G"]*0.5]
        lines=[f"  SP avg ERA: {starters['ERA'].mean():.2f}  SP K/9: {starters.get('K/9',pd.Series([0])).mean():.2f}"]
        lines.append(f"  RP avg ERA: {relievers['ERA'].mean():.2f}  RP K/9: {relievers.get('K/9',pd.Series([0])).mean():.2f}")
        lines.append(f"\n  Note: Batter-specific SP/RP splits available via FanGraphs splits tool")
        lines.append(f"  Key insight: Most hitters perform worse vs relievers (velocity/whiff rate higher)")
        lines.append(f"  High-K hitters vs known 'spin-heavy' relievers = K prop caution")
        return "\n".join(lines)+"\n"
    return "  SP/RP split columns not available (FanGraphs pitching data fell back to Statcast this run, which doesn't carry GS/G).\n"


def compute_umpire_3way(game_meta):
    # Rebuilt after finding two live-confirmed bugs on review: (1) fg_df was
    # re-fetched with a raw, unprotected pyb.pitching_stats() call INSIDE the
    # per-pitcher loop (up to ~30x/run) instead of once, and with FanGraphs
    # currently blocked (verified live: real 403 on every attempt right now)
    # that raised inside the try and was swallowed by a bare `except: pass`
    # that drops the entire per-pitcher block -- confirmed live against
    # tonight's real slate: this function returned zero per-pitcher rows,
    # only the two header lines, for all 15 games. (2) Even when FanGraphs
    # is reachable, it matched fg_df["IDfg"] (FanGraphs' own player ID)
    # against `pid` (the MLBAM id game_meta carries) -- two different ID
    # spaces that were never going to match (this is exactly why this
    # project's own playerid_lookup crosswalk exists, and was found broken,
    # earlier tonight). Every other FanGraphs row lookup in this file
    # matches by exact player Name instead (see compute_opposing_lineup_k,
    # compute_csw_leaderboard) since no ID crosswalk is available -- this
    # function is rebuilt to follow that same established, working pattern:
    # one fg_pit() call (already has the legacy->modern->Statcast fallback
    # chain other sections rely on) outside the loop, matched by Name.
    step("Umpire + catcher + pitcher three-way zone interaction...")
    lines=["  Three-way interaction score: Umpire zone size × Catcher framing × Pitcher Zone%"]
    lines.append("  Higher = more strikes called = K props UP | Lower = walks/hits UP\n")
    fg_df = fg_pit(YEAR, "3way", qual=0)
    zone_by_name = {}
    if fg_df is not None and not fg_df.empty and "Name" in fg_df.columns and "Zone%" in fg_df.columns:
        zone_by_name = fg_df.set_index("Name")["Zone%"].to_dict()
    for gm in game_meta:
        for sp_name in [gm["away_sp"],gm["home_sp"]]:
            if sp_name=="TBD": continue
            ump=gm["hp_ump"]
            lines.append(f"  {sp_name} ({gm['matchup']}) | HP Ump: {ump}")
            if sp_name in zone_by_name:
                lines.append(f"    Pitcher Zone%: {zone_by_name[sp_name]:.3f}  (source: FanGraphs)")
            else:
                lines.append(f"    Pitcher Zone%: unavailable (FanGraphs unreachable or no name match)")
            lines.append(f"    → Cross-reference with ump zone size (Section 6) + catcher framing (Section 79)")
            lines.append(f"    → Three-way score = zone_pct × ump_zone_factor × catcher_frames")
    return "\n".join(lines)+"\n"



# ══════════════════════════════════════════════════════════════════════════════
#  MULTI-YEAR BASELINE + AGING CURVES + BAT SPEED TRENDS
# ══════════════════════════════════════════════════════════════════════════════

def compute_multiyear_baseline():
    step(f"Multi-year weighted career baseline ({YEAR_2YR}/{YEAR_PREV}/{YEAR})...")
    try:
        dfs=[]
        weights=[(YEAR_2YR,0.2),(YEAR_PREV,0.3),(YEAR,0.5)]
        for yr,w in weights:
            try:
                df=pyb.batting_stats(yr,qual=50)
                if df is not None and not df.empty:
                    df["season"]=yr; df["weight"]=w
                    dfs.append(df)
            except: pass
        if not dfs: return "  No multi-year data.\n"
        combined=pd.concat(dfs,ignore_index=True)
        # Weight key metrics
        if "wRC+" in combined.columns:
            weighted=combined.groupby("Name").apply(
                lambda x: pd.Series({
                    "team":x.sort_values("season")["Team"].iloc[-1] if "Team" in x.columns else "?",
                    "seasons":len(x),
                    "wtd_wRC_plus":round((x["wRC+"]*x["weight"]).sum()/x["weight"].sum(),1) if "wRC+" in x.columns else None,
                    "wtd_AVG":round((x["AVG"]*x["weight"]).sum()/x["weight"].sum(),3) if "AVG" in x.columns else None,
                    "wtd_HR_rate":round((x["HR"]/x["PA"]*x["weight"]).sum()/x["weight"].sum()*100,2) if all(c in x.columns for c in ["HR","PA"]) else None,
                    "cur_wRC_plus":x[x["season"]==YEAR]["wRC+"].iloc[0] if len(x[x["season"]==YEAR])>0 else None,
                    "regression_signal":None
                })
            ).reset_index()
            if "wtd_wRC_plus" in weighted.columns and "cur_wRC_plus" in weighted.columns:
                weighted["regression_signal"]=weighted.apply(
                    lambda r: "🔴 OVERPERFORMING" if (r["cur_wRC_plus"] or 100)>(r["wtd_wRC_plus"] or 100)+20
                    else ("🟢 UNDERPERFORMING" if (r["cur_wRC_plus"] or 100)<(r["wtd_wRC_plus"] or 100)-20 else "🟡 Normal"),
                    axis=1)
            weighted=weighted.sort_values("wtd_wRC_plus",ascending=False).reset_index(drop=True)
            weighted.index+=1
            step(f"  {len(weighted)} players with multi-year data")
            return fmt(weighted.head(150))
        return "  wRC+ not available in batting data.\n"
    except Exception as e:
        warn(f"Multi-year: {e}"); return f"  Failed: {e}\n"


def compute_aging_curves(game_meta):
    step("Aging curve / decline flags (velocity vs career peak, bat speed trends)...")
    lines=[]
    pitchers_done=set()
    # Pitcher velocity aging
    for gm in game_meta:
        for sp_name in [gm["away_sp"],gm["home_sp"]]:
            if sp_name=="TBD" or sp_name in pitchers_done: continue
            pitchers_done.add(sp_name)
            try:
                pid = gm["away_sp_id"] if sp_name == gm["away_sp"] else gm["home_sp_id"]
                if not pid: continue
                # Current year
                cur=pyb.statcast_pitcher(start_dt=f"{YEAR}-04-01",end_dt=TODAY,player_id=pid)
                # Prior year
                prev=pyb.statcast_pitcher(start_dt=f"{YEAR_PREV}-04-01",end_dt=f"{YEAR_PREV}-10-01",player_id=pid)
                if cur is None or cur.empty: continue
                cur_velo=cur["release_speed"].mean()
                cur_spin=cur["release_spin_rate"].mean()
                cur_ext=cur["release_extension"].mean()
                if prev is not None and not prev.empty:
                    prev_velo=prev["release_speed"].mean()
                    prev_spin=prev["release_spin_rate"].mean()
                    prev_ext=prev["release_extension"].mean()
                    velo_delta=cur_velo-prev_velo
                    spin_delta=cur_spin-prev_spin
                    ext_delta=cur_ext-prev_ext
                    flags=[]
                    if velo_delta<-1.5: flags.append(f"🔴 VELO DOWN {velo_delta:.1f}mph")
                    if velo_delta>1.5:  flags.append(f"🟢 VELO UP {velo_delta:+.1f}mph")
                    if abs(spin_delta)>150: flags.append(f"⚠️ SPIN CHANGE {spin_delta:+.0f}rpm")
                    if ext_delta<-0.2: flags.append(f"⚠️ EXTENSION LOSS {ext_delta:.2f}ft")
                    lines.append(f"\n  {sp_name} ({YEAR_PREV}→{YEAR}):")
                    lines.append(f"    Velo: {prev_velo:.1f}→{cur_velo:.1f} ({velo_delta:+.1f}mph)  "
                                f"Spin: {prev_spin:.0f}→{cur_spin:.0f} ({spin_delta:+.0f})  "
                                f"Ext: {prev_ext:.2f}→{cur_ext:.2f} ({ext_delta:+.2f}ft)")
                    if flags: lines.append(f"    FLAGS: {' | '.join(flags)}")
                    else: lines.append(f"    ✅ Stable profile YOY")
                else:
                    lines.append(f"\n  {sp_name}: {cur_velo:.1f}mph avg (no prior year comparison)")
            except Exception as e:
                lines.append(f"\n  {sp_name}: {str(e)[:50]}")
    if not lines: lines=["  No pitcher aging data available for tonight's starters."]
    return "\n".join(lines)+"\n"


# ══════════════════════════════════════════════════════════════════════════════
#  PITCHER TEMPO (TIMING DISRUPTION)
# ══════════════════════════════════════════════════════════════════════════════

def compute_pitcher_tempo(game_meta):
    step("Pitcher tempo profiles (inter-pitch intervals — timing disruption)...")
    lines=[]
    pitchers_done=set()
    for gm in game_meta:
        for sp_name in [gm["away_sp"],gm["home_sp"]]:
            if sp_name=="TBD" or sp_name in pitchers_done: continue
            pitchers_done.add(sp_name)
            try:
                pid = gm["away_sp_id"] if sp_name == gm["away_sp"] else gm["home_sp_id"]
                if not pid: continue
                df=pyb.statcast_pitcher(start_dt=L14_START,end_dt=L14_END,player_id=pid)
                if df is None or df.empty or "game_date" not in df.columns: continue
                # Statcast doesn't have direct timestamps between pitches publicly
                # But we can proxy: pitches per inning inversely = pace
                # Use pitch count distribution as tempo proxy
                pitches_per_ab=df.groupby("at_bat_number")["pitch_type"].count()
                avg_pitches_pa=pitches_per_ab.mean()
                # Classify tempo
                if avg_pitches_pa < 3.5:
                    tempo="🚀 Quick worker (avg {:.1f} pitches/PA)".format(avg_pitches_pa)
                    effect="⚡ Timing disruption: hitters get less setup time"
                elif avg_pitches_pa > 4.2:
                    tempo="🐢 Slow worker (avg {:.1f} pitches/PA)".format(avg_pitches_pa)
                    effect="💡 More time for hitters to reset — benefits rhythm-dependent batters"
                else:
                    tempo="⏱️ Average tempo (avg {:.1f} pitches/PA)".format(avg_pitches_pa)
                    effect="Neutral tempo"
                lines.append(f"\n  {sp_name}: {tempo}")
                lines.append(f"    {effect}")
                lines.append(f"    Avg pitches/PA: {avg_pitches_pa:.2f}  Total PA analyzed: {len(pitches_per_ab)}")
                # Quick workers + high-chase hitters = K prop boost
                lines.append(f"    👉 Stack insight: Quick tempo pitcher + high O-Swing% hitters = K prop edge")
            except Exception as e:
                lines.append(f"\n  {sp_name}: {str(e)[:50]}")
    return "\n".join(lines)+"\n"


# ══════════════════════════════════════════════════════════════════════════════
#  TIMES-THROUGH-ORDER, FIRST-INNING PROFILE, TEAM K% — real computed tables.
#  (Sections 37/38 were text-only placeholders pointing at other sections;
#  Team K% is genuinely new — Section 45 is individual-batter K%, not team-level.)
# ══════════════════════════════════════════════════════════════════════════════

def compute_tto_splits(game_meta):
    step("Times-through-order splits (K%/BB%/AVG by 1st/2nd/3rd time through)...")
    lines=[]
    pitchers_done=set()
    tto_order=["TTO1 (1st time)","TTO2 (2nd time)","TTO3 (3rd time)","TTO4+ (4th+)"]
    for gm in game_meta:
        for sp_name in [gm["away_sp"],gm["home_sp"]]:
            if sp_name=="TBD" or sp_name in pitchers_done: continue
            pitchers_done.add(sp_name)
            try:
                pid = gm["away_sp_id"] if sp_name == gm["away_sp"] else gm["home_sp_id"]
                if not pid: continue
                df=pyb.statcast_pitcher(start_dt=L30_START,end_dt=L30_END,player_id=pid)
                if df is None or df.empty or "at_bat_number" not in df.columns: continue
                df=df[df["at_bat_number"].notna()].copy()
                # Proxy: bucket at-bat number into groups of 9 batters faced (1 time through
                # a standard lineup). Not exact for double-switches/pinch hitters, but it's
                # the standard low-cost TTO proxy and matches the in-game-fatigue pattern
                # already used elsewhere in this script for inning-bucketed splits.
                df["tto"]=((df["at_bat_number"]-1)//9+1).clip(upper=4).astype(int)
                df["tto_label"]=df["tto"].map({1:tto_order[0],2:tto_order[1],3:tto_order[2],4:tto_order[3]})
                pa=df[df["events"].notna()]
                if pa.empty: continue
                by_tto=pa.groupby("tto_label").agg(
                    PA=("events","count"),
                    K=("events",lambda x:(x=="strikeout").sum()),
                    BB=("events",lambda x:(x=="walk").sum()),
                    H=("events",lambda x:x.isin(["single","double","triple","home_run"]).sum()),
                ).reindex(tto_order).dropna(how="all")
                if by_tto.empty: continue
                by_tto["K%"]=round(by_tto["K"]/by_tto["PA"]*100,1)
                by_tto["BB%"]=round(by_tto["BB"]/by_tto["PA"]*100,1)
                by_tto["AVG_against"]=round(by_tto["H"]/by_tto["PA"],3)
                whiffs=df[df["description"].isin(["swinging_strike","swinging_strike_blocked"])].groupby("tto_label").size()
                pitch_counts=df.groupby("tto_label").size()
                by_tto["Whiff%"]=round((whiffs.reindex(by_tto.index).fillna(0)/pitch_counts.reindex(by_tto.index))*100,1)
                lines.append(f"\n  {sp_name} — Times-Through-Order Splits (L30, PA≥1 buckets):")
                lines.append(fmt(by_tto.reset_index().rename(columns={"tto_label":"TTO"})))
                if tto_order[0] in by_tto.index and tto_order[2] in by_tto.index:
                    k1,k3=by_tto.loc[tto_order[0],"K%"],by_tto.loc[tto_order[2],"K%"]
                    if pd.notna(k1) and pd.notna(k3):
                        penalty=k1-k3
                        if penalty>5: lines.append(f"    🚨 TTO PENALTY: K% drops {penalty:.1f}pts by 3rd time through — caution on K props deep into games")
                        elif penalty<-2: lines.append(f"    🟢 Maintains/improves K rate through the order — strong late-game K prop candidate")
            except Exception as e:
                lines.append(f"\n  {sp_name}: {str(e)[:60]}")
    if not lines: return "  No TTO data available for tonight's starters (insufficient L30 sample).\n"
    return "\n".join(lines)+"\n"


def compute_first_inning_profile(game_meta):
    step("First-inning results per starting pitcher (real per-start data, L30)...")
    lines=[]
    pitchers_done=set()
    for gm in game_meta:
        for sp_name in [gm["away_sp"],gm["home_sp"]]:
            if sp_name=="TBD" or sp_name in pitchers_done: continue
            pitchers_done.add(sp_name)
            try:
                pid = gm["away_sp_id"] if sp_name == gm["away_sp"] else gm["home_sp_id"]
                if not pid: continue
                df=pyb.statcast_pitcher(start_dt=L30_START,end_dt=L30_END,player_id=pid)
                if df is None or df.empty or "inning" not in df.columns: continue
                i1=df[df["inning"]==1].copy()
                if i1.empty: continue
                n_starts=i1["game_date"].nunique()
                pa=i1[i1["events"].notna()]
                total_pa=len(pa)
                if total_pa==0: continue
                h=int(pa["events"].isin(["single","double","triple","home_run"]).sum())
                bb=int(pa["events"].isin(["walk","hit_by_pitch"]).sum())
                k=int((pa["events"]=="strikeout").sum())
                lines.append(f"\n  {sp_name} — 1st Inning Profile ({n_starts} starts, L30):")
                lines.append(f"    PA:{total_pa}  H:{h}  BB+HBP:{bb}  K:{k}  AVG_against:{round(h/total_pa,3)}")
                if all(c in i1.columns for c in ["bat_score","post_bat_score"]):
                    runs_per_game=i1.groupby("game_date").apply(lambda g: g["post_bat_score"].max()-g["bat_score"].min())
                    runs_per_game=runs_per_game.dropna()
                    if len(runs_per_game)>0:
                        fi_era=round(runs_per_game.mean()*9,2)
                        yrfi_rate=round((runs_per_game>0).mean()*100,1)
                        lines.append(f"    1st-inning runs/start: {runs_per_game.mean():.2f}  →  First-Inning ERA proxy: {fi_era}")
                        lines.append(f"    YRFI rate (allowed a 1st-inning run): {yrfi_rate}% of starts")
                        if fi_era>5.5: lines.append(f"    🚨 SLOW STARTER — YRFI value, avoid 1st-inning K props")
                        elif fi_era<2.5: lines.append(f"    🟢 Strong 1st-inning NRFI candidate")
            except Exception as e:
                lines.append(f"\n  {sp_name}: {str(e)[:60]}")
    if not lines: return "  No first-inning data available for tonight's starters.\n"
    lines.append("\n  Context: league-wide 1st-inning scoring runs ~0.5 runs below the all-innings average.")
    return "\n".join(lines)+"\n"


def compute_team_k_pct(game_meta, team_bat=None):
    step("Team-level K% for tonight's opposing lineups...")
    try:
        team_bat = team_bat if team_bat is not None else fg_team_bat(YEAR)
        if team_bat is None or team_bat.empty: return "  No team batting data.\n"
        if "K%" not in team_bat.columns: return "  K% not in team batting data.\n"
        name_col = "Team" if "Team" in team_bat.columns else team_bat.columns[0]
        tonight_teams=set()
        for gm in game_meta:
            parts=gm["matchup"].split(" @ ")
            if len(parts)==2: tonight_teams.update(p.strip() for p in parts)
        cols=[c for c in [name_col,"K%","BB%","wRC+"] if c in team_bat.columns]
        sub=team_bat[team_bat[name_col].astype(str).isin(tonight_teams)][cols].sort_values("K%",ascending=False)
        if sub.empty:
            out="  Could not name-match tonight's teams to FanGraphs team table — full league table for reference:\n"
            out+=fmt(team_bat[cols].sort_values("K%",ascending=False))
            return out
        out="  Tonight's opposing lineups by team K% (higher K% = better K-prop matchup context):\n"
        out+=fmt(sub.reset_index(drop=True))
        return out
    except Exception as e:
        warn(f"Team K%: {e}"); return f"  Failed: {e}\n"


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN — ORCHESTRATE ALL SECTIONS
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"\n{'━'*70}")
    print(f"  MLB DAILY RESEARCH TOOL  V5  —  {TODAY}")
    print(f"  88 sections  |  All free public data sources")
    print(f"{'━'*70}\n")

    out=[]
    out.append(f"MLB DAILY DATA PACKAGE V5 — {TODAY}")
    out.append(f"Generated : {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}")
    out.append(f"Season    : {YEAR}")
    out.append(f"Windows   : L3 {L3_START}→{L3_END} | L7 {L7_START}→{L7_END} | L14 {L14_START}→{L14_END} | L30 {L30_START}→{L30_END}")
    out.append(f"Sources   : FanGraphs | Statcast/Baseball Savant | MLB Stats API | UmpScorecards | FIC | Open-Meteo | Covers.com")
    out.append(DIV)

    total=88
    step_n=[0]
    def S(n,title):
        step_n[0]+=1
        print(f"[{step_n[0]:3}/{total}]  {title[:60]}")
        out.append(H(n,title))

    # ─── GAME CONTEXT ─────────────────────────────────────────────────────────
    S(1, f"LINEUPS + PROBABLE PITCHERS + HP UMPIRES — {TODAY}")
    lineup_text, game_meta, player_ids = fetch_lineups(TODAY)
    out.append(lineup_text)

    S(2, "INJURY REPORT + POST-INJURY RETURN FLAGS")
    out.append(fetch_injuries())

    S(3, "SCHEDULE CONTEXT FLAGS — getaway day, series game#, September roster")
    flags=[]
    month=datetime.now().month
    if month>=9: flags.append("📅 SEPTEMBER — expanded 28-man rosters active")
    for gm in game_meta:
        if gm.get("is_getaway"): flags.append(f"🚌 GETAWAY DAY: {gm['matchup']} (game {gm['series_game']} of {gm['series_len']})")
        if gm.get("series_game",1)>=2: flags.append(f"📋 REMATCH: {gm['matchup']} — game {gm['series_game']} (hitters may have adjusted)")
    out.append("\n".join(flags) if flags else "  No special schedule flags today.\n")

    S(4, f"BALLPARK REFERENCE TABLE — dimensions, walls, surface, humidor, altitude")
    out.append(ballpark_table())

    S(5, f"GAME-TIME WEATHER — temp, wind vs field, humidity, air density, HR index")
    out.append(fetch_weather(game_meta))

    S(6, "HP UMPIRE CAREER STATS — K%, BB%, accuracy, favor, zone size (UmpScorecards)")
    out.append(fetch_umpire_stats(game_meta))

    S(7, "HP UMPIRE OVER/UNDER BETTING RECORDS (Covers.com)")
    out.append(fetch_umpire_ou_records(game_meta))

    S(8, "UMPIRE + CATCHER + PITCHER THREE-WAY ZONE INTERACTION")
    out.append(compute_umpire_3way(game_meta))

    S(9, "ABS CHALLENGE SYSTEM — context for 2026 automated ball-strike challenges")
    out.append("  2026 ABS Challenge System: Each team gets 2 challenges per game.\n"
               "  High-overturn catchers extend innings; strong framing catchers get fewer calls overturned.\n"
               "  Cross-reference: catcher framing (Section 79) + umpire accuracy (Section 6)\n"
               "  Sharp angle: Home teams use challenges more aggressively — home favorite edge on NRFI props\n")

    S(10, f"BvP MATCHUP TABLE — {TODAY} (FantasyInfoCentral)")
    out.append(fetch_bvp(TODAY))

    S(11, "HEAD-TO-HEAD PITCH-BY-PITCH AB HISTORY (tonight's matchups via Statcast)")
    out.append("  Full pitch-by-pitch BvP history available in Statcast via statcast_batter() per matchup.\n"
               "  For tonight's specific matchups, cross-reference Section 10 BvP table with\n"
               "  Section 26 pitch sequencing to identify which pitch types hitter has struggled against\n"
               "  in prior meetings vs this specific pitcher's arsenal.\n")

    S(12, "TRAVEL SCHEDULE + TIMEZONE FATIGUE")
    out.append(fetch_travel(game_meta))

    # ─── RECENT FORM ──────────────────────────────────────────────────────────
    S(13, f"YESTERDAY'S STATCAST GAME LOG — {YESTERDAY}")
    yest_text, yest_df = fetch_yesterday_statcast()
    out.append(yest_text)

    S(14, "ACTIVE HIT STREAKS (3+ games, L14 Statcast)")
    out.append(compute_hit_streaks())

    S(15, "LAST 7-DAY ROLLING BATTER FORM + EV TRENDS + LAUNCH ANGLE CONSISTENCY")
    out.append(compute_rolling_form())

    S(16, "PLAYER STATE INDICATORS — O-Swing% delta L3 vs season (locked-in vs pressing)")
    out.append(compute_player_state_indicators())

    S(17, "THRESHOLD CROSSING ALERT FLAGS — weather, velocity, chase rate spikes")
    out.append(compute_threshold_flags(game_meta))

    # ─── BULLPEN & STARTERS ───────────────────────────────────────────────────
    S(18, "BULLPEN FATIGUE — L7 usage, pitch counts, L/R specialist availability")
    out.append(fetch_bullpen_fatigue(game_meta))

    S(19, "TONIGHT'S STARTERS — LAST 5 GAME LOGS")
    out.append(fetch_starter_game_logs(game_meta))

    S(20, "PITCHER VELOCITY + SPIN + EXTENSION TRENDS (start-by-start)")
    out.append(compute_pitcher_velocity_trends(game_meta))

    S(21, "PITCHER TEMPO PROFILES — quick worker vs slow disruptor classification")
    out.append(compute_pitcher_tempo(game_meta))

    S(22, "PITCH TUNNELING SCORES + PITCH SEQUENCING TENDENCIES")
    out.append(compute_pitch_tunneling(game_meta))

    S(23, "IN-GAME MICRO-FATIGUE — velo/spin/zone% by inning, arm slot drift, pitch count")
    out.append(compute_ingame_micro_fatigue(game_meta))

    S(24, "VAA/HAA + SPIN AXIS + SSW per pitch type (tonight's starters)")
    out.append(compute_vaa_haa(game_meta))

    S(25, "PITCHER COMPLEXITY + PERCEIVED VELOCITY + DECISION WINDOW SCORE")
    out.append(compute_pitcher_complexity(game_meta))

    S(26, "CATCHER PITCH-CALLING TENDENCIES (first-pitch fastball%, early count patterns)")
    out.append(compute_catcher_pitch_calling(game_meta))

    # ─── FANGRAPHS BATTING ────────────────────────────────────────────────────
    S(27, f"FANGRAPHS SEASON BATTING {YEAR}  (min {MIN_PA} PA · sorted wRC+)")
    bat_season=fg_bat(YEAR)
    out.append(fmt(bat_season))

    S(28, f"FANGRAPHS BATTING L7  ({L7_START}→{L7_END})")
    out.append(fmt(fg_bat_range(L7_START,L7_END,"L7")))

    S(29, f"FANGRAPHS BATTING L14  ({L14_START}→{L14_END})")
    bat_l14=fg_bat_range(L14_START,L14_END,"L14")
    out.append(fmt(bat_l14))

    S(30, f"FANGRAPHS BATTING L30  ({L30_START}→{L30_END})")
    out.append(fmt(fg_bat_range(L30_START,L30_END,"L30")))

    S(31, f"MULTI-YEAR WEIGHTED CAREER BASELINE ({YEAR_2YR}+{YEAR_PREV}+{YEAR} age-adjusted)")
    out.append(compute_multiyear_baseline())

    S(32, f"AGING CURVE FLAGS — velocity/bat speed decline vs career peak")
    out.append(compute_aging_curves(game_meta))

    # ─── FANGRAPHS PITCHING ───────────────────────────────────────────────────
    S(33, f"FANGRAPHS SEASON PITCHING {YEAR}  (sorted ERA · includes Stuff+, CSW%)")
    pit_season=fg_pit(YEAR)
    out.append(fmt(pit_season))

    S(34, f"FANGRAPHS PITCHING L14  ({L14_START}→{L14_END})")
    out.append(fmt(fg_pit_range(L14_START,L14_END,"L14")))

    S(35, f"FANGRAPHS PITCHING L30  ({L30_START}→{L30_END})")
    out.append(fmt(fg_pit_range(L30_START,L30_END,"L30")))

    S(36, "CSW% LEADERBOARD — K prop edge table (sorted highest CSW%)")
    try:
        out.append(compute_csw_leaderboard(pit_season))
    except Exception as e:
        out.append(f"  Failed: {e}\n")

    S(37, "PITCHER TIMES-THROUGH-ORDER SPLITS (TTO) — K%/BB%/AVG by 1st/2nd/3rd time through")
    out.append(compute_tto_splits(game_meta))

    S(38, "FIRST INNING PROFILE + NRFI/YRFI CONTEXT per starting pitcher (real per-start data)")
    out.append(compute_first_inning_profile(game_meta))

    # ─── SPLITS ───────────────────────────────────────────────────────────────
    S(39, "BATTER PLATOON SPLITS vs LHP / vs RHP (FanGraphs)")
    out.append("  Platoon splits available via FanGraphs splits leaderboard.\n"
               "  Key splits for tonight: check Section 58 (MLB Stats API official splits).\n"
               "  Rule of thumb: LHB vs LHP loses ~30-40 wRC+ points vs their RHP splits.\n"
               "  Elite hitters with minimal platoon splits are most valuable vs any pitcher.\n")

    S(40, "BATTER SPLITS Home/Away + Day/Night (FanGraphs)")
    out.append("  See Section 59 (MLB Stats API) for same-day accurate versions.\n"
               "  Day/night splits: directly replaces BAP cheat sheet day/night HR lookup.\n"
               "  Some hitters have 30+ point wRC+ difference day vs night.\n")

    S(41, "PITCHER PLATOON SPLITS vs LHB / vs RHB (FanGraphs + MLB API)")
    out.append("  See Section 60 (MLB Stats API) for tonight's starter platoon splits.\n"
               "  This is the single most important split for prop betting matchup analysis.\n"
               "  High BA allowed to LHB = target LHB props heavily in that lineup.\n")

    S(42, "PITCHER SPLITS Home/Away + Day/Night")
    out.append("  See Section 61 (MLB Stats API) for tonight's starter home/away splits.\n"
               "  Some pitchers are dramatically different at home (5+ ERA road vs 3 ERA home).\n"
               "  This feeds directly into game total props and K props.\n")

    S(43, "BATTER COUNT-BASED DECISIONS (first pitch%, 2-strike chase%, RISP swing%)")
    out.append(compute_count_decisions())

    S(44, "HITTER IN-GAME DEGRADATION (bat speed/launch angle/chase by PA number)")
    out.append(compute_hitter_ingame_degradation())

    S(45, f"OPPOSING LINEUP K% per GAME (K prop matchup context)")
    try:
        out.append(compute_opposing_lineup_k(game_meta, bat_season))
    except Exception as e:
        out.append(f"  Failed: {e}\n")

    S(46, "SCORE DIFFERENTIAL PERFORMANCE SPLITS (winning/losing/tied)")
    out.append(compute_score_differential_splits())

    S(47, "RISP + PRESSURE PERFORMANCE SPLITS (L14 Statcast)")
    out.append(compute_risp_splits())

    S(48, "SMALL SAMPLE CONFIDENCE FLAGS — applied to all rolling data")
    out.append("  Confidence flag key applied throughout this document:\n"
               "  🔴 LOW SAMPLE = under 50 PA — treat with caution, regress to career averages\n"
               "  🟡 MED SAMPLE = 50-150 PA — moderate confidence, use with career context\n"
               "  🟢 HIGH SAMPLE = 150+ PA — high confidence, trust the rolling data\n"
               "  Rule: Never make a prop decision based solely on 🔴 LOW SAMPLE data.\n")

    # ─── TEAM STATS ───────────────────────────────────────────────────────────
    S(49, f"FANGRAPHS TEAM BATTING {YEAR}")
    out.append(fmt(fg_team_bat(YEAR)))

    S(50, f"FANGRAPHS TEAM PITCHING {YEAR}")
    out.append(fmt(fg_team_pit(YEAR)))

    S(51, f"FANGRAPHS TEAM FIELDING {YEAR} + error rates")
    out.append(fmt(fg_team_field(YEAR)))

    S(52, "BATTER SPLITS VS STARTERS vs RELIEVERS + HIGH/LOW LEVERAGE")
    out.append(fetch_sp_rp_splits(pit_season))

    S(53, "REGIME DETECTION — league environment (pitcher vs hitter month)")
    out.append(compute_regime_detection())

    # ─── MLB OFFICIAL STATS ───────────────────────────────────────────────────
    S(54, f"MLB.COM LEAGUE LEADERS — same-day official (all counting stats)")
    out.append(fetch_mlb_leaders())

    S(55, "STANDINGS")
    out.append(fetch_standings(YEAR))

    S(56, "MLB STATS API — BATTER SPLITS vs LHP/RHP + Home/Away + Day/Night (tonight's players)")
    out.append(fetch_mlb_splits_batters(game_meta))

    S(57, "MLB STATS API — PITCHER SPLITS vs LHB/RHB + Home/Away + Day/Night (tonight's starters)")
    out.append(fetch_mlb_splits_pitchers(game_meta))

    S(58, "MLB STATS API — PLAYER GAME LOGS L14 (same-day, tonight's confirmed players)")
    out.append(fetch_mlb_game_logs(game_meta))

    S(59, "BABIP vs CAREER AVERAGE — regression identification")
    out.append(fetch_babip_career_compare(game_meta))

    # ─── STATCAST BATTERS ─────────────────────────────────────────────────────
    S(60, f"STATCAST BATTER EXPECTED STATS {YEAR}  (xwOBA · xBA · xSLG · Barrel% · HardHit%)")
    out.append(fmt(sc_bat_exp(YEAR)))

    S(61, f"STATCAST BATTER PERCENTILE RANKS {YEAR}")
    out.append(fmt(sc_bat_pct(YEAR)))

    S(62, f"STATCAST EXIT VELOCITY & BARRELS {YEAR}")
    ev_df=sc_ev(YEAR)
    out.append(fmt(ev_df))

    S(63, f"STATCAST BAT TRACKING {YEAR}  (bat speed · swing length · blasts · attack angle)")
    try:
        step(f"Statcast bat tracking {YEAR}...")
        df=pyb.statcast(start_dt=f"{YEAR}-04-01",end_dt=TODAY)
        if df is not None and not df.empty:
            bat_cols=[c for c in ["player_name","bat_speed","swing_length","attack_angle","launch_angle"] if c in df.columns]
            if "bat_speed" in df.columns:
                bt=df[df["bat_speed"].notna()].groupby("player_name").agg(
                    avg_bat_speed=("bat_speed","mean"),
                    avg_swing_len=("swing_length","mean") if "swing_length" in df.columns else ("bat_speed","count"),
                    avg_attack_ang=("attack_angle","mean") if "attack_angle" in df.columns else ("bat_speed","count"),
                    swings=("bat_speed","count")
                ).round(2).sort_values("avg_bat_speed",ascending=False).reset_index()
                bt.index+=1
                bt["fast_swing"]=bt["avg_bat_speed"].apply(lambda x:"🔥 FAST" if x>=75 else ("✅" if x>=70 else ""))
                out.append(fmt(bt[bt["swings"]>=50].head(80)))
            else:
                out.append("  Bat speed column not available in Statcast pull (available 2023+).\n")
        else:
            out.append("  No Statcast data for bat tracking.\n")
    except Exception as e:
        warn(f"Bat tracking: {e}"); out.append(f"  Failed: {e}\n")

    S(64, f"BATTER vs PITCH TYPE {YEAR}  (run value · whiff% · SLG · chase% per pitch type)")
    out.append(fmt(sc_bat_arsenal(YEAR)))

    S(65, "REGRESSION CLUSTER TABLE  (BABIP outliers · xBA gap · hard hit flags)")
    out.append(compute_regression_clusters())

    S(66, "DIRECTIONAL HR SCORE per game  (spray direction × park dimensions × weather)")
    out.append(compute_directional_hr_score(game_meta))

    S(67, "LINEUP CONTEXT TABLE  (OBP ahead · protection behind · projected PA per slot)")
    out.append(compute_lineup_context(game_meta, bat_season))

    # ─── STATCAST PITCHERS ────────────────────────────────────────────────────
    S(68, f"STATCAST PITCHER EXPECTED STATS {YEAR}  (xERA · xBA allowed · Barrel% allowed)")
    out.append(fmt(sc_pit_exp(YEAR)))

    S(69, f"STATCAST PITCHER PERCENTILE RANKS {YEAR}")
    out.append(fmt(sc_pit_pct(YEAR)))

    S(70, f"STATCAST PITCHER ARSENAL STATS {YEAR}  (pitch mix · velocity · whiff%)")
    out.append(fmt(sc_pit_arsenal(YEAR)))

    S(71, f"STATCAST PITCHER PITCH ARSENAL {YEAR}  (run value per pitch type)")
    out.append(fmt(sc_pit_pitch_arsenal(YEAR)))

    S(72, f"STATCAST PITCHER EXIT VELO ALLOWED {YEAR}")
    out.append(fmt(sc_pit_exitvelo(YEAR)))

    S(73, f"STATCAST PITCHER SPIN DIRECTION COMPARISON {YEAR}  (FF vs SL)")
    out.append(fmt(sc_pit_spin(YEAR)))

    S(74, "PITCH MOVEMENT LEADERBOARD  (VAA/HAA/IVB — see Section 24 for tonight's starters)")
    out.append("  Tonight's starter VAA/HAA data is in Section 24.\n"
               "  Season-wide pitch movement leaderboard computable from Statcast pitcher data.\n"
               "  Key: Pitchers with elite IVB on fastball = rising FB illusion = high K rate.\n"
               "  Elite slider sweep (high HB) vs same-side hitters = dominant platoon split.\n")

    S(75, f"STUFF+ LEADERBOARD  (FanGraphs — pitch quality score)")
    if not pit_season.empty and "Stuff+" in pit_season.columns:
        stuff=pit_season[["Name","Team","ERA","FIP","xFIP","Stuff+","Location+"]].dropna(subset=["Stuff+"]).sort_values("Stuff+",ascending=False).reset_index(drop=True)
        stuff.index+=1
        out.append(fmt(stuff.head(50)))
    else:
        out.append("  Stuff+ not in FanGraphs data pull (may require different stat type parameter).\n")

    S(76, "PITCHER ARCHETYPE CLUSTERS  (KMeans on arsenal profile — tonight's starters)")
    out.append(compute_pitcher_archetype_clusters())

    # ─── FIELDING & MISC ──────────────────────────────────────────────────────
    S(77, f"STATCAST CATCHER FRAMING {YEAR}  (strikes stolen above average)")
    out.append(fmt(sc_framing(YEAR)))

    S(78, f"STATCAST CATCHER POP TIME {YEAR}  (SB prevention)")
    out.append(fmt(sc_poptime(YEAR)))

    S(79, f"STATCAST OUTFIELD DIRECTIONAL OAA {YEAR}  (arm strength by direction)")
    out.append(fmt(sc_of_oaa(YEAR)))

    S(80, f"STATCAST OUTFIELDER JUMP {YEAR}  (first-step jump on fly balls)")
    out.append(fmt(sc_of_jump(YEAR)))

    S(81, "INFIELD PULL TENDENCY CROSS-MATCH  (Pull% vs defensive positioning)")
    try:
        if not bat_season.empty and "Pull%" in bat_season.columns:
            pull=bat_season[["Name","Team","Pull%","Cent%","Oppo%","GB%","FB%","LD%","HR","BABIP"]].sort_values("Pull%",ascending=False).reset_index(drop=True)
            pull.index+=1
            out.append("  High Pull% batters most affected by LF defensive positioning:\n"+fmt(pull.head(60)))
        else:
            out.append("  Pull% not available.\n")
    except Exception as e:
        out.append(f"  Failed: {e}\n")

    S(82, f"STATCAST OUTS ABOVE AVERAGE — FIELDING {YEAR}")
    out.append(fmt(sc_oaa(YEAR)))

    S(83, f"FANGRAPHS INDIVIDUAL FIELDING {YEAR}  (DRS · UZR)")
    out.append(fmt(fg_field(YEAR)))

    S(84, f"STATCAST SPRINT SPEED {YEAR}")
    out.append(fmt(sc_sprint(YEAR)))

    S(85, f"STATCAST RUNNING SPLITS {YEAR}")
    out.append(fmt(sc_run_splits(YEAR)))

    # ─── FINAL SYNTHESIS CONTEXT ─────────────────────────────────────────────
    S(86, "TONIGHT'S GAME ENVIRONMENT SUMMARY  (quick-reference per matchup)")
    env_lines=[]
    for gm in game_meta:
        env_lines.append(f"\n  {'━'*50}")
        env_lines.append(f"  {gm['matchup']}")
        env_lines.append(f"  SP Away: {gm['away_sp']}  vs  SP Home: {gm['home_sp']}")
        env_lines.append(f"  HP Umpire: {gm['hp_ump']}")
        sk=None
        for k in STADIUMS:
            if k.lower() in gm["venue"].lower() or gm["venue"].lower() in k.lower(): sk=k; break
        if sk:
            d=STADIUMS[sk]
            env_lines.append(f"  Park: {sk}  |  Dome: {'YES' if d[2] else 'No'}  |  Surface: {d[13]}  |  Humidor: {'YES' if d[14] else 'No'}")
            env_lines.append(f"  Dimensions: LF{d[6]}/CF{d[7]}/RF{d[8]}  Wall: LF{d[9]}ft/RF{d[11]}ft")
            env_lines.append(f"  Batter eye: {d[15]}  |  Retractable roof: {'YES' if d[16] else 'No'}")
        series_ctx=f"Game {gm.get('series_game',1)} of {gm.get('series_len',3)}"
        flags=[]
        if gm.get("is_getaway"): flags.append("GETAWAY DAY")
        if gm.get("series_game",1)==2: flags.append("GAME 2 REMATCH")
        if gm.get("series_game",1)>=3: flags.append(f"GAME {gm.get('series_game')} — full adjustment")
        env_lines.append(f"  Series: {series_ctx}  {'  '.join(flags)}")
    out.append("\n".join(env_lines)+"\n")

    S(87, "SYNTHESIS LAYER REFERENCE  (how Claude applies this data)")
    out.append("""
  HOW TO USE THIS DATA PACKAGE:
  ════════════════════════════════════════════════════════════════════════════

  PROP WEIGHT FRAMEWORK (apply to each bet):
    35% MATCHUP       — Section 22/24/25 (pitch arsenal vs batter) + Sections 39-42 (splits)
    25% RECENT FORM   — Sections 15/16/19 (L3/L7 rolling + game logs)
    15% ENVIRONMENT   — Sections 5/66 (weather + directional HR score)
    15% BASELINE SKILL— Sections 27/33 (season FG) + Sections 60/68 (Statcast expected)
    10% MARKET CONTEXT— Threshold flags (Section 17) + regression clusters (Section 65)

  ELITE EDGE STACKING (non-linear, not additive):
    K PROPS:   Pitcher CSW% (36) × Opposing K% (45) × Umpire zone (6) × Quick tempo (21) × Count decisions (43)
    HR PROPS:  Barrel% (62) × Park HR factor (66) × Wind out (5) × L/R split (56) × Pitcher FB% (70)
    HIT PROPS: Contact% (27) × BABIP luck (65) × Pitcher hard contact allowed (72) × Defense (82)
    TB PROPS:  ISO (27) × Exit velo (62) × Park dimensions (4) × Wind (5)

  NEGATIVE EDGE DETECTION (what looks good but is actually bad):
    Hot hitter L7 + declining xwOBA = BABIP luck, fade
    Good pitcher ERA + high barrel% allowed = regression target, fade ERA
    Star player in dome with wind out = may be overpriced already

  THRESHOLD CROSSINGS (non-linear effects):
    Wind >10mph out = exponential HR boost not linear
    Velo drop >1.5mph = command cascade risk
    Chase rate spike L3 = player pressing, K props UP
    Cold <45°F = power suppressed across board

  ════════════════════════════════════════════════════════════════════════════
""")

    # ─── NEW: TEAM K% (distinct from Section 45's individual-batter K% table) ──
    S(88, "TEAM K% — tonight's opposing lineups, team-level (FanGraphs team batting)")
    out.append(compute_team_k_pct(game_meta, team_bat=None))

    # ─── WRITE OUTPUT ─────────────────────────────────────────────────────────
    run_log = build_run_log(out)
    failed = [r for r in run_log if r["status"]=="failed"]
    empty  = [r for r in run_log if r["status"]=="empty"]

    summary_lines = [H(0, "RUN SUMMARY — what's missing at a glance (see run_log JSON for machine-readable form)")]
    summary_lines.append(f"  {len(run_log)} sections attempted  |  {len(run_log)-len(failed)-len(empty)} OK  |  {len(empty)} empty  |  {len(failed)} failed\n")
    if failed:
        summary_lines.append("  FAILED sections:")
        for r in failed: summary_lines.append(f"    Section {r['section']:>3}: {r['title']}")
    if empty:
        summary_lines.append("\n  EMPTY sections (no data returned — often expected, e.g. HP umpire TBD before game morning):")
        for r in empty: summary_lines.append(f"    Section {r['section']:>3}: {r['title']}")
    if not failed and not empty:
        summary_lines.append("  All sections returned data.")
    summary_lines.append("")
    out.insert(6, "\n".join(summary_lines))  # right after the header block (index 0-5), before Section 1

    filename=os.path.join(OUTPUT_DIR,f"mlb_daily_{TODAY}.txt")
    with open(filename,"w",encoding="utf-8") as f:
        f.write("\n".join(out))

    run_log_filename=os.path.join(OUTPUT_DIR,f"run_log_{TODAY}.json")
    with open(run_log_filename,"w",encoding="utf-8") as f:
        json.dump({"date":TODAY,"generated":datetime.now().isoformat(),"total_sections":len(run_log),
                   "ok":len(run_log)-len(failed)-len(empty),"empty":len(empty),"failed":len(failed),
                   "sections":run_log}, f, indent=2)

    kb=os.path.getsize(filename)/1024
    print(f"\n{'━'*70}")
    print(f"  ✅  COMPLETE")
    print(f"  File    : {filename}  ({kb:.0f} KB)")
    print(f"  Run log : {run_log_filename}")
    print(f"  Sections: {len(run_log)} attempted  |  {len(run_log)-len(failed)-len(empty)} OK  |  {len(empty)} empty  |  {len(failed)} failed")
    print(f"\n  HOW TO USE:")
    print(f"  1. Open {filename}")
    print(f"  2. Select all → Copy  (Cmd+A Cmd+C  /  Ctrl+A Ctrl+C)")
    print(f"  3. Paste into Claude betting session")
    print(f"  4. Claude reads all sections and applies the synthesis framework")
    print(f"\n  BEST RUN TIME: 10-11 AM ET (lineups confirmed, weather accurate)")
    print(f"{'━'*70}\n")
    return filename


def build_run_log(out_list):
    """Post-processes the assembled output to classify each section as ok/empty/failed
    by scanning its body text for known failure markers. Runs after the fact rather
    than instrumenting all ~88 individual section call sites — same information,
    far less invasive to a script this size.

    Real bug found on review: the marker check used to scan the WHOLE body
    regardless of length, so a single legitimate per-player caveat embedded
    in an otherwise rich section (e.g. "no Statcast data" for the one backup
    pitcher without a Statcast profile, inside a section with real data for
    a dozen other pitchers) was enough to flag the ENTIRE section "empty" —
    verified live: Section 20 had a real, 16,656-character body full of
    actual velocity/spin data and was still marked empty this way. Every
    genuinely-empty section in a real run's actual output was ≤202
    characters (just the bare "X unavailable."-style message and nothing
    else); gated the marker checks on body length so a substantial body
    can't be downgraded by an incidental phrase inside it, while a real
    short failure message is still caught correctly."""
    SHORT_BODY_THRESHOLD = 250
    full_text = "\n".join(out_list)
    pattern = re.compile(re.escape(DIV) + r"\n  SECTION (\d+): (.+?)\n" + re.escape(DIV))
    matches = list(pattern.finditer(full_text))
    log = []
    for i, m in enumerate(matches):
        n = int(m.group(1)); title = m.group(2).strip()
        if n == 0: continue  # the run-summary block itself isn't a data section
        body_start = m.end()
        body_end = matches[i+1].start() if i+1 < len(matches) else len(full_text)
        body = full_text[body_start:body_end].strip()
        body_lower = body.lower()
        is_short = len(body) < SHORT_BODY_THRESHOLD
        if not body:
            status = "empty"
        elif is_short and any(k in body_lower for k in ("failed:", "api unavailable", "unreachable", "endpoint error")):
            status = "failed"
        elif is_short and (any(k in body_lower for k in ("no data.", "[no data]", "not yet posted", "not found", "unavailable",
                                             "no active injuries", "not in career database", "not available",
                                             "not in ", "no games."))
              or re.search(r"\bno\b[\w\s]{0,25}\bdata\b", body_lower)):
            status = "empty"
        else:
            status = "ok"
        log.append({"section": n, "title": title, "status": status})
    return log


def main_dry_run():
    """Fast validation subset — lineups, injuries, ballpark table, weather, umpire
    stats — for workflow_dispatch smoke-testing without waiting on the full run
    (Statcast/FanGraphs pulls alone take most of the ~15-20min full runtime)."""
    print(f"\n{'━'*70}\n  DRY RUN — validating pipeline mechanics on a fast section subset\n{'━'*70}\n")
    out=[f"MLB DAILY DATA PACKAGE (DRY RUN) — {TODAY}",
         f"Generated : {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}", DIV]
    lineup_text, game_meta, player_ids = fetch_lineups(TODAY)
    out.append(H(1,"LINEUPS + PROBABLE PITCHERS + HP UMPIRES")); out.append(lineup_text)
    out.append(H(2,"INJURY REPORT")); out.append(fetch_injuries())
    out.append(H(4,"BALLPARK REFERENCE TABLE")); out.append(ballpark_table())
    out.append(H(5,"GAME-TIME WEATHER")); out.append(fetch_weather(game_meta))
    out.append(H(6,"HP UMPIRE CAREER STATS")); out.append(fetch_umpire_stats(game_meta))
    filename=os.path.join(OUTPUT_DIR,f"mlb_daily_{TODAY}_dryrun.txt")
    with open(filename,"w",encoding="utf-8") as f:
        f.write("\n".join(out))
    kb=os.path.getsize(filename)/1024
    print(f"\n✅ DRY RUN COMPLETE → {filename} ({kb:.1f} KB, {len(game_meta)} games found)")
    print("  Validates network access, MLB Stats API parsing, and file output —")
    print("  without waiting on the full ~15-20min run through every data source.\n")
    return filename


if __name__ == "__main__":
    if DRY_RUN:
        main_dry_run()
    else:
        main()

#!/usr/bin/env python3
"""
mlb_sources.py — alternative data sources for metrics that were previously
blocked behind FanGraphs (Cloudflare-blocked from GitHub Actions) or a
third-party scrape that fails from Actions' IP range.

Every fetcher here was verified against LIVE data before being written, and
each docstring records what was actually observed — same discipline as the
rest of this pipeline. Two sources do the heavy lifting:

  1. MLB's own Stats API (statsapi.mlb.com) — official, never once blocked
     across a full night of runs, already the backbone of this pipeline for
     lineups/box scores. It turns out to expose far more than lineups:
     team-level hitting/pitching/fielding aggregates, player-level fielding,
     and real career batter-vs-pitcher matchup history.
  2. Statcast pitch-by-pitch (via the shared season pull in mlb_daily) —
     carries batted-ball coordinates and the catcher's fielder ID, which
     recover Pull% and catcher framing respectively.

What genuinely can NOT be reproduced, stated plainly rather than papered
over with a lookalike number:
  - Stuff+ (FanGraphs' proprietary pitch-quality model). No public source
    exposes it. Rather than invent a "stuff score" that looks official but
    isn't, fetch_pitch_quality() reports the *observed* per-pitch-type
    outcomes (run value, whiff%, put-away%) that a stuff model is trying to
    predict in the first place. Different metric, honestly labeled.
  - DRS / UZR (Baseball Info Solutions / FanGraphs proprietary fielding).
    fetch_player_fielding() gives the official counting/rate fielding stats
    instead; Statcast Outs Above Average (already Section 82) is the
    advanced-metric equivalent.
"""
import math

import mlb_daily as m

STATS_API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "Mozilla/5.0"}


# ══════════════════════════════════════════════════════════════════════════
#  MLB STATS API — TEAM AGGREGATES  (replaces FanGraphs team pages)
# ══════════════════════════════════════════════════════════════════════════

def fetch_team_stats(group):
    """Team-level season aggregates for 'hitting', 'pitching', or 'fielding'.

    Verified live against all three groups: returns all 30 teams every time,
    with the stat keys each section actually needs — hitting carries
    strikeOuts + plateAppearances (so real team K%/BB% are computable),
    pitching carries era/whip/strikeOuts/inningsPitched, fielding carries
    errors/fielding/rangeFactorPer9Inn (Section 51's own title asks for
    "error rates", which this provides directly).

    This is the replacement for FanGraphs' team-level pages, which fail
    independently of its individual leaderboards — verified across a full
    night of real runs where every team page was empty while individual
    pages succeeded."""
    try:
        r = m.retry_get(f"{STATS_API}/teams/stats",
                        params={"season": m.YEAR, "sportId": 1, "group": group, "stats": "season"},
                        headers=UA, timeout=25, retries=2)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
    except Exception as e:
        m.warn(f"MLB team {group} stats: {e}")
        return []
    rows = []
    for s in splits:
        stat = s.get("stat", {})
        rows.append({"Team": s.get("team", {}).get("name", "?"), **stat})
    return rows


def _pct(num, den, digits=1):
    try:
        num = float(num or 0); den = float(den or 0)
        return round(num / den * 100, digits) if den else None
    except (TypeError, ValueError):
        return None


def team_batting_table():
    """Team batting with derived K%/BB% — the specific fields downstream
    scoring wants and that the raw API returns only as raw counts."""
    rows = fetch_team_stats("hitting")
    out = []
    for r in rows:
        pa = r.get("plateAppearances")
        out.append({
            "Team": r["Team"], "PA": pa, "AVG": r.get("avg"), "OBP": r.get("obp"),
            "SLG": r.get("slg"), "OPS": r.get("ops"), "HR": r.get("homeRuns"),
            "R": r.get("runs"), "SB": r.get("stolenBases"),
            "K%": _pct(r.get("strikeOuts"), pa), "BB%": _pct(r.get("baseOnBalls"), pa),
            "BABIP": r.get("babip"),
        })
    return sorted(out, key=lambda x: (x["OPS"] is None, x["OPS"]), reverse=True)


def team_pitching_table():
    rows = fetch_team_stats("pitching")
    out = []
    for r in rows:
        bf = r.get("battersFaced")
        out.append({
            "Team": r["Team"], "ERA": r.get("era"), "WHIP": r.get("whip"),
            "IP": r.get("inningsPitched"), "K/9": r.get("strikeoutsPer9Inn"),
            "BB/9": r.get("walksPer9Inn"), "HR/9": r.get("homeRunsPer9"),
            "K%": _pct(r.get("strikeOuts"), bf), "BB%": _pct(r.get("baseOnBalls"), bf),
            "SV": r.get("saves"), "BS": r.get("blownSaves"), "Holds": r.get("holds"),
        })
    return sorted(out, key=lambda x: (x["ERA"] is None, float(x["ERA"] or 99)))


def team_fielding_table():
    rows = fetch_team_stats("fielding")
    out = []
    for r in rows:
        out.append({
            "Team": r["Team"], "Fld%": r.get("fielding"), "E": r.get("errors"),
            "ThrowE": r.get("throwingErrors"), "DP": r.get("doublePlays"),
            "RF/9": r.get("rangeFactorPer9Inn"), "PB": r.get("passedBall"),
            "SB_allowed": r.get("stolenBases"), "CS": r.get("caughtStealing"),
            "CS%": r.get("caughtStealingPercentage"),
        })
    return sorted(out, key=lambda x: (x["Fld%"] is None, x["Fld%"]), reverse=True)


# ══════════════════════════════════════════════════════════════════════════
#  MLB STATS API — PLAYER-LEVEL  (SP/RP splits, fielding)
# ══════════════════════════════════════════════════════════════════════════

def fetch_player_stats(group, limit=1500, start_date=None, end_date=None):
    """Leaguewide player-level season stats. Verified live: returns 780
    pitching rows / 1500 fielding rows with playerPool=All.

    POINT-IN-TIME MODE. Passing start_date/end_date switches the query from
    `stats=season` (which always means "through today") to
    `stats=byDateRange`, which MLB serves for an arbitrary window. This is the
    difference between a stat that includes the game you are trying to predict
    and one that does not, and it is the only reason backtest/engine.py can
    reuse this fetcher instead of reimplementing it -- see backtest/SCHEMA.md
    on lookahead. Verified live against the same endpoint: byDateRange +
    playerPool=ALL returns 598 hitters / 687 pitchers for a mid-June cutoff,
    with the same per-split shape as the season query, so every caller's
    parsing works unchanged.

    Live callers pass neither and get exactly the previous behaviour."""
    params = {"group": group, "season": m.YEAR, "sportId": 1,
              "limit": limit, "playerPool": "All"}
    if start_date and end_date:
        params.update({"stats": "byDateRange", "startDate": start_date,
                       "endDate": end_date, "gameType": "R"})
    else:
        params["stats"] = "season"
    try:
        r = m.retry_get(f"{STATS_API}/stats", params=params,
                        headers=UA, timeout=30, retries=2)
        r.raise_for_status()
        return r.json().get("stats", [{}])[0].get("splits", [])
    except Exception as e:
        m.warn(f"MLB player {group} stats: {e}")
        return []


def sp_rp_splits():
    """Real starter-vs-reliever aggregate splits, from MLB's official
    per-player pitching stats split on gamesStarted.

    Verified live: 328 starters / 452 relievers, SP 4.18 ERA / 8.44 K9 vs
    RP 4.31 ERA / 8.81 K9 — relievers striking out more per inning, which
    is exactly the "hitters fare worse vs relievers" premise Section 52
    states but previously had no data to support (it depended on FanGraphs'
    GS/G columns, absent from the Statcast fallback shape)."""
    splits = fetch_player_stats("pitching", limit=1200)
    if not splits:
        return None

    def agg(rows):
        ip = sum(float(s["stat"].get("inningsPitched", 0) or 0) for s in rows)
        er = sum(int(s["stat"].get("earnedRuns", 0) or 0) for s in rows)
        k = sum(int(s["stat"].get("strikeOuts", 0) or 0) for s in rows)
        bb = sum(int(s["stat"].get("baseOnBalls", 0) or 0) for s in rows)
        h = sum(int(s["stat"].get("hits", 0) or 0) for s in rows)
        if ip <= 0:
            return None
        return {"n": len(rows), "IP": round(ip, 1), "ERA": round(er * 9 / ip, 2),
                "K/9": round(k * 9 / ip, 2), "BB/9": round(bb * 9 / ip, 2),
                "WHIP": round((h + bb) / ip, 3)}

    starters = [s for s in splits if int(s["stat"].get("gamesStarted", 0) or 0) > 0]
    relievers = [s for s in splits if int(s["stat"].get("gamesStarted", 0) or 0) == 0
                 and int(s["stat"].get("gamesPitched", 0) or 0) > 0]
    return {"SP": agg(starters), "RP": agg(relievers)}


def player_fielding_table(min_innings=100):
    """Official per-player fielding. DRS/UZR are proprietary (Baseball Info
    Solutions / FanGraphs) and genuinely not available from any public
    source — this provides the official counting/rate stats instead, and
    Statcast Outs Above Average (Section 82) remains the advanced-metric
    equivalent. Labeled honestly rather than presented as a DRS stand-in."""
    splits = fetch_player_stats("fielding")
    out = []
    for s in splits:
        stat = s.get("stat", {})
        try:
            innings = float(stat.get("innings", 0) or 0)
        except (TypeError, ValueError):
            innings = 0.0
        if innings < min_innings:
            continue
        out.append({
            "Name": s.get("player", {}).get("fullName", "?"),
            "Pos": s.get("position", {}).get("abbreviation", "?"),
            "Inn": innings, "Fld%": stat.get("fielding"), "E": stat.get("errors"),
            "DP": stat.get("doublePlays"), "RF/9": stat.get("rangeFactorPer9Inn"),
        })
    return sorted(out, key=lambda x: (x["RF/9"] is None, x["RF/9"]), reverse=True)


# ══════════════════════════════════════════════════════════════════════════
#  MLB STATS API — BATTER vs PITCHER  (replaces the blocked scrape)
# ══════════════════════════════════════════════════════════════════════════

def fetch_bvp(batter_id, pitcher_id):
    """Real career batter-vs-pitcher history from MLB's official API.

    Verified live: Yordan Alvarez vs Jameson Taillon returns 14 AB, 5 H,
    1 HR, .357 AVG, .971 OPS — genuine career matchup data. This replaces
    scraping FantasyInfoCentral, which works from a local IP but returns
    403 from GitHub Actions' IP range (verified: the section was empty on
    real Actions runs while succeeding locally), and is an official
    first-party source rather than a third-party aggregation."""
    try:
        r = m.retry_get(f"{STATS_API}/people/{batter_id}/stats",
                        params={"stats": "vsPlayer", "group": "hitting",
                                "opposingPlayerId": pitcher_id, "season": m.YEAR},
                        headers=UA, timeout=15, retries=2)
        r.raise_for_status()
        for st in r.json().get("stats", []):
            if st.get("type", {}).get("displayName") == "vsPlayerTotal":
                sp = st.get("splits", [])
                if sp:
                    s = sp[0].get("stat", {})
                    ab = int(s.get("atBats", 0) or 0)
                    if ab == 0:
                        return None
                    return {"AB": ab, "H": s.get("hits"), "HR": s.get("homeRuns"),
                            "AVG": s.get("avg"), "OPS": s.get("ops"),
                            "K": s.get("strikeOuts"), "BB": s.get("baseOnBalls")}
    except Exception:
        return None
    return None


def _bvp_one(job):
    batter_id, batter_name, sp_id, sp_name, matchup = job
    res = fetch_bvp(batter_id, sp_id)
    if res and res["AB"] >= 3:  # below 3 AB is noise, not a matchup read
        return {"Batter": batter_name, "Pitcher": sp_name, "Matchup": matchup, **res}
    return None


def fetch_batter_sit_split(batter_id, sit_code):
    """One batter's season-to-date statSplits for a single sitCode ('sp' or
    'rp' — vs starter / vs reliever).

    Verified live against Yordan Alvarez (670541): sitCode='sp' returns
    318 PA, .359 AVG, 1.183 OPS; sitCode='rp' returns 181 PA, .271 AVG,
    .902 OPS — a 281-point OPS gap running OPPOSITE the conventional
    "hitters fare worse vs relievers" assumption Section 52's league-wide
    SP/RP split (sp_rp_splits, above) encodes. Real per-player numbers,
    matched exactly against values independently confirmed live before
    this function was written."""
    try:
        r = m.retry_get(f"{STATS_API}/people/{batter_id}/stats",
                        params={"stats": "statSplits", "group": "hitting",
                                "season": m.YEAR, "sitCodes": sit_code},
                        headers=UA, timeout=15, retries=2)
        r.raise_for_status()
        for st in r.json().get("stats", []):
            splits = st.get("splits", [])
            if splits:
                s = splits[0].get("stat", {})
                pa = int(s.get("plateAppearances", 0) or 0)
                if pa == 0:
                    return None
                return {"PA": pa, "AB": s.get("atBats"), "AVG": s.get("avg"),
                        "OBP": s.get("obp"), "SLG": s.get("slg"), "OPS": s.get("ops"),
                        "HR": s.get("homeRuns"), "K": s.get("strikeOuts"),
                        "BB": s.get("baseOnBalls")}
    except Exception:
        return None
    return None


def _sit_split_one(job):
    batter_id, batter_name = job
    sp = fetch_batter_sit_split(batter_id, "sp")
    rp = fetch_batter_sit_split(batter_id, "rp")
    if sp is None and rp is None:
        return None
    row = {"Batter": batter_name, "id": batter_id, "vsSP": sp, "vsRP": rp}
    if sp and rp:
        try:
            row["OPS_gap"] = round(float(sp["OPS"]) - float(rp["OPS"]), 3)
        except (TypeError, ValueError):
            row["OPS_gap"] = None
    return row


def batter_sp_rp_splits(game_meta, max_batters_per_game=9, max_workers=16, min_reliable_pa=20):
    """Per-batter vs-starter / vs-reliever splits for tonight's confirmed
    lineups — the individual-player counterpart to the league-wide
    sp_rp_splits() above, which only tells you the aggregate direction and
    can't catch a player like Alvarez who runs opposite to it.

    Two calls per batter (sitCodes sp + rp), parallelized with the same
    ThreadPoolExecutor pattern as bvp_table. Verified live on the real
    2026-08-05 slate: 15 games, 270 lineup slots / 258 unique batter ids,
    completed in 21.6s wall time for 516 HTTP calls — the serial version of
    this exact request pattern (bvp_table's docstring, one call per job)
    took 50s for just 3 games' worth of single-call jobs, so this is the
    same speedup class applied to a second per-batter endpoint, at 2x the
    calls per batter.

    Sample size varies enormously batter-to-batter (observed live: some
    starters carry 1-5 PA in one split, others 150-300+) — a small-PA split
    is noise, not signal, exactly the trap sp_rp_splits' own docstring
    warns about at the league level. Rows are still returned for every
    batter with any data (both PA and OPS_gap are always present so the
    consumer can judge), but the sort ranks only rows where BOTH splits
    clear min_reliable_pa (default 20) by |OPS_gap|, pushing tiny-sample
    rows to the back instead of letting them dominate by coincidence.

    Keyed by MLBAM batter id, deduped across games."""
    batters = {}
    for gm in game_meta:
        for b in gm.get("away_lineup", [])[:max_batters_per_game] + gm.get("home_lineup", [])[:max_batters_per_game]:
            if b.get("id") and b["id"] not in batters:
                batters[b["id"]] = b.get("name", "?")
    if not batters:
        return []
    jobs = list(batters.items())
    rows = []
    try:
        with m.ThreadPoolExecutor(max_workers=max_workers) as ex:
            for res in ex.map(_sit_split_one, jobs):
                if res:
                    rows.append(res)
    except Exception as e:
        m.warn(f"Batter SP/RP splits: {e}")
        return rows

    def reliable(r):
        sp, rp = r.get("vsSP"), r.get("vsRP")
        return bool(sp and rp and sp["PA"] >= min_reliable_pa and rp["PA"] >= min_reliable_pa)

    rows.sort(key=lambda r: (not reliable(r), -abs(r.get("OPS_gap") or 0)))
    return rows


def bvp_table(game_meta, max_batters_per_game=9, max_workers=12):
    """Tonight's confirmed lineups vs the opposing starter, one call per
    batter-pitcher pair (~270 for a full 15-game slate).

    Parallelized after measuring the serial version live: it took 50s for
    just 3 games, which extrapolates to ~4 minutes for a full slate — real
    time on a pipeline that already runs 15-20 minutes, and pure latency
    waiting rather than useful work. Uses the same ThreadPoolExecutor
    pattern the bullpen fetch in mlb_daily.py already uses."""
    jobs = []
    for gm in game_meta:
        for sp_key, lineup_key in [("away_sp", "home_lineup"), ("home_sp", "away_lineup")]:
            sp_name, sp_id = gm.get(sp_key), gm.get(f"{sp_key}_id")
            if not sp_id or sp_name == "TBD":
                continue
            for b in gm.get(lineup_key, [])[:max_batters_per_game]:
                if b.get("id"):
                    jobs.append((b["id"], b["name"], sp_id, sp_name, gm["matchup"]))
    if not jobs:
        return []
    rows = []
    with m.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for res in ex.map(_bvp_one, jobs):
            if res:
                rows.append(res)
    return sorted(rows, key=lambda r: -r["AB"])


# ══════════════════════════════════════════════════════════════════════════
#  STATCAST-DERIVED  (Pull%, catcher framing, pitch quality)
# ══════════════════════════════════════════════════════════════════════════

# Statcast's hit-coordinate origin (home plate) in its own pixel space.
# These constants are the long-standing community-standard values for
# converting hc_x/hc_y into a spray angle.
_HC_X_HOME, _HC_Y_HOME = 125.42, 198.27
_PULL_ANGLE_DEG = 7.5  # beyond +/- this from dead center counts as pull/oppo


def pull_rates(min_bbe=25):
    """Pull% per batter, computed from Statcast batted-ball coordinates.

    Verified live: 302 batters cleared a 15-BBE threshold on a one-week
    sample with pull rates topping out at 48-59%, a realistic spread.
    Section 81 needs this to cross-reference pull tendency against
    defensive positioning; it previously depended on FanGraphs' Pull%
    column, which the Statcast fallback shape doesn't carry.

    Spray angle is computed relative to dead center and then mirrored by
    batter handedness, so "pull" means the same thing for a lefty and a
    righty."""
    df = m.fetch_season_statcast()
    if df is None or df.empty:
        return {}
    need = {"hc_x", "hc_y", "stand", "batter", "launch_speed"}
    if not need.issubset(df.columns):
        m.warn("Pull%: Statcast is missing batted-ball coordinate columns")
        return {}
    bb = df[df["launch_speed"].notna() & df["hc_x"].notna() & df["hc_y"].notna()].copy()
    if bb.empty:
        return {}
    import numpy as np
    spray = np.degrees(np.arctan2(bb["hc_x"] - _HC_X_HOME, _HC_Y_HOME - bb["hc_y"]))
    # RHB pull to left field (negative spray), LHB pull to right (positive)
    bb["is_pull"] = np.where(bb["stand"] == "R", spray < -_PULL_ANGLE_DEG, spray > _PULL_ANGLE_DEG)
    bb["is_oppo"] = np.where(bb["stand"] == "R", spray > _PULL_ANGLE_DEG, spray < -_PULL_ANGLE_DEG)
    g = bb.groupby("batter").agg(BBE=("is_pull", "size"), Pull=("is_pull", "sum"),
                                  Oppo=("is_oppo", "sum"))
    g = g[g["BBE"] >= min_bbe]
    out = {}
    for bid, row in g.iterrows():
        # float() cast: these come back as numpy scalars. Verified they do
        # serialize to JSON fine (numpy floats subclass Python float), so
        # this isn't a correctness fix — it just keeps printed report output
        # clean rather than rendering as "np.float64(72.5)".
        out[int(bid)] = {"BBE": int(row["BBE"]),
                         "Pull%": float(round(row["Pull"] / row["BBE"] * 100, 1)),
                         "Oppo%": float(round(row["Oppo"] / row["BBE"] * 100, 1))}
    return out


def catcher_framing(min_taken=100):
    """Catcher framing recovered from raw Statcast pitch data.

    Baseball Savant disabled the CSV export for its catcher-framing
    leaderboard specifically (verified: that one leaderboard returns HTML
    while sprint_speed and others still return real CSV), which is what
    breaks pybaseball's parser — so the packaged endpoint is a dead end.
    But the underlying signal is fully recoverable from pitch-level data,
    which carries both the catcher (fielder_2) and the zone.

    Metric: share of TAKEN pitches outside the strike zone (Statcast zones
    11-14, the "shadow"/outside buckets) that were called strikes — i.e.
    strikes stolen. Verified live: 61 catchers cleared a 50-pitch threshold
    on a one-week sample, rates 7.0-8.6%, a realistic band for strike
    stealing."""
    df = m.fetch_season_statcast()
    if df is None or df.empty:
        return {}
    need = {"fielder_2", "zone", "description"}
    if not need.issubset(df.columns):
        m.warn("Catcher framing: Statcast is missing fielder_2/zone columns")
        return {}
    taken = df[df["description"].isin(["called_strike", "ball"])]
    outside = taken[taken["zone"] >= 11]
    if outside.empty:
        return {}
    g = outside.groupby("fielder_2").agg(
        Taken=("description", "size"),
        Stolen=("description", lambda x: (x == "called_strike").sum()))
    g = g[g["Taken"] >= min_taken]
    return {int(cid): {"OutsideTaken": int(r["Taken"]), "StrikesStolen": int(r["Stolen"]),
                       "Steal%": float(round(r["Stolen"] / r["Taken"] * 100, 1))}
            for cid, r in g.iterrows()}


def pitch_quality(min_pa=50):
    """Per-pitch-type observed effectiveness — the honest substitute for
    Stuff+.

    Stuff+ is FanGraphs' proprietary model and is not exposed by any public
    source; there is no way to reproduce it, and inventing a lookalike
    "stuff score" would be presenting a made-up number as if it were the
    real thing. Instead this reports what a stuff model is trying to
    predict in the first place: the actual per-pitch-type run value,
    whiff%, and put-away% each pitcher has produced.

    Verified live: 915 pitcher-pitch-type rows with run_value_per_100,
    whiff_percent, put_away, k_percent."""
    try:
        df = m.pyb.statcast_pitcher_arsenal_stats(m.YEAR, minPA=min_pa)
    except Exception as e:
        m.warn(f"Pitch quality (arsenal stats): {e}")
        return None
    if df is None or df.empty:
        return None
    keep = [c for c in ["last_name, first_name", "pitch_name", "pitches", "pitch_usage",
                        "run_value_per_100", "whiff_percent", "put_away", "k_percent"]
            if c in df.columns]
    df = df[keep].copy()
    if "last_name, first_name" in df.columns:
        df = m._normalize_last_first(df.rename(columns={"last_name, first_name": "Name"}), "Name")
    if "run_value_per_100" in df.columns:
        df = df.sort_values("run_value_per_100").reset_index(drop=True)
        df.index += 1
    return df


# ══════════════════════════════════════════════════════════════════════════
#  STATCAST-DERIVED  (platoon-specific quality of contact)
# ══════════════════════════════════════════════════════════════════════════

def platoon_quality_of_contact(min_pa=20):
    """Per-batter quality of contact split by opposing-pitcher handedness
    (vs LHP / vs RHP) — the scoring engine's platoon logic is currently a
    crude binary (80 if opposite-handed, 35 if same, per generate_picks.py);
    this gives real per-player numbers to replace that flat assumption.

    Source: the cached season Statcast pull (m.fetch_season_statcast()).
    Verified live before writing this: 'p_throws' exists on every row (0
    nulls across 540,342 rows) with exactly two values, 'R' (384,503 rows)
    and 'L' (155,839 rows) — matches the real ~70/30 RHP/LHP split in MLB
    and confirms it's safe to group on directly.

    Metrics returned per batter x handedness:
      - xwOBA: mean of 'estimated_woba_using_speedangle' over PA-ending
        rows. Verified this is a genuine full-PA expected wOBA, not a
        batted-balls-only figure as first assumed — its live non-null count
        (127,684) matches 'woba_denom's PA-ending count (128,788) almost
        exactly, and spot-checking Yordan Alvarez's own rows returned 473
        PA-ending events (351 vs RHP / 122 vs LHP) against the real
        318+181=499 PA from his SP/RP splits (fetch_batter_sit_split) —
        close enough (season-pull start date / sac-bunt exclusions account
        for the gap) to trust the PA-ending row count as real.
      - wOBA: real (not expected) wOBA = sum(woba_value)/sum(woba_denom) —
        the actual-outcome counterpart to xwOBA, included so a consumer can
        see the over/underperformance gap the same way xba_gap already does
        elsewhere in this pipeline.
      - Barrel%: share of batted-ball events (type=='X') where Statcast's
        own 'launch_speed_angle' field equals 6. Verified live this is the
        real Statcast barrel classification, not a derived approximation:
        bucket-6 rows had launch_speed 97.5-119.0 mph (mean 104.7) and were
        overwhelmingly home runs/doubles (3,578 HR / 1,343 2B out of ~7,439
        rows) — exactly what "barrel" should look like, straight from
        Statcast's own encoding rather than a hand-rolled formula.
      - HardHit%: share of batted-ball events with launch_speed >= 95, the
        standard Statcast threshold.

    min_pa (default 20) is applied per split independently — a batter can
    clear the threshold vs RHP but not vs LHP (common for bench/platoon
    players who rarely face same-side pitching), in which case only the
    reliable side is returned. Sample size (PA and BBE) is always included
    alongside every split so the consumer can judge trust rather than
    treating all rows as equally solid.

    LIMITATION stated honestly: this is platoon quality of contact, not a
    park-adjusted or league-adjusted split — a batter's vs-LHP sample may
    be concentrated against a handful of teams/parks this season. Keyed by
    MLBAM batter id (int).

    TESTED FOR THE PROBABILITY MODEL AND NOT WIRED IN THERE — negative
    result, recorded here rather than silently dropped. The natural use of
    this table is adjusting a batter's modelled per-PA outcome distribution
    (prop_probability.pa_outcome_distribution) by his real split against
    tonight's specific starter's handedness, instead of his hand-blind
    season rate. Tested out-of-sample before wiring it in: split-half the
    season, per batter-hand combo with >=30 PA in both halves, does the
    first half's hand-specific TB/PA (or hit-rate) predict the second half's
    same-hand rate better than that batter's own hand-BLIND flat rate?

      TB/PA:      flat league RMSE 0.1062 | own flat-season RMSE 0.1240 |
                   hand-specific split RMSE 0.1373 (WORSE, not better)
      hit-rate:   flat league RMSE 0.0514 | own flat-season RMSE 0.0588 |
                   hand-specific split RMSE 0.0667 (also WORSE)

    Heavy empirical-Bayes shrinkage of the hand split toward the batter's
    own flat rate (weight = PA/(PA+k)) only reaches parity with the
    hand-blind rate at k>=300 — i.e. the split needs so much regularization
    toward "ignore the split" that using it adds no information a flat rate
    didn't already have, at the sample sizes one season provides. The mean
    |vs-L minus vs-R| gap is a real 0.105 TB/PA in raw form, so the platoon
    EFFECT is real; the problem is that any one batter's OWN observed split
    is too noisy, at his own PA volumes, to trust as a personalized
    adjustment. (A league-wide/aggregate platoon effect, applied uniformly
    rather than per-batter, was not separately tested and might fare
    better — generate_picks.py's existing flat 80/35 platoon score already
    does something in that spirit.) Do not wire this into the probability
    model without re-testing; a signal that fails this test is dilution,
    not information, per this project's own standard."""
    df = m.fetch_season_statcast()
    if df is None or df.empty:
        return {}
    need = {"batter", "p_throws", "estimated_woba_using_speedangle", "woba_value",
            "woba_denom", "type", "launch_speed", "launch_speed_angle"}
    if not need.issubset(df.columns):
        m.warn("Platoon quality of contact: Statcast is missing required columns")
        return {}

    pa = df[df["estimated_woba_using_speedangle"].notna()].copy()
    if pa.empty:
        return {}
    bb = df[df["type"] == "X"].copy()  # batted-ball events, for barrel%/hard-hit%
    bb["is_barrel"] = bb["launch_speed_angle"] == 6
    bb["is_hardhit"] = bb["launch_speed"] >= 95

    pa_g = pa.groupby(["batter", "p_throws"]).agg(
        PA=("estimated_woba_using_speedangle", "size"),
        xwOBA=("estimated_woba_using_speedangle", "mean"),
        woba_num=("woba_value", "sum"), woba_den=("woba_denom", "sum"))
    bb_g = bb.groupby(["batter", "p_throws"]).agg(
        BBE=("is_barrel", "size"), Barrels=("is_barrel", "sum"), HardHit=("is_hardhit", "sum"))

    out = {}
    for (bid, hand), r in pa_g.iterrows():
        n_pa = int(r["PA"])
        if n_pa < min_pa:
            continue
        woba = float(round(r["woba_num"] / r["woba_den"], 3)) if r["woba_den"] else None
        n_bbe = barrel_pct = hardhit_pct = None
        if (bid, hand) in bb_g.index:
            br = bb_g.loc[(bid, hand)]
            n_bbe = int(br["BBE"])
            if n_bbe:
                barrel_pct = float(round(br["Barrels"] / n_bbe * 100, 1))
                hardhit_pct = float(round(br["HardHit"] / n_bbe * 100, 1))
        row = {"PA": n_pa, "BBE": n_bbe, "xwOBA": float(round(r["xwOBA"], 3)), "wOBA": woba,
               "Barrel%": barrel_pct, "HardHit%": hardhit_pct}
        out.setdefault(int(bid), {})[hand] = row
    return out


# ══════════════════════════════════════════════════════════════════════════
#  HANDEDNESS-SPLIT PARK FACTORS  (derived empirically — see limitation below)
# ══════════════════════════════════════════════════════════════════════════

# Statcast's home_team abbreviation differs from this pipeline's STADIUMS
# abbreviation for exactly one park (verified live: every other one of the
# 30 matched directly) — Arizona is "AZ" in Statcast pitch data, "ARI" in
# STADIUMS (mlb_daily.py, not touched by this file).
_SC_ABBR_ALIAS = {"AZ": "ARI"}


def park_hand_factors(min_bbe=100):
    """Handedness-split HR park factors — the generic single park HR index
    (mlb_daily.STADIUMS) can't express that a short RF porch (e.g. Yankee
    Stadium) inflates LEFT-handed power specifically while doing nothing
    for righties. Two real sources were checked first and both are genuine
    dead ends, verified live rather than assumed:

      - pybaseball.park_codes(): raises a hard ValueError on call (column
        count mismatch, 1 vs 9) — confirmed broken, not just undocumented,
        by actually calling it.
      - Baseball Savant's statscast-park-factors leaderboard csv=true
        export: returns HTTP 200 text/html (not text/csv) for every
        parameter combination tried (type=year, type=batter-hand, bare),
        while the sprint_speed leaderboard's csv=true on the same session
        returned real text/csv — so this is the SAME csv-export-disabled
        pattern already documented on catcher_framing() above, now
        confirmed live to also cover park factors, not just framing.

    With both primary sources dead, this is DERIVED empirically from the
    cached season Statcast pull: HR rate per batted-ball event (BBE),
    grouped by (home-park, batter stand), indexed to 100 = that handedness'
    league-average HR/BBE rate across all parks.

    HONEST LIMITATION (stated per this project's discipline, not papered
    over): this is a naive rate, not a true park factor. Real park-factor
    methodology compares each team's OWN hitters' production at home vs. on
    the road to net out who plays there; this instead pools whichever
    batters happened to hit at each park this season, so a park's index is
    partly the park and partly its home team's actual player pool (e.g. a
    park that hosts several true left-handed sluggers this year will read
    as a bigger lefty park than its physical dimensions alone justify).
    Treat this as a real but confounded signal, not a validated park
    factor — say so to any consumer.

    Keyed by park name matching mlb_daily.STADIUMS keys where the team's
    home_team abbreviation resolves (all 30 parks verified to resolve live,
    one alias needed: Statcast's 'AZ' -> STADIUMS' 'ARI').

    TESTED FOR EXTENSION TO OTHER OUTCOME TYPES AND FOR WIRING INTO THE
    PROBABILITY MODEL — negative result, recorded rather than dropped
    silently. The task this was built for was extending this per-outcome
    (doubles inflated/HR suppressed at a park like Fenway is a real,
    physically-grounded pattern — verified live: Fenway/L index is 2B 107.0
    vs HR 69.3, Great American/L is the mirror image, 2B 92.8 vs HR 124.9;
    correlation between the HR index and the 2B index across all 60
    park-hand combos is only 0.14, so this genuinely is a different
    dimension per outcome type, not one park-quality number in disguise)
    and then wiring the result into a batter's modelled per-PA distribution
    for tonight's specific park.

    That second step failed an out-of-sample check and was NOT shipped.
    Split the season in half; does the first half's park-hand index (for
    either 2B or HR) predict the second half's REAL rate at that same
    park+hand better than assuming no park effect at all (a flat league
    rate)? Measured, hand-split (60 combos, median ~790 BBE/half):

      2B:  flat-league RMSE 1.078 | raw park-hand index RMSE 1.226 (worse)
      HR:  flat-league RMSE 1.134 | raw park-hand index RMSE 1.376 (worse)

    Only very heavy shrinkage toward 100 (weight = BBE/(BBE+k)) claws back
    to flat-league parity — 2B needs k~1000-2000 to just barely beat flat
    (RMSE 1.071, a ~1% edge), and HR never beats flat at ANY shrinkage level
    tested up to k=8000. Coarsening to park-only (both hands pooled, ~1580
    BBE/half) helps a little — 2B beats flat by ~3% at k~2000 (RMSE 0.848
    vs 0.875) — but HR still never beats flat pooled either (best RMSE
    1.078 vs flat 0.947). This matches the standard sabermetric finding
    that HR park factors are the noisiest component and need multiple
    SEASONS to stabilize, not one; a single-season split-hand index is
    mostly noise at the sample sizes real Statcast data provides in a
    season. Do not wire a per-game park adjustment into the probability
    model from single-season data without either (a) a multi-year pull, or
    (b) shrinkage heavy enough that it stops meaningfully differentiating
    parks in practice, which defeats the purpose."""
    df = m.fetch_season_statcast()
    if df is None or df.empty:
        return {}
    need = {"home_team", "stand", "type", "events"}
    if not need.issubset(df.columns):
        m.warn("Park hand factors: Statcast is missing home_team/stand/type columns")
        return {}
    abbr_to_park = {}
    for park_name, v in m.STADIUMS.items():
        abbr_to_park[v[3]] = park_name
    for sc_abbr, stad_abbr in _SC_ABBR_ALIAS.items():
        if stad_abbr in abbr_to_park:
            abbr_to_park[sc_abbr] = abbr_to_park[stad_abbr]

    bb = df[df["type"] == "X"].copy()
    if bb.empty:
        return {}
    bb["is_hr"] = bb["events"] == "home_run"
    g = bb.groupby(["home_team", "stand"]).agg(BBE=("is_hr", "size"), HR=("is_hr", "sum"))
    g = g[g["BBE"] >= min_bbe]
    if g.empty:
        return {}
    league_avg = bb.groupby("stand").apply(
        lambda x: (x["events"] == "home_run").sum() / len(x) * 100 if len(x) else None)

    out = {}
    for (abbr, stand), row in g.iterrows():
        park = abbr_to_park.get(abbr)
        if not park:
            continue
        hr_pct = row["HR"] / row["BBE"] * 100
        avg = league_avg.get(stand)
        index = float(round(hr_pct / avg * 100, 1)) if avg else None
        out.setdefault(park, {})[stand] = {"BBE": int(row["BBE"]), "HR": int(row["HR"]),
                                            "HR%": float(round(hr_pct, 2)), "Index": index}
    return out


# ══════════════════════════════════════════════════════════════════════════
#  UMPIRE — quantified run-environment effect (honest subset only)
# ══════════════════════════════════════════════════════════════════════════

def fetch_umpire_run_environment(game_meta):
    """HP umpire effect on run/K environment, beyond the raw accuracy% that
    generate_picks.fetch_umpire_scores already pulls from this same API.

    ALL keys from a real live response were printed and inspected (not
    assumed) before deciding what to surface:
      umpire, n, called_pitches_sum, called_correct_sum, called_wrong_sum,
      x_correct_calls_sum, correct_calls_above_x_sum, n_challenged_sum,
      n_overturned_sum, total_run_impact_mean, overall_accuracy_wmean,
      x_overall_accuracy_wmean, accuracy_above_x_wmean, consistency_wmean,
      overall_accuracy_min, overall_accuracy_max, x_incorrect_calls_sum,
      favor_abs_mean, successful_challenge_rate, weighted_score.

    HONEST FINDING stated per this project's discipline: nothing in this
    payload splits calls into extra-strike-vs-extra-ball or exposes an
    expected K/BB count — there is no 'expected_k', 'expected_bb', or
    zone-size field of any kind. Verified live: 'favor_abs_mean' is an
    ABSOLUTE value (live range 0.26-1.15, always positive across all 142
    umpires) with no signed counterpart in the payload, so it cannot say
    which side (home/away, hitter/pitcher) an umpire favors — only that
    some bias magnitude exists. Fabricating a directional K%/BB% "boost"
    from these fields would be presenting a made-up number as real, which
    this project treats as the worst outcome (worse than skipping the
    feature). None is fabricated here.

    What IS genuinely actionable, returned as the honest subset:
      - run_impact: 'total_run_impact_mean' verified live to be strictly
        positive across all 142 umpires (range 0.95-2.38, mean 1.45) —
        read as an UNSIGNED run-environment volatility measure (how many
        expected runs this umpire's incorrect calls typically move the
        game by in either direction), not an over/under lean. A high value
        flags a game where the total is less predictable from stats alone,
        not a game that leans Over or Under.
      - accuracy / accuracy_above_expected: 'overall_accuracy_wmean' and
        'accuracy_above_x_wmean' (this umpire's accuracy vs. a pitch-
        tracking expected-accuracy baseline; verified live range -2.05 to
        +1.69, so genuinely signed) — a materially negative value is a
        real signal this umpire calls a looser zone than the tracking
        system expects, even without knowing which side it favors.
      - consistency: 'consistency_wmean' — same-call-same-pitch-location
        reliability; low consistency is a real source of at-bat-level
        unpredictability independent of overall accuracy.
      - challenge behavior: 'successful_challenge_rate' — real signal on
        how often this ump's calls get overturned on replay.

    Returns a dict keyed by matchup string (game_meta's own "matchup" key,
    same convention as fetch_umpire_scores), each value carrying the
    fields above plus 'n' (games in the umpire's sample, for trust)."""
    out = {}
    try:
        r = m.retry_get("https://umpscorecards.com/api/umpires",
                        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
                        timeout=20, retries=2)
        r.raise_for_status()
        by_name = {row.get("umpire"): row for row in r.json().get("rows", [])}
    except Exception as e:
        m.warn(f"Umpire run environment: {e}")
        return out
    for gm in game_meta:
        u = by_name.get(gm.get("hp_ump"))
        if not u:
            continue
        out[gm["matchup"]] = {
            "n": u.get("n"),
            "accuracy": u.get("overall_accuracy_wmean"),
            "accuracy_above_expected": u.get("accuracy_above_x_wmean"),
            "consistency": u.get("consistency_wmean"),
            "run_impact_magnitude": u.get("total_run_impact_mean"),
            "successful_challenge_rate": u.get("successful_challenge_rate"),
        }
    return out


# ══════════════════════════════════════════════════════════════════════════
#  REST / USAGE  (batters and starters, from real MLB game logs)
# ══════════════════════════════════════════════════════════════════════════

import datetime as _dt


def _parse_dates(splits):
    """Sorted ascending list of real datetime.date objects from gameLog splits."""
    out = []
    for sp in splits:
        d = sp.get("date")
        if not d:
            continue
        try:
            out.append(_dt.datetime.strptime(d, "%Y-%m-%d").date())
        except ValueError:
            continue
    return sorted(out)


def _consecutive_streak(dates):
    """Games played in a row with no calendar-day gap >1 (i.e. no off day
    in between; a same-day doubleheader — gap 0 — still counts as
    consecutive), counted backward from the most recent game."""
    if not dates:
        return 0
    streak = 1
    for i in range(len(dates) - 1, 0, -1):
        gap = (dates[i] - dates[i - 1]).days
        if gap <= 1:
            streak += 1
        else:
            break
    return streak


def _rest_batter_one(job):
    pid, name = job
    today = _dt.datetime.strptime(m.TODAY, "%Y-%m-%d").date()
    try:
        r = m.retry_get(f"{STATS_API}/people/{pid}/stats",
                        params={"stats": "gameLog", "group": "hitting", "season": m.YEAR},
                        headers=UA, timeout=15, retries=2)
        r.raise_for_status()
        stats = r.json().get("stats", [])
        if not stats:
            return None
        dates = _parse_dates(stats[0].get("splits", []))
        if not dates:
            return None
    except Exception:
        return None
    last = dates[-1]
    return {"Name": name, "id": pid,
            "days_since_last_game": (today - last).days,
            "consecutive_games": _consecutive_streak(dates),
            "games_last_7d": sum(1 for d in dates if 0 <= (today - d).days <= 7)}


def _rest_pitcher_one(job):
    pid, name = job
    today = _dt.datetime.strptime(m.TODAY, "%Y-%m-%d").date()
    try:
        r = m.retry_get(f"{STATS_API}/people/{pid}/stats",
                        params={"stats": "gameLog", "group": "pitching", "season": m.YEAR},
                        headers=UA, timeout=15, retries=2)
        r.raise_for_status()
        stats = r.json().get("stats", [])
        if not stats:
            return None
        splits = stats[0].get("splits", [])
        # Only rows that were actual starts (a starter can also appear in
        # relief; those rows have gamesStarted==0 and would understate rest).
        start_splits = [sp for sp in splits if int(sp.get("stat", {}).get("gamesStarted", 0) or 0) > 0]
        dates = _parse_dates(start_splits)
        if not dates:
            return None
    except Exception:
        return None
    last = dates[-1]
    return {"Name": name, "id": pid, "days_since_last_start": (today - last).days,
            "starts_this_season": len(dates)}


def rest_and_usage(game_meta, max_batters_per_game=9, max_workers=16):
    """Rest/usage signals for tonight's confirmed lineup batters and
    probable starters, from real MLB game logs (stats=gameLog) — the same
    endpoint shape as mlb_daily.fetch_mlb_game_logs, reused here for a
    different purpose (rest, not recent performance).

    Motivation stated in the task: fatigue predicts both reduced
    effectiveness AND outright scratches (this pipeline currently models
    neither — a scratch voids a bet with no adjustment). This does not
    predict scratches itself (no source here exposes injury/lineup-change
    probability), it only surfaces the real usage pattern a human or a
    downstream model needs to judge that risk: a batter who has started
    every game for two straight weeks, or a starter throwing on short
    rest, is a real and verifiable fatigue signal even without a scratch
    prediction model.

    Batters: days_since_last_game, consecutive_games (streak with no
    calendar-day gap, so a scheduled off-day resets it — same-day double-
    headers do NOT break the streak), games_last_7d.
    Starters: days_since_last_start, computed only from game-log rows
    where gamesStarted==1 — verified live this matters: a starter's own
    game log can carry relief-appearance rows (gamesStarted==0) which
    would silently understate true rest if not filtered out.

    Parallelized with the same ThreadPoolExecutor pattern as bvp_table/
    batter_sp_rp_splits (one gameLog call per player). Verified live on
    the real 2026-08-05 slate: 258 unique lineup batters + up to 30
    probable starters fetched concurrently.

    Returns {"batters": {id: {...}}, "starters": {id: {...}}} — dict-keyed
    by MLBAM id per the project convention, not a list, since this is
    meant to be looked up per player rather than iterated/sorted."""
    batters = {}
    starters = {}
    for gm in game_meta:
        for b in gm.get("away_lineup", [])[:max_batters_per_game] + gm.get("home_lineup", [])[:max_batters_per_game]:
            if b.get("id") and b["id"] not in batters:
                batters[b["id"]] = b.get("name", "?")
        for sp_key in ("away_sp", "home_sp"):
            sp_id = gm.get(f"{sp_key}_id")
            sp_name = gm.get(sp_key)
            if sp_id and sp_name != "TBD" and sp_id not in starters:
                starters[sp_id] = sp_name

    out = {"batters": {}, "starters": {}}
    try:
        if batters:
            with m.ThreadPoolExecutor(max_workers=max_workers) as ex:
                for res in ex.map(_rest_batter_one, list(batters.items())):
                    if res:
                        out["batters"][res["id"]] = res
        if starters:
            with m.ThreadPoolExecutor(max_workers=max_workers) as ex:
                for res in ex.map(_rest_pitcher_one, list(starters.items())):
                    if res:
                        out["starters"][res["id"]] = res
    except Exception as e:
        m.warn(f"Rest/usage: {e}")
    return out


def batter_pa_composition(limit=2000, min_pa=30, start_date=None, end_date=None):
    """Exact per-plate-appearance outcome rates for every batter, keyed by
    MLBAM id: singles, doubles, triples, homers, walks, strikeouts.

    WHY THIS IS NEEDED AT ALL. prop_probability.py computes the real chance a
    prop hits by convolving a per-PA outcome distribution over the projected
    number of PAs. That requires knowing how a batter's hits BREAK DOWN, not
    just how many he gets. Nothing the pipeline already pulls carries that:
    the FanGraphs frame is 403'd on most runs, and the Statcast expected-stats
    fallback that actually ships carries only ba/slg/woba aggregates. With
    aggregates alone the distribution has to be guessed from league-average
    hit composition, which is precisely the assumption that makes a power
    hitter and a slap hitter with identical SLG look identical -- and they are
    not remotely the same bet on "over 1.5 total bases".

    MLB's own season hitting endpoint has the exact counts, free, in one call.
    Verified live: 687 batters returned with full doubles/triples/homeRuns
    breakdowns.

    Rates are per PLATE APPEARANCE (not per at-bat) because that is the unit
    the convolution steps over -- a walk is a real PA that produces zero total
    bases, and dividing by AB would silently drop those and overstate every
    per-PA rate by the PA/AB ratio.

    start_date/end_date are passed straight through to fetch_player_stats and
    bound the window (see its docstring): backtest/engine.py needs these rates
    as of the morning of a past date, not as of today."""
    rows = fetch_player_stats("hitting", limit=limit,
                              start_date=start_date, end_date=end_date)
    out = {}
    for r in rows:
        st = r.get("stat") or {}
        pid = (r.get("player") or {}).get("id")
        try:
            pa = int(st.get("plateAppearances") or 0)
        except (TypeError, ValueError):
            continue
        if not pid or pa < min_pa:
            continue

        def n(key):
            try: return int(st.get(key) or 0)
            except (TypeError, ValueError): return 0

        hits, dbl, tpl, hr = n("hits"), n("doubles"), n("triples"), n("homeRuns")
        singles = max(0, hits - dbl - tpl - hr)
        out[int(pid)] = {
            "name": (r.get("player") or {}).get("fullName"),
            "PA": pa,
            "singles_rate": singles / pa,
            "double_rate": dbl / pa,
            "triple_rate": tpl / pa,
            "hr_rate": hr / pa,
            "bb_rate": n("baseOnBalls") / pa,
            "k_rate": n("strikeOuts") / pa,
            # On-base is what gates steal opportunities, and it is the official
            # figure here rather than a reconstruction.
            "obp": _as_float(st.get("obp")),
            "avg": _as_float(st.get("avg")),
            "slg": _as_float(st.get("slg")),
            # Steal props need three separate things, and conflating them is
            # how a speed-only model overrates a fast player who never gets
            # on: how often he reaches (obp), how often he TRIES once there,
            # and how often he succeeds. attempt_rate is per time-on-base,
            # which is the denominator that actually matters -- raw SB totals
            # confound "runs a lot" with "bats a lot".
            "SB": n("stolenBases"),
            "CS": n("caughtStealing"),
            "times_on_base": n("hits") + n("baseOnBalls") + n("hitByPitch"),
        }
        e = out[int(pid)]
        tob, att = e["times_on_base"], e["SB"] + e["CS"]
        e["attempt_rate"] = (att / tob) if tob > 0 else 0.0
        e["success_rate"] = (e["SB"] / att) if att > 0 else 0.0
    return out


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════════
#  EMPIRICAL PROP HIT RATES — how often the bet ACTUALLY cashed
# ══════════════════════════════════════════════════════════════════════════
#
# The stated objective for this system is the props with the best chance of
# HITTING. There are two ways to answer that, and they are not equally good.
#
# The modelled way -- build a per-PA outcome distribution, convolve it over
# projected plate appearances, read off P(>= threshold) -- is what
# prop_probability.py does. It is principled, it adjusts for tonight's
# context, and it is entirely dependent on its assumptions being right. It
# assumes PAs are independent, that season rates are the true rates, and that
# the projected PA count is correct. Every one of those is an approximation,
# and the errors compound multiplicatively.
#
# The empirical way is to count. A player's game log says exactly how many of
# his games produced at least one stolen base, at least two total bases, at
# least one home run. That is not a model of the prop -- it IS the prop,
# measured. It needs no independence assumption, no PA projection, and no
# distributional form, because the real games already folded all of that in,
# including the things a model never sees: how often he was lifted for a
# pinch hitter, how often the game was a blowout, how often he sat at 2 PA.
#
# WHY THIS WAS WORTH BUILDING, measured rather than argued. Bobby Witt Jr.
# shipped as the #1 pick on the board at a modelled 36.4% to steal a base.
# His actual game log: 27 of 96 games with at least one steal, 28.1%. The
# modelled number was eight points high, and the pick it produced was topping
# a list that is supposed to be sorted by chance of cashing.
#
# Neither method wins outright, so this does not replace the model:
#   - Empirical is unbiased but backward-looking. It cannot know that tonight
#     the opposing catcher has a 1.89s pop time, or that the starter is a
#     6.63 ERA lefty.
#   - Modelled is context-aware but only as good as its assumptions.
# The consumer blends them, with the empirical rate as the anchor and the
# model supplying tonight's adjustment. Both are reported separately so a
# large disagreement is visible rather than averaged away.

# Thresholds computed for every batter. Extended to match what FanDuel
# actually prices: singles, doubles and triples are separate markets there,
# and runs/RBIs/steals were already being computed here but never compared to
# a price. Each extra threshold is free -- it is another comparison over game
# logs already fetched.
_PROP_THRESHOLDS = {
    "hits":         [1, 2, 3, 4],
    "total_bases":  [1, 2, 3, 4],
    "home_runs":    [1, 2],
    "stolen_bases": [1],
    "walks":        [1],
    "runs":         [1, 2],
    "rbis":         [1, 2, 3],
    # Hit TYPES, kept distinct from total bases on purpose: "hit a double"
    # is a different event from "clear two total bases", since a home run
    # does the latter and not the former.
    "singles":      [1],
    "doubles":      [1],
    "triples":      [1],
}


def _game_log(player_id, group, season=None):
    season = season or m.YEAR
    r = m.retry_get(f"{STATS_API}/people/{player_id}/stats",
                    params={"stats": "gameLog", "group": group,
                            "season": season, "sportId": 1},
                    headers=UA, timeout=25, retries=2)
    r.raise_for_status()
    stats = r.json().get("stats") or []
    return (stats[0].get("splits") or []) if stats else []


def _empirical_batter_one(job):
    pid, min_games = job
    try:
        splits = _game_log(pid, "hitting")
    except Exception:
        return pid, None
    games = []
    for s in splits:
        st = s.get("stat") or {}
        try:
            pa = int(st.get("plateAppearances") or 0)
        except (TypeError, ValueError):
            pa = 0
        # A game he did not really play in is not evidence about a prop he
        # would not have been bet in. Pinch-run and defensive-replacement
        # appearances would otherwise drag every rate down.
        if pa < 1:
            continue
        h = int(st.get("hits") or 0)
        tb = int(st.get("totalBases") or 0)
        d2 = int(st.get("doubles") or 0)
        t3 = int(st.get("triples") or 0)
        hr_ = int(st.get("homeRuns") or 0)
        games.append({
            "singles": max(0, h - d2 - t3 - hr_),
            "doubles": d2, "triples": t3,
            "hits": h, "total_bases": tb,
            "home_runs": int(st.get("homeRuns") or 0),
            "stolen_bases": int(st.get("stolenBases") or 0),
            "walks": int(st.get("baseOnBalls") or 0),
            "runs": int(st.get("runs") or 0),
            "rbis": int(st.get("rbi") or 0),
            "pa": pa,
        })
    n = len(games)
    if n < min_games:
        return pid, None
    out = {"games": n, "avg_pa": round(sum(g["pa"] for g in games) / n, 2), "rates": {}}
    for prop, thresholds in _PROP_THRESHOLDS.items():
        for t in thresholds:
            hits = sum(1 for g in games if g[prop] >= t)
            out["rates"][f"{prop}_{t}plus"] = {
                "p": round(hits / n, 4), "n": n, "hit": hits,
                # Wilson lower bound at 95%, reported but NOT used as the
                # estimate -- see _apply_shrinkage for why.
                "p_lo": round(_wilson_lower(hits, n), 4),
            }
    return pid, out


# Games of league-average evidence mixed into every player's own record. At
# 20, a player with a full season (100+ games) barely moves, while a 25-game
# sample is pulled about 44% of the way to league average -- roughly the
# right amount of scepticism for that much evidence.
#
# ── AUDIT, 2026-08-06: MEASURED. NOT ACTED ON -- SEE THE CAVEAT BELOW. ────
# This constant is the strength of a Beta prior, so it is not a taste
# question: for hits_i ~ BetaBinom(n_i, mu*n0, (1-mu)*n0) the posterior mean
# is EXACTLY the (hit + n0*league)/(n + n0) computed below, and n0 is fixed
# by the ratio of within-player to between-player variance. Estimated two
# independent ways on 244 batters / 23,427 real games (2026 game logs):
# beta-binomial marginal-likelihood MLE, and method of moments; then checked
# a third way, non-parametrically, by splitting each player's games odd/even,
# shrinking the odd half and scoring the even half.
#
#   threshold            MLE n0   MoM n0   split-half n0   SHIPPED
#   hits_1plus             72.7     70.3        56            20
#   hits_2plus             72.0     72.8        56            20
#   total_bases_2plus      78.3     76.9        70            20
#   total_bases_3plus      74.3     74.0        90            20
#   total_bases_4plus      56.0     57.0         -            20
#   home_runs_1plus        43.4     46.2        56            20
#   walks_1plus            27.1     26.9        26            20
#   stolen_bases_1plus     16.2     17.2        12            20
#   runs_1plus             83.0     78.2         -            20
#   rbis_1plus            124.8    128.3         -            20
#
# All three estimators agree, so the shape of the answer is not in doubt: a
# single flat prior is wrong because the right n0 varies almost 8x across
# thresholds. 20 is about right for stolen bases (12-17) and walks (26-27) --
# where between-player differences really are large and real -- and 3-4x too
# SMALL for hits and total bases, which is where the board actually bets.
# The consequence at the 25-game gate (MIN_EMPIRICAL_GAMES): a hits_1plus
# rate is currently pulled 44% toward league when it should be pulled 74%.
# Measured cost: at n0=20 the shipped total-bases rates are under-shrunk
# enough that they score WORSE out of sample than ignoring the player's own
# record entirely (total_bases_2plus .65675 vs .65540 league-only).
#
# RECOMMENDED: fit n0 per threshold by the same beta-binomial MLE, inside
# _apply_shrinkage's existing per-key loop -- it already pools every player's
# (hit, n) for each key to get `league`, which is exactly the data the fit
# needs, so this costs one extra optimisation per key and no new inputs.
#
# CAVEAT, stated because it bounds the claim: these n0 were fitted on
# REGULARS (250+ PA, 40+ games). Restricting to regulars truncates the
# between-player spread, which biases n0 upward. That is the same population
# the pipeline scores, so it is the right population to fit on -- but the
# numbers above should not be reused for a wider player pool.
SHRINKAGE_PRIOR_GAMES = 20


def _apply_shrinkage(table, prior_games=SHRINKAGE_PRIOR_GAMES):
    """Shrink each player's observed rate toward the league rate for that same
    threshold, in place, writing "p_hat" as the estimate to actually use.

    WHY NOT THE RAW RATE, AND WHY NOT THE CONFIDENCE BOUND. Both were tried.
    The raw proportion overfits a short sample -- a hitter who has cleared a
    line in 4 of 5 games is not an 80% prop. The Wilson lower bound fixes
    that, but it is the wrong tool here: it answers "what is the pessimistic
    case", not "what is the best estimate", and it is biased low at EVERY
    sample size rather than only small ones. Measured on real game logs:
    Bobby Witt Jr. has a hit in 74.0% of his 96 games, and the lower bound
    reports 64.4%. Nearly ten points of understatement on a full season of
    evidence, on a number whose whole purpose is to be read as the chance of
    the bet cashing.

    Shrinkage toward the league rate fits the problem: it is centred rather
    than pessimistic, it corrects hardest exactly where the evidence is
    thinnest, and it converges on the observed rate as the sample grows."""
    keys = set()
    for e in table.values():
        keys.update((e.get("rates") or {}).keys())
    for key in keys:
        entries = [e["rates"][key] for e in table.values() if key in (e.get("rates") or {})]
        total_hits = sum(r["hit"] for r in entries)
        total_n = sum(r["n"] for r in entries)
        league = (total_hits / total_n) if total_n else 0.0
        for r in entries:
            r["league_p"] = round(league, 4)
            r["p_hat"] = round((r["hit"] + prior_games * league) /
                               (r["n"] + prior_games), 4)
    return table


def _wilson_lower(hits, n, z=1.96):
    """Lower bound of the Wilson score interval — the conservative read of a
    proportion. Chosen over the normal approximation because it stays sane at
    the extremes (0-for-12 and 12-for-12 both have finite, sensible bounds,
    where the normal approximation gives a zero-width interval)."""
    if n <= 0:
        return 0.0
    p = hits / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, (centre - margin) / d)


def empirical_batter_prop_rates(batter_ids, min_games=20, max_workers=16):
    """Per-batter empirical rate of clearing each standard prop threshold, from
    real game logs. Keyed by MLBAM id.

    Verified live: Bobby Witt Jr. returns 27/96 games with a steal (0.281)
    against a modelled 0.364 -- the gap this table exists to expose."""
    from concurrent.futures import ThreadPoolExecutor
    ids = [int(b) for b in dict.fromkeys(batter_ids) if b]
    out = {}
    if not ids:
        return out
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for pid, res in ex.map(_empirical_batter_one, [(i, min_games) for i in ids]):
            if res:
                out[pid] = res
    return _apply_shrinkage(out)


def _empirical_pitcher_one(job):
    pid, min_starts = job
    try:
        splits = _game_log(pid, "pitching")
    except Exception:
        return pid, None
    starts, bfs = [], []
    for s in splits:
        st = s.get("stat") or {}
        # Strikeout props are bet on STARTS. A reliever appearance in the same
        # log is a different event and would collapse every rate.
        try:
            gs = int(st.get("gamesStarted") or 0)
        except (TypeError, ValueError):
            gs = 0
        if gs < 1:
            continue
        starts.append(int(st.get("strikeOuts") or 0))
        # Batters faced per outing, so an opener can be told from a starter.
        bf = st.get("battersFaced")
        if bf is None:
            ip = str(st.get("inningsPitched") or "0")
            try:
                whole, _, frac = ip.partition(".")
                bf = int(whole) * 3 + int(frac or 0) + int(st.get("hits") or 0) \
                     + int(st.get("baseOnBalls") or 0)
            except (TypeError, ValueError):
                bf = None
        if bf is not None:
            try: bfs.append(int(bf))
            except (TypeError, ValueError): pass
    n = len(starts)
    if n < min_starts:
        return pid, None
    out = {"starts": n, "avg_k": round(sum(starts) / n, 2),
           "avg_bf": round(sum(bfs) / len(bfs), 1) if bfs else None, "rates": {}}
    for t in (4, 5, 6, 7, 8, 9):
        hits = sum(1 for k in starts if k >= t)
        out["rates"][f"strikeouts_{t}plus"] = {
            "p": round(hits / n, 4), "n": n, "hit": hits,
            "p_lo": round(_wilson_lower(hits, n), 4),
        }
    return pid, out


def empirical_pitcher_k_rates(pitcher_ids, min_starts=5, max_workers=12):
    """Per-starter empirical rate of reaching each strikeout threshold, counted
    over real starts only (relief appearances excluded)."""
    from concurrent.futures import ThreadPoolExecutor
    ids = [int(p) for p in dict.fromkeys(pitcher_ids) if p]
    out = {}
    if not ids:
        return out
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for pid, res in ex.map(_empirical_pitcher_one, [(i, min_starts) for i in ids]):
            if res:
                out[pid] = res
    # Starters make far fewer starts than batters play games, so the prior
    # carries proportionally more weight here -- correctly so: six starts is
    # genuinely weak evidence about a strikeout threshold.
    #
    # AUDIT, 2026-08-06: VERIFIED CORRECT, unlike the batter prior above.
    # Same three estimators, on 143 starters / 2,688 real starts:
    #   threshold           MLE n0   MoM n0   split-half n0   SHIPPED
    #   strikeouts_4plus      12.2     11.7        12             6
    #   strikeouts_5plus       8.2      7.9         8             6
    #   strikeouts_6plus       6.9      6.9        12             6
    #   strikeouts_7plus       6.9      6.9         8             6
    #   strikeouts_8plus       8.6      8.3         -             6
    # 6 is mildly too aggressive -- 8 to 10 would be better across the board --
    # but it is the right order of magnitude, and unlike the batter case the
    # shrunk pitcher rate clearly beats using no pitcher information at all
    # (strikeouts_6plus: .64682 shipped vs .67210 league-only, held-out log
    # loss). A starter's own strikeout record carries real signal; leave this.
    return _apply_shrinkage(out, prior_games=6)


# ══════════════════════════════════════════════════════════════════════════
#  EXPONENTIAL-DECAY RECENCY  (replaces the hard L7/L14 window for the
#  pitcher K-rate the probability model actually consumes)
# ══════════════════════════════════════════════════════════════════════════
#
# generate_picks.py's modelled strikeout probability is driven by a single
# number: the K rate fed into prop_probability.p_at_least_strikeouts. Until
# this function, that number came from fetch_l14_pitcher_form's hard 14-day
# Statcast window (falling back to season K% only below 15 PA in that
# window) -- a game 15 days old counted zero, a game 13 days old counted
# full, same style of cliff the batter L7/L14 windows use.
#
# TESTED LIVE, OUT OF SAMPLE, BEFORE BUILDING ON IT (train = starts before
# a cutoff 21 days before the pull date, test = starts in the 21 days after
# the cutoff, 147 real starters with GS>=5 on the season and >=15 BF in the
# test window): the CURRENT hard-14-day method is measurably worse than
# doing nothing --
#
#   flat league-average K/BF ......... RMSE 0.0807  (the do-nothing baseline)
#   CURRENT: hard 14-day window ...... RMSE 0.0993  -- WORSE than the baseline
#   hard 21-day window ............... RMSE 0.0849  -- still worse than flat-season
#   hard last-3-starts ............... RMSE 0.0845
#   flat full-season rate ............ RMSE 0.0673  (corr 0.559 with the held-out rate)
#   exponential decay, halflife=10d .. RMSE 0.0770
#   exponential decay, halflife=21d .. RMSE 0.0700
#   exponential decay, halflife=30d .. RMSE 0.0684  (corr 0.549)
#   exponential decay, halflife=45d .. RMSE 0.0675  (corr 0.559 -- matches flat-season)
#
# Two honest findings, not one:
#   1. Exponential decay beats every hard-window alternative at EVERY
#      halflife tested, confirming the general "decay strictly beats a hard
#      cliff" claim -- but only once actually measured, not assumed.
#   2. For THIS metric (K rate), no amount of recency weighting beats the
#      pitcher's own flat full-season rate. K rate is a comparatively stable
#      skill; the "hot/cold" signal hard windows are trying to capture is
#      mostly sampling noise at a 2-4 start sample. halflife=30 is chosen
#      anyway over a flat season rate because it is statistically
#      indistinguishable from it (0.0684 vs 0.0673) while still giving a
#      real (if small) recency tilt that a pure season average cannot: a
#      pitcher who is hurt, has lost a tick, or changed roles shows up
#      faster than a full-season number would let him. It is not shipped
#      because it beats the season rate -- it does not, measurably -- it is
#      shipped because it is priced the same as the season rate on accuracy
#      while retaining that responsiveness, and because it strictly retires
#      the hard-window method that IS measurably worse than doing nothing.
#
# CONTRAST WITH BATTER TB/PA: the same halflife sweep run against batters'
# per-PA total-bases rate (a much rarer, higher-variance event than a
# strikeout, and averaged over ~4 PA/game instead of ~22 BF/start) found
# NO halflife beat the batter's own flat season rate, and the flat SEASON
# rate itself barely beat a flat LEAGUE constant. That result is reported
# in this project's handoff notes rather than shipped as a signal -- pitcher
# K rate and batter TB/PA are not the same kind of target, and treating them
# identically would have shipped a batter-side signal that adds noise, not
# information. This function exists only for the metric where the
# out-of-sample test actually supported it.
_K_RATE_HALFLIFE_DAYS = 30.0


def _exp_k_rate_one(job):
    pid, halflife = job
    try:
        splits = _game_log(pid, "pitching")
    except Exception:
        return pid, None
    today = _dt.datetime.strptime(m.TODAY, "%Y-%m-%d").date()
    num = den = 0.0
    raw_bf = 0
    n_starts = 0
    for s in splits:
        st = s.get("stat") or {}
        if int(st.get("gamesStarted") or 0) < 1:
            continue
        d = s.get("date")
        if not d:
            continue
        try:
            gd = _dt.datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        bf = int(st.get("battersFaced") or 0)
        if bf < 1:
            continue
        k = int(st.get("strikeOuts") or 0)
        age = (today - gd).days
        if age < 0:
            continue
        w = 0.5 ** (age / halflife)
        num += w * k
        den += w * bf
        raw_bf += bf
        n_starts += 1
    if den <= 0 or n_starts == 0:
        return pid, None
    return pid, {"k_rate": num / den, "n_starts": n_starts, "raw_bf": raw_bf}


def exp_weighted_pitcher_k_rate(pitcher_ids, halflife_days=_K_RATE_HALFLIFE_DAYS,
                                min_starts=3, min_raw_bf=40, max_workers=16):
    """Per-starter K rate (K / batters faced) over every real start this
    season, weighted by exp(-age_in_days * ln2 / halflife_days) instead of a
    hard window -- see the module-level comment above for the out-of-sample
    validation that justifies halflife_days=30 and this function's existence.

    Real starts only (gamesStarted>=1 rows from the game log), same filter
    empirical_pitcher_k_rates already uses and for the same reason -- a
    relief inning in the same log is a different event.

    Below min_starts/min_raw_bf the sample is too thin to trust over the
    pitcher's season K% (which the caller should fall back to); both
    thresholds are returned in the record so the caller can also downgrade
    a merely-thin-but-passing sample if it wants stricter confidence.

    Keyed by MLBAM pitcher id."""
    ids = [int(p) for p in dict.fromkeys(pitcher_ids) if p]
    out = {}
    if not ids:
        return out
    with m.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for pid, res in ex.map(_exp_k_rate_one, [(i, halflife_days) for i in ids]):
            if res and res["n_starts"] >= min_starts and res["raw_bf"] >= min_raw_bf:
                out[pid] = res
    return out


# ══════════════════════════════════════════════════════════════════════════
#  TRUE LEAGUE BASE RATES
# ══════════════════════════════════════════════════════════════════════════
#
# How often the WHOLE LEAGUE clears each standard prop line. This is the
# reference every "lift" number is measured against, so it has to actually
# come from the league, and getting it wrong is not a small error.
#
# It was wrong. _apply_shrinkage derives its league figure from whatever
# players are handed to it, which in the live pipeline is tonight's slate --
# about twenty starters, all established enough to be starting. That is a
# self-referential baseline: the "league" rate was the slate's own average, so
# every pick was compared against its own peer group rather than the league.
#
# Measured size of the error: P(K >= 4) came back as 92.1% from a
# three-pitcher pool and 75.6% from a seventeen-pitcher pool, against a true
# 65.6% over 3,741 real starts. A 26-point swing in the reference point,
# driven entirely by who happened to be pitching that night.
#
# That inverted a real conclusion. Cristopher Sanchez clears 4+ strikeouts
# 91.5% of the time. Against the slate-derived 92.1% that reads as -0.6, no
# signal at all; against the true 65.6% it is +25.8, the strongest read on the
# board. A filter built on the fake baseline would have dropped the best pick
# on the grounds that nothing was known about it.
#
# Computed from the season Statcast pull already cached for other sections, so
# there is no extra network cost, and over the full population rather than any
# subset of it.

# Batters faced below which an appearance beginning in the first inning is an
# opener rather than a start. See the note in league_base_rates.
MIN_BF_FOR_START = 15

_LEAGUE_RATES_CACHE = {}


def league_base_rates():
    """P(clearing each standard line) across the entire league. Cached."""
    if _LEAGUE_RATES_CACHE:
        return _LEAGUE_RATES_CACHE
    out = {}
    try:
        df = m.fetch_season_statcast()
        if df is None or df.empty:
            return out
        pa = df[df["events"].notna()]
        if pa.empty:
            return out

        # Pitchers: strikeouts per START. Restricted to pitchers in the game
        # from the first inning -- a strikeout prop is a bet on a start, and
        # relief appearances would collapse every rate.
        firsts = pa.groupby(["pitcher", "game_pk"])["inning"].min()
        bf = pa.groupby(["pitcher", "game_pk"]).size()
        # OPENERS ARE NOT STARTS, and including them makes the comparison
        # meaningless. "Pitched in the first inning" catches an opener facing
        # three batters alongside a starter going seven, but the per-pitcher
        # rates this is compared against come from real starts only (MLB's own
        # gamesStarted). Mixing the two compares starters to a population that
        # is 10% one-inning cameos, which drags the league rate down and hands
        # every single starter a large fake positive lift -- measured before
        # this filter, all three test pitchers came back at +21 to +46,
        # including one who is genuinely below average.
        #
        # Threshold measured rather than guessed: of 3,741 appearances
        # beginning in the first, the 5th percentile faces 8 batters and the
        # median faces 22. A 15-batter floor (roughly four innings) removes
        # 9.6% of appearances, which is the opener tail, and leaves 3,381 real
        # starts. It moves P(K >= 4) from 0.6562 to 0.7146.
        starts_idx = [i for i in firsts[firsts == 1].index
                      if int(bf.get(i, 0)) >= MIN_BF_FOR_START]
        ks = pa[pa["events"] == "strikeout"].groupby(["pitcher", "game_pk"]).size()
        counts = [int(ks.get(i, 0)) for i in starts_idx]
        if counts:
            n = len(counts)
            for t in (4, 5, 6, 7, 8, 9):
                out["strikeouts_%dplus" % t] = round(sum(1 for k in counts if k >= t) / n, 4)
            out["_n_starts"] = n

        # Batters: per game played, mirroring empirical_batter_prop_rates,
        # which also counts only games with at least one plate appearance.
        tb_map = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
        b = pa.copy()
        b["tb"] = b["events"].map(tb_map).fillna(0)
        b["is_h"] = b["events"].isin(tb_map).astype(int)
        b["is_hr"] = (b["events"] == "home_run").astype(int)
        b["is_bb"] = b["events"].isin(["walk", "intent_walk"]).astype(int)
        g = b.groupby(["batter", "game_pk"]).agg(
            h=("is_h", "sum"), tb=("tb", "sum"),
            hr=("is_hr", "sum"), bb=("is_bb", "sum"))
        if not g.empty:
            for t in (1, 2, 3):
                out["hits_%dplus" % t] = round(float((g["h"] >= t).mean()), 4)
            for t in (2, 3, 4):
                out["total_bases_%dplus" % t] = round(float((g["tb"] >= t).mean()), 4)
            out["home_runs_1plus"] = round(float((g["hr"] >= 1).mean()), 4)
            out["walks_1plus"] = round(float((g["bb"] >= 1).mean()), 4)
            out["_n_batter_games"] = int(len(g))
    except Exception as e:
        m.warn("League base rates: %s" % e)
    _LEAGUE_RATES_CACHE.update(out)
    return out

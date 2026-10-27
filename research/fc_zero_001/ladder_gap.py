#!/usr/bin/env python3
"""FC-ZERO-001 H1 "ladder gap": the evaluation frozen in PREREG.md (pinned at 96652d3a).

Usage: python3 ladder_gap.py <cache dir for StatsAPI JSON>
Prices: data/props/props_2026-*.json (production FanDuel captures, NOT authenticated).
Outcomes: official MLB StatsAPI schedule + boxscores (free public endpoints), cached on disk.
"""
from __future__ import annotations

import glob
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from odds_fanduel import normalize_name  # noqa: E402  (the production normaliser, per PREREG)

API = "https://statsapi.mlb.com/api/v1"
SEED, BOOT = 20261027, 4000
POST = {"F", "D", "L", "W"}


def T(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def imp(a):
    return (-a) / (-a + 100.0) if a < 0 else 100.0 / (a + 100.0)


def payout(a):                        # profit per 1 unit staked on a win
    return 100.0 / -a if a < 0 else a / 100.0


def get(cache, name, url, params=None):
    p = Path(cache) / name
    if p.exists():
        return json.loads(p.read_text())
    for i in range(5):
        try:
            r = requests.get(url, params=params, timeout=30)
            r.raise_for_status()
            p.write_text(r.text)
            return r.json()
        except requests.RequestException:
            time.sleep(2 ** i)
    raise RuntimeError(f"fetch failed: {url}")


def population():
    """Decision snapshot per (event_id, player_norm): latest with taken_at <= start-30min holding both rows."""
    pop, counts = {}, Counter()
    for f in sorted(glob.glob(str(ROOT / "data/props/props_2026-*.json"))):
        for s in json.loads(Path(f).read_text()).get("snapshots", []):
            book = defaultdict(dict)
            for r in s.get("rows", []):
                if r.get("in_play") or not isinstance(r.get("american"), int) or not r.get("start_time"):
                    continue
                if (r["stat"], r["needs"]) not in (("hits", 1), ("hits_runs_rbis", 1)):
                    continue
                if T(r["taken_at"]) > T(r["start_time"]) - timedelta(minutes=30):
                    continue
                book[(r["event_id"], r["player_norm"])][r["stat"]] = r
            for k, b in book.items():
                if len(b) != 2:
                    continue
                h, x = b["hits"], b["hits_runs_rbis"]
                if k not in pop or T(h["taken_at"]) >= T(pop[k]["taken_at"]):
                    pop[k] = {"event_id": k[0], "player_norm": k[1], "player": h["player"], "game": h["game"],
                              "start_time": h["start_time"], "taken_at": h["taken_at"],
                              "h1": h["american"], "hrr1": x["american"], "g": imp(x["american"]) - imp(h["american"])}
    counts["pairs"] = len(pop)
    return list(pop.values()), counts


def teams(game):
    a, h = game.split(" @ ")
    return a.split(" (")[0].strip(), h.split(" (")[0].strip()


def grade(rows, cache):
    days = sorted({T(r["start_time"]).date() for r in rows})
    sched = get(cache, f"sched_{days[0]}_{days[-1]}.json", f"{API}/schedule",
                {"sportId": 1, "startDate": str(days[0] - timedelta(days=1)), "endDate": str(days[-1] + timedelta(days=1))})
    games = [g for d in sched.get("dates", []) for g in d.get("games", [])]
    out, c = [], Counter()
    box_names = {}
    for r in rows:
        a, h = teams(r["game"])
        st = T(r["start_time"])
        cand = [g for g in games if g["teams"]["away"]["team"]["name"] == a and g["teams"]["home"]["team"]["name"] == h
                and abs(T(g["gameDate"]) - st) <= timedelta(hours=6)]
        if len(cand) != 1:
            c["unmatched_game"] += 1
            continue
        g = cand[0]
        if g.get("status", {}).get("codedGameState") != "F":
            c["not_final"] += 1
            continue
        pk = g["gamePk"]
        if pk not in box_names:
            bx = get(cache, f"box_{pk}.json", f"{API}/game/{pk}/boxscore")
            m = defaultdict(list)
            for side in ("away", "home"):
                for p in bx["teams"][side]["players"].values():
                    m[normalize_name(p["person"]["fullName"])].append(p.get("stats", {}).get("batting", {}))
            box_names[pk] = m
        hits = box_names[pk].get(r["player_norm"])
        if not hits:
            c["unmatched_player"] += 1
            continue
        if len(hits) > 1:
            c["ambiguous_player"] += 1
            continue
        b = hits[0]
        if not b or int(b.get("plateAppearances", 0) or 0) == 0:
            c["void"] += 1
            continue
        y = int((b.get("hits", 0) or 0) + (b.get("runs", 0) or 0) + (b.get("rbi", 0) or 0) >= 1)
        out.append(dict(r, game_pk=pk, game_type=g.get("gameType"), slate=g.get("officialDate"), y=y,
                        hits_y=int((b.get("hits", 0) or 0) >= 1)))
        c["graded"] += 1
    return out, c


def assign_terciles(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["hrr1"]].append(r)
    dropped = 0
    for s, rs in by.items():
        if len(rs) < 6:
            for r in rs:
                r["terc"] = None
            dropped += len(rs)
            continue
        gs = np.array([r["g"] for r in rs])
        order = gs.argsort(kind="mergesort")
        ranks = np.empty(len(gs))
        ranks[order] = np.arange(1, len(gs) + 1)
        for v in np.unique(gs):                      # tie-average
            ranks[gs == v] = ranks[gs == v].mean()
        pct = (ranks - 0.5) / len(gs)
        for r, p in zip(rs, pct):
            r["terc"] = "LOW" if p <= 1 / 3 else ("HIGH" if p > 2 / 3 else "MID")
    return dropped


def D_stat(rows):
    by = defaultdict(lambda: {"LOW": [], "HIGH": [], "n": 0})
    for r in rows:
        if r.get("terc") is None:
            continue
        by[r["hrr1"]]["n"] += 1
        if r["terc"] in ("LOW", "HIGH"):
            by[r["hrr1"]][r["terc"]].append(r["y"])
    num = den = 0.0
    for s, v in by.items():
        if v["LOW"] and v["HIGH"]:
            num += v["n"] * (np.mean(v["LOW"]) - np.mean(v["HIGH"]))
            den += v["n"]
    return num / den if den else float("nan")


def roi(rows):
    return float(np.mean([payout(r["hrr1"]) if r["y"] else -1.0 for r in rows])) if rows else float("nan")


def boot(rows, fn, rng):
    games = defaultdict(list)
    for r in rows:
        games[r["game_pk"]].append(r)
    keys = list(games)
    vals = []
    for _ in range(BOOT):
        pick = rng.integers(0, len(keys), len(keys))
        vals.append(fn([r for i in pick for r in games[keys[i]]]))
    vals = np.array(vals)
    vals = vals[~np.isnan(vals)]
    return {"ci95": [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))],
            "ci99": [float(np.percentile(vals, 0.5)), float(np.percentile(vals, 99.5))]}


def split_summary(rows, rng):
    dropped = assign_terciles(rows)
    d = D_stat(rows)
    res = {"graded": len(rows), "games": len({r["game_pk"] for r in rows}), "strata_dropped_rows": dropped,
           "D": d, "D_boot": boot(rows, D_stat, rng), "terciles": {}}
    for t in ("LOW", "MID", "HIGH"):
        rs = [r for r in rows if r.get("terc") == t]
        res["terciles"][t] = {"n": len(rs), "wins": sum(r["y"] for r in rs), "hit_rate": float(np.mean([r["y"] for r in rs])) if rs else None,
                              "mean_raw_break_even": float(np.mean([imp(r["hrr1"]) for r in rs])) if rs else None,
                              "roi": roi(rs), "roi_boot": boot(rs, roi, rng) if rs else None,
                              "mean_g": float(np.mean([r["g"] for r in rs])) if rs else None}
    return res


def pick(rows, n, key):
    out, per_game, seen = [], Counter(), set()
    for r in sorted(rows, key=key):
        if len(out) >= n:
            break
        if r["player_norm"] in seen or per_game[r["game_pk"]] >= 2:
            continue
        out.append(r)
        seen.add(r["player_norm"])
        per_game[r["game_pk"]] += 1
    return out


def arm_stats(sel):
    w = sum(r["y"] for r in sel)
    odds = sorted(r["hrr1"] for r in sel)
    return {"n": len(sel), "wins": w, "losses": len(sel) - w, "hit_rate": w / len(sel) if sel else None, "roi": roi(sel),
            "mean_raw_break_even": float(np.mean([imp(r["hrr1"]) for r in sel])) if sel else None,
            "odds_median": odds[len(odds) // 2] if odds else None, "odds_range": [odds[0], odds[-1]] if odds else None}


def champion(rs_slates):
    reg = json.loads((ROOT / "data/public_top_picks/registry.json").read_text())["entries"]
    by = defaultdict(list)
    for e in reg.values():
        if e.get("slate_date") in rs_slates:
            by[e["slate_date"]].append(e)
    return by


def main(cache):
    Path(cache).mkdir(parents=True, exist_ok=True)
    rows, pc = population()
    graded, gc = grade(rows, cache)
    rng = np.random.default_rng(SEED)
    rs = [r for r in graded if r["game_type"] == "R"]
    ps = [r for r in graded if r["game_type"] in POST]
    res = {"prereg_commit": "96652d3ad30ea1cff110d1a0d503cab876df72f2", "population": dict(pc), "grading": dict(gc),
           "other_game_types": dict(Counter(r["game_type"] for r in graded if r["game_type"] != "R" and r["game_type"] not in POST)),
           "P_RS": split_summary(rs, rng), "P_PS": split_summary(ps, rng) if ps else None}
    D = res["P_RS"]["D"]
    fav = "LOW" if D > 0 else "HIGH"
    res["favoured_tercile"] = fav
    # equal-volume arms on slates carrying published Top Picks
    slates = sorted({r["slate"] for r in rs})
    champ = champion(set(slates))
    arms = {"A": [], "B": [], "C": []}
    a_w = a_n = 0
    target = 0
    for s in slates:
        n = len(champ.get(s, []))
        if not n:
            continue
        target += n
        day = [r for r in rs if r["slate"] == s]
        sgn = 1 if fav == "LOW" else -1
        arms["B"] += pick([r for r in day if r.get("terc") == fav], n,
                          key=lambda r: (-imp(r["hrr1"]), sgn * r["g"], r["event_id"], r["player_norm"]))
        arms["C"] += pick(day, n, key=lambda r: (-imp(r["hrr1"]), r["event_id"], r["player_norm"]))
        arms["A"] += champ[s]       # outcomes for A are the published record (FC-ACC-019 R1 reconciliation)
    res["equal_volume"] = {"slates": sum(1 for s in slates if champ.get(s)), "champion_N": target,
                           "B": arm_stats(arms["B"]), "C": arm_stats(arms["C"]),
                           "B_fill_share": len(arms["B"]) / target if target else None}
    bset = {(r["event_id"], r["player_norm"]) for r in arms["B"]}
    cset = {(r["event_id"], r["player_norm"]) for r in arms["C"]}
    res["equal_volume"]["B_vs_C_added_winners"] = sum(r["y"] for r in arms["B"] if (r["event_id"], r["player_norm"]) not in cset)
    res["equal_volume"]["B_vs_C_removed_winners"] = sum(r["y"] for r in arms["C"] if (r["event_id"], r["player_norm"]) not in bset)
    res["equal_volume"]["A_entries"] = len(arms["A"])
    # verdict
    rsd = res["P_RS"]
    excl = not (rsd["D_boot"]["ci99"][0] <= 0 <= rsd["D_boot"]["ci99"][1])
    fav_roi_lo = rsd["terciles"][fav]["roi_boot"]["ci95"][0]
    stable = res["P_PS"] is not None and np.sign(res["P_PS"]["D"]) == np.sign(D)
    vol = (res["equal_volume"]["B_fill_share"] or 0) >= 0.95
    b_gt_c = res["equal_volume"]["B"]["roi"] > res["equal_volume"]["C"]["roi"]
    res["checks"] = {"primary_ci99_excludes_0": bool(excl), "favoured_roi_ci95_lower_gt_0": bool(fav_roi_lo > 0),
                     "postseason_same_sign": bool(stable), "volume_fill_ge_95pct": bool(vol), "B_roi_gt_C_roi": bool(b_gt_c)}
    res["verdict"] = "STOP" if not excl else ("GO" if all(res["checks"].values()) else "MODIFY")
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "ladder_gap.json").write_text(json.dumps(res, indent=1, default=float))
    return res


if __name__ == "__main__":
    r = main(sys.argv[1])
    print(json.dumps({k: r[k] for k in ("population", "grading", "favoured_tercile", "checks", "verdict")}, indent=1))

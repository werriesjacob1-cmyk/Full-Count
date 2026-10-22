"""FC-ACC-014 feature construction (PREREG.md §2-§4). Every feature uses only games
on dates STRICTLY BEFORE the target date. The target game's own record is used
only for identity (team, home/away), never for an outcome-derived value.

Usage: python3 features.py <canonical rows.jsonl>
Writes out/features_hits.jsonl, out/features_tb.jsonl, out/features_k.jsonl (gitignored)
and out/features_manifest.json (sha256 + coverage).
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import re
import sys
from collections import defaultdict, Counter
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
sys.path.insert(0, str(ROOT))
import fc013_build as F13  # noqa: E402

PA_MAX = 8


def od(s):
    return date.fromisoformat(s).toordinal()


class Prefix:
    """Per-key chronological prefix sums; query(key, before_ord, after_ord=None) -> sums over
    rows with after_ord <= ord < before_ord."""

    def __init__(self, fields):
        self.fields, self.raw = fields, defaultdict(list)

    def add(self, key, ordv, vals):
        self.raw[key].append((ordv, vals))

    def freeze(self):
        self.idx = {}
        for k, rows in self.raw.items():
            rows.sort(key=lambda x: x[0])
            ords = [r[0] for r in rows]
            cum = np.zeros((len(rows) + 1, len(self.fields)))
            cum[1:] = np.cumsum(np.array([r[1] for r in rows], dtype=float), axis=0)
            self.idx[k] = (ords, cum)
        self.raw = None
        return self

    def query(self, key, before, after=None):
        if key not in self.idx:
            return np.zeros(len(self.fields))
        ords, cum = self.idx[key]
        hi = bisect.bisect_left(ords, before)
        lo = 0 if after is None else bisect.bisect_left(ords, after)
        return cum[hi] - cum[lo]


def shrink(num, den, prior_rate, prior_n):
    return (num + prior_n * prior_rate) / (den + prior_n) if prior_rate is not None else (num / den if den else None)


def parse_weather(w):
    temp = None
    try:
        temp = float(w.get("temp")) if w.get("temp") not in (None, "") else None
    except (TypeError, ValueError):
        temp = None
    wind = str(w.get("wind") or "")
    m = re.match(r"\s*(\d+)\s*mph", wind)
    mph = float(m.group(1)) if m else None
    cond = str(w.get("condition") or "")
    roof = int(cond in ("Dome", "Roof Closed") or wind.strip().lower().startswith("0 mph, none"))
    return {"temp": temp, "wind_mph": mph, "wind_out": int("Out" in wind), "wind_in": int(" In " in f" {wind} "),
            "roof": roof}


def main(canon_path):
    bg = json.loads((OUT / "batter_games.json").read_text())
    ps = json.loads((OUT / "pitcher_starts.json").read_text())
    sched = json.loads((OUT / "schedule.json").read_text())
    people = json.loads((OUT / "people.json").read_text())

    # ---- batter, team, league, venue prefix tables -------------------------------------------
    BF = ["pa", "h", "tb", "so", "bb", "hr", "xbh", "s1", "d2", "d3", "g"]
    bat = Prefix(BF)          # key (pid, season)
    team = Prefix(["r", "pa", "h", "so", "g"])   # key (team, season)
    lg = Prefix(["pa", "h", "tb", "so", "s1", "d2", "d3", "hr"])   # key season
    venue = Prefix(["pa", "h", "tb", "so"])      # key venue (all seasons)
    lg_all = Prefix(["pa", "h", "tb", "so"])     # key "all"
    game_team = defaultdict(lambda: [0, 0, 0, 0])  # (game, team) -> r, pa, h, so
    game_tot = defaultdict(lambda: [0, 0, 0, 0])   # game -> pa, h, tb, so
    bg_by = {}
    for r in bg:
        o = od(r["date"])
        s1 = r["h"] - r["d2"] - r["d3"] - r["hr"]
        bat.add((r["pid"], r["season"]), o, [r["pa"], r["h"], r["tb"], r["so"], r["bb"], r["hr"],
                                             r["d2"] + r["d3"] + r["hr"], s1, r["d2"], r["d3"], 1 if r["pa"] > 0 else 0])
        gt = game_team[(r["game_pk"], r["team"])]
        gt[0] += r["r"]; gt[1] += r["pa"]; gt[2] += r["h"]; gt[3] += r["so"]
        tt = game_tot[r["game_pk"]]
        tt[0] += r["pa"]; tt[1] += r["h"]; tt[2] += r["tb"]; tt[3] += r["so"]
        lg.add(r["season"], o, [r["pa"], r["h"], r["tb"], r["so"], s1, r["d2"], r["d3"], r["hr"]])
        bg_by[(r["game_pk"], r["pid"])] = r
    gdate = {int(k): v["date"] for k, v in sched.items()}
    for (gp, tm), v in game_team.items():
        if gp in gdate:
            team.add((tm, int(gdate[gp][:4])), od(gdate[gp]), v + [1])
    for gp, v in game_tot.items():
        g = sched.get(str(gp))
        if g and g["venue"]:
            venue.add(g["venue"], od(g["date"]), v)
            lg_all.add("all", od(g["date"]), v)
    for t in (bat, team, lg, venue, lg_all):
        t.freeze()

    # ---- pitcher starts prefix -------------------------------------------------------------
    pit = Prefix(["bf", "h", "tba", "so", "bb", "hr", "gs", "pitches"])
    pit_rows = defaultdict(list)
    for r in ps:
        tba = r["h"] + r["d2"] + 2 * r["d3"] + 3 * r["hr"]
        pit.add((r["pid"], r["season"]), od(r["date"]), [r["bf"], r["h"], tba, r["ks"], r["bb"], r["hr"], 1, r["pitches"]])
        pit_rows[r["pid"]].append(r)
    pit.freeze()
    for v in pit_rows.values():
        v.sort(key=lambda x: (x["date"], x["game_pk"]))

    # ---- PA-per-game distribution by batting slot (strictly prior, all seasons) -------------
    slot_events = []
    for gp, g in sched.items():
        for side in ("home_lineup", "away_lineup"):
            for k, pid in enumerate(g[side][:9]):
                r = bg_by.get((int(gp), pid))
                if r is not None:
                    slot_events.append((od(g["date"]), k + 1, min(r["pa"], PA_MAX)))
    slot_events.sort()
    slot_ords = [e[0] for e in slot_events]
    slot_cum = np.zeros((len(slot_events) + 1, 10, PA_MAX + 1))
    for i, (_, s, n) in enumerate(slot_events):
        slot_cum[i + 1] = slot_cum[i]
        slot_cum[i + 1, s, n] += 1

    def pa_dist(slot, before):
        hi = bisect.bisect_left(slot_ords, before)
        c = slot_cum[hi, slot] if 1 <= slot <= 9 else slot_cum[hi, 1:].sum(0)
        tot = c.sum()
        return (c / tot).tolist() if tot >= 200 else None

    def league_rates(season, before):
        v = lg.query(season, before)
        if v[0] < 20000:
            v = lg.query(season - 1, 10 ** 9)
        pa = v[0]
        return {"hit": v[1] / pa, "tb": v[2] / pa, "so": v[3] / pa, "s1": v[4] / pa, "d2": v[5] / pa,
                "d3": v[6] / pa, "hr": v[7] / pa} if pa else None

    def batter_feats(pid, season, before, L):
        v = bat.query((pid, season), before)
        pv = bat.query((pid, season - 1), 10 ** 9)
        r7 = bat.query((pid, season), before, before - 7)
        r14 = bat.query((pid, season), before, before - 14)
        pa = v[0]
        f = {"skill_hit": shrink(v[1], pa, L["hit"], 100), "skill_tb": shrink(v[2], pa, L["tb"], 100),
             "skill_k": shrink(v[3], pa, L["so"], 100), "skill_bb": shrink(v[4], pa, 0.085, 100),
             "skill_hr": shrink(v[5], pa, L["hr"], 100),
             "skill_xbh": shrink(v[6], pa, L["d2"] + L["d3"] + L["hr"], 100),
             "skill_logpa": math.log1p(pa),
             "skill_prev_hit": shrink(pv[1], pv[0], L["hit"], 100) if pv[0] else None,
             "skill_prev_tb": shrink(pv[2], pv[0], L["tb"], 100) if pv[0] else None,
             "rec_hit7": shrink(r7[1], r7[0], L["hit"], 20), "rec_tb7": shrink(r7[2], r7[0], L["tb"], 20),
             "rec_hit14": shrink(r14[1], r14[0], L["hit"], 20), "rec_tb14": shrink(r14[2], r14[0], L["tb"], 20),
             "rec_g7": r7[10]}
        comp = {"s1": shrink(v[7], pa, L["s1"], 100), "d2": shrink(v[8], pa, L["d2"], 100),
                "d3": shrink(v[9], pa, L["d3"], 100), "hr": shrink(v[5], pa, L["hr"], 100)}
        return f, comp

    def sp_feats(sp, season, before, L):
        v = pit.query((sp, season), before)
        bfv = v[0]
        return {"opp_sp_hit": shrink(v[1], bfv, L["hit"], 100), "opp_sp_tba": shrink(v[2], bfv, L["tb"], 100),
                "opp_sp_k": shrink(v[3], bfv, L["so"], 100), "opp_sp_bb": shrink(v[4], bfv, 0.085, 100),
                "opp_sp_hr": shrink(v[5], bfv, L["hr"], 100), "opp_sp_starts": v[6]}

    def venue_factor(vid, before):
        v = venue.query(vid, before)
        a = lg_all.query("all", before)
        if not a[0]:
            return None, None, None
        lh, lt, lk = a[1] / a[0], a[2] / a[0], a[3] / a[0]
        return (shrink(v[1], v[0], lh, 3000) / lh, shrink(v[2], v[0], lt, 3000) / lt, shrink(v[3], v[0], lk, 3000) / lk)

    # ---- batter markets -----------------------------------------------------------------------
    cover = defaultdict(Counter)
    outs = {"hits": open(OUT / "features_hits.jsonl", "w"), "total_bases": open(OUT / "features_tb.jsonl", "w")}
    with open(canon_path) as fh:
        for line in fh:
            c = json.loads(line)
            mk = c.get("prop_type")
            if not ((mk == "hits" and c.get("needs") == 1) or (mk == "total_bases" and c.get("needs") == 2)):
                continue
            if c.get("outcome") not in (0, 1):
                cover[mk]["ungraded_skipped"] += 1
                continue
            dt, gp, pid = c["date"], int(c["game_pk"]), int(c["player_id"])
            season, before = int(dt[:4]), od(dt)
            g = sched.get(str(gp))
            own = bg_by.get((gp, pid))
            L = league_rates(season, before)
            sig = c.get("signals") or {}
            f = {"base_logit": None}
            p0 = min(max(float(c["predicted_prob"]), 1e-4), 1 - 1e-4)
            f["base_logit"] = math.log(p0 / (1 - p0))
            bf_, comp = batter_feats(pid, season, before, L)
            f.update(bf_)
            slot = None
            if sig.get("lineup_slot") is not None:
                slot = int(round(9 - float(sig["lineup_slot"]) / 12.5))
            elif g:
                for side in ("home_lineup", "away_lineup"):
                    if pid in g[side][:9]:
                        slot = g[side].index(pid) + 1
            home = own["home"] if own else (g and pid in g["home_lineup"])
            tm = own["team"] if own else None
            tv = team.query((tm, season), before) if tm else np.zeros(5)
            f.update({"opp_slot": slot, "opp_home": int(bool(home)) if home is not None else None,
                      "opp_team_rpg": (tv[0] / tv[4]) if tv[4] else None,
                      "lin_team_hit": shrink(tv[2], tv[1], L["hit"], 500) if tv[1] else None})
            sp = None
            if g:
                sp = g["away_sp"] if home else g["home_sp"]
            spf = sp_feats(sp, season, before, L) if sp else {}
            f.update({k: spf.get(k) for k in ("opp_sp_hit", "opp_sp_tba", "opp_sp_k", "opp_sp_bb", "opp_sp_hr", "opp_sp_starts")})
            bh = (people.get(str(pid)) or {}).get("bat")
            ph = (people.get(str(sp)) or {}).get("throw") if sp else None
            f["match_switch"] = int(bh == "S") if bh else None
            f["match_same_hand"] = int(bh == ph) if (bh in ("L", "R") and ph in ("L", "R")) else (0 if bh == "S" else None)
            for s_ in ("platoon", "sp_era_weak", "l7_avg_ev", "l7_barrel_pct", "season_barrel_pct", "iso", "platoon_xwoba",
                       "platoon_barrel_pct", "bat_speed_trend", "pitch_exploit", "woba_behind", "woba_ahead",
                       "park_hand_index", "pull_park_synergy", "days_rest", "series_game", "getaway_day",
                       "bullpen_fatigue", "opp_catcher_framing", "bullpen_era_diff"):
                f[f"sig_{s_}"] = sig.get(s_)
            vh = vt = vk = None
            wx = {"temp": None, "wind_mph": None, "wind_out": None, "wind_in": None, "roof": None}
            if g:
                vh, vt, vk = venue_factor(g["venue"], before)
                wx = parse_weather(g["weather"])
            f.update({"park_hit": vh, "park_tb": vt, **{f"wx_{k}": v for k, v in wx.items()}})
            pad = pa_dist(slot, before) if slot else None
            ratio_h = (spf.get("opp_sp_hit") / L["hit"]) if spf.get("opp_sp_hit") else 1.0
            ratio_hr = (spf.get("opp_sp_hr") / L["hr"]) if spf.get("opp_sp_hr") else 1.0
            m3 = {"pa_dist": pad, "q_hit": None, "comp": None}
            if pad and f["skill_hit"]:
                b, pz, lgh = f["skill_hit"], (spf.get("opp_sp_hit") or L["hit"]), L["hit"]
                q = (b * pz / lgh) / ((b * pz / lgh) + ((1 - b) * (1 - pz) / (1 - lgh)))      # log5
                m3["q_hit"] = min(0.95, q * (vh or 1.0))
                comp2 = {k: comp[k] * (ratio_hr if k == "hr" else ratio_h) * (vt or 1.0) for k in comp}
                m3["comp"] = comp2
            for k, v in f.items():
                cover[mk][f"has_{k}"] += v is not None
            cover[mk]["rows"] += 1
            rec = {"date": dt, "season": season, "month": dt[:7], "game_pk": gp, "player_id": pid,
                   "y": int(c["outcome"]), "fair_test": bool(c.get("fair_test")), "p0": float(c["predicted_prob"]),
                   "f": f, "m3": m3}
            outs[mk].write(json.dumps(rec) + "\n")
    for v in outs.values():
        v.close()

    # ---- strikeouts (FC-ACC-013 universe, 2024-2026) ------------------------------------------
    k_starts = [{"season": r["season"], "date": r["date"], "game_pk": r["game_pk"], "pitcher_id": r["pid"],
                 "pitches": r["pitches"], "bf": r["bf"], "outs": r["outs"], "ks": r["ks"]}
                for r in ps if r["season"] in (2024, 2025, 2026)]
    krows, kexcl = F13.build(k_starts)
    cover["strikeouts"]["fc013_excluded"] = dict(kexcl)
    with open(OUT / "features_k.jsonl", "w") as fh:
        for r in krows:
            season, before, pid, gp = r["season"], od(r["date"]), r["pitcher_id"], r["game_pk"]
            L = league_rates(season, before)
            g = sched.get(str(gp))
            hist = [x for x in pit_rows[pid] if x["season"] == season and x["date"] < r["date"]]
            v = pit.query((pid, season), before)
            pv = pit.query((pid, season - 1), 10 ** 9)
            f = {}
            for t in (4, 5, 6):
                p0 = min(max(r[f"A{t}"], 1e-4), 1 - 1e-4)
                f[f"base_logit{t}"] = math.log(p0 / (1 - p0))
            f.update({"skill_kexp": r["k_rate"], "skill_k": shrink(v[3], v[0], L["so"], 100),
                      "skill_bb": shrink(v[4], v[0], 0.085, 100),
                      "skill_prev_k": shrink(pv[3], pv[0], L["so"], 100) if pv[0] else None,
                      "skill_starts": r["prior_starts"],
                      "work_ebf": r["ebf"], "work_bf_mean": float(np.mean([x["bf"] for x in hist[-10:]])),
                      "work_bf_sd": r["prior_bf_sd"], "work_last_pitches": hist[-1]["pitches"],
                      "work_rest": od(r["date"]) - od(hist[-1]["date"]) - 1,
                      "rec_k3": sum(x["ks"] for x in hist[-3:]) / max(1, sum(x["bf"] for x in hist[-3:]))})
            lineup, opp_team, ph = [], None, (people.get(str(pid)) or {}).get("throw")
            if g:
                is_home = g["home_sp"] == pid or (g["away_sp"] != pid and any(
                    x["game_pk"] == gp and x["home"] for x in pit_rows[pid]))
                lineup = g["away_lineup"] if is_home else g["home_lineup"]
                opp_team = g["away"] if is_home else g["home"]
            ks_, same = [], []
            for b in lineup[:9]:
                bv = bat.query((b, season), before)
                ks_.append(shrink(bv[3], bv[0], L["so"], 50))
                bh = (people.get(str(b)) or {}).get("bat")
                if bh in ("L", "R") and ph in ("L", "R"):
                    same.append(int(bh == ph))
            tv = team.query((opp_team, season), before) if opp_team else np.zeros(5)
            vk = venue_factor(g["venue"], before)[2] if g else None
            wx = parse_weather(g["weather"]) if g else {"temp": None, "roof": None}
            f.update({"match_lineup_k": float(np.mean(ks_)) if ks_ else None, "match_lineup_n": len(ks_),
                      "match_team_k": shrink(tv[3], tv[1], L["so"], 500) if tv[1] else None,
                      "match_same_hand": float(np.mean(same)) if same else None,
                      "park_k": vk, "wx_temp": wx.get("temp"), "wx_roof": wx.get("roof")})
            lk = L["so"]
            lin = f["match_lineup_k"] or lk
            b = r["k_rate"]
            q = (b * lin / lk) / ((b * lin / lk) + ((1 - b) * (1 - lin) / (1 - lk)))
            m3 = {t: sum(w * F13.pp._binom_at_least(bfx, q, t) for bfx, w in r["draws"]) for t in (4, 5, 6)}
            for k, val in f.items():
                cover["strikeouts"][f"has_{k}"] += val is not None
            cover["strikeouts"]["rows"] += 1
            rec = {"date": r["date"], "season": season, "month": r["date"][:7], "game_pk": gp, "player_id": pid,
                   "y": {t: r[f"y{t}"] for t in (4, 5, 6)}, "p0": {t: r[f"A{t}"] for t in (4, 5, 6)},
                   "outs": r["outs"], "f": f, "m3": m3}
            fh.write(json.dumps(rec) + "\n")
    man = {"canonical_rows": canon_path, "coverage": {k: dict(v) for k, v in cover.items()},
           "sha256": {n: hashlib.sha256((OUT / n).read_bytes()).hexdigest()
                      for n in ("features_hits.jsonl", "features_tb.jsonl", "features_k.jsonl")}}
    (OUT / "features_manifest.json").write_text(json.dumps(man, indent=1, default=str))
    print(json.dumps({k: v.get("rows") for k, v in man["coverage"].items()}, indent=1), json.dumps(man["sha256"], indent=1))


if __name__ == "__main__":
    main(sys.argv[1])

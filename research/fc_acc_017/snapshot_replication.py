#!/usr/bin/env python3
"""FC-ACC-017 Phase 8 replication on a wider authentic pregame population.

Every priced, lineup-confirmed hits 1+ candidate in 8 production dashboard snapshots
(docs/data.json at the main commits listed below), restricted to games starting after the
snapshot's generated_at, graded against official MLB StatsAPI batter game logs (hits >= 1,
PA > 0; the FC-ACC-014 retrieval of 2026-10-22, sha256 of batter_games.json recorded).
Prices are production-captured, not FC-OPS-001 authenticated. Outcomes are joined only after
all pregame fields are fixed. Hits 1+ only: the logs carry no RBI, so H+R+RBI cannot be graded.

Usage: python3 snapshot_replication.py <snapshot dir with YYYY-MM-DD.json> <batter_games.json>
"""
from __future__ import annotations

import glob
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SNAPSHOTS = {"2026-08-20": "101ba792a6fed61f15c73007589de29fe4d9a1e7", "2026-08-25": "741362be9e53114c156fb947409ff9573a9fd3fe",
             "2026-08-30": "0fd51d984af2b01576c9d851a54a825e71075a2a", "2026-09-04": "c74f0975ff85f69e16e2c36d8dc59a7e7435faff",
             "2026-09-09": "1b00cd7565d6d8943b87cd5f8a33b4b23d58a061", "2026-09-15": "04beb2da7895a7004cfaa297f8e7f0fc42708823",
             "2026-09-20": "f8baf7395dfa4c73c9d37b14da3004e28a121a30", "2026-09-25": "0f9660d62c839038ae88a62c38422da9fba12953"}
HOLD = 1.08


def T(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def candidates(snapshot):
    gen = T(snapshot["generated_at"])
    for p in snapshot.get("props") or []:
        pr = p.get("projection") or {}
        if (pr.get("stat"), pr.get("needs")) != ("hits", 1) or p.get("market_odds") is None:
            continue
        if p.get("lineup_assumed") or p.get("hit_probability") is None or not p.get("game_start"):
            continue
        if T(p["game_start"]) <= gen:
            continue                                   # pregame only
        yield p


def main(snap_dir, logs_path):
    logs = json.loads(Path(logs_path).read_text())
    G = {(r["game_pk"], r["pid"]): r for r in logs if r["season"] == 2026}
    rows, ungraded = [], 0
    for d in SNAPSHOTS:
        snap = json.loads((Path(snap_dir) / f"{d}.json").read_text())
        for p in candidates(snap):
            g = G.get((int(p["game_pk"]), int(p["player_id"])))
            if g is None or g["pa"] == 0:
                ungraded += 1
                continue
            rows.append({"date": d, "model": p["hit_probability"], "fair": p["market_implied"] / HOLD,
                         "odds": p["market_odds"], "status": p.get("recommendation_status"), "game_pk": int(p["game_pk"]),
                         "y": 1.0 if g["h"] >= 1 else 0.0})
    y = np.array([r["y"] for r in rows]); m = np.array([r["model"] for r in rows]); f = np.array([r["fair"] for r in rows])

    def ll(q):
        q = np.clip(q, 0.01, 0.99)
        return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))

    def band(x, edges):
        out = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            k = (x >= lo) & (x < hi)
            out.append({"lo": lo, "hi": hi, "n": int(k.sum()), "hit": float(y[k].mean()) if k.sum() else None,
                        "model": float(m[k].mean()) if k.sum() else None, "fair": float(f[k].mean()) if k.sum() else None})
        return out
    fav = (f >= 0.60) & (m >= 0.60) & (np.array([r["odds"] for r in rows]) >= -350)
    tp = np.array([r["status"] == "top_pick" for r in rows])
    res = {"snapshots": SNAPSHOTS, "batter_games_sha256": hashlib.sha256(Path(logs_path).read_bytes()).hexdigest(),
           "graded": len(rows), "ungraded_no_log_or_0pa": ungraded, "hit_rate": float(y.mean()),
           "mean_model": float(m.mean()), "mean_fair": float(f.mean()), "logloss_model": ll(m), "logloss_market": ll(f),
           "by_gap": band(m - f, [-1, 0, 0.05, 0.10, 1]), "by_fair": band(f, [0, 0.5, 0.6, 0.65, 0.7, 1]),
           "favorite_lane": {"n": int(fav.sum()), "hit": float(y[fav].mean())},
           "champion_top_pick_rows": {"n": int(tp.sum()), "hit": float(y[tp].mean()) if tp.sum() else None},
           "outcomes_joined_after_pregame_filter": True}
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "snapshot_replication.json").write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    r = main(sys.argv[1], sys.argv[2])
    print(json.dumps({k: v for k, v in r.items() if k not in ("snapshots", "by_gap", "by_fair")}, indent=1))
    print(json.dumps(r["by_gap"]), json.dumps(r["by_fair"]))

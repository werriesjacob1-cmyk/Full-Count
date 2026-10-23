#!/usr/bin/env python3
"""FC-ACC-016 Amendment A1 — eligibility/shortfall feasibility (research only, outcome-free).

Inputs:
  * archived production dashboard snapshots (`docs/data.json` at pinned main commits);
    their prices are NOT authenticated under FC-OPS-001, so nothing here is evidence
    about winners or ROI, only about whether candidates can structurally qualify;
  * the reproduced FC-ACC-014 feature rows (for frozen-challenger probabilities on
    the dates both sources cover).

Outcomes are never read: the feature rows' `y` is deleted on load, and dashboard
settlement fields are ignored.

Usage: python3 feasibility.py <snapshot dir> <fc_acc_014 out dir>
Snapshot files are named YYYY-MM-DD.json; their commit SHAs are listed in SNAPSHOTS.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import checker as C  # noqa: E402  (frozen FC-ACC-016 checker, imported read-only)

SNAPSHOTS = {  # last docs/data.json commit at or before 22:30 UTC on each date (main history)
    "2026-08-20": "101ba792a6fed61f15c73007589de29fe4d9a1e7",
    "2026-08-25": "741362be9e53114c156fb947409ff9573a9fd3fe",
    "2026-08-30": "0fd51d984af2b01576c9d851a54a825e71075a2a",
    "2026-09-04": "c74f0975ff85f69e16e2c36d8dc59a7e7435faff",
    "2026-09-09": "1b00cd7565d6d8943b87cd5f8a33b4b23d58a061",
    "2026-09-15": "04beb2da7895a7004cfaa297f8e7f0fc42708823",
    "2026-09-20": "f8baf7395dfa4c73c9d37b14da3004e28a121a30",
    "2026-09-25": "0f9660d62c839038ae88a62c38422da9fba12953",
}
WIDTH_SENSITIVITY = (0.02, 0.04, 0.06)   # hypothetical interval widths, ONLY to show what an interval would change


def key(p):
    pr = p.get("projection") or {}
    return pr.get("stat"), pr.get("needs")


def load_features(path):
    out = {}
    with open(path) as fh:
        for line in fh:
            r = json.loads(line)
            r.pop("y", None)
            out[(r["date"], int(r["game_pk"]), int(r["player_id"]))] = r["f"]
    return out


def main(snap_dir, feat_dir):
    snap_dir, feat_dir = Path(snap_dir), Path(feat_dir)
    models = C.load_frozen_models()
    feats = {"total_bases": load_features(feat_dir / "features_tb.jsonl"),
             "hits": load_features(feat_dir / "features_hits.jsonl")}
    res = {"outcomes_read": False, "prices_authenticated": False, "snapshots": {}}
    for date, sha in SNAPSHOTS.items():
        P = json.loads((snap_dir / f"{date}.json").read_text()).get("props") or []
        s = {"commit": sha, "rows": len(P), "status": dict(Counter(p.get("recommendation_status") for p in P)),
             "value_lane_N": sum(p.get("recommendation_status") == "value" for p in P),
             "value_lane_markets": dict(Counter("%s:%s" % key(p) for p in P if p.get("recommendation_status") == "value")),
             "top_pick_N": sum(p.get("recommendation_status") == "top_pick" for p in P),
             "top_pick_hits1_N": sum(p.get("recommendation_status") == "top_pick" and key(p) == ("hits", 1) for p in P)}
        # ---- E2: TB 2+ in the value lane
        tb = [p for p in P if key(p) == ("total_bases", 2)]
        e2 = Counter(rows=len(tb))
        for p in tb:
            e2["priced"] += p.get("market_odds") is not None
            e2["champion_interval_present"] += bool(p.get("prob_ci"))
            e2["champion_value_status"] += p.get("recommendation_status") == "value"
            f = feats["total_bases"].get((date, int(p["game_pk"]), int(p["player_id"]))) if p.get("player_id") else None
            if f is None or p.get("market_odds") is None:
                continue
            q, st = C.challenger_probability(models["total_bases"], f)
            if q is None:
                continue
            e2["challenger_scored_priced"] += 1
            ci = p.get("prob_ci")
            lo = (q - max(0.0, p["hit_probability"] - ci[0])) if ci else None
            e2["challenger_passes_value_gate_actual_production_interval"] += C.prob_gate("V", q, lo, p["market_odds"])
            for w in WIDTH_SENSITIVITY:
                e2[f"challenger_would_pass_if_interval_width_{w}"] += C.prob_gate("V", q, q - w, p["market_odds"])
        s["E2_tb2_value_lane"] = dict(e2)
        # ---- E1: hits 1+ in the Top Pick lane (non-probability gates as production applies them)
        hits = [p for p in P if key(p) == ("hits", 1)]
        e1 = Counter(rows=len(hits))
        for p in hits:
            base_ok = (p.get("market_odds") is not None and not p.get("lineup_assumed")
                       and p.get("reliability") in ("A", "B") and bool(p.get("prob_ci")))
            e1["nonprob_gates_and_interval_ok"] += base_ok
            f = feats["hits"].get((date, int(p["game_pk"]), int(p["player_id"]))) if p.get("player_id") else None
            if not base_ok or f is None:
                continue
            q, st = C.challenger_probability(models["hits"], f)
            if q is None:
                continue
            e1["challenger_scored"] += 1
            lo = q - max(0.0, p["hit_probability"] - p["prob_ci"][0])
            ch_ok = C.prob_gate("TP", q, lo, p["market_odds"])
            is_tp = p.get("recommendation_status") == "top_pick"
            e1["challenger_qualifies"] += ch_ok
            e1["champion_top_pick_and_challenger_qualifies"] += ch_ok and is_tp
            e1["champion_top_pick_challenger_rejects"] += (not ch_ok) and is_tp
            e1["challenger_only_qualifies"] += ch_ok and not is_tp
        s["E1_hits1_top_pick_lane"] = dict(e1)
        res["snapshots"][date] = s
    tot = Counter()
    for s in res["snapshots"].values():
        tot.update({f"E2_{k}": v for k, v in s["E2_tb2_value_lane"].items()})
        tot["value_lane_N"] += s["value_lane_N"]
        tot["top_pick_hits1_N"] += s["top_pick_hits1_N"]
    res["totals"] = dict(tot)
    return res


if __name__ == "__main__":
    out = main(sys.argv[1], sys.argv[2])
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "feasibility.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out["totals"], indent=1))
    for d, s in out["snapshots"].items():
        print(d, "valueN", s["value_lane_N"], s["value_lane_markets"], "| E2", s["E2_tb2_value_lane"], "| E1", s["E1_hits1_top_pick_lane"])

#!/usr/bin/env python3
"""FC-ACC-017 Phase 1: independent reconstruction of the published Top Pick ledger.

Sources (read-only, on main):
  * data/public_top_picks/registry.json - immutable publication registry (one entry per
    published Top Pick, with the frozen publication-time snapshot);
  * results/grades_<date>.json `public_top_picks` - the graded public picks per slate;
  * results/history.json `public_top_pick_totals` - the published running totals.

Never writes to any of them. Uses no canonical replay outcome.
Output: research/fc_acc_017/out/ledger.json (rows + reconciliation).
"""
from __future__ import annotations

import glob
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
COMPARE = ("hit_probability", "market_odds", "market_implied", "market_edge", "prob_ci", "reliability",
           "lineup_assumed", "stat", "projection", "game_pk", "player_id", "recommendation_status")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build(repo=REPO):
    reg_p, hist_p = repo / "data/public_top_picks/registry.json", repo / "results/history.json"
    reg = json.loads(reg_p.read_text())["entries"]
    hist = json.loads(hist_p.read_text())
    files = sorted(glob.glob(str(repo / "results/grades_*.json")))
    rows, recon, seen = [], {"problems": []}, Counter()
    per_day = {}
    for f in files:
        g = json.loads(Path(f).read_text())
        pubs = g.get("public_top_picks") or []
        cnt = Counter(p.get("grade") for p in pubs)
        declared = g.get("public_top_pick_counts")
        if pubs or declared:
            per_day[g["date"]] = dict(cnt)
            if declared:
                exp = {"hits": cnt.get("hit", 0), "misses": cnt.get("miss", 0), "voids": cnt.get("void", 0),
                       "ungraded": cnt.get("ungraded", 0)}
                if any(declared.get(k, 0) != v for k, v in exp.items()):
                    recon["problems"].append({"date": g["date"], "declared": declared, "recomputed": exp})
        for p in pubs:
            seen[p["id"]] += 1
            e = reg.get(p["id"])
            if e is None:
                recon["problems"].append({"id": p["id"], "problem": "GRADED_BUT_NOT_IN_REGISTRY"})
                continue
            snap = e["snapshot"]
            diffs = [k for k in COMPARE if k in snap and snap.get(k) != p.get(k)]
            if diffs:
                recon["problems"].append({"id": p["id"], "problem": "SNAPSHOT_MISMATCH", "fields": diffs})
            rows.append({"id": p["id"], "slate_date": e["slate_date"], "graded_file_date": g["date"],
                         "first_published_at": e["first_published_at"], "snapshot": snap,
                         "grade": p.get("grade"), "actual": p.get("actual"), "threshold": p.get("threshold"),
                         "post": {k: p.get(k) for k in ("actual_pa_est", "actual_ab", "was_substitute", "batting_order",
                                                        "game_innings", "shortened_game", "fair_test", "opportunity",
                                                        "settlement_state", "settlement_authority")}})
    dup = [k for k, v in seen.items() if v > 1]
    missing = sorted(set(reg) - set(seen))
    c = Counter(r["grade"] for r in rows)
    tot = hist.get("public_top_pick_totals") or {}
    recon.update({
        "registry_entries": len(reg), "graded_rows": len(rows), "duplicate_graded_ids": dup,
        "registry_ids_never_graded": missing,
        "grades": dict(c),
        "history_totals": tot,
        "history_matches": (c.get("hit", 0), c.get("miss", 0), c.get("void", 0), c.get("ungraded", 0)) ==
                           (tot.get("hits"), tot.get("misses"), tot.get("voids"), tot.get("ungraded")),
        "slate_files_with_public_picks": len(per_day),
        "source_sha256": {"registry.json": sha(reg_p), "history.json": sha(hist_p),
                          "grades_files_concat": hashlib.sha256(b"".join(Path(f).read_bytes() for f in files)).hexdigest(),
                          "grades_files": len(files)},
    })
    return rows, recon, per_day


if __name__ == "__main__":
    rows, recon, per_day = build()
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out" / "ledger.json").write_text(json.dumps({"reconciliation": recon, "per_day": per_day, "rows": rows},
                                                         indent=1, default=str))
    print(json.dumps({k: v for k, v in recon.items() if k != "problems"}, indent=1, default=str))
    print("problems:", len(recon["problems"]), recon["problems"][:5])
    sys.exit(0)

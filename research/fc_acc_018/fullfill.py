#!/usr/bin/env python3
"""FC-ACC-018 descriptive (NOT a claim): same-count comparison restricted to slates where BOTH
B and C filled the cap-comparable target exactly. Slate inclusion depends only on candidate supply
(payload completeness), never on outcomes. Post hoc relative to PREREG; labelled descriptive."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import evaluate as EV  # noqa: E402


def main(repo, raw):
    sel = json.loads((HERE / "out" / "selection.json").read_text())
    U = json.loads((HERE / "out" / "universe.json").read_text())
    rows_by = {(x["slate"], x["id"]): x for x in U["candidates"]}
    champ = EV.SA.champion(repo)
    import glob
    pub = {}
    for f in sorted(glob.glob(str(Path(repo) / "results/grades_*.json"))):
        for p in json.loads(Path(f).read_text()).get("public_top_picks") or []:
            pub[p["id"]] = {"hit": "WIN", "miss": "LOSS"}.get(p.get("grade"), "VOID")
    bat, pit = EV.logs(raw)
    out = {"slates": []}
    tot = {k: [0, 0, 0.0] for k in ("A_cap", "B", "C")}
    for d, s in sorted(sel["slates"].items()):
        c = s["arms"]["cap"]
        if not (c["target"] and c["B"]["filled"] == c["target"] == c["C"]["filled"]):
            continue
        A = {r["id"]: r for r in champ[d]}
        arms = {"A_cap": ([A[i] for i in s["A_cap_ids"]], lambda r: pub.get(r["id"], "VOID")),
                "B": ([rows_by[(d, i)] for i in c["B"]["ids"]], lambda r: EV.grade(r, bat, pit)),
                "C": ([rows_by[(d, i)] for i in c["C"]["ids"]], lambda r: EV.grade(r, bat, pit))}
        rec = {"slate": d, "target": c["target"], "P1": d not in EV.P_DEV_DATES and d < EV.P_DEV_FROM}
        for k, (rs, f) in arms.items():
            st = EV.arm_stats(rs, f)
            rec[k] = {"wins": st["wins"], "settled": st["settled"], "mkt_exp": round(st["market_expected_wins"], 2)}
            tot[k][0] += st["wins"]; tot[k][1] += st["settled"]; tot[k][2] += st["market_expected_wins"]
        out["slates"].append(rec)
    out["totals"] = {k: {"wins": v[0], "settled": v[1], "hit_rate": v[0] / v[1] if v[1] else None, "market_expected_wins": v[2]}
                     for k, v in tot.items()}
    (HERE / "out" / "fullfill_descriptive.json").write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    o = main(sys.argv[1], sys.argv[2])
    print(json.dumps(o, indent=1))

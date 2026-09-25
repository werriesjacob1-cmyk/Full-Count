#!/usr/bin/env python3
"""Print markdown tables from team_context_report.json (or the DEV report)."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def fmt(p, key="paired_delta_mean"):
    if not p or not p.get("n_matched"):
        return "n/a"
    ci = p.get("paired_delta_ci95") or [float("nan")] * 2
    return f"{p[key]:+.3f} [{ci[0]:+.3f}, {ci[1]:+.3f}]"


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "team_context_report.json"
    r = json.loads((HERE / name).read_text())
    parts = [p for p in ("DEV_2016_2022", "HOLDOUT_2023_2025", "FRESH_2026")
             if p in next(iter(r["markets"].values()))["configs"]["ALL"]["vs_b0"]["partitions"]]
    print("### Team volume intermediate (MAE delta vs strictly-prior team mean)\n")
    print("| target | config | " + " | ".join(parts) + " |")
    print("|---|---|" + "---|" * len(parts))
    for target, configs in r["team_level"].items():
        for cfg, res in configs.items():
            cells = [fmt(res["partitions"].get(p)) for p in parts]
            print(f"| {target} | {cfg} | " + " | ".join(cells) + " |")
    for market, mr in r["markets"].items():
        print(f"\n### {market} (scale k={mr['scale_control_k']})\n")
        print("| config | params | partition | n | activation | vs B0 | vs scale control | vs volume base | bias (pred-actual) |")
        print("|---|---|---|---|---|---|---|---|---|")
        for cfg, res in mr["configs"].items():
            prm = res["params"]
            ptxt = f"a={prm['alpha']}, b={prm['beta']}, m={prm['pseudo_games']:g}"
            for p in parts:
                vb = res["vs_b0"]["partitions"].get(p, {})
                vk = res["vs_scale_control"]["partitions"].get(p, {})
                vv = res.get("vs_volume_base", {}).get("partitions", {}).get(p)
                bias = res["bias"].get(p, {}).get("mean_pred_minus_actual")
                print(f"| {cfg} | {ptxt} | {p} | {vk.get('n_matched', 0)} | "
                      f"{vk.get('activation_share', 0):.3f} | {fmt(vb)} | {fmt(vk)} | "
                      f"{fmt(vv) if vv else '-'} | {bias:+.3f} |" if bias is not None else "")


if __name__ == "__main__":
    main()

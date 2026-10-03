#!/usr/bin/env python3
"""Does the production per-family calibration layer improve accuracy?
EXPLORATORY. No fitting: both probabilities are the pipeline's own outputs.

For final-run board picks (regular season, hit/miss) where the pipeline
applied a calibration (`calibrated_by` set) and kept the pre-calibration
value (`raw_hit_probability`), compare log loss and Brier of the calibrated
`hit_probability` against the raw value, per calibrated family, with a
game-clustered percentile bootstrap (B=2000, seed 20261001) on the paired
log-loss difference (calibrated minus raw; negative = calibration helps).

    python3 engineering/mlb_accuracy_forward_chain_20261001/calib_layer.py
"""
from __future__ import annotations

import json
import os
import random
from collections import defaultdict

from forward_chain import DATA_SHA, FIRST, LAST, clip, git_json, git_ls, ll, paired, y_of

HERE = os.path.dirname(os.path.abspath(__file__))


def load():
    out = []
    for path in sorted(git_ls("results")):
        base = os.path.basename(path)
        if not (base.startswith("grades_") and base.endswith(".json")):
            continue
        d = base[len("grades_"):-5]
        if not FIRST <= d <= LAST:
            continue
        for x in git_json(path).get("picks") or []:
            y = y_of(x.get("grade"))
            if (x.get("recommendation_status") is None or y is None or not x.get("calibrated_by")
                    or x.get("raw_hit_probability") is None or x.get("hit_probability") is None):
                continue
            out.append({"date": d, "game_pk": str(x.get("game_pk")), "family": x["calibrated_by"],
                        "cal": x["hit_probability"], "raw": x["raw_hit_probability"], "y": y})
    return out


def main():
    rows = load()
    rng = random.Random(20261001)
    fams = defaultdict(list)
    for r in rows:
        fams[r["family"]].append(r)
    report = {"evidence_class": "EXPLORATORY_DESCRIPTIVE", "data_sha": DATA_SHA, "n_rows": len(rows), "families": {}}
    for f, rs in sorted(fams.items()):
        n = len(rs)
        report["families"][f] = {
            "n": n, "realized": sum(r["y"] for r in rs) / n,
            "mean_raw": sum(r["raw"] for r in rs) / n, "mean_cal": sum(r["cal"] for r in rs) / n,
            "ll_raw": sum(ll(r["raw"], r["y"]) for r in rs) / n, "ll_cal": sum(ll(r["cal"], r["y"]) for r in rs) / n,
            "brier_raw": sum((clip(r["raw"]) - r["y"]) ** 2 for r in rs) / n,
            "brier_cal": sum((clip(r["cal"]) - r["y"]) ** 2 for r in rs) / n,
            "paired_ll_cal_minus_raw": paired(rs, "cal", "raw", rng) if n >= 20 else None}
    report["pooled_paired_ll_cal_minus_raw"] = paired(rows, "cal", "raw", rng)
    with open(os.path.join(HERE, "calib_layer_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()

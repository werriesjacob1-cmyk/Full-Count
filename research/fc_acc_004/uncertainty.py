"""Pitcher- and game-clustered paired uncertainty for fixed FC-ACC-004 diagnostic."""
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

root = Path(__file__).resolve().parent
report = json.loads((root / "report.json").read_text())
rows = list(csv.DictReader((root / "model_rows.csv").open(newline="")))
rng = np.random.default_rng(20261016)
out = {}
for year in (2025, 2026):
    subset = [r for r in rows if int(r["season"]) == year]
    out[year] = {}
    for target in ("outs", "ks"):
        rate = "out_rate" if target == "outs" else "k_rate"
        coeff = report["coefficients_2024"][target]
        delta = {}
        for r in subset:
            base = float(r["baseline_bf"]) * float(r[rate])
            alt = base + coeff[0] + coeff[1] * float(r["pitch_slope_20"]) + coeff[2] * int(r["short_rest"])
            actual = int(r[target])
            delta[(r["pitcher_id"], r["game_pk"])] = abs(alt - actual) - abs(base - actual)
        by_pitcher = defaultdict(list)
        by_game = defaultdict(list)
        for r in subset:
            d = delta[(r["pitcher_id"], r["game_pk"])]
            by_pitcher[r["pitcher_id"]].append(d)
            by_game[r["game_pk"]].append(d)
        result = {"paired_delta_mae": float(np.mean(list(delta.values())))}
        for cluster_name, groups in (("pitcher", by_pitcher), ("game", by_game)):
            arrays = [np.asarray(v) for v in groups.values()]
            sums = np.asarray([a.sum() for a in arrays])
            counts = np.asarray([len(a) for a in arrays])
            ids = rng.integers(len(arrays), size=(2000, len(arrays)))
            samples = sums[ids].sum(axis=1) / counts[ids].sum(axis=1)
            result[cluster_name + "_ci95"] = np.percentile(samples, [2.5, 97.5]).tolist()
            result[cluster_name + "_clusters"] = len(arrays)
        out[year][target] = result
(root / "uncertainty.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))

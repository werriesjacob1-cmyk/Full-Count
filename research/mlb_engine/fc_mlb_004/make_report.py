#!/usr/bin/env python3
"""Render out/REPORT.md mechanically from out/RESULTS.json + out/FROZEN_FIT_004.json (no hand-edited numbers)."""
import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
r = json.load(open(os.path.join(OUT, "RESULTS.json")))
f = json.load(open(os.path.join(OUT, "FROZEN_FIT_004.json")))
ci = lambda d: f"{d['diff_pp']:+.2f} [{d['ci95_pp'][0]:+.2f}, {d['ci95_pp'][1]:+.2f}]"  # noqa: E731
L = [f"# FC-MLB-004 — pitcher contact-suppression: {r['verdict_K10_P1_vs_P0']} (K=10, P1 vs P0)", "",
     f"**{r['label']}**", "",
     f"- Criteria: {r['criteria']}",
     f"- FC-MLB-002 frozen fit sha256 `{r['fc_mlb_002_fit_sha256']}` (P0 = its CH1a, unchanged)",
     f"- FC-MLB-004 frozen fit sha256 `{r['frozen_fit_004_sha256']}`; kpx={r['kpx']}, kd={r['kd']}; P2 valid point-in-time: {r['p2_valid']}",
     f"- Fidelity gate (P0, CH0 vs FC-MLB-002 b52ac418ba predictions): {r['fidelity_gate']}",
     f"- Population: {r['population']}", "",
     "## Frozen 2025 fit", "",
     f"- kpx grid (2025 Jul16–end holdout mean log-lik): {f['grid_kpx']}; kd grid: {f['grid_kd']}"]
for m, c in f["coef"].items():
    L.append(f"- {m}: {', '.join(f'{k}={v:.3f}' for k, v in c.items())}")
L += ["", "| 2025 split model (fit Apr15–Jul15) | holdout logloss/PA |", "|---|---|"]
for m, h in f["holdout_2025"].items():
    L.append(f"| {m} | {-h['holdout_ll_jul16_end']:.6f} |")
L += ["", "## Primary — realized Hits 1+ at equal volume (top-min(K, eligible) per date; paired 95% date-block CI, pp)", "",
      "| K | selected each | dates short | CH0 | P0 | P1 | P2 | P1−P0 | P2−P0 | P2−P1 | P1−CH0 | P0−CH0 |",
      "|---|---|---|---|---|---|---|---|---|---|---|---|"]
for K, row in r["primary"].items():
    rt = row["rates"]
    L.append(f"| {K[1:]} | {row['counts']['selected_rows']} | {row['counts']['dates_short']} | {rt['CH0']:.4f} | {rt['P0']:.4f} | "
             f"{rt['P1']:.4f} | {rt.get('P2', float('nan')):.4f} | {ci(row['P1_minus_P0'])} | {ci(row['P2_minus_P0'])} | "
             f"{ci(row['P2_minus_P1'])} | {ci(row['P1_minus_CH0'])} | {ci(row['P0_minus_CH0'])} |")
L += ["", f"Reduces early instability (Apr–Jun P1−CH0 gain exceeds P0−CH0 by ≥1 pp): {r['reduces_early_instability']}", "",
      "## Stability (K=10, pp; logloss gain ×1e4 per player-game)", "",
      "| group | dates | rows | P1−P0 | P2−P0 | P2−P1 | P0−CH0 | P1−CH0 | ll P1 vs P0 | ll P2 vs P0 |", "|---|---|---|---|---|---|---|---|---|---|"]
for g, v in r["stability_K10"].items():
    L.append(f"| {g} | {v['dates']} | {v['rows']} | {v['P1_minus_P0_pp']:+.2f} | {v['P2_minus_P0_pp']:+.2f} | {v['P2_minus_P1_pp']:+.2f} | "
             f"{v['P0_minus_CH0_pp']:+.2f} | {v['P1_minus_CH0_pp']:+.2f} | {1e4 * v['ll_gain_P1_vs_P0']:+.1f} | {1e4 * v['ll_gain_P2_vs_P0']:+.1f} |")
pq = r["probability_quality"]
L += ["", "## Probability quality (all player-games)", "", "| model | logloss | brier | mean pred |", "|---|---|---|---|"]
for n in ("CH0", "P0", "P1", "P2"):
    if n in pq:
        L.append(f"| {n} | {pq[n]['logloss']:.5f} | {pq[n]['brier']:.5f} | {pq[n]['mean_pred']:.4f} |")
L += ["", f"- Correlations: { {k: round(v, 3) for k, v in pq['corr'].items()} }; corr(Lp, Lpx) starter = {pq['feature_corr_Lp_Lpx_sp']:.3f}"]
for n in ("P0", "P1", "P2"):
    if n in pq:
        L.append(f"- Calibration {n} deciles (pred, act): {[(d['pred'], d['act']) for d in pq[n]['calibration_deciles']]}")
m = r["market"]
L += ["", "## Market subset (FanDuel Over 0.5 Hits, last pregame snapshot; one-sided de-vig: APPROXIMATE)", "",
      f"- Source: {m['props_source']}; identity: {m['identity']}",
      f"- Rows {m['rows']} over {m.get('dates')} dates; logloss { {k: round(v, 5) for k, v in m.get('logloss', {}).items()} }"]
for K in ("topK3", "topK5"):
    if K in m:
        t = m[K]
        L.append(f"- {K}: selected {t['counts']['selected_rows']} each; rates { {k: round(v, 4) for k, v in t['rates'].items()} }; "
                 + "; ".join(f"{k} {ci(v)}" for k, v in t.items() if "_minus_" in k))
L += ["", "## Artifacts", "", f"- {r['artifacts']}", "",
      "No production, selector, pick, ledger or V3 change. Development evidence only; confirmation is prospective.", "",
      "Alligator."]
open(os.path.join(OUT, "REPORT.md"), "w").write("\n".join(L) + "\n")
print("\n".join(L))

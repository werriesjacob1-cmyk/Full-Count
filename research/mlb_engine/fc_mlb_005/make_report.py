#!/usr/bin/env python3
"""Render out/REPORT.md mechanically from out/RESULTS.json + out/FROZEN_FIT_005.json (no hand-edited numbers)."""
import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
r = json.load(open(os.path.join(OUT, "RESULTS.json")))
f = json.load(open(os.path.join(OUT, "FROZEN_FIT_005.json")))
ci = lambda d: f"{d['diff_pp']:+.2f} [{d['ci95_pp'][0]:+.2f}, {d['ci95_pp'][1]:+.2f}]"  # noqa: E731
L = [f"# FC-MLB-005 — team conversion residual isolation: {r['verdict_K10_D1_vs_D0']} (K=10, D1 vs D0)", "",
     f"**{r['label']}**", "",
     f"- Criteria: {r['criteria']}",
     f"- Terminology: {r['terminology']}",
     f"- Fits: FC-MLB-002 `{r['fc_mlb_002_fit_sha256'][:12]}` (D0 = CH1a), FC-MLB-004 `{r['fc_mlb_004_fit_sha256'][:12]}` (k_d source), "
     f"FC-MLB-005 `{r['frozen_fit_005_sha256']}`; k_d = {r['kd']}",
     f"- Fidelity gate (D0, CH0 vs FC-MLB-004 P0/CH0 @ 46712d8091): {r['fidelity_gate']}",
     f"- Population: {r['population']}", "",
     "## Frozen 2025 fit", ""]
for m, c in f["coef"].items():
    L.append(f"- {m}: {', '.join(f'{k}={v:.4f}' for k, v in c.items())}")
L += [f"- D0 refit check: max |Δ| vs frozen CH1a = {f['d0_refit_check']['max_abs_dev_vs_frozen_CH1a']} (tol {f['d0_refit_check']['tol']})",
      "- 2025 split holdout logloss/PA (fit Apr15–Jul15, score Jul16–end): "
      + "; ".join(f"{k} {-v['holdout_ll_jul16_end']:.6f}" for k, v in f["holdout_2025"].items()), "",
      "## Primary — realized Hits 1+ at equal volume (top-min(K, eligible) per date; paired 95% date-block CI, pp)", "",
      "| K | selected each | dates short | CH0 | D0 | D1 | D1−D0 | D0−CH0 | D1−CH0 |", "|---|---|---|---|---|---|---|---|---|"]
for K, row in r["primary"].items():
    rt = row["rates"]
    L.append(f"| {K[1:]} | {row['counts']['selected_rows']} | {row['counts']['dates_short']} | {rt['CH0']:.4f} | {rt['D0']:.4f} | "
             f"{rt['D1']:.4f} | {ci(row['D1_minus_D0'])} | {ci(row['D0_minus_CH0'])} | {ci(row['D1_minus_CH0'])} |")
L += ["", f"- K5/K20 under the same rule (descriptive): {r['verdict_K5_K20_same_rule_descriptive']}",
      f"- Half gap (Jul–Sep minus Apr–Jun gain vs CH0, pp): {r['half_gap_pp']}", "",
      "## Stability (K=10, pp; logloss gain ×1e4 per player-game)", "",
      "| group | dates | rows | D1−D0 | D0−CH0 | D1−CH0 | ll D1 vs D0 |", "|---|---|---|---|---|---|---|"]
for g, v in r["stability_K10"].items():
    L.append(f"| {g} | {v['dates']} | {v['rows']} | {v['D1_minus_D0_pp']:+.2f} | {v['D0_minus_CH0_pp']:+.2f} | "
             f"{v['D1_minus_CH0_pp']:+.2f} | {1e4 * v['ll_gain_D1_vs_D0']:+.1f} |")
pq = r["probability_quality"]
L += ["", "## Probability quality", "", "| model | logloss | brier | mean pred |", "|---|---|---|---|"]
for n in ("CH0", "D0", "D1"):
    L.append(f"| {n} | {pq[n]['logloss']:.5f} | {pq[n]['brier']:.5f} | {pq[n]['mean_pred']:.4f} |")
L += ["", f"- corr(D0, D1) = {pq['corr_D0_D1']:.4f}; mean top-10 overlap per date = {pq['topK10_overlap_share_D0_D1']:.3f}"]
for n in ("D0", "D1"):
    L.append(f"- Calibration {n} deciles (pred, act): {[(d['pred'], d['act']) for d in pq[n]['calibration_deciles']]}")
L += ["", "## Mechanism diagnostics (descriptive only)", ""]
for k, v in r["mechanism"].items():
    L.append(f"- **{k}**: {json.dumps(v, default=float)}")
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
      "No production, selector, pick, ledger or V3 change. Development evidence only.", "", "Alligator."]
open(os.path.join(OUT, "REPORT.md"), "w").write("\n".join(L) + "\n")
print("\n".join(L))

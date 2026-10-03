#!/usr/bin/env python3
"""Render out/REPORT.md mechanically from out/RESULTS.json + out/FROZEN_FIT_006.json (no hand-edited numbers)."""
import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
r = json.load(open(os.path.join(OUT, "RESULTS.json")))
f = json.load(open(os.path.join(OUT, "FROZEN_FIT_006.json")))
ci = lambda d: f"{d['diff_pp']:+.2f} [{d['ci95_pp'][0]:+.2f}, {d['ci95_pp'][1]:+.2f}]"  # noqa: E731
J = lambda x: json.dumps(x, default=float)                                                # noqa: E731
L = [f"# FC-MLB-006 — PA event world model: {r['verdict_K10_E1_vs_P0']} (K=10, E1 vs P0)", "",
     f"**{r['label']}**", "",
     f"- Criteria: {r['criteria']}",
     f"- Frozen fit sha256 `{r['frozen_fit_006_sha256']}`; baseline fit `{r['baseline_fit_sha256']}`",
     f"- Fidelity gate (P0, CH0 vs FC-MLB-004 @ 46712d8091): {r['fidelity_gate']}",
     f"- Population: {r['population']}", "",
     "## Frozen 2025 fit", "",
     f"- Categories {f['categories']}; n PA {f['n_pa_2025']}; convergence {f['convergence']}",
     f"- Method-of-moments prior strengths (2025): {J(f['prior_strengths_mom_2025'])}", "",
     "| category | alpha | beta (batter) | gamma (pitcher) | delta (same hand) |", "|---|---|---|---|---|"]
for c, v in f["theta"].items():
    L.append(f"| {c} | {v['alpha']:.3f} | {v['beta_batter']:.3f} | {v['gamma_pitcher']:.3f} | {v['delta_same_hand']:.3f} |")
L += ["", f"- 2025 split check (descriptive): {J({k: v for k, v in f['split_check_2025_descriptive'].items() if k != 'convergence_first_half'})}", "",
      "## Primary — realized Hits 1+ at equal volume (top-min(K, eligible) per date; paired 95% date-block CI, pp)", "",
      "| K | selected each | dates | short | CH0 | P0 | E1 | E1−P0 | rule | P0−CH0 | E1−CH0 | overlap | added hit | removed hit |",
      "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
for K, row in r["primary"].items():
    rt, s, c = row["rates"], row["selection_E1_vs_P0"], row["counts"]
    L.append(f"| {K[1:]} | {c['selected_rows']} | {c['dates']} | {c['dates_short']} {c['shortfalls'] or ''} | {rt['CH0']:.4f} | {rt['P0']:.4f} | "
             f"{rt['E1']:.4f} | {ci(row['E1_minus_P0'])} | {row['verdict_rule_E1_vs_P0']} | {ci(row['P0_minus_CH0'])} | "
             f"{ci(row['E1_minus_CH0'])} | {s['overlap_share']:.3f} | {s['added_hit_rate']:.4f} (n={s['added_n']}) | "
             f"{s['removed_hit_rate']:.4f} (n={s['removed_n']}) |")
L += ["", "Concentration of selections:"]
for K, row in r["primary"].items():
    L.append(f"- {K}: {J(row['selection_E1_vs_P0']['concentration'])}")
L += ["", "## Stability (K=10, pp; logloss gain ×1e4 per player-game)", "",
      "| group | dates | rows | E1−P0 | P0−CH0 | E1−CH0 | ll E1 vs P0 |", "|---|---|---|---|---|---|---|"]
for g, v in r["stability_K10"].items():
    L.append(f"| {g} | {v['dates']} | {v['rows']} | {v['E1_minus_P0_pp']:+.2f} | {v['P0_minus_CH0_pp']:+.2f} | "
             f"{v['E1_minus_CH0_pp']:+.2f} | {1e4 * v['ll_gain_E1_vs_P0']:+.1f} |")
wm = r["world_model"]
L += ["", "## World-model diagnostics", "",
      f"- Invariants: {J(wm['invariants'])}",
      f"- 2026 PA level (actual pitcher): {J(wm['pa_level_2026_actual_pitcher'])}",
      f"- PA-level calibration deciles (pred, act): {J(wm['pa_level_calibration_deciles'])}",
      f"- Opponent information: {J(wm['opponent_information'])}",
      f"- Rank correlation: {J(wm['rank'])}", "",
      "| player-game model | logloss | brier | mean pred |", "|---|---|---|---|"]
for n, v in wm["player_game_probability_quality"].items():
    L.append(f"| {n} | {v['logloss']:.5f} | {v['brier']:.5f} | {v['mean_pred']:.4f} |")
L.append(f"\nActual Hits 1+ rate {wm['actual_h1_rate']:.4f}.")
for n, v in wm["player_game_probability_quality"].items():
    L.append(f"- Calibration {n} (pred, act): {[(d['pred'], d['act']) for d in v['calibration_deciles']]}")
m = r["market"]
L += ["", "## Market subset (secondary; one-sided de-vig APPROXIMATE)", "",
      f"- Source: {m['props_source']}; identity: {m['identity']}",
      f"- Rows {m['rows']} over {m.get('dates')} dates; logloss {J({k: round(v, 5) for k, v in m.get('logloss', {}).items()})}"]
for K in ("topK3", "topK5"):
    if K in m:
        t = m[K]
        L.append(f"- {K}: selected {t['counts']['selected_rows']} each; rates {J({k: round(v, 4) for k, v in t['rates'].items()})}; "
                 + "; ".join(f"{k} {ci(v)}" for k, v in t.items() if "_minus_" in k))
L += ["", "## Artifacts", "", f"- {r['artifacts']}", "",
      "No production, selector, pick, ledger, site, V3 or NFL change. Development evidence only.", "", "Alligator."]
open(os.path.join(OUT, "REPORT.md"), "w").write("\n".join(L) + "\n")
print("\n".join(L))

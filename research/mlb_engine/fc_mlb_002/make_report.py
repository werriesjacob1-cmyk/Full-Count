#!/usr/bin/env python3
"""Render out/REPORT.md mechanically from out/RESULTS.json + out/FROZEN_FIT_2025.json (no hand-edited numbers)."""
import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
r = json.load(open(os.path.join(OUT, "RESULTS.json")))
f = json.load(open(os.path.join(OUT, "FROZEN_FIT_2025.json")))
L = [f"# FC-MLB-002 — Hits 1+ batter×pitcher challenger: {r['verdict_K10_CH1_vs_CH0']} (K=10, CH1 vs CH0)", "",
     f"**{r['label']}**", "",
     f"- Criteria: {r['criteria']}",
     f"- Champion code: main `{r['champion_code']}` (production functions imported unchanged)",
     f"- Frozen 2025 fit sha256 `{r['frozen_fit_sha256']}`; shrinkage k = {r['k']}",
     f"- Population: {r['population']}", "",
     "## Frozen 2025 coefficients (PA-level logistic; fitted on 2025 only)", "",
     "| model | coefficients | 2025 Jul16–Sep holdout logloss/PA (fit Apr15–Jul15) |", "|---|---|---|"]
for m, c in f["coef"].items():
    L.append(f"| {m} | {', '.join(f'{k}={v:.3f}' for k, v in c.items())} | {-f['split_check_2025'][m]['holdout_ll_jul16_sep']:.5f} |")
L += ["", "## Repair (Codex findings 1-4)", "", f"- {r['repair']}", "",
      "## Primary — realized Hits 1+ rate at equal volume (top-min(K, eligible) per date, paired by date; 95% date-block CI)", "",
      "| K | selected rows each | dates short | CH0 | CH0b (Δ, CI) | CH1a (Δ, CI) | CH1 (Δ, CI) | CH1−CH1a (CI) |",
      "|---|---|---|---|---|---|---|---|"]
for K, row in r["primary"].items():
    f_ = lambda n: f"{row[n]['rate']:.4f} ({row[n]['diff_pp']:+.2f}, [{row[n]['ci95_pp'][0]:+.2f}, {row[n]['ci95_pp'][1]:+.2f}])"  # noqa: E731
    d_ = row["CH1_minus_CH1a"]
    L.append(f"| {K[1:]} | {row['counts']['selected_rows']} | {row['counts']['dates_short']} {row['counts']['shortfalls'] or ''} | "
             f"{row['CH0_rate']:.4f} | {f_('CH0b')} | {f_('CH1a')} | {f_('CH1')} | {d_['diff_pp']:+.2f} [{d_['ci95_pp'][0]:+.2f}, {d_['ci95_pp'][1]:+.2f}] |")
L += ["", "Stability (K=10, pp vs CH0): " + "; ".join(f"{k}: CH1 {v['CH1_minus_CH0_pp']:+.2f} / CH1a {v['CH1a_minus_CH0_pp']:+.2f} ({v['dates']} dates)" for k, v in r["stability_K10"].items()), "",
      "## Secondary diagnostics", "", "| model | logloss | brier | mean pred |", "|---|---|---|---|"]
s = r["secondary"]
for m in ("CH0", "CH0b", "CH1a", "CH1"):
    L.append(f"| {m} | {s[m]['logloss']:.5f} | {s[m]['brier']:.5f} | {s[m]['mean_pred']:.4f} |")
L += ["", "| diagnostic family | CH0 logloss (mean pred) | CH1 logloss (mean pred) | actual |", "|---|---|---|---|"]
for t in ("h2", "tb2"):
    L.append(f"| {t} | {s['CH0_' + t]['logloss']:.5f} ({s['CH0_' + t]['mean_pred']:.4f}) | "
             f"{s['CH1_' + t]['logloss']:.5f} ({s['CH1_' + t]['mean_pred']:.4f}) | {s['CH0_' + t]['actual']:.4f} |")
L += ["", f"- Correlation CH0 vs CH1: {s['corr_CH0_CH1']:.3f}; mean top-10 overlap per date: {s['topK10_overlap_share']:.3f}",
      f"- Subgroup logloss gain CH1 vs CH0 (per batter-game): {s['subgroups']}",
      f"- Calibration CH0 deciles (pred, act): {[(d['pred'], d['act']) for d in s['calibration_CH0_deciles']]}",
      f"- Calibration CH1 deciles (pred, act): {[(d['pred'], d['act']) for d in s['calibration_CH1_deciles']]}", "",
      "## Market-relative (FanDuel Over 0.5 Hits, last pregame snapshot, devigged with production assumed hold)", ""]
m = r["market"]
L.append(f"- Source: {m.get('props_source')}; {m.get('devig')}")
L.append(f"- Identity: {m.get('identity')}")
L.append(f"- Matched player-games: {m.get('rows_with_price')} over {m.get('dates')} dates; logloss {m.get('logloss')}; mean {m.get('mean')}")
for K in ("topK3", "topK5"):
    if K in m:
        t = m[K]
        L.append(f"- {K}: selected {t['counts']['selected_rows']} each; CH0 {t['CH0']:.4f}, CH1a {t['CH1a']:.4f}, CH1 {t['CH1']:.4f}, market {t['mkt_fair']:.4f}; "
                 + "; ".join(f"{c} {t[c]['diff_pp']:+.2f} [{t[c]['ci95_pp'][0]:+.2f}, {t[c]['ci95_pp'][1]:+.2f}]"
                             for c in ("CH1a_minus_CH0", "CH1_minus_CH0", "CH1a_minus_mkt_fair", "CH1_minus_mkt_fair")))
for k in ("disagreement_coef_CH0", "disagreement_coef_CH1", "CH1_disagreement_quintiles"):
    if k in m:
        L.append(f"- {k}: {m[k]}")
L += ["", "## Artifacts", "", f"- {r['artifacts']}", "",
      "No production, selector, pick, ledger or V3 change. Development evidence only; confirmation is prospective.", "",
      "Alligator."]
open(os.path.join(OUT, "REPORT.md"), "w").write("\n".join(L) + "\n")
print("\n".join(L))

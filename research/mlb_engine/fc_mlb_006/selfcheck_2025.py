"""Mechanics-only smoke run of the evaluation code path on the 2025 TRAINING season (in-sample; not evidence).
No 2026 data is touched; no fidelity gate (reference predictions exist only for 2026)."""
import json, os, sys
import numpy as np
import fc_mlb_006 as W
F = W.F
sys.path.insert(0, F.CHAMP)
import prop_probability as pp, generate_picks as gp
f2 = json.load(open(W.M5.X.F002_FIT)); fz = json.load(open(os.path.join(W.OUT, "FROZEN_FIT_006.json")))
kz, theta = fz["prior_strengths_mom_2025"], W.theta_from(fz["theta"])
prov, pa, T, bg, pa6, T6 = W.load_all()
ev, info, _ = W.build_population(2025, pa, T, bg, gp)
ev = W.predict_baseline(ev, T, f2["k"], f2["coef"]["CH1a"], pp)
h, q_sp, q_pen, q_mix, _ = W.event_components(ev, T6, kz, theta)
ev["E1_raw"], ev["E1"] = W.aggregate(h, ev["proj_pa"], ev["lg_h1"], pp)
h2, *_ = W.event_components(ev, T6, kz, theta, zero_pitcher=True)
allq = np.vstack([q_sp, q_pen])
out = {"label": "SELFCHECK 2025 IN-SAMPLE: mechanics only, not evidence", "rows": int(len(ev)),
       "max_abs_sum_minus_1": float(np.abs(allq.sum(1) - 1).max()), "finite": bool(np.isfinite(allq).all()),
       "h_range": [float(h.min()), float(h.max())], "sel": W.selection_detail(ev, "E1", "P0", 10)["overlap_share"],
       "nan_E1": int(ev["E1"].isna().sum())}
json.dump(out, open(os.path.join(W.OUT, "SELFCHECK_2025_INSAMPLE.json"), "w"), indent=1)
print(out)

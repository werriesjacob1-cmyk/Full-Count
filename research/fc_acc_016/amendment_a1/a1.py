"""FC-ACC-016 Amendment A1 — experiment bindings, E2 ROI verdict and structural gate.

Imports the frozen v1.0.0 checker read-only and verifies, on import, that none of
the frozen v1.0.0 files changed. It adds no selection rule and relaxes no gate.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
V1 = HERE.parent
sys.path.insert(0, str(V1))
A1 = json.loads((HERE / "amendment_a1.json").read_text())

for _name, _sha in A1["frozen_v1_files_sha256"].items():
    if hashlib.sha256((V1 / _name).read_bytes()).hexdigest() != _sha:
        raise RuntimeError(f"frozen FC-ACC-016 v1.0.0 file changed: {_name}")

import checker as C  # noqa: E402

EXPERIMENTS = {k: (v["lane"], v["challenger"]) for k, v in A1["experiments"].items()}


def evaluate_experiment(exp, bundles, rng_seed=None):
    """Run one preregistered experiment. Lane/market are bound by the amendment and
    cannot be chosen by the caller, so E1 and E2 can never be mixed or pooled."""
    if exp not in EXPERIMENTS:
        raise ValueError(f"unknown experiment {exp!r}; only {sorted(EXPERIMENTS)} exist")
    lane, market = EXPERIMENTS[exp]
    res = C.evaluate(bundles, lane, market, rng_seed=rng_seed)
    res["experiment"] = exp
    res["verdict_label"] = C.verdict_label(res) if exp == "E1" else roi_verdict_label(res)
    return res


def top_pick_hit_rate(results):
    """Hit rate over Top Pick selections only. Refuses anything but E1 (lane TP)."""
    if results.get("experiment") != "E1" or results.get("lane") != "TP":
        raise ValueError("Top Pick hit-rate denominators may contain lane-TP (E1) selections only")
    t = results["totals"]
    return {arm: (t[f"{arm}_wins"] / t[f"{arm}_stakes"] if t[f"{arm}_stakes"] else None) for arm in ("A", "B", "C")}


def roi_verdict_label(res):
    """E2 preregistered rule: ROI-primary superiority at equal value-lane volume."""
    r = A1["e2_roi_rule"]
    if res["noncomparable_share"] is not None and res["noncomparable_share"] > r["max_noncomparable_unit_share"]:
        return "NOT_EVALUABLE_INTEGRITY"
    if res["volume_share"] is not None and res["volume_share"] < r["min_realized_volume_share"]:
        return "NOT_EVALUABLE_VOLUME_SHORTFALL"
    if res["discordant"]["D_settled"] < r["min_discordant_settled"]:
        return "NOT_EVALUABLE_INSUFFICIENT_DISCORDANCE"
    cal = res.get("challenger_calibration")
    cb = C.EV["calibration_bounds"]
    cal_ok = cal is not None and abs(cal["citl"]) <= cb["citl_abs_max"] and cb["slope_min"] <= cal["slope"] <= cb["slope_max"]
    ci = res.get("ci_delta_roi_slate")
    ok = ci is not None and ci[0] > r["roi_ci_lower_gt"] and cal_ok
    return "SUPPORTED_PENDING_AUTHORIZATION" if ok else "NOT_SUPPORTED"


def e2_structural_gate(feasibility):
    """Outcome-free pre-capture gate for E2 (amendment_a1.json e2_structural_gate)."""
    g = A1["e2_structural_gate"]
    snaps = feasibility["snapshots"]
    rows = sum(s["E2_tb2_value_lane"].get("rows", 0) for s in snaps.values())
    with_ci = sum(s["E2_tb2_value_lane"].get("champion_interval_present", 0) for s in snaps.values())
    elig = sum(s["E2_tb2_value_lane"].get("challenger_passes_value_gate_actual_production_interval", 0) for s in snaps.values())
    full = [s for s in snaps.values()    # full slates where the frozen challenger could be scored
            if s["rows"] >= 1000 and s["E2_tb2_value_lane"].get("challenger_scored_priced", 0) > 0]
    per_full = (sum(s["E2_tb2_value_lane"].get("challenger_passes_value_gate_actual_production_interval", 0) for s in full)
                / len(full)) if full else 0.0
    reasons = []
    if len(snaps) < g["min_snapshots"]:
        reasons.append("TOO_FEW_SNAPSHOTS")
    if not rows or with_ci / rows < g["min_share_tb2_rows_with_production_interval"]:
        reasons.append("TB2_PRODUCTION_INTERVAL_ABSENT")
    if per_full < g["min_projected_eligible_tb2_challenger_rows_per_full_slate"]:
        reasons.append("TB2_VALUE_LANE_ELIGIBILITY_BELOW_MINIMUM")
    return {"status": "EVALUABLE" if not reasons else "NOT_EVALUABLE_STRUCTURAL", "reasons": reasons,
            "tb2_rows": rows, "tb2_rows_with_production_interval": with_ci,
            "tb2_challenger_eligible_actual_rules": elig, "eligible_per_full_slate": per_full,
            "snapshots": len(snaps), "full_slate_snapshots": len(full)}

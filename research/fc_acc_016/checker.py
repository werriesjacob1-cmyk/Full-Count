#!/usr/bin/env python3
"""FC-ACC-016 prospective portfolio-challenge protocol checker (research only, inactive).

What it does: given per-decision-unit bundles (interface `fc-acc-016-bundle-1`, see
PROTOCOL.md §8), it
  1. verifies the frozen model lock and the pinned production gate code;
  2. builds arms A (actual champion), B (champion + frozen challenger competing for the
     same N slots under identical operational gates) and C (market-only) WITHOUT reading
     outcomes;
  3. settles the sealed selections and computes the preregistered outcomes and verdict labels.

What it never does: fetch data, parse sportsbook pages, export champion decisions, write
to any production path, fit or recalibrate anything. Offer acceptance (FC-OPS-001 /
PR #226) and champion export (PR #230) are Codex-owned inputs; this module only consumes
records already carrying their labels.

Usage: python3 checker.py --self-check   (runs the labeled synthetic suite)
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
import prop_probability as pp  # noqa: E402  (production gate math, read-only; sha pinned in protocol.json)

PROTOCOL = json.loads((HERE / "protocol.json").read_text())
LOCK = json.loads((HERE / "model_lock.json").read_text())
G = PROTOCOL["gates"]
EV = PROTOCOL["evaluation"]


def _sha_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _sha_obj(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _t(s):
    if not isinstance(s, str):
        return None
    try:
        v = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return v.astimezone(timezone.utc) if v.tzinfo else None


# ------------------------------------------------------------------ locks
def verify_gate_code():
    pins = PROTOCOL["production_gate_code"]
    bad = [f for f in ("prop_probability.py", "recommendation.py") if _sha_file(REPO / f) != pins[f + "_sha256"]]
    if bad:
        raise RuntimeError(f"production gate code changed since preregistration: {bad}")
    return True


def load_frozen_models():
    path = REPO / LOCK["artifact_path"]
    if _sha_file(path) != LOCK["artifact_sha256"] or LOCK["artifact_sha256"] != PROTOCOL["frozen_model_sha256"]:
        raise RuntimeError("frozen model artifact sha256 mismatch")
    models = json.loads(path.read_text())
    for k, e in LOCK["entries"].items():
        m = models[k]
        if (_sha_obj(m) != e["entry_sha256"] or m["keys"] != e["feature_order"]
                or _sha_obj(m["coef"]) != e["coef_sha256"]):
            raise RuntimeError(f"frozen model entry {k} does not match the lock")
    return models


def challenger_probability(entry, features):
    """Frozen transform only. A feature never missing in training is critical: if it is
    missing now the challenger cannot score (fail closed, never imputed)."""
    keys = entry["keys"]
    miss_keys = set(entry["missing_indicator_for"])
    x = []
    for k in keys:
        v = features.get(k)
        v = None if v is None or (isinstance(v, float) and not math.isfinite(v)) else float(v)
        if v is None and k not in miss_keys:
            return None, f"MISSING_CRITICAL:{k}"
        x.append(np.nan if v is None else v)
    x = np.array(x)
    med, mu, sd, coef = (np.array(entry[k]) for k in ("median", "mu", "sd", "coef"))
    miss = [keys.index(k) for k in entry["missing_indicator_for"]]
    z = (np.concatenate([np.where(np.isnan(x), med, x), np.isnan(x[miss]).astype(float)]) - mu) / sd
    return float(1 / (1 + np.exp(-(coef[0] + z @ coef[1:])))), "OK"


# ------------------------------------------------------------------ gates
def offer_reasons(row, unit, first_pitch):
    o = row.get("offer")
    if not o:
        return ["NO_OFFER"]
    r = []
    if o.get("join_label") != G["offer_join_label_required"]:
        r.append("OFFER_NOT_MATCHED")
    if o.get("authenticated") is not True:
        r.append("OFFER_NOT_AUTHENTICATED")
    if o.get("sportsbook") != G["sportsbook"]:
        r.append("WRONG_BOOK")
    if (o.get("market"), o.get("needs"), o.get("line"), o.get("side")) != \
            (row.get("market"), row.get("needs"), row.get("line"), row.get("wager_side")):
        r.append("OFFER_IDENTITY_MISMATCH")
    a = o.get("american")
    if not isinstance(a, int) or isinstance(a, bool) or abs(a) < 100:
        r.append("INVALID_PRICE")
    obs, cut = _t(o.get("observed_at")), _t(unit.get("decision_cutoff_at"))
    if obs is None or cut is None:
        r.append("MISSING_TIME")
    else:
        if obs > cut:
            r.append("OFFER_AFTER_CUTOFF")
        elif (cut - obs).total_seconds() > G["max_offer_age_seconds"]:
            r.append("STALE_OFFER")
        if first_pitch is None or obs >= first_pitch:
            r.append("OFFER_NOT_PREGAME")
    return r


def nonprob_reasons(row, unit, lane, first_pitch):
    L = PROTOCOL["lanes"][lane]
    r = offer_reasons(row, unit, first_pitch)
    if row.get("data_fresh") is not True:
        r.append("DATA_NOT_FRESH")
    if L["lineup_confirmed_required"] and row.get("lineup_confirmed") is not True:
        r.append("LINEUP_NOT_CONFIRMED")
    if L["min_reliability"] and row.get("reliability") not in L["min_reliability"]:
        r.append("RELIABILITY_BELOW_MINIMUM")
    return r


def challenger_input_reasons(row, unit, slate_date):
    c = row.get("challenger")
    if not c:
        return ["NO_CHALLENGER_INPUTS"]
    r = []
    if row.get("lineup_confirmed") is not True:
        r.append("LINEUP_NOT_CONFIRMED")
    if c.get("slot_source") != G["slot_source_required"]:
        r.append("SLOT_NOT_FROM_CONFIRMED_LINEUP")
    if c.get("sp_source") != G["sp_source_required"]:
        r.append("SP_NOT_ANNOUNCED_PROBABLE_AT_DECISION")
    built, cut = _t(c.get("features_built_at")), _t(unit.get("decision_cutoff_at"))
    if built is None or cut is None or built > cut:
        r.append("FEATURES_AFTER_CUTOFF")
    if not isinstance(c.get("features_data_through"), str) or c["features_data_through"] >= slate_date:
        r.append("FEATURES_USE_SAME_DAY_OR_LATER_DATA")
    return r


def prob_gate(lane, p, p_lo, american):
    L = PROTOCOL["lanes"][lane]
    v = pp.value_verdict(p, american, prob_lo=p_lo, min_roi=G["min_roi"], require_robust=G["require_robust"])
    ok = v["verdict"] == "BET"
    if L["probability_floor_min"] is not None and p < L["probability_floor_min"]:
        ok = False
    if L["probability_cap_exclusive"] is not None and p >= L["probability_cap_exclusive"]:
        ok = False
    if ok and L["suspect_blocks"] and pp.market_agreement(p, american)["agreement"] == "SUSPECT":
        ok = False
    return ok


def _order_key(lane, p, p_lo, american, cid):
    lo = p_lo if p_lo is not None else -1.0
    roi_lo = pp.expected_roi(lo, american) if p_lo is not None else -9.0
    if PROTOCOL["lanes"][lane]["ordering"] == "PESSIMISTIC_PROBABILITY_DESC":
        return (-lo, -roi_lo, cid)
    return (-roi_lo, -lo, cid)


# ------------------------------------------------------------------ selection (outcome-blind)
def select_unit(unit, lane, challenger_market, models):
    """Return arms and diagnostics for one decision unit. Never reads outcomes."""
    ch = PROTOCOL["challengers"][challenger_market]
    if ch["role"] not in ("PRIMARY", "SECONDARY"):
        raise ValueError("strikeout entries are research diagnostics and never enter an arm")
    entry = models[challenger_market]
    M = (ch["market"], ch["needs"])
    status = PROTOCOL["lanes"][lane]["champion_status"]
    starts = {g["game_pk"]: _t(g.get("first_pitch_at")) for g in unit.get("games", [])}
    rows = unit.get("rows", [])
    by_id = {r["canonical_id"]: r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError("duplicate canonical_id in unit")
    out = {"unit_id": unit["unit_id"], "slate_date": unit["slate_date"], "lane": lane, "market": challenger_market,
           "integrity": [], "comparable": True, "reasons": [], "challenger_exclusions": defaultdict(int)}
    # integrity
    prep, cut, seal = (_t(unit.get(k)) for k in ("prepared_at", "decision_cutoff_at", "sealed_at"))
    first = min((s for s in starts.values() if s), default=None)
    if not all((prep, cut, seal, first)) or None in starts.values():
        out["integrity"].append("MISSING_TIME")
    else:
        if prep > cut:
            out["integrity"].append("PREPARED_AFTER_CUTOFF")
        if any((s - prep).total_seconds() < PROTOCOL["final_pregame_margin_seconds"] for s in starts.values()):
            out["integrity"].append("NOT_FINAL_PREGAME_RUN")
        if seal > first:
            out["integrity"].append("SEALED_AFTER_FIRST_PITCH")
        if cut > first:
            out["integrity"].append("CUTOFF_AFTER_FIRST_PITCH")
    if unit.get("coverage_status") not in ("COMPLETE", "ZERO_PICK"):
        out["integrity"].append(f"COVERAGE_{unit.get('coverage_status')}")
    if unit.get("model_artifact_sha256") != LOCK["artifact_sha256"]:
        out["integrity"].append("MODEL_SHA_NOT_RECORDED_OR_MISMATCH")
    # arm A: the actual champion, never re-derived
    A = sorted(r["canonical_id"] for r in rows if r.get("recommendation_status") == status)
    N = len(A)
    out["N"] = N
    for cid in A:
        if offer_reasons(by_id[cid], unit, starts.get(by_id[cid]["game_pk"])):
            out["reasons"].append(f"CHAMPION_OFFER_INVALID:{cid}")
    if out["integrity"]:
        out["comparable"] = False
        out["reasons"].insert(0, "INTEGRITY_FAILURE")
    elif any(x.startswith("CHAMPION_OFFER_INVALID") for x in out["reasons"]):
        out["comparable"] = False
    # arm B
    pool, reserved = [], set()
    for cid in A:
        r = by_id[cid]
        if (r["market"], r["needs"]) == M:
            continue                      # champion rows of the challenger market are re-governed below
        lo = (r.get("prob_ci") or [None])[0]
        pool.append((_order_key(lane, r["hit_probability"], lo, r["offer"]["american"] if r.get("offer") else -10000, cid),
                     cid, "CHAMPION"))
        reserved.add(r["player_id"])
    scored = {}
    for r in rows:
        if (r["market"], r["needs"]) != M:
            continue
        cid = r["canonical_id"]
        why = nonprob_reasons(r, unit, lane, starts.get(r["game_pk"])) + challenger_input_reasons(r, unit, unit["slate_date"])
        p = None
        if not why:
            p, st = challenger_probability(entry, r["challenger"]["features"])
            if p is None:
                why.append(st)
        if why:
            for w in why:
                out["challenger_exclusions"][w] += 1
            continue
        scored[cid] = p
        ci = r.get("prob_ci")
        p_lo = (p - max(0.0, r["hit_probability"] - ci[0])) if (ci and r.get("hit_probability") is not None) else None
        a = r["offer"]["american"]
        if not prob_gate(lane, p, p_lo, a):
            out["challenger_exclusions"]["FAILS_PROBABILITY_OR_PRICE_GATE"] += 1
            continue
        if r["player_id"] in reserved:
            out["challenger_exclusions"]["PLAYER_ALREADY_SELECTED"] += 1
            continue
        pool.append((_order_key(lane, p, p_lo, a, cid), cid, "CHALLENGER"))
    pool.sort()
    B, players = [], set()
    for _, cid, src in pool:
        if len(B) == N:
            break
        pid = by_id[cid]["player_id"]
        if src == "CHALLENGER" and pid in players:
            out["challenger_exclusions"]["PLAYER_ALREADY_SELECTED"] += 1
            continue
        B.append(cid)
        players.add(pid)
    # arm C: market-only over the same lane universe (no model, no probability gate)
    def market_only(restrict):
        cand = []
        for r in rows:
            if restrict and (r["market"], r["needs"]) != M:
                continue
            if nonprob_reasons(r, unit, lane, starts.get(r["game_pk"])):
                continue
            cand.append((-pp.implied_probability(r["offer"]["american"]), r["canonical_id"]))
        cand.sort()
        sel, seen = [], set()
        for _, cid in cand:
            if len(sel) == N:
                break
            if by_id[cid]["player_id"] in seen:
                continue
            sel.append(cid)
            seen.add(by_id[cid]["player_id"])
        return sorted(sel)
    C = market_only(False)
    CM = market_only(True)
    out.update({"A": A, "B": sorted(B), "C": C, "C_market": CM,
                "shortfall": {"B": N - len(B), "C": N - len(C), "C_market": N - len(CM)},
                "added": sorted(set(B) - set(A)), "removed": sorted(set(A) - set(B)),
                "challenger_scored": scored,
                "a_player_duplicates": len(A) - len({by_id[c]["player_id"] for c in A})})
    out["challenger_exclusions"] = dict(out["challenger_exclusions"])
    return out


# ------------------------------------------------------------------ settlement and evaluation
def settle(ids, unit, outcomes):
    by_id = {r["canonical_id"]: r for r in unit["rows"]}
    s = {"wins": 0, "losses": 0, "void": 0, "unresolved": 0, "profit": 0.0}
    for cid in ids:
        st = (outcomes.get(cid) or {}).get("status", "UNRESOLVED")
        if st == "WIN":
            s["wins"] += 1
            s["profit"] += pp.decimal_odds(by_id[cid]["offer"]["american"]) - 1.0
        elif st == "LOSS":
            s["losses"] += 1
            s["profit"] -= 1.0
        elif st in ("PUSH", "VOID"):
            s["void"] += 1
        else:
            s["unresolved"] += 1
    s["stakes"] = s["wins"] + s["losses"]
    return s


def _calib(p, y):
    if len(p) < 20 or y.min() == y.max():
        return None
    lp = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))
    w = np.zeros(2)
    X = np.column_stack([np.ones_like(lp), lp])
    for _ in range(50):
        q = 1 / (1 + np.exp(-X @ w))
        H = (X * (q * (1 - q))[:, None]).T @ X + 1e-9 * np.eye(2)
        w -= np.linalg.solve(H, X.T @ (q - y))
    return {"n": int(len(p)), "citl": float(y.mean() - p.mean()), "slope": float(w[1]),
            "brier": float(np.mean((p - y) ** 2)),
            "logloss": float(-np.mean(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1))))}


def evaluate(bundles, lane, challenger_market, rng_seed=None):
    models = load_frozen_models()
    verify_gate_code()
    rng = np.random.default_rng(EV["bootstrap_seed"] if rng_seed is None else rng_seed)
    units, per_slate = [], defaultdict(lambda: defaultdict(float))
    n_units = noncomp = shortfall_units = pending = 0
    req_N = realized_B = 0
    D_settled = added_w = removed_w = added_s = removed_s = 0
    cal_p, cal_y = [], []
    for b in bundles:
        outcomes = b.get("outcomes", {})
        for u in b["units"]:
            sel = select_unit(u, lane, challenger_market, models)
            n_units += 1
            if not sel["comparable"]:
                noncomp += 1
                units.append({**sel, "status": "NONCOMPARABLE"})
                continue
            st = {arm: settle(sel[arm], u, outcomes) for arm in ("A", "B", "C", "C_market")}
            if any(st[a]["unresolved"] for a in st):
                pending += 1
                units.append({**sel, "status": "PENDING_SETTLEMENT"})
                continue
            if any(sel["shortfall"][a] for a in ("B", "C")):
                shortfall_units += 1
            req_N += sel["N"]
            realized_B += len(sel["B"])
            for cid in sel["added"]:
                s = (outcomes.get(cid) or {}).get("status")
                if s in ("WIN", "LOSS"):
                    added_s += 1; added_w += s == "WIN"
            for cid in sel["removed"]:
                s = (outcomes.get(cid) or {}).get("status")
                if s in ("WIN", "LOSS"):
                    removed_s += 1; removed_w += s == "WIN"
            for cid, p in sel["challenger_scored"].items():
                s = (outcomes.get(cid) or {}).get("status")
                if s in ("WIN", "LOSS"):
                    cal_p.append(p); cal_y.append(1.0 if s == "WIN" else 0.0)
            ps = per_slate[sel["slate_date"]]
            for arm in ("A", "B", "C", "C_market"):
                for k in ("wins", "stakes", "profit"):
                    ps[f"{arm}_{k}"] += st[arm][k]
            units.append({**sel, "status": "SETTLED", "settled": st})
    D_settled = added_s + removed_s
    slates = sorted(per_slate)
    tot = {k: sum(per_slate[s][k] for s in slates) for k in
           [f"{a}_{m}" for a in ("A", "B", "C", "C_market") for m in ("wins", "stakes", "profit")]}

    def roi(a, T):
        return T[f"{a}_profit"] / T[f"{a}_stakes"] if T[f"{a}_stakes"] else None

    res = {"lane": lane, "market": challenger_market, "units": n_units, "noncomparable_units": noncomp,
           "pending_units": pending, "shortfall_units": shortfall_units, "slates_settled": len(slates),
           "requested_N": req_N, "realized_B": realized_B, "totals": tot,
           "delta_wins_B_minus_A": tot["B_wins"] - tot["A_wins"], "delta_wins_C_minus_A": tot["C_wins"] - tot["A_wins"],
           "roi": {a: roi(a, tot) for a in ("A", "B", "C", "C_market")},
           "discordant": {"added_settled": added_s, "added_wins": added_w, "removed_settled": removed_s,
                          "removed_wins": removed_w, "D_settled": D_settled}}
    res["delta_roi_B_minus_A"] = (res["roi"]["B"] - res["roi"]["A"]) if None not in (res["roi"]["B"], res["roi"]["A"]) else None
    # slate-clustered bootstrap (equal counts preserved within slate)
    if len(slates) >= 2:
        V = np.array([[per_slate[s][f"{a}_{m}"] for a in ("A", "B") for m in ("wins", "stakes", "profit")] for s in slates])
        d = rng.integers(0, len(slates), size=(EV["bootstrap_reps"], len(slates)))
        S = V[d].sum(1)
        dw = S[:, 3] - S[:, 0]
        with np.errstate(invalid="ignore", divide="ignore"):
            droi = S[:, 5] / S[:, 4] - S[:, 2] / S[:, 1]
        droi = droi[np.isfinite(droi)]
        res["ci_delta_wins_slate"] = [float(np.percentile(dw, 2.5)), float(np.percentile(dw, 97.5))]
        res["ci_delta_roi_slate"] = [float(np.percentile(droi, 2.5)), float(np.percentile(droi, 97.5))] if len(droi) else None
        half = len(slates) // 2
        res["halves_delta_wins"] = [float(sum(per_slate[s]["B_wins"] - per_slate[s]["A_wins"] for s in slates[:half])),
                                    float(sum(per_slate[s]["B_wins"] - per_slate[s]["A_wins"] for s in slates[half:]))]
    res["challenger_calibration"] = _calib(np.array(cal_p), np.array(cal_y)) if cal_p else None
    res["volume_share"] = realized_B / req_N if req_N else None
    res["noncomparable_share"] = noncomp / n_units if n_units else None
    res["verdict_label"] = verdict_label(res)
    res["unit_records"] = units
    return res


def verdict_label(res):
    """Preregistered labels (PROTOCOL.md §7). SUPPORTED is evidence, never promotion."""
    if res["noncomparable_share"] is not None and res["noncomparable_share"] > EV["max_noncomparable_unit_share"]:
        return "NOT_EVALUABLE_INTEGRITY"
    if res["volume_share"] is not None and res["volume_share"] < EV["min_realized_volume_share"]:
        return "NOT_EVALUABLE_VOLUME_SHORTFALL"
    if res["discordant"]["D_settled"] < EV["min_discordant_settled_for_evaluable"]:
        return "NOT_EVALUABLE_INSUFFICIENT_DISCORDANCE"
    cal = res.get("challenger_calibration")
    cb = EV["calibration_bounds"]
    cal_ok = cal is not None and abs(cal["citl"]) <= cb["citl_abs_max"] and cb["slope_min"] <= cal["slope"] <= cb["slope_max"]
    ok = (res.get("ci_delta_wins_slate") and res["ci_delta_wins_slate"][0] > 0
          and res.get("ci_delta_roi_slate") and res["ci_delta_roi_slate"][0] >= EV["roi_noninferiority_margin_per_unit"]
          and (res["delta_roi_B_minus_A"] or 0) >= 0
          and all(h > 0 for h in res.get("halves_delta_wins", [0])) and cal_ok)
    return "SUPPORTED_PENDING_AUTHORIZATION" if ok else "NOT_SUPPORTED"


def serving_skew(entry, feature_rows):
    """Outcome-free training/serving parity check (PROTOCOL.md §5.4). The frozen mu of a
    __missing column is the training missing rate; feature mu/sd come from the artifact."""
    keys, miss = entry["keys"], entry["missing_indicator_for"]
    mu, sd, med = np.array(entry["mu"]), np.array(entry["sd"]), np.array(entry["median"])
    b = EV["serving_skew_bounds"]
    X = np.array([[np.nan if f.get(k) is None else float(f[k]) for k in keys] for f in feature_rows])
    flags = []
    for j, k in enumerate(keys):
        col = X[:, j]
        train_miss = mu[len(keys) + miss.index(k)] if k in miss else 0.0
        fm = float(np.isnan(col).mean())
        if abs(fm - train_miss) > b["missing_rate_abs_diff_max"]:
            flags.append({"feature": k, "check": "missing_rate", "train": float(train_miss), "forward": fm})
        filled = np.where(np.isnan(col), med[j], col)
        shift = (filled.mean() - mu[j]) / sd[j]
        if abs(shift) > b["standardized_mean_shift_abs_max"]:
            flags.append({"feature": k, "check": "mean_shift_sd", "shift": float(shift)})
    return {"n": len(feature_rows), "flags": flags, "pass": not flags}


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        import synthetic
        report = synthetic.run_suite()
        (HERE / "out").mkdir(exist_ok=True)
        (HERE / "out" / "self_check.json").write_text(json.dumps(report, indent=1, default=str))
        print(json.dumps({k: v["pass"] for k, v in report["cases"].items()}, indent=1))
        print("ALL PASS" if report["all_pass"] else "FAILURES")
        sys.exit(0 if report["all_pass"] else 1)

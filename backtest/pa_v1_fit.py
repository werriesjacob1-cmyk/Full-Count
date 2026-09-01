#!/usr/bin/env python3
"""pa_v1_fit.py -- produce the ONE deterministic PA-v1 fitted artifact.

PA-v1 is the residual opportunity challenger that earned EARNS_PROSPECTIVE_SHADOW
on the locked canonical-v2 historical experiment. This module fits it exactly
once, on all certified historical data available BEFORE the prospective launch,
and freezes the result.

Why fit here rather than inside the live build: the live scorer must be pure and
read-only over a frozen artifact. Fitting from outcomes inside every live build
would silently make the challenger a moving target, and a moving challenger
cannot be evaluated prospectively at all.

IMMUTABILITY. Once prospective evaluation begins this artifact is frozen. There
is no automatic refit from forward outcomes. Any later refit is PA-v2, a new
evidence regime with its own effective_from, and it cannot retroactively replace
PA-v1 receipts.

    python3 backtest/pa_v1_fit.py --rows <certified rows.jsonl> --out <artifact.json>
"""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys
from collections import defaultdict
from datetime import datetime, timezone

PROTOCOL_VERSION = "prospective-hits-pa-v1"
MARKET = "hits"
MIN_CELL_N = 200
PA_STATES = ["0", "1", "2", "3", "4", "5", "6+"]
HITTER_MARKETS = frozenset({
    "hits", "total_bases", "hits_runs_rbis", "home_run", "singles",
    "doubles", "triples", "rbis", "runs", "hard_hit_105",
})
# The nine whole-date quarantines certified on the research view. Recorded so the
# artifact can prove which dates could not have contributed to the fit.
CERTIFIED_QUARANTINED_DATES = (
    "2024-05-22", "2024-07-11", "2024-08-26", "2024-08-28", "2025-05-21",
    "2025-06-07", "2025-07-02", "2025-08-03", "2026-06-17",
)


def derive_batting_order(lineup_slot):
    if lineup_slot is None:
        return None
    order = round(9.0 - lineup_slot * 8.0 / 100.0)
    return order if 1 <= order <= 9 else None


def pa_bucket_fine(actual_pa):
    if actual_pa is None:
        return "unknown"
    return "6+" if actual_pa >= 6 else str(int(actual_pa))


def getaway_day_group(signals):
    v = signals.get("getaway_day")
    if v is None:
        return None
    return "getaway_day" if v < 0 else "not_getaway_day"


def days_rest_group(signals):
    v = signals.get("days_rest")
    if v is None:
        return None
    if v <= 0:
        return "0_days_rest"
    if v == 1:
        return "1_day_rest"
    if v <= 3:
        return "2-3_days_rest"
    return "4plus_days_rest"


def joint_key(signals):
    order = derive_batting_order(signals.get("lineup_slot"))
    if order is None:
        return None
    dr, ga = days_rest_group(signals), getaway_day_group(signals)
    if dr is None or ga is None:
        return None
    return (order, dr, ga)


def _cell_token(key):
    """Stable JSON-safe token for a joint cell, so the artifact round-trips."""
    return "|".join(str(p) for p in key)


def fit(rows, train_cutoff):
    graded = [r for r in rows if r.get("outcome") in (0, 1)]
    hitter = [r for r in graded if r.get("prop_type") in HITTER_MARKETS
              and (r.get("date") or "") <= train_cutoff]

    seen, player_games = set(), []
    for r in hitter:
        k = (r.get("date"), r.get("game_pk"), r.get("player_id"))
        if k in seen:
            continue
        seen.add(k)
        player_games.append(r)

    jc, jt = defaultdict(lambda: defaultdict(int)), defaultdict(int)
    oc, ot = defaultdict(lambda: defaultdict(int)), defaultdict(int)
    for r in player_games:
        sig = r.get("signals") or {}
        pa = r.get("actual_pa")
        if pa is None:
            continue
        state = pa_bucket_fine(pa)
        order = derive_batting_order(sig.get("lineup_slot"))
        if order is not None:
            oc[order][state] += 1
            ot[order] += 1
        k = joint_key(sig)
        if k is not None:
            jc[k][state] += 1
            jt[k] += 1

    joint_table = {_cell_token(k): dict({s: round(jc[k].get(s, 0) / t, 6) for s in PA_STATES}, _n=t)
                   for k, t in jt.items() if t >= MIN_CELL_N}
    order_table = {str(o): dict({s: round(oc[o].get(s, 0) / t, 6) for s in PA_STATES}, _n=t)
                   for o, t in ot.items()}

    hc = defaultdict(lambda: {"n": 0, "hits": 0})
    for r in hitter:
        if r.get("prop_type") != MARKET:
            continue
        pa = r.get("actual_pa")
        if pa is None:
            continue
        b = hc[pa_bucket_fine(pa)]
        b["n"] += 1
        b["hits"] += r["outcome"]
    hit_rate_given_pa = {s: (round(v["hits"] / v["n"], 6) if v["n"] else None)
                         for s, v in hc.items()}

    return {
        "joint_pa_table": joint_table,
        "order_pa_table": order_table,
        "hit_rate_given_pa": hit_rate_given_pa,
        "train_player_games": len(player_games),
        "train_market_rows": sum(1 for r in hitter if r.get("prop_type") == MARKET),
        "joint_cells_fit": len(joint_table),
        "order_cells_fit": len(order_table),
    }


def _sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def _code_sha():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=30).stdout.strip() or None
    except Exception:
        return None


def canonical_json(obj):
    """Deterministic serialization -- the artifact hash must be reproducible."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_artifact(rows_path, effective_from=None, train_cutoff=None):
    rows, dates = [], set()
    with open(rows_path) as fh:
        for line in fh:
            r = json.loads(line)
            rows.append(r)
            if r.get("date"):
                dates.add(r["date"])
    cutoff = train_cutoff or max(dates)
    tables = fit(rows, cutoff)

    body = {
        "protocol_version": PROTOCOL_VERSION,
        "model": "residual_order_days_rest_getaway",
        "market": MARKET,
        "min_cell_n": MIN_CELL_N,
        "pa_states": PA_STATES,
        "fallback_semantics": {
            "sparse_or_missing_joint_cell": "order-only PA distribution",
            "order_unavailable": "PA score unavailable -> caller substitutes the "
                                 "champion's own probability as a NEUTRAL rank "
                                 "fallback; the candidate is never removed",
            "unpriced_pa_state": "state skipped, remaining mass renormalized",
        },
        "training_input": {
            "certified_rows_path": os.path.basename(rows_path),
            "certified_rows_sha256": _sha256_file(rows_path),
            "certified_rows_count": len(rows),
            "certified_date_min": min(dates) if dates else None,
            "certified_date_max": max(dates) if dates else None,
            "certified_dates_with_rows": len(dates),
            "quarantined_dates_excluded_by_certification": list(CERTIFIED_QUARANTINED_DATES),
            "train_cutoff_inclusive": cutoff,
        },
        "fitting_code": {
            "file": "backtest/pa_v1_fit.py",
            "repo_code_sha": _code_sha(),
        },
        "tables": tables,
    }
    body["fitted_artifact_sha256"] = hashlib.sha256(
        canonical_json(body).encode("utf-8")).hexdigest()
    body["effective_from"] = effective_from or datetime.now(timezone.utc).isoformat()
    body["immutability"] = (
        "FROZEN once prospective evaluation begins. No automatic refit from "
        "forward outcomes. A later refit is PA-v2 with its own effective_from "
        "and cannot retroactively replace PA-v1 receipts."
    )
    return body


# ---- the pure live scorer: read-only over the frozen artifact ----
def score(signals, artifact):
    """P(hit) = sum_k P(PA=k | context) * P(hit | PA=k). Returns None when the
    batting order is unavailable; the caller substitutes the champion probability
    as a neutral rank fallback and never drops the candidate."""
    tables = artifact["tables"]
    joint, order_t = tables["joint_pa_table"], tables["order_pa_table"]
    hr = tables["hit_rate_given_pa"]
    k = joint_key(signals or {})
    dist = joint.get(_cell_token(k)) if k else None
    if dist is None:
        order = derive_batting_order((signals or {}).get("lineup_slot"))
        dist = order_t.get(str(order)) if order is not None else None
    if not dist:
        return None
    tot = w = 0.0
    for s in artifact["pa_states"]:
        p_pa, p_hit = dist.get(s, 0.0), hr.get(s)
        if p_hit is None or p_pa <= 0:
            continue
        tot += p_pa * p_hit
        w += p_pa
    return round(tot / w, 6) if w > 0 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--effective-from")
    ap.add_argument("--train-cutoff")
    a = ap.parse_args()
    art = build_artifact(a.rows, a.effective_from, a.train_cutoff)
    with open(a.out, "w") as fh:
        fh.write(canonical_json(art))
    print(f"fitted_artifact_sha256 = {art['fitted_artifact_sha256']}")
    print(f"train_cutoff           = {art['training_input']['train_cutoff_inclusive']}")
    print(f"joint cells / order    = {art['tables']['joint_cells_fit']} / {art['tables']['order_cells_fit']}")
    print(f"train player-games     = {art['tables']['train_player_games']}")


if __name__ == "__main__":
    main()

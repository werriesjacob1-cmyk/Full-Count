#!/usr/bin/env python3
"""INDEPENDENT reproduction of the locked canonical-v2 Hits PA/opportunity experiment.

Written from:
  1. backtest/canonical_v2_hits_pa_opportunity_protocol.md  (locked SPECIFICATION)
  2. backtest/residual_challenger_model.py + its bound helpers (pre-existing
     mechanism the protocol explicitly pins, byte-identical on main)
  3. the certified date-quarantined research view rows.jsonl

NOT derived from: canonical_v2_hits_pa_opportunity_locked_run.py, PR #78/#79/#81/#82,
or any stored SUPERCHAD result JSON. Those were quarantined before this was written.
"""
import json, math, sys
from collections import defaultdict

ROWS = "/home/user/m0-evidence/rv/canonical-v2-research/rows.jsonl"
MARKET = "hits"
SAFE_POOL_FLOOR = 0.60      # protocol: champion historical safe-pool proxy
MIN_CELL_N = 200            # protocol: bound to residual_challenger_model
PA_STATES = ["0", "1", "2", "3", "4", "5", "6+"]
HITTER_MARKETS = frozenset({
    "hits", "total_bases", "hits_runs_rbis", "home_run", "singles",
    "doubles", "triples", "rbis", "runs", "hard_hit_105",
})

# ---- bound helpers, transcribed from the pinned pre-existing modules ----
def derive_batting_order(lineup_slot):
    if lineup_slot is None: return None
    order = round(9.0 - lineup_slot * 8.0 / 100.0)
    return order if 1 <= order <= 9 else None

def pa_bucket_fine(actual_pa):
    if actual_pa is None: return "unknown"
    if actual_pa >= 6: return "6+"
    return str(int(actual_pa))

def getaway_day_group(signals):
    v = signals.get("getaway_day")
    if v is None: return None
    return "getaway_day" if v < 0 else "not_getaway_day"

def days_rest_group(signals):
    v = signals.get("days_rest")
    if v is None: return None
    if v <= 0: return "0_days_rest"
    if v == 1: return "1_day_rest"
    if v <= 3: return "2-3_days_rest"
    return "4plus_days_rest"

def joint_key(row):
    sig = row.get("signals") or {}
    order = derive_batting_order(sig.get("lineup_slot"))
    if order is None: return None
    dr, ga = days_rest_group(sig), getaway_day_group(sig)
    if dr is None or ga is None: return None
    return (order, dr, ga)

def dedupe_player_games(rows):
    seen = {}
    for r in rows:
        k = (r.get("date"), r.get("game_pk"), r.get("player_id"))
        if k not in seen: seen[k] = r
    return list(seen.values())

def fit_pa_distribution(player_games):
    counts, totals = defaultdict(lambda: defaultdict(int)), defaultdict(int)
    for r in player_games:
        order = derive_batting_order((r.get("signals") or {}).get("lineup_slot"))
        pa = r.get("actual_pa")
        if order is None or pa is None: continue
        counts[order][pa_bucket_fine(pa)] += 1
        totals[order] += 1
    return {o: dict({s: round(counts[o].get(s, 0)/t, 6) for s in PA_STATES}, _n=t)
            for o, t in totals.items()}

def fit_joint_pa_distribution(player_games, min_cell_n=MIN_CELL_N):
    counts, totals = defaultdict(lambda: defaultdict(int)), defaultdict(int)
    for r in player_games:
        k, pa = joint_key(r), r.get("actual_pa")
        if k is None or pa is None: continue
        totals[k] += 1
        counts[k][pa_bucket_fine(pa)] += 1
    return {k: dict({s: round(counts[k].get(s, 0)/t, 6) for s in PA_STATES}, _n=t)
            for k, t in totals.items() if t >= min_cell_n}

def fit_hit_rate_given_pa(rows, market):
    c = defaultdict(lambda: {"n": 0, "hits": 0})
    for r in rows:
        if r.get("prop_type") != market: continue
        pa = r.get("actual_pa")
        if pa is None: continue
        b = c[pa_bucket_fine(pa)]
        b["n"] += 1; b["hits"] += r["outcome"]
    return {s: (round(v["hits"]/v["n"], 6) if v["n"] else None) for s, v in c.items()}

def challenger_probability_joint(row, joint_dist, order_dist, hit_rate_given_pa):
    k = joint_key(row)
    dist = joint_dist.get(k) if k else None
    if dist is None:
        order = derive_batting_order((row.get("signals") or {}).get("lineup_slot"))
        dist = order_dist.get(order)
    if not dist: return None
    tot = w = 0.0
    for s in PA_STATES:
        p_pa = dist.get(s, 0.0); p_hit = hit_rate_given_pa.get(s)
        if p_hit is None or p_pa <= 0: continue
        tot += p_pa * p_hit; w += p_pa
    return round(tot / w, 6) if w > 0 else None

def candidate_key(r):
    return (r.get("date"), r.get("game_pk"), r.get("player_id"),
            r.get("prop_type"), r.get("line"), r.get("needs"))

# ---- statistics ----
def two_proportion_z(h1, n1, h2, n2):
    if not n1 or not n2: return None, None
    p1, p2 = h1/n1, h2/n2
    p = (h1+h2)/(n1+n2)
    se = math.sqrt(p*(1-p)*(1/n1 + 1/n2))
    if se == 0: return None, None
    z = (p1-p2)/se
    pv = math.erfc(abs(z)/math.sqrt(2))
    return z, pv

def wilson(h, n, z=1.959963984540054):
    if not n: return (None, None)
    p = h/n; d = 1 + z*z/n
    c = (p + z*z/(2*n))/d
    m = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/d
    return (c-m, c+m)

def date_cluster_bootstrap(per_date, iters=5000, seed=20260831):
    """Deterministic LCG resample of DATES (clusters), delta = chal_rate - champ_rate."""
    dates = sorted(per_date)
    D = len(dates)
    st = seed & 0xFFFFFFFF
    deltas = []
    for _ in range(iters):
        ch = cn = ah = an = 0
        for _ in range(D):
            st = (1103515245*st + 12345) & 0x7FFFFFFF
            d = per_date[dates[st % D]]
            ch += d["champ_hits"]; cn += d["champ_n"]
            ah += d["chal_hits"];  an += d["chal_n"]
        if cn and an: deltas.append(ah/an - ch/cn)
    deltas.sort()
    lo = deltas[int(0.025*len(deltas))]
    hi = deltas[int(0.975*len(deltas))-1]
    return lo, hi, len(deltas)

def season_phase(date):
    m = int(date[5:7])
    if m <= 4: return "April"
    if m <= 7: return "May-Jul"
    return "August+"

# ---- experiment ----
def run(train_years, eval_year, all_rows):
    graded = [r for r in all_rows if r.get("outcome") in (0, 1)]
    hitter = [r for r in graded if r.get("prop_type") in HITTER_MARKETS]
    train = [r for r in hitter if r["date"][:4] in train_years]

    pg = dedupe_player_games(train)
    joint_dist = fit_joint_pa_distribution(pg)
    order_dist = fit_pa_distribution(pg)
    hr_given_pa = fit_hit_rate_given_pa(train, MARKET)

    universe = [r for r in graded if r["date"][:4] == eval_year and r["prop_type"] == MARKET]

    by_date = defaultdict(list)
    for r in universe: by_date[r["date"]].append(r)

    champ_sel, chal_sel = [], []
    per_date, fallback_n, mismatches, used_joint = {}, 0, 0, 0
    for d in sorted(by_date):
        rows = by_date[d]
        champs = [r for r in rows if (r.get("predicted_prob") or 0.0) >= SAFE_POOL_FLOOR]
        n_d = len(champs)
        scored = []
        for r in rows:
            cp = r.get("predicted_prob")
            ch = challenger_probability_joint(r, joint_dist, order_dist, hr_given_pa)
            if ch is None:
                ch = cp            # protocol: neutral fallback to champion probability
                fallback_n += 1
            else:
                k = joint_key(r)
                if k is not None and k in joint_dist: used_joint += 1
            scored.append((ch if ch is not None else 0.0,
                           cp if cp is not None else 0.0, candidate_key(r), r))
        scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
        picks = [t[3] for t in scored[:n_d]]
        if len(picks) != n_d: mismatches += 1
        champ_sel.extend(champs); chal_sel.extend(picks)
        per_date[d] = {"champ_n": n_d, "champ_hits": sum(r["outcome"] for r in champs),
                       "chal_n": len(picks), "chal_hits": sum(r["outcome"] for r in picks)}

    ck = {candidate_key(r) for r in champ_sel}
    lk = {candidate_key(r) for r in chal_sel}
    overlap = ck & lk
    added = [r for r in chal_sel if candidate_key(r) not in ck]
    removed = [r for r in champ_sel if candidate_key(r) not in lk]

    cn, chn = len(champ_sel), len(chal_sel)
    chits = sum(r["outcome"] for r in champ_sel)
    lhits = sum(r["outcome"] for r in chal_sel)
    ah, rh = sum(r["outcome"] for r in added), sum(r["outcome"] for r in removed)
    z, pv = two_proportion_z(ah, len(added), rh, len(removed))
    lo, hi, nb = date_cluster_bootstrap(per_date)

    phases = defaultdict(lambda: {"added_n":0,"added_h":0,"removed_n":0,"removed_h":0})
    for r in added:
        p = phases[season_phase(r["date"])]; p["added_n"] += 1; p["added_h"] += r["outcome"]
    for r in removed:
        p = phases[season_phase(r["date"])]; p["removed_n"] += 1; p["removed_h"] += r["outcome"]

    pos = sum(1 for d in per_date.values() if d["chal_hits"] > d["champ_hits"])
    eq  = sum(1 for d in per_date.values() if d["chal_hits"] == d["champ_hits"])
    neg = sum(1 for d in per_date.values() if d["chal_hits"] < d["champ_hits"])

    return {
        "eval_year": eval_year, "train_years": sorted(train_years),
        "candidate_rows": len(universe), "dates": len(per_date),
        "champion_selected": cn, "challenger_selected": chn,
        "champion_hits": chits, "challenger_hits": lhits,
        "champion_hit_rate": chits/cn, "challenger_hit_rate": lhits/chn,
        "delta": lhits/chn - chits/cn,
        "overlap": len(overlap), "added_n": len(added), "removed_n": len(removed),
        "added_hits": ah, "removed_hits": rh,
        "added_hit_rate": ah/len(added) if added else None,
        "removed_hit_rate": rh/len(removed) if removed else None,
        "z": z, "p_value": pv,
        "wilson_added": wilson(ah, len(added)), "wilson_removed": wilson(rh, len(removed)),
        "bootstrap_95": [lo, hi], "bootstrap_draws": nb,
        "per_date_mismatches": mismatches,
        "fallback_to_champion_prob": fallback_n,
        "used_joint_cell": used_joint,
        "joint_cells_fit": len(joint_dist), "order_cells_fit": len(order_dist),
        "churn_dates_positive": pos, "churn_dates_equal": eq, "churn_dates_negative": neg,
        "season_phase": {k: dict(v) for k, v in sorted(phases.items())},
        "market_mix": {MARKET: len(universe)},
    }

def main():
    rows = []
    with open(ROWS) as fh:
        for line in fh: rows.append(json.loads(line))
    out = {"2025": run({"2024"}, "2025", rows),
           "2026": run({"2024","2025"}, "2026", rows)}
    print(json.dumps(out, indent=2, sort_keys=True))

if __name__ == "__main__":
    main()

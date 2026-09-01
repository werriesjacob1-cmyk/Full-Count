#!/usr/bin/env python3
"""INDEPENDENT reproduction of SUPERCHAD's corrected FULL-UNIVERSE /
EXACT-PER-DATE disagreement revalidation.

Built from the mission's stated application rule + the pre-existing bound
mechanisms (prob_bucket, baseline_context_conflict, conflict_tier, MIN_CELL_N).
NOT derived from disagreement_experiment_runner.py or any stored result JSON.

Corrections vs the OLD locked disagreement experiment, per the mission:
  - the universe is EVERY certified Hits row (the old one required
    cat_baseline_skill is not None, a restricted universe);
  - volume is matched exactly PER DATE, not aggregate across the holdout;
  - a missing disagreement component does NOT remove the candidate;
  - missing/sparse tier -> training bucket's tier-blind empirical Hits rate;
  - unseen training bucket -> current probability.
"""
import json, math, sys
from collections import defaultdict
sys.path.insert(0, "/home/user/m0-independent")
from pa_reproduce import (two_proportion_z, wilson, date_cluster_bootstrap,
                          candidate_key, ROWS, SAFE_POOL_FLOOR)

MARKET = "hits"
MIN_CELL_N = 200

def prob_bucket(p, width=0.05):
    if p is None: return None
    idx = round(p / width, 6)
    lo = int(idx) * width
    return f"{lo:.2f}-{lo + width:.2f}"

def baseline_context_conflict(row):
    b, c = row.get("cat_baseline_skill"), row.get("cat_context")
    if b is None or c is None: return None
    return b - c

def conflict_tier(conflict, hi=20.0, lo=-20.0):
    if conflict is None: return None
    if conflict >= hi: return "high_empirical_low_context"
    if conflict <= lo: return "high_context_low_empirical"
    return "balanced"

def _rate(hits, n):
    return round(hits / n, 4) if n else None

def fit_bucket_tier_hit_rate(train_rows, min_cell_n=MIN_CELL_N):
    cell = defaultdict(lambda: {"n":0,"hits":0}); buck = defaultdict(lambda: {"n":0,"hits":0})
    for r in train_rows:
        b = prob_bucket(r.get("predicted_prob"))
        if b is None: continue
        buck[b]["n"] += 1; buck[b]["hits"] += r["outcome"]
        t = conflict_tier(baseline_context_conflict(r))
        if t is None: continue
        cell[(b,t)]["n"] += 1; cell[(b,t)]["hits"] += r["outcome"]
    return ({k:_rate(v["hits"],v["n"]) for k,v in cell.items() if v["n"] >= min_cell_n},
            {k:_rate(v["hits"],v["n"]) for k,v in buck.items()})

def run(train_years, eval_year, all_rows):
    graded = [r for r in all_rows if r.get("outcome") in (0,1)]
    # FULL universe: every certified Hits row. No cat_baseline_skill restriction.
    mkt = [r for r in graded if r.get("prop_type") == MARKET]
    # EVALUATION universe = every certified Hits row. This is the correction:
    # the old locked experiment restricted the universe to cat_baseline_skill
    # is not None, which is what made its result non-comparable.
    universe = [r for r in mkt if r["date"][:4] == eval_year]
    # TRAINING set keeps the bound model's own restriction. The mission's prose
    # says "the training bucket's tier-blind empirical Hits rate" without saying
    # which rows train; disagreement_challenger_model.build_report settles it --
    # it restricts market_rows (hence train_rows) to cat_baseline_skill is not
    # None, because a conflict tier is only computable on those rows. Training
    # the tier-blind fallback on rows that could never carry a tier would mix a
    # different population into the baseline. Verified: applying the widened
    # universe to TRAINING as well reproduces 2026 but not 2025.
    train = [r for r in mkt if r["date"][:4] in train_years
             and r.get("cat_baseline_skill") is not None]
    cell_rate, bucket_rate = fit_bucket_tier_hit_rate(train)

    src = {"supported_cell":0, "missing_component_bucket_fallback":0,
           "sparse_cell_bucket_fallback":0, "unseen_bucket_current_prob":0}
    by_date = defaultdict(list)
    for r in universe: by_date[r["date"]].append(r)

    champ_sel, chal_sel, per_date, mismatches = [], [], {}, 0
    for d in sorted(by_date):
        rows = by_date[d]
        champs = [r for r in rows if (r.get("predicted_prob") or 0.0) >= SAFE_POOL_FLOOR]
        n_d = len(champs)
        scored = []
        for r in rows:
            cp = r.get("predicted_prob")
            b = prob_bucket(cp)
            conflict = baseline_context_conflict(r)
            t = conflict_tier(conflict)
            if b is None:
                score = cp; src["unseen_bucket_current_prob"] += 1
            elif t is not None and (b,t) in cell_rate:
                score = cell_rate[(b,t)]; src["supported_cell"] += 1
            elif t is None:
                score = bucket_rate.get(b)
                if score is None: score = cp; src["unseen_bucket_current_prob"] += 1
                else: src["missing_component_bucket_fallback"] += 1
            else:
                score = bucket_rate.get(b)
                if score is None: score = cp; src["unseen_bucket_current_prob"] += 1
                else: src["sparse_cell_bucket_fallback"] += 1
            scored.append((score if score is not None else 0.0,
                           cp if cp is not None else 0.0, candidate_key(r), r))
        scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
        picks = [x[3] for x in scored[:n_d]]
        if len(picks) != n_d: mismatches += 1
        champ_sel.extend(champs); chal_sel.extend(picks)
        per_date[d] = {"champ_n":n_d, "champ_hits":sum(r["outcome"] for r in champs),
                       "chal_n":len(picks), "chal_hits":sum(r["outcome"] for r in picks)}

    ck = {candidate_key(r) for r in champ_sel}; lk = {candidate_key(r) for r in chal_sel}
    added = [r for r in chal_sel if candidate_key(r) not in ck]
    removed = [r for r in champ_sel if candidate_key(r) not in lk]
    cn, chn = len(champ_sel), len(chal_sel)
    chits, lhits = sum(r["outcome"] for r in champ_sel), sum(r["outcome"] for r in chal_sel)
    ah, rh = sum(r["outcome"] for r in added), sum(r["outcome"] for r in removed)
    z, pv = two_proportion_z(ah, len(added), rh, len(removed))
    lo, hi, nb = date_cluster_bootstrap(per_date)
    return {"eval_year":eval_year, "train_years":sorted(train_years),
            "candidate_rows":len(universe), "N_per_arm":cn,
            "current_hit_rate":chits/cn, "disagreement_hit_rate":lhits/chn,
            "delta":lhits/chn - chits/cn, "overlap":len(ck & lk),
            "added_n":len(added), "removed_n":len(removed),
            "added_hit_rate":ah/len(added) if added else None,
            "removed_hit_rate":rh/len(removed) if removed else None,
            "z":z, "p_value":pv, "bootstrap_95":[lo,hi], "bootstrap_draws":nb,
            "per_date_mismatches":mismatches, "score_sources":src}

def main():
    rows=[]
    with open(ROWS) as fh:
        for line in fh: rows.append(json.loads(line))
    print(json.dumps({"2025":run({"2024"},"2025",rows),
                      "2026":run({"2024","2025"},"2026",rows)}, indent=2, sort_keys=True))

if __name__ == "__main__":
    main()

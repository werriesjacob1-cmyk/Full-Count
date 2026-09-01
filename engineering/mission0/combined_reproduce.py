#!/usr/bin/env python3
"""INDEPENDENT reproduction of the locked PA x Disagreement COMBINED experiment.

From FULL_COUNT_PA_DISAGREEMENT_COMBINED_PROTOCOL_LOCKED_2026-09-01.md
(sha256 16f7c8c2b45453cbd0c8ec68c22be6f7c25a4ffc6a80b18730210d6d266beed1)
plus the pre-existing bound mechanisms. NOT derived from
run_locked_combined_stream.py or any stored result JSON.

Locked combined ranking is LEXICOGRAPHIC, never a weighted blend:
  1. higher PA challenger probability
  2. among EXACT PA-score ties only, higher disagreement auxiliary score
  3. then higher current predicted_prob
  4. then stable semantic candidate key
Decisive comparison is combined vs PA-ONLY.
"""
import json, sys
from collections import defaultdict
sys.path.insert(0, "/home/user/m0-independent")
from pa_reproduce import (ROWS, SAFE_POOL_FLOOR, MARKET, HITTER_MARKETS, candidate_key,
                          dedupe_player_games, fit_pa_distribution, fit_joint_pa_distribution,
                          fit_hit_rate_given_pa, challenger_probability_joint,
                          two_proportion_z, wilson, date_cluster_bootstrap, season_phase)
from disagreement_reproduce import (prob_bucket, baseline_context_conflict,
                                    conflict_tier, fit_bucket_tier_hit_rate)

def run(train_years, eval_year, all_rows):
    graded = [r for r in all_rows if r.get("outcome") in (0, 1)]
    hitter = [r for r in graded if r.get("prop_type") in HITTER_MARKETS]
    pa_train = [r for r in hitter if r["date"][:4] in train_years]
    pg = dedupe_player_games(pa_train)
    joint_dist, order_dist = fit_joint_pa_distribution(pg), fit_pa_distribution(pg)
    hr_given_pa = fit_hit_rate_given_pa(pa_train, MARKET)

    mkt = [r for r in graded if r.get("prop_type") == MARKET]
    dis_train = [r for r in mkt if r["date"][:4] in train_years
                 and r.get("cat_baseline_skill") is not None]
    cell_rate, bucket_rate = fit_bucket_tier_hit_rate(dis_train)

    universe = [r for r in mkt if r["date"][:4] == eval_year]
    by_date = defaultdict(list)
    for r in universe: by_date[r["date"]].append(r)

    champ, pa_sel, comb_sel = [], [], []
    per_date_pa, per_date_comb, mism = {}, {}, 0
    fb = {"pa_fallback_to_current":0, "dis_bucket_fallback":0, "dis_unseen_bucket_current":0}
    tie_broken_by_disagreement = 0

    for d in sorted(by_date):
        rows = by_date[d]
        champs = [r for r in rows if (r.get("predicted_prob") or 0.0) >= SAFE_POOL_FLOOR]
        n_d = len(champs)
        recs = []
        for r in rows:
            cp = r.get("predicted_prob") or 0.0
            pa = challenger_probability_joint(r, joint_dist, order_dist, hr_given_pa)
            if pa is None:
                pa = cp; fb["pa_fallback_to_current"] += 1
            b = prob_bucket(r.get("predicted_prob"))
            t = conflict_tier(baseline_context_conflict(r))
            if b is not None and t is not None and (b, t) in cell_rate:
                dis = cell_rate[(b, t)]
            elif b is not None and bucket_rate.get(b) is not None:
                dis = bucket_rate[b]; fb["dis_bucket_fallback"] += 1
            else:
                dis = cp; fb["dis_unseen_bucket_current"] += 1
            recs.append({"pa": pa, "dis": dis, "cp": cp, "k": candidate_key(r), "r": r})

        pa_rank   = sorted(recs, key=lambda x: (-x["pa"],              -x["cp"], x["k"]))
        comb_rank = sorted(recs, key=lambda x: (-x["pa"], -x["dis"],   -x["cp"], x["k"]))
        pa_picks   = [x["r"] for x in pa_rank[:n_d]]
        comb_picks = [x["r"] for x in comb_rank[:n_d]]
        if len(pa_picks) != n_d or len(comb_picks) != n_d: mism += 1
        # how many changed specifically because disagreement broke an exact PA tie
        tie_broken_by_disagreement += len({x["k"] for x in comb_rank[:n_d]} -
                                          {x["k"] for x in pa_rank[:n_d]})
        champ.extend(champs); pa_sel.extend(pa_picks); comb_sel.extend(comb_picks)
        per_date_pa[d]   = {"champ_n":n_d,"champ_hits":sum(r["outcome"] for r in champs),
                            "chal_n":len(pa_picks),"chal_hits":sum(r["outcome"] for r in pa_picks)}
        per_date_comb[d] = {"champ_n":len(pa_picks),"champ_hits":sum(r["outcome"] for r in pa_picks),
                            "chal_n":len(comb_picks),"chal_hits":sum(r["outcome"] for r in comb_picks)}

    pk = {candidate_key(r) for r in pa_sel}; bk = {candidate_key(r) for r in comb_sel}
    comb_only = [r for r in comb_sel if candidate_key(r) not in pk]
    pa_only   = [r for r in pa_sel   if candidate_key(r) not in bk]
    n = len(pa_sel)
    pah = sum(r["outcome"] for r in pa_sel); cbh = sum(r["outcome"] for r in comb_sel)
    coh = sum(r["outcome"] for r in comb_only); poh = sum(r["outcome"] for r in pa_only)
    z, pv = two_proportion_z(coh, len(comb_only), poh, len(pa_only))
    lo, hi, nb = date_cluster_bootstrap(per_date_comb)
    ph = defaultdict(lambda: {"comb_n":0,"comb_h":0,"pa_n":0,"pa_h":0})
    for r in comb_only:
        p=ph[season_phase(r["date"])]; p["comb_n"]+=1; p["comb_h"]+=r["outcome"]
    for r in pa_only:
        p=ph[season_phase(r["date"])]; p["pa_n"]+=1; p["pa_h"]+=r["outcome"]
    pos=sum(1 for v in per_date_comb.values() if v["chal_hits"]>v["champ_hits"])
    eq =sum(1 for v in per_date_comb.values() if v["chal_hits"]==v["champ_hits"])
    neg=sum(1 for v in per_date_comb.values() if v["chal_hits"]<v["champ_hits"])
    return {"eval_year":eval_year,"candidate_rows":len(universe),"N_per_arm":n,
            "champion_hit_rate":sum(r["outcome"] for r in champ)/len(champ),
            "pa_hits":pah,"pa_hit_rate":pah/n,
            "combined_hits":cbh,"combined_hit_rate":cbh/n,
            "combined_minus_pa":cbh/n - pah/n,
            "overlap":len(pk & bk),
            "combined_only_n":len(comb_only),"combined_only_hit_rate":coh/len(comb_only) if comb_only else None,
            "pa_only_n":len(pa_only),"pa_only_hit_rate":poh/len(pa_only) if pa_only else None,
            "z":z,"p_value":pv,
            "wilson_combined_only":wilson(coh,len(comb_only)),"wilson_pa_only":wilson(poh,len(pa_only)),
            "bootstrap_95_combined_minus_pa":[lo,hi],"bootstrap_draws":nb,
            "per_date_mismatches":mism,"changed_by_disagreement_tiebreak":tie_broken_by_disagreement,
            "fallbacks":fb,"dates_positive":pos,"dates_equal":eq,"dates_negative":neg,
            "season_phase":{k:dict(v) for k,v in sorted(ph.items())}}

def main():
    rows=[]
    with open(ROWS) as fh:
        for line in fh: rows.append(json.loads(line))
    print(json.dumps({"2025":run({"2024"},"2025",rows),
                      "2026":run({"2024","2025"},"2026",rows)}, indent=2, sort_keys=True))

if __name__ == "__main__":
    main()

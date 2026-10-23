#!/usr/bin/env python3
"""FC-ACC-001 equal-volume selector competition, exactly as PREREG.md
(commit bb8600e8f5, sha256 e6466a71...dbbb). Do not edit arm definitions here
without a labelled amendment."""
import collections
import math
import random
import sys

import numpy as np

sys.path.insert(0, __import__("os").path.dirname(__file__))
import common  # noqa: E402

PREREG_SHA256 = "e6466a71cdbacb86ce0f10af1cd89e353c20aa8f99ac8c3df59a616d5905dbbb"
SEED, B = 20261013, 2000
REGULAR_END = "2026-09-28"  # regular season 09-20..09-27; postseason from 09-29
CORE = {"hits", "hits_runs_rbis", "total_bases", "strikeouts", "pitcher_outs"}


def key_of(r):
    who = r.get("combo_player_ids") or r.get("player_id")
    return (str(who), r["stat"], r["market_side"])


def p_of(r):
    return r["prediction"]["hit_probability"]


def q_of(r):
    return r["market"]["market_implied"] if r["market"]["market_implied"] is not None else common.implied(r["market"]["market_odds"])


def eligible(r):
    e, m, pr = r["eligibility"], r["market"], r["prediction"]
    return (m["market_odds"] is not None and e["qc_status"] == "kept" and not e["lineup_assumed"]
            and pr["reliability"] in ("A", "B", "C") and pr["hit_probability"] is not None)


def take(cands, keyfn, n):
    out, seen = [], set()
    for r in sorted(cands, key=lambda r: (-keyfn(r), r["candidate_id"])):
        k = key_of(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
        if len(out) == n:
            break
    return out


ARMS = {
    "CH1_BLEND": (lambda r: True, lambda r: (p_of(r) + q_of(r)) / 2),
    "CH2_MARKET": (lambda r: True, q_of),
    "CH3_CAPPED_MODEL": (lambda r: p_of(r) - q_of(r) <= 0.10 and q_of(r) >= 0.52, p_of),
    "CH4_DEPTH": (lambda r: (r["prediction"]["sample_n"] or 0) >= 40, p_of),
}


def grade_of(grades, r):
    g = grades.get(r["candidate_id"])
    return g.get("grade") if g else None


def build():
    # Window pinned to the preregistered evaluation (09-20..10-11, the slates
    # graded when the competition ran); later-graded boards are new evidence.
    boards, prov = common.load_freezes(through=common.EVIDENCE_CUTOFF)
    slates, excluded = [], []
    for b, grades in boards:
        for g, t in b["game_start_times"].items():
            if t and t <= b["sealed_at"]:
                raise SystemExit(f"{b['date']}: game {g} started before seal")
        if not grades:
            excluded.append({"date": b["date"], "reason": "no graded file"})
            continue
        recs = b["records"]
        champ = [r for r in recs if r["selector"]["recommendation_status"] == "top_pick"]
        U = [r for r in recs if eligible(r)]
        n = len(champ)
        arms = {"CHAMPION": champ}
        short = {}
        for name, (flt, key) in ARMS.items():
            pool = [r for r in U if flt(r)]
            sel = take(pool, key, n) if n else []
            arms[name] = sel
            short[name] = n - len(sel)
        one = take(champ, p_of, 10**6)
        bygame, ch5 = set(), []
        for r in one:
            if r["game_pk"] not in bygame:
                bygame.add(r["game_pk"])
                ch5.append(r)
        arms["CH5_ONE_PER_GAME"] = ch5
        short["CH5_ONE_PER_GAME"] = n - len(ch5)
        slates.append({"date": b["date"], "n": n, "arms": arms, "short": short, "grades": grades,
                       "champ_outside_U": sum(not eligible(r) for r in champ)})
    return slates, excluded, prov


def stats(rows, grades):
    s = [(r, grade_of(grades, r)) for r in rows]
    settled = [(r, g) for r, g in s if g in ("hit", "miss")]
    h = sum(g == "hit" for _, g in settled)
    pnl = sum(common.american_profit(r["market"]["market_odds"]) if g == "hit" else -1.0
              for r, g in settled if r["market"]["market_odds"] is not None)
    priced = sum(r["market"]["market_odds"] is not None for r, _ in settled)
    qs = [q_of(r) for r, _ in settled if r["market"]["market_odds"] is not None]
    return {"selected": len(rows), "settled": len(settled), "hits": h,
            "void_or_ungraded": len(rows) - len(settled), "priced_settled": priced,
            "units": round(pnl, 3), "mean_q": (sum(qs) / len(qs)) if qs else None,
            "mean_p": (sum(p_of(r) for r, _ in settled) / len(settled)) if settled else None,
            "markets": collections.Counter(r["stat"] for r in rows)}


def agg(slates, arm):
    tot = collections.Counter()
    markets = collections.Counter()
    qsum = psum = 0.0
    for s in slates:
        st = stats(s["arms"][arm], s["grades"])
        for k in ("selected", "settled", "hits", "void_or_ungraded", "priced_settled"):
            tot[k] += st[k]
        tot["units"] += st["units"]
        markets.update(st["markets"])
        qsum += (st["mean_q"] or 0) * st["priced_settled"]
        psum += (st["mean_p"] or 0) * st["settled"]
    return {"selected": tot["selected"], "settled": tot["settled"], "hits": tot["hits"],
            "hit_rate": round(tot["hits"] / tot["settled"], 4) if tot["settled"] else None,
            "void_or_ungraded": tot["void_or_ungraded"], "units": round(tot["units"], 2),
            "roi": round(tot["units"] / tot["priced_settled"], 4) if tot["priced_settled"] else None,
            "mean_q": round(qsum / tot["priced_settled"], 4) if tot["priced_settled"] else None,
            "mean_p": round(psum / tot["settled"], 4) if tot["settled"] else None,
            "markets": dict(markets.most_common()), "slates": len(slates),
            "daily_volume": {s["date"]: len(s["arms"][arm]) for s in slates}}


def overlap(slates, arm):
    acc = collections.Counter()
    for s in slates:
        g = s["grades"]
        c = {r["candidate_id"]: grade_of(g, r) for r in s["arms"]["CHAMPION"]}
        a = {r["candidate_id"]: grade_of(g, r) for r in s["arms"][arm]}
        acc["shared"] += len(c.keys() & a.keys())
        acc["winners_retained"] += sum(1 for k in c.keys() & a.keys() if c[k] == "hit")
        acc["winners_removed"] += sum(1 for k in c.keys() - a.keys() if c[k] == "hit")
        acc["losers_removed"] += sum(1 for k in c.keys() - a.keys() if c[k] == "miss")
        acc["winners_added"] += sum(1 for k in a.keys() - c.keys() if a[k] == "hit")
        acc["losers_added"] += sum(1 for k in a.keys() - c.keys() if a[k] == "miss")
        acc["unsettled_changed"] += sum(1 for k in (a.keys() ^ c.keys()) if (a.get(k) or c.get(k)) not in ("hit", "miss"))
    acc["net_winners"] = acc["winners_added"] - acc["winners_removed"]
    return dict(acc)


def cluster_units(slates, arm):
    """Per slate: list of game clusters with (champ_hits, champ_settled, arm_hits, arm_settled)."""
    out = []
    for s in slates:
        g = s["grades"]
        games = collections.defaultdict(lambda: [0, 0, 0, 0])
        for idx, a in ((0, "CHAMPION"), (2, arm)):
            for r in s["arms"][a]:
                gr = grade_of(g, r)
                if gr in ("hit", "miss"):
                    games[r["game_pk"]][idx] += gr == "hit"
                    games[r["game_pk"]][idx + 1] += 1
        out.append(list(games.values()))
    return out


def diff_of(units):
    t = np.zeros(4)
    for sl in units:
        for gm in sl:
            t += gm
    if t[1] == 0 or t[3] == 0:
        return None
    return t[2] / t[3] - t[0] / t[1]


def bootstrap(units, rng, level):
    ds = []
    for _ in range(B):
        if level == "game":
            samp = [[sl[rng.randrange(len(sl))] for _ in sl] if sl else [] for sl in units]
        else:
            samp = [units[rng.randrange(len(units))] for _ in units]
        d = diff_of(samp)
        if d is not None:
            ds.append(d)
    ds.sort()
    lb = ds[int(0.05 * len(ds))]
    p_one = sum(d <= 0 for d in ds) / len(ds)
    return {"lb95_one_sided": round(lb, 4), "ci90": [round(ds[int(.05 * len(ds))], 4), round(ds[int(.95 * len(ds)) - 1], 4)],
            "p_one_sided": round(p_one, 4)}


def logistic(X, y, iters=50):
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        z = X @ beta
        mu = 1 / (1 + np.exp(-z))
        W = mu * (1 - mu) + 1e-9
        H = X.T @ (X * W[:, None]) + 1e-6 * np.eye(X.shape[1])
        beta = beta + np.linalg.solve(H, X.T @ (y - mu))
    return beta


def auc(score, y):
    order = np.argsort(score)
    ranks = np.empty(len(score))
    ranks[order] = np.arange(1, len(score) + 1)
    # average ties
    vals = np.asarray(score)[order]
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[j + 1] == vals[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2
        i = j + 1
    n1 = y.sum()
    n0 = len(y) - n1
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def pool_test(slates, rng, core_only):
    rows = []
    for s in slates:
        g = s["grades"]
        b_recs = None
        for r in s["_U"]:
            gr = grade_of(g, r)
            if gr not in ("hit", "miss"):
                continue
            if core_only and r["stat"] not in CORE:
                continue
            p, q = p_of(r), q_of(r)
            p = min(max(p, 1e-4), 1 - 1e-4)
            q = min(max(q, 1e-4), 1 - 1e-4)
            rows.append((s["date"] + ":" + r["game_pk"], p, q, 1 if gr == "hit" else 0))
    lg = lambda x: math.log(x / (1 - x))
    X = np.array([[1.0, lg(q), lg(p) - lg(q)] for _, p, q, _ in rows])
    y = np.array([t[3] for t in rows], dtype=float)
    beta = logistic(X, y)
    clusters = collections.defaultdict(list)
    for i, t in enumerate(rows):
        clusters[t[0]].append(i)
    keys = list(clusters)
    bs = []
    for _ in range(B):
        idx = [i for _ in keys for i in clusters[keys[rng.randrange(len(keys))]]]
        bs.append(logistic(X[idx], y[idx], iters=25)[2])
    bs.sort()
    P, Q = np.array([t[1] for t in rows]), np.array([t[2] for t in rows])
    return {"n": len(rows), "games": len(keys), "hit_rate": round(float(y.mean()), 4),
            "coef_logit_q": round(float(beta[1]), 4), "coef_d": round(float(beta[2]), 4),
            "coef_d_ci95": [round(float(bs[int(.025 * B)]), 4), round(float(bs[int(.975 * B) - 1]), 4)],
            "auc_p": round(auc(P, y), 4), "auc_q": round(auc(Q, y), 4), "auc_blend": round(auc((P + Q) / 2, y), 4),
            "mean_p": round(float(P.mean()), 4), "mean_q": round(float(Q.mean()), 4)}


def main():
    rng = random.Random(SEED)
    slates, excluded, prov = build()
    boards, _ = common.load_freezes(through=common.EVIDENCE_CUTOFF)
    Umap = {b["date"]: [r for r in b["records"] if eligible(r)] for b, _ in boards}
    for s in slates:
        s["_U"] = Umap[s["date"]]
    res = {"prereg_sha256": PREREG_SHA256, "inputs": prov, "excluded_slates": excluded,
           "champion_outside_U": sum(s["champ_outside_U"] for s in slates),
           "slates": [s["date"] for s in slates], "arms": {}}
    arm_names = ["CHAMPION", "CH1_BLEND", "CH2_MARKET", "CH3_CAPPED_MODEL", "CH4_DEPTH", "CH5_ONE_PER_GAME"]
    for arm in arm_names:
        comp = [s for s in slates if arm == "CHAMPION" or s["short"].get(arm, 0) == 0]
        entry = {"all_slates": agg(slates, arm), "comparable_slates": [s["date"] for s in comp],
                 "noncomparable": {s["date"]: s["short"][arm] for s in slates if arm != "CHAMPION" and s["short"].get(arm, 0)},
                 "regular_2026": agg([s for s in slates if s["date"] < REGULAR_END], arm),
                 "post_2026": agg([s for s in slates if s["date"] >= REGULAR_END], arm)}
        if arm != "CHAMPION":
            entry["champion_on_comparable"] = agg(comp, "CHAMPION")
            entry["arm_on_comparable"] = agg(comp, arm)
            entry["overlap"] = overlap(comp, arm)
            units = cluster_units(comp, arm)
            entry["diff_hit_rate"] = round(diff_of(units), 4) if diff_of(units) is not None else None
            entry["bootstrap_game"] = bootstrap(units, rng, "game")
            entry["bootstrap_slate"] = bootstrap(units, rng, "slate")
        res["arms"][arm] = entry
    # Holm over secondary CH2-CH4 (game-level one-sided p)
    sec = sorted(((res["arms"][a]["bootstrap_game"]["p_one_sided"], a) for a in ("CH2_MARKET", "CH3_CAPPED_MODEL", "CH4_DEPTH")))
    holm, m = {}, len(sec)
    running = 0.0
    for i, (p, a) in enumerate(sec):
        running = max(running, min(1.0, (m - i) * p))
        holm[a] = round(float(running), 4)
    res["holm_adjusted_p_secondary"] = holm
    ch1 = res["arms"]["CH1_BLEND"]
    champ_c, arm_c = ch1["champion_on_comparable"], ch1["arm_on_comparable"]
    lb = ch1["bootstrap_game"]["lb95_one_sided"]
    if ch1["diff_hit_rate"] is None or ch1["diff_hit_rate"] <= 0 or lb <= 0:
        verdict = "NO_DEMONSTRATED_IMPROVEMENT"
    elif (arm_c["mean_q"] - champ_c["mean_q"]) > 0.03 and arm_c["roi"] <= champ_c["roi"]:
        verdict = "CHALK_GUARD"
    else:
        verdict = "EXPLORATORY_POSITIVE"
    res["primary_verdict_CH1"] = verdict
    res["pool_test_all"] = pool_test(slates, rng, False)
    res["pool_test_core"] = pool_test(slates, rng, True)
    for s in slates:
        del s["_U"]
    common.write("competition.json", res)
    print("VERDICT CH1:", verdict)
    for a in arm_names:
        e = res["arms"][a]
        x = e["all_slates"]
        line = f"{a:18s} sel={x['selected']:3d} settled={x['settled']:3d} hits={x['hits']:3d} hr={x['hit_rate']} roi={x['roi']} q={x['mean_q']} p={x['mean_p']}"
        if a != "CHAMPION":
            line += f" | diff={e['diff_hit_rate']} gameLB={e['bootstrap_game']['lb95_one_sided']} p1={e['bootstrap_game']['p_one_sided']} slateLB={e['bootstrap_slate']['lb95_one_sided']} noncomp={e['noncomparable']} ov={e['overlap']}"
        print(line)
        print("    regular:", {k: e['regular_2026'][k] for k in ('settled', 'hits', 'hit_rate', 'roi')}, "post:", {k: e['post_2026'][k] for k in ('settled', 'hits', 'hit_rate', 'roi')})
        print("    markets:", x["markets"])
    print("holm:", holm, "champ outside U:", res["champion_outside_U"], "excluded:", excluded)
    print("pool all:", res["pool_test_all"]); print("pool core:", res["pool_test_core"])


if __name__ == "__main__":
    main()

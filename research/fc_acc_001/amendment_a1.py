#!/usr/bin/env python3
"""FC-ACC-001 Amendment A1 (post-audit corrections; Codex review 6151131715).

POST-HOC and labelled as such. It does not alter PREREG.md, competition.py's
preregistered arms, or out/competition.json / out/baseline_public.json, which
remain the immutable original evidence. It recomputes, from authoritative
sources:
  1. the public Top Pick baseline (history + retained results grade files);
  2. four explicitly separated populations;
  3. price semantics: offered-price break-even (uniform) vs no-vig fair vs model;
  4. CH1/CH2 with a UNIFORM raw break-even q (CH1u/CH2u), next to the original;
  5. slate-cluster uncertainty incl. CH1 vs the market-only control;
  6. offer-provenance feasibility for the forward protocol.
Nothing here is promotable; the blend weight 0.5 is fixed, not tuned.
"""
import collections
import math
import random
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))
import common  # noqa: E402
import competition as C  # noqa: E402
import offer_provenance as OP  # noqa: E402

SEED, B = 20261015, 10000


def settled(rows):
    return [r for r in rows if r["grade"] in ("hit", "miss")]


def public_summary(rows):
    s = settled(rows)
    h = sum(r["grade"] == "hit" for r in s)
    pnl = sum(common.american_profit(r["odds"]) if r["grade"] == "hit" else -1.0 for r in s)
    be = [common.break_even_raw(r["odds"]) for r in s]
    return {"published": len(rows), "settled": len(s), "wins": h, "losses": len(s) - h,
            "void": sum(r["grade"] == "void" for r in rows),
            "ungraded": sum(r["grade"] not in ("hit", "miss", "void") for r in rows),
            "hit_rate": round(h / len(s), 4), "units": round(pnl, 2), "roi": round(pnl / len(s), 4),
            "mean_break_even_raw_at_published_odds": round(sum(be) / len(be), 4),
            "mean_stored_market_implied_MIXED": round(sum(r["q"] for r in s) / len(s), 4),
            "hit_minus_break_even_pp": round(100 * (h / len(s) - sum(be) / len(be)), 2)}


def p_of(r):
    return r["prediction"]["hit_probability"]


def be_of(r):
    return common.break_even_raw(r["market"]["market_odds"])


def arms_amended(slates):
    """Uniform-price arms on the SAME research pool U_d, same N_d, same tie rule."""
    boards, _ = common.load_freezes(through=common.EVIDENCE_CUTOFF)
    U = {b["date"]: [r for r in b["records"] if C.eligible(r)] for b, _ in boards}
    sealed = {b["date"]: b["sealed_at"] for b, _ in boards}
    for s in slates:
        n = s["n"]
        s["sealed_at"] = sealed[s["date"]]
        s["arms"]["CH1u_BLEND_RAW_BE"] = C.take(U[s["date"]], lambda r: (p_of(r) + be_of(r)) / 2, n) if n else []
        s["arms"]["CH2u_MARKET_RAW_BE"] = C.take(U[s["date"]], be_of, n) if n else []
    return slates


def arm_stats(slates, arm):
    hits = n = 0
    pnl = 0.0
    be = []
    for s in slates:
        for r in s["arms"][arm]:
            g = C.grade_of(s["grades"], r)
            if g not in ("hit", "miss"):
                continue
            n += 1
            hits += g == "hit"
            pnl += common.american_profit(r["market"]["market_odds"]) if g == "hit" else -1.0
            be.append(be_of(r))
    return {"settled": n, "wins": hits, "hit_rate": round(hits / n, 4), "roi": round(pnl / n, 4),
            "mean_break_even_raw": round(sum(be) / n, 4),
            "hit_minus_break_even_pp": round(100 * (hits / n - sum(be) / n), 2),
            "share_shorter_than_-200": round(sum(b >= 2 / 3 for b in be) / n, 3)}


def per_slate(slates, arm):
    out = []
    for s in slates:
        h = n = 0
        pnl = 0.0
        for r in s["arms"][arm]:
            g = C.grade_of(s["grades"], r)
            if g in ("hit", "miss"):
                n += 1
                h += g == "hit"
                pnl += common.american_profit(r["market"]["market_odds"]) if g == "hit" else -1.0
        out.append((h, n, pnl))
    return out


def paired_boot(slates, a, b, rng):
    A, Bv = per_slate(slates, a), per_slate(slates, b)
    pts = {"wins_diff": sum(x[0] for x in A) - sum(x[0] for x in Bv),
           "roi_diff": sum(x[2] for x in A) / sum(x[1] for x in A) - sum(x[2] for x in Bv) / sum(x[1] for x in Bv)}
    dw, dr = [], []
    idx = range(len(A))
    for _ in range(B):
        pick = [rng.choice(idx) for _ in idx]
        wa = sum(A[i][0] for i in pick); wb = sum(Bv[i][0] for i in pick)
        na = sum(A[i][1] for i in pick); nb = sum(Bv[i][1] for i in pick)
        if na and nb:
            dw.append(wa - wb)
            dr.append(sum(A[i][2] for i in pick) / na - sum(Bv[i][2] for i in pick) / nb)
    dw.sort(); dr.sort()
    q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))]  # noqa: E731
    return {**{k: round(v, 4) for k, v in pts.items()},
            "wins_diff_ci90_slate": [q(dw, .05), q(dw, .95)],
            "roi_diff_ci90_slate": [round(q(dr, .05), 4), round(q(dr, .95), 4)], "resamples": len(dw)}


def populations(slates, public_rows):
    published = {(r["date"], r["id"]) for r in public_rows}
    out = {}
    champ = [(s["date"], r) for s in slates for r in s["arms"]["CHAMPION"]]
    out["P1_published_customer_top_picks"] = {"definition": "public registry entries (customer-facing)",
                                              "count": len(public_rows)}
    out["P2_publication_eligible_candidate_top_picks_in_freezes"] = {
        "definition": "freeze records with recommendation_status=top_pick (existing policy), graded slates",
        "count": len(champ),
        "actually_published": sum((d, r["candidate_id"]) in published for d, r in champ),
        "not_published": sum((d, r["candidate_id"]) not in published for d, r in champ),
        "note": "not-published picks fall in the 2026-10-06..10-11 Pages publication outage (PR #224)"}
    out["P3_research_board_selections"] = {
        "definition": "freeze selector.selected_top_pick (top-10 main board; research artifact, not customer Top Picks)",
        "count": sum(r["selector"]["selected_top_pick"] for s in slates for r in s["_all"])}
    for arm in ("CH1_BLEND", "CH2_MARKET", "CH1u_BLEND_RAW_BE", "CH2u_MARKET_RAW_BE"):
        rows = [r for s in slates for r in s["arms"][arm]]
        out[f"P4_alternative_policy_shadow_{arm}"] = {
            "definition": "alternative-policy shadow selection on the research pool; NOT eligible under the existing Top Pick policy unless status says so",
            "count": len(rows),
            "existing_policy_status": dict(collections.Counter(r["selector"]["recommendation_status"] for r in rows)),
            "price_clears_false": sum(r["market"]["price_clears"] is not True for r in rows),
            "reliability": dict(collections.Counter(r["prediction"]["reliability"] for r in rows)),
            "published": sum((s["date"], r["candidate_id"]) in published for s in slates for r in s["arms"][arm])}
    return out


def pool_test_uniform(slates, rng):
    rows = []
    for s in slates:
        for r in s["_U"]:
            g = C.grade_of(s["grades"], r)
            if g not in ("hit", "miss"):
                continue
            p = min(max(p_of(r), 1e-4), 1 - 1e-4); q = min(max(be_of(r), 1e-4), 1 - 1e-4)
            rows.append((s["date"] + ":" + r["game_pk"], p, q, 1.0 if g == "hit" else 0.0))
    import numpy as np
    lg = lambda x: math.log(x / (1 - x))  # noqa: E731
    X = np.array([[1.0, lg(q), lg(p) - lg(q)] for _, p, q, _ in rows]); y = np.array([t[3] for t in rows])
    beta = C.logistic(X, y)
    cl = collections.defaultdict(list)
    for i, t in enumerate(rows):
        cl[t[0]].append(i)
    keys = list(cl); bs = []
    for _ in range(2000):
        idx = [i for _ in keys for i in cl[keys[rng.randrange(len(keys))]]]
        bs.append(float(C.logistic(X[idx], y[idx], iters=25)[2]))
    bs.sort()
    return {"n": len(rows), "coef_d_uniform_raw_break_even": round(float(beta[2]), 4),
            "ci95": [round(bs[50], 4), round(bs[1949], 4)]}


def main():
    rng = random.Random(SEED)
    pub_rows, pub_prov, recon = common.load_public_reconciled()
    post_rows, _, _ = common.load_public_reconciled(cutoff="9999-12-31")
    post = [r for r in post_rows if r["date"] > common.EVIDENCE_CUTOFF]
    by_market = collections.defaultdict(list)
    for r in pub_rows:
        by_market[r["stat"]].append(r)
    res = {"label": "AMENDMENT_A1_POST_HOC_CORRECTIONS", "prereg_unchanged_sha256": C.PREREG_SHA256,
           "public_inputs": pub_prov, "grade_reconciliation": recon,
           "P1_public_baseline": public_summary(pub_rows),
           "P1_public_by_market": {k: public_summary(v) for k, v in sorted(by_market.items())},
           "P1_post_window_new_evidence": public_summary(post) if [r for r in post if r["grade"] in ("hit", "miss")] else
                                          {"published": len(post), "settled": 0}}
    slates, excluded, prov = C.build()
    # Immutability: the boards/grade files used must be byte-identical to the
    # ones recorded by the original preregistered run.
    import json as _json
    orig = _json.load(open(__import__("os").path.join(common.OUT, "competition.json")))["inputs"]
    if [(i["board"], i["sha256"], i["graded_sha256"]) for i in orig if i["graded"]] != \
            [(i["board"], i["sha256"], i["graded_sha256"]) for i in prov if i["graded"]]:
        raise SystemExit("evidence window differs from the original preregistered run")
    boards, _ = common.load_freezes(through=common.EVIDENCE_CUTOFF)
    allrec = {b["date"]: b["records"] for b, _ in boards}
    U = {b["date"]: [r for r in b["records"] if C.eligible(r)] for b, _ in boards}
    for s in slates:
        s["_all"] = allrec[s["date"]]; s["_U"] = U[s["date"]]
    arms_amended(slates)
    names = ["CHAMPION", "CH1_BLEND", "CH2_MARKET", "CH1u_BLEND_RAW_BE", "CH2u_MARKET_RAW_BE"]
    res["arms"] = {a: arm_stats(slates, a) for a in names}
    res["arms"]["CH1_BLEND"]["note"] = "ORIGINAL preregistered arm; q = stored market_implied (MIXED semantics)"
    res["arms"]["CH1u_BLEND_RAW_BE"]["note"] = "A1 corrected: q = uniform offered-price break-even"
    res["paired_slate_bootstrap"] = {f"{a}_vs_{b}": paired_boot(slates, a, b, rng) for a, b in (
        ("CH1_BLEND", "CHAMPION"), ("CH1_BLEND", "CH2_MARKET"),
        ("CH1u_BLEND_RAW_BE", "CHAMPION"), ("CH1u_BLEND_RAW_BE", "CH2u_MARKET_RAW_BE"))}
    res["populations"] = populations(slates, pub_rows)
    res["pool_test_uniform"] = pool_test_uniform(slates, rng)
    res["offer_feasibility"] = OP.feasibility(slates, ["CHAMPION", "CH1u_BLEND_RAW_BE", "CH2u_MARKET_RAW_BE"])
    res["inputs"] = prov
    for s in slates:
        del s["_all"], s["_U"]
    common.write("amendment_a1.json", res)
    import json
    print(json.dumps({k: res[k] for k in ("grade_reconciliation", "P1_public_baseline", "arms", "paired_slate_bootstrap",
                                         "populations", "pool_test_uniform", "offer_feasibility")}, indent=1, default=str))


if __name__ == "__main__":
    main()

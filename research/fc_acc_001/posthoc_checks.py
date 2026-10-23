#!/usr/bin/env python3
"""FC-ACC-001 POST-HOC descriptive checks (NOT preregistered; computed after
competition.py). They challenge the preregistered result and can only weaken
or qualify it, never upgrade it:
  1. ROI difference (arm - champion) with game-clustered bootstrap.
  2. Excess over market: realized hit rate - mean frozen implied probability
     (the hit rate the price itself expects; positive = beating the book's view).
  3. Concentration: max picks per game / per player per slate.
  4. Price distribution of selections.
"""
import collections
import random
import sys

sys.path.insert(0, __import__("os").path.dirname(__file__))
import common  # noqa: E402
import competition as C  # noqa: E402


def per_game(slates, arm):
    out = []
    for s in slates:
        g = collections.defaultdict(lambda: [0.0, 0, 0.0, 0.0])  # pnl, n, hits, sum_q
        for r in s["arms"][arm]:
            gr = C.grade_of(s["grades"], r)
            if gr not in ("hit", "miss"):
                continue
            x = g[r["game_pk"]]
            x[0] += common.american_profit(r["market"]["market_odds"]) if gr == "hit" else -1.0
            x[1] += 1
            x[2] += gr == "hit"
            x[3] += C.q_of(r)
        out.append(dict(g))
    return out


def roi_excess(units):
    pnl = n = h = qs = 0.0
    for sl in units:
        for v in sl.values():
            pnl += v[0]; n += v[1]; h += v[2]; qs += v[3]
    return (pnl / n, h / n - qs / n) if n else (None, None)


def main():
    rng = random.Random(20261014)
    slates, _, _ = C.build()
    res = {"note": "POST-HOC, not preregistered; qualifies the preregistered result only"}
    champ = per_game(slates, "CHAMPION")
    for arm in ("CHAMPION", "CH1_BLEND", "CH2_MARKET", "CH3_CAPPED_MODEL", "CH4_DEPTH"):
        u = per_game(slates, arm)
        roi, exc = roi_excess(u)
        conc_g = max((collections.Counter(r["game_pk"] for r in s["arms"][arm]).most_common(1) or [(0, 0)])[0][1] for s in slates)
        conc_p = max((collections.Counter(C.key_of(r)[0] for r in s["arms"][arm]).most_common(1) or [(0, 0)])[0][1] for s in slates)
        odds = sorted(r["market"]["market_odds"] for s in slates for r in s["arms"][arm])
        med = odds[len(odds) // 2]
        entry = {"roi": round(roi, 4), "excess_over_market_pp": round(100 * exc, 2),
                 "max_picks_one_game_one_slate": conc_g, "max_picks_one_player_one_slate": conc_p,
                 "median_odds": med, "share_shorter_than_-200": round(sum(o <= -200 for o in odds) / len(odds), 3)}
        if arm != "CHAMPION":
            dr, de = [], []
            for _ in range(C.B):
                ia = []
                for sc, sa in zip(champ, u):
                    keys = sorted(set(sc) | set(sa))
                    if not keys:
                        continue
                    pick = [keys[rng.randrange(len(keys))] for _ in keys]
                    ia.append(({k: sc[k] for k in pick if k in sc}, {k: sa[k] for k in pick if k in sa}, pick))
                # recompute with multiplicity of resampled keys
                tc = [0.0, 0, 0.0, 0.0]; ta = [0.0, 0, 0.0, 0.0]
                for sc_, sa_, pick in ia:
                    for k in pick:
                        for t, src in ((tc, sc_), (ta, sa_)):
                            if k in src:
                                for j in range(4):
                                    t[j] += src[k][j]
                if tc[1] and ta[1]:
                    dr.append(ta[0] / ta[1] - tc[0] / tc[1])
                    de.append((ta[2] - ta[3]) / ta[1] - (tc[2] - tc[3]) / tc[1])
            dr.sort(); de.sort()
            entry["roi_diff_vs_champion"] = round(roi - roi_excess(champ)[0], 4)
            entry["roi_diff_ci90_game"] = [round(dr[int(.05 * len(dr))], 4), round(dr[int(.95 * len(dr)) - 1], 4)]
            entry["excess_diff_ci90_game_pp"] = [round(100 * de[int(.05 * len(de))], 2), round(100 * de[int(.95 * len(de)) - 1], 2)]
        res[arm] = entry
        print(arm, entry)
    common.write("posthoc_checks.json", res)


if __name__ == "__main__":
    main()

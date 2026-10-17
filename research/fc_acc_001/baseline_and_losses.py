#!/usr/bin/env python3
"""FC-ACC-001 step 1-2: champion baseline per evidence regime + loss attribution.

Regime A (public ledger, graded via retained history) is the champion the
customer actually received. Loss attribution for HYPOTHESIS GENERATION uses
only public picks dated before 2026-09-20, so that the selector competition
(prospective freezes, 2026-09-20 .. 2026-10-12) is evaluated on dates the
hypotheses were not derived from. Full-period tables are descriptive only.
"""
import collections
import math
import sys
from datetime import datetime

sys.path.insert(0, __import__("os").path.dirname(__file__))
import common  # noqa: E402

EXPLORE_END = "2026-09-20"   # exclusive


def wilson(h, n, z=1.96):
    if n == 0:
        return None
    p = h / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - r) / d, 3), round((c + r) / d, 3)]


def summarize(rows):
    s = [r for r in rows if r["grade"] in ("hit", "miss")]
    h = sum(r["grade"] == "hit" for r in s)
    priced = [r for r in s if r["odds"] is not None]
    pnl = sum((common.american_profit(r["odds"]) if r["grade"] == "hit" else -1.0) for r in priced)
    return {"published": len(rows), "settled": len(s), "hits": h, "misses": len(s) - h,
            "hit_rate": round(h / len(s), 4) if s else None, "ci95": wilson(h, len(s)),
            "priced_settled": len(priced), "units": round(pnl, 2),
            "roi": round(pnl / len(priced), 4) if priced else None,
            "mean_stated_p": round(sum(r["p"] for r in s) / len(s), 4) if s else None,
            "mean_implied_q": round(sum(r["q"] for r in priced if r["q"] is not None) / max(1, sum(r["q"] is not None for r in priced)), 4) if priced else None,
            "void_or_ungraded": sum(r["grade"] not in ("hit", "miss") for r in rows)}


def band(x, edges):
    if x is None:
        return "none"
    for lo, hi in zip(edges, edges[1:]):
        if lo <= x < hi:
            return f"[{lo},{hi})"
    return f">={edges[-1]}"


def odds_band(o):
    if o is None:
        return "unpriced"
    return ("<=-250" if o <= -250 else "-249..-180" if o <= -180 else "-179..-140" if o <= -140
            else "-139..-110" if o <= -110 else "-109..+100" if o <= 100 else ">+100")


def lead_minutes(r):
    if not r["game_start"]:
        return None
    a = datetime.fromisoformat(r["published_at"].replace("Z", "+00:00"))
    b = datetime.fromisoformat(r["game_start"].replace("Z", "+00:00"))
    return (b - a).total_seconds() / 60


def breakdowns(rows):
    # concentration within (date, game) and (date, player)
    per_game = collections.Counter((r["date"], r["game_pk"]) for r in rows)
    per_player = collections.Counter((r["date"], r["player"]) for r in rows)
    per_day = collections.Counter(r["date"] for r in rows)
    dims = {
        "market": lambda r: r["stat"],
        "side": lambda r: r["side"],
        "market_side": lambda r: f"{r['stat']}:{r['side']}",
        "odds_band": lambda r: odds_band(r["odds"]),
        "stated_p_band": lambda r: band(r["p"], [0, .6, .63, .66, .70, .75, 1.01]),
        "model_minus_market": lambda r: band(None if r["q"] is None else r["p"] - r["q"], [-1, 0, .03, .06, .10, .15, 1]),
        "market_disagrees_flag": lambda r: str(r["market_disagrees"]),
        "reliability": lambda r: str(r["reliability"]),
        "sample_n": lambda r: band(r["sample_n"], [0, 10, 20, 40, 80, 10**6]),
        "lead_time_min": lambda r: band(lead_minutes(r), [-10**6, 60, 180, 360, 720, 10**6]),
        "picks_same_game_same_day": lambda r: str(min(per_game[(r["date"], r["game_pk"])], 4)) + ("+" if per_game[(r["date"], r["game_pk"])] >= 4 else ""),
        "picks_same_player_same_day": lambda r: str(min(per_player[(r["date"], r["player"])], 3)) + ("+" if per_player[(r["date"], r["player"])] >= 3 else ""),
        "daily_volume": lambda r: band(per_day[r["date"]], [0, 6, 11, 16, 21, 10**6]),
        "calibration_version_label": lambda r: str(r["calibration_version"]),
    }
    out = {}
    for name, fn in dims.items():
        groups = collections.defaultdict(list)
        for r in rows:
            groups[fn(r)].append(r)
        out[name] = {k: summarize(v) for k, v in sorted(groups.items())}
    return out


def by_date(rows):
    g = collections.defaultdict(list)
    for r in rows:
        g[r["date"]].append(r)
    return {d: summarize(v) for d, v in sorted(g.items())}


def main():
    rows, prov = common.load_public()
    explore = [r for r in rows if r["date"] < EXPLORE_END]
    later = [r for r in rows if r["date"] >= EXPLORE_END]
    res = {
        "provenance": prov,
        "regime": "A_PUBLIC_LEDGER",
        "coverage": {"first_date": min(r["date"] for r in rows), "last_date": max(r["date"] for r in rows),
                     "graded_in_retained_history": sum(r["grade"] in ("hit", "miss") for r in rows),
                     "not_in_retained_history": sum(r["grade"] is None for r in rows),
                     "history_note": "docs/history.json retains 45 days; picks dated 2026-08-18..08-28 and 10-06..10-12 have no retained grade here and are counted, never imputed."},
        "full_period": summarize(rows),
        "explore_pre_2026_09_20": summarize(explore),
        "post_2026_09_20": summarize(later),
        "by_date": by_date(rows),
        "breakdowns_explore": breakdowns(explore),
        "breakdowns_full_descriptive": breakdowns(rows),
    }
    common.write("baseline_public.json", res)
    print("FULL", res["full_period"]); print("EXPLORE", res["explore_pre_2026_09_20"]); print("POST", res["post_2026_09_20"])
    for dim in ("market_side", "odds_band", "stated_p_band", "model_minus_market", "market_disagrees_flag",
                "reliability", "sample_n", "lead_time_min", "picks_same_game_same_day", "picks_same_player_same_day", "daily_volume"):
        print(f"\n-- {dim} (explore, pre-09-20)")
        for k, v in res["breakdowns_explore"][dim].items():
            if v["settled"]:
                print(f"  {k:26s} n={v['settled']:4d} hit={v['hit_rate']:.3f} ci={v['ci95']} roi={v['roi']} q={v['mean_implied_q']} p={v['mean_stated_p']}")


if __name__ == "__main__":
    main()

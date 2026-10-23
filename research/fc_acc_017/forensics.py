#!/usr/bin/env python3
"""FC-ACC-017 Phases 1-3: forensics of the published Top Pick ledger (public evidence only).

Reads out/ledger.json (from ledger.py). Pregame fields come from the immutable publication
snapshot. Postgame fields (actual PA, opportunity) are used ONLY to diagnose losses, never as
selection features. Devigged market probability = market_implied / 1.08 (production's own
ASSUMED_PROP_HOLD; the feed is one-sided, so this is an approximation, as production states).
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
HOLD = 1.08


def dec(a):
    return 1 + (100 / -a if a < 0 else a / 100)


def txt(s):
    return " ".join((s.get("watchouts") or []) + (s.get("why") or []))


def logistic(X, y, iters=60):
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        q = 1 / (1 + np.exp(-X @ w))
        H = (X * (q * (1 - q))[:, None]).T @ X + 1e-9 * np.eye(X.shape[1])
        w -= np.linalg.solve(H, X.T @ (q - y))
    return w, np.sqrt(np.diag(np.linalg.inv(H)))


def main():
    L = json.loads((HERE / "out" / "ledger.json").read_text())
    rows = L["rows"]
    R = [r for r in rows if r["grade"] in ("hit", "miss")]
    S = [r["snapshot"] for r in R]
    y = np.array([r["grade"] == "hit" for r in R], float)
    p = np.array([s["hit_probability"] for s in S])
    fair = np.array([s["market_implied"] / HOLD for s in S])
    odds = np.array([s["market_odds"] for s in S], float)
    profit = np.array([(dec(o) - 1) if h else -1.0 for o, h in zip(odds, y)])

    def cell(mask):
        n = int(mask.sum())
        return {"n": n, "hit": float(y[mask].mean()) if n else None, "model": float(p[mask].mean()) if n else None,
                "fair": float(fair[mask].mean()) if n else None, "roi": float(profit[mask].mean()) if n else None}

    def by(keyf):
        keys = [keyf(r) for r in R]
        return {str(k): cell(np.array([kk == k for kk in keys])) for k in sorted(set(keys), key=str)}

    def bands(x, edges):
        return {f"[{lo},{hi})": cell((x >= lo) & (x < hi)) for lo, hi in zip(edges[:-1], edges[1:])}

    def market(r):
        pr = r["snapshot"]["projection"]
        return f"{pr['stat']}:{pr['needs']}"

    def slot(r):
        m = re.search(r"batting slot (\d)", txt(r["snapshot"]))
        return ("P" if r["snapshot"]["type"] == "pitcher" else (int(m.group(1)) if m else None))
    per_slate = Counter(r["slate_date"] for r in rows)
    slate_dates = sorted(per_slate)
    games = Counter((r["slate_date"], r["snapshot"]["game_pk"]) for r in R)
    players = Counter((r["slate_date"], r["snapshot"]["player_id"]) for r in R)
    pair_outcomes = Counter()
    for k, c in players.items():
        if c > 1:
            pair_outcomes[tuple(sorted(r["grade"] for r in R if (r["slate_date"], r["snapshot"]["player_id"]) == k))] += 1
    lg = lambda q: np.log(q / (1 - q))  # noqa: E731
    w, se = logistic(np.column_stack([np.ones_like(p), lg(np.clip(fair, 0.05, 0.95)), lg(p)]), y)

    def ll(q):
        q = np.clip(q, 0.01, 0.99)
        return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))
    bat = [r for r in R if r["snapshot"]["type"] == "batter"]
    proj = [(float(m.group(1)), r["post"].get("actual_pa_est"), r["grade"]) for r in bat
            for m in [re.search(r"Projected ([\d.]+) PA", txt(r["snapshot"]))] if m and r["post"].get("actual_pa_est") is not None]
    last = slate_dates[-14:]
    res = {
        "phase1": {
            "counts": dict(Counter(r["grade"] for r in rows)), "slates": len(slate_dates),
            "first_slate": slate_dates[0], "last_slate": slate_dates[-1],
            "settled": len(R), "wins": int(y.sum()), "losses": int((1 - y).sum()), "hit_rate": float(y.mean()),
            "through_2026_10_18": [int(sum(r["grade"] == "hit" for r in R if r["slate_date"] <= "2026-10-18")),
                                   int(sum(r["grade"] == "miss" for r in R if r["slate_date"] <= "2026-10-18"))],
            "roi_flat_stake_published_price": float(profit.mean()), "profit_units": float(profit.sum()),
            "mean_model_probability": float(p.mean()), "mean_devig_market_probability": float(fair.mean()),
            "by_market": by(market), "by_month": by(lambda r: r["slate_date"][:7]),
            "last_14_slates": cell(np.array([r["slate_date"] in last for r in R])),
            "picks_per_slate": {"median": float(np.median(list(per_slate.values()))), "max": max(per_slate.values()),
                                "min": min(per_slate.values())},
            "by_model_probability": bands(p, [0.60, 0.62, 0.64, 0.66, 0.68, 0.70, 0.75, 0.95]),
            "correlation": {"picks_sharing_game": int(sum(c for c in games.values() if c > 1)),
                            "same_player_multi_pick_slates": int(sum(1 for c in players.values() if c > 1)),
                            "same_player_outcome_combos": {"+".join(k): v for k, v in pair_outcomes.items()}}},
        "phase2": {
            "expected_losses_under_model": float((1 - p).sum()), "expected_losses_under_market": float((1 - fair).sum()),
            "observed_losses": int((1 - y).sum()),
            "all_top_picks_flagged_market_disagreement": int(sum(any("market itself disagrees" in x for x in s.get("status_reasons") or [])
                                                                 for s in S)),
            "logloss_model": ll(p), "logloss_market": ll(fair),
            "brier_model": float(np.mean((p - y) ** 2)), "brier_market": float(np.mean((fair - y) ** 2)),
            "joint_logit_intercept_market_model": {"coef": w.tolist(), "se": se.tolist()},
            "opportunity_postgame_diagnostic": {"n": len(proj), "projected_pa_mean": float(np.mean([a for a, _, _ in proj])),
                                                "actual_pa_mean": float(np.mean([b for _, b, _ in proj])),
                                                "share_pa_le3_losses": float(np.mean([b <= 3 for _, b, g in proj if g == "miss"])),
                                                "share_pa_le3_wins": float(np.mean([b <= 3 for _, b, g in proj if g == "hit"]))}},
        "phase3": {
            "by_devig_market": bands(fair, [0.30, 0.45, 0.50, 0.55, 0.60, 0.65, 0.80]),
            "by_model_minus_market_gap": bands(p - fair, [-0.2, 0.05, 0.08, 0.11, 0.14, 0.18, 0.6]),
            "by_price": bands(odds, [-1000, -250, -200, -160, -130, -110, 100, 2000]),
            "by_player_type": by(lambda r: r["snapshot"]["type"]),
            "by_batting_slot": by(slot),
            "by_reliability": by(lambda r: r["snapshot"].get("reliability")),
            "by_interval_width": by(lambda r: None if not r["snapshot"].get("prob_ci") else
                                    ("<0.08" if r["snapshot"]["prob_ci"][1] - r["snapshot"]["prob_ci"][0] < 0.08 else ">=0.08")),
            "by_slate_volume": by(lambda r: "1-5" if per_slate[r["slate_date"]] <= 5 else
                                  ("6-15" if per_slate[r["slate_date"]] <= 15 else "16+")),
            "lineup_assumed_values": dict(Counter(str(s.get("lineup_assumed")) for s in S))}}
    (HERE / "out" / "forensics.json").write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    r = main()
    print(json.dumps(r["phase1"]["counts"]), r["phase1"]["hit_rate"], r["phase2"]["expected_losses_under_market"])

#!/usr/bin/env python3
"""diagnose_live_deploy_zero_top_picks.py -- 2026-08-24 22:22 UTC live deploy
still shows 0 Top Picks despite the CI-eligibility fix (commit f5fae265)
being merged AND deployed (dashboard-refresh run #212, completed 22:27 UTC).
This runs the full gate waterfall against the REAL deployed payload using
THIS checkout's recommendation.py/prop_probability.py directly (this
checkout IS what was deployed -- no /tmp/prod_diag needed), including the
sample_n==0 gate, to find the real bottleneck rather than assume "no bets
qualify."

    /tmp/mlbvenv/bin/python3 backtest/diagnose_live_deploy_zero_top_picks.py <payload.json>
"""
import datetime
import json
import sys

sys.path.insert(0, ".")
import recommendation as rec  # noqa: E402
import prop_probability as pp  # noqa: E402


def main(path):
    d = json.load(open(path))
    props = d["props"]
    generated_at = d.get("generated_at")
    odds_fetched_at = d.get("odds_fetched_at")
    now = datetime.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    fresh, fresh_reasons = rec.freshness_check(
        now=now, odds_fetched_at=odds_fetched_at, board_generated_at=generated_at)

    print(f"generated_at={generated_at}  odds_fetched_at={odds_fetched_at}")
    print(f"board-level freshness: fresh={fresh} reasons={fresh_reasons}")
    print(f"n props total: {len(props)}\n")

    mismatches = []
    n_checked = 0
    for p in props:
        prob = p.get("hit_probability")
        if prob is None:
            continue
        n_checked += 1
        cand = {
            "hit_probability": prob, "reliability": p.get("reliability"),
            "lineup_assumed": p.get("lineup_assumed"), "lift": p.get("lift"),
            "market_odds": p.get("market_odds"), "prob_ci": p.get("prob_ci"),
            "sample_n": p.get("sample_n"),
        }
        result = rec.classify_recommendation(cand, now=now, data_fresh=fresh,
                                             fresh_reasons=fresh_reasons)
        live_status = p.get("recommendation_status")
        if result["status"] != live_status:
            mismatches.append((p.get("name"), p.get("stat"), live_status, result["status"]))

    print("=" * 100)
    print(f"PUBLICATION CROSS-CHECK: re-running classify_recommendation() against "
          f"{n_checked} real, priced candidates")
    print("=" * 100)
    if not mismatches:
        print("  ZERO mismatches -- live payload matches classify_recommendation() exactly. "
              "0 Top Picks is real, not a publication/dashboard bug.")
    else:
        print(f"  {len(mismatches)} MISMATCHES:")
        for name, stat, live, recomputed in mismatches[:20]:
            print(f"    {name} ({stat}): live={live!r} recomputed={recomputed!r}")

    candidates = [p for p in props if p.get("hit_probability") is not None]

    def has_confirmed_lineup(p):
        return not p.get("lineup_assumed")

    def has_evidence(p):
        return p.get("sample_n") != 0

    def prob_ok(p):
        return p.get("hit_probability", 0) >= rec.TOP_PICK_MIN_PROB

    def reliability_ok(p):
        return p.get("reliability") in rec.TOP_PICK_MIN_RELIABILITY

    def has_price(p):
        return p.get("market_odds") is not None

    def roi_ok(p):
        if not has_price(p):
            return False
        roi = pp.expected_roi(p["hit_probability"], p["market_odds"])
        return roi >= rec.TOP_PICK_MIN_ROI

    def has_ci(p):
        return p.get("prob_ci") is not None

    def ci_robust_ok(p):
        if not has_price(p) or not has_ci(p):
            return False
        lo = p["prob_ci"][0]
        return pp.expected_roi(lo, p["market_odds"]) > 0

    def survives_all(p):
        cand = {
            "hit_probability": p.get("hit_probability"), "reliability": p.get("reliability"),
            "lineup_assumed": p.get("lineup_assumed"), "lift": p.get("lift"),
            "market_odds": p.get("market_odds"), "prob_ci": p.get("prob_ci"),
            "sample_n": p.get("sample_n"),
        }
        return rec.classify_recommendation(cand, now=now, data_fresh=fresh,
                                           fresh_reasons=fresh_reasons)["status"] == "top_pick"

    gates = [
        ("confirmed lineup", has_confirmed_lineup),
        ("real evidence (sample_n != 0)", has_evidence),
        ("hit probability >= 60%", prob_ok),
        ("A/B reliability", reliability_ok),
        ("real posted price", has_price),
        ("passes freshness (board-level)", lambda p: fresh),
        ("passes ROI floor", roi_ok),
        ("has a usable CI", has_ci),
        ("pessimistic CI bound passes robustness test", ci_robust_ok),
    ]

    print("\n" + "=" * 100)
    print(f"REQUIREMENT-BY-REQUIREMENT FUNNEL (n={len(candidates)} real, priced candidates)")
    print("=" * 100)
    for label, fn in gates:
        n = sum(1 for p in candidates if fn(p))
        print(f"  {label:50s} {n:5d} / {len(candidates)}  ({n/len(candidates):.1%})")
    n_survive = sum(1 for p in candidates if survives_all(p))
    print(f"  {'SURVIVING ALL REQUIREMENTS (real top_pick)':50s} {n_survive:5d} / "
          f"{len(candidates)}  ({n_survive/len(candidates):.1%})")

    scored = []
    for p in candidates:
        checks = {label: fn(p) for label, fn in gates}
        n_failed = sum(1 for v in checks.values() if not v)
        prob_gap = max(0.0, rec.TOP_PICK_MIN_PROB - p.get("hit_probability", 0))
        roi_gap = 0.0
        if has_price(p):
            roi = pp.expected_roi(p["hit_probability"], p["market_odds"])
            roi_gap = max(0.0, rec.TOP_PICK_MIN_ROI - roi)
        closeness = prob_gap + roi_gap
        failed_labels = [label for label, ok in checks.items() if not ok]
        scored.append((n_failed, closeness, p, failed_labels))

    scored.sort(key=lambda t: (t[0], t[1]))

    print("\n" + "=" * 100)
    print("BEST 15 NEAR-MISSES (fewest failed gates, then closest on probability/ROI)")
    print("=" * 100)
    for n_failed, closeness, p, failed_labels in scored[:15]:
        odds = p.get("market_odds")
        implied = pp.implied_probability(odds) if odds is not None else None
        edge = (p["hit_probability"] - implied) if implied is not None else None
        print(f"\n  {p.get('name')} -- {p.get('prop')}  (game_pk={p.get('game_pk')})")
        print(f"    probability={p['hit_probability']:.4f}  odds={odds}  "
              f"implied={implied if implied is None else round(implied,4)}  "
              f"edge={edge if edge is None else f'{edge:+.4f}'}")
        print(f"    reliability={p.get('reliability')}  ci={p.get('prob_ci')}  "
              f"lineup_assumed={p.get('lineup_assumed')}  sample_n={p.get('sample_n')}")
        print(f"    live recommendation_status={p.get('recommendation_status')!r}  "
              f"live status_reasons={p.get('status_reasons')}")
        print(f"    FAILED GATES ({n_failed}): {failed_labels}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/today_live.json")

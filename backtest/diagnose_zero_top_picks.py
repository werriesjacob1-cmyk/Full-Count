#!/usr/bin/env python3
"""diagnose_zero_top_picks.py -- live, direct diagnosis of tonight's 0-Top-Pick
board, run against PRODUCTION's exact recommendation.py/prop_probability.py
(fetched via `git show origin/main:...` into /tmp/prod_diag, NOT this
branch's modified recommendation.py, which adds a sample_n==0 gate not yet
deployed) -- so this measures what the live site is ACTUALLY doing, not what
this branch would do.

Requirement-by-requirement funnel + best-10 near-misses, using the real
classify_recommendation() function itself (not a reimplementation), plus a
direct check that the payload's own recommendation_status field agrees with
what classify_recommendation() returns when re-run on the same data --
ruling out a publication/dashboard mismatch.

    /tmp/mlbvenv/bin/python3 backtest/diagnose_zero_top_picks.py /tmp/today_live.json
"""
import datetime
import json
import sys

sys.path.insert(0, "/tmp/prod_diag")
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
    print(f"board-level freshness (real freshness_check()): fresh={fresh} reasons={fresh_reasons}")
    print(f"TOP_PICK_MIN_PROB={rec.TOP_PICK_MIN_PROB} TOP_PICK_MIN_RELIABILITY="
          f"{rec.TOP_PICK_MIN_RELIABILITY} TOP_PICK_MIN_ROI={rec.TOP_PICK_MIN_ROI} "
          f"LEAN_MIN_LIFT={rec.LEAN_MIN_LIFT}")
    print(f"n props total: {len(props)}\n")

    # ── 1. CROSS-CHECK: does the payload's own recommendation_status agree
    # with re-running the real classify_recommendation() on the same data?
    # Rules out a publication/dashboard mismatch (stale cache, wrong function
    # version shipped, a dashboard-side override) as the actual cause.
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
        }
        result = rec.classify_recommendation(cand, now=now, data_fresh=fresh,
                                             fresh_reasons=fresh_reasons)
        live_status = p.get("recommendation_status")
        if result["status"] != live_status:
            mismatches.append((p.get("name"), p.get("stat"), live_status, result["status"]))

    print("=" * 100)
    print(f"PUBLICATION CROSS-CHECK: re-running the real classify_recommendation() against "
          f"{n_checked} real, priced candidates")
    print("=" * 100)
    if not mismatches:
        print("  ZERO mismatches -- the live payload's recommendation_status field is exactly "
              "what classify_recommendation() itself returns on this data. No publication/"
              "dashboard bug; 0 Top Picks is a real, direct consequence of what the "
              "requirements gate returns, not a display/pipeline wiring problem.")
    else:
        print(f"  {len(mismatches)} MISMATCHES found -- publication does NOT match "
              f"classify_recommendation()'s real output:")
        for name, stat, live, recomputed in mismatches[:20]:
            print(f"    {name} ({stat}): live shows {live!r}, classify_recommendation() says "
                  f"{recomputed!r}")

    # ── 2. THE REQUIREMENT-BY-REQUIREMENT FUNNEL ──
    candidates = [p for p in props if p.get("hit_probability") is not None]

    def has_confirmed_lineup(p):
        return not p.get("lineup_assumed")

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
        }
        return rec.classify_recommendation(cand, now=now, data_fresh=fresh,
                                           fresh_reasons=fresh_reasons)["status"] == "top_pick"

    gates = [
        ("confirmed lineup", has_confirmed_lineup),
        ("hit probability >= 60%", prob_ok),
        ("A/B reliability", reliability_ok),
        ("real posted price", has_price),
        ("passes freshness (board-level)", lambda p: fresh),
        ("passes ROI floor (market/value requirement)", roi_ok),
        ("has a usable CI", has_ci),
        ("pessimistic CI bound passes robustness test", ci_robust_ok),
    ]

    print("\n" + "=" * 100)
    print(f"REQUIREMENT-BY-REQUIREMENT FUNNEL (n={len(candidates)} real, priced candidates -- "
          f"every prop with a real hit_probability)")
    print("=" * 100)
    for label, fn in gates:
        n = sum(1 for p in candidates if fn(p))
        print(f"  {label:50s} {n:5d} / {len(candidates)}  ({n/len(candidates):.1%})")
    n_survive = sum(1 for p in candidates if survives_all(p))
    print(f"  {'SURVIVING ALL REQUIREMENTS (real top_pick)':50s} {n_survive:5d} / "
          f"{len(candidates)}  ({n_survive/len(candidates):.1%})")

    # ── 3. NEAR MISSES: rank by number of failed gates, then by how close ──
    scored = []
    for p in candidates:
        checks = {label: fn(p) for label, fn in gates}
        n_failed = sum(1 for v in checks.values() if not v)
        # "closeness" tie-breaker: probability distance below the 60% floor
        # (0 if already above it) plus, when priced, ROI distance below the
        # floor -- both real, comparable, dimensionless-ish gaps.
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
    print("BEST 10 NEAR-MISSES (fewest failed gates, then closest on probability/ROI)")
    print("=" * 100)
    for n_failed, closeness, p, failed_labels in scored[:10]:
        odds = p.get("market_odds")
        implied = pp.implied_probability(odds) if odds is not None else None
        edge = (p["hit_probability"] - implied) if implied is not None else None
        print(f"\n  {p.get('name')} -- {p.get('prop')}")
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

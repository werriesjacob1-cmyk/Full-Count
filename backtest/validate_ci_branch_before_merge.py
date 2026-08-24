#!/usr/bin/env python3
"""validate_ci_branch_before_merge.py -- final production-equivalent
validation before merging accuracy/ci-eligibility-and-lineup-timing.

Compares PRODUCTION's real classify_recommendation() (from origin/main,
/tmp/prod_diag) against THIS BRANCH's real classify_recommendation() +
historical_prob_ci(), both run against the exact same live payload, so
the diff is attributable ONLY to this branch's code changes -- nothing
else about the data differs between the two runs.

    /tmp/mlbvenv/bin/python3 backtest/validate_ci_branch_before_merge.py /tmp/today_live_final.json
"""
import datetime
import json
import sys
from collections import Counter, defaultdict

sys.path.insert(0, ".")
import generate_picks as gp  # noqa: E402 -- THIS branch's version
import recommendation as branch_rec  # noqa: E402 -- THIS branch's version
import prop_probability as pp  # noqa: E402 -- identical on both

sys.path.insert(0, "/tmp/prod_diag")
import recommendation as prod_rec  # noqa: E402 -- origin/main's version, unmodified


def classify_with(rec_module, cand, now, fresh, fresh_reasons):
    return rec_module.classify_recommendation(cand, now=now, data_fresh=fresh,
                                               fresh_reasons=fresh_reasons)


def main(path):
    d = json.load(open(path))
    props = d["props"]
    generated_at = d.get("generated_at")
    odds_fetched_at = d.get("odds_fetched_at")
    now = datetime.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))

    prod_fresh, prod_reasons = prod_rec.freshness_check(
        now=now, odds_fetched_at=odds_fetched_at, board_generated_at=generated_at)
    branch_fresh, branch_reasons = branch_rec.freshness_check(
        now=now, odds_fetched_at=odds_fetched_at, board_generated_at=generated_at)
    assert prod_fresh == branch_fresh, "freshness_check itself differs between branches -- unexpected"

    print(f"generated_at={generated_at}  fresh={prod_fresh}  n_props={len(props)}")
    bands = gp.load_reliability_bands()
    print(f"reliability_bands.json currently loaded: {sum(len(v) for v in bands.values())} "
          f"reportable cells across {len(bands)} (stat,needs) keys\n")

    candidates = [p for p in props if p.get("hit_probability") is not None]

    prod_results = {}
    branch_results = {}
    ci_audit_failures = []

    for p in candidates:
        key = (p.get("player_id"), p.get("stat"), (p.get("projection") or {}).get("needs"),
               p.get("name"))
        base_cand = {
            "hit_probability": p.get("hit_probability"), "reliability": p.get("reliability"),
            "lineup_assumed": p.get("lineup_assumed"), "lift": p.get("lift"),
            "market_odds": p.get("market_odds"), "sample_n": p.get("sample_n"),
        }

        # PRODUCTION: exactly the CI already in the live payload, nothing added.
        prod_cand = dict(base_cand, prob_ci=p.get("prob_ci"))
        prod_results[key] = classify_with(prod_rec, prod_cand, now, prod_fresh, prod_reasons)

        # THIS BRANCH: same starting prob_ci, but if it's None, try the real
        # historical_prob_ci() fallback -- exactly what attach_reliability()
        # does, applied here directly since we don't have the live emp
        # tables to re-run attach_reliability() itself end to end.
        ci = p.get("prob_ci")
        ci_source = None
        if ci is None:
            stat = p.get("stat")
            needs = (p.get("projection") or {}).get("needs")
            hci = gp.historical_prob_ci(stat, needs, p.get("hit_probability"))
            if hci is not None:
                ci = hci
                ci_source = "historical_reliability_band"
                # AUDIT: verify the exact band cell this came from really
                # does clear MIN_RELIABILITY_BAND_N -- catches any future
                # regression where historical_prob_ci might return a
                # cheating interval below the floor.
                band_stat = "home_run" if stat == "home_runs" else stat
                cell_group = bands.get(f"{band_stat}_{int(needs)}") if needs is not None else None
                bucket = round(min(max(int(float(p['hit_probability']) // 0.05) * 0.05, 0.0),
                                   0.95), 2)
                cell = (cell_group or {}).get(f"{bucket:.2f}")
                if not cell or cell.get("n", 0) < gp.MIN_RELIABILITY_BAND_N:
                    ci_audit_failures.append((p.get("name"), stat, needs, cell))
        branch_cand = dict(base_cand, prob_ci=ci)
        branch_results[key] = (classify_with(branch_rec, branch_cand, now, branch_fresh,
                                             branch_reasons), ci, ci_source)

    print("=" * 100)
    print(f"CI-COVERAGE AUDIT: every candidate that got a prob_ci from the historical band "
          f"mechanism -- confirming its cell really cleared n>={gp.MIN_RELIABILITY_BAND_N}")
    print("=" * 100)
    if ci_audit_failures:
        print(f"  FAILURE: {len(ci_audit_failures)} candidates got a CI from a cell below the "
              f"coverage floor -- THIS WOULD BE A BUG:")
        for name, stat, needs, cell in ci_audit_failures:
            print(f"    {name} {stat} needs={needs}: cell={cell}")
    else:
        n_from_band = sum(1 for _, _, src in branch_results.values()
                          if src == "historical_reliability_band")
        print(f"  PASS: {n_from_band} candidates received a prob_ci from the historical band "
              f"mechanism, and every single one's source cell independently verified to have "
              f"n >= {gp.MIN_RELIABILITY_BAND_N} real historical rows. Zero exceptions.")

    print("\n" + "=" * 100)
    print("STATUS DELTA: production vs this branch, same live data")
    print("=" * 100)
    delta_counts = Counter()
    changes = []
    for key, prod_r in prod_results.items():
        branch_r, ci, ci_source = branch_results[key]
        if prod_r["status"] != branch_r["status"]:
            delta_counts[(prod_r["status"], branch_r["status"])] += 1
            changes.append((key, prod_r, branch_r, ci, ci_source))

    if not changes:
        print("  ZERO status changes -- this branch produces an IDENTICAL board to production "
              "on this data. (Not expected -- re-check inputs.)")
    else:
        for (old, new), n in delta_counts.most_common():
            print(f"  {old} -> {new}: {n} candidate(s)")
        print(f"\n  Total candidates whose status changed: {len(changes)}")

    print("\n" + "=" * 100)
    print("FULL DETAIL on every status change (should be a short, explainable list)")
    print("=" * 100)
    for key, prod_r, branch_r, ci, ci_source in changes:
        player_id, stat, needs, name = key
        print(f"\n  {name} -- {stat} (needs={needs})")
        print(f"    production: {prod_r['status']!r}  reasons={prod_r['status_reasons']}")
        print(f"    branch:     {branch_r['status']!r}  reasons={branch_r['status_reasons']}")
        print(f"    ci={ci}  ci_source={ci_source}")

    print("\n" + "=" * 100)
    print("SPECIFIC CLAIMS TO VERIFY")
    print("=" * 100)
    for target_name, target_stat in [("Cal Raleigh", "hits_runs_rbis"),
                                     ("Weston Wilson", "hits_runs_rbis"),
                                     ("Taylor Ward", "hits_runs_rbis"),
                                     ("Kevin Gausman", "pitcher_outs"),
                                     ("Otto Lopez", "hits_runs_rbis")]:
        for key, (branch_r, ci, ci_source) in branch_results.items():
            player_id, stat, needs, name = key
            if name == target_name and stat == target_stat:
                print(f"  {name} ({stat}, needs={needs}): branch status={branch_r['status']!r} "
                      f"ci={ci} ci_source={ci_source}")
                print(f"    reasons={branch_r['status_reasons']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/today_live_final.json")

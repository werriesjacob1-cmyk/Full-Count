#!/usr/bin/env python3
"""READ-ONLY probe: measure LIVE PA-v1 feature availability and cell resolution.

Mission 1.1 section 15 requires proving the LIVE features PA-v1 consumes have
the same semantic decoding and fallback rules as the historical training
representation. The structural argument is strong -- both regimes read
`signals[...]` written by the SAME `generate_picks._sig()` calls, and both
decode through the SAME `backtest.pa_v1_fit` group functions -- but a
structural argument is not a measurement.

This script runs one real live scoring pass and reports, for real Hits
candidates:
  * how often each of the three PA-v1 features is present at all;
  * the observed domain of each stored value;
  * how many candidates resolve to a full joint cell vs the order-only
    marginal vs unscorable.

It writes nothing into the repository and changes no production behaviour.
Output goes to stdout and, optionally, to a path given as argv[1].
"""

import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import generate_picks as gp  # noqa: E402
from backtest.pa_v1_fit import (  # noqa: E402
    derive_batting_order,
    days_rest_group,
    getaway_day_group,
    joint_key,
    score,
)

FEATURES = ("lineup_slot", "days_rest", "getaway_day")


def main():
    artifact = json.load(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "backtest", "pa_v1_fitted_artifact.json")))
    joint = artifact["tables"]["joint_pa_table"]

    # _build_and_score() returns (candidates, ctx). These are pre-quality-
    # control candidates, which is exactly right here: parity is about how the
    # three PA-v1 features are ENCODED and DECODED, not about eligibility.
    candidates, _ctx = gp._build_and_score()

    hits = [c for c in candidates
            if (c.get("projection") or {}).get("stat") == "hits"
            or any(o.get("stat") == "hits" for o in (c.get("line_options") or []))]

    present = collections.Counter()
    domains = {f: collections.Counter() for f in FEATURES}
    groups = {"order": collections.Counter(), "days_rest": collections.Counter(),
              "getaway": collections.Counter()}
    resolution = collections.Counter()

    for c in hits:
        sig = c.get("signals") or {}
        for f in FEATURES:
            if f in sig:
                present[f] += 1
                domains[f][sig[f]] += 1
        order = derive_batting_order(sig.get("lineup_slot"))
        groups["order"][order] += 1
        groups["days_rest"][days_rest_group(sig)] += 1
        groups["getaway"][getaway_day_group(sig)] += 1
        k = joint_key(sig)
        if k is None:
            resolution["no_joint_key"] += 1
        elif "|".join(str(p) for p in k) in joint:
            resolution["joint_cell_present"] += 1
        else:
            resolution["joint_key_but_cell_absent"] += 1
        resolution["scored" if score(sig, artifact) is not None else "unscorable"] += 1

    report = {
        "hits_candidates": len(hits),
        "feature_present_count": dict(present),
        "feature_present_rate": {f: (present[f] / len(hits) if hits else None)
                                 for f in FEATURES},
        "observed_domains": {f: dict(sorted(domains[f].items(),
                                            key=lambda kv: str(kv[0])))
                             for f in FEATURES},
        "decoded_groups": {k: {str(a): b for a, b in v.items()}
                           for k, v in groups.items()},
        "cell_resolution": dict(resolution),
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

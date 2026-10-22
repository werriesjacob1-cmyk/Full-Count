#!/usr/bin/env python3
"""Calibration governance invariant (2026-10-12 audit).

Production calibrators (backtest/calibrators_by_market.json) must equal an
APPROVED, versioned entry in backtest/calibration_registry.json -- market for
market: method, fitted params and supported region -- and
recommendation.CALIBRATION_VERSION must equal that entry's version.

Why: between 2026-09-14 and 2026-10-12 the weekly calibration-recheck bot
changed shipped strikeouts/pitcher_outs probabilities five times with no
version bump and no review, contrary to engineering/AUDIT/README.md. The only
alarm was two tests that pinned three markets' coefficients by accident; this
test states the intended invariant explicitly and covers EVERY market.

A failure here is a governance finding, not a test to "update": resolve it
by (a) restoring an approved calibrator set, or (b) a reviewed PR adding a
new approved registry entry with a bumped CALIBRATION_VERSION and Jacob's
authorization. Never by copying the bot's latest numbers into the registry.
"""
import hashlib
import json
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import recommendation as rec  # noqa: E402

n_pass = n_total = 0


def check(cond, msg, detail=""):
    global n_pass, n_total
    n_total += 1
    if cond:
        n_pass += 1
        print(f"  [PASS] {msg}")
    else:
        print(f"  [FAIL] {msg}" + (f"\n         {detail}" if detail else ""))


def canon(entry):
    bins = [b for b in (entry.get("meta") or {}).get("support_bins", []) if b.get("supported")]
    return {"method": entry.get("method"), "params": entry.get("params"),
            "supported_region": [min(b["lo"] for b in bins), max(b["hi"] for b in bins)] if bins else None}


def same(a, b):
    if a.keys() != b.keys():
        return False
    for m in a:
        x, y = a[m], b[m]
        if x["method"] != y["method"] or x["supported_region"] != y["supported_region"]:
            return False
        if x["params"].keys() != y["params"].keys():
            return False
        if any(abs(float(x["params"][k]) - float(y["params"][k])) > 1e-12 for k in x["params"]):
            return False
    return True


def diff(live, approved):
    out = []
    for m in sorted(set(live) | set(approved)):
        if m not in approved:
            out.append(f"{m}: UNAPPROVED market in production {live[m]}")
        elif m not in live:
            out.append(f"{m}: approved calibrator missing from production")
        elif not same({m: live[m]}, {m: approved[m]}):
            out.append(f"{m}: production {live[m]} != approved {approved[m]}")
    return out


def sha256_of(rel):
    with open(os.path.join(HERE, rel), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


registry = json.load(open(os.path.join(HERE, "backtest", "calibration_registry.json")))
live_raw = json.load(open(os.path.join(HERE, "backtest", "calibrators_by_market.json")))
live = {m: canon(e) for m, e in live_raw.items()}

print("== 1. every approved registry entry is reproducible from its committed fixture")
for entry in registry["approved"]:
    fx = json.load(open(os.path.join(HERE, entry["fixture"])))
    check(same({m: canon(e) for m, e in fx.items()}, entry["markets"]),
          f"approved v{entry['calibration_version']} matches fixture {entry['fixture']}")
    check(sha256_of(entry["fixture"]) == entry["source_file_sha256"],
          f"approved v{entry['calibration_version']} fixture bytes equal recorded digest "
          f"{entry['source_file_sha256'][:12]} (source commit {entry['source_commit'][:10]})")
    check(bool((entry.get("authorization") or {}).get("by")),
          f"approved v{entry['calibration_version']} records who authorized it")

print("== 2. PRODUCTION calibrators equal an approved, versioned entry")
match = [e for e in registry["approved"] if same(live, e["markets"])]
latest = registry["approved"][-1]
check(bool(match),
      "backtest/calibrators_by_market.json equals an APPROVED registry entry (market for market)",
      "UNAPPROVED PRODUCTION CALIBRATION -- Jacob decision required (restore approved set, or "
      "approve a new versioned entry):\n         " + "\n         ".join(diff(live, latest["markets"])))
if match:
    check(rec.CALIBRATION_VERSION == match[0]["calibration_version"],
          f"recommendation.CALIBRATION_VERSION ({rec.CALIBRATION_VERSION}) equals the approved "
          f"entry's version ({match[0]['calibration_version']})")

print("== 2b. production bytes equal an approved artifact exactly (any meta/support edit needs an entry)")
prod_sha = sha256_of(os.path.join("backtest", "calibrators_by_market.json"))
check(prod_sha in {e["source_file_sha256"] for e in registry["approved"]},
      f"production calibrators_by_market.json sha256 {prod_sha[:12]} is an approved artifact digest")

print("== 2c. every unapproved bot-written version that shipped is preserved byte-exact and stays unapproved")
approved_digests = {e["source_file_sha256"] for e in registry["approved"]}
for u in registry["unapproved_history"]:
    check(sha256_of(u["fixture"]) == u["file_sha256"],
          f"audit record {u['date']} {u['commit'][:10]} preserved byte-exact")
    check(u["file_sha256"] not in approved_digests,
          f"audit record {u['date']} {u['commit'][:10]} is not listed as approved")
check(prod_sha not in {u["file_sha256"] for u in registry["unapproved_history"]},
      "production is not any recorded unapproved bot-written version")

print("== 3. the weekly recheck can only write candidates, never production")
wf = yaml.safe_load(open(os.path.join(HERE, ".github", "workflows", "calibration-recheck.yml")))
steps = wf["jobs"]["recheck"]["steps"]
runs = "\n".join(str(s.get("run", "")) for s in steps)
check("refit_calibrators.py --dry-run" in " ".join(runs.split()),
      "calibration-recheck runs refit_calibrators.py with --dry-run (no production write)")
check("git add backtest/calibrators_by_market.json" not in runs
      and "calibrators_by_market.json" not in [t for s in steps for t in str(s.get("run", "")).split()
                                               if t.startswith("backtest/calibrators")],
      "calibration-recheck never stages backtest/calibrators_by_market.json")

print(f"\nRESULT: {n_pass}/{n_total} checks passed")
sys.exit(0 if n_pass == n_total else 1)

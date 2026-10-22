#!/usr/bin/env python3
"""FC-ACC-015 outcome-scramble invariance test (PLAN.md §2).

For each fixed target date D: every outcome statistic of every batter game and
pitcher start dated ON OR AFTER D is replaced with random values, the FROZEN
FC-ACC-014 feature builder (research/fc_acc_014/features.py, unmodified) is
rerun on a copy, and every target-date feature and M3 ingredient must be
bit-identical to the reproduced originals. Labels (y) and post-game strata
(outs) are expected to change and are excluded from the comparison.

Usage:
  python3 leak_scramble.py <fc_acc_014 dir with out/ rebuilt> <canonical rows.jsonl> <work dir>
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

DATES = ("2025-05-15", "2025-07-20", "2025-09-10", "2026-04-25", "2026-06-15", "2026-08-10")
BAT_STATS = ("pa", "ab", "h", "d2", "d3", "hr", "bb", "so", "tb", "r", "hbp")
PIT_STATS = ("pitches", "bf", "outs", "ks", "h", "d2", "d3", "hr", "bb")
SEED = 20261022


def scramble(rows, stats, d, rng):
    n = 0
    for r in rows:
        if r["date"] >= d:
            for k in stats:
                r[k] = rng.randint(1, 9) if k in ("pa", "bf", "pitches") else rng.randint(0, 6)
            n += 1
    return n


def key(r):
    return (r["date"], int(r["game_pk"]), int(r["player_id"]))


def comparable(rec):
    return json.dumps({"f": rec["f"], "m3": rec["m3"]}, sort_keys=True)


def load_target(path, d):
    out = {}
    with open(path) as fh:
        for line in fh:
            r = json.loads(line)
            if r["date"] == d:
                out[key(r)] = comparable(r)
    return out


def main(src, canon, work):
    src, work = Path(src).resolve(), Path(work).resolve()
    repo = src.parents[1]
    result = {"dates": {}, "seed": SEED}
    for d in DATES:
        root = work / d
        if root.exists():
            shutil.rmtree(root)
        mod = root / "research" / "fc_acc_014"
        (mod / "out").mkdir(parents=True)
        for f in ("features.py", "fc013_build.py"):
            shutil.copy2(src / f, mod / f)
        os.symlink(repo / "prop_probability.py", root / "prop_probability.py")
        os.symlink(repo / "backtest", root / "backtest")
        rng = random.Random(f"{SEED}-{d}")
        bg = json.loads((src / "out" / "batter_games.json").read_text())
        ps = json.loads((src / "out" / "pitcher_starts.json").read_text())
        nb, np_ = scramble(bg, BAT_STATS, d, rng), scramble(ps, PIT_STATS, d, rng)
        (mod / "out" / "batter_games.json").write_text(json.dumps(bg))
        (mod / "out" / "pitcher_starts.json").write_text(json.dumps(ps))
        for f in ("schedule.json", "people.json"):
            shutil.copy2(src / "out" / f, mod / "out" / f)
        sub = root / "canon_subset.jsonl"
        with open(canon) as fh, open(sub, "w") as oh:
            for line in fh:
                if d in line and json.loads(line).get("date") == d:
                    oh.write(line)
        subprocess.run([sys.executable, str(mod / "features.py"), str(sub)], check=True,
                       stdout=subprocess.DEVNULL, cwd=root)
        res = {"scrambled_batter_games": nb, "scrambled_pitcher_starts": np_}
        for name in ("features_hits.jsonl", "features_tb.jsonl", "features_k.jsonl"):
            a, b = load_target(src / "out" / name, d), load_target(mod / "out" / name, d)
            diff = sorted(k for k in a if a[k] != b.get(k))
            res[name] = {"target_rows": len(a), "rows_after_scramble": len(b), "missing": len(set(a) - set(b)),
                         "extra": len(set(b) - set(a)), "changed": len(diff), "first_changed": [list(x) for x in diff[:3]]}
        res["batter_rows_compared"] = res["features_hits.jsonl"]["rows_after_scramble"] + res["features_tb.jsonl"]["rows_after_scramble"]
        res["identical"] = all(res[n]["changed"] == 0 and res[n]["missing"] == 0 and res[n]["extra"] == 0
                               and res[n]["target_rows"] > 0
                               for n in ("features_hits.jsonl", "features_tb.jsonl", "features_k.jsonl"))
        result["dates"][d] = res
        print(d, json.dumps(res), flush=True)
        shutil.rmtree(root)
    result["all_identical"] = all(v["identical"] for v in result["dates"].values())
    return result


if __name__ == "__main__":
    out = main(*sys.argv[1:4])
    dest = Path(__file__).resolve().parent / "out"
    dest.mkdir(exist_ok=True)
    (dest / "leak_scramble.json").write_text(json.dumps(out, indent=1))
    print("all_identical:", out["all_identical"])

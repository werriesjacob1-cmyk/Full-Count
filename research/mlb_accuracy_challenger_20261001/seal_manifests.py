#!/usr/bin/env python3
"""Build and write same-cutoff manifests BEFORE outcomes exist (preregistration v2, section 5.6).

For each date in [start, end] whose frozen board exists at --data-sha:
  * refuses if `output/board_freeze_graded_{date}.json` already exists at --data-sha
    (outcomes may exist; a manifest built now would not be "before outcomes");
  * refuses if the earliest game on the board has not started at --data-sha's commit
    time (the board can still be resealed, so the cutoff is not final yet);
  * skips a date whose manifest file already exists (manifests are never rewritten);
  * otherwise writes manifests/{date}.json and prints its sha256.
It never opens a graded file. Commit the written files right away: run_eval_v2.py
admits a slate only when its manifest's first commit precedes the graded file's.

    python3 research/mlb_accuracy_challenger_20261001/seal_manifests.py \
        --start 2026-10-02 --end 2026-10-03 --data-sha origin/main
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness as H  # noqa: E402
import manifest as MF  # noqa: E402

OUT = os.path.join(HERE, "manifests")


def _git(*a):
    return subprocess.check_output(["git", "-C", HERE, *a], stderr=subprocess.DEVNULL)


def exists(sha, path):
    try:
        _git("cat-file", "-e", f"{sha}:{path}")
        return True
    except subprocess.CalledProcessError:
        return False


def git_json(sha, path):
    return json.loads(_git("show", f"{sha}:{path}")) if exists(sha, path) else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--data-sha", required=True)
    ap.add_argument("--out-dir", default=OUT)
    a = ap.parse_args(argv)
    sha = _git("rev-parse", a.data_sha).decode().strip()
    sha_time = H._utc(_git("show", "-s", "--format=%cI", sha).decode().strip())
    os.makedirs(a.out_dir, exist_ok=True)
    d, end, status = date.fromisoformat(a.start), date.fromisoformat(a.end), {}
    while d <= end:
        ds, d = str(d), d + timedelta(days=1)
        bpath, gpath, out = f"output/board_freeze_{ds}.json", f"output/board_freeze_graded_{ds}.json", \
            os.path.join(a.out_dir, f"{ds}.json")
        if os.path.exists(out):
            status[ds] = "ALREADY_SEALED"
            continue
        if not exists(sha, bpath):
            status[ds] = "NO_BOARD"
            continue
        if exists(sha, gpath):
            status[ds] = "REFUSED_GRADED_FILE_EXISTS"
            continue
        board = git_json(sha, bpath)
        earliest = min(H._utc(v) for v in board["game_start_times"].values())
        if sha_time < earliest:
            status[ds] = "REFUSED_BOARD_NOT_FINAL"
            continue
        cutoff = H._utc(board["sealed_at"]).date()
        ppaths = [f"data/props/props_{cutoff - timedelta(days=1)}.json", f"data/props/props_{cutoff}.json"]
        props = [git_json(sha, p) for p in ppaths]
        m = MF.build_manifest(board, props, source_paths={"board": f"{sha}:{bpath}",
                                                          "props": [f"{sha}:{p}" for p in ppaths if exists(sha, p)]})
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(m, fh, indent=1, sort_keys=True)
            fh.write("\n")
        status[ds] = {"SEALED": m["manifest_sha256"], "champion_in_primary_universe":
                      m["counts"]["champion_in_primary_universe"], "eligible_primary": m["counts"]["eligible_primary"]}
    print(json.dumps({"data_sha": sha, "data_sha_time": str(sha_time), "status": status}, indent=1))


if __name__ == "__main__":
    main()

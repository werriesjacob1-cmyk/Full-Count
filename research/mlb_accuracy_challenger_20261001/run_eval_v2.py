#!/usr/bin/env python3
"""Runner for preregistration v2 (thin I/O wrapper; analysis is harness_v2.py).

A date enters the evaluation only when ALL hold:
  * manifests/{date}.json exists at --manifest-ref and its hash rebuilds;
  * the graded file exists at --data-sha and links to the manifest's board sha;
  * the manifest's first commit on --manifest-ref is strictly earlier than the
    graded file's first commit on --data-sha (sealed before outcomes existed).
Everything else is listed (pending, late, unlinked) and never guessed.

    python3 research/mlb_accuracy_challenger_20261001/run_eval_v2.py --regime POSTSEASON_2026_SHADOW \
        --start 2026-10-02 --end 2026-11-05 --data-sha <main sha> --manifest-ref <branch sha> --out out.json
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
import harness_v2 as H2  # noqa: E402

MDIR = "research/mlb_accuracy_challenger_20261001/manifests"


def _git(*a):
    return subprocess.check_output(["git", "-C", HERE, *a], stderr=subprocess.DEVNULL).decode()


def git_json(ref, path):
    try:
        return json.loads(_git("show", f"{ref}:{path}"))
    except subprocess.CalledProcessError:
        return None


def first_commit_time(ref, path):
    out = _git("log", ref, "--diff-filter=A", "--format=%cI", "--", path).split()
    return H._utc(out[-1]) if out else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--regime", required=True, choices=("POSTSEASON_2026_SHADOW", "CONFIRMATORY_2027_REGULAR"))
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--data-sha", required=True)
    ap.add_argument("--manifest-ref", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    pairs, skipped = [], {}
    d, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    while d <= end:
        ds, d = str(d), d + timedelta(days=1)
        mpath, gpath = f"{MDIR}/{ds}.json", f"output/board_freeze_graded_{ds}.json"
        m = git_json(a.manifest_ref, mpath)
        if m is None:
            skipped[ds] = "NO_MANIFEST"
            continue
        g = git_json(a.data_sha, gpath)
        if g is None:
            skipped[ds] = "PENDING_UNGRADED"
            continue
        tm, tg = first_commit_time(a.manifest_ref, mpath), first_commit_time(a.data_sha, gpath)
        if tm is None or tg is None or not tm < tg:
            skipped[ds] = f"LATE_MANIFEST manifest={tm} graded={tg}"
            continue
        if g.get("source_board_sha256") != m["board_sha256"]:
            skipped[ds] = "GRADED_NOT_LINKED_TO_MANIFEST_BOARD"
            continue
        pairs.append((m, g))
    out = H2.evaluate(pairs, H.load_coefficients(), regime=a.regime)
    out.update({"data_sha": a.data_sha, "manifest_ref": a.manifest_ref, "window": [a.start, a.end],
                "skipped_dates": skipped})
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True, default=str)
        fh.write("\n")
    print(json.dumps({k: out[k] for k in ("regime", "primary_verdict", "n_slates", "skipped_dates")}))


if __name__ == "__main__":
    main()

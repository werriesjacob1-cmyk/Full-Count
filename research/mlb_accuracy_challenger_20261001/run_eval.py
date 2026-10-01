#!/usr/bin/env python3
"""Runner for the preregistered evaluation (thin I/O wrapper; all analysis is harness.py).

Reads frozen boards and their graded files via `git show <data-sha>:output/...` for each
date in [start, end] where BOTH exist, and calls harness.evaluate with the locked boundary.
Dates without a graded file are listed as pending, never guessed.

    python3 research/mlb_accuracy_challenger_20261001/run_eval.py \
        --regime POSTSEASON_2026_SHADOW --start 2026-10-02 --end 2026-11-05 --data-sha <main sha>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

BOUNDARY_UTC = "2026-10-01T18:00:00Z"  # locked by the preregistration (section 10)


def git_json(sha, path):
    try:
        return json.loads(subprocess.check_output(["git", "show", f"{sha}:{path}"], stderr=subprocess.DEVNULL))
    except subprocess.CalledProcessError:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--regime", required=True, choices=("POSTSEASON_2026_SHADOW", "CONFIRMATORY_2027_REGULAR"))
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--data-sha", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    slates, pending = [], []
    d, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    while d <= end:
        b = git_json(a.data_sha, f"output/board_freeze_{d}.json")
        if b is not None:
            g = git_json(a.data_sha, f"output/board_freeze_graded_{d}.json")
            (slates.append((b, g)) if g is not None else pending.append(str(d)))
        d += timedelta(days=1)
    out = H.evaluate(slates, H.load_coefficients(), boundary_utc=BOUNDARY_UTC, regime=a.regime)
    out.update({"data_sha": a.data_sha, "window": [a.start, a.end], "pending_ungraded_dates": pending})
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True, default=str)
        fh.write("\n")
    print(json.dumps({k: out[k] for k in ("regime", "primary_verdict", "n_slates", "pending_ungraded_dates")}))


if __name__ == "__main__":
    main()

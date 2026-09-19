#!/usr/bin/env python3
"""grade_board_freeze.py — grades yesterday's sealed full-board snapshot
against real outcomes.

This is the missing half of the winner's-curse calibration instrumentation
PR #131/#132/#138 were built for (see `board_freeze.py`'s own module
docstring): `board_freeze.py` seals the complete candidate universe at
generation time; `board_freeze_grader.grade_frozen_board` can already grade
it; nothing has ever invoked either the freeze's OWN output being committed
or this grading step in production. This script closes the grading half.

No-op if yesterday's `output/board_freeze_{date}.json` doesn't exist (the
freeze itself failed that day, or this script is running for the first time
and there is no prior frozen board yet) — never blocks the rest of the
pipeline, matching `grade_results.py`'s own missing-file convention for
yesterday's picks.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import board_freeze as bf
import board_freeze_grader as bfg

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")


def frozen_board_path(date: str) -> str:
    return os.path.join(OUTPUT_DIR, f"board_freeze_{date}.json")


def grade_date(date: str) -> dict | None:
    """Grade one specific date's frozen board, if it exists. Returns the
    graded-board mapping on success, or None if there was nothing to grade
    (missing file or a real grading failure) -- callers treat both as
    non-fatal, matching `grade_results.py`'s own missing-file convention."""
    path = frozen_board_path(date)
    if not os.path.exists(path):
        print(f"No frozen board for {date} ({path} not found) -- nothing to grade.")
        return None

    with open(path, "r", encoding="utf-8") as f:
        frozen_board = json.load(f)

    try:
        bf.verify_board_seal(frozen_board)
        graded = bfg.grade_frozen_board(frozen_board, date=date)
    except Exception as e:
        print(f"Board-freeze grading failed for {date} ({e}) -- picks pipeline unaffected.")
        return None

    out_path = bfg.graded_board_path(date)
    bfg.write_graded_board(graded, out_path)
    print(f"Graded frozen board ({graded['record_count']} candidates) to {out_path}")
    return graded


def main(*, date_override: str | None = None) -> int:
    target_date = date_override or (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    grade_date(target_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""First-pitch-aware planning and deadline enforcement for the V3 runner (prereg v3 sections 4, 7, 16).

Each slate unit's deadline is derived from the actual schedule snapshot: the earliest
non-TBD first pitch of the unit. The runner must START by
    deadline - (shadow + capture + manifest + seal/receipts budgets) - safety margin
and, before every step, must still be able to finish all remaining steps plus the
safety margin before first pitch. Otherwise: MISS UNIT -- no seal, no confirmatory
use, no backfill.
"""
from __future__ import annotations

from datetime import timedelta

import manifest_v3 as M3

BUDGET_S = {"shadow": 20 * 60, "capture": 15 * 60, "manifest": 2 * 60, "seal": 8 * 60}
STEPS = ("shadow", "capture", "manifest", "seal")
SAFETY_S = 10 * 60          # = MIN_LEAD_S: the seal must exist at least this long before first pitch


class MissUnit(Exception):
    """Insufficient time to complete the remaining steps before first pitch."""


def unit_first_pitch(schedule, window):
    starts = [g["game_date"] for g in schedule["games"]
              if not g.get("start_time_tbd") and M3.window_of(g["game_date"]) == window]
    return min(starts, key=M3.utc) if starts else None


def plan(schedule, now_utc):
    out = []
    for window in ("DAY", "NIGHT"):
        fp = unit_first_pitch(schedule, window)
        if fp is None:
            continue
        latest_start = M3.utc(fp) - timedelta(seconds=sum(BUDGET_S.values()) + SAFETY_S)
        out.append({"date": schedule["date"], "window": window, "first_pitch_utc": fp,
                    "latest_start_utc": latest_start.isoformat(),
                    "status": "PLANNED" if M3.utc(now_utc) <= latest_start else "MISSED_NO_CONFIRMATORY_USE"})
    return out


def guard(now_utc, first_pitch_utc, next_step):
    """Raise MissUnit unless every step from `next_step` on, plus the safety margin, fits."""
    remaining = sum(BUDGET_S[s] for s in STEPS[STEPS.index(next_step):])
    if M3.utc(now_utc) + timedelta(seconds=remaining + SAFETY_S) > M3.utc(first_pitch_utc):
        raise MissUnit(f"before {next_step}: {remaining + SAFETY_S}s needed, first pitch {first_pitch_utc}")

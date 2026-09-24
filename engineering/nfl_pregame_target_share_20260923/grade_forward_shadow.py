#!/usr/bin/env python3
"""Grade the frozen 2026 week-3 forward shadow. Committed BEFORE any
week-3 outcome existed, so the grading rules could not be shaped by the
results.

Rules (fixed now):
- Refuse to grade if the artifact's SHA-256 does not match its .sha256 file.
- A candidate with no real week-3 stats row for his frozen team is VOID
  (did not play, or not recorded), never a miss.
- Grade only games whose FINAL real stats rows exist, i.e. both teams have
  rows in the week-3 file. Report every ungraded game explicitly.
- Primary descriptive comparison: C1 minus B0 receptions MAE on graded
  rows, player-clustered 95% interval (seed 20260924). One NFL week is a
  small sample, so this is reported as forward evidence, NOT as a new
  confirmatory test. The confirmatory test is the locked 2019-2022 holdout.
- No sportsbook line is invented. Real-line evaluation happens only where a
  real captured offer exists, in a separate step.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from data_cache import load_player_weeks  # noqa: E402
from stats_lib import player_clustered_diff_ci, poisson_brier  # noqa: E402

ARTIFACT = HERE / "forward_shadow_2026_wk3.json"
body = ARTIFACT.read_text()
expected = (HERE / "forward_shadow_2026_wk3.sha256").read_text().split()[0]
if hashlib.sha256(body.encode()).hexdigest() != expected:
    raise SystemExit("artifact SHA-256 mismatch: refusing to grade a modified freeze")
artifact = json.loads(body)
season, week = artifact["target"]["season"], artifact["target"]["week"]

live = load_player_weeks(season, refresh=True)
wk = [r for r in live["rows"] if r["week"] == week]
teams_with_rows = {r["team"] for r in wk}
by_player = {(r["player_id"], r["team"]): r for r in wk}

graded, void, ungraded_games = [], [], set()
for c in artifact["candidates"]:
    home_away = c["game_id"].split("_")[2:4]
    if not all(t in teams_with_rows for t in home_away):
        ungraded_games.add(c["game_id"])
        continue
    row = by_player.get((c["player_id"], c["team"]))
    if row is None:
        void.append({"player_id": c["player_id"], "player_name": c["player_name"], "game_id": c["game_id"]})
        continue
    graded.append({**c, "realized_receptions": row["receptions"]})

report = {
    "graded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "artifact_sha256": expected, "outcome_source": {k: live[k] for k in ("source_url", "fetched_at_utc", "rows_sha256")},
    "n_frozen": artifact["n_candidates"], "n_graded": len(graded), "n_void": len(void),
    "ungraded_games_no_final_rows": sorted(ungraded_games),
    "evidence_class": "PROSPECTIVE_FORWARD_SHADOW_ONE_WEEK_NOT_CONFIRMATORY",
}
if len(graded) >= 2:
    models = {"b0": "b0_projection", "existing_unadjusted": "existing_unadjusted_projection", "c1": "c1_projection"}
    report["models"] = {
        name: {
            "mae": statistics.fmean(abs(g[key] - g["realized_receptions"]) for g in graded),
            "bias": statistics.fmean(g[key] - g["realized_receptions"] for g in graded),
            "poisson_brier": statistics.fmean(poisson_brier(g[key], g["realized_receptions"]) for g in graded),
        }
        for name, key in models.items()
    }
    if len({g["player_id"] for g in graded}) >= 2:
        report["c1_minus_b0_mae"] = player_clustered_diff_ci(
            graded, lambda g: abs(g["c1_projection"] - g["realized_receptions"]),
            lambda g: abs(g["b0_projection"] - g["realized_receptions"]),
        )
report["void_players"] = void
out = HERE / "forward_shadow_2026_wk3_grade.json"
out.write_text(json.dumps(report, indent=2))
print(json.dumps({k: v for k, v in report.items() if k != "void_players"}, indent=2))

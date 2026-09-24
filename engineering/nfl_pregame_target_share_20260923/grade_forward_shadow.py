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

Amendment, 2026-09-24, still BEFORE any week-3 outcome existed, following
the independent review (Issue #91 comment 5805874000). Two descriptive
secondaries were added; the rules above are unchanged:
- `chain_x_constant`: the existing unadjusted chain times the single
  constant 0.8277 (mean targets per dropback on the 2024-2025 exploratory
  rows). The review showed this constant does as well as C1's per-team
  ratio on the locked holdout. Caveat, stated now: the forward shadow's
  team rows are player-derived, with dropbacks ~6.9% lower than pinned PBP,
  so this constant variant is expected to project LOW here, while C1 is
  source-invariant.
- `including_zero_stat_actives`: frozen candidates with no stats row but
  real offensive snaps in the live 2026 snap-count file are scored as 0
  receptions instead of VOID. The review showed that excluding them favors
  C1. Snap counts are a live, unpinned asset whose SHA-256 is recorded. If
  the snap file or crosswalk cannot be fetched, this block is reported as
  not computed and is never guessed.
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
CHAIN_CONSTANT = 0.8277


def _compare(rows):
    if len({g["player_id"] for g in rows}) < 2:
        return None
    out = {}
    for name, fn in {
        "b0": lambda g: g["b0_projection"],
        "c1": lambda g: g["c1_projection"],
        "chain_x_constant": lambda g: g["existing_unadjusted_projection"] * CHAIN_CONSTANT,
    }.items():
        out[name] = {"n": len(rows), "mae": statistics.fmean(abs(fn(g) - g["realized_receptions"]) for g in rows)}
    out["c1_minus_chain_x_constant_mae"] = player_clustered_diff_ci(
        rows, lambda g: abs(g["c1_projection"] - g["realized_receptions"]),
        lambda g: abs(g["existing_unadjusted_projection"] * CHAIN_CONSTANT - g["realized_receptions"]),
    )
    return out


report["secondary_chain_x_constant"] = {"constant": CHAIN_CONSTANT, "graded_rows": _compare(graded)}

try:
    import urllib.request

    from nfl.research.role_intelligence_data_prep import (
        SNAP_COUNTS_URL_TMPL,
        fetch_players_crosswalk,
        parse_snap_counts_csv,
    )
    url = SNAP_COUNTS_URL_TMPL.format(season=season)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "full-count-mission10/1.0"}), timeout=180) as resp:
        raw = resp.read()
    snaps = parse_snap_counts_csv(raw.decode("utf-8"), season, fetch_players_crosswalk())
    active = {(s["player_id"], s["team"]) for s in snaps if s["week"] == week and s["player_id"] and s["offense_snaps"] > 0}
    by_id = {c["player_id"]: c for c in artifact["candidates"]}
    zero_rows = [{**by_id[v["player_id"]], "realized_receptions": 0.0} for v in void
                 if (v["player_id"], by_id[v["player_id"]]["team"]) in active]
    report["secondary_including_zero_stat_actives"] = {
        "snap_source": {"url": url, "sha256": hashlib.sha256(raw).hexdigest(), "unpinned_live_asset": True},
        "n_void_with_real_offense_snaps": len(zero_rows),
        "comparison": _compare(graded + zero_rows),
    }
except Exception as exc:  # noqa: BLE001 - a secondary is reported as not computed, never guessed
    report["secondary_including_zero_stat_actives"] = {"status": "NOT_COMPUTED", "reason": repr(exc)}

report["void_players"] = void
out = HERE / "forward_shadow_2026_wk3_grade.json"
out.write_text(json.dumps(report, indent=2))
print(json.dumps({k: v for k, v in report.items() if k != "void_players"}, indent=2))

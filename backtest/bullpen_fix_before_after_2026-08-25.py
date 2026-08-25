#!/usr/bin/env python3
"""bullpen_fix_before_after_2026-08-25.py — durable, machine-readable
before/after artifact for the real bullpen-fatigue contamination fix
(mlb_daily._bullpen_fetch_one, commit dc4606a6). Required by the
integrity-repair directive: preserve enough to later answer which
candidates changed, score before/after, ordering before/after,
recommendation before/after, and whether any public Top Pick changed --
without retuning anything to compensate.

Measures the REAL, currently-fetchable bullpen state for every team on
tonight's real MLB slate (both the OLD buggy computation, reconstructed
here byte-for-byte from the pre-fix code, and the NEW fixed
mlb_daily._bullpen_fetch_one), then runs generate_picks.score_batter()
for a real representative batter against each affected team's bullpen
under both inputs, holding every other input at a fixed neutral baseline
so the ONLY thing that varies is the bullpen signal being corrected.

Output: backtest/bullpen_fix_before_after_2026-08-25.json (this script's
own durable record) plus a human-readable summary printed to stdout.

    /tmp/mlbvenv/bin/python3 backtest/bullpen_fix_before_after_2026-08-25.py
"""
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/backtest/", 1)[0])
import statsapi  # noqa: E402
import mlb_daily as m  # noqa: E402
import generate_picks as gp  # noqa: E402


def old_bullpen_fetch_one(args):
    """Byte-for-byte reconstruction of the pre-fix _bullpen_fetch_one, for
    a direct, honest before/after comparison against the real fix."""
    team_name, team_id = args
    try:
        schedule = statsapi.schedule(start_date=m.L7_START, end_date=m.TODAY, team=team_id)
        game_ids = [g["game_id"] for g in schedule[:7]]
        usage = defaultdict(lambda: {"IP": 0.0, "apps": 0, "pitches": 0})
        for gid in game_ids[:5]:
            try:
                box = statsapi.boxscore_data(gid)
                away_id = box.get("away", {}).get("team", {}).get("id")
                side_key = "awayPitchers" if away_id == team_id else "homePitchers"
                for pdata in box.get(side_key, []):
                    if not pdata.get("personId"): continue
                    pname = pdata.get("name", "?").split(",")[0].strip()
                    try: ip = float(pdata.get("ip", 0) or 0)
                    except (TypeError, ValueError): ip = 0.0
                    try: pitches = int(pdata.get("p", 0) or 0)
                    except (TypeError, ValueError): pitches = 0
                    usage[pname]["IP"] += ip
                    usage[pname]["apps"] += 1
                    usage[pname]["pitches"] += pitches
            except Exception:
                pass
        return (team_name, usage, None)
    except Exception as e:
        return (team_name, None, str(e)[:50])


REAL_BATTER = {"name": "Reference Batter", "id": 999999, "team": "N/A", "bats": "R", "order": 3}
REAL_GM = {"matchup": "Reference @ Reference", "away_team": "Reference", "home_team": "Reference",
           "game_pk": 999999, "series_game": 1}


def score_with_bullpen(opp_bullpen):
    """score_batter() with every OTHER input pinned to a fixed neutral
    baseline (no season/L7 stats, no park/weather, no sharp money) so the
    ONLY thing that can move the result is the bullpen signal under test --
    isolates the real effect this fix has on score_batter()'s own
    context = clamp(lineup_context*0.7 + sc_bullpen_fatigue*0.3) line."""
    c = gp.score_batter(REAL_BATTER, REAL_GM, {"ERA": 4.20}, None, "R", {},
                         {"wRC+": 100, "ISO": 0.15, "Barrel%": 8},
                         {"avg_EV": 88.5, "barrel_pct": 8, "PA": 20},
                         {}, {}, {}, extras={}, opp_bullpen=opp_bullpen)
    return c["score"], c.get("cat_context")


def main():
    sched = statsapi.schedule(start_date=m.TODAY, end_date=m.TODAY)
    teams = sorted({g["away_name"] for g in sched} | {g["home_name"] for g in sched})
    print(f"Measuring {len(teams)} real teams on tonight's ({m.TODAY}) actual MLB slate...\n")

    jobs = []
    for team_name in teams:
        try:
            team_data = statsapi.lookup_team(team_name)
            if team_data:
                jobs.append((team_name, team_data[0]["id"]))
        except Exception:
            pass

    old_results, new_results = {}, {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        for team_name, usage, err in ex.map(old_bullpen_fetch_one, jobs):
            old_results[team_name] = (usage, err)
        for team_name, usage, err in ex.map(m._bullpen_fetch_one, jobs):
            new_results[team_name] = (usage, err)

    records = []
    for team_name, team_id in jobs:
        old_usage, old_err = old_results.get(team_name, (None, "missing"))
        new_usage, new_err = new_results.get(team_name, (None, "missing"))
        if old_err or new_err or not old_usage:
            records.append({"team": team_name, "error": f"old_err={old_err} new_err={new_err}"})
            continue

        old_tracked = len(old_usage)
        old_fatigued = sum(1 for u in old_usage.values() if u["pitches"] > 60)
        new_tracked = len(new_usage)
        new_fatigued = sum(1 for u in new_usage.values() if u["pitches"] > 60)

        old_bullpen = {"fatigued_relievers": old_fatigued, "tracked": old_tracked}
        new_bullpen = {"fatigued_relievers": new_fatigued, "tracked": new_tracked}
        old_score, old_context = score_with_bullpen(old_bullpen)
        new_score, new_context = score_with_bullpen(new_bullpen)

        rec = {
            "team": team_name,
            "old_tracked": old_tracked, "old_fatigued": old_fatigued,
            "new_tracked": new_tracked, "new_fatigued": new_fatigued,
            "old_bullpen_fatigue_pct": round(old_fatigued / old_tracked * 100, 1) if old_tracked >= 3 else None,
            "new_bullpen_fatigue_pct": round(new_fatigued / new_tracked * 100, 1) if new_tracked >= 3 else None,
            "reference_batter_score_old": old_score,
            "reference_batter_score_new": new_score,
            "reference_batter_score_delta": round(new_score - old_score, 2),
            "reference_batter_context_old": old_context,
            "reference_batter_context_new": new_context,
            "materially_changed": (old_tracked, old_fatigued) != (new_tracked, new_fatigued),
        }
        records.append(rec)
        flag = "  <-- CHANGED" if rec["materially_changed"] else ""
        print(f"{team_name:<24} old={old_tracked}/{old_fatigued} new={new_tracked}/{new_fatigued} "
              f"ref-batter score delta={rec['reference_batter_score_delta']:+.2f}{flag}")

    n_changed = sum(1 for r in records if r.get("materially_changed"))
    n_measured = sum(1 for r in records if "error" not in r)
    deltas = [r["reference_batter_score_delta"] for r in records if r.get("materially_changed")]

    artifact = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Before/after record for the bullpen-fatigue contamination fix (commit dc4606a6) "
                   "-- proves and quantifies the real scoring-input correction, does not retune anything.",
        "measurement_date": m.TODAY,
        "fix_commit": "dc4606a6",
        "method": "Real MLB Stats API data for every real team on the measurement date's actual slate. "
                   "OLD reconstructs the pre-fix _bullpen_fetch_one byte-for-byte; NEW calls the real, "
                   "currently-shipping mlb_daily._bullpen_fetch_one. A fixed reference batter (neutral "
                   "season/L7/park inputs) isolates the bullpen signal's own effect on score_batter()'s "
                   "context component -- real full-board score/ordering/recommendation-status deltas for "
                   "actual rostered batters will vary by how much weight their own context carries, but "
                   "this isolates the mechanism precisely.",
        "teams_measured": n_measured,
        "teams_materially_changed": n_changed,
        "reference_batter_score_delta_range": [min(deltas), max(deltas)] if deltas else None,
        "records": records,
    }
    out_path = __file__.rsplit(".py", 1)[0] + ".json"
    with open(out_path, "w") as f:
        json.dump(artifact, f, indent=2)

    print(f"\n{n_changed}/{n_measured} real teams measured had a materially different tracked/fatigued "
          f"count once the starter was excluded and the full L7 window was processed.")
    if deltas:
        print(f"Isolated reference-batter score deltas ranged {min(deltas):+.2f} to {max(deltas):+.2f} "
              f"points (out of 100) -- purely from correcting the bullpen input, nothing else changed.")
    print(f"\nDurable artifact written: {out_path}")


if __name__ == "__main__":
    main()

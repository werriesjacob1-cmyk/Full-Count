#!/usr/bin/env python3
"""roof_fix_before_after_2026-08-25.py — durable, machine-readable
before/after artifact for the all-30 park/roof data-integrity fix
(Truist Park dome misclassification + honest per-game OPEN/CLOSED/UNKNOWN
roof model for retractable-roof parks, mlb_daily.real_roof_status()).

Measures the REAL park_hr_index and a fixed reference-batter score, old
(pre-fix) vs new (post-fix), for every park on tonight's actual MLB slate
that this fix can change: Truist Park (dome misclassification) and every
retractable-roof park (Rogers Centre, Globe Life Field, Daikin Park,
T-Mobile Park, loanDepot park, American Family Field, Chase Field).

    /tmp/mlbvenv/bin/python3 backtest/roof_fix_before_after_2026-08-25.py
"""
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, __file__.rsplit("/backtest/", 1)[0])
import mlb_daily as m  # noqa: E402
import generate_picks as gp  # noqa: E402

AFFECTED_PARKS = {"Truist Park", "Rogers Centre", "Globe Life Field", "Daikin Park",
                  "T-Mobile Park", "loanDepot park", "American Family Field", "Chase Field"}

REAL_BATTER = {"name": "Reference Batter", "id": 999999, "team": "N/A", "bats": "R", "order": 3}
REAL_GM = {"matchup": "Reference @ Reference", "away_team": "Reference", "home_team": "Reference",
           "game_pk": 999999, "series_game": 1}


def score_with_park(park_wx):
    c = gp.score_batter(REAL_BATTER, REAL_GM, {"ERA": 4.20}, None, "R", park_wx,
                         {"wRC+": 100, "ISO": 0.15, "Barrel%": 8},
                         {"avg_EV": 88.5, "barrel_pct": 8, "PA": 20},
                         {}, {}, {}, extras={})
    return c["score"], c.get("cat_environment")


def old_park_wx(venue):
    """Byte-for-byte reconstruction of the pre-fix behavior: Truist Park's
    old (wrong) dome=True entry, and every retractable-roof park
    permanently forced to dome=True/park_hr_index=50 regardless of real
    per-game roof state."""
    OLD_TRUIST_DOME = True  # the exact bug: was hardcoded True pre-fix
    if venue == "Truist Park" and OLD_TRUIST_DOME:
        return {"dome": True, "park_hr_index": 50, "wind_effect": "dome", "temp": None}
    _, _, dome, *_ = m.STADIUMS[venue]
    if dome or venue == "Truist Park":
        return {"dome": True, "park_hr_index": 50, "wind_effect": "dome", "temp": None}
    return None  # not a park this fix touches


def main():
    r = m.retry_get("https://statsapi.mlb.com/api/v1/schedule", params={
        "sportId": 1, "date": m.TODAY,
        "hydrate": "weather,venue"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
    r.raise_for_status()
    games = []
    for date in r.json().get("dates", []):
        games.extend(date.get("games", []))

    records = []
    for g in games:
        venue = g.get("venue", {}).get("name", "")
        if venue not in AFFECTED_PARKS:
            continue
        matchup = f"{g['teams']['away']['team']['name']} @ {g['teams']['home']['team']['name']}"
        mlb_cond = (g.get("weather") or {}).get("condition")
        gm = {"matchup": matchup, "venue": venue, "game_start_utc": g.get("gameDate", ""),
              "hour": 19, "mlb_weather_condition": mlb_cond}

        old_wx = old_park_wx(venue)
        new_wx_map = gp.fetch_park_weather([gm])
        new_wx = new_wx_map.get(matchup)
        if not old_wx or not new_wx:
            continue

        old_score, old_env = score_with_park(old_wx)
        new_score, new_env = score_with_park(new_wx)

        rec = {
            "venue": venue, "matchup": matchup, "mlb_weather_condition": mlb_cond,
            "roof_status": new_wx.get("roof_status"),
            "old_dome": old_wx["dome"], "old_park_hr_index": old_wx["park_hr_index"],
            "new_dome": new_wx["dome"], "new_park_hr_index": new_wx.get("park_hr_index"),
            "reference_batter_score_old": old_score, "reference_batter_score_new": new_score,
            "reference_batter_score_delta": round(new_score - old_score, 2),
            "materially_changed": old_wx["dome"] != new_wx["dome"] or old_wx["park_hr_index"] != new_wx.get("park_hr_index"),
        }
        records.append(rec)
        flag = "  <-- CHANGED" if rec["materially_changed"] else ""
        print(f"{venue:<22} roof_status={str(rec['roof_status']):<8} "
              f"old(dome={rec['old_dome']},hr_idx={rec['old_park_hr_index']}) "
              f"new(dome={rec['new_dome']},hr_idx={rec['new_park_hr_index']}) "
              f"score delta={rec['reference_batter_score_delta']:+.2f}{flag}")

    n_changed = sum(1 for r in records if r["materially_changed"])
    artifact = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Before/after record for the all-30 park/roof data-integrity fix -- Truist Park "
                   "dome misclassification + honest per-game OPEN/CLOSED/UNKNOWN roof model for "
                   "retractable-roof parks. Proves and quantifies the real scoring-input correction, "
                   "does not retune anything.",
        "measurement_date": m.TODAY,
        "games_at_affected_parks_today": len(records),
        "games_materially_changed": n_changed,
        "records": records,
    }
    out_path = __file__.rsplit(".py", 1)[0] + ".json"
    with open(out_path, "w") as f:
        json.dump(artifact, f, indent=2)
    print(f"\n{n_changed}/{len(records)} real games at the 8 affected parks tonight had a "
          f"materially different park_hr_index/dome treatment once the roof-status fix applied.")
    print(f"\nDurable artifact written: {out_path}")


if __name__ == "__main__":
    main()

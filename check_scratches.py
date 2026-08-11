#!/usr/bin/env python3
"""
check_scratches.py — re-checks the board against the lineups as they stand
now, and flags any pick whose player is no longer in one.

THE HOLE THIS FILLS.

quality_control() already refuses to score a batter whose lineup was never
confirmed: fewer than nine posted hitters means the batting-order slot is a
guess, and slot is the strongest single input in the model. That check runs
ONCE, at the moment picks are generated.

Picks are generated at 14:30, 15:30, 20:00 and 22:30 UTC. Games start later.
Everything that happens in between — a player scratched in warmups, a lineup
reshuffled after a late injury, a starter pushed a day — happens after the
only check that would have caught it. A batter confirmed in the two o'clock
lineup and scratched at six was a fully valid pick when it was made and is a
dead bet by first pitch.

That is not a small edge case. It is the difference between a bet that loses
and a bet that should never have been placed, and unlike every other error in
this project it is not a modelling problem at all — the information is
published, free, and simply arrives after we stopped looking.

WHY THIS IS SEPARATE FROM generate_picks.py.

Regenerating the board would be the wrong response to a scratch. The other
nine picks were correct and re-running would silently reshuffle them against
a slate that has partly started, which is exactly the mid-slate hazard
bettable_games() exists to prevent. This only ever reports; it changes no
pick and rewrites no board.

WHAT COUNTS AS A SCRATCH.

The player is absent from his team's posted lineup while that lineup is
posted and complete (nine or more hitters). An incomplete or missing lineup
means the lineup has not been posted yet, which is not evidence of anything
and is reported separately as unknown rather than being called a scratch.
Pitchers are checked against the probable starter for their game instead,
since they never appear in a batting order.
"""
import argparse
import json
import os
import sys
from datetime import datetime

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
STATS_API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "Mozilla/5.0"}


def picks_path(date):
    return os.path.join(OUTPUT_DIR, f"picks_{date}.json")


def current_rosters(date):
    """Posted lineups and probable starters as they stand right now.

    Returns {game_pk: {"away_ids", "home_ids", "away_posted", "home_posted",
    "away_sp_id", "home_sp_id", "status", "away_team", "home_team"}}."""
    import requests
    r = requests.get(f"{STATS_API}/schedule",
                     params={"sportId": 1, "date": date,
                             "hydrate": "lineups,probablePitcher,team"},
                     headers=UA, timeout=30)
    r.raise_for_status()
    out = {}
    for d in r.json().get("dates", []):
        for g in d.get("games", []):
            lu = g.get("lineups") or {}
            away = lu.get("awayPlayers") or []
            home = lu.get("homePlayers") or []
            teams = g.get("teams") or {}
            out[g.get("gamePk")] = {
                "away_ids": {p.get("id") for p in away},
                "home_ids": {p.get("id") for p in home},
                # A lineup is POSTED only when it is complete. Nine is the
                # threshold quality_control() already uses, and treating a
                # partial scrape as authoritative would turn every slow feed
                # into a phantom scratch alert.
                "away_posted": len(away) >= 9,
                "home_posted": len(home) >= 9,
                "away_sp_id": ((teams.get("away") or {}).get("probablePitcher") or {}).get("id"),
                "home_sp_id": ((teams.get("home") or {}).get("probablePitcher") or {}).get("id"),
                "away_team": ((teams.get("away") or {}).get("team") or {}).get("name"),
                "home_team": ((teams.get("home") or {}).get("team") or {}).get("name"),
                "status": (g.get("status") or {}).get("detailedState"),
            }
    return out


def check(picks, rosters):
    """Classify every pick as ok / scratched / unknown, with a reason."""
    rows = []
    for p in picks:
        gp, pid = p.get("game_pk"), p.get("player_id")
        info = rosters.get(gp)
        base = {"rank": p.get("rank"), "name": p.get("name"),
                "prop": p.get("prop"), "team": p.get("team"),
                "matchup": p.get("matchup")}
        if not info or not pid:
            rows.append({**base, "state": "unknown",
                         "note": "game or player not found in the current schedule"})
            continue
        if p.get("type") == "pitcher_combo":
            # Two starters, not one -- team is None on this pick (it spans
            # both teams), so the single-side "team" comparison below can't
            # place it. Both listed starters have to still be the probable
            # starters for the pick to still mean what it said.
            ids = p.get("combo_player_ids") or []
            away_sp, home_sp = info.get("away_sp_id"), info.get("home_sp_id")
            if not ids or away_sp is None or home_sp is None:
                rows.append({**base, "state": "unknown",
                             "note": "no probable starters listed for this game"})
            elif set(ids) != {away_sp, home_sp}:
                rows.append({**base, "state": "scratched",
                             "note": "one or both starters are no longer the listed probables"})
            else:
                rows.append({**base, "state": "ok", "note": "both starters still listed"})
            continue

        side = "away" if p.get("team") == info.get("away_team") else "home"

        if p.get("type") == "pitcher" or (p.get("projection") or {}).get("stat") in (
                "strikeouts", "first_inning_run"):
            # A pitcher never appears in a batting order, so absence from one
            # says nothing. The probable starter is the right comparison.
            sp = info.get(f"{side}_sp_id")
            if sp is None:
                rows.append({**base, "state": "unknown",
                             "note": "no probable starter listed for this side"})
            elif sp != pid:
                rows.append({**base, "state": "scratched",
                             "note": "no longer the listed probable starter for this game"})
            else:
                rows.append({**base, "state": "ok", "note": "still the listed starter"})
            continue

        if not info.get(f"{side}_posted"):
            rows.append({**base, "state": "unknown",
                         "note": "lineup not posted yet — absence proves nothing"})
        elif pid not in info[f"{side}_ids"]:
            rows.append({**base, "state": "scratched",
                         "note": "lineup is posted and complete, and he is not in it"})
        else:
            rows.append({**base, "state": "ok", "note": "in the posted lineup"})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--fail-on-scratch", action="store_true",
                    help="exit non-zero when any pick is scratched")
    args = ap.parse_args()

    path = picks_path(args.date)
    if not os.path.exists(path):
        print(f"No board for {args.date} ({path}) — nothing to re-check.")
        return 0
    with open(path, encoding="utf-8") as f:
        picks = json.load(f).get("picks", [])
    if not picks:
        print(f"No picks in {path}.")
        return 0

    try:
        rosters = current_rosters(args.date)
    except Exception as e:
        # Never fail the pipeline over this. A missed check is bad; a broken
        # workflow that stops the rest of the run is worse.
        print(f"Could not fetch current lineups ({e}) — board left as-is.")
        return 0

    rows = check(picks, rosters)
    scratched = [r for r in rows if r["state"] == "scratched"]
    unknown = [r for r in rows if r["state"] == "unknown"]

    print(f"Re-checked {len(rows)} pick(s) for {args.date} at "
          f"{datetime.now().strftime('%H:%M')} against the lineups as they stand now.\n")
    if scratched:
        print(f"  DO NOT BET ({len(scratched)}):")
        for r in scratched:
            print(f"    #{r['rank']} {r['name']} — {r['prop']}")
            print(f"        {r['note']}")
        print()
    if unknown:
        print(f"  Not yet confirmable ({len(unknown)}) — re-run closer to first pitch:")
        for r in unknown:
            print(f"    #{r['rank']} {r['name']} — {r['note']}")
        print()
    ok = len(rows) - len(scratched) - len(unknown)
    print(f"  {ok} pick(s) confirmed still live.")

    out = os.path.join(OUTPUT_DIR, f"scratches_{args.date}.json")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"date": args.date, "checked_at": datetime.now().isoformat(),
                   "n_scratched": len(scratched), "n_unknown": len(unknown),
                   "rows": rows}, f, indent=2)
    print(f"\nWrote {out}")
    return 1 if (scratched and args.fail_on_scratch) else 0


if __name__ == "__main__":
    sys.exit(main())

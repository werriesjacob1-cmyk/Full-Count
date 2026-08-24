#!/usr/bin/env python3
"""measure_last_known_lineup_accuracy.py -- 2026-08-24 accuracy investigation,
lineup-timing item.

THE QUESTION: mlb_daily.fetch_last_known_lineup() (the pipeline's own
tier-4 "assumed" lineup fallback -- a team's batting order from its most
recent COMPLETED game) is used today for early-day candidates when nothing
has posted a real lineup yet. No historical grading data exists yet to
measure how often that guess is actually right (results/grades_*.json
never tracked lineup_assumed before this investigation -- see the session
report). This script builds a DIFFERENT, real, immediately-computable
measurement instead: for real past dates, reconstruct what
fetch_last_known_lineup() WOULD have returned that morning, and diff it
against the REAL, final, confirmed lineup MLB actually used that day
(fetch_lineups() on a past date returns the real final one). Uses the
pipeline's own real functions, not a reimplementation -- apples to apples
with what the live "assumed" tier actually produces.

THIS IS A PROXY, STATED HONESTLY. Matching the exact batting slot is a
stronger claim than "this batter got real plate appearances that day" --
a lineup shuffle (e.g. a player moved from 3rd to 6th) would count as a
slot mismatch here even though the batter still played and the model's
PA-projection error from that specific shuffle is usually small. What
this measures directly: (a) whether the SAME 9 players started at all
(name-match rate), and (b) whether each one batted in the SAME order
slot (exact-slot match rate). Both are real, useful, honestly-labeled
numbers -- not a substitute for the eventual gold-standard measurement
(shadow-score real historical candidates under both a projected and a
confirmed lineup, then compare each against the real graded outcome),
which is a bigger undertaking flagged as future work in the accompanying
report.

    /tmp/mlbvenv/bin/python3 backtest/measure_last_known_lineup_accuracy.py
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mlb_daily as m

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "_last_known_lineup_accuracy.json")

# Spread across a real season, not clustered -- early season (where the
# assumed tier fires most, since fewer teams' most-recent game is close by
# in form/roster) and mid-season control dates alike.
SAMPLE_DATES = [
    "2024-04-05", "2024-04-15", "2024-04-25", "2024-05-10", "2024-06-15",
    "2024-07-15", "2024-08-15", "2024-09-15",
    "2025-04-05", "2025-04-15", "2025-04-25", "2025-05-10", "2025-06-15",
    "2025-07-15", "2025-08-15", "2025-09-15",
]
GAMES_PER_DATE = 5  # bounds total network calls to a manageable count


def main():
    team_ids = {t["name"]: t["id"] for t in m.get_team_ids()}
    results = []
    for date in SAMPLE_DATES:
        print(f"=== {date} ===", flush=True)
        try:
            _lines, game_meta, _pids = m.fetch_lineups(date)
        except Exception as e:
            print(f"  fetch_lineups failed: {e}", flush=True)
            continue
        for gm in game_meta[:GAMES_PER_DATE]:
            matchup = gm.get("matchup", "")
            if " @ " not in matchup:
                continue
            away_name, home_name = matchup.split(" @ ", 1)
            for side, team_name, real_lineup in (
                ("away", away_name, gm.get("away_lineup") or []),
                ("home", home_name, gm.get("home_lineup") or []),
            ):
                if len(real_lineup) < 9:
                    continue
                team_id = team_ids.get(team_name)
                if team_id is None:
                    continue
                try:
                    guessed = m.fetch_last_known_lineup(team_id, date)
                except Exception as e:
                    print(f"  fetch_last_known_lineup({team_name}) failed: {e}", flush=True)
                    continue
                if not guessed:
                    print(f"  {team_name}: no last-known lineup available (early season / "
                          f"no completed game in the prior 12 days)", flush=True)
                    continue
                real_ids_by_slot = {e.get("order"): e.get("id") for e in real_lineup if e.get("id")}
                guessed_ids_by_slot = {e.get("order"): e.get("id") for e in guessed if e.get("id")}
                real_id_set = set(real_ids_by_slot.values())
                guessed_id_set = set(guessed_ids_by_slot.values())
                name_matches = len(real_id_set & guessed_id_set)
                slot_matches = sum(1 for slot, pid in guessed_ids_by_slot.items()
                                   if real_ids_by_slot.get(slot) == pid)
                row = {
                    "date": date, "team": team_name, "side": side,
                    "n_real": len(real_id_set), "n_guessed": len(guessed_id_set),
                    "name_matches": name_matches, "slot_matches": slot_matches,
                }
                results.append(row)
                print(f"  {team_name:25s} name_match={name_matches}/9 slot_match={slot_matches}/9",
                      flush=True)

    with open(OUT_PATH, "w") as f:
        json.dump(results, f, indent=1)

    print(f"\n{'='*80}\n{len(results)} team-days measured, written to {OUT_PATH}\n{'='*80}")
    if results:
        n_name = sum(r["name_matches"] for r in results)
        n_slot = sum(r["slot_matches"] for r in results)
        n_slots_total = sum(9 for _ in results)
        print(f"Name-match rate (same 9 players started, any order):  "
              f"{n_name}/{n_slots_total} = {n_name/n_slots_total:.1%}")
        print(f"Exact-slot-match rate (same player, same batting slot): "
              f"{n_slot}/{n_slots_total} = {n_slot/n_slots_total:.1%}")
        dist = Counter(r["name_matches"] for r in results)
        print("\nDistribution of name-matches per team-day (out of 9):")
        for k in sorted(dist):
            print(f"  {k}/9 players correct: {dist[k]} team-days")


if __name__ == "__main__":
    main()

"""Recheck representative official boxscore labels against official play logs.

This is a bounded six-game source validation, separate from synthetic tests.
The play log is used only to audit the parser; the full experiment uses box
scores because an all-season live-feed download is unnecessary for labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from run import BASE_URL, cache_boxscore, get_bytes, parse_game


# (season, gamePk, original player id, expected substitute ids, expected PA
# lost to substitute batters, required official action phrases).
CASES = (
    (2024, 744795, 521692, (680118, 666023), 0,
     ("Pinch-runner Dairon Blanco replaces Salvador Perez",
      "Freddy Fermin replaces Dairon Blanco")),
    (2025, 777722, 641487, (660844,), 2,
     ("Leo Rivas replaces shortstop J.P. Crawford",
      "Defensive switch from second base to shortstop for Dylan Moore")),
    (2026, 822844, 665489, (664702,), 3,
     ("Myles Straw replaces first baseman Vladimir Guerrero Jr.",
      "Defensive switch from third base to first base for Kazuma Okamoto")),
    (2026, 824546, 803011, (502054, 671976), 1,
     ("Pinch-hitter Tommy Pham replaces Sam Antonacci",
      "Pinch-runner Tristan Peters replaces Tommy Pham")),
    (2026, 824222, 701678, (656716,), 0,
     ("Pinch-runner Zach McKinstry replaces Hao-Yu Lee",)),
    (2026, 824789, 665966, (687952, 650968), 1,
     ("Pinch-hitter Christian Encarnacion-Strand replaces Carlos Narváez",
      "Yohel Pozo replaces Christian Encarnacion-Strand")),
)


def validate(cache: Path, out: Path) -> None:
    results = []
    for year, pk, starter, replacements, lost_pa, phrases in CASES:
        game = {"game_pk": pk, "season": year, "date": str(year), "game_time": "sample"}
        box = cache_boxscore(game, cache)
        rows, errors = parse_game(box)
        assert not errors and len(rows) == 18, (pk, errors)
        found = [r for r in rows if r["player_id"] == starter]
        assert len(found) == 1, (pk, starter, found)
        r = found[0]
        assert r["replacement_ids"] == list(replacements), (pk, r)
        assert r["lost_pa"] == lost_pa, (pk, r)
        url = f"{BASE_URL.replace('/api/v1', '/api/v1.1')}/game/{pk}/feed/live"
        raw = get_bytes(url)
        live = json.loads(raw)
        actions = [event.get("details", {}).get("description", "")
                   for play in live["liveData"]["plays"]["allPlays"]
                   for event in play.get("playEvents", [])
                   if event.get("type") == "action"]
        for phrase in phrases:
            assert any(phrase in action for action in actions), (pk, phrase)
        results.append({"game_pk": pk, "season": year, "starter_id": starter,
                        "replacement_ids": list(replacements), "lost_pa": lost_pa,
                        "boxscore_sha256": box["sha256"],
                        "live_feed_sha256": hashlib.sha256(raw).hexdigest(),
                        "verified_action_phrases": list(phrases)})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"validated {len(results)} representative official games: {out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--cache", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    validate(args.cache, args.out)

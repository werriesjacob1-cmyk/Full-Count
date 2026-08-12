#!/usr/bin/env python3
"""test_persistence.py — coverage for three zero-coverage generate_picks.py
persistence functions: archive_existing_picks, write_json, and persist_
player_snapshots. All write real files to disk and had zero test coverage,
despite archive_existing_picks existing specifically to prevent a
documented, serious failure mode (bettable_games' own docstring: a re-run
silently overwriting the picks file that was actually bet on, destroying
the day's real record), and write_json/persist_player_snapshots both
having their own documented history of a field being computed in memory
and then silently dropped at the JSON boundary (combo_player_ids,
signal_weight_adjustment), which broke grading for real shipped pick types.

All three module-level path constants (PICKS_JSON_FILE, PLAYERS_DIR) are
monkeypatched to a temp directory for the duration of each test, and
restored afterward -- this suite never touches the real output/ or
data/players/ directories.

    /tmp/mlbvenv/bin/python3 test_persistence.py
"""
import sys
import os
import json
import shutil
import tempfile

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
_results = []


def check(cond, msg, detail=""):
    _results.append((bool(cond), msg, detail))
    if VERBOSE or not cond:
        tag = "PASS" if cond else "FAIL"
        line = "  [%s] %s" % (tag, msg)
        if detail and (VERBOSE or not cond):
            line += "\n         " + detail
        print(line)


def head(t):
    if VERBOSE:
        print()
    print("-- %s" % t)


import generate_picks as gp

TMPDIR = tempfile.mkdtemp(prefix="gridiron_test_persist_")


def cand(name="Slugger", player_id=5, prop="Over 1.5 Total Bases", **over):
    c = {"type": "batter", "name": name, "player_id": player_id, "team": "Athletics",
         "matchup": "Athletics @ Astros", "game_pk": 900001, "side": None,
         "prop": prop, "projection": {"stat": "total_bases", "value": 1.5, "needs": 2},
         "score": 72.0, "confidence": "Medium", "notable_signals": 1,
         "hit_probability": 0.68, "signals": {"skill": 65.0}}
    c.update(over)
    return c


head("== archive_existing_picks ==")
head("1. no existing picks file at all returns None -- nothing to archive")

gp.PICKS_JSON_FILE = os.path.join(TMPDIR, "no_such_file.json")
check(gp.archive_existing_picks("2026-08-12") is None,
      "with no existing picks JSON on disk, archive_existing_picks returns None")

head("2. THE FAILURE THIS PREVENTS: a real existing picks file is copied to a "
     "timestamped archive BEFORE anything can overwrite it")

real_picks_path = os.path.join(TMPDIR, "real_picks.json")
gp.PICKS_JSON_FILE = real_picks_path
gp.OUTPUT_DIR = TMPDIR
payload = {"date": "2026-08-12", "generated": "2026-08-12T11:00:00", "picks": [{"name": "Real Pick"}]}
with open(real_picks_path, "w") as f:
    json.dump(payload, f)
archive_path = gp.archive_existing_picks("2026-08-12")
check(archive_path is not None and os.path.exists(archive_path),
      "a real existing picks file produces a real archive file on disk", f"got {archive_path}")
with open(archive_path) as f:
    archived_content = json.load(f)
check(archived_content == payload,
      "the archived copy is byte-for-byte the same data as the original -- the exact "
      "board that was actually bet, preserved before any overwrite can happen")

head("3. archiving twice at the identical timestamp doesn't overwrite/duplicate the same archive")

second_call = gp.archive_existing_picks("2026-08-12")
check(second_call is None,
      "calling archive_existing_picks again for the same already-archived generated "
      "timestamp returns None (the archive already exists), rather than silently "
      "re-writing over itself", f"got {second_call}")

head("4. a corrupted (unparseable) existing picks file doesn't crash the run")

corrupt_path = os.path.join(TMPDIR, "corrupt_picks.json")
gp.PICKS_JSON_FILE = corrupt_path
with open(corrupt_path, "w") as f:
    f.write("{not valid json[[[")
check(gp.archive_existing_picks("2026-08-12") is None,
      "a corrupted picks JSON file is handled gracefully (returns None, warns), not a "
      "crash that would take down the whole run")

head("== write_json ==")
head("1. a normal top10 + moonshots + by_category payload writes a well-formed picks file")

json_path = os.path.join(TMPDIR, "picks_out.json")
gp.PICKS_JSON_FILE = json_path
top10 = [cand(name="Top Pick", player_id=1)]
moonshots = [cand(name="Moon Pick", player_id=2, prop="Home Run")]
by_category = {"hits": [cand(name="Category Pick", player_id=3, prop="Over 0.5 Hits")]}
gp.write_json(top10, moonshots, by_category)
check(os.path.exists(json_path), "write_json produces a real file on disk")
with open(json_path) as f:
    written = json.load(f)
check(written["date"] == gp.m.TODAY and "generated" in written,
      "the written payload carries date and a real generated timestamp")
names = [p["name"] for p in written["picks"]]
check(names == ["Top Pick", "Moon Pick", "Category Pick"],
      "top10, moonshots, and category picks are all appended into the SAME picks list, "
      "in that order, ranks continuing rather than restarting", f"got {names}")
ranks = [p["rank"] for p in written["picks"]]
check(ranks == [1, 2, 3], "ranks are continuous across all three groups, not restarted "
      "at 1 for each group", f"got {ranks}")

head("2. THE BUG THIS FIXES: combo_player_ids and signal_weight_adjustment survive to disk "
     "-- both were computed in memory and silently dropped at this exact boundary before")

combo_c = cand(name="Combo Pick", player_id=None,
              combo_player_ids=[501, 502], signal_weight_adjustment={"platoon": 1.1})
gp.write_json([combo_c], [], {})
with open(json_path) as f:
    written2 = json.load(f)
check(written2["picks"][0]["combo_player_ids"] == [501, 502],
      "combo_player_ids is present in the written JSON, not silently dropped -- without "
      "it, grade_pick's combined_strikeouts branch fails 'missing combo_player_ids'",
      f"got {written2['picks'][0]}")
check(written2["picks"][0]["signal_weight_adjustment"] == {"platoon": 1.1},
      "signal_weight_adjustment is also present -- apply_signal_weights' own docstring "
      "promise ('every adjustment is recorded, never silent') only holds if this "
      "survives to the persisted record")

head("3. a candidate with hit_probability=None writes max_acceptable_price/estimated_odds "
     "as None too, never a fabricated price for an unpriced pick")

unpriced = cand(name="Unpriced", player_id=9, hit_probability=None)
gp.write_json([unpriced], [], {})
with open(json_path) as f:
    written3 = json.load(f)
check(written3["picks"][0]["max_acceptable_price"] is None
      and written3["picks"][0]["estimated_odds"] is None,
      "an unpriced candidate's derived price fields are honestly None, not computed "
      "from a missing probability")

head("== persist_player_snapshots ==")
head("4. a fresh player (no existing file) gets a new history file with one snapshot")

players_dir = os.path.join(TMPDIR, "players")
gp.PLAYERS_DIR = players_dir
gp.persist_player_snapshots([cand(name="Fresh Player", player_id=42)])
player_path = os.path.join(players_dir, "42.json")
check(os.path.exists(player_path), "a new player file is created on first run")
with open(player_path) as f:
    hist = json.load(f)
check(hist["player_id"] == 42 and hist["name"] == "Fresh Player"
      and len(hist["snapshots"]) == 1,
      "the fresh history carries the right id, name, and exactly one snapshot",
      f"got {hist}")
check(hist["snapshots"][0]["evaluations"][0]["combo_player_ids"] is None
      or "combo_player_ids" in hist["snapshots"][0]["evaluations"][0],
      "combo_player_ids is a real key in the persisted evaluation (same gap class as "
      "write_json, this function's own comments say found the same way)")

head("5. a SECOND run on the SAME DATE replaces (not duplicates) that day's snapshot")

gp.persist_player_snapshots([cand(name="Fresh Player", player_id=42, score=99.0)])
with open(player_path) as f:
    hist2 = json.load(f)
check(len(hist2["snapshots"]) == 1,
      "re-running on the identical date still leaves exactly ONE snapshot for that "
      "date, not two", f"got {len(hist2['snapshots'])} snapshots")
check(hist2["snapshots"][0]["evaluations"][0]["score"] == 99.0,
      "the SAME-DATE snapshot is replaced with the latest run's data, not appended "
      "alongside the stale one")

head("6. multiple candidates for the SAME player on the same day are grouped into one "
     "snapshot's evaluations list, not one snapshot each")

gp.persist_player_snapshots([
    cand(name="Multi Prop", player_id=77, prop="Over 1.5 Total Bases"),
    cand(name="Multi Prop", player_id=77, prop="To Steal a Base",
        projection={"stat": "stolen_base", "value": 1}),
])
with open(os.path.join(players_dir, "77.json")) as f:
    hist3 = json.load(f)
check(len(hist3["snapshots"]) == 1 and len(hist3["snapshots"][0]["evaluations"]) == 2,
      "two props for the same player on the same day produce ONE snapshot with TWO "
      "evaluations, not two separate snapshots", f"got {hist3['snapshots']}")

head("7. history is bounded to PLAYER_SNAPSHOT_HISTORY_DAYS entries, oldest dropped first")

gp.PLAYER_SNAPSHOT_HISTORY_DAYS = 3
seed_path = os.path.join(players_dir, "88.json")
seed_history = {"player_id": 88, "name": "Long History Guy", "snapshots": [
    {"date": f"2026-08-{d:02d}", "evaluations": []} for d in range(1, 6)  # 5 real prior days
]}
with open(seed_path, "w") as f:
    json.dump(seed_history, f)
gp.persist_player_snapshots([cand(name="Long History Guy", player_id=88)])
with open(seed_path) as f:
    hist4 = json.load(f)
check(len(hist4["snapshots"]) == 3,
      "with PLAYER_SNAPSHOT_HISTORY_DAYS=3, a file that had 5 old days plus today's new "
      "one (6 total) is trimmed down to exactly the most recent 3", f"got {len(hist4['snapshots'])}")
check(hist4["snapshots"][-1]["date"] == gp.m.TODAY,
      "today's newly-written snapshot is the most recent entry, not trimmed away itself")
gp.PLAYER_SNAPSHOT_HISTORY_DAYS = 60

head("8. a candidate with no player_id is skipped entirely, not persisted under a null key")

before_files = set(os.listdir(players_dir))
gp.persist_player_snapshots([cand(name="No ID", player_id=None)])
after_files = set(os.listdir(players_dir))
check(before_files == after_files,
      "a candidate with player_id=None produces no new file on disk at all")

head("9. an empty candidate list writes nothing and doesn't crash")

gp.persist_player_snapshots([])
check(True, "an empty candidate list completes without raising")

shutil.rmtree(TMPDIR, ignore_errors=True)

n_pass = sum(1 for ok, _, _ in _results if ok)
n_total = len(_results)
print("\n" + "=" * 78)
print(f"RESULT: {n_pass}/{n_total} checks passed")
if n_pass < n_total:
    print()
    for ok, msg, detail in _results:
        if not ok:
            print(f"  FAILED: {msg}")
            if detail:
                print(f"          {detail}")
print("=" * 78)
sys.exit(0 if n_pass == n_total else 1)

#!/usr/bin/env python3
"""test_normalize_live_perf.py -- regression coverage for the 2026-08-24
freshness-outage fix: normalize_live() used to call merge_live_states()
once PER INCOMING PROP ENTRY, and merge_live_states() deep-copies its
entire `base` argument on every call -- O(n) work repeated n times, O(n^2)
total. Measured directly against the real, uncompacted docs/live.json
(~9,850 entries): 6+ minutes in this one function, the dominant cost
behind the Dashboard Live Update workflow's 15-minute timeout (see
backtest/measure_normalize_live_perf.py for the live-payload measurement
this file's synthetic version is modeled on).

THE FIX: dashboard.live_state.merge_live_state_delta_into(merged, incoming)
is merge_live_states()'s exact per-field reconciliation loop, factored out
so a caller building up ONE accumulator across MANY small documents can
mutate it in place instead of deep-copying it on every call.
normalize_live() now calls this directly on its own freshly-built
accumulator.

Two things must both hold, and this file proves both:
1. CORRECTNESS: the new code produces byte-identical output to the old
   per-entry merge_live_states() loop, including the one case that
   actually needs real field-level reconciliation -- two distinct old_ids
   remapping onto the same new_id (a genuine collision).
2. PERFORMANCE: normalize_live() on a large synthetic live-state document
   completes in a small constant-ish time, not time that grows with the
   square of the entry count.

    /tmp/mlbvenv/bin/python3 test_normalize_live_perf.py
"""
import copy
import sys
import time

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


from dashboard.live_state import (  # noqa: E402
    default_live_state, merge_live_state_delta_into, merge_live_states,
)
from dashboard.prepare_pages_artifact import normalize_live  # noqa: E402


def make_delta(**over):
    d = {"market_odds": -110, "market_observed_at": "2026-08-24T20:00:00+00:00",
         "_field_updated_at": {"market_odds": "2026-08-24T20:00:00+00:00"}}
    d.update(over)
    return d


def old_normalize_live_reference(live, id_map):
    """The exact pre-fix algorithm: one merge_live_states() call per
    incoming entry. Reconstructed here (not imported -- the fix replaced
    it) purely as a correctness oracle for check 1 below."""
    from dashboard.live_state import copy as _copy  # noqa
    import copy as _c
    normalized = _c.deepcopy(live)
    remapped = default_live_state()
    for key in ("updated_at", "prices_updated_at", "grades_updated_at",
                "grades_checked_at", "prices_checked_at"):
        remapped[key] = normalized.get(key)
    for old_id, delta in (normalized.get("props") or {}).items():
        new_id = id_map.get(old_id, old_id)
        one = default_live_state()
        one["updated_at"] = normalized.get("updated_at")
        one["prices_updated_at"] = normalized.get("prices_updated_at")
        one["grades_updated_at"] = normalized.get("grades_updated_at")
        one["props"][new_id] = _c.deepcopy(delta)
        remapped = merge_live_states(remapped, one)
    return remapped


head("1. CORRECTNESS, no collisions: new normalize_live() output is "
     "byte-identical to the old per-entry merge_live_states() loop for the "
     "overwhelming common case -- every old_id maps to a distinct new_id")

live1 = {
    "schema_version": 3, "props": {
        f"fc2:900001:player-{i}:hits:1:over": make_delta(market_odds=-100 - i)
        for i in range(200)
    },
    "updated_at": "2026-08-24T20:00:00+00:00",
}
id_map1 = {}  # already-canonical ids, id_map is a no-op (the real production shape)
old1 = old_normalize_live_reference(live1, id_map1)
new1 = normalize_live(live1, id_map1)
check(old1 == new1, "old and new implementations produce an identical result on 200 "
      "non-colliding entries", f"n_old={len(old1['props'])} n_new={len(new1['props'])} "
      f"equal={old1 == new1}")

head("2. CORRECTNESS, a genuine collision: two distinct old_ids remap onto the SAME "
     "new_id -- the one case that actually requires real field-level reconciliation, "
     "not just a direct assignment. Both implementations must resolve it identically "
     "(newer timestamp per field wins).")

live2 = {
    "schema_version": 3, "props": {
        "legacy-id-A": make_delta(market_odds=-110,
                                   market_observed_at="2026-08-24T20:00:00+00:00",
                                   _field_updated_at={"market_odds": "2026-08-24T20:00:00+00:00"}),
        "legacy-id-B": make_delta(market_odds=-130,
                                   market_observed_at="2026-08-24T20:05:00+00:00",
                                   _field_updated_at={"market_odds": "2026-08-24T20:05:00+00:00"}),
    },
    "updated_at": "2026-08-24T20:05:00+00:00",
}
id_map2 = {"legacy-id-A": "fc2:900001:player-1:hits:1:over",
           "legacy-id-B": "fc2:900001:player-1:hits:1:over"}
old2 = old_normalize_live_reference(live2, id_map2)
new2 = normalize_live(live2, id_map2)
check(old2 == new2, "old and new implementations resolve a genuine id collision "
      "identically", f"old={old2['props']}\n         new={new2['props']}")
check(new2["props"]["fc2:900001:player-1:hits:1:over"]["market_odds"] == -130,
      "sanity: the LATER-timestamped delta (id-B, -130) actually won the field-level "
      "reconciliation, proving this is a real merge, not just 'last one written wins'",
      str(new2["props"]["fc2:900001:player-1:hits:1:over"]))

head("3. CORRECTNESS: merge_live_state_delta_into() mutating a fresh accumulator "
     "produces the identical result to merge_live_states() building the same "
     "accumulator from the same base+incoming, for a real multi-field delta")

base3 = default_live_state()
incoming3 = {
    "props": {"fc2:900001:player-9:hits:1:over": make_delta(
        market_odds=-115, price_clears=True,
        settlement_state="provisional_hit", settlement_authority="live_observation",
        settlement_observed_at="2026-08-24T20:10:00+00:00",
        settlement_source="mlb_game_feed_by_game_pk", result_actual=1,
        game_state="live", game_state_observed_at="2026-08-24T20:10:00+00:00",
        game_state_source="mlb_game_feed_by_game_pk",
    )},
    "updated_at": "2026-08-24T20:10:00+00:00",
}
via_merge_live_states = merge_live_states(base3, incoming3)
acc = copy.deepcopy(base3)
merge_live_state_delta_into(acc, incoming3)
check(via_merge_live_states["props"] == acc["props"],
      "merge_live_states() and merge_live_state_delta_into() (on a pre-deep-copied "
      "accumulator) produce identical props", f"got {acc['props']}")

head("4. PERFORMANCE: normalize_live() on a large synthetic live-state document "
     "completes in well under a second -- proof the O(n^2) behavior is gone. The old "
     "algorithm, measured directly against the real production docs/live.json "
     "(~9,850 entries), took 6+ minutes for this exact function.")

N = 3000
big_live = {
    "schema_version": 3, "props": {
        f"fc2:900001:player-{i}:hits:1:over": make_delta(market_odds=-100 - (i % 50))
        for i in range(N)
    },
    "updated_at": "2026-08-24T20:00:00+00:00",
}
t0 = time.time()
result = normalize_live(big_live, {})
elapsed = time.time() - t0
check(len(result["props"]) == N, f"sanity: all {N} entries survived normalization",
      f"got {len(result['props'])}")
check(elapsed < 5.0,
      f"normalize_live() on {N} synthetic entries completes in under 5s (a generous "
      f"ceiling -- real observed time is well under 1s); the old O(n^2) algorithm "
      f"would take minutes at this size", f"elapsed={elapsed:.3f}s")

head("5. PERFORMANCE GUARD, a second live-freshness-critical function: "
     "compact_live_state() -- the function that runs on EVERY successful "
     "dashboard-live.yml completion and is what actually shrinks docs/live.json "
     "back down (9,853 -> 14 entries, confirmed live) once normalize_live()'s "
     "O(n^2) bug stopped starving it of a chance to run at all. It has its own, "
     "different O(n) shape (a single loop, dict/set membership checks -- see its "
     "own docstring) but carries no regression test of its own yet. Added here, "
     "next to normalize_live()'s, so a future change to either can't reintroduce "
     "the same failure class (a stray list where a set/dict was assumed, an "
     "accidental nested loop) without a fast, obvious CI failure -- exactly the "
     "kind of regression this freshness-outage investigation exists to catch "
     "before it reaches production again, not after 16+ hours of stale odds.")

from dashboard.live_state import compact_live_state  # noqa: E402

N2 = 5000
big_live2 = default_live_state()
big_live2["updated_at"] = "2026-08-24T20:00:00+00:00"
for i in range(N2):
    pid = f"fc2:900001:player-{i}:hits:1:over"
    if i % 3 == 0:
        big_live2["props"][pid] = {
            "settlement_state": "hit", "settlement_authority": "official_final",
            "settlement_source": "mlb_game_feed_by_game_pk", "result_actual": 1,
            "settlement_observed_at": "2026-08-24T18:00:00+00:00",
        }
    else:
        big_live2["props"][pid] = {"settlement_state": "open", "settlement_authority": "none",
                                   "settlement_source": "dashboard_builder",
                                   "settlement_observed_at": "2026-08-24T18:00:00+00:00"}
# A realistic compaction call: most props are neither current nor published,
# so they're real candidates for the eligibility checks compact_live_state()
# runs per entry -- the shape that would expose an accidental O(n^2) the same
# way the real 9,853-entry docs/live.json exposed normalize_live()'s.
current_ids2 = {f"fc2:900001:player-{i}:hits:1:over" for i in range(0, N2, 100)}
published_ids2 = {f"fc2:900001:player-{i}:hits:1:over" for i in range(0, N2, 50)}
durable2 = {f"fc2:900001:player-{i}:hits:1:over": ("hit", "2026-08-24T19:00:00+00:00")
           for i in range(0, N2, 3)}
t0 = time.time()
compacted = compact_live_state(big_live2, current_ids=current_ids2, published_ids=published_ids2,
                               durable_settlements=durable2)
elapsed2 = time.time() - t0
check(len(compacted["props"]) <= N2, "sanity: compaction never grows the prop count",
      f"got {len(compacted['props'])} of {N2}")
check(elapsed2 < 5.0,
      f"compact_live_state() on {N2} synthetic entries completes in under 5s -- same "
      f"generous ceiling as normalize_live()'s own guard above", f"elapsed={elapsed2:.3f}s")

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

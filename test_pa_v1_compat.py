"""Mission 1.2 — PA-v1 historical-semantics compatibility adapter.

Locks the reference-clock defect and its remedy across every boundary.
"""
import sys

from backtest import pa_v1_compat as pac
from backtest.pa_v1_fit import days_rest_group
from generate_picks import clamp

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f" {detail}"))
    if not cond:
        FAILURES.append(name)


def hist_signal(n_hist):
    return clamp((n_hist - 1) * 2, -3, 4)


def live_signal(n_live):
    return clamp((n_live - 1) * 2, -3, 4)


print("Check 1: the defect is real — live and historical disagree untransformed")
# last game at D-k  =>  n_hist = k-1 (clock D-1),  n_live = k (clock D)
disagree = []
for k in range(1, 9):
    hb = days_rest_group({"days_rest": hist_signal(k - 1)})
    lb = days_rest_group({"days_rest": live_signal(k)})
    if hb != lb:
        disagree.append((k, hb, lb))
check("at least one circumstance disagrees", len(disagree) >= 1, str(disagree))
check("D-2 disagrees (one off day, a very common case)",
      any(k == 2 for k, _, _ in disagree), str(disagree))
check("D-3 disagrees", any(k == 3 for k, _, _ in disagree), str(disagree))

print("\nCheck 2: the scaled signal is NOT invertible (so the raw is required)")
images = {live_signal(n): set() for n in range(1, 9)}
for n in range(1, 9):
    images[live_signal(n)].add(hist_signal(n - 1))
ambiguous = {v: img for v, img in images.items() if len(img) > 1}
check("some live signal maps to >1 historical signal", bool(ambiguous), str(images))
check("specifically v_live=4", 4 in ambiguous, str(ambiguous))

print("\nCheck 3: THE TRANSFORM IS EXACT for every derivable circumstance")
for k in range(1, 12):
    n_live = k
    want = hist_signal(k - 1)
    got, note = pac.historical_days_rest_signal(n_live)
    check(f"last game D-{k}: transform reproduces the frozen cell",
          got == want and days_rest_group({"days_rest": got})
          == days_rest_group({"days_rest": want}),
          f"got {got} want {want}")

print("\nCheck 4: the historical feature is off-days, proven not assumed")
# (D-1) - (D-k) = k-1 = the number of off days strictly between the two.
for k in range(1, 6):
    off_days = k - 1
    raw, _ = pac.historical_days_rest_raw(k)
    check(f"last game D-{k}: {off_days} off day(s)", raw == off_days,
          f"got {raw}")
check("named for what it measures",
      pac.HISTORICAL_FEATURE == "off_days_since_last_game")

print("\nCheck 5: doubleheader / same-day rule is explicit and fails closed")
v, note = pac.historical_days_rest_signal(0)
check("same-day game yields no value", v is None)
check("with a named reason", note == pac.FALLBACK_SAME_DAY_GAME, note)
out, note2 = pac.adapt_signals({"lineup_slot": 100.0, "days_rest": 0,
                                "getaway_day": 0}, 0)
check("the key is REMOVED, not set to None (matches _sig's contract)",
      "days_rest" not in out)
check("other signals are untouched",
      out["lineup_slot"] == 100.0 and out["getaway_day"] == 0)

print("\nCheck 6: absent stays absent; the input is never mutated")
check("None in -> key removed", "days_rest" not in pac.adapt_signals({"days_rest": 2}, None)[0])
src = {"days_rest": 99, "lineup_slot": 50.0}
pac.adapt_signals(src, 3)
check("input dict unmutated", src == {"days_rest": 99, "lineup_slot": 50.0})
check("output is a different object", pac.adapt_signals(src, 3)[0] is not src)

print("\nCheck 7: the frozen artifact is untouched")
import json
art = json.load(open("backtest/pa_v1_fitted_artifact.json"))
check("scientific hash unchanged",
      art["scientific_content_sha256"]
      == "a4f598bd4138305d8da4d85767eb873781b10e918dd1e402d536d9cd13fadf4a")
check("adapter never writes the artifact",
      "pa_v1_fitted_artifact" not in open("backtest/pa_v1_compat.py").read())
check("adapter never touches fitted tables",
      "joint_pa_table" not in open("backtest/pa_v1_compat.py").read())

print("\nCheck 8: provenance is receipt-recordable and versioned")
p = pac.provenance(3)
for k in ("compat_version", "historical_feature", "historical_reference_clock",
          "live_reference_clock", "live_days_since_last_game",
          "historical_equivalent_raw", "note"):
    check(f"provenance carries {k}", k in p)
check("version string is stable", p["compat_version"] == "pa-v1-rest-semantics-compat-v1")
check("records BOTH clocks",
      "minus_1" in p["historical_reference_clock"] and p["live_reference_clock"] == "slate_date")

print()
if FAILURES:
    print(f"FAILED {len(FAILURES)}: {FAILURES}")
    sys.exit(1)
print("All PA-v1 compat checks passed.")

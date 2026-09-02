"""The frozen bootstrap contract's pin, and the per-arm clustering diagnostic.

Both checks exist because a red team broke the previous versions by execution:
the pin covered the DECLARED contract values while the resampling code could
be swapped underneath it, and the clustering diagnostic was measured over both
arms pooled, understating the challenger's own concentration ~3x.
"""

import importlib.util
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest import prospective_bootstrap as pb

FAILURES = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f" {detail}" if not condition else ""))
    if not condition:
        FAILURES.append(name)


def load_mutated(old, new, name):
    """Import a private copy of the module with one edit applied."""
    src = open(pb.__file__, encoding="utf-8").read()
    assert old in src, "the source this test mutates has moved"
    d = tempfile.mkdtemp()
    path = os.path.join(d, f"{name}.py")
    open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


print("Check 1: the pin covers the IMPLEMENTATION, not only the declarations")
check("the unmodified module verifies", pb.verify_contract_unmodified() is True)

swapped = load_mutated(
    """        drawn = [rng.choice(dates) for _ in range(len(dates))]
        resampled = []
        for date in drawn:
            resampled.extend(clusters[date])""",
    """        resampled = [rng.choice(settlements) for _ in range(len(settlements))]""",
    "pb_rowswap")
# The exact attack: resample ROWS instead of date clusters. This narrows the
# interval -- the direction that flatters a promotion decision -- while
# CONTRACT still declares `unit: slate_date`.
check("the declared-values pin alone does NOT notice the swap",
      swapped.contract_sha256() == swapped.EXPECTED_CONTRACT_SHA256)
try:
    swapped.verify_contract_unmodified()
    check("the implementation swap is REFUSED", False, "it was accepted")
except swapped.ContractModified as exc:
    check("the implementation swap is REFUSED", True)
    check("and the reason names the implementation, not a value",
          "IMPLEMENTATION" in str(exc))

print("\nCheck 2: prose edits do not trip the pin")
prose = load_mutated('"""Hash the RESAMPLING CODE, not just the declared contract values.',
                     '"""Hash the resampling code. Reworded docstring.',
                     "pb_prose")
check("a docstring rewrite still verifies",
      prose.verify_contract_unmodified() is True)

print("\nCheck 3: concentration is measured PER ARM")
# PA-v1 reselects 4 players; the champion spreads over 20. Pooled, PA-v1's
# concentration disappears into the champion's spread.
rows = []
for d in range(5):
    for i in range(4):
        rows.append({"slate_date": f"2026-09-0{d+1}", "player_id": 100 + i,
                     "game_pk": 900 + i, "outcome": "hit",
                     "champion_member": False, "pa_v1_member": True})
    for i in range(4):
        rows.append({"slate_date": f"2026-09-0{d+1}", "player_id": 200 + d * 4 + i,
                     "game_pk": 800 + d * 4 + i, "outcome": "miss",
                     "champion_member": True, "pa_v1_member": False})

pooled = pb.concentration(rows, "player_id")
pa = pb.concentration(rows, "player_id", "pa_v1_member")
champ = pb.concentration(rows, "player_id", "champion_member")
print(f"    pooled effective_n={pooled['effective_n']}  "
      f"pa_v1={pa['effective_n']}  champion={champ['effective_n']}")
check("PA-v1's own effective count is far lower than the pooled one",
      pa["effective_n"] < pooled["effective_n"] / 2,
      f"{pa['effective_n']} vs {pooled['effective_n']}")
check("PA-v1's max share is the concentration that matters",
      pa["max_share"] > pooled["max_share"])
check("the champion arm is genuinely more spread", champ["effective_n"] > pa["effective_n"])

print("\nCheck 4: broken arm pairing is REPORTED, not silent")
sec = pb.secondary_clustering(rows, "player_id")
check("every player cluster here feeds exactly one arm",
      sec["unpaired_fraction"] == 1.0, str(sec["unpaired_fraction"]))
check("and the count is carried alongside it",
      sec["single_arm_clusters"] == sec["n_clusters"])
paired = [dict(r, champion_member=True, pa_v1_member=True) for r in rows]
check("a genuinely paired unit reports 0.0",
      pb.secondary_clustering(paired, "player_id")["unpaired_fraction"] == 0.0)

print()
if FAILURES:
    print(f"FAILED {len(FAILURES)}: {FAILURES}")
    sys.exit(1)
print("All prospective bootstrap contract/clustering checks passed.")

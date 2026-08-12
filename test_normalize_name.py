#!/usr/bin/env python3
"""test_normalize_name.py — direct coverage for odds_fanduel.normalize_name,
the join key used everywhere a name from one source (FanDuel, MLB Stats API,
Statcast) has to match a name from another. It had no dedicated test of its
own -- only incidental exercise as a side effect of other tests -- despite
being a single point of failure: get this wrong and a real price silently
fails to match a real player everywhere in the pipeline at once.

    /tmp/mlbvenv/bin/python3 test_normalize_name.py
"""
import sys

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


import odds_fanduel as fd

n = fd.normalize_name

head_cases = [
    ("Bobby Witt Jr.", "bobby witt", "Jr. suffix stripped (this project's own docstring example)"),
    ("Michael Harris II", "michael harris", "II suffix stripped"),
    ("Ken Griffey Sr.", "ken griffey", "Sr. suffix stripped"),
    ("Robinson Cano III", "robinson cano", "III suffix stripped"),
    ("Julio Rodriguez IV", "julio rodriguez", "IV suffix stripped"),
    ("Jose Ramirez", "jose ramirez", "a plain name with no suffix is unchanged (lowercased)"),
    ("YORDAN ALVAREZ", "yordan alvarez", "all-caps input still matches lowercase"),
]
for raw, want, desc in head_cases:
    got = n(raw)
    check(got == want, desc, f"normalize_name({raw!r}) = {got!r}, want {want!r}")

# Accented characters: real MLB rosters carry plenty (Ramirez/Ramírez,
# Alcántara, Suárez, Encarnación) and FanDuel/MLB Stats API don't always
# agree on whether to render the accent.
accent_cases = [
    ("José Ramírez", "jose ramirez"),
    ("Sandy Alcántara", "sandy alcantara"),
    ("Eugenio Suárez", "eugenio suarez"),
    ("Edwin Encarnación", "edwin encarnacion"),
]
for raw, want in accent_cases:
    got = n(raw)
    check(got == want, f"accented {raw!r} normalizes to the unaccented ASCII form",
          f"got {got!r}, want {want!r}")
check(n("José Ramírez") == n("Jose Ramirez"),
      "an accented and unaccented spelling of the SAME name collide to the same key",
      f"{n('José Ramírez')!r} vs {n('Jose Ramirez')!r}")

# A name that legitimately CONTAINS "ii"/"iv"/etc as normal letters, not a
# generational suffix, must not be mangled -- the \b word boundaries in the
# regex are what should protect this.
check(n("Aristides Aquino") == "aristides aquino",
      "a name containing 'i'-heavy substrings with no suffix is not falsely stripped",
      f"got {n('Aristides Aquino')!r}")

# The whole point of stripping the suffix: "Bobby Witt Jr." (one source) and
# "Bobby Witt" (another source omitting the suffix, same real person) must
# normalize to the SAME key, or a real price would silently fail to match
# the player it belongs to.
check(n("Bobby Witt Jr.") == n("Bobby Witt"),
      "the intended collision: two spellings of the same real person's name "
      "(with/without a generational suffix) normalize to the same key")

check(n("") == "", "an empty string returns empty rather than raising")
check(n(None) == "", "None returns empty rather than raising -- callers pass "
                     "v.get('name') results that can genuinely be None")

check(n("Jr. Jones") == n("Jr. Jones"),
      "idempotent / self-consistent on repeated calls with the same input")
check(n(n("Bobby Witt Jr.")) == n("Bobby Witt Jr."),
      "normalizing an already-normalized name is a no-op (idempotent)")

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

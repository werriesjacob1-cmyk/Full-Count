#!/usr/bin/env python3
"""Public-ledger monotonicity: previously published identities must not vanish.

WHAT THIS IS. On 2026-09-03 `refs/heads/main` was force-pushed onto an unmerged
branch. 153 commits disappeared and, with them, part of the immutable public
evidence estate: 12 canonical identities vanished from the graded ledger and 6
from the publication registry. Nobody noticed for 20 minutes, and then only by
accident -- the same slate returned 18 picks and then 6.

This compares that estate between two commits and fails if any canonical
identity present in the earlier one is absent from the later one.

WHAT IT IS NOT. This is DETECTION, not PREVENTION. It runs after a push has
already been accepted. Only a server-side GitHub ruleset blocking force-pushes
is prevention, and it must be configured at GitHub, not here. In particular a
push that deletes or disables the workflow invoking this checker cannot be
caught by that workflow -- an in-repository check cannot detect its own
removal. That limitation is real and is not engineered around.

WHY IDENTITIES AND NOT COUNTS. Counts hide substitution: a ledger can lose one
pick and gain another and still total the same. The two estates also regressed
by DIFFERENT amounts in the incident (12 vs 6), so they are compared
separately rather than pooled.

PER SPORT (NFL-01). Each sport has its OWN estate files and they are compared
independently, so a healthy MLB estate can never mask a loss in an NFL one.
Pooling the two into a single identity set is exactly the mistake that would
allow it: 270 healthy MLB identities beside one lost NFL identity would still
look like a large, healthy set. So losses are reported per sport, and an estate
is never compared against another sport's.

NFL ESTATES DO NOT EXIST YET, and their absence must not be a failure. The rule
for an estate declared OPTIONAL is:

    absent at BOTH refs   -> not established yet; nothing to lose; not a failure
    present at BOTH       -> compared, exactly like a required estate
    present at BEFORE and
      absent at AFTER     -> the whole estate vanished. THAT IS THE LOUDEST
                             possible failure, and it fails.

MLB estates remain REQUIRED, so a missing MLB estate is still Unreadable and
still fails closed. That behaviour is unchanged and is byte-verified.

CROSS-SPORT CONTAMINATION is also checked: an MLB estate must contain no
`fcnfl1:` identity and an NFL estate no `fc2:` one. A row in the wrong estate
means a writer crossed the partition, which would put NFL evidence at risk of
being rewritten by MLB tooling that has every right to rewrite its own.

    python3 ledger_integrity.py <before-ref> <after-ref>

Read-only: reads git objects, writes nothing, needs no credentials.
Exit 0 = no identity lost. Exit 1 = identity lost, or the comparison could not
be made. Missing evidence is never reported as PASS.
"""
import json
import subprocess
import sys

REGISTRY = "data/public_top_picks/registry.json"


class Unreadable(Exception):
    """The estate could not be read at a ref, so no verdict is possible."""


def _show(ref, path):
    r = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def _grades_files(ref):
    r = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref, "results/"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise Unreadable(f"cannot list results/ at {ref}")
    return [p for p in r.stdout.split() if "/grades_" in p and p.endswith(".json")]


def graded_identities(ref):
    """Canonical ids recorded in results/grades_*.json public_top_picks."""
    out = set()
    for path in _grades_files(ref):
        blob = _show(ref, path)
        if blob is None:
            raise Unreadable(f"cannot read {path} at {ref}")
        try:
            payload = json.loads(blob)
        except (json.JSONDecodeError, ValueError) as exc:
            # A grades file that stopped parsing is itself an integrity
            # problem: its identities become unverifiable. Fail closed.
            raise Unreadable(f"{path} at {ref} is not valid JSON: {exc}") from exc
        for rec in (payload.get("public_top_picks") or []):
            if rec.get("id"):
                out.add(rec["id"])
    return out


def registry_identities(ref):
    """Canonical ids in the publication registry (its `entries` keys)."""
    blob = _show(ref, REGISTRY)
    if blob is None:
        raise Unreadable(f"cannot read {REGISTRY} at {ref}")
    try:
        payload = json.loads(blob)
    except (json.JSONDecodeError, ValueError) as exc:
        raise Unreadable(f"{REGISTRY} at {ref} is not valid JSON: {exc}") from exc
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        raise Unreadable(f"{REGISTRY} at {ref} has no `entries` object")
    return set(entries)


# NFL estate paths. Separate files from MLB's by design: a buggy future NFL
# writer must be structurally incapable of deleting MLB public evidence because
# both sports share one file. These do not exist yet.
NFL_REGISTRY = "nfl/data/public_top_picks/registry.json"
NFL_RESULTS_DIR = "nfl/results/"


def _estate_absent(ref, path):
    return _show(ref, path) is None


def nfl_graded_identities(ref):
    """Canonical ids in nfl/results/grades_*.json public_top_picks."""
    out = set()
    r = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref, NFL_RESULTS_DIR],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise Unreadable(f"cannot list {NFL_RESULTS_DIR} at {ref}")
    for path in [p for p in r.stdout.split()
                 if "/grades_" in p and p.endswith(".json")]:
        blob = _show(ref, path)
        if blob is None:
            raise Unreadable(f"cannot read {path} at {ref}")
        try:
            payload = json.loads(blob)
        except (json.JSONDecodeError, ValueError) as exc:
            raise Unreadable(f"{path} at {ref} is not valid JSON: {exc}") from exc
        for rec in (payload.get("public_top_picks") or []):
            if rec.get("id"):
                out.add(rec["id"])
    return out


def nfl_registry_identities(ref):
    """Canonical ids in the NFL publication registry (its `entries` keys)."""
    blob = _show(ref, NFL_REGISTRY)
    if blob is None:
        raise Unreadable(f"cannot read {NFL_REGISTRY} at {ref}")
    try:
        payload = json.loads(blob)
    except (json.JSONDecodeError, ValueError) as exc:
        raise Unreadable(f"{NFL_REGISTRY} at {ref} is not valid JSON: {exc}") from exc
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        raise Unreadable(f"{NFL_REGISTRY} at {ref} has no `entries` object")
    return set(entries)


def _nfl_estate_exists(ref):
    """True if either NFL estate file is present at `ref`."""
    if _show(ref, NFL_REGISTRY) is not None:
        return True
    r = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref, NFL_RESULTS_DIR],
                       capture_output=True, text=True)
    return r.returncode == 0 and any(
        "/grades_" in p and p.endswith(".json") for p in r.stdout.split())


# (estate_name, reader). Preserved under the original name and value so any
# existing importer of ESTATES sees exactly what it saw before.
ESTATES = (("graded ledger (results/grades_*.json)", graded_identities),
           ("publication registry (%s)" % REGISTRY, registry_identities))

NFL_ESTATES = (("NFL graded ledger (%sgrades_*.json)" % NFL_RESULTS_DIR,
                nfl_graded_identities),
               ("NFL publication registry (%s)" % NFL_REGISTRY,
                nfl_registry_identities))

# (sport, estates, required). MLB is REQUIRED: a missing MLB estate is the
# incident this file exists for, and stays fail-closed. NFL is OPTIONAL only
# until its estates first appear -- see the docstring for the exact rule, which
# still fails when an estate that existed before has gone.
SPORT_ESTATES = (("MLB", ESTATES, True), ("NFL", NFL_ESTATES, False))

# An identity in the wrong sport's estate means a writer crossed the partition.
FOREIGN_PREFIXES = {"MLB": ("fcnfl1:",), "NFL": ("fc2:",)}


def contamination(before, after):
    """Return a list of (sport, estate_name, foreign ids) found at `after`.

    Checked at `after` only: the question is whether the estate is contaminated
    NOW. A historical cross-write would also show, since it would still be
    present.
    """
    found = []
    for sport, estates, required in SPORT_ESTATES:
        if not required and not _nfl_estate_exists(after):
            continue
        for name, reader in estates:
            foreign = sorted(
                i for i in reader(after)
                if any(i.startswith(p) for p in FOREIGN_PREFIXES[sport])
            )
            if foreign:
                found.append((sport, name, foreign))
    return found


def compare(before, after):
    """Return a list of (estate_name, sorted lost ids). Empty list = clean.

    Signature and MLB behaviour unchanged. NFL estates are compared only once
    they exist; an NFL estate present at `before` and gone at `after` is
    reported as a total loss rather than skipped.
    """
    lost = []
    for sport, estates, required in SPORT_ESTATES:
        if not required:
            existed = _nfl_estate_exists(before)
            exists_now = _nfl_estate_exists(after)
            if not existed and not exists_now:
                # Not established yet. Nothing to lose, so not a failure -- but
                # also never silently treated as verified.
                continue
            if existed and not exists_now:
                raise Unreadable(
                    f"the entire {sport} public estate present at {before} is "
                    f"ABSENT at {after}. An established evidence estate does not "
                    "legitimately disappear."
                )
        for name, reader in estates:
            gone = sorted(reader(before) - reader(after))
            if gone:
                lost.append((name, gone))
    return lost


def compare_per_sport(before, after):
    """Losses grouped by sport: {sport: [(estate_name, lost_ids), ...]}.

    Grouped so an operator reading a failure sees WHICH SPORT regressed. A
    healthy MLB estate must never make an NFL loss look small, which is what
    pooling the two identity sets would do.
    """
    per_sport = {}
    for sport, estates, required in SPORT_ESTATES:
        if not required:
            existed = _nfl_estate_exists(before)
            exists_now = _nfl_estate_exists(after)
            if not existed and not exists_now:
                continue
            if existed and not exists_now:
                raise Unreadable(
                    f"the entire {sport} public estate present at {before} is "
                    f"ABSENT at {after}. An established evidence estate does not "
                    "legitimately disappear."
                )
        for name, reader in estates:
            gone = sorted(reader(before) - reader(after))
            if gone:
                per_sport.setdefault(sport, []).append((name, gone))
    return per_sport


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-4])
        return 2
    before, after = argv[1], argv[2]
    try:
        per_sport = compare_per_sport(before, after)
        crossed = contamination(before, after)
    except Unreadable as exc:
        # Never convert missing evidence into a pass.
        print(f"FAIL  cannot verify the public estate: {exc}")
        return 1
    if not per_sport and not crossed:
        print(f"PASS  no published identity lost between {before} and {after}")
        return 0
    if per_sport:
        print(f"FAIL  published identities disappeared between {before} and {after}")
        # Grouped by sport so a loss in one is never softened by the other
        # looking healthy.
        for sport in sorted(per_sport):
            for name, gone in per_sport[sport]:
                print(f"  [{sport}] {name}: {len(gone)} lost")
                for i in gone:
                    print(f"      {i}")
    if crossed:
        print(f"FAIL  an estate contains another sport's identities at {after}")
        for sport, name, foreign in crossed:
            print(f"  [{sport}] {name}: {len(foreign)} foreign identity(ies)")
            for i in foreign:
                print(f"      {i}")
        print("\nA writer crossed the sport partition. The estates are separate")
        print("files precisely so one sport's tooling cannot rewrite the other's.")
    if per_sport:
        print("\nThis is immutable public evidence. Do not repair it by editing the")
        print("estate: restore the commits that carried it.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

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


ESTATES = (("graded ledger (results/grades_*.json)", graded_identities),
           ("publication registry (%s)" % REGISTRY, registry_identities))


def compare(before, after):
    """Return a list of (estate_name, sorted lost ids). Empty list = clean."""
    lost = []
    for name, reader in ESTATES:
        gone = sorted(reader(before) - reader(after))
        if gone:
            lost.append((name, gone))
    return lost


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-4])
        return 2
    before, after = argv[1], argv[2]
    try:
        lost = compare(before, after)
    except Unreadable as exc:
        # Never convert missing evidence into a pass.
        print(f"FAIL  cannot verify the public estate: {exc}")
        return 1
    if not lost:
        print(f"PASS  no published identity lost between {before} and {after}")
        return 0
    print(f"FAIL  published identities disappeared between {before} and {after}")
    for name, gone in lost:
        print(f"  {name}: {len(gone)} lost")
        for i in gone:
            print(f"      {i}")
    print("\nThis is immutable public evidence. Do not repair it by editing the")
    print("estate: restore the commits that carried it.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

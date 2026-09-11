#!/usr/bin/env python3
"""Refuse to commit an NFL capture that touched anything outside nfl/raw/.

    python3 -m nfl.archive.commit_guard            # check only, exit 1 on violation
    python3 -m nfl.archive.commit_guard --commit -m "..."

WHAT THIS ACTUALLY GUARANTEES, STATED WITHOUT INFLATION. This is a
SOFTWARE GUARD. GitHub Actions `permissions: contents: write` is
REPOSITORY-SCOPED; there is no path-scoped GitHub write permission, and any
claim that a workflow token is confined to a subtree is false. The capture
job's token CAN write results/, data/public_top_picks/, or any MLB file. This
script refusing is the only thing that stops it, and it is defeatable by
editing this file or the workflow that calls it.

WHAT IS SERVER-ENFORCED, for contrast:
  * the `protect-main-and-evidence` ruleset on refs/heads/main blocks force
    pushes and deletions there. That is prevention, and it lives at GitHub.
  * a job issued a token WITHOUT contents: write cannot commit at all.

So the defence in depth is: narrow allowlist (here) + a capture branch that is
not main + main's existing server-side ruleset + review of any change to this
file. Not a permission boundary. Saying otherwise would repeat exactly the
false claim this mission was told to correct.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from nfl.paths import ALLOWED_WRITE_PREFIXES, PathViolation, assert_only_allowed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--commit", action="store_true",
                        help="commit the allowed paths after the check passes")
    parser.add_argument("-m", "--message", default="NFL raw world-state capture")
    args = parser.parse_args(argv[1:])

    try:
        changed = assert_only_allowed()
    except PathViolation as exc:
        print("REFUSED  NFL archival path guard rejected this run.\n")
        print(exc)
        return 1

    if not changed:
        print("nothing changed; no commit needed")
        return 0

    print(f"allowed: {len(changed)} changed path(s) under "
          f"{', '.join(ALLOWED_WRITE_PREFIXES)}")
    if not args.commit:
        return 0

    # Stage ONLY the allowlisted prefixes. `git commit -a` or a bare `git add .`
    # would sweep in anything else present in the tree, which is the mistake
    # this guard exists to make impossible rather than merely unlikely.
    for prefix in ALLOWED_WRITE_PREFIXES:
        subprocess.run(["git", "add", "--", prefix], check=True)

    # Re-check AFTER staging: the guard must describe what is actually about to
    # be committed, not what the tree looked like a moment earlier.
    try:
        assert_only_allowed()
    except PathViolation as exc:
        print("REFUSED  tree changed between check and stage.\n")
        print(exc)
        return 1

    staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                            capture_output=True, text=True, check=True)
    offenders = [p for p in staged.stdout.split()
                 if not any(p.startswith(x) for x in ALLOWED_WRITE_PREFIXES)]
    if offenders:
        print("REFUSED  staged paths outside the allowlist:")
        for path in offenders:
            print(f"  {path}")
        subprocess.run(["git", "reset"], check=False)
        return 1

    subprocess.run(["git", "commit", "-m", args.message], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

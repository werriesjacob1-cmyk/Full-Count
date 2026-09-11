#!/usr/bin/env python3
"""Which repository paths NFL archival is permitted to create or modify.

WHY THIS MODULE EXISTS. The NFL raw-capture job needs a push credential to
commit what it observed. A GitHub Actions token that can write the repository
can write ANY path in it. There is no directory-scoped GitHub write
permission -- `permissions: contents: write` is repository-scoped, full stop.
Any claim that a workflow's token is confined to a subtree is false.

So the confinement here is SOFTWARE-GUARDED, not server-enforced, and this
module is the guard. It is the single place that answers "may NFL archival
touch this path?", so the capture job, its tests, and the commit step all
consult one definition instead of three drifting copies.

WHAT IS ACTUALLY ENFORCED WHERE -- stated plainly because overstating it is
the failure mode this replaces:

  SERVER-ENFORCED (GitHub, outside this repository's control):
    * the `protect-main-and-evidence` ruleset on refs/heads/main blocks force
      pushes and deletions on main. That is prevention.
    * a token issued without `contents: write` cannot commit at all.
  SOFTWARE-GUARDED (this module, defeatable by editing this module):
    * the allowed-path allowlist below.
    * the capture job's refusal to commit when any staged path falls outside
      it, including a path it did not itself create.

A guard that lives in the repository cannot police its own removal. That gap
is real and is not engineered around. It is bounded by review of changes to
this file, not by a runtime check.
"""
from __future__ import annotations

import os
import subprocess

# Every path NFL archival may create or modify, as repo-relative prefixes.
# Deliberately NARROW: raw evidence and the coverage manifests that describe
# it. Not nfl/ as a whole -- source code changes belong in a reviewed commit
# by a person, never in an automated capture push.
ALLOWED_WRITE_PREFIXES = (
    "nfl/raw/",
)

# Paths that hold MLB public evidence. Named explicitly and checked separately
# from the allowlist so that a violation touching them is reported as what it
# is -- an attempt to modify the immutable MLB estate -- rather than as a
# generic out-of-scope path. These are the estates ledger_integrity.py exists
# to protect.
MLB_EVIDENCE_PREFIXES = (
    "data/public_top_picks/",
    "results/",
    "output/",
    "data/",
    "docs/",
)


class PathViolation(Exception):
    """A staged path is outside what NFL archival is permitted to write."""


def is_allowed(path: str) -> bool:
    """True if `path` (repo-relative, forward slashes) may be written."""
    normalized = path.replace(os.sep, "/").lstrip("./")
    return any(normalized.startswith(p) for p in ALLOWED_WRITE_PREFIXES)


def classify(path: str) -> str:
    """Describe a disallowed path for an operator reading a failed run."""
    normalized = path.replace(os.sep, "/").lstrip("./")
    for prefix in MLB_EVIDENCE_PREFIXES:
        if normalized.startswith(prefix):
            return f"MLB PUBLIC EVIDENCE ({prefix})"
    return "outside NFL archival scope"


def staged_paths(repo_root: str = ".") -> list[str]:
    """Repo-relative paths git currently reports as changed, staged or not.

    Includes untracked files. A capture run that wrote somewhere unexpected
    must be caught whether or not it also remembered to `git add` it.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=repo_root, capture_output=True, text=True,
        )
    except OSError as exc:
        # A missing working directory, or no git binary, means the changed set
        # cannot be enumerated. Raised as PathViolation rather than left as an
        # OSError so that callers guarding on PathViolation fail CLOSED instead
        # of dying with a traceback that reads like a crash rather than a
        # refusal. Found by test_unreadable_repo_fails_closed, which failed
        # with FileNotFoundError before this existed.
        raise PathViolation(
            f"cannot enumerate changed paths under {repo_root!r}: {exc}. "
            "No enumeration means no verdict; failing closed."
        ) from exc
    if result.returncode != 0:
        # Cannot enumerate what changed, so cannot certify the run is clean.
        # Missing evidence is never a pass.
        raise PathViolation(
            f"cannot list changed paths (git status exited {result.returncode}): "
            f"{result.stderr.strip()}"
        )
    paths = []
    for record in result.stdout.split("\0"):
        if len(record) < 4:
            continue
        status, path = record[:2], record[3:]
        # Rename/copy records carry `to\0from`; the -z split already separated
        # them, and the destination is what a guard cares about.
        if status[0] in ("R", "C"):
            paths.append(path)
        else:
            paths.append(path)
    return [p for p in paths if p]


def assert_only_allowed(repo_root: str = ".") -> list[str]:
    """Raise PathViolation unless every changed path is inside the allowlist.

    Returns the allowed changed paths on success so a caller can log exactly
    what it is about to commit.
    """
    changed = staged_paths(repo_root)
    violations = [p for p in changed if not is_allowed(p)]
    if violations:
        lines = [
            "NFL archival attempted to modify paths it is not permitted to write.",
            "",
        ]
        for path in sorted(violations):
            lines.append(f"  REFUSED  {path}    [{classify(path)}]")
        lines += [
            "",
            "Permitted prefixes:",
        ]
        lines += [f"  {p}" for p in ALLOWED_WRITE_PREFIXES]
        lines += [
            "",
            "No commit was made. This guard is SOFTWARE-GUARDED, not a GitHub",
            "permission: the token could have written these paths. The refusal",
            "is the only thing that stopped it.",
        ]
        raise PathViolation("\n".join(lines))
    return sorted(changed)

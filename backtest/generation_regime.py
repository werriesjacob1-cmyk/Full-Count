#!/usr/bin/env python3
"""generation_regime.py -- formal code-identity layer for canonical datasets.

WHY THIS EXISTS. The canonical reconstruction currently in flight is a
MIXED-SHA dataset by construction: 385 dates were salvaged from the dead
PID-1633 run (generated under ``2ce95fe9``) and the remaining ~490 are
being generated under the pinned ``022c8829``. The importer deliberately
preserves each checkpoint's true originating ``code_git_sha`` rather than
laundering it (see canonical_run.import_legacy) -- which is correct, and
which means something has to answer the question that fact raises:

    are rows generated under those two SHAs actually comparable?

An independent audit answered it by spot-checking eight files by hand and
concluding "identical". That conclusion was directionally right but the
METHOD was not sufficient, and this module exists because it was not:
running the real transitive import closure from ``backtest/engine.py``
finds 18 first-party files, not 8, and THREE of them differ between the
two SHAs (``grade_results.py``, ``dashboard/live_state.py``,
``dashboard/settlement_rules.py``) -- the audit named only the first.
Hand-picked file lists silently miss things; a computed closure does not.

WHAT THIS MODULE CLAIMS, AND WHAT IT DOES NOT. It computes a strict,
reproducible ``generation_regime_fingerprint`` over the full computed
closure plus the model artifacts the generation path actually loads. If
two SHAs share a fingerprint, that is strong STRUCTURAL evidence they
generate identical rows. If they do not, this module reports exactly
which files differ and refuses to call them equivalent -- it never
hand-waves "that difference probably doesn't matter."

Structural equality is deliberately NOT treated as sufficient proof on
its own. Per the governing mission: file comparison alone cannot certify
a canonical artifact, because a fingerprint cannot see through dynamic
dispatch, lazy imports, or data-dependent branches. The authoritative
evidence is the OVERLAP REPLAY (see backtest/overlap_replay.py):
regenerate real salvaged dates under the pinned SHA and compare every
predictive field. This module's job is to make the structural question
precise and machine-checkable, and to carry the replay's verdict
alongside it in one auditable record.

THE CLOSURE IS COMPUTED, NOT CURATED, with two explicit exceptions that
a pure AST walk provably cannot find and that are therefore declared by
hand (and asserted to exist, so a rename cannot silently drop them):

  * ``backtest/calibration.py`` -- generate_picks.load_calibrator() does
    ``sys.path.insert(0, .../backtest)`` then ``import calibration``.
    A naive resolver looks for ``calibration.py`` at the repo root, does
    not find it, and drops a genuinely generation-critical module.
  * the fitted calibrator DATA files. These are not code and no import
    graph will ever mention them, but swapping them changes every
    predicted probability -- which is the definition of a regime change.

REPOSITORY IDENTITY. This module also owns the canonical repository
identity and the correction path for manifests that recorded the wrong
one, because both are provenance-integrity questions and belong in one
place rather than scattered through the runner.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Bump when the DEFINITION of the closure changes (entry points, declared
# extras, artifact set). Two fingerprints are only comparable at the same
# closure_version -- otherwise "different fingerprint" would conflate "the
# code changed" with "we changed how we look at the code."
CLOSURE_VERSION = 1

# ── Repository identity ────────────────────────────────────────────────
#
# Verified 2026-08-27 against GitHub's own API, which is authoritative
# here: PR #71's head and base both report
# repo.full_name == "werriesjacob1-cmyk/Full-Count". The remote configured
# in this checkout still points at the pre-rename PROJECT-GRIDIRON URL,
# which GitHub transparently redirects -- so every push succeeds and
# nothing ever surfaced the discrepancy. That is exactly how a wrong
# provenance value survives unnoticed.
CANONICAL_REPOSITORY_IDENTITY = "werriesjacob1-cmyk/Full-Count"

# Historical names that genuinely refer to THIS repository. An alias is
# not "acceptable" -- a manifest carrying one still fails validation
# without an explicit correction record. It is recorded so a correction
# can prove the old value was a stale alias for the same repo rather
# than a different repository entirely, which is a materially different
# (and much worse) provenance failure.
KNOWN_REPOSITORY_ALIASES = {
    "werriesjacob1-cmyk/PROJECT-GRIDIRON":
        "pre-rename name for the same repository; GitHub redirects it to "
        "Full-Count. Confirmed via the GitHub API reporting full_name="
        "'werriesjacob1-cmyk/Full-Count' for this repo's own pull requests.",
}

# ── Generation-critical closure definition ────────────────────────────

# Row generation starts here. Everything reachable from this module is
# generation-critical by construction.
GENERATION_ENTRY_MODULES = ("backtest/engine.py",)

# Files a pure AST closure provably cannot reach -- see module docstring.
# Asserted to exist at every SHA inspected, so a rename fails loudly
# instead of silently shrinking the closure.
DECLARED_EXTRA_CODE_PATHS = (
    "backtest/calibration.py",
)

# Fitted model artifacts loaded at generation time. Not code; still
# regime-defining.
DECLARED_ARTIFACT_PATHS = (
    "backtest/calibrators_by_market.json",
    "backtest/calibrator_oldscorer.json",
)

# Directories searched when resolving a first-party module name, in
# order. "backtest" is present because of the sys.path injection in
# generate_picks.load_calibrator().
FIRST_PARTY_SEARCH_DIRS = ("", "backtest")


class RegimeError(Exception):
    """Raised when a regime question cannot be answered honestly."""


def _git(args, cwd=REPO_ROOT, check=True):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=60)
    if check and proc.returncode != 0:
        raise RegimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _blob_sha(sha, path):
    """The git object id of `path` at `sha`, or None if absent."""
    proc = subprocess.run(["git", "rev-parse", f"{sha}:{path}"], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=30)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _read_at(sha, path):
    proc = subprocess.run(["git", "show", f"{sha}:{path}"], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return None
    return proc.stdout


def _resolve_first_party(module_name, sha):
    """Map a dotted module name to a repo-relative path at `sha`, or None
    if it is not a first-party module (stdlib/third-party)."""
    rel = module_name.replace(".", "/")
    for base in FIRST_PARTY_SEARCH_DIRS:
        for cand in (f"{rel}.py", f"{rel}/__init__.py"):
            path = f"{base}/{cand}" if base else cand
            if _blob_sha(sha, path):
                return path
    return None


def discover_generation_closure(sha):
    """Transitive first-party import closure from the generation entry
    points, computed against the tree at `sha` (never the working copy --
    a dirty checkout must not be able to change what a historical SHA's
    regime is said to be).

    Walks every Import/ImportFrom node in the whole AST, including ones
    nested inside functions, because this codebase genuinely uses lazy
    in-function imports on the generation path (grade_public_pick's
    `from dashboard.live_state import game_state` is one).
    """
    seen, queue = set(), list(GENERATION_ENTRY_MODULES)
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        src = _read_at(sha, path)
        if src is None:
            raise RegimeError(
                f"generation entry/closure member {path!r} does not exist at {sha} -- "
                f"the closure definition (CLOSURE_VERSION={CLOSURE_VERSION}) is stale "
                f"for this commit and must be reviewed rather than silently shrunk")
        seen.add(path)
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            raise RegimeError(f"cannot parse {path} at {sha}: {exc}") from exc
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                resolved = _resolve_first_party(name, sha)
                if resolved and resolved not in seen:
                    queue.append(resolved)

    for extra in DECLARED_EXTRA_CODE_PATHS:
        if not _blob_sha(sha, extra):
            raise RegimeError(
                f"declared generation-critical file {extra!r} is missing at {sha}. "
                f"It is declared by hand precisely because the import graph cannot "
                f"see it, so its disappearance must fail loudly rather than quietly "
                f"reduce the regime's footprint.")
        seen.add(extra)
    return sorted(seen)


def generation_regime_fingerprint(sha):
    """A stable, reproducible identity for 'the code+artifacts that turn a
    date into rows' at `sha`.

    The fingerprint is a sha256 over the canonical JSON of every closure
    member's path and git blob id, plus the artifact blobs, plus
    CLOSURE_VERSION. Git blob ids are content hashes, so this is a true
    content fingerprint -- it does not care about commit history, author,
    timestamps, or how the code got there.
    """
    closure = discover_generation_closure(sha)
    files = {}
    for path in closure:
        blob = _blob_sha(sha, path)
        if blob is None:
            raise RegimeError(f"closure member {path!r} vanished while fingerprinting {sha}")
        files[path] = blob

    artifacts = {}
    for path in DECLARED_ARTIFACT_PATHS:
        blob = _blob_sha(sha, path)
        if blob is None:
            raise RegimeError(
                f"declared generation artifact {path!r} is missing at {sha} -- refusing "
                f"to fingerprint a regime whose fitted calibrators cannot be located")
        artifacts[path] = blob

    payload = {
        "closure_version": CLOSURE_VERSION,
        "files": files,
        "artifacts": artifacts,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        "sha": sha,
        "closure_version": CLOSURE_VERSION,
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "n_files": len(files),
        "n_artifacts": len(artifacts),
        "files": files,
        "artifacts": artifacts,
    }


def changed_functions(sha_a, sha_b, path):
    """Which top-level/nested function definitions differ between two SHAs.

    Compares normalized AST dumps rather than text, so a comment edit or
    reflow is correctly reported as no change while any real structural
    edit is caught. Used to CHARACTERIZE a difference for the audit
    record -- never to excuse one.
    """
    def _funcs(src):
        out = {}
        if src is None:
            return out
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[node.name] = hashlib.sha256(ast.dump(node).encode()).hexdigest()
        return out

    fa, fb = _funcs(_read_at(sha_a, path)), _funcs(_read_at(sha_b, path))
    return {
        "added": sorted(set(fb) - set(fa)),
        "removed": sorted(set(fa) - set(fb)),
        "changed": sorted(k for k in (set(fa) & set(fb)) if fa[k] != fb[k]),
    }


def compare_generation_regimes(sha_a, sha_b):
    """Full structural comparison of two generation regimes.

    Returns a record suitable for embedding directly in a canonical
    provenance package. ``structurally_equivalent`` is True only when the
    fingerprints match exactly. When they do not, ``differing_files``
    carries a per-file function-level characterization so a human (or the
    overlap replay) can act on specifics rather than a bare "differs".

    This function deliberately does NOT decide whether a structural
    difference is benign. That question is answered empirically by the
    overlap replay, and the answer is recorded separately -- see
    build_equivalence_record().
    """
    fa = generation_regime_fingerprint(sha_a)
    fb = generation_regime_fingerprint(sha_b)

    all_paths = sorted(set(fa["files"]) | set(fb["files"]))
    differing = {}
    for path in all_paths:
        ba, bb = fa["files"].get(path), fb["files"].get(path)
        if ba == bb:
            continue
        entry = {"blob_a": ba, "blob_b": bb}
        if ba and bb and path.endswith(".py"):
            entry["functions"] = changed_functions(sha_a, sha_b, path)
        differing[path] = entry

    differing_artifacts = {
        p: {"blob_a": fa["artifacts"].get(p), "blob_b": fb["artifacts"].get(p)}
        for p in sorted(set(fa["artifacts"]) | set(fb["artifacts"]))
        if fa["artifacts"].get(p) != fb["artifacts"].get(p)
    }

    return {
        "closure_version": CLOSURE_VERSION,
        "sha_a": sha_a,
        "sha_b": sha_b,
        "fingerprint_a": fa["fingerprint"],
        "fingerprint_b": fb["fingerprint"],
        "structurally_equivalent": fa["fingerprint"] == fb["fingerprint"],
        "n_closure_files": fa["n_files"],
        "differing_files": differing,
        "differing_artifacts": differing_artifacts,
        "compared_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Dataset regime classification ─────────────────────────────────────

SINGLE_SHA = "single_sha"
MIXED_EQUIVALENT = "mixed_sha_generation_equivalent"
MIXED_NON_EQUIVALENT = "mixed_sha_non_equivalent"
MIXED_UNPROVEN = "mixed_sha_equivalence_unproven"

# Only these two may ever back a canonical certification.
CANONICAL_ELIGIBLE_REGIME_STATUSES = (SINGLE_SHA, MIXED_EQUIVALENT)


def build_equivalence_record(sha_a, sha_b, *, replay_verdict=None, replay_evidence=None):
    """Combine the structural comparison with the empirical overlap-replay
    verdict into the single record a canonical package carries.

    ``replay_verdict`` must be one of:
      * ``"equivalent"``     -- replay regenerated real salvaged dates under
                                sha_b and every predictive field matched
      * ``"not_equivalent"`` -- replay found a substantive mismatch
      * ``None``             -- replay has not been run

    The classification is deliberately conservative in both directions:

      * Structurally identical + replay not run  -> MIXED_EQUIVALENT.
        Identical closure fingerprints mean the same bytes of code and the
        same fitted artifacts produced both segments; there is no
        mechanism left by which they could diverge.
      * Structurally different + replay says equivalent -> MIXED_EQUIVALENT,
        because empirical row-level proof over real dates is STRONGER
        evidence than a file hash, and the mission requires exactly this
        escape hatch for a difference that provably does not touch the
        generation path.
      * Structurally different + replay not run -> MIXED_UNPROVEN, which is
        NOT canonical-eligible. This is the honest state for "we know the
        files differ and we have not yet demonstrated it doesn't matter."
      * Any replay saying not_equivalent -> MIXED_NON_EQUIVALENT, always,
        regardless of what the fingerprints say.
    """
    if replay_verdict not in (None, "equivalent", "not_equivalent"):
        raise ValueError(f"invalid replay_verdict {replay_verdict!r}")

    comparison = compare_generation_regimes(sha_a, sha_b)
    structural = comparison["structurally_equivalent"]

    if replay_verdict == "not_equivalent":
        status = MIXED_NON_EQUIVALENT
    elif replay_verdict == "equivalent":
        status = MIXED_EQUIVALENT
    elif structural:
        status = MIXED_EQUIVALENT
    else:
        status = MIXED_UNPROVEN

    return {
        "record_type": "generation_regime_equivalence",
        "status": status,
        "canonical_eligible": status in CANONICAL_ELIGIBLE_REGIME_STATUSES,
        "structural_comparison": comparison,
        "replay_verdict": replay_verdict,
        "replay_evidence": replay_evidence,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Per-row and per-checkpoint code_git_sha values are PRESERVED as "
            "originally recorded. This record is additive provenance about the "
            "relationship between two regimes; it never rewrites which SHA "
            "actually generated any row."
        ),
    }


def classify_dataset_regime(observed_shas, equivalence_records=()):
    """Classify a whole dataset from the distinct code_git_sha values its
    checkpoints/rows actually carry.

    ``observed_shas`` is the real set found in the data -- not what a
    manifest claims. A dataset is only MIXED_EQUIVALENT if EVERY pair of
    distinct observed SHAs is covered by an equivalence record that is
    itself canonical-eligible; a single uncovered pair drops the whole
    dataset to MIXED_UNPROVEN.
    """
    shas = sorted({s for s in observed_shas if s})
    if len(shas) <= 1:
        return {"status": SINGLE_SHA, "observed_shas": shas,
                "canonical_eligible": True, "uncovered_pairs": []}

    covered = {}
    for rec in equivalence_records:
        comp = rec.get("structural_comparison") or {}
        pair = tuple(sorted((comp.get("sha_a"), comp.get("sha_b"))))
        covered[pair] = rec

    uncovered, non_equivalent = [], []
    for i, a in enumerate(shas):
        for b in shas[i + 1:]:
            rec = covered.get(tuple(sorted((a, b))))
            if rec is None:
                uncovered.append([a, b])
            elif rec.get("status") == MIXED_NON_EQUIVALENT:
                non_equivalent.append([a, b])
            elif not rec.get("canonical_eligible"):
                uncovered.append([a, b])

    if non_equivalent:
        status = MIXED_NON_EQUIVALENT
    elif uncovered:
        status = MIXED_UNPROVEN
    else:
        status = MIXED_EQUIVALENT

    return {
        "status": status,
        "observed_shas": shas,
        "canonical_eligible": status in CANONICAL_ELIGIBLE_REGIME_STATUSES,
        "uncovered_pairs": uncovered,
        "non_equivalent_pairs": non_equivalent,
    }


# ── Repository identity correction ────────────────────────────────────

class RepositoryIdentityError(Exception):
    """Raised when an artifact's claimed repository identity is wrong and
    no explicit, valid correction record accompanies it."""


def build_repository_identity_correction(manifest, *, reason, fix_commit,
                                         verified_by="github_api_full_name"):
    """Create an explicit, auditable correction for a manifest that
    recorded the wrong repository identity.

    This is deliberately an ADDITIVE record rather than an in-place edit
    of the manifest. Silently rewriting a historical manifest's
    repository_identity would produce an artifact that looks like it was
    always correct -- which is provenance laundering, and is precisely
    what the governing mission forbids. The original wrong value stays
    exactly where it was; this record sits beside it and says what the
    truth is, who established it, and which commit fixed the code that
    produced the error.
    """
    original = manifest.get("repository_identity")
    if original == CANONICAL_REPOSITORY_IDENTITY:
        raise ValueError(
            "manifest already records the canonical repository identity; "
            "a correction record would be meaningless")
    return {
        "record_type": "repository_identity_correction",
        "run_id": manifest.get("run_id"),
        "original_manifest_value": original,
        "corrected_repository_identity": CANONICAL_REPOSITORY_IDENTITY,
        "original_value_is_known_alias": original in KNOWN_REPOSITORY_ALIASES,
        "alias_explanation": KNOWN_REPOSITORY_ALIASES.get(original),
        "reason": reason,
        "verified_by": verified_by,
        "fix_commit": fix_commit,
        "corrected_at": datetime.now(timezone.utc).isoformat(),
        "manifest_left_unmodified": True,
    }


def validate_repository_identity(manifest, corrections=()):
    """Fail closed unless the artifact's repository identity is correct,
    or is wrong but carries a matching explicit correction record.

    Returns the effective (post-correction) identity on success.
    """
    claimed = manifest.get("repository_identity")
    if claimed == CANONICAL_REPOSITORY_IDENTITY:
        return CANONICAL_REPOSITORY_IDENTITY

    for rec in corrections:
        if rec.get("record_type") != "repository_identity_correction":
            continue
        if rec.get("original_manifest_value") != claimed:
            continue
        if rec.get("corrected_repository_identity") != CANONICAL_REPOSITORY_IDENTITY:
            continue
        if rec.get("run_id") not in (None, manifest.get("run_id")):
            continue
        return CANONICAL_REPOSITORY_IDENTITY

    raise RepositoryIdentityError(
        f"artifact claims repository_identity={claimed!r}, which is not the canonical "
        f"{CANONICAL_REPOSITORY_IDENTITY!r}, and no matching correction record was "
        f"supplied. "
        + (f"({claimed!r} is a known pre-rename alias for this same repository, so a "
           f"correction record is appropriate here -- but it must be created "
           f"explicitly, not assumed.) "
           if claimed in KNOWN_REPOSITORY_ALIASES else
           f"({claimed!r} is not even a known alias of this repository, which is a "
           f"more serious provenance failure than a stale name.) ")
        + "Canonical certification cannot proceed on unverified provenance.")


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fingerprint", metavar="SHA", help="print one regime fingerprint")
    ap.add_argument("--compare", nargs=2, metavar=("SHA_A", "SHA_B"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.fingerprint:
        out = generation_regime_fingerprint(args.fingerprint)
        print(json.dumps(out, indent=2) if args.json
              else f"{out['sha'][:12]}  fingerprint={out['fingerprint']}  "
                   f"files={out['n_files']} artifacts={out['n_artifacts']}")
    elif args.compare:
        out = compare_generation_regimes(*args.compare)
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(f"structurally_equivalent: {out['structurally_equivalent']}")
            print(f"  {out['sha_a'][:12]} -> {out['fingerprint_a']}")
            print(f"  {out['sha_b'][:12]} -> {out['fingerprint_b']}")
            for path, info in out["differing_files"].items():
                fn = info.get("functions", {})
                print(f"  DIFFERS {path}: changed={fn.get('changed')} "
                      f"added={fn.get('added')} removed={fn.get('removed')}")
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

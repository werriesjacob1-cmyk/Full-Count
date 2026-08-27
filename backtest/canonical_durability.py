"""Remote durability and provenance for canonical backtest runs.

WHY THIS EXISTS
===============
On 2026-08-27 an idle container was reclaimed. The filesystem and the entire
git object store went with it. A canonical run that had completed 421 date
checkpoints -- 299 ok, 122 no_games, 0 errors, through 2025-05-27 -- was
destroyed, and roughly five hours of generation had to be thrown away.

The run had a remote push mechanism. It pushed only `manifest.json` and the
per-date `.meta.json` ledger, explicitly excluding the bulk `.jsonl` row data
to keep git history small. So the *provenance* survived on
`canonical-run-manifests` and the *data* did not. We knew exactly which dates
had completed, and had none of their rows.

This module closes that gap. The design constraint is unchanged -- do not dump
hundreds of megabytes of uncompressed rows into ordinary git history -- but the
conclusion is different: per-date rows are pushed, gzipped, as write-once blobs.

Measured on real canonical output, not estimated: 2024-04-01 produced 1537 rows,
1.22 MB raw, 67 KB gzipped (18x compression); 2024-04-02 produced 1436 rows,
1.17 MB raw, 66 KB gzipped. At ~66 KB per played date the full
2024-04-01..2026-08-25 range (~600 dates with games) comes to roughly 40 MB
across the entire branch history. Each date's blob is written exactly once and
never rewritten, so growth is additive in dates rather than quadratic in
pushes x dates.

THE RECOVERY CONTRACT
=====================
A fresh container, with nothing but a clone of the repository, must be able to:

  1. discover the latest durable checkpoint for a run id,
  2. verify it belongs to the same run contract (identity),
  3. verify its integrity (per-date checksums, recomputed),
  4. reconstruct local continuation state, and
  5. resume only the missing dates.

Any identity or integrity mismatch FAILS CLOSED. A checkpoint that cannot be
proven compatible is never silently reused -- reusing an incompatible
checkpoint would silently blend two generation regimes into one artifact, which
is the single worst outcome available here.

WHAT THIS MODULE DOES NOT DO
============================
It does not change probability, scoring, calibration, threshold, selector,
market, settlement, or grading behavior. It is provenance and durability only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The dedicated durable channel. Deliberately NOT `canonical-run-manifests`:
# that branch holds the dead 2026-08-27 run's metadata ledger, and mixing a new
# run's checkpoints into it would force a reconciliation between a manifest
# describing 421 completed dates and row data that no longer exists for any of
# them. That branch is preserved, untouched, as the historical record.
DURABLE_BRANCH = "canonical-durable-checkpoints"

DURABILITY_SCHEMA_VERSION = 1

# Packages whose version genuinely changes canonical output. A full `pip freeze`
# hash is also recorded, but pinning these by name makes a mismatch readable
# instead of "some hash differs".
CRITICAL_PACKAGES = (
    "pybaseball", "pandas", "numpy", "requests", "scipy",
    "scikit-learn", "pyarrow", "python-dateutil", "pytz",
)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ══════════════════════════════════════════════════════════════════════════
#  ENVIRONMENT IDENTITY
# ══════════════════════════════════════════════════════════════════════════

def environment_identity(*, include_full_freeze_hash=True):
    """The interpreter and package environment a run actually executed in.

    The same git SHA is NOT the same scientific environment. A pybaseball or
    pandas upgrade between two runs of byte-identical code can change the rows
    those runs produce, and without this record the two datasets look
    interchangeable when they are not. `generation_regime.py` fingerprints the
    *code*; this fingerprints everything the code ran on top of.
    """
    versions = {}
    try:
        from importlib import metadata as _md
    except ImportError:                                    # pragma: no cover
        _md = None
    if _md is not None:
        for name in CRITICAL_PACKAGES:
            try:
                versions[name] = _md.version(name)
            except Exception:
                versions[name] = None

    freeze_hash = None
    freeze_count = None
    if include_full_freeze_hash and _md is not None:
        try:
            dists = sorted(
                f"{d.metadata['Name']}=={d.version}"
                for d in _md.distributions()
                if d.metadata and d.metadata.get("Name")
            )
            freeze_count = len(dists)
            freeze_hash = _sha256_bytes("\n".join(dists).encode())
        except Exception:
            pass

    ident = {
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "critical_packages": versions,
        "pip_freeze_sha256": freeze_hash,
        "pip_freeze_package_count": freeze_count,
        "recorded_at": _now_iso(),
    }
    ident["environment_fingerprint"] = _sha256_bytes(
        json.dumps(
            {k: ident[k] for k in
             ("python_version", "python_implementation", "platform",
              "machine", "critical_packages", "pip_freeze_sha256")},
            sort_keys=True,
        ).encode()
    )
    return ident


def compare_environments(a, b):
    """Structured comparison. Returns compatible//differences, never a bare bool.

    A differing environment is not automatically disqualifying -- it is
    something a human or the certifier must rule on -- but it must never be
    invisible.
    """
    if not a or not b:
        return {"comparable": False, "reason": "one or both environments not recorded",
                "compatible": False, "differences": []}
    diffs = []
    for key in ("python_version", "python_implementation", "platform", "machine"):
        if a.get(key) != b.get(key):
            diffs.append({"field": key, "a": a.get(key), "b": b.get(key)})
    pa, pb = a.get("critical_packages") or {}, b.get("critical_packages") or {}
    for name in sorted(set(pa) | set(pb)):
        if pa.get(name) != pb.get(name):
            diffs.append({"field": f"package:{name}", "a": pa.get(name), "b": pb.get(name)})
    if a.get("pip_freeze_sha256") != b.get("pip_freeze_sha256"):
        diffs.append({"field": "pip_freeze_sha256",
                      "a": a.get("pip_freeze_sha256"), "b": b.get("pip_freeze_sha256")})
    return {
        "comparable": True,
        "compatible": not diffs,
        "identical_fingerprint": a.get("environment_fingerprint") == b.get("environment_fingerprint"),
        "differences": diffs,
    }


# ══════════════════════════════════════════════════════════════════════════
#  SOURCE LINEAGE
# ══════════════════════════════════════════════════════════════════════════
#
# The manifest previously recorded `source_provider: "mlb_statsapi"` -- a bare
# string covering several genuinely different upstream systems with different
# revision behavior. Statcast in particular is revised retroactively: the same
# request for the same historical window can return different values weeks
# apart, which is exactly what the 2026-08-26 six-row platoon_xwoba mismatch
# turned out to be. A single string cannot express that, so a mismatch traced
# to it is unfalsifiable.

def source_lineage_record(name, *, request_identity, retrieval_timestamp=None,
                          library=None, library_version=None, row_count=None,
                          schema_columns=None, content_sha256=None,
                          date_coverage=None, cache_mode=None, notes=None):
    """One structured provenance record for one canonical input.

    `request_identity` is what was actually asked for (endpoint plus window, or
    the Statcast start/end pair), not a human label. Two records with the same
    request_identity and different content_sha256 are the signature of an
    upstream revision, and that is the whole point of recording it.
    """
    rec = {
        "source": name,
        "request_identity": request_identity,
        "retrieval_timestamp": retrieval_timestamp or _now_iso(),
        "library": library,
        "library_version": library_version,
        "row_count": row_count,
        "schema_columns": sorted(schema_columns) if schema_columns else None,
        "schema_fingerprint": (
            _sha256_bytes(",".join(sorted(schema_columns)).encode()) if schema_columns else None
        ),
        "content_sha256": content_sha256,
        "date_coverage": date_coverage,
        "cache_mode": cache_mode,
        "notes": notes,
    }
    return rec


def lineage_fingerprint(records):
    """Order-independent fingerprint over a set of lineage records."""
    keyed = sorted(
        json.dumps({k: r.get(k) for k in
                    ("source", "request_identity", "content_sha256",
                     "schema_fingerprint", "row_count")},
                   sort_keys=True)
        for r in records
    )
    return _sha256_bytes("\n".join(keyed).encode())


# ══════════════════════════════════════════════════════════════════════════
#  STATCAST CACHE INTEGRITY
# ══════════════════════════════════════════════════════════════════════════

class CacheIntegrityError(Exception):
    """A cached source chunk could not be proven usable for canonical work."""


# Columns canonical scoring genuinely depends on. Absence is disqualifying, not
# a warning: a missing launch_speed silently turns every moonshot rate into a
# degraded estimate that still looks like a number.
REQUIRED_STATCAST_COLUMNS = (
    "game_date", "player_name", "batter", "pitcher", "events",
    "launch_speed", "launch_angle", "estimated_woba_using_speedangle",
    "p_throws", "stand", "hit_distance_sc",
)


def validate_statcast_cache(path, *, expected_start=None, expected_end=None,
                            required_columns=REQUIRED_STATCAST_COLUMNS,
                            max_age_days=None, strict=True):
    """Validate a cached Statcast parquet on CONTENT, not on its filename.

    The weakness this replaces: a cache was accepted because its filename
    covered the requested date range. A filename is a claim by whoever wrote
    the file. It says nothing about whether the parquet is complete, whether it
    has the columns canonical scoring needs, whether it was truncated by a
    process that died mid-write, or how old the retrieval is.

    Returns a report dict. With strict=True (the canonical default) an
    unusable cache raises CacheIntegrityError so the caller repulls or aborts,
    rather than silently generating a season of degraded rows.
    """
    report = {
        "path": path, "usable": False, "checked_at": _now_iso(),
        "problems": [], "row_count": None, "columns": None,
        "content_sha256": None, "schema_fingerprint": None,
        "min_date": None, "max_date": None, "retrieval_timestamp": None,
    }

    if not os.path.exists(path):
        report["problems"].append("file does not exist")
        if strict:
            raise CacheIntegrityError(f"{path}: file does not exist")
        return report

    report["content_sha256"] = _sha256_file(path)
    try:
        mtime = os.path.getmtime(path)
        report["retrieval_timestamp"] = datetime.fromtimestamp(mtime, timezone.utc).isoformat()
        if max_age_days is not None and (time.time() - mtime) > max_age_days * 86400:
            report["problems"].append(
                f"cache is older than {max_age_days}d (retrieved {report['retrieval_timestamp']})")
    except OSError:
        report["problems"].append("could not stat file for retrieval timestamp")

    try:
        import pandas as pd
        df = pd.read_parquet(path)
    except Exception as exc:
        report["problems"].append(f"parquet unreadable: {exc}")
        if strict:
            raise CacheIntegrityError(f"{path}: parquet unreadable: {exc}")
        return report

    report["row_count"] = int(len(df))
    cols = list(df.columns)
    report["columns"] = sorted(cols)
    report["schema_fingerprint"] = _sha256_bytes(",".join(sorted(cols)).encode())

    missing = [c for c in required_columns if c not in cols]
    if missing:
        report["problems"].append(f"missing required column(s): {', '.join(missing)}")

    if report["row_count"] == 0:
        report["problems"].append("cache has zero rows")

    if "game_date" in cols and report["row_count"]:
        try:
            gd = pd.to_datetime(df["game_date"], errors="coerce").dropna()
            if len(gd):
                report["min_date"] = str(gd.min().date())
                report["max_date"] = str(gd.max().date())
                if expected_start and report["min_date"] > expected_start:
                    report["problems"].append(
                        f"coverage starts {report['min_date']}, after requested {expected_start}")
                if expected_end and report["max_date"] < expected_end:
                    report["problems"].append(
                        f"coverage ends {report['max_date']}, before requested {expected_end}")
            else:
                report["problems"].append("game_date column has no parseable values")
        except Exception as exc:
            report["problems"].append(f"could not evaluate game_date coverage: {exc}")

    report["usable"] = not report["problems"]
    if strict and not report["usable"]:
        raise CacheIntegrityError(
            f"{path}: unusable for canonical work -- " + "; ".join(report["problems"]))
    return report


# pybaseball keeps its own on-disk cache whose state is invisible to the run.
# Canonical mode must SAY which it used, because "fresh" and "frozen" produce
# different source vintages from identical code, and an unlabelled run cannot
# be compared to either.
CACHE_MODE_FRESH = "fresh_source"
CACHE_MODE_FROZEN = "frozen_cache"


def declare_cache_mode(mode, *, cache_dir=None):
    if mode not in (CACHE_MODE_FRESH, CACHE_MODE_FROZEN):
        raise ValueError(
            f"cache_mode must be {CACHE_MODE_FRESH!r} or {CACHE_MODE_FROZEN!r}, got {mode!r} -- "
            "canonical generation may not leave the source vintage implicit")
    rec = {"cache_mode": mode, "declared_at": _now_iso(), "cache_dir": cache_dir,
           "cache_present": bool(cache_dir and os.path.isdir(cache_dir))}
    if rec["cache_present"]:
        try:
            files = sorted(
                os.path.join(dp, f)
                for dp, _, fns in os.walk(cache_dir) for f in fns
            )
            rec["cached_file_count"] = len(files)
            rec["cache_inventory_sha256"] = _sha256_bytes(
                "\n".join(f"{os.path.relpath(p, cache_dir)}:{os.path.getsize(p)}"
                          for p in files).encode())
        except OSError:
            pass
    return rec


# ══════════════════════════════════════════════════════════════════════════
#  DURABILITY POLICY
# ══════════════════════════════════════════════════════════════════════════

class DurabilityPolicy:
    """When to spend a push. Bounded loss, not zero loss.

    Pushing after every date would be correct and wasteful: a canonical date
    takes tens of seconds, so per-date pushes would add a network round trip
    and a git commit to each one, and produce ~880 commits for one run.

    The chosen bound is `every_n_dates=10` OR `every_seconds=900`, whichever
    comes first, plus a final push at the end of every invocation.

    MAXIMUM WORK LOST TO A CONTAINER DEATH: 10 dates, or 15 minutes of
    generation, whichever comes first.

    A correction, because the first version of this docstring reasoned from a
    bad number. It claimed the 2026-08-27 run generated 421 dates in ~62
    minutes (~8.8s/date), and concluded the ten-date rule would fire every ~90
    seconds. That rate was not a generation rate at all: those checkpoints
    carry `extra.imported_from = "legacy_rows_backfill_v2"`, meaning they were
    SALVAGED from an earlier artifact in bulk, and their elapsed_seconds are
    the legacy run's timings carried forward by the import.

    The real per-date generation cost, measured two ways, agrees:
      * those same salvaged metas record avg 75.1s/date (min 31.6, max 92.8)
      * the live 2026-08-27T141713Z run measures ~92s/date on fresh generation

    So at ~85s/date the ten-date rule fires roughly every 14 minutes and the two
    rules very nearly coincide. The stated bound is unaffected -- it is a
    maximum, and both rules still cap it -- but "ten dates" and "fifteen
    minutes" are the same bound in practice, not two very different ones.
    """

    def __init__(self, every_n_dates=10, every_seconds=900, enabled=True):
        if every_n_dates is not None and every_n_dates < 1:
            raise ValueError("every_n_dates must be >= 1")
        self.every_n_dates = every_n_dates
        self.every_seconds = every_seconds
        self.enabled = enabled
        self._since_push = 0
        self._last_push_at = time.time()
        self.pushes = []

    def note_date_completed(self):
        self._since_push += 1

    def should_push(self, *, final=False):
        if not self.enabled:
            return False
        if final:
            return self._since_push > 0
        if self.every_n_dates is not None and self._since_push >= self.every_n_dates:
            return True
        if self.every_seconds is not None and (time.time() - self._last_push_at) >= self.every_seconds:
            return self._since_push > 0
        return False

    def note_pushed(self, result):
        self._since_push = 0
        self._last_push_at = time.time()
        self.pushes.append(result)

    def describe(self):
        return {"every_n_dates": self.every_n_dates, "every_seconds": self.every_seconds,
                "enabled": self.enabled, "dates_since_last_push": self._since_push,
                "pushes_attempted": len(self.pushes),
                "pushes_succeeded": sum(1 for p in self.pushes if p.get("pushed"))}


# ══════════════════════════════════════════════════════════════════════════
#  DURABLE CHECKPOINT INDEX
# ══════════════════════════════════════════════════════════════════════════
#
# The index is the recovery entry point: one small JSON file naming the run
# contract and every date whose rows are durably present, with the checksum of
# each. A fresh container reads exactly this file to know what it can trust.

def build_durable_index(manifest, state_summary, *, environment=None,
                        lineage=None, cache_mode=None, dates=None):
    idx = {
        "durability_schema_version": DURABILITY_SCHEMA_VERSION,
        "run_id": manifest["run_id"],
        "updated_at": _now_iso(),
        # --- identity: every field a resume must match before reusing anything
        "identity": {
            "run_id": manifest["run_id"],
            "code_git_sha": manifest.get("code_git_sha"),
            "schema_version": manifest.get("schema_version"),
            "requested_start_date": manifest.get("requested_start_date"),
            "requested_end_date": manifest.get("requested_end_date"),
            "weather_mode": manifest.get("weather_mode"),
            "repository_identity": manifest.get("repository_identity"),
            "model_artifact_versions": manifest.get("model_artifact_versions"),
            "evidence_regime": manifest.get("evidence_regime"),
            "candidate_identity_fields": manifest.get("candidate_identity_fields"),
        },
        "environment": environment,
        "source_lineage": lineage or [],
        "source_lineage_fingerprint": lineage_fingerprint(lineage) if lineage else None,
        "cache_mode": cache_mode,
        "dates": dates or {},
        "summary": state_summary,
    }
    idx["identity_fingerprint"] = _sha256_bytes(
        json.dumps(idx["identity"], sort_keys=True).encode())
    return idx


class IdentityMismatch(Exception):
    """A durable checkpoint does not belong to the run contract being resumed."""


class DurableIntegrityError(Exception):
    """A durable checkpoint's bytes do not match its recorded checksum."""


def assert_identity_compatible(index, manifest, *, allow_environment_drift=False):
    """Fail closed unless the durable checkpoint provably belongs to this run.

    Every field here changes what the rows MEAN. Resuming across any of them
    would silently produce one artifact containing two different regimes, which
    is worse than losing the run: the resulting dataset looks complete and is
    not comparable to itself.

    Environment drift is fail-closed by default for the same reason. An operator
    may pass allow_environment_drift=True only as an explicit, visible override;
    the returned environment report still records every difference.
    """
    want = index.get("identity") or {}
    problems = []
    for field in ("run_id", "code_git_sha", "schema_version",
                  "requested_start_date", "requested_end_date",
                  "weather_mode", "repository_identity",
                  "evidence_regime", "candidate_identity_fields"):
        a, b = want.get(field), manifest.get(field)
        if a != b:
            problems.append(f"{field}: durable={a!r} local={b!r}")

    wa = want.get("model_artifact_versions") or {}
    wb = manifest.get("model_artifact_versions") or {}
    for k in sorted(set(wa) | set(wb)):
        if wa.get(k) != wb.get(k):
            problems.append(f"model_artifact_versions.{k}: durable={wa.get(k)!r} local={wb.get(k)!r}")

    if problems:
        raise IdentityMismatch(
            "refusing to resume from this durable checkpoint -- it does not match "
            "the local run contract:\n  " + "\n  ".join(problems) +
            "\nStart a new run id instead. Silently mixing regimes is not an option.")

    env_report = None
    if index.get("environment"):
        env_report = compare_environments(index["environment"], environment_identity())
        if not env_report["compatible"] and not allow_environment_drift:
            raise IdentityMismatch(
                "durable checkpoint's environment differs from this one and "
                "allow_environment_drift=False:\n  " +
                "\n  ".join(f"{d['field']}: {d['a']!r} -> {d['b']!r}"
                            for d in env_report["differences"]))
    return {"compatible": True, "environment": env_report,
            "identity_fingerprint": index.get("identity_fingerprint")}


# ══════════════════════════════════════════════════════════════════════════
#  GIT PLUMBING
# ══════════════════════════════════════════════════════════════════════════
#
# Plumbing only -- hash-object / mktree / commit-tree against a scratch index.
# Never `git add`, `git commit`, or `git checkout`. The canonical run's own
# worktree is a PINNED DETACHED HEAD, and an earlier version of the manifest
# pusher used porcelain and silently advanced it, which its own code-identity
# guard then correctly refused to run against. Plumbing cannot move HEAD, the
# real index, or the working tree of any repo it is invoked from.

def _git(args, *, cwd=REPO_ROOT, env=None, timeout=120, check=False):
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=check,
                          capture_output=True, text=True, timeout=timeout)


def _git_common_dir(repo_root=REPO_ROOT):
    p = _git(["rev-parse", "--git-common-dir"], cwd=repo_root, timeout=15)
    if p.returncode != 0:
        return None
    d = p.stdout.strip()
    return d if os.path.isabs(d) else os.path.normpath(os.path.join(repo_root, d))


def durable_paths(run_id, date=None):
    """Layout on the durable branch. Stable, because recovery depends on it."""
    base = f"canonical/{run_id}"
    if date is None:
        return {"base": base,
                "index": f"{base}/index.json",
                "manifest": f"{base}/manifest.json"}
    return {"rows_gz": f"{base}/rows/{date}.jsonl.gz",
            "meta": f"{base}/rows/{date}.meta.json"}


def push_durable_checkpoint(run_dir, manifest, *, dates=None, environment=None,
                            lineage=None, cache_mode=None, state_summary=None,
                            branch=DURABLE_BRANCH, remote="origin",
                            repo_root=REPO_ROOT, include_rows=True):
    """Push per-date rows (gzipped) plus meta plus the recovery index.

    This is the function whose absence caused the 2026-08-27 loss. The previous
    mechanism pushed `.meta.json` and `manifest.json` only, so after the
    container died we knew precisely which 421 dates had completed and had the
    rows for none of them.

    Rows are gzipped and written as one blob per date. A date already present
    on the branch with a matching checksum is not rewritten, so repeated pushes
    add only the new dates and history growth stays linear in dates, not in
    pushes x dates.

    Best-effort by contract: a push failure is REPORTED, never fatal to the run
    -- but it is reported loudly, because a run whose pushes are silently
    failing has the durability of the run that we lost.
    """
    result = {"pushed": False, "branch": branch, "reason": None,
              "dates_written": 0, "dates_skipped_present": 0, "bytes_written": 0,
              "commit": None, "at": _now_iso()}

    git_dir = _git_common_dir(repo_root)
    if git_dir is None:
        result["reason"] = "not a git checkout"
        return result

    run_id = manifest["run_id"]
    dp = durable_paths(run_id)

    if dates is None:
        rows_dir = os.path.join(run_dir, "checkpoints")
        dates = sorted(
            fn[: -len(".meta.json")]
            for fn in os.listdir(rows_dir)
            if fn.endswith(".meta.json")
        ) if os.path.isdir(rows_dir) else []

    scratch = tempfile.NamedTemporaryFile(delete=False).name
    env = dict(os.environ, GIT_DIR=git_dir, GIT_INDEX_FILE=scratch, GIT_WORK_TREE=repo_root)
    try:
        # Start the scratch index from the branch's current tip when there is
        # one, so previously pushed dates are carried forward rather than lost.
        parent = None
        for ref in (f"refs/remotes/{remote}/{branch}", f"refs/heads/{branch}"):
            p = _git(["rev-parse", "--verify", "--quiet", ref], env=env, timeout=15)
            if p.returncode == 0 and p.stdout.strip():
                parent = p.stdout.strip()
                break
        if parent is None:
            ls = _git(["ls-remote", remote, f"refs/heads/{branch}"], env=env, timeout=60)
            if ls.returncode == 0 and ls.stdout.strip():
                parent = ls.stdout.split()[0]
                _git(["fetch", "-q", remote, f"refs/heads/{branch}"], env=env, timeout=120)

        present = set()
        parent_ledger = {}
        if parent:
            rt = _git(["read-tree", parent], env=env, timeout=30)
            if rt.returncode != 0:
                result["reason"] = f"read-tree failed: {rt.stderr.strip()[:200]}"
                return result
            ls_files = _git(["ls-files", "--", f"{dp['base']}/"], env=env, timeout=30)
            present = set(ls_files.stdout.split("\n")) if ls_files.returncode == 0 else set()

            # Existing paths are not proof that the existing bytes belong to
            # this local checkpoint. Load the parent ledger once so every
            # "already present" decision can be checksum-backed.
            idx_existing = _git(
                ["show", f"{parent}:{dp['index']}"], env=env, timeout=30)
            if idx_existing.returncode == 0 and idx_existing.stdout.strip():
                try:
                    parent_ledger = (
                        json.loads(idx_existing.stdout).get("dates") or {}
                    )
                except json.JSONDecodeError:
                    result["reason"] = (
                        f"existing durable index {dp['index']!r} is not valid JSON; "
                        "refusing to trust path existence alone")
                    return result
        else:
            _git(["read-tree", "--empty"], env=env, timeout=15)

        def stage_blob(path_in_tree, data_bytes, *, executable=False):
            ho = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo_root,
                                env=env, input=data_bytes, capture_output=True, timeout=60)
            if ho.returncode != 0:
                return None
            blob = ho.stdout.decode().strip()
            mode = "100755" if executable else "100644"
            up = _git(["update-index", "--add", "--cacheinfo", f"{mode},{blob},{path_in_tree}"],
                      env=env, timeout=30)
            return blob if up.returncode == 0 else None

        def require_stage(path_in_tree, data_bytes, *, executable=False):
            blob = stage_blob(path_in_tree, data_bytes, executable=executable)
            if blob is None:
                result["reason"] = (
                    f"failed to stage required durable blob {path_in_tree!r}; "
                    "refusing to create or push a partial checkpoint commit")
                return False
            return True

        for d in dates:
            paths = durable_paths(run_id, d)
            meta_local = os.path.join(run_dir, "checkpoints", f"{d}.meta.json")
            data_local = os.path.join(run_dir, "checkpoints", f"{d}.jsonl")
            if not os.path.exists(meta_local):
                continue
            if paths["meta"] in present and paths["rows_gz"] in present:
                remote_entry = parent_ledger.get(d)
                if remote_entry is None:
                    result["reason"] = (
                        f"durable paths for {d} already exist but the parent index "
                        "has no checksum ledger entry; refusing to trust path existence")
                    return result
                local_meta_sha = _sha256_file(meta_local)
                remote_meta_sha = remote_entry.get("meta_sha256")
                local_data_sha = (
                    _sha256_file(data_local)
                    if os.path.exists(data_local) else None
                )
                remote_data_sha = remote_entry.get("data_sha256")
                meta_matches = (
                    remote_meta_sha is not None
                    and local_meta_sha == remote_meta_sha
                )
                data_matches = (
                    not include_rows
                    or (
                        remote_data_sha is not None
                        and local_data_sha == remote_data_sha
                    )
                )
                if meta_matches and data_matches:
                    result["dates_skipped_present"] += 1
                    continue
                result["reason"] = (
                    f"existing durable date {d} does not match local checkpoint "
                    f"(meta_match={meta_matches}, data_match={data_matches}); "
                    "refusing to overwrite or silently skip conflicting durable bytes")
                return result
            if include_rows and not os.path.exists(data_local):
                result["reason"] = (
                    f"checkpoint {d} has metadata but no rows file at {data_local!r}; "
                    "refusing to publish an incomplete durable date")
                return result
            with open(meta_local, "rb") as f:
                meta_bytes = f.read()
            if not require_stage(paths["meta"], meta_bytes):
                return result
            if include_rows:
                with open(data_local, "rb") as f:
                    raw = f.read()
                gz = gzip.compress(raw, compresslevel=9)
                if not require_stage(paths["rows_gz"], gz):
                    return result
                result["bytes_written"] += len(gz)
            result["dates_written"] += 1

        with open(os.path.join(run_dir, "manifest.json"), "rb") as f:
            if not require_stage(dp["manifest"], f.read()):
                return result

        index = build_durable_index(
            manifest, state_summary or {}, environment=environment,
            lineage=lineage, cache_mode=cache_mode,
            dates=durable_date_ledger(run_dir, dates),
        )
        if not require_stage(
                dp["index"], json.dumps(index, indent=2, sort_keys=True).encode()):
            return result

        tree = _git(["write-tree"], env=env, timeout=60)
        if tree.returncode != 0:
            result["reason"] = f"write-tree failed: {tree.stderr.strip()[:200]}"
            return result
        msg = (f"Canonical durable checkpoint {run_id} @ {_now_iso()}\n\n"
               f"dates written: {result['dates_written']}  "
               f"already present: {result['dates_skipped_present']}  "
               f"row bytes (gz): {result['bytes_written']}")
        args = ["commit-tree", tree.stdout.strip(), "-m", msg]
        if parent:
            args += ["-p", parent]
        cp = _git(args, env=env, timeout=30)
        if cp.returncode != 0:
            result["reason"] = f"commit-tree failed: {cp.stderr.strip()[:200]}"
            return result
        commit = cp.stdout.strip()
        result["commit"] = commit

        push = _git(["push", remote, f"{commit}:refs/heads/{branch}"], env=env, timeout=300)
        result["pushed"] = push.returncode == 0
        if not result["pushed"]:
            result["reason"] = push.stderr.strip()[:500]
        else:
            _git(["update-ref", f"refs/remotes/{remote}/{branch}", commit], env=env, timeout=15)
        return result
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        for p in (scratch, scratch + ".lock"):
            if os.path.exists(p):
                os.remove(p)


def durable_date_ledger(run_dir, dates):
    """Per-date record the recovery path verifies against: status, row count,
    and the sha256 of the RAW (pre-gzip) rows file. Gzip is not deterministic
    across implementations, so the checksum is taken over the uncompressed
    bytes -- the thing whose identity actually matters."""
    ledger = {}
    for d in dates:
        meta_p = os.path.join(run_dir, "checkpoints", f"{d}.meta.json")
        data_p = os.path.join(run_dir, "checkpoints", f"{d}.jsonl")
        if not os.path.exists(meta_p):
            continue
        try:
            with open(meta_p, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        # write_checkpoint() names this field "row_count"; reading "rows" here
        # silently recorded None for every date on the durable index.
        entry = {"status": meta.get("status"), "rows": meta.get("row_count"),
                 "meta_sha256": _sha256_file(meta_p)}
        if os.path.exists(data_p):
            entry["data_sha256"] = _sha256_file(data_p)
            entry["data_bytes"] = os.path.getsize(data_p)
        else:
            entry["data_sha256"] = None
            entry["data_bytes"] = 0
        ledger[d] = entry
    return ledger


# ══════════════════════════════════════════════════════════════════════════
#  RECOVERY
# ══════════════════════════════════════════════════════════════════════════

def _read_durable_blob(path_in_tree, *, branch=DURABLE_BRANCH, remote="origin",
                       repo_root=REPO_ROOT, ref=None):
    """Read one file out of the durable branch WITHOUT checking it out.

    Checking the branch out would replace the caller's working tree, which in a
    canonical worktree is a pinned detached HEAD. `git show <ref>:<path>` reads
    straight from the object database and touches nothing.
    """
    git_dir = _git_common_dir(repo_root)
    if git_dir is None:
        return None
    env = dict(os.environ, GIT_DIR=git_dir)
    if ref is None:
        for cand in (f"refs/remotes/{remote}/{branch}", f"refs/heads/{branch}"):
            p = _git(["rev-parse", "--verify", "--quiet", cand], env=env, timeout=15)
            if p.returncode == 0 and p.stdout.strip():
                ref = p.stdout.strip()
                break
    if ref is None:
        return None
    p = subprocess.run(["git", "show", f"{ref}:{path_in_tree}"], cwd=repo_root, env=env,
                       capture_output=True, timeout=120)
    return p.stdout if p.returncode == 0 else None


def fetch_durable_branch(*, branch=DURABLE_BRANCH, remote="origin", repo_root=REPO_ROOT):
    """Fetch the durable branch into a remote-tracking ref. This is the FIRST
    thing a fresh container does: everything else reads from the object store."""
    git_dir = _git_common_dir(repo_root)
    if git_dir is None:
        return {"ok": False, "reason": "not a git checkout"}
    env = dict(os.environ, GIT_DIR=git_dir)
    p = _git(["fetch", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"],
             env=env, timeout=300)
    if p.returncode != 0:
        return {"ok": False, "reason": p.stderr.strip()[:400]}
    tip = _git(["rev-parse", f"refs/remotes/{remote}/{branch}"], env=env, timeout=15)
    return {"ok": True, "tip": tip.stdout.strip() if tip.returncode == 0 else None}


def discover_durable_runs(*, branch=DURABLE_BRANCH, remote="origin", repo_root=REPO_ROOT):
    """List run ids that have a durable index on the branch, newest first."""
    git_dir = _git_common_dir(repo_root)
    if git_dir is None:
        return []
    env = dict(os.environ, GIT_DIR=git_dir)
    ref = None
    for cand in (f"refs/remotes/{remote}/{branch}", f"refs/heads/{branch}"):
        p = _git(["rev-parse", "--verify", "--quiet", cand], env=env, timeout=15)
        if p.returncode == 0 and p.stdout.strip():
            ref = p.stdout.strip()
            break
    if ref is None:
        return []
    p = _git(["ls-tree", "-r", "--name-only", ref, "canonical/"], env=env, timeout=60)
    if p.returncode != 0:
        return []
    runs = {}
    for line in p.stdout.split("\n"):
        if line.endswith("/index.json"):
            run_id = line.split("/")[1]
            raw = _read_durable_blob(line, ref=ref, repo_root=repo_root)
            if not raw:
                continue
            try:
                idx = json.loads(raw)
            except json.JSONDecodeError:
                continue
            runs[run_id] = {
                "run_id": run_id,
                "updated_at": idx.get("updated_at"),
                "dates": len(idx.get("dates") or {}),
                "summary": idx.get("summary"),
                "identity_fingerprint": idx.get("identity_fingerprint"),
                "code_git_sha": (idx.get("identity") or {}).get("code_git_sha"),
                "index_path": line,
            }
    return sorted(runs.values(), key=lambda r: r.get("updated_at") or "", reverse=True)


def load_durable_index(run_id, *, branch=DURABLE_BRANCH, remote="origin", repo_root=REPO_ROOT):
    raw = _read_durable_blob(durable_paths(run_id)["index"], branch=branch,
                             remote=remote, repo_root=repo_root)
    if raw is None:
        return None
    return json.loads(raw)


def restore_from_durable(run_dir, run_id, *, branch=DURABLE_BRANCH, remote="origin",
                         repo_root=REPO_ROOT, manifest=None, verify=True,
                         allow_environment_drift=False):
    """Rebuild local continuation state from the remote. The recovery contract.

    Steps, in this order, because each depends on the previous one holding:

      1. Read the durable index.
      2. Verify it belongs to the same run contract        -> IdentityMismatch
      3. Restore manifest.json if the caller has none.
      4. For every date in the ledger, decompress the rows and RECOMPUTE the
         sha256 over the raw bytes, comparing against the ledger's value.
                                                            -> DurableIntegrityError
      5. Report what was restored so the caller can resume only what is missing.

    Nothing is written to run_dir until step 2 passes. A checkpoint that cannot
    be proven compatible never lands on disk at all. Environment identity is
    part of that gate by default; allow_environment_drift=True is an explicit
    research override, never the silent default.
    """
    report = {"run_id": run_id, "restored": [], "skipped_present": [], "failed": [],
              "manifest_restored": False, "index": None, "identity": None,
              "at": _now_iso()}

    index = load_durable_index(run_id, branch=branch, remote=remote, repo_root=repo_root)
    if index is None:
        raise FileNotFoundError(
            f"no durable index for run {run_id!r} on {remote}/{branch} -- "
            "nothing to recover from. Did fetch_durable_branch() run first?")
    report["index"] = {"updated_at": index.get("updated_at"),
                       "dates": len(index.get("dates") or {}),
                       "identity_fingerprint": index.get("identity_fingerprint")}

    manifest_path = os.path.join(run_dir, "manifest.json")

    if manifest is None and os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

    manifest_raw = None
    if manifest is None:
        manifest_raw = _read_durable_blob(
            durable_paths(run_id)["manifest"], branch=branch,
            remote=remote, repo_root=repo_root)
        if manifest_raw is None:
            raise FileNotFoundError(
                f"durable index exists for {run_id} but manifest.json does not")
        manifest = json.loads(manifest_raw)

    # Identity is the write gate. In particular, when the local run directory
    # does not yet exist, do not even create it until the durable manifest has
    # been proven compatible with the durable index.
    report["identity"] = assert_identity_compatible(
        index, manifest, allow_environment_drift=allow_environment_drift)

    os.makedirs(os.path.join(run_dir, "checkpoints"), exist_ok=True)
    if manifest_raw is not None:
        with open(manifest_path, "wb") as f:
            f.write(manifest_raw)
        report["manifest_restored"] = True

    ledger = index.get("dates") or {}
    for d in sorted(ledger):
        entry = ledger[d]
        meta_p = os.path.join(run_dir, "checkpoints", f"{d}.meta.json")
        data_p = os.path.join(run_dir, "checkpoints", f"{d}.jsonl")
        if os.path.exists(meta_p) and os.path.exists(data_p):
            if not verify:
                report["skipped_present"].append(d)
                continue

            want_data = entry.get("data_sha256")
            want_meta = entry.get("meta_sha256")
            data_matches = (
                want_data is not None and _sha256_file(data_p) == want_data
            )
            meta_matches = (
                want_meta is not None and _sha256_file(meta_p) == want_meta
            )
            if data_matches and meta_matches:
                report["skipped_present"].append(d)
                continue
            # A local pair is trusted only when BOTH blobs match the durable
            # ledger. Otherwise fall through to verified remote restoration,
            # which repairs the local copy from the durable source of truth.

        paths = durable_paths(run_id, d)
        meta_raw = _read_durable_blob(paths["meta"], branch=branch, remote=remote, repo_root=repo_root)
        if meta_raw is None:
            report["failed"].append({"date": d, "reason": "meta blob missing on durable branch"})
            continue

        rows_raw = b""
        gz = _read_durable_blob(paths["rows_gz"], branch=branch, remote=remote, repo_root=repo_root)
        if gz is not None:
            try:
                rows_raw = gzip.decompress(gz)
            except (OSError, EOFError, gzip.BadGzipFile) as exc:
                report["failed"].append({"date": d, "reason": f"gzip decompress failed: {exc}"})
                continue
        elif entry.get("data_bytes"):
            report["failed"].append({"date": d, "reason": "rows blob missing but ledger says it has bytes"})
            continue

        if verify:
            got = _sha256_bytes(rows_raw)
            want = entry.get("data_sha256")
            if want is not None and got != want:
                raise DurableIntegrityError(
                    f"{run_id} {d}: restored rows sha256 {got} != ledger {want}. "
                    "The durable checkpoint is corrupt or was tampered with; refusing to "
                    "resume from it. Nothing further has been written for this date.")
            got_meta = _sha256_bytes(meta_raw)
            want_meta = entry.get("meta_sha256")
            if want_meta is not None and got_meta != want_meta:
                raise DurableIntegrityError(
                    f"{run_id} {d}: restored meta sha256 {got_meta} != ledger {want_meta}.")

        with open(data_p, "wb") as f:
            f.write(rows_raw)
        with open(meta_p, "wb") as f:
            f.write(meta_raw)
        report["restored"].append(d)

    return report

#!/usr/bin/env python3
"""canonical_population.py -- the bridge from canonical durable rows to a
fingerprinted EligiblePopulation the equal-volume framework can measure.

WHY THIS MODULE EXISTS.

backtest/equal_volume.py is the only machinery in this project that
measures the North Star -- REALIZED HIT RATE AT EQUAL PICK VOLUME. It is
complete, adversarially tested, and until now had no data path at all:
before this module, EligiblePopulation and EqualVolumeExperiment were
constructed exclusively inside test files. accuracy_lab.py, meanwhile,
HAS a data path but records only Brier, log-loss and a calibration table
-- it is a probability-quality lab, and reading its output as if it
answered "does this select better picks" is the specific mistake the
North Star exists to prevent.

This module closes that gap and nothing else. It performs no scoring, no
fitting, no promotion, and no network access -- it reshapes already-
generated, already-graded canonical rows into the frozen population
object equal_volume.py requires.

WHERE THE ROWS COME FROM.

The canonical run publishes each date to the durable checkpoint branch as
rows/{date}.jsonl.gz plus an index.json carrying per-date sha256s and the
run's bound source lineage. This module reads that branch through `git
show` -- read-only plumbing against an object database, never a checkout
-- so it cannot disturb a pinned worktree or a running generator. It is
therefore safe to build a population from a run that is still generating;
you simply get the dates durable so far.

DATASET IDENTITY IS DERIVED, NOT ASSERTED.

equal_volume.EqualVolumeExperiment(promotion_grade=True) demands
artifact_sha256 and artifact_row_count, because an unidentified dataset
cannot back a promotion claim. A row set assembled from N per-date files
has no single file to hash, so artifact_sha256 here is computed over the
sorted (date, data_sha256) pairs the run itself recorded -- it changes if
any date's bytes change, if a date is added, or if one is dropped. The
Statcast artifact the rows were generated FROM is carried separately as
source_artifact_sha256: a different question (what was the input?) from
the one artifact_sha256 answers (what are these rows?), and collapsing
the two is exactly the confusion the source-identity work exists to
prevent.

FAIR_TEST IS NEVER APPLIED SILENTLY.

backtest/calibration.py's own docstring sets the house rule: fair_test
filtering is an explicit caller decision with NO default exclusion,
because "did this pick get a real opportunity" is a judgement about what
you are measuring, not a data-cleaning step. build_eligible_population()
therefore REQUIRES fair_test_only to be passed, records the choice in the
eligibility definition, and reports how many rows it removed.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess

from backtest.equal_volume import (
    CANDIDATE_IDENTITY_FIELDS,
    EligiblePopulation,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL_DURABLE_REF = "origin/canonical-durable-checkpoints"

# Bumped whenever the MEANING of eligibility changes -- a new filter, a
# changed default, a different required field. Two populations built under
# different versions are not comparable and must not be pooled.
ELIGIBILITY_DEFINITION_VERSION = "1.0.0"

EVIDENCE_REGIME = "canonical_historical_model_data"


class CanonicalPopulationError(Exception):
    """The canonical rows could not be loaded or are not fit to measure."""


def _git(args, *, repo_root=REPO_ROOT, binary=False):
    proc = subprocess.run(["git", "-C", repo_root] + args,
                          capture_output=True)
    if proc.returncode != 0:
        raise CanonicalPopulationError(
            f"git {' '.join(args)} failed: "
            f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout if binary else proc.stdout.decode("utf-8")


def read_run_index(run_id, *, ref=CANONICAL_DURABLE_REF, repo_root=REPO_ROOT):
    """The run's durable index.json: per-date sha256s and source lineage."""
    raw = _git(["show", f"{ref}:canonical/{run_id}/index.json"],
               repo_root=repo_root)
    return json.loads(raw)


def load_canonical_rows(run_id, *, ref=CANONICAL_DURABLE_REF,
                        repo_root=REPO_ROOT, dates=None, index=None):
    """Read a run's durable rows. Returns (rows, artifact).

    `dates` restricts to a subset (still validated against the index, so a
    typo raises rather than silently yielding fewer rows). `index` lets a
    caller supply an already-read index to avoid a second git call.

    Every date is verified against the sha256 the run recorded for it. A
    mismatch raises: silently measuring rows whose bytes changed since
    they were certified is the failure this whole layer exists to stop.
    """
    idx = index if index is not None else read_run_index(
        run_id, ref=ref, repo_root=repo_root)
    available = idx.get("dates") or {}
    if not available:
        raise CanonicalPopulationError(
            f"run {run_id!r} has no durable dates recorded in its index")

    wanted = sorted(available) if dates is None else sorted(dates)
    unknown = [d for d in wanted if d not in available]
    if unknown:
        raise CanonicalPopulationError(
            f"run {run_id!r} has no durable rows for {unknown!r}; "
            f"available dates are {sorted(available)[0]}..{sorted(available)[-1]}")

    rows, per_date = [], []
    for date in wanted:
        meta = available[date]
        if meta.get("status") != "ok":
            raise CanonicalPopulationError(
                f"{run_id} date {date} has status {meta.get('status')!r}, not "
                f"'ok' -- a date the run itself did not certify must not enter "
                f"a population silently")
        blob = _git(["show", f"{ref}:canonical/{run_id}/rows/{date}.jsonl.gz"],
                    repo_root=repo_root, binary=True)
        try:
            raw = gzip.decompress(blob)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise CanonicalPopulationError(
                f"{run_id} date {date}: rows blob will not decompress: {exc}")
        # data_sha256 is recorded over the UNCOMPRESSED .jsonl bytes -- see
        # canonical_durability.py's own restore path, which decompresses and
        # then hashes. Hashing the .gz here would reject every real date.
        actual = hashlib.sha256(raw).hexdigest()
        expected = meta.get("data_sha256")
        if expected and actual != expected:
            raise CanonicalPopulationError(
                f"{run_id} date {date}: rows sha256 {actual[:12]} does not match "
                f"the {expected[:12]} recorded in the index -- these bytes are "
                f"not the bytes the run certified")
        date_rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        rows.extend(date_rows)
        per_date.append((date, actual))

    lineage = idx.get("source_lineage") or []
    source_sha = lineage[0]["content_sha256"] if lineage else None

    artifact = {
        # What are these rows? Changes if any date's bytes change, or if a
        # date is added or removed.
        "artifact_sha256": hashlib.sha256(
            json.dumps(per_date, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")).hexdigest(),
        "artifact_row_count": len(rows),
        "run_id": run_id,
        "n_dates": len(per_date),
        "date_range": [per_date[0][0], per_date[-1][0]] if per_date else None,
        # What were these rows generated FROM? A different question.
        "source_artifact_sha256": source_sha,
        "source_lineage_entries": len(lineage),
        "code_git_sha": (idx.get("identity") or {}).get("code_git_sha"),
        "durable_ref": ref,
        "run_complete": None,  # unknown from the index alone; see below
    }
    return rows, artifact


REQUIRED_ROW_FIELDS = ("outcome", "score", "predicted_prob")


def build_eligible_population(rows, artifact, *, fair_test_only,
                              markets=None, date_range=None,
                              required_fields=REQUIRED_ROW_FIELDS,
                              definition=None):
    """Freeze canonical rows into an EligiblePopulation.

    fair_test_only is REQUIRED and has no default -- see this module's
    docstring and backtest/calibration.py's own rule. Every filter records
    what it removed in the population's exclusions list, so a population
    can always answer "what is not in me, and why".
    """
    if not isinstance(fair_test_only, bool):
        raise CanonicalPopulationError(
            "fair_test_only must be passed explicitly as a bool. Whether a pick "
            "that never got a real opportunity belongs in the population is a "
            "decision about what you are measuring -- it must not be defaulted.")

    exclusions, kept = [], list(rows)

    def _drop(reason, predicate):
        nonlocal kept
        before = len(kept)
        kept = [r for r in kept if predicate(r)]
        removed = before - len(kept)
        if removed:
            exclusions.append({"reason": reason, "n_removed": removed})

    if date_range is not None:
        lo, hi = date_range
        _drop(f"date outside {lo}..{hi}", lambda r: lo <= r.get("date", "") <= hi)
    if markets is not None:
        allowed = set(markets)
        _drop(f"prop_type not in {sorted(allowed)}",
              lambda r: r.get("prop_type") in allowed)
    for field in required_fields:
        _drop(f"missing required field {field!r}",
              lambda r, f=field: r.get(f) is not None)
    if fair_test_only:
        _drop("fair_test is False (pick had no real opportunity)",
              lambda r: r.get("fair_test", True) is not False)

    if not kept:
        raise CanonicalPopulationError(
            f"no rows survived eligibility; exclusions={exclusions}")

    dataset_identity = {
        "artifact_sha256": artifact["artifact_sha256"],
        "artifact_row_count": artifact["artifact_row_count"],
        "run_id": artifact.get("run_id"),
        "n_dates": artifact.get("n_dates"),
        "date_range": artifact.get("date_range"),
        "source_artifact_sha256": artifact.get("source_artifact_sha256"),
        "code_git_sha": artifact.get("code_git_sha"),
    }

    if definition is None:
        definition = (
            "canonical durable rows with a recorded outcome, score and "
            "predicted_prob"
            + (", fair_test only" if fair_test_only else
               ", INCLUDING rows that got no real opportunity")
            + (f", markets={sorted(markets)}" if markets else ", all markets")
        )

    return EligiblePopulation(
        kept,
        definition=definition,
        definition_version=ELIGIBILITY_DEFINITION_VERSION,
        evidence_regime=EVIDENCE_REGIME,
        dataset_identity=dataset_identity,
        exclusions=exclusions,
    )


def load_eligible_population(run_id, *, fair_test_only, ref=CANONICAL_DURABLE_REF,
                             repo_root=REPO_ROOT, dates=None, markets=None,
                             date_range=None):
    """Convenience: read a run's durable rows and freeze them in one call."""
    rows, artifact = load_canonical_rows(run_id, ref=ref, repo_root=repo_root,
                                         dates=dates)
    return build_eligible_population(rows, artifact,
                                     fair_test_only=fair_test_only,
                                     markets=markets, date_range=date_range)


def describe_population_coverage(population):
    """What a population can and cannot support -- checked, not assumed.

    Exists because the canonical artifact has real, documented gaps (no
    calibrated_prob by schema design, no odds by backtest design, no
    extras on this run). A caller that discovers those in the middle of an
    experiment writes a wrong conclusion; a caller that reads them here
    picks a different experiment.
    """
    rows = population.rows
    n = len(rows)
    from collections import Counter
    present = {f: sum(1 for r in rows if r.get(f) is not None)
               for f in ("score", "predicted_prob", "calibrated_prob",
                         "outcome", "fair_test", "extras")}
    return {
        "n_rows": n,
        "n_dates": len({r.get("date") for r in rows}),
        "n_games": len({r.get("game_pk") for r in rows}),
        "markets": dict(Counter(r.get("prop_type") for r in rows)),
        "field_coverage": present,
        "base_rate": (sum(1 for r in rows if r.get("outcome")) / n) if n else None,
        "supports_realized_hit_rate": present["outcome"] == n,
        "supports_calibration_comparison": present["calibrated_prob"] > 0,
        "supports_usable_volume": False,  # no odds on canonical rows, by design
        "clustering_unit": "game_pk",
        "effective_n_note": (
            "uncertainty must cluster on game_pk: rows within a game share "
            "lineup, park, weather and opponent, so the effective sample size "
            "is the game count, not the row count"),
    }

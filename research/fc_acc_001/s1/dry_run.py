#!/usr/bin/env python3
"""FC-ACC-001-S1 NON-LIVE dry run. Uses SYNTHETIC fixtures (no network) and,
separately, real committed board-freeze candidates with NO raw sportsbook
capture, to show the fail-closed accounting. Output is labelled
DRY_RUN_NONCONFIRMATORY and is NOT forward evidence."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import features as F  # noqa: E402
import fanduel as FDC  # noqa: E402
import fixtures as X  # noqa: E402
import ledger as L  # noqa: E402
import manifest as M  # noqa: E402
import raw_archive as RA  # noqa: E402
import workload as W  # noqa: E402


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

VERSIONS = {"model_version": "2026.08.15", "selection_policy_version": "1.0.0", "calibration_version": "1.0.0",
            "feature_version": "1.0.0", "git_sha": "DRY_RUN"}


def synthetic(work):
    arch = RA.RawArchive(os.path.join(work, "archive"))
    cap = FDC.sweep(arch, X.make_fetch(), clock=X.make_clock())
    gl_sha, _ = arch.put(X.gamelog_bytes())
    wl = W.pregame_workload(arch, gl_sha, X.DATE)
    snaps = {cid: F.snapshot(cid, next(c["stat"] for c in X.candidates() if c["candidate_id"] == cid), raw,
                             wl if cid in ("c5", "c6") else None, captured_at=f"{X.DATE}T21:30:00Z",
                             model_versions=VERSIONS) for cid, raw in X.raw_candidates().items()}
    unit = {"date": X.DATE, "window": "NIGHT", "decision_at": f"{X.DATE}T21:30:00Z"}
    m = M.build(unit=unit, games=X.GAMES, candidates=X.candidates(), capture=cap, archive=arch,
                feature_snapshots=snaps, mode="DRY_RUN_NONCONFIRMATORY", model_versions=VERSIONS)
    path, status = L.seal(os.path.join(work, "ledger"), m, arch, ledger_kind="DRY_RUN", now=f"{X.DATE}T21:31:00Z")
    _, again = L.seal(os.path.join(work, "ledger"), m, arch, ledger_kind="DRY_RUN", now=f"{X.DATE}T21:32:00Z")
    return {"label": "DRY_RUN_NONCONFIRMATORY_SYNTHETIC", "capture_status": cap["status"],
            "offers_parsed": len(FDC.parse_offers(cap, arch)), "manifest_sha256": m["manifest_sha256"],
            "accounting": m["accounting"],
            "arms": {k: {kk: v[kk] for kk in ("policy", "n_target", "n_selected", "status")} | {
                "selections": [s["candidate_id"] for s in v["selections"]]} for k, v in m["arms"].items()},
            "seal": status, "reseal": again, "archive_bytes": arch.size_bytes(),
            "manifest_bytes": len(json.dumps(m, sort_keys=True, separators=(",", ":"))),
            "workload_example": wl}


def real_without_capture():
    """Real committed candidates (latest board freeze) but no raw capture exists:
    every offer must be unauthenticated -> the build fails closed."""
    import glob
    f = sorted(glob.glob(os.path.join(HERE, "..", "..", "..", "output", "board_freeze_2026-*.json")))[-1]
    b = _load_json(f)
    with tempfile.TemporaryDirectory() as d:
        arch = RA.RawArchive(d)
        empty = FDC.sweep(arch, lambda path: (_ for _ in ()).throw(RuntimeError("no live capture in dry run")),
                          clock=lambda: b["sealed_at"].replace("+00:00", "Z"))
        try:
            M.build(unit={"date": b["date"], "window": "NIGHT", "decision_at": b["sealed_at"]},
                    games=[{"game_pk": k, "matchup": "?", "first_pitch_utc": v} for k, v in b["game_start_times"].items()],
                    candidates=b["records"], capture=empty, archive=arch, feature_snapshots={},
                    mode="DRY_RUN_NONCONFIRMATORY", model_versions=b["provenance"])
            return {"board": os.path.basename(f), "result": "UNEXPECTED_BUILD"}
        except M.ManifestError as e:
            return {"board": os.path.basename(f), "candidates": b["record_count"], "result": "FAIL_CLOSED", "reason": str(e)}


def main():
    with tempfile.TemporaryDirectory() as work:
        out = {"synthetic": synthetic(work), "real_candidates_no_capture": real_without_capture()}
    print(json.dumps(out, indent=1, sort_keys=True))
    return out


if __name__ == "__main__":
    main()

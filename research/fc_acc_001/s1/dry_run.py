#!/usr/bin/env python3
"""FC-ACC-001-S1 NON-LIVE dry run. Uses SYNTHETIC fixtures (no network) and,
separately, the latest real committed board freeze with NO raw schedule and NO
raw sportsbook capture, to show fail-closed behaviour. Output is labelled
DRY_RUN_NONCONFIRMATORY and is NOT forward evidence."""
import glob
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import anchor as AN  # noqa: E402
import features as F  # noqa: E402
import fanduel as FDC  # noqa: E402
import fixtures as X  # noqa: E402
import ledger as L  # noqa: E402
import manifest as M  # noqa: E402
import raw_archive as RA  # noqa: E402
import schedule as SCH  # noqa: E402
import sources as S  # noqa: E402
import workload as W  # noqa: E402


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def synthetic(work):
    arch = RA.RawArchive(os.path.join(work, "archive"))
    cap = X.capture(arch)
    wl = W.pregame_workload(arch, X.gamelog_source(arch), "9", X.DATE, decision_at=X.DECISION)
    stat = {r["candidate_id"]: r["stat"] for r in X.records()}
    snaps = {cid: F.snapshot(cid, stat[cid], raw, wl if cid in ("c5", "c6") else None, captured_at=X.DECISION,
                             model_versions=X.VERSIONS) for cid, raw in X.raw_candidates().items()}
    commitment = X.commitment(arch)
    m = M.build(unit=X.UNIT, board=X.board(), schedule_source=X.schedule_source(arch), capture=cap, archive=arch,
                feature_snapshots=snaps, mode="DRY_RUN_NONCONFIRMATORY", cutoff_commitment=commitment)
    led = os.path.join(work, "ledger")
    cutoff_status = L.record_cutoff(led, commitment, arch, ledger_kind="DRY_RUN", now=f"{X.DATE}T18:30:00Z")
    _, status = L.seal(led, m, arch, ledger_kind="DRY_RUN", now=f"{X.DATE}T21:31:00Z")
    _, again = L.seal(led, m, arch, ledger_kind="DRY_RUN", now=f"{X.DATE}T21:32:00Z")
    return {"label": "DRY_RUN_NONCONFIRMATORY_SYNTHETIC", "capture_status": cap["status"],
            "offers_parsed": len(FDC.parse_offers(cap, arch)), "manifest_sha256": m["manifest_sha256"],
            "unit_status": m["unit_status"], "non_comparable_reasons": m["non_comparable_reasons"],
            "accounting": m["accounting"], "certification": m["certification"],
            "arms": {k: {kk: v[kk] for kk in ("policy", "n_target", "n_selected", "status")} | {
                "selections": [s["candidate_id"] for s in v["selections"]]} for k, v in m["arms"].items()},
            "cutoff_commitment": {"cutoff_utc": commitment["cutoff_utc"],
                                  "commitment_deadline_utc": commitment["commitment_deadline_utc"],
                                  "recorded": cutoff_status,
                                  "statement_unpublished": AN.commitment_statement(commitment),
                                  "anchor_status": AN.verify_commitment_anchor(commitment, [])},
            "seal": status, "reseal": again, "anchor_statement_unpublished": AN.head_statement(led),
            "anchor_status_without_external_anchor": AN.verify_anchors(led, [], arch),
            "archive_bytes": arch.size_bytes(),
            "manifest_bytes": len(json.dumps(m, sort_keys=True, separators=(",", ":"))),
            "workload_example": {k: v for k, v in wl.items() if k != "source_record"}}


def real_without_sources():
    """Latest real committed board (seal re-verified) with no archived schedule
    and no sportsbook capture: the build must fail closed."""
    f = sorted(p for p in glob.glob(os.path.join(HERE, "..", "..", "..", "output", "board_freeze_2026-*.json"))
               if "graded" not in p)[-1]
    b = _load_json(f)
    with tempfile.TemporaryDirectory() as d:
        arch = RA.RawArchive(d)
        clock = lambda: b["sealed_at"].replace("+00:00", "Z")  # noqa: E731
        no_net = lambda path: (_ for _ in ()).throw(RuntimeError("no live fetch in dry run"))  # noqa: E731
        sched = S.fetch(arch, no_net, clock, source=SCH.SOURCE, request=SCH.request_for(b["date"]))
        try:
            M.build(unit={"date": b["date"], "window": "NIGHT", "decision_at": b["sealed_at"]}, board=b,
                    schedule_source=sched, capture=FDC.sweep(arch, no_net, clock=clock), archive=arch,
                    feature_snapshots={}, mode="DRY_RUN_NONCONFIRMATORY")
            return {"board": os.path.basename(f), "result": "UNEXPECTED_BUILD"}
        except M.ManifestError as e:
            return {"board": os.path.basename(f), "candidates": b["record_count"], "result": "FAIL_CLOSED",
                    "reason": str(e)}


def main():
    with tempfile.TemporaryDirectory() as work:
        out = {"synthetic": synthetic(work), "real_board_without_sources": real_without_sources()}
    print(json.dumps(out, indent=1, sort_keys=True))
    return out


if __name__ == "__main__":
    main()

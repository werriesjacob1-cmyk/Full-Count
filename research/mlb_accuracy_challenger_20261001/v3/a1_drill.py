#!/usr/bin/env python3
"""FC-MLB-001A EVIDENCE-INTEGRITY DRILL -- NOT A PROSPECTIVE UNIT.

  record:  python3 a1_drill.py record --repo R --date YYYY-MM-DD --window DAY|NIGHT --out DIR
           Runs runner.py --mode drill --with-tsa (local only: no evidence-ref push, no #91 receipt, never a
           chain unit), then writes A1_DRILL.json (identities + record environment) and SHA256SUMS.
  verify:  python3 a1_drill.py verify --drill-dir DIR [--result OUT.json]
           From the sealed drill artifacts ALONE, in a fresh isolated environment: every artifact hash, the
           board / capture / schedule / manifest bindings, manifest re-resolution, overlay cutoff, tape identity,
           the TSA tokens over the manifest digest (verified from token bytes), provenance identity, and an
           exact shadow replay (0 misses, 0 unconsumed, identical locked environment). Exit 0 iff all pass.

The drill directory name and every file carry the DRILL label; nothing here touches CHAIN.json, the evidence
ref, the dispatcher, the trigger or any slate unit.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import capture as CP  # noqa: E402
import isolation as ISO  # noqa: E402
import manifest_v3 as M3  # noqa: E402
import seal as SL  # noqa: E402
import shadow as SH  # noqa: E402
import verify_evidence as VE  # noqa: E402

LABEL = "FC-MLB-001A EVIDENCE-INTEGRITY DRILL — NOT A PROSPECTIVE UNIT"
PART_BYTES = 95 * 1024 * 1024          # GitHub rejects files > 100 MB; parts are byte-exact slices of the tape


def split_tape(d, name="shadow_tape.json.gz"):
    """Store the (complete, large) tape as byte-exact parts + an index binding the whole-file sha256."""
    p = os.path.join(d, name)
    whole = _sha(p)
    parts = []
    with open(p, "rb") as fh:
        i = 0
        while True:
            b = fh.read(PART_BYTES)
            if not b:
                break
            pn = f"{name}.part{i:02d}"
            with open(os.path.join(d, pn), "wb") as out:
                out.write(b)
            parts.append({"name": pn, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)})
            i += 1
    with open(os.path.join(d, name + ".parts.json"), "w") as fh:
        json.dump({"file": name, "sha256": whole, "bytes": os.path.getsize(p), "parts": parts}, fh, indent=1)
    os.remove(p)
    return whole


def materialize(d):
    """Return a directory where every split artifact is reassembled (a temp COPY; the drill dir is never modified)."""
    idx = [n for n in os.listdir(d) if n.endswith(".parts.json")]
    if not idx:
        return d, None
    import shutil
    import tempfile
    t = tempfile.mkdtemp(prefix="a1drill_")
    w = os.path.join(t, os.path.basename(os.path.normpath(d)))
    shutil.copytree(d, w)
    for n in idx:
        meta = json.load(open(os.path.join(w, n)))
        with open(os.path.join(w, meta["file"]), "wb") as out:
            for part in meta["parts"]:
                b = open(os.path.join(w, part["name"]), "rb").read()
                if hashlib.sha256(b).hexdigest() != part["sha256"]:
                    raise SystemExit(f"tape part hash mismatch: {part['name']}")
                out.write(b)
        if _sha(os.path.join(w, meta["file"])) != meta["sha256"]:
            raise SystemExit(f"reassembled {meta['file']} sha256 mismatch")
    return w, t


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def _git(*a):
    return subprocess.check_output(["git", "-C", HERE, *a]).decode().strip()


def write_sums(d):
    names = sorted(n for n in os.listdir(d) if n != "SHA256SUMS" and os.path.isfile(os.path.join(d, n)))
    with open(os.path.join(d, "SHA256SUMS"), "w") as fh:
        fh.writelines(f"{_sha(os.path.join(d, n))}  {n}\n" for n in names)


def record(a):
    import runner as RN
    if not os.path.basename(os.path.normpath(a.out)).startswith("A1_DRILL_"):
        raise SystemExit("drill output directory must be named A1_DRILL_* (never a slate-unit name)")
    rc = RN.main(["--mode", "drill", "--with-tsa", "--repo", a.repo, "--date", a.date, "--window", a.window, "--out", a.out])
    if rc != 0 or not os.path.exists(os.path.join(a.out, "DRILL_SUMMARY.json")):
        raise SystemExit(f"drill record did not complete (runner rc={rc})")
    for junk in ("shadow_tape.json.gz.record.report.json", "shadow_tape.json.gz.record.env.json"):
        p = os.path.join(a.out, junk)
        if os.path.exists(p):
            os.replace(p, os.path.join(a.out, "record_" + junk.split(".gz.")[1]))
    st = os.path.join(a.out, "shadow_tape.json.gz.record.strace")          # raw record process-tree trace (R5)
    if os.path.exists(st):
        import shutil
        with open(st, "rb") as src, gzip.GzipFile(os.path.join(a.out, "record_record.strace.gz"), "wb", mtime=0) as dst:
            shutil.copyfileobj(src, dst)
        os.remove(st)
    summ = json.load(open(os.path.join(a.out, "DRILL_SUMMARY.json")))
    env = json.load(open(os.path.join(a.out, "shadow_env.json")))
    meta = {"label": LABEL, "not_a_prospective_unit": True, "evidence_chain": "NOT APPENDED (drill mode: no push, no #91 receipt)",
            "slate_used_for_live_inputs": {"date": a.date, "window": a.window},
            "amendment_commit": _git("rev-parse", "HEAD"),
            "amendment_v3_tree": _git("rev-parse", "HEAD:research/mlb_accuracy_challenger_20261001/v3"),
            "shadow_pin": SH.SHADOW_PIN, "shadow_tree": SH.SHADOW_TREE,
            "lock_sha256": env["lock_sha256"], "installed_set_sha256": env["installed_set_sha256"],
            "record_environment": {"python": env["python"], "machine": env["machine"], "platform": platform.platform(),
                                   "isolation": env["isolation"], "guard_violations": env["guard"]["violations"],
                                   "n_http_recorded": env["n_http"]},
            "manifest_sha256": summ["manifest_sha256"], "artifacts_sha256": summ["artifacts_sha256"]}
    meta["tape_storage"] = {"split_parts": True, "whole_sha256": split_tape(a.out),
                            "reason": "complete (cache-free) tape exceeds GitHub's 100 MB per-file limit"}
    with open(os.path.join(a.out, "A1_DRILL.json"), "w") as fh:
        json.dump(meta, fh, indent=1, sort_keys=True)
    write_sums(a.out)
    print(json.dumps(meta, indent=1, sort_keys=True))
    return 0


# Every check belongs to exactly one gate. INTEGRITY: sealed artifacts intact and the replay consumed exactly the
# tape under the same locked environment. FROZEN_CONFORMANCE: the frozen FC-MLB-001A acceptance criteria as written.
GATES = {"integrity": ("sha256sums", "artifact_set", "provenance_identity", "board_hash", "capture_hash", "schedule_hash",
                       "manifest_hash", "manifest_reproducible", "overlay", "tape_identity", "timestamps",
                       "replay_tape_exact", "replay_environment"),
         "frozen_conformance": ("literal_board_identity", "r5_process_trace_frozen_surfaces", "r5_record_process_trace",
                                "record_conforms_to_current_a1")}


def _load(d, n):
    return json.load(gzip.open(os.path.join(d, n))) if n.endswith(".gz") else json.load(open(os.path.join(d, n)))


def verify(a):
    orig = a.drill_dir
    res = {"label": LABEL, "checks": {}}
    ok = res["checks"]
    sums = [ln.split("  ", 1) for ln in open(os.path.join(orig, "SHA256SUMS")).read().splitlines()]
    bad = [n for h, n in sums if not os.path.exists(os.path.join(orig, n)) or _sha(os.path.join(orig, n)) != h]
    unlisted = sorted(set(os.listdir(orig)) - {n for _, n in sums} - {"SHA256SUMS"})
    ok["sha256sums"] = "PASS" if not bad and not unlisted else f"FAIL: changed={bad} unlisted={unlisted}"
    d, tmp = materialize(orig)
    try:
        return _verify(a, d, res, ok)
    finally:
        if tmp:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


def _verify(a, d, res, ok):

    def fail(k, why):
        ok[k] = f"FAIL: {why}"

    summ, meta = _load(d, "DRILL_SUMMARY.json"), _load(d, "A1_DRILL.json")
    arts = summ["artifacts_sha256"]
    need = set(VE.REQUIRED_ARTIFACTS) | {"shadow_env.json"}
    if not need <= set(arts) or set(arts) - need - set(VE.OPTIONAL_ARTIFACTS):
        fail("artifact_set", sorted(arts))
    else:
        mism = [n for n, h in arts.items() if _sha(os.path.join(d, n)) != h]
        ok["artifact_set"] = "PASS" if not mism else f"FAIL: {mism}"
    board, cap, sched, man = (_load(d, n) for n in ("shadow_board.json.gz", "capture.json.gz", "schedule.json", "manifest.json.gz"))
    try:
        SH.verify_shadow_board(board)
        ok["provenance_identity"] = ("PASS" if board["provenance"]["git_sha"] == SH.SHADOW_PIN[:10]
                                     and (man.get("shadow_provenance") or {}).get("pin") == SH.SHADOW_PIN
                                     else f"FAIL: {board['provenance'].get('git_sha')}")
        ok["board_hash"] = "PASS" if VE.H.canonical_board_hash(board) == board["board_sha256"] == man["shadow_board_sha256"] else "FAIL"
        CP.verify_capture(cap)
        ok["capture_hash"] = "PASS" if cap["capture_sha256"] == man["capture_sha256"] else "FAIL"
        ok["schedule_hash"] = "PASS" if CP.canonical_sha256(sched) == man["schedule_sha256"] else "FAIL"
        M3.verify_manifest(man)
        rebuilt = M3.build_manifest(board, cap, sched, window=man["window"], cutoff_utc=man["cutoff_utc"],
                                    shadow_provenance=man["shadow_provenance"])
        ok["manifest_hash"] = "PASS" if man["manifest_sha256"] == summ["manifest_sha256"] == meta["manifest_sha256"] else "FAIL"
        ok["manifest_reproducible"] = "PASS" if rebuilt["manifest_sha256"] == man["manifest_sha256"] else "FAIL"
    except (ValueError, KeyError) as exc:
        fail("sealed_inputs", str(exc))
    prov = man.get("shadow_provenance") or {}
    ov = prov.get("overlay") or {}
    overlay_bytes = open(os.path.join(d, "overlay.json"), "rb").read() if "overlay.json" in arts else None
    ok["overlay"] = "PASS" if (overlay_bytes is None) == (ov.get("sealed_sha256") is None) and (
        overlay_bytes is None or (_sha(os.path.join(d, "overlay.json")) == ov["sealed_sha256"] and all(
            SH._safe_le(s.get("taken_at"), M3.utc(ov["overlay_cutoff"])) for s in json.loads(overlay_bytes).get("snapshots") or []))) else "FAIL"
    ok["tape_identity"] = "PASS" if prov.get("tape_sha256") == arts.get("shadow_tape.json.gz") else "FAIL"
    tsa = summ.get("drill_tsa_over_manifest_sha256") or {}
    tchk = {}
    for name, tok in tsa.items():
        b64 = tok if isinstance(tok, str) and not tok.startswith("FAILED") else None
        gen = SL.tsa_check(man["manifest_sha256"], b64, name) if b64 else None
        tchk[name] = (str(gen) if gen else "INVALID_OR_MISSING")
    pregame = [n for n, g in tchk.items() if g != "INVALID_OR_MISSING" and M3.utc(g) < M3.utc(summ["first_pitch_utc"])]
    res["tsa"] = tchk
    ok["timestamps"] = "PASS" if len(pregame) == len(tsa) >= 1 else f"FAIL: {tchk}"
    detail = {}
    keep = os.path.join(os.path.dirname(os.path.abspath(a.result)), os.path.basename(os.path.normpath(a.drill_dir)) + "_REPLAY_B") if a.result else None
    VE.replay_shadow(d, board, overlay_bytes, detail, keep_dir=keep)
    rep = detail.get("replay_env") or {}
    rec_fp = _load(d, "shadow_env.json") if os.path.exists(os.path.join(d, "shadow_env.json")) else {}
    trace = rep.get("process_trace") or {}
    lit = detail.get("literal_board") or {}
    res["replay"] = {"misses": rep.get("replay_misses"), "unconsumed": rep.get("unconsumed"),
                     "n_http": rep.get("n_http"), "guard_violations": (rep.get("guard") or {}).get("violations"),
                     "environment_mismatch": detail.get("environment_mismatch"), "error": detail.get("replay_error"),
                     "record_nonconformance": detail.get("record_nonconformance"),
                     "replay_env": {k: rep.get(k) for k in ("a1_version", "python", "machine", "lock_sha256", "installed_set_sha256",
                                                            "isolation", "git_identity")},
                     "retained_outputs_dir": os.path.basename(keep) if keep else None}
    res["literal_board"] = lit
    res["r5_process_trace"] = {k: trace.get(k) for k in ("method", "n_successful_calls", "executables", "by_class",
                                                         "frozen_r5_permitted_classes", "frozen_r5_violation_classes",
                                                         "frozen_r5_violations", "examples")}
    res["diagnostics"] = {"canonical_equivalence_NOT_THE_CRITERION": detail.get("canonical_equivalence_diagnostic_only")}
    # integrity: the sealed evidence is intact and the replay consumed exactly the tape under the same locked env
    ok["replay_tape_exact"] = ("PASS" if rep.get("replay_misses") == 0 and rep.get("unconsumed") == 0
                               and not detail.get("replay_error") and not (rep.get("guard") or {}).get("violations") else
                               f"FAIL: misses={rep.get('replay_misses')} unconsumed={rep.get('unconsumed')} error={detail.get('replay_error')}")
    ok["replay_environment"] = "PASS" if rep and not detail.get("environment_mismatch") else f"FAIL: {detail.get('environment_mismatch') or 'no replay env'}"
    # frozen conformance: the FC-MLB-001A acceptance criteria exactly as frozen (never canonicalized or waived)
    ok["literal_board_identity"] = "PASS" if lit.get("identical") else f"FAIL: differing fields {lit.get('differing_fields')}"
    ok["r5_process_trace_frozen_surfaces"] = ("PASS" if trace and trace.get("frozen_r5_violations") == 0 else
                                              f"FAIL: {trace.get('frozen_r5_violations')} reads/execs outside the pinned tree + "
                                              f"isolated HOME: {trace.get('frozen_r5_violation_classes')}")
    conf = ISO.check_record_conformance(rec_fp) if rec_fp else ["no sealed record fingerprint"]
    ok["record_conforms_to_current_a1"] = "PASS" if not conf else f"FAIL: {conf}"
    rtrace = rec_fp.get("process_trace")
    ok["r5_record_process_trace"] = ("PASS" if rtrace and rtrace.get("frozen_r5_violations") == 0 else
                                     "FAIL: " + ("record was not traced (recorded before the process-tree trace existed)" if not rtrace
                                                 else f"{rtrace.get('frozen_r5_violations')} frozen-R5 violations at record"))
    res["identities"] = {k: meta.get(k) for k in ("amendment_commit", "amendment_v3_tree", "shadow_pin", "lock_sha256",
                                                  "installed_set_sha256", "manifest_sha256")}
    res["hashes"] = {"shadow_board_sha256": board.get("board_sha256"), "capture_sha256": cap.get("capture_sha256"),
                     "schedule_sha256": man.get("schedule_sha256"), "manifest_sha256": man.get("manifest_sha256"),
                     "tape_sha256": arts.get("shadow_tape.json.gz")}
    res["verifier_environment"] = {"python": platform.python_version(), "platform": platform.platform()}
    res["gates"] = {g: ("PASS" if all(ok.get(k, "FAIL: missing") == "PASS" for k in ks) else "FAIL") for g, ks in GATES.items()}
    res["result"] = "PASS" if all(v == "PASS" for v in ok.values()) and all(v == "PASS" for v in res["gates"].values()) else "FAIL"
    if a.result:
        with open(a.result, "w") as fh:
            json.dump(res, fh, indent=1, sort_keys=True)
    print(json.dumps(res, indent=1, sort_keys=True))
    return 0 if res["result"] == "PASS" else 1


def legacy(a):
    """Read-only: replay each existing (pre-A1) sealed unit from its sealed artifacts in a clean isolated
    environment. Expected: none reproduces (they are DESCRIPTIVE SHADOW, NOT REPRODUCIBLE). Never writes there."""
    out = {"label": "FC-MLB-001A legacy-unit check (read-only; seals untouched)", "units": {}}
    for unit in sorted(os.listdir(os.path.join(a.evidence_dir, "seals"))):
        d = os.path.join(a.evidence_dir, "seals", unit)
        before = {n: _sha(os.path.join(d, n)) for n in sorted(os.listdir(d))}
        board = _load(d, "shadow_board.json.gz")
        ov = os.path.join(d, "overlay.json")
        detail = {}
        exact = VE.replay_shadow(d, board, open(ov, "rb").read() if os.path.exists(ov) else None, detail)
        rep = detail.get("replay_env") or {}
        after = {n: _sha(os.path.join(d, n)) for n in sorted(os.listdir(d))}
        out["units"][unit] = {"reproducible": bool(exact), "replay_misses": rep.get("replay_misses"),
                              "unconsumed": rep.get("unconsumed"), "legacy_unit": detail.get("legacy_unit"),
                              "error": detail.get("replay_error"), "unit_files_unchanged": before == after,
                              "seal_sha256": json.load(open(os.path.join(d, "seal.json"))).get("seal_sha256")}
    out["all_not_reproducible"] = all(not u["reproducible"] for u in out["units"].values())
    out["all_unchanged"] = all(u["unit_files_unchanged"] for u in out["units"].values())
    with open(a.result, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps(out, indent=1, sort_keys=True))
    return 0 if out["all_not_reproducible"] and out["all_unchanged"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--repo", required=True)
    r.add_argument("--date", required=True)
    r.add_argument("--window", choices=("DAY", "NIGHT"), required=True)
    r.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--drill-dir", required=True)
    v.add_argument("--result")
    lg = sub.add_parser("legacy")
    lg.add_argument("--evidence-dir", required=True)
    lg.add_argument("--result", required=True)
    a = ap.parse_args(argv)
    return {"record": record, "verify": verify, "legacy": legacy}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())

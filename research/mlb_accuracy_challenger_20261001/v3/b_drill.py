#!/usr/bin/env python3
"""FC-MLB-001B EVIDENCE-INTEGRITY DRILL -- NOT A PROSPECTIVE UNIT.

  record:  sudo python3 b_drill.py record --repo R --date YYYY-MM-DD --window DAY|NIGHT --out DIR/B_DRILL_<...>
                                           --store LOCAL_STORE_DIR --transport-out TRANSPORT_DIR
           ENVIRONMENT A (a fresh GitHub-hosted runner job). runner.py --mode drill --with-tsa (no evidence-ref push, no
           #91 receipt, never a chain unit) in the 001B pinned-runtime sandbox. The complete tape goes to the
           create-only content-addressed store (isolated local store; R2 is not authorized) and NEVER into the drill
           directory or git. One copy, named fc-mlb-001b-drill-tape-<sha256>.json.gz, is placed in TRANSPORT_DIR and
           uploaded by the workflow as ONE GitHub Actions artifact (temporary, non-authoritative drill transport;
           SUPERCHAD disposition 2026-10-05: no git ref, no git history). Writes B_DRILL.json and SHA256SUMS.
  verify:  sudo python3 b_drill.py verify --drill-dir DIR --tape-file DOWNLOADED_TAPE [--record B_DRILL_RECORD.json]
                                          [--result OUT.json]
           ENVIRONMENT B, from the sealed drill artifacts alone + the tape retrieved BY EXACT IDENTITY (size + sha256
           verified before use): every artifact hash and binding, manifest re-resolution, overlay cutoff, sealed
           scientific payload (frozen spec), envelope chronology incl. TSA tokens over the manifest digest, a fresh
           pinned-runtime replay with no network, LITERAL scientific-payload identity, runtime/dependency identity,
           zero forbidden/unclassified reads (record + replay + setup traces), enumerated kernel surfaces.
           Exit 0 iff every check passes.

Nothing here touches CHAIN.json, the evidence ref, the dispatcher, the trigger, an activation or any slate unit.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import capture as CP  # noqa: E402
import manifest_v3 as M3  # noqa: E402
import payload as PL  # noqa: E402
import runtime_image as RI  # noqa: E402
import sandbox as SB  # noqa: E402
import seal as SL  # noqa: E402
import shadow as SH  # noqa: E402
import tape_store as TS  # noqa: E402
import verify_evidence as VE  # noqa: E402

LABEL = "FC-MLB-001B EVIDENCE-INTEGRITY DRILL — NOT A PROSPECTIVE UNIT"
GATES = {
    "integrity": ("frozen_record_identity", "sha256sums", "artifact_set", "provenance_identity", "board_hash", "capture_hash", "schedule_hash",
                  "manifest_hash", "manifest_reproducible", "overlay", "payload_sealed", "tape_identity",
                  "envelope_chronology"),
    "replay": ("tape_retrieved_verified", "replay_exact", "payload_literal_identity", "runtime_dependency_identity",
               "no_forbidden_reads", "kernel_surfaces_enumerated"),
}


def _sha(p):
    return SB._sha_file(p)


def _git(*a):
    return subprocess.check_output(["git", "-c", f"safe.directory={HERE}", "-C", HERE, *a]).decode().strip()


def _load(d, n):
    return json.load(gzip.open(os.path.join(d, n))) if n.endswith(".gz") else json.load(open(os.path.join(d, n)))


def write_sums(d):
    names = sorted(n for n in os.listdir(d) if n != "SHA256SUMS")
    with open(os.path.join(d, "SHA256SUMS"), "w") as fh:
        fh.writelines(f"{_sha(os.path.join(d, n))}  {n}\n" for n in names)


def record(a):
    import runner as RN
    if not os.path.basename(os.path.normpath(a.out)).startswith("B_DRILL_"):
        raise SystemExit("drill output directory must be named B_DRILL_* (never a slate-unit name)")
    os.environ["V3B_TAPE_STORE"] = "localfs:" + os.path.abspath(a.store)
    rc = RN.main(["--mode", "drill", "--with-tsa", "--repo", a.repo, "--date", a.date, "--window", a.window, "--out", a.out])
    if rc != 0 or not os.path.exists(os.path.join(a.out, "DRILL_SUMMARY.json")):
        raise SystemExit(f"drill record did not complete (runner rc={rc})")
    tape = os.path.join(a.out, "shadow_tape.json.gz")
    summ = _load(a.out, "DRILL_SUMMARY.json")
    loc = summ["tape_store"]
    if TS.file_identity(tape) != (loc["sha256"], loc["bytes"]):
        raise SystemExit("local tape is not the stored tape identity")
    os.makedirs(a.transport_out, exist_ok=True)                              # ONE file, named by its sha256
    shutil.copyfile(tape, os.path.join(a.transport_out, TS.transport_asset_name(loc["sha256"])))
    os.rename(tape + ".record.report.json", os.path.join(a.out, "record_report.json"))
    for leftover in (tape, tape + ".record.env.json", tape + ".record.strace", tape + ".record.setup.strace"):
        if os.path.exists(leftover):
            os.remove(leftover)                                             # tape: in the store; env: = shadow_env.json
    env = _load(a.out, "shadow_env.json")
    meta = {"label": LABEL, "not_a_prospective_unit": True,
            "evidence_chain": "NOT APPENDED (drill mode: no push, no #91 receipt, no seal)",
            "slate_used_for_live_inputs": {"date": a.date, "window": a.window},
            "amendment_commit": _git("rev-parse", "HEAD"),
            "amendment_v3_tree": _git("rev-parse", "HEAD:research/mlb_accuracy_challenger_20261001/v3"),
            "criteria": "ops e5475e66c0 engineering/ops/TASKS/FC-MLB-001B.md sha256 f8efd4b563751731c6d78ce8949eebcf6e9745317c990cde9220febbe68ec652",
            "shadow_pin": SH.SHADOW_PIN, "shadow_tree": SH.SHADOW_TREE, "runtime_image": env["runtime_image"],
            "lock_sha256": env["lock_sha256"], "installed_set_sha256": env["installed_set_sha256"],
            "scientific_payload_spec": PL.SPEC, "scientific_payload_sha256": summ["scientific_payload_sha256"],
            "manifest_sha256": summ["manifest_sha256"], "artifacts_sha256": summ["artifacts_sha256"],
            "tape_store_sealed": loc,
            "tape_transport": {"store": TS.ARTIFACT_KIND, "asset_name": TS.transport_asset_name(loc["sha256"]),
                               "key": loc["key"], "sha256": loc["sha256"], "bytes": loc["bytes"],
                               "note": "drill-only, non-authoritative GitHub Actions artifact carrying the SAME content "
                                       "identity; bytes verified (size + sha256) before replay"},
            "record_environment": {"python": env["python"], "kernel": env["kernel_surfaces"], "user": env["user"],
                                   "network": env["network"], "isolation": env["isolation"], "n_http_recorded": env["n_http"],
                                   "forbidden_reads": env["process_trace"]["forbidden_reads"],
                                   "trace_by_class": env["process_trace"]["by_class"],
                                   "platform": platform.platform()}}
    with open(os.path.join(a.out, "B_DRILL.json"), "w") as fh:
        json.dump(meta, fh, indent=1, sort_keys=True)
    write_sums(a.out)
    print(json.dumps(meta, indent=1, sort_keys=True))
    return 0


def verify(a):
    d = a.drill_dir
    res = {"label": LABEL, "checks": {}}
    ok = res["checks"]
    try:
        _verify(a, d, res, ok)
    except Exception as exc:  # noqa: BLE001 -- any crash is a failed verification, never a pass
        ok["verifier_crash"] = f"FAIL: {type(exc).__name__}: {exc}"[:800]
    res["gates"] = {g: ("PASS" if all(ok.get(k, "FAIL: not evaluated") == "PASS" for k in ks) else "FAIL")
                    for g, ks in GATES.items()}
    res["result"] = "PASS" if all(v == "PASS" for v in ok.values()) and all(v == "PASS" for v in res["gates"].values()) else "FAIL"
    res["verifier_environment"] = {"python": platform.python_version(), "platform": platform.platform(),
                                   "kernel": SB.kernel_fingerprint()}
    if a.result:
        with open(a.result, "w") as fh:
            json.dump(res, fh, indent=1, sort_keys=True)
    print(json.dumps(res, indent=1, sort_keys=True))
    return 0 if res["result"] == "PASS" else 1


def _verify(a, d, res, ok):
    fail = lambda k, why: ok.__setitem__(k, f"FAIL: {why}")
    sums_text = open(os.path.join(d, "SHA256SUMS")).read()
    if a.record:        # identities frozen in git (committed from record A's annotations) -- the only trust anchor
        rec = json.load(open(a.record))
        tt = (rec.get("tape") or {})
        problems = []
        if rec.get("sha256sums") != sums_text:
            problems.append("drill files differ from the committed SHA256SUMS")
        if a.tape_file and os.path.basename(a.tape_file) != tt.get("asset_name"):
            problems.append("downloaded tape is not the committed transport asset")
        ok["frozen_record_identity"] = "PASS" if not problems else f"FAIL: {problems}"
        res["frozen_record"] = {k: rec.get(k) for k in ("record_run_id", "files_artifact", "tape", "drill")}
    else:
        ok["frozen_record_identity"] = "FAIL: no committed B_DRILL_RECORD.json given (--record)"
    sums = [ln.split("  ", 1) for ln in sums_text.splitlines()]
    bad = [n for h, n in sums if not os.path.exists(os.path.join(d, n)) or _sha(os.path.join(d, n)) != h]
    unlisted = sorted(set(os.listdir(d)) - {n for _, n in sums} - {"SHA256SUMS"})
    ok["sha256sums"] = "PASS" if not bad and not unlisted else f"FAIL: changed={bad} unlisted={unlisted}"
    summ, meta = _load(d, "DRILL_SUMMARY.json"), _load(d, "B_DRILL.json")
    arts = summ["artifacts_sha256"]
    need = set(VE.B_REQUIRED_ARTIFACTS)
    if not need <= set(arts) or set(arts) - need - set(VE.OPTIONAL_ARTIFACTS) or "shadow_tape.json.gz" in os.listdir(d):
        fail("artifact_set", sorted(arts))
    else:
        mism = [n for n, h in arts.items() if _sha(os.path.join(d, n)) != h]
        ok["artifact_set"] = "PASS" if not mism and arts == meta["artifacts_sha256"] else f"FAIL: {mism}"
    board, cap, sched, man = (_load(d, n) for n in ("shadow_board.json.gz", "capture.json.gz", "schedule.json", "manifest.json.gz"))
    prov = man.get("shadow_provenance") or {}
    try:
        SH.verify_shadow_board(board)
        ok["provenance_identity"] = ("PASS" if board["provenance"]["git_sha"] == SH.SHADOW_PIN[:10]
                                     and prov.get("pin") == SH.SHADOW_PIN else f"FAIL: {board['provenance'].get('git_sha')}")
        ok["board_hash"] = "PASS" if VE.H.canonical_board_hash(board) == board["board_sha256"] == man["shadow_board_sha256"] else "FAIL"
        CP.verify_capture(cap)
        ok["capture_hash"] = "PASS" if cap["capture_sha256"] == man["capture_sha256"] else "FAIL"
        ok["schedule_hash"] = "PASS" if CP.canonical_sha256(sched) == man["schedule_sha256"] else "FAIL"
        M3.verify_manifest(man)
        ok["manifest_hash"] = "PASS" if man["manifest_sha256"] == summ["manifest_sha256"] == meta["manifest_sha256"] else "FAIL"
        rebuilt = M3.build_manifest(board, cap, sched, window=man["window"], cutoff_utc=man["cutoff_utc"], shadow_provenance=prov)
        ok["manifest_reproducible"] = "PASS" if rebuilt["manifest_sha256"] == man["manifest_sha256"] else "FAIL"
    except (ValueError, KeyError) as exc:
        fail("sealed_inputs", str(exc))
    ov = prov.get("overlay") or {}
    overlay_bytes = open(os.path.join(d, "overlay.json"), "rb").read() if "overlay.json" in arts else None
    ok["overlay"] = "PASS" if (overlay_bytes is None) == (ov.get("sealed_sha256") is None) and (
        overlay_bytes is None or (hashlib.sha256(overlay_bytes).hexdigest() == ov["sealed_sha256"] and all(
            SH._safe_le(s.get("taken_at"), M3.utc(ov["overlay_cutoff"])) for s in json.loads(overlay_bytes).get("snapshots") or []))) else "FAIL"
    probs = VE.sealed_b_identity_problems(d, board, prov)
    ok["payload_sealed"] = "PASS" if not [p for p in probs if "payload" in p or "runtime" in p or "trace" in p] else f"FAIL: {probs}"
    loc, tr = prov.get("tape_store") or {}, meta.get("tape_transport") or {}
    ok["tape_identity"] = ("PASS" if not [p for p in probs if "tape" in p] and loc.get("sha256") == summ["tape_sha256"]
                           and all(tr.get(k) == loc.get(k) for k in ("key", "sha256", "bytes")) else f"FAIL: {probs} {loc} {tr}")
    tsa = summ.get("drill_tsa_over_manifest_sha256") or {}
    tchk = {}
    for name, tok in tsa.items():
        b64 = tok if isinstance(tok, str) and not tok.startswith("FAILED") else None
        gen = SL.tsa_check(man["manifest_sha256"], b64, name) if b64 else None
        if gen:
            tchk[name] = str(gen).replace(" ", "T")
    res["tsa"] = tchk
    chrono = PL.envelope_chronology(board, man["cutoff_utc"], tchk, summ["first_pitch_utc"], VE.H.canonical_board_hash)
    if len(tchk) != len(tsa) or not tsa:
        chrono.append(f"TSA tokens invalid or missing: {sorted(set(tsa) - set(tchk))}")
    ok["envelope_chronology"] = "PASS" if not chrono else f"FAIL: {chrono}"
    res["envelope"] = {"board_generated_at": board.get("board_generated_at"), "sealed_at": board.get("sealed_at"),
                       "manifest_cutoff_utc": man.get("cutoff_utc"), "tsa": tchk, "first_pitch_utc": summ["first_pitch_utc"]}
    # ---- environment B replay --------------------------------------------------------------------------------------
    detail = {}
    keep = os.path.join(os.path.dirname(os.path.abspath(a.result)), os.path.basename(os.path.normpath(d)) + "_REPLAY_B") if a.result else None
    if a.record:                                  # the tape identity B must find is the committed one
        tt = json.load(open(a.record)).get("tape") or {}
        if any(tt.get(k) != loc.get(k) for k in ("sha256", "bytes", "key")):
            fail("tape_identity", f"committed tape identity {tt} != sealed {loc}")
    transport = dict(tr, local_path=os.path.abspath(a.tape_file)) if tr and a.tape_file else dict(tr or {}, local_path=None)
    VE.replay_shadow_b(d, board, overlay_bytes, prov, detail, keep_dir=keep, tape_locator=transport)
    rep = detail.get("replay_env") or {}
    rec = _load(d, "shadow_env.json")
    pay = detail.get("payload") or {}
    ok["tape_retrieved_verified"] = "PASS" if (detail.get("tape_fetched") or {}).get("verified") else f"FAIL: {detail.get('replay_error')}"
    ok["replay_exact"] = ("PASS" if rep.get("replay_misses") == 0 and rep.get("unconsumed") == 0 and not detail.get("replay_error")
                          else f"FAIL: misses={rep.get('replay_misses')} unconsumed={rep.get('unconsumed')} error={detail.get('replay_error')}")
    ok["payload_literal_identity"] = ("PASS" if pay.get("literal_identical") and pay.get("record_payload_sha256") ==
                                      prov.get("scientific_payload_sha256") == pay.get("replay_payload_sha256")
                                      else f"FAIL: {pay}")
    ok["runtime_dependency_identity"] = ("PASS" if rep and not detail.get("environment_mismatch")
                                         and rec["runtime_image"]["manifest_digest"] == RI.MANIFEST_DIGEST
                                         else f"FAIL: {detail.get('environment_mismatch')}")
    traces = {"record": rec.get("process_trace"), "replay": rep.get("process_trace"),
              "record_setup": rec.get("setup_trace"), "replay_setup": rep.get("setup_trace")}
    nforb = [(t or {}).get("forbidden_reads") for t in traces.values()]
    ok["no_forbidden_reads"] = "PASS" if nforb and all(n == 0 for n in nforb) else f"FAIL: {nforb}"
    kern = {s: {"kernel": (fp.get("kernel_surfaces") or {}), "virtual_paths": (fp.get("process_trace") or {}).get("kernel_virtual_paths"),
                "n": (fp.get("process_trace") or {}).get("n_kernel_virtual_paths")} for s, fp in (("record", rec), ("replay", rep))}
    ok["kernel_surfaces_enumerated"] = ("PASS" if all(v["kernel"].get("kernel_release") and v["virtual_paths"] is not None
                                                      for v in kern.values()) else "FAIL")
    res["replay"] = {"misses": rep.get("replay_misses"), "unconsumed": rep.get("unconsumed"), "n_http": rep.get("n_http"),
                     "error": detail.get("replay_error"), "environment_mismatch": detail.get("environment_mismatch"),
                     "network": rep.get("network"), "user": rep.get("user"), "tape_fetched": detail.get("tape_fetched"),
                     "retained_outputs_dir": os.path.basename(keep) if keep else None}
    res["payload"] = pay
    res["traces"] = {k: {kk: (v or {}).get(kk) for kk in ("n_successful_calls", "executables", "by_class",
                                                          "forbidden_or_unclassified", "forbidden_reads", "launcher_pre_chroot")}
                     for k, v in traces.items()}
    res["kernel_surfaces"] = kern
    res["identities"] = {"runtime_image_digest": rec.get("runtime_image", {}).get("manifest_digest"),
                         "runtime_rootfs_tree_sha256": rec.get("runtime_image", {}).get("rootfs_tree_sha256"),
                         "replay_runtime_image_digest": (rep.get("runtime_image") or {}).get("manifest_digest"),
                         "replay_rootfs_tree_sha256": (rep.get("runtime_image") or {}).get("rootfs_tree_sha256"),
                         "lock_sha256": rec.get("lock_sha256"), "installed_set_sha256": rec.get("installed_set_sha256"),
                         "replay_installed_set_sha256": rep.get("installed_set_sha256"), "python": rec.get("python"),
                         "git_identity": rec.get("git_identity"), "amendment_commit": meta.get("amendment_commit"),
                         "amendment_v3_tree": meta.get("amendment_v3_tree"), "shadow_pin": SH.SHADOW_PIN}
    res["hashes"] = {"scientific_payload_sha256": prov.get("scientific_payload_sha256"),
                     "shadow_board_sha256_envelope": board.get("board_sha256"), "capture_sha256": cap.get("capture_sha256"),
                     "schedule_sha256": man.get("schedule_sha256"), "manifest_sha256": man.get("manifest_sha256"),
                     "tape_sha256": prov.get("tape_sha256"), "tape_bytes": loc.get("bytes")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--repo", required=True)
    r.add_argument("--date", required=True)
    r.add_argument("--window", choices=("DAY", "NIGHT"), required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--store", required=True)
    r.add_argument("--transport-out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--drill-dir", required=True)
    v.add_argument("--tape-file", help="the downloaded drill transport file (environment B)")
    v.add_argument("--record", help="committed B_DRILL_RECORD.json: the frozen identities")
    v.add_argument("--result")
    a = ap.parse_args(argv)
    return {"record": record, "verify": verify}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())

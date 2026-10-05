#!/usr/bin/env python3
"""MLB challenger v3 runner -- prereg v3 sections 4, 7, 16. BUILT, NOT ACTIVATED.

One invocation handles one slate unit (date, window) and does only:
  1. frozen shadow champion run, RECORDED (netrecord tape + sealed game-line overlay);
  2. dedicated FanDuel capture + MLB schedule snapshot (with rosters);
  3. manifest (cutoff = now, after capture completion);
  4. prospective mode only: append the seal to the evidence ref, #91 receipt, two
     RFC 3161 tokens, then verify the receipts from raw evidence;
  5. stop.
Before every step schedule_plan.guard() must pass, else MISS UNIT (no seal, no
confirmatory use, no backfill). It never reads outcomes, never grades, never
edits the prereg/challengers/old seals.

Modes:
  --mode drill        local only, no push, no #91 post; label DRILL_NONCONFIRMATORY.
  --mode prospective  only if activation.verify_activation() passes (exact Jacob
                      comment + exact prereg + exact code). No activation exists.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import activation as AC  # noqa: E402
import capture as CP  # noqa: E402
import manifest_v3 as M3  # noqa: E402
import schedule_plan as SP  # noqa: E402
import seal as SL  # noqa: E402
import shadow as SH  # noqa: E402
import verify_evidence as VE  # noqa: E402

MIN_LEAD_S = SP.SAFETY_S


def now():
    return datetime.now(timezone.utc).isoformat()


def schedule_snapshot(date):
    import requests
    r = requests.get("https://statsapi.mlb.com/api/v1/schedule",
                     params={"sportId": 1, "date": date, "hydrate": "probablePitcher,team"}, timeout=30)
    r.raise_for_status()
    games = []
    for d in r.json().get("dates") or []:
        for g in d.get("games") or []:
            t = g["teams"]
            rosters = {}
            for side in ("away", "home"):
                try:
                    rr = requests.get(f"https://statsapi.mlb.com/api/v1/teams/{t[side]['team']['id']}/roster",
                                      params={"rosterType": "active", "date": date}, timeout=30)
                    rr.raise_for_status()
                    rosters[side] = [{"id": p["person"]["id"], "name": p["person"]["fullName"]}
                                     for p in rr.json().get("roster") or []]
                except Exception:  # noqa: BLE001 -- a missing roster only removes the slug-less fallback proof
                    rosters[side] = None
            games.append({"game_pk": g["gamePk"], "game_type": g.get("gameType"), "game_date": g["gameDate"],
                          "start_time_tbd": bool((g.get("status") or {}).get("startTimeTBD")),
                          "double_header": g.get("doubleHeader"), "game_number": g.get("gameNumber"),
                          "away_team": t["away"]["team"]["name"], "home_team": t["home"]["team"]["name"],
                          "probable_pitcher_ids": [x["probablePitcher"]["id"] for x in (t["away"], t["home"])
                                                   if x.get("probablePitcher")],
                          "rosters": rosters, "status": (g.get("status") or {}).get("detailedState")})
    return {"source": "statsapi.mlb.com/api/v1/schedule", "date": date, "fetched_at": now(), "games": games}


def _dump(path, obj, gz=False):
    data = json.dumps(obj, indent=1, sort_keys=True).encode()
    with (gzip.GzipFile(path, "wb", mtime=0) if gz else open(path, "wb")) as fh:
        fh.write(data)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("drill", "prospective"), required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--window", choices=("DAY", "NIGHT"), required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--with-tsa", action="store_true", help="drill only: also request (labelled) TSA tokens")
    a = ap.parse_args(argv)
    if a.mode == "prospective":
        ok, reasons = AC.verify_activation(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
        if not ok:
            print(f"REFUSED: prospective v3 collection is not activated: {reasons}")
            return 2
    sched = schedule_snapshot(a.date)
    fp = SP.unit_first_pitch(sched, a.window)
    if fp is None:
        print("NO_UNIT: no timed games in this window")
        return 0
    os.makedirs(a.out, exist_ok=True)
    try:
        SP.guard(now(), fp, "shadow")
        tree = os.path.join(a.out, "shadow_tree")
        prov = SH.build_tree(a.repo, tree, a.date)
        try:
            tape = os.path.join(a.out, "shadow_tape.json.gz")
            board = SH.verify_shadow_board(json.load(open(SH.run_pipeline(tree, tape, "record"))))
            ov = os.path.join(tree, SH.LIVE_OVERLAY_TEMPLATE.format(date=a.date))
            if prov["overlay"].get("sealed_sha256"):
                shutil.copy(ov, os.path.join(a.out, "overlay.json"))
        finally:
            SH.remove_tree(a.repo, tree)
        prov["tape_sha256"] = SH.sha256_file(tape)
        SP.guard(now(), fp, "capture")
        cap = CP.capture()
        SP.guard(now(), fp, "manifest")
        cutoff = now()
        man = M3.build_manifest(board, cap, sched, window=a.window, cutoff_utc=cutoff, shadow_provenance=prov)
        if not SP.seal_deadline_consistent(man, sched, a.window):
            raise SP.MissUnit("no timed covered game or manifest deadline earlier than the planned deadline")
        SP.guard(now(), fp, "seal")
    except SP.MissUnit as exc:
        _dump(os.path.join(a.out, "MISSED_UNIT.json"), {"date": a.date, "window": a.window, "first_pitch_utc": fp,
                                                        "at": now(), "reason": str(exc),
                                                        "status": "MISS_UNIT_NO_CONFIRMATORY_USE_NO_BACKFILL"})
        print(f"MISS UNIT: {exc}")
        return 4
    _dump(os.path.join(a.out, "shadow_board.json.gz"), board, gz=True)
    _dump(os.path.join(a.out, "capture.json.gz"), cap, gz=True)
    _dump(os.path.join(a.out, "schedule.json"), sched)
    _dump(os.path.join(a.out, "manifest.json.gz"), man, gz=True)
    env_fp = os.path.join(a.out, "shadow_tape.json.gz.record.env.json")      # FC-MLB-001A: sealed record environment
    if not os.path.exists(env_fp):
        raise RuntimeError("A1: record-environment fingerprint missing; refusing to seal")
    shutil.copy(env_fp, os.path.join(a.out, "shadow_env.json"))
    names = list(VE.REQUIRED_ARTIFACTS) + [n for n in VE.OPTIONAL_ARTIFACTS if os.path.exists(os.path.join(a.out, n))]
    arts = {n: SH.sha256_file(os.path.join(a.out, n)) for n in names}
    summary = {"label": "DRILL_NONCONFIRMATORY" if a.mode == "drill" else "PROSPECTIVE", "date": a.date,
               "window": a.window, "cutoff_utc": cutoff, "first_pitch_utc": fp, "manifest_sha256": man["manifest_sha256"],
               "capture_status": cap["status"], "capture_completed_at": cap["capture_completed_at"],
               "overlay": prov["overlay"], "tape_sha256": prov["tape_sha256"], "artifacts_sha256": arts,
               "counts": man["counts"]}
    if a.mode == "drill":
        if a.with_tsa:
            summary["drill_tsa_over_manifest_sha256"] = {n: _try_tsa(man["manifest_sha256"], u) for n, u in SL.TSAS.items()}
        _dump(os.path.join(a.out, "DRILL_SUMMARY.json"), summary)
        print(json.dumps({k: v for k, v in summary.items() if k != "drill_tsa_over_manifest_sha256"}, indent=1))
        return 0
    status = publish_seal(a, man, arts, fp)
    return 0 if status == "ON_TIME" else 3


def _try_tsa(digest, url):
    try:
        return SL.tsa_request(digest, url)
    except Exception as exc:  # noqa: BLE001
        return f"FAILED: {exc}"


def _github_post_comment(body):
    import requests
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not tok:
        raise RuntimeError("no GitHub token: cannot post the external receipt (unit LOST)")
    r = requests.post(f"{VE.GITHUB_API}/issues/91/comments", json={"body": body}, timeout=30,
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r.json()["id"]


def _frozen_challenger_version():
    VE.load_frozen_coefficients()                        # raises unless the exact preregistered artifact
    return VE.FROZEN_COEFFICIENTS_SHA256


def publish_seal(a, man, arts, fp):
    """Prospective only (activation-gated). Append-only: one new unit directory + CHAIN entry; never force."""
    ev = os.path.join(a.out, "evidence")
    subprocess.run(["git", "-C", a.repo, "fetch", "origin", SL.EVIDENCE_REF], check=True)
    subprocess.run(["git", "-C", a.repo, "worktree", "add", "--detach", ev, f"origin/{SL.EVIDENCE_REF}"], check=True)
    chain = json.load(open(os.path.join(ev, "CHAIN.json")))
    VE.load_chain(ev)                                    # never append to a broken chain
    unit = f"{man['date']}_{man['window']}"
    unit_dir = os.path.join(ev, "seals", unit)
    if os.path.exists(unit_dir):
        raise RuntimeError("slate unit already sealed; superseding is not allowed")
    seal = SL.build_seal(man, prev_seal_sha256=chain[-1]["seal_sha256"], prereg_sha256=VE.PREREG_SHA256,
                         challenger_sha256=_frozen_challenger_version(),
                         shadow_id=SH.SHADOW_ID, created_at=now(), artifacts_sha256=arts)
    os.makedirs(unit_dir)
    for name in arts:
        shutil.copy(os.path.join(a.out, name), unit_dir)
    _dump(os.path.join(unit_dir, "seal.json"), seal)
    chain.append({"index": len(chain), "unit": unit, "seal_sha256": seal["seal_sha256"]})
    _dump(os.path.join(ev, "CHAIN.json"), chain)
    SP.guard(now(), fp, "seal")
    for cmd in (["add", "-A"], ["commit", "-m", f"MLB v3 seal {unit} {seal['seal_sha256'][:12]}"],
                ["push", "origin", f"HEAD:{SL.EVIDENCE_REF}"]):
        subprocess.run(["git", "-C", ev, *cmd], check=True)
    receipts = {"github_comment_id": _github_post_comment(SL.receipt_comment_body(seal)),
                "tsa": [{"tsa": n, "token_b64": _try_tsa(seal["seal_sha256"], u)} for n, u in SL.TSAS.items()]}
    _dump(os.path.join(unit_dir, "receipts.json"), receipts)
    for cmd in (["add", "-A"], ["commit", "-m", f"MLB v3 receipts {unit}"], ["push", "origin", f"HEAD:{SL.EVIDENCE_REF}"]):
        subprocess.run(["git", "-C", ev, *cmd], check=True)
    status, detail = SL.verify_receipts_raw(seal, VE.fetch_issue_comment(receipts["github_comment_id"]),
                                            receipts["tsa"], earliest_first_pitch_utc=man["earliest_first_pitch_utc"])
    print(json.dumps({"unit": unit, "seal_sha256": seal["seal_sha256"], "status": status, "detail": detail}, default=str))
    return status


if __name__ == "__main__":
    sys.exit(main())

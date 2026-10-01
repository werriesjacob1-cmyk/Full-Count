#!/usr/bin/env python3
"""MLB challenger v3 runner -- preregistration v3, section 16. BUILT, NOT ACTIVATED.

One invocation handles one slate unit (date, window) and does only:
  1. frozen shadow champion run (shadow.py) in a fresh pinned tree;
  2. dedicated FanDuel capture (capture.py) + MLB schedule snapshot;
  3. verify the capture completed;
  4. build the manifest (cutoff = now, after capture completion);
  5. seal externally BEFORE the earliest covered first pitch (prospective mode only):
     evidence-ref commit, Issue #91 receipt, two RFC 3161 timestamps;
  6. verify the receipts; stop.
It never reads outcomes, never grades, never edits the prereg or challengers,
and never rewrites an earlier seal.

Modes:
  --mode drill        local only; writes to --out; label DRILL_NONCONFIRMATORY;
                      no push, no #91 post. Allowed now.
  --mode prospective  REFUSED unless ACTIVATION.json (a Jacob authorization
                      record naming the exact #91 comment) exists next to this
                      file AND env MLB_V3_ACTIVATION=JACOB_AUTHORIZED. Neither
                      exists at the v3 lock.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import capture as CP  # noqa: E402
import manifest_v3 as M3  # noqa: E402
import seal as SL  # noqa: E402
import shadow as SH  # noqa: E402

ACTIVATION_FILE = os.path.join(HERE, "ACTIVATION.json")
MIN_LEAD_S = 10 * 60     # the whole sequence must finish this long before the earliest covered first pitch


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
            games.append({"game_pk": g["gamePk"], "game_type": g.get("gameType"), "game_date": g["gameDate"],
                          "double_header": g.get("doubleHeader"), "game_number": g.get("gameNumber"),
                          "away_team": t["away"]["team"]["name"], "home_team": t["home"]["team"]["name"],
                          "probable_pitcher_ids": [x["probablePitcher"]["id"] for x in (t["away"], t["home"])
                                                   if x.get("probablePitcher")],
                          "status": (g.get("status") or {}).get("detailedState")})
    return {"source": "statsapi.mlb.com/api/v1/schedule", "date": date, "fetched_at": now(), "games": games}


def activation_ok():
    if os.environ.get("MLB_V3_ACTIVATION") != "JACOB_AUTHORIZED" or not os.path.exists(ACTIVATION_FILE):
        return False
    rec = json.load(open(ACTIVATION_FILE))
    return bool(rec.get("authorized") is True and rec.get("issue_91_comment_id") and rec.get("prereg_commit"))


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
    if a.mode == "prospective" and not activation_ok():
        print("REFUSED: prospective v3 collection is not activated (needs Jacob's separate authorization).")
        return 2
    os.makedirs(a.out, exist_ok=True)
    tree = os.path.join(a.out, "shadow_tree")
    prov = SH.build_tree(a.repo, tree, a.date)
    try:
        return _run(a, tree, prov)
    finally:
        SH.remove_tree(a.repo, tree)


def _run(a, tree, prov):
    board = SH.verify_shadow_board(json.load(open(SH.run_pipeline(tree))))
    sched = schedule_snapshot(a.date)
    cap = CP.capture()
    if cap["status"] != "COMPLETE":
        print(f"capture status {cap['status']}: affected events will be EVENT_NOT_OBSERVED, never MARKET_ABSENT")
    cutoff = now()
    man = M3.build_manifest(board, cap, sched, window=a.window, cutoff_utc=cutoff, shadow_provenance=prov)
    label = "DRILL_NONCONFIRMATORY" if a.mode == "drill" else "PROSPECTIVE"
    _dump(os.path.join(a.out, "shadow_board.json.gz"), board, gz=True)
    _dump(os.path.join(a.out, "capture.json.gz"), cap, gz=True)
    _dump(os.path.join(a.out, "schedule.json"), sched)
    _dump(os.path.join(a.out, "manifest.json.gz"), man, gz=True)
    summary = {"label": label, "date": a.date, "window": a.window, "cutoff_utc": cutoff,
               "manifest_sha256": man["manifest_sha256"], "capture_status": cap["status"],
               "capture_started_at": cap["capture_started_at"], "capture_completed_at": cap["capture_completed_at"],
               "earliest_first_pitch_utc": man["earliest_first_pitch_utc"], "counts": man["counts"]}
    if a.mode == "drill":
        if a.with_tsa:
            tok = {}
            for name, url in SL.TSAS.items():
                try:
                    tok[name] = SL.tsa_request(man["manifest_sha256"], url)
                except Exception as exc:  # noqa: BLE001
                    tok[name] = f"FAILED: {exc}"
            summary["drill_tsa_tokens_over_manifest_sha256"] = tok
        _dump(os.path.join(a.out, "DRILL_SUMMARY.json"), summary)
        print(json.dumps(summary, indent=1))
        return 0
    status = publish_seal(a, man, summary)
    return 0 if status == "ON_TIME" else 3


def _github(method, path, body=None):
    import requests
    tok = os.environ.get("GITHUB_TOKEN")
    if not tok:
        raise RuntimeError("GITHUB_TOKEN not available: cannot post the external receipt (slate LOST)")
    r = requests.request(method, f"https://api.github.com/repos/werriesjacob1-cmyk/Full-Count/{path}", json=body,
                         headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                         timeout=30)
    r.raise_for_status()
    return r.json()


def publish_seal(a, man, summary):
    """Prospective only (activation-gated). Appends one seal to the evidence ref, posts the #91
    receipt, obtains two RFC 3161 tokens, re-fetches the receipt from GitHub, verifies timing."""
    import subprocess
    ev = os.path.join(a.out, "evidence")
    subprocess.run(["git", "-C", a.repo, "fetch", "origin", SL.EVIDENCE_REF], check=True)
    subprocess.run(["git", "-C", a.repo, "worktree", "add", "--detach", ev, f"origin/{SL.EVIDENCE_REF}"], check=True)
    chain = sorted(json.load(open(os.path.join(ev, "CHAIN.json"))), key=lambda x: x["index"])
    unit_dir = os.path.join(ev, "seals", f"{man['date']}_{man['window']}")
    if os.path.exists(unit_dir):
        raise RuntimeError("slate unit already sealed; superseding is not allowed")
    prereg = os.path.join(os.path.dirname(os.path.dirname(HERE)), "mlb_accuracy_challenger_prereg_v3_20261001.md")
    coef = os.path.join(os.path.dirname(HERE), "frozen_coefficients.json")
    seal = SL.build_seal(man, prev_seal_sha256=chain[-1]["seal_sha256"], prereg_sha256=SH.sha256_file(prereg),
                         challenger_sha256=SH.sha256_file(coef), shadow_id=SH.SHADOW_ID, created_at=now())
    os.makedirs(unit_dir)
    for name in ("shadow_board.json.gz", "capture.json.gz", "schedule.json", "manifest.json.gz"):
        subprocess.run(["cp", os.path.join(a.out, name), unit_dir], check=True)
    _dump(os.path.join(unit_dir, "seal.json"), seal)
    chain.append({"index": len(chain), "unit": f"{man['date']}_{man['window']}", "seal_sha256": seal["seal_sha256"]})
    _dump(os.path.join(ev, "CHAIN.json"), chain)
    subprocess.run(["git", "-C", ev, "add", "-A"], check=True)
    subprocess.run(["git", "-C", ev, "commit", "-m", f"MLB v3 seal {man['date']} {man['window']} {seal['seal_sha256'][:12]}"], check=True)
    subprocess.run(["git", "-C", ev, "push", "origin", f"HEAD:{SL.EVIDENCE_REF}"], check=True)   # never --force
    gh = _github("POST", "issues/91/comments", {"body": SL.receipt_comment_body(seal)})
    toks = []
    for name, url in SL.TSAS.items():
        try:
            t = SL.tsa_request(seal["seal_sha256"], url)
            toks.append({"tsa": name, "token_b64": t, "gen_time": SL.tsa_gen_time(t).isoformat()})
        except Exception as exc:  # noqa: BLE001
            toks.append({"tsa": name, "error": str(exc)})
    fetched = _github("GET", f"issues/comments/{gh['id']}")
    receipts = {"github": {"id": fetched["id"], "created_at": fetched["created_at"], "body": fetched["body"],
                           "html_url": fetched["html_url"]}, "tsa": toks}
    _dump(os.path.join(unit_dir, "receipts.json"), receipts)
    subprocess.run(["git", "-C", ev, "add", "-A"], check=True)
    subprocess.run(["git", "-C", ev, "commit", "-m", f"MLB v3 receipts {man['date']} {man['window']}"], check=True)
    subprocess.run(["git", "-C", ev, "push", "origin", f"HEAD:{SL.EVIDENCE_REF}"], check=True)
    check = {"github": receipts["github"],
             "tsa": [dict(t, verified=SL.tsa_verify_named(seal["seal_sha256"], t["token_b64"], t["tsa"])) for t in toks if "token_b64" in t]}
    status, detail = SL.verify_receipts(seal, check)
    print(json.dumps({"seal_sha256": seal["seal_sha256"], "status": status, "detail": detail}, default=str))
    return status


if __name__ == "__main__":
    sys.exit(main())

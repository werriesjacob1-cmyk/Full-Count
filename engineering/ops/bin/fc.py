#!/usr/bin/env python3
"""FULL COUNT ops CLI (stdlib only). Deterministic orientation, queue, blind challenge capsules,
compact checkpoints and change-only status polling.

Reads the ops state either from a checkout (when run as a file inside engineering/ops/bin) or,
when piped (`git show FETCH_HEAD:engineering/ops/bin/fc.py | python3 - ...`), from a git ref
(--ref, default FETCH_HEAD) via `git show`. Never writes the repo except `move` (checkout only).

  orient [TASK_ID] [--challenger]   CURRENT_STATE + board (+ capsule / blind challenge capsule)
  queue                             validate WORK_QUEUE.json (states, WIP, path conflicts); board
  capsule TASK_ID                   full (owner) capsule
  challenge-capsule TASK_ID         objective-only capsule for the challenger (no builder notes)
  checkpoint TASK_ID [--head SHA] [--tests TXT]   compact Issue #91 checkpoint
  move TASK_ID STATE --by AGENT     transition (checkout mode), validates and stamps UPDATED
  status [--quiet] [--until-change SEC --max-hours H]   change-only PR/CI/#91/branch/evidence poll
Exit codes: 0 ok/no change, 1 validation error, 2 runtime error, 3 status changed.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

OPS = "engineering/ops"
STATES = ("READY", "CLAUDE_ACTIVE", "CODEX_ACTIVE", "READY_FOR_CHALLENGE", "BLOCKED",
          "MINIMUM_REPAIR", "READY_FOR_SUPERCHAD", "DONE")
ACTIVE_BUILD = {"CLAUDE_ACTIVE", "CODEX_ACTIVE", "MINIMUM_REPAIR"}
TRANSITIONS = {
    "READY": {"CLAUDE_ACTIVE", "CODEX_ACTIVE", "BLOCKED"},
    "CLAUDE_ACTIVE": {"READY_FOR_CHALLENGE", "BLOCKED", "READY", "READY_FOR_SUPERCHAD", "DONE"},
    "CODEX_ACTIVE": {"READY_FOR_CHALLENGE", "BLOCKED", "READY", "READY_FOR_SUPERCHAD", "DONE"},  # skip-challenge: ops only
    "READY_FOR_CHALLENGE": {"MINIMUM_REPAIR", "READY_FOR_SUPERCHAD", "BLOCKED"},
    "MINIMUM_REPAIR": {"READY_FOR_CHALLENGE", "BLOCKED"},
    "BLOCKED": {"READY", "CLAUDE_ACTIVE", "CODEX_ACTIVE", "MINIMUM_REPAIR", "READY_FOR_CHALLENGE"},
    "READY_FOR_SUPERCHAD": {"DONE", "MINIMUM_REPAIR", "READY", "BLOCKED"},
    "DONE": set(),
}
REQUIRED = ("TASK_ID", "SPORT", "KIND", "LANE", "OWNER", "CHALLENGER", "STATUS", "BASE_SHA", "BRANCH",
            "OBJECTIVE", "ACCEPTANCE_CRITERIA", "FILES_OR_AREAS", "EVIDENCE_POINTERS", "BLOCKERS",
            "NEXT_ACTION", "AUTHORITY_REQUIRED", "UPDATED")
# The ONLY fields a challenger receives (contract §4). Builder notes, log, blockers, next action excluded.
CHALLENGE_FIELDS = ("TASK_ID", "SPORT", "KIND", "LANE", "OWNER", "CHALLENGER", "STATUS", "BASE_SHA", "BRANCH",
                    "HEAD_SHA", "OBJECTIVE", "ACCEPTANCE_CRITERIA", "FILES_OR_AREAS", "EVIDENCE_POINTERS",
                    "AUTHORITY_REQUIRED")
EXCLUDED_SECTIONS = ("BUILDER_NOTES", "LOG")
PLACEHOLDER = re.compile(r"^\s*(TBD|TODO|NOT YET WRITTEN|SEE TASKS/)", re.I)


def has_acceptance(t, src=None):
    """Real acceptance criteria exist: a non-placeholder entry in the queue, or (for a pointer)
    a non-placeholder ACCEPTANCE_CRITERIA section in the capsule."""
    items = [a for a in (t.get("ACCEPTANCE_CRITERIA") or []) if a and not PLACEHOLDER.match(a)]
    if items:
        return True
    if src is not None:
        body = sections(src.read(f"TASKS/{t['TASK_ID']}.md") or "").get("ACCEPTANCE_CRITERIA", "")
        lines = [ln for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("<!--")]
        return bool(lines) and not PLACEHOLDER.match(lines[0])
    return False
HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() and os.path.exists(__file__) else None


class Src:
    def __init__(self, ref):
        self.disk = None
        if HERE and os.path.exists(os.path.join(HERE, "..", "WORK_QUEUE.json")):
            self.disk = os.path.normpath(os.path.join(HERE, ".."))
        self.ref = ref

    def read(self, rel):
        if self.disk:
            p = os.path.join(self.disk, rel)
            return open(p).read() if os.path.exists(p) else None
        r = subprocess.run(["git", "show", f"{self.ref}:{OPS}/{rel}"], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    def write(self, rel, text):
        if not self.disk:
            raise SystemExit("move requires a checkout of claude/full-count-ops-state (not piped mode)")
        with open(os.path.join(self.disk, rel), "w") as fh:
            fh.write(text)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_queue(src):
    raw = src.read("WORK_QUEUE.json")
    if raw is None:
        raise SystemExit("WORK_QUEUE.json not found (fetch claude/full-count-ops-state first)")
    return json.loads(raw)


def sections(md):
    out, cur = {}, None
    for line in (md or "").splitlines():
        m = re.match(r"^## +([A-Z_]+)", line)
        if m:
            cur = m.group(1)
            out[cur] = []
        elif cur:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def overlaps(a, b):
    a, b = a.rstrip("/*"), b.rstrip("/*")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def validate(q, src=None):
    errs, warn = [], []
    tasks = q.get("tasks", [])
    ids = [t.get("TASK_ID") for t in tasks]
    if len(ids) != len(set(ids)):
        errs.append("DUPLICATE_TASK_ID")
    for t in tasks:
        miss = [f for f in REQUIRED if f not in t]
        if miss:
            errs.append(f"{t.get('TASK_ID')}: MISSING_FIELDS {miss}")
        if t.get("STATUS") not in STATES:
            errs.append(f"{t.get('TASK_ID')}: BAD_STATUS {t.get('STATUS')}")
        if t.get("KIND") in ("build", "research") and t.get("STATUS") in ACTIVE_BUILD | {"READY_FOR_CHALLENGE"} \
                and not has_acceptance(t, src):
            errs.append(f"{t['TASK_ID']}: ACTIVE_WITHOUT_ACCEPTANCE_CRITERIA")
        if t.get("OWNER") == t.get("CHALLENGER") and t.get("KIND") != "ops":
            errs.append(f"{t.get('TASK_ID')}: SELF_CHALLENGE")
        if t.get("STATUS") in ACTIVE_BUILD and t.get("UPDATED"):
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(t["UPDATED"].replace("Z", "+00:00"))).total_seconds() / 3600
            if age_h > q.get("claim_ttl_hours", 12) and not t.get("WIP_EXEMPT"):
                warn.append(f"{t['TASK_ID']}: CLAIM_EXPIRED ({age_h:.0f}h) -> may return to READY")
    wip = q.get("wip", {})
    active = [t for t in tasks if t.get("STATUS") in ACTIVE_BUILD and not t.get("WIP_EXEMPT")]
    by_sport = {}
    for t in active:
        by_sport.setdefault(t["SPORT"], []).append(t["TASK_ID"])
    for s, lst in by_sport.items():
        if len(lst) > wip.get("active_build_per_sport", 1):
            errs.append(f"WIP_EXCEEDED {s}: {lst}")
    ch = [t["TASK_ID"] for t in tasks if t.get("CHALLENGE_ACTIVE")]
    if len(ch) > wip.get("active_challenge_total", 1):
        errs.append(f"WIP_EXCEEDED challenge: {ch}")
    live = [t for t in tasks if t.get("STATUS") in ACTIVE_BUILD | {"READY_FOR_CHALLENGE"}]
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            hit = [(x, y) for x in a.get("FILES_OR_AREAS", []) for y in b.get("FILES_OR_AREAS", []) if overlaps(x, y)]
            if hit:
                errs.append(f"PATH_CONFLICT {a['TASK_ID']} x {b['TASK_ID']}: {hit[:3]}")
    return errs, warn


def board(q):
    rows = []
    for t in q.get("tasks", []):
        flag = " [auto]" if t.get("WIP_EXEMPT") else ""
        ch = " [challenge-active]" if t.get("CHALLENGE_ACTIVE") else ""
        rows.append(f"{t['TASK_ID']:<11} {t['SPORT']:<4} {t['STATUS']:<20} owner={t['OWNER']:<6} "
                    f"chal={t['CHALLENGER']:<6} {t.get('BRANCH') or '-'}{flag}{ch}\n            next: {t['NEXT_ACTION']}")
    return "\n".join(rows)


def find(q, tid):
    for t in q.get("tasks", []):
        if t["TASK_ID"] == tid:
            return t
    raise SystemExit(f"unknown task {tid}")


def fmt_fields(t, fields):
    out = []
    for f in fields:
        if f not in t:
            continue
        v = t[f]
        if isinstance(v, list):
            out.append(f"{f}:" + ("" if v else " []"))
            out += [f"  - {x}" for x in v]
        else:
            out.append(f"{f}: {v}")
    return "\n".join(out)


def capsule(src, q, tid):
    t = find(q, tid)
    md = src.read(f"TASKS/{tid}.md") or ""
    return f"# CAPSULE {tid} (owner view)\n{fmt_fields(t, [k for k in t])}\n\n{md.strip()}"


def challenge_capsule(src, q, tid):
    t = find(q, tid)
    secs = sections(src.read(f"TASKS/{tid}.md") or "")
    acc = secs.get("ACCEPTANCE_CRITERIA", "")
    text = (f"# CHALLENGE CAPSULE {tid} (objective state only; builder notes/log/verdicts excluded)\n"
            f"{fmt_fields(t, CHALLENGE_FIELDS)}\n\n## ACCEPTANCE_CRITERIA (detail)\n{acc}\n\n"
            f"Write your verdict to {OPS}/AUDITS/{tid}/<agent>.md (PASS|BLOCK + numbered findings). "
            f"Verify claims from code/artifacts at the exact SHA; do not read other audits of this task.")
    for name in EXCLUDED_SECTIONS:                      # fail closed if anything excluded leaked in
        body = secs.get(name, "")
        for line in body.splitlines():
            if len(line.strip()) > 12 and line.strip() in text:
                raise SystemExit(f"ISOLATION_FAILURE: {name} content leaked into challenge capsule")
    return text


def checkpoint(t, head=None, tests=None):
    ev = (t.get("EVIDENCE_POINTERS") or ["-"])[0]
    return "\n".join(["CHECKPOINT",
                      f"TASK={t['TASK_ID']}  HEAD={head or t.get('HEAD_SHA') or '-'}  STATUS={t['STATUS']}",
                      f"TESTS={tests or 'n/a'}  BLOCKERS={len(t.get('BLOCKERS') or [])}",
                      f"EVIDENCE={ev}",
                      f"NEXT={t['NEXT_ACTION']}  AUTHORITY={t['AUTHORITY_REQUIRED']}",
                      "Alligator"])


# ---------------- status (deterministic, change-only) ----------------
def gh(repo, path):
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/{path}",
                                 headers={"Accept": "application/vnd.github+json"})
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def snapshot(w):
    repo, snap = w["repo"], {}
    for b in w.get("branches", []):
        try:
            snap[f"branch:{b}"] = gh(repo, f"branches/{b}")["commit"]["sha"][:10]
        except Exception as exc:  # noqa: BLE001
            snap[f"branch:{b}"] = f"ERR {type(exc).__name__}"
    for n in w.get("prs", []):
        p = gh(repo, f"pulls/{n}")
        cr = gh(repo, f"commits/{p['head']['sha']}/check-runs?per_page=100").get("check_runs", [])
        concl = {}
        for c in cr:
            k = c.get("conclusion") or c.get("status")
            concl[k] = concl.get(k, 0) + 1
        snap[f"pr:{n}"] = (f"{p['head']['sha'][:10]} {'merged' if p.get('merged') else p['state']}"
                           f"{' draft' if p.get('draft') else ''} ci={json.dumps(concl, sort_keys=True)}")
    if w.get("issue"):
        i = gh(repo, f"issues/{w['issue']}")
        snap[f"issue:{w['issue']}"] = f"comments={i['comments']}"
    for e in w.get("evidence_chains", []):
        c = gh(repo, f"contents/{e['path']}?ref={e['ref']}")
        chain = json.loads(base64.b64decode(c["content"]))
        snap[f"chain:{e['ref']}"] = f"len={len(chain)} last={chain[-1].get('unit')}"
    return snap


def issue_delta(w, old, new):
    key = f"issue:{w.get('issue')}"
    if not w.get("issue") or old.get(key) == new.get(key) or key not in old:
        return []
    n_old = int(old[key].split("=")[1])
    n_new = int(new[key].split("=")[1])
    per = 100
    last_page = (n_new - 1) // per + 1
    got = gh(w["repo"], f"issues/{w['issue']}/comments?per_page={per}&page={last_page}")
    if n_new - n_old > len(got) and last_page > 1:
        got = gh(w["repo"], f"issues/{w['issue']}/comments?per_page={per}&page={last_page - 1}") + got
    out = []
    for c in got[-(n_new - n_old):]:
        first = (c["body"].strip().splitlines() or [""])[0][:90]
        out.append(f"  #91 {c['id']} {c['created_at']} | {first}")
    return out


def status(args, src):
    w = json.loads(src.read("WATCH.json"))
    state_path = os.path.expanduser(args.state)
    deadline = time.time() + args.max_hours * 3600
    while True:
        old = json.load(open(state_path)) if os.path.exists(state_path) else {}
        new = snapshot(w)
        changed = {k: (old.get(k), v) for k, v in new.items() if old.get(k) != v}
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
        if changed and old:
            lines = [f"{k}: {a} -> {b}" for k, (a, b) in sorted(changed.items())] + issue_delta(w, old, new)
            json.dump(new, open(state_path, "w"), indent=1, sort_keys=True)
            print(f"FC_STATUS CHANGED {len(changed)} @ {now()}")
            print("\n".join(lines))
            return 3
        json.dump(new, open(state_path, "w"), indent=1, sort_keys=True)
        if not old:
            print(f"FC_STATUS BASELINE {len(new)} targets @ {now()}")
            if not args.until_change:
                print("\n".join(f"{k}: {v}" for k, v in sorted(new.items())))
                return 0
        elif not args.until_change:
            if not args.quiet:
                print(f"FC_STATUS NO_CHANGE {len(new)} targets @ {now()}")
            return 0
        if time.time() > deadline:
            print(f"FC_STATUS NO_CHANGE until max-hours @ {now()}")
            return 0
        time.sleep(args.until_change)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default="FETCH_HEAD", help="git ref holding the ops state (piped mode)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("orient"); o.add_argument("task", nargs="?"); o.add_argument("--challenger", action="store_true")
    sub.add_parser("queue")
    c = sub.add_parser("capsule"); c.add_argument("task")
    cc = sub.add_parser("challenge-capsule"); cc.add_argument("task")
    k = sub.add_parser("checkpoint"); k.add_argument("task"); k.add_argument("--head"); k.add_argument("--tests")
    m = sub.add_parser("move"); m.add_argument("task"); m.add_argument("state", choices=STATES)
    m.add_argument("--by", required=True, choices=("claude", "codex", "superchad", "jacob"))
    s = sub.add_parser("status"); s.add_argument("--quiet", action="store_true")
    s.add_argument("--until-change", type=int, default=0, help="poll every N seconds until a change")
    s.add_argument("--max-hours", type=float, default=6.0)
    s.add_argument("--state", default=os.environ.get("FC_STATUS_STATE", "~/.cache/fc_status.json"))
    a = ap.parse_args(argv)
    src = Src(a.ref)
    try:
        if a.cmd == "status":
            return status(a, src)
        q = load_queue(src)
        if a.cmd in ("orient", "queue"):
            errs, warn = validate(q, src)
            out = []
            if a.cmd == "orient":
                out.append(src.read("CURRENT_STATE.md") or "CURRENT_STATE.md missing")
            out.append("# QUEUE\n" + board(q))
            out += [f"WARN {x}" for x in warn] + [f"ERROR {x}" for x in errs]
            if a.cmd == "orient" and a.task:
                out.append(challenge_capsule(src, q, a.task) if a.challenger else capsule(src, q, a.task))
            text = "\n\n".join(out)
            print(text)
            print(f"\n[fc orient payload: {len(text.encode())} bytes ≈ {len(text.encode()) // 4} tokens]"
                  if a.cmd == "orient" else "")
            return 1 if errs else 0
        if a.cmd == "capsule":
            print(capsule(src, q, a.task))
        elif a.cmd == "challenge-capsule":
            print(challenge_capsule(src, q, a.task))
        elif a.cmd == "checkpoint":
            print(checkpoint(find(q, a.task), a.head, a.tests))
        elif a.cmd == "move":
            t = find(q, a.task)
            if a.state not in TRANSITIONS[t["STATUS"]]:
                raise SystemExit(f"ILLEGAL_TRANSITION {t['STATUS']} -> {a.state}")
            if a.state in ACTIVE_BUILD and a.by not in ("claude", "codex"):
                raise SystemExit("only an agent can claim work")
            if a.state == "CLAUDE_ACTIVE" and a.by != "claude" or a.state == "CODEX_ACTIVE" and a.by != "codex":
                raise SystemExit(f"{a.by} cannot set {a.state}")
            if t["KIND"] != "ops" and t["STATUS"] in ("CLAUDE_ACTIVE", "CODEX_ACTIVE") \
                    and a.state in ("READY_FOR_SUPERCHAD", "DONE"):
                raise SystemExit("NO_SELF_APPROVAL: build/research must pass READY_FOR_CHALLENGE first")
            if a.state == "DONE" and t["KIND"] != "ops" and a.by not in ("superchad", "jacob"):
                raise SystemExit("DONE is set by SUPERCHAD or Jacob after challenge")
            if a.state == "READY_FOR_SUPERCHAD" and t["STATUS"] == "READY_FOR_CHALLENGE" \
                    and a.by != t.get("CHALLENGER"):
                raise SystemExit("only the challenger passes a task to SUPERCHAD")
            t["STATUS"], t["UPDATED"] = a.state, now()
            t.setdefault("HISTORY", []).append(f"{t['UPDATED']} {a.by} -> {a.state}")
            errs, _ = validate(q, src)
            if errs:
                raise SystemExit("REFUSED (queue would be invalid): " + "; ".join(errs))
            src.write("WORK_QUEUE.json", json.dumps(q, indent=1) + "\n")
            print(f"{a.task} -> {a.state}")
        return 0
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"FC_ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

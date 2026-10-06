#!/usr/bin/env python3
"""FC-MLB-001B reactivation package -- the ONLY place post-Codex bindings are computed. Prepares; never executes.

It never writes inside the repository, never commits, never pushes, never posts, never touches the trigger.

Step 1 (after the 001B drill PASSES and Codex PASSES the exact head; SUPERCHAD/Jacob approve):
    python3 prepare_activation.py --repo R --implementation-commit <FULL SHA> --codex-passed-commit <FULL SHA> --out D
  -> D/REACTIVATION_BINDINGS.json (every binding derived from that exact commit) and
     D/JACOB_AUTHORIZATION.txt (the comment text JACOB posts himself on Issue #91; agents never post it).
Step 2 (after Jacob's comment exists):
    ... --jacob-comment-id <ID> [--comment-json FILE] --activation-timestamp <ISO>
  -> D/ACTIVATION_<ID>.json (validated by the EXISTING activation.check_comment against the real comment),
     its sha256, D/v3-unit-record.yml (the bound record workflow for the request branch) and D/v3_dispatch.py (the
     post-001B dispatcher with every placeholder bound, including the record workflow's sha256; refuses otherwise).

Tape storage (Jacob, 2026-10-06): the initial backend is the TEMPORARY GitHub Actions artifact store (no Cloudflare
account action). Its contract, the configured artifact retention, and the migration thresholds are bindings, and the
R2 migration milestone (FC-MLB-001C) is stated in Jacob's text. A commit without the temporary store is refused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V3 = os.path.dirname(HERE)
sys.path.insert(0, V3)
import activation as AC  # noqa: E402
import verify_evidence as VE  # noqa: E402

CRITERIA = {"task": "FC-MLB-001B", "ops_commit": "e5475e66c0b8a6be83f82e3e17828cb701b30c2c",
            "file": "engineering/ops/TASKS/FC-MLB-001B.md",
            "file_sha256": "f8efd4b563751731c6d78ce8949eebcf6e9745317c990cde9220febbe68ec652"}
PREREG_BLOB = "71e7d2828df20a01f476376efcc0c651828dcaf2"
EVIDENCE_REF = "claude/mlb-challenger-v3-evidence"
OPS_REF = "claude/mlb-v3-ops"
TRIGGER = {"id": "trig_011u98uXVuFEipPfbTT6KGur", "cron": "7 13-23,0-1 * * *",
           "state_now": "enabled=false since 2026-10-05T16:39:32Z (V3 HOLD)"}
TEMPLATE = os.path.join(HERE, "v3_dispatch.post_001b.template.py")
WORKFLOW_TEMPLATE = os.path.join(HERE, "v3_unit_record.workflow.template.yml")
REQUEST_BRANCH = "claude/mlb-v3-unit-requests"
# Measured 2026-10-05 (agents cannot read the repository retention setting: API 403): drill artifact 11369873498,
# created 2026-10-05T21:03:13Z, expires 2027-01-03T20:49:20Z -> effective 90 days, anchored to the run start.
OBSERVED_RETENTION = {"artifact_id": 11369873498, "created_at": "2026-10-05T21:03:13Z",
                      "expires_at": "2027-01-03T20:49:20Z", "effective_days": 90,
                      "repository_setting": "not readable by agents (GitHub API 403); Jacob may confirm it in "
                                            "Settings > Actions > General > Artifact and log retention"}
MIGRATION_MILESTONE = ("FC-MLB-001C: migrate authoritative V3 tape storage from GitHub Actions artifacts to Cloudflare "
                       "R2 or equivalent durable object storage before temporary artifact retention threatens any "
                       "prospective evidence")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class Refused(SystemExit):
    pass


def git(repo, *a):
    return subprocess.check_output(["git", "-C", repo, *a], stderr=subprocess.DEVNULL).decode()


def _const(src, name):
    m = re.search(rf'^{name}\s*=\s*"([^"]+)"', src, re.M)
    if not m:
        raise Refused(f"{name} not found at the implementation commit")
    return m.group(1)


def _int_const(src, name):
    m = re.search(rf"^{name}\s*=\s*(\d+)\s*$", src, re.M)
    if not m:
        raise Refused(f"{name} not found at the implementation commit")
    return int(m.group(1))


def bindings(repo, commit, codex_passed, retention_days=90):
    if not (FULL_SHA.match(commit or "") and FULL_SHA.match(codex_passed or "")):
        raise Refused("full 40-hex commits required (no abbreviations, no placeholders)")
    if commit != codex_passed:
        raise Refused("the implementation commit must be EXACTLY the head Codex passed")
    try:
        git(repo, "cat-file", "-e", f"{commit}^{{commit}}")
    except subprocess.CalledProcessError as exc:
        raise Refused(f"commit {commit} is not present in {repo}") from exc
    show = lambda p: git(repo, "show", f"{commit}:{p}")
    tree = git(repo, "rev-parse", f"{commit}:{AC.V3_DIR}").strip()
    blob = git(repo, "rev-parse", f"{commit}:{AC.PREREG_PATH}").strip()
    prereg_sha = hashlib.sha256(subprocess.check_output(["git", "-C", repo, "show", f"{commit}:{AC.PREREG_PATH}"])).hexdigest()
    if blob != PREREG_BLOB or prereg_sha != VE.PREREG_SHA256:
        raise Refused(f"prereg changed at {commit} (blob {blob}, sha256 {prereg_sha}): STOP")
    for need in ("sandbox.py", "runtime_image.py", "payload.py", "tape_store.py", "b_drill.py"):
        try:
            show(f"{AC.V3_DIR}/{need}")
        except subprocess.CalledProcessError as exc:
            raise Refused(f"{need} missing at {commit}: not a 001B implementation") from exc
    for need in ("artifact_api.py", "retention_monitor.py", "tape_migration.py"):
        try:
            show(f"{AC.V3_DIR}/{need}")
        except subprocess.CalledProcessError as exc:
            raise Refused(f"{need} missing at {commit}: no TEMPORARY Actions-artifact store, and R2 is not "
                          f"configured -- the reviewed head must include the temporary store") from exc
    ts_src, rm_src = show(f"{AC.V3_DIR}/tape_store.py"), show(f"{AC.V3_DIR}/retention_monitor.py")
    if not (isinstance(retention_days, int) and 1 <= retention_days <= 400):
        raise Refused(f"artifact retention {retention_days!r} days is not a valid GitHub retention")
    warn, crit = _int_const(rm_src, "WARN_DAYS"), _int_const(rm_src, "CRITICAL_DAYS")
    if not crit < warn < retention_days:
        raise Refused(f"migration thresholds ({warn}/{crit} days) do not fit a {retention_days}-day retention")
    lock = subprocess.check_output(["git", "-C", repo, "show", f"{commit}:{AC.V3_DIR}/shadow-requirements.lock"])
    try:
        evidence_head = git(repo, "rev-parse", f"origin/{EVIDENCE_REF}").strip()
        ops_head = git(repo, "rev-parse", f"origin/{OPS_REF}").strip()
    except subprocess.CalledProcessError:
        evidence_head = ops_head = None
    return {"implementation_commit": commit, "implementation_tree": tree, "codex_passed_commit": codex_passed,
            "criteria": CRITERIA, "prereg_commit": VE.PREREG_COMMIT, "prereg_sha256": prereg_sha, "prereg_blob": blob,
            "runtime_digest": _const(show(f"{AC.V3_DIR}/runtime_image.py"), "MANIFEST_DIGEST"),
            "runtime_python": _const(show(f"{AC.V3_DIR}/runtime_image.py"), "EXPECTED_PYTHON"),
            "scientific_payload_spec": _const(show(f"{AC.V3_DIR}/payload.py"), "SPEC"),
            "storage_contract": _const(ts_src, "STORAGE_CONTRACT"),
            "tape_storage": {"initial_backend": _const(ts_src, "GHA_KIND"),
                             "initial_contract": _const(ts_src, "GHA_CONTRACT"),
                             "durability": "TEMPORARY (Actions artifact retention); NOT archival",
                             "artifact_retention_days": retention_days, "migration_warn_days_remaining": warn,
                             "migration_critical_days_remaining": crit, "observed_retention": OBSERVED_RETENTION,
                             "durable_target": {"backend": "r2", "contract": _const(ts_src, "STORAGE_CONTRACT"),
                                                "status": "code ready; Cloudflare account NOT set up (no Jacob action now)"},
                             "migration_milestone": MIGRATION_MILESTONE, "request_branch": REQUEST_BRANCH},
            "dependency_lock_sha256": hashlib.sha256(lock).hexdigest(),
            "evidence_branch": EVIDENCE_REF, "trigger": TRIGGER,
            "rollback_point": {"disable_trigger": TRIGGER["id"], "dispatcher_ops_head_before": ops_head,
                               "evidence_head_before": evidence_head,
                               "note": "units sealed after activation stay on the append-only chain; nothing is deleted"}}


def jacob_text(b):
    """The exact comment JACOB posts (activation.check_comment: first line, action phrase, prereg commit + sha256,
    implementation commit, no agent marker). Agents never post it."""
    return "\n".join([
        "JACOB AUTHORIZATION: ALLOW",
        "",
        "V3 prospective activation (FC-MLB-001B amended implementation).",
        f"Prereg commit: {b['prereg_commit']}",
        f"Prereg sha256: {b['prereg_sha256']}",
        f"Implementation commit: {b['implementation_commit']}",
        f"Implementation v3 tree: {b['implementation_tree']}",
        f"Runtime image: {b['runtime_digest']} (CPython {b['runtime_python']})",
        f"Scientific payload spec: {b['scientific_payload_spec']}; storage contract: {b['storage_contract']}",
        f"Tape storage: TEMPORARY GitHub Actions artifacts ({b['tape_storage']['initial_contract']}), retention "
        f"{b['tape_storage']['artifact_retention_days']} days, migration warning at "
        f"{b['tape_storage']['migration_warn_days_remaining']} days remaining. Not archival: migration to durable R2 "
        f"is required before retention threatens any evidence (FC-MLB-001C).",
        f"Criteria: {b['criteria']['task']} at ops {b['criteria']['ops_commit']} (sha256 {b['criteria']['file_sha256']})",
        "Collection may resume through the reviewed dispatcher only. The 2026 postseason remains DESCRIPTIVE SHADOW;",
        "the 2027 regular season is CONFIRMATORY. The four 2026-10-03/04 units remain quarantined.", ""])


def _bind(s, values):
    for k, v in values.items():
        s = s.replace(f"@@{k}@@", v)
    return s


def _values(b, comment_id, activation_sha):
    return {"IMPLEMENTATION_COMMIT": b["implementation_commit"], "IMPLEMENTATION_TREE": b["implementation_tree"],
            "RUNTIME_DIGEST": b["runtime_digest"], "JACOB_COMMENT_ID": str(comment_id),
            "ACTIVATION_SHA256": activation_sha, "REQUEST_BRANCH": b["tape_storage"]["request_branch"],
            "ARTIFACT_RETENTION_DAYS": str(b["tape_storage"]["artifact_retention_days"])}


def bind_workflow(b, comment_id, activation_sha):
    s = _bind(open(WORKFLOW_TEMPLATE).read(), _values(b, comment_id, activation_sha))
    s = s.replace("*@@*)", '*@""@*)')                                         # the guard itself stays armed
    left = sorted(set(re.findall(r"@@[A-Z_0-9]+@@", s)))
    if left:
        raise Refused(f"unbound placeholders remain in the record workflow: {left}")
    return s


def bind_dispatcher(b, comment_id, activation_sha, workflow_sha):
    s = _bind(open(TEMPLATE).read(), dict(_values(b, comment_id, activation_sha), RECORD_WORKFLOW_SHA256=workflow_sha))
    s = s.replace('if "@@" in AUTH_COMMIT', 'if "@" + "@" in AUTH_COMMIT')       # the guard itself stays armed
    left = sorted(set(re.findall(r"@@[A-Z_0-9]+@@", s)))
    if left:
        raise Refused(f"unbound placeholders remain: {left}")
    compile(s, "v3_dispatch.py", "exec")
    return s


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--implementation-commit", required=True)
    ap.add_argument("--codex-passed-commit", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jacob-comment-id")
    ap.add_argument("--comment-json", help="offline: the fetched comment object (else fetched from the GitHub API)")
    ap.add_argument("--activation-timestamp")
    ap.add_argument("--artifact-retention-days", type=int, default=90,
                    help="retention-days for tape artifacts (the repository's effective retention, measured: 90)")
    a = ap.parse_args(argv)
    b = bindings(a.repo, a.implementation_commit, a.codex_passed_commit, a.artifact_retention_days)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "REACTIVATION_BINDINGS.json"), "w") as fh:
        json.dump(b, fh, indent=1, sort_keys=True)
    with open(os.path.join(a.out, "JACOB_AUTHORIZATION.txt"), "w") as fh:
        fh.write(jacob_text(b))
    if not a.jacob_comment_id:
        print(json.dumps({"step": 1, "written": ["REACTIVATION_BINDINGS.json", "JACOB_AUTHORIZATION.txt"]}, indent=1))
        return 0
    comment = json.load(open(a.comment_json)) if a.comment_json else VE.fetch_issue_comment(a.jacob_comment_id)
    if not a.activation_timestamp:
        raise Refused("--activation-timestamp required (must not precede the comment)")
    rec = {"activation_timestamp": a.activation_timestamp, "implementation_commit": b["implementation_commit"],
           "implementation_tree": b["implementation_tree"], "jacob_authorization_comment_created_at": comment.get("created_at"),
           "jacob_authorization_comment_id": int(a.jacob_comment_id),
           "jacob_authorization_comment_url": f"https://github.com/{AC.REPOSITORY}/issues/91#issuecomment-{a.jacob_comment_id}",
           "pr_number": AC.PR_NUMBER, "prereg_commit": VE.PREREG_COMMIT, "prereg_sha256": VE.PREREG_SHA256,
           "protocol_version": AC.PROTOCOL, "repository": AC.REPOSITORY}
    failed = [k for k, ok in AC.check_comment(comment, rec).items() if not ok]
    if failed:
        raise Refused(f"Jacob's comment does not satisfy the activation verifier: {failed}")
    if comment.get("created_at") and rec["activation_timestamp"] < comment["created_at"]:
        raise Refused("activation timestamp precedes the authorization comment")
    raw = (json.dumps(rec, indent=1, sort_keys=True) + "\n").encode()
    sha = hashlib.sha256(raw).hexdigest()
    with open(os.path.join(a.out, f"ACTIVATION_{a.jacob_comment_id}.json"), "wb") as fh:
        fh.write(raw)
    wf = bind_workflow(b, a.jacob_comment_id, sha)
    with open(os.path.join(a.out, "v3-unit-record.yml"), "w") as fh:
        fh.write(wf)
    wf_sha = hashlib.sha256(wf.encode()).hexdigest()
    with open(os.path.join(a.out, "v3_dispatch.py"), "w") as fh:
        fh.write(bind_dispatcher(b, a.jacob_comment_id, sha, wf_sha))
    print(json.dumps({"step": 2, "activation_record_sha256": sha, "record_workflow_sha256": wf_sha,
                      "written": [f"ACTIVATION_{a.jacob_comment_id}.json", "v3-unit-record.yml", "v3_dispatch.py"]},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

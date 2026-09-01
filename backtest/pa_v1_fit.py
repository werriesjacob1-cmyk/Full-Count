#!/usr/bin/env python3
"""pa_v1_fit.py -- produce the ONE deterministic PA-v1 fitted artifact.

PA-v1 is the residual opportunity challenger that earned EARNS_PROSPECTIVE_SHADOW
on the locked canonical-v2 historical experiment. This module fits it exactly
once, on all certified historical data available BEFORE the prospective launch,
and freezes the result.

Why fit here rather than inside the live build: the live scorer must be pure and
read-only over a frozen artifact. Fitting from outcomes inside every live build
would silently make the challenger a moving target, and a moving challenger
cannot be evaluated prospectively at all.

IMMUTABILITY. Once prospective evaluation begins this artifact is frozen. There
is no automatic refit from forward outcomes. Any later refit is PA-v2, a new
evidence regime with its own effective_from, and it cannot retroactively replace
PA-v1 receipts.

    python3 backtest/pa_v1_fit.py --rows <certified rows.jsonl> --out <artifact.json>
"""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys
from collections import defaultdict
from datetime import datetime, timezone

PROTOCOL_VERSION = "prospective-hits-pa-v1"
MARKET = "hits"
MIN_CELL_N = 200
PA_STATES = ["0", "1", "2", "3", "4", "5", "6+"]
HITTER_MARKETS = frozenset({
    "hits", "total_bases", "hits_runs_rbis", "home_run", "singles",
    "doubles", "triples", "rbis", "runs", "hard_hit_105",
})
# The nine whole-date quarantines certified on the research view. Recorded so the
# artifact can prove which dates could not have contributed to the fit.
CERTIFIED_QUARANTINED_DATES = (
    "2024-05-22", "2024-07-11", "2024-08-26", "2024-08-28", "2025-05-21",
    "2025-06-07", "2025-07-02", "2025-08-03", "2026-06-17",
)

# REQUIRED certified input. The locked protocol section 3 names one specific
# research view; an authoritative fit must FAIL CLOSED on anything else rather
# than faithfully recording the digest of whatever --rows happened to point at.
REQUIRED_ROWS_SHA256 = "8ca010641d08008044c8c3b609162d6e5d69f07bb79be6705b2690a51ab2cb34"
REQUIRED_ROWS_COUNT = 1186300
REQUIRED_DATES_WITH_ROWS = 555
REQUIRED_MAX_DATE = "2026-08-25"


class CertifiedInputError(ValueError):
    """The input is not the certified canonical-v2 research view."""


class WorktreeDirtyError(ValueError):
    """Authoritative fitting may not run from uncommitted fitter changes."""


class PlayerGameConflict(ValueError):
    """One player-game disagrees with itself about a PA-v1 opportunity fact."""


def derive_batting_order(lineup_slot):
    if lineup_slot is None:
        return None
    order = round(9.0 - lineup_slot * 8.0 / 100.0)
    return order if 1 <= order <= 9 else None


def pa_bucket_fine(actual_pa):
    if actual_pa is None:
        return "unknown"
    return "6+" if actual_pa >= 6 else str(int(actual_pa))


def getaway_day_group(signals):
    v = signals.get("getaway_day")
    if v is None:
        return None
    return "getaway_day" if v < 0 else "not_getaway_day"


def days_rest_group(signals):
    v = signals.get("days_rest")
    if v is None:
        return None
    if v <= 0:
        return "0_days_rest"
    if v == 1:
        return "1_day_rest"
    if v <= 3:
        return "2-3_days_rest"
    return "4plus_days_rest"


def joint_key(signals):
    order = derive_batting_order(signals.get("lineup_slot"))
    if order is None:
        return None
    dr, ga = days_rest_group(signals), getaway_day_group(signals)
    if dr is None or ga is None:
        return None
    return (order, dr, ga)


def _cell_token(key):
    """Stable JSON-safe token for a joint cell, so the artifact round-trips."""
    return "|".join(str(p) for p in key)


def fit(rows, train_cutoff):
    graded = [r for r in rows if r.get("outcome") in (0, 1)]
    hitter = [r for r in graded if r.get("prop_type") in HITTER_MARKETS
              and (r.get("date") or "") <= train_cutoff]

    # PLAYER-GAME CONSISTENCY BEFORE DEDUPE.
    # The dedupe keeps the FIRST row per (date, game_pk, player_id). That is only
    # sound if every row of that player-game agrees about the PA-v1 opportunity
    # facts. If two rows disagree, "first wins" silently resolves a real data
    # conflict by market/row order. Assert agreement instead and STOP on
    # conflict -- no majority vote, no market priority, no row-order tiebreak.
    groups = defaultdict(list)
    for r in hitter:
        groups[(r.get("date"), r.get("game_pk"), r.get("player_id"))].append(r)

    conflicts = []
    player_games = []
    for k in sorted(groups, key=lambda t: tuple(str(x) for x in t)):
        rows = groups[k]
        facts = {}
        for r in rows:
            sig = r.get("signals") or {}
            observed = {
                "actual_pa": r.get("actual_pa"),
                "batting_order": derive_batting_order(sig.get("lineup_slot")),
                "days_rest_group": days_rest_group(sig),
                "getaway_day_group": getaway_day_group(sig),
            }
            for name, value in observed.items():
                if value is None:
                    continue  # absent is not a conflict; absent is absent
                prior = facts.get(name)
                if prior is None:
                    facts[name] = value
                elif prior != value:
                    conflicts.append({
                        "player_game": [str(x) for x in k],
                        "fact": name, "values": sorted({str(prior), str(value)}),
                        "rows_in_group": len(rows),
                    })
        player_games.append(rows[0])

    if conflicts:
        raise PlayerGameConflict(
            f"{len(conflicts)} player-game opportunity-fact conflict(s); refusing to "
            f"resolve by row order. First: {conflicts[0]}")

    jc, jt = defaultdict(lambda: defaultdict(int)), defaultdict(int)
    oc, ot = defaultdict(lambda: defaultdict(int)), defaultdict(int)
    for r in player_games:
        sig = r.get("signals") or {}
        pa = r.get("actual_pa")
        if pa is None:
            continue
        state = pa_bucket_fine(pa)
        order = derive_batting_order(sig.get("lineup_slot"))
        if order is not None:
            oc[order][state] += 1
            ot[order] += 1
        k = joint_key(sig)
        if k is not None:
            jc[k][state] += 1
            jt[k] += 1

    joint_table = {_cell_token(k): dict({s: round(jc[k].get(s, 0) / t, 6) for s in PA_STATES}, _n=t)
                   for k, t in jt.items() if t >= MIN_CELL_N}
    order_table = {str(o): dict({s: round(oc[o].get(s, 0) / t, 6) for s in PA_STATES}, _n=t)
                   for o, t in ot.items()}

    hc = defaultdict(lambda: {"n": 0, "hits": 0})
    for r in hitter:
        if r.get("prop_type") != MARKET:
            continue
        pa = r.get("actual_pa")
        if pa is None:
            continue
        b = hc[pa_bucket_fine(pa)]
        b["n"] += 1
        b["hits"] += r["outcome"]
    hit_rate_given_pa = {s: (round(v["hits"] / v["n"], 6) if v["n"] else None)
                         for s, v in hc.items()}

    return {
        "joint_pa_table": joint_table,
        "order_pa_table": order_table,
        "hit_rate_given_pa": hit_rate_given_pa,
        "train_player_games": len(player_games),
        "train_market_rows": sum(1 for r in hitter if r.get("prop_type") == MARKET),
        "joint_cells_fit": len(joint_table),
        "order_cells_fit": len(order_table),
    }


def _sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def _git(*args):
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def code_identity(authoritative):
    """Bind the bytes that actually ran, not just a branch pointer.

    `git rev-parse HEAD` on a dirty worktree names a commit whose content is NOT
    what executed. An authoritative fit therefore also hashes this file's real
    bytes and requires the fitter to be committed. Fails closed rather than
    recording a HEAD that does not describe the running code.
    """
    me = os.path.abspath(__file__)
    rel = "backtest/pa_v1_fit.py"
    with open(me, "rb") as fh:
        file_sha = hashlib.sha256(fh.read()).hexdigest()
    head = _git("rev-parse", "HEAD")
    dirty_fitter = _git("status", "--porcelain", "--", rel)
    dirty_all = _git("status", "--porcelain")
    committed_blob = _git("rev-parse", f"HEAD:{rel}")
    clean_fitter = (dirty_fitter == "")
    # SCIENTIFIC code identity: facts about the bytes that ran. Deterministic
    # for a given commit, so it belongs inside the hashed body.
    ident = {
        "file": rel,
        "fitter_file_sha256": file_sha,
        "repo_head_sha": head,
        "committed_blob_id": committed_blob,
        "fitter_worktree_clean": clean_fitter,
    }
    # RUN provenance: true of this invocation, not of the science. Hashing it
    # would make the artifact identity depend on when it ran and on whether
    # unrelated files happened to be dirty -- so it is recorded OUTSIDE the
    # hashed body.
    provenance = {
        "repo_worktree_fully_clean": (dirty_all == ""),
        "authoritative_run": bool(authoritative),
    }
    if authoritative and not clean_fitter:  # noqa: E501
        raise WorktreeDirtyError(
            "authoritative PA-v1 fitting requires a committed, unmodified "
            f"{rel}; `git status --porcelain -- {rel}` reported: {dirty_fitter!r}. "
            "Commit the fitter first so repo_head_sha describes the bytes that ran."
        )
    return ident, provenance


def assert_certified_input(rows, dates, rows_sha256, authoritative):
    """Fail closed unless this really is the certified canonical-v2 research view."""
    problems = []
    if rows_sha256 != REQUIRED_ROWS_SHA256:
        problems.append(f"rows sha256 {rows_sha256} != required {REQUIRED_ROWS_SHA256}")
    if len(rows) != REQUIRED_ROWS_COUNT:
        problems.append(f"row count {len(rows)} != required {REQUIRED_ROWS_COUNT}")
    if len(dates) != REQUIRED_DATES_WITH_ROWS:
        problems.append(f"dates-with-rows {len(dates)} != required {REQUIRED_DATES_WITH_ROWS}")
    if dates and max(dates) != REQUIRED_MAX_DATE:
        problems.append(f"max date {max(dates)} != required {REQUIRED_MAX_DATE}")
    present = sorted(set(CERTIFIED_QUARANTINED_DATES) & set(dates))
    if present:
        problems.append(f"quarantined dates carry rows: {present}")
    verified = not problems
    if authoritative and problems:
        raise CertifiedInputError(
            "input is not the certified canonical-v2 research view: " + "; ".join(problems))
    return {
        "required_rows_sha256": REQUIRED_ROWS_SHA256,
        "required_rows_count": REQUIRED_ROWS_COUNT,
        "required_dates_with_rows": REQUIRED_DATES_WITH_ROWS,
        "required_max_date": REQUIRED_MAX_DATE,
        "quarantined_dates_required_empty": list(CERTIFIED_QUARANTINED_DATES),
        "verified": verified,
        "problems": problems,
    }


def canonical_json(obj):
    """Deterministic serialization -- the artifact hash must be reproducible."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_artifact(rows_path, effective_from=None, train_cutoff=None,
                   authoritative=False):
    rows, dates = [], set()
    with open(rows_path) as fh:
        for line in fh:
            r = json.loads(line)
            rows.append(r)
            if r.get("date"):
                dates.add(r["date"])
    rows_sha = _sha256_file(rows_path)
    certified = assert_certified_input(rows, dates, rows_sha, authoritative)
    code_id, run_provenance = code_identity(authoritative)
    cutoff = train_cutoff or REQUIRED_MAX_DATE
    tables = fit(rows, cutoff)

    body = {
        "protocol_version": PROTOCOL_VERSION,
        "model": "residual_order_days_rest_getaway",
        "market": MARKET,
        "min_cell_n": MIN_CELL_N,
        "pa_states": PA_STATES,
        "fallback_semantics": {
            "sparse_or_missing_joint_cell": "order-only PA distribution",
            "order_unavailable": "PA score unavailable -> caller substitutes the "
                                 "champion's own probability as a NEUTRAL rank "
                                 "fallback; the candidate is never removed",
            "unpriced_pa_state": "state skipped, remaining mass renormalized",
        },
        "training_input": {
            "certified_rows_path": os.path.basename(rows_path),
            "certified_rows_sha256": rows_sha,
            "certified_rows_count": len(rows),
            "certified_date_min": min(dates) if dates else None,
            "certified_date_max": max(dates) if dates else None,
            "certified_dates_with_rows": len(dates),
            "quarantined_dates_excluded_by_certification": list(CERTIFIED_QUARANTINED_DATES),
            "train_cutoff_inclusive": cutoff,
        },
        "certified_input_contract": certified,
        "fitting_code": code_id,
        "tables": tables,
        # effective_from and the immutability contract are part of the SCIENTIFIC
        # BODY, not metadata bolted on afterwards. Two artifacts that differ only
        # in when PA-v1 becomes applicable are scientifically different artifacts
        # and must not be able to advertise the same content hash.
        "effective_from": effective_from or datetime.now(timezone.utc).isoformat(),
        "versioning_contract": {
            "frozen_once_first_eligible_receipt_exists": True,
            "forward_outcomes_may_refit": False,
            "later_refit_is": "PA-v2, new effective_from, may not retroactively "
                              "replace PA-v1 receipts or scores",
            "statement": (
                "FROZEN once prospective evaluation begins. No automatic refit "
                "from forward outcomes. A later refit is PA-v2 with its own "
                "effective_from and cannot retroactively replace PA-v1 receipts."
            ),
        },
    }
    # Hash the COMPLETE scientific body. Everything that could change what the
    # model is, what it was fitted on, which bytes produced it, or when it
    # becomes applicable is inside this hash. Only the self-referential field is
    # excluded -- run provenance and created_at are siblings, added after.
    body["scientific_content_sha256"] = hashlib.sha256(
        canonical_json(body).encode("utf-8")).hexdigest()
    # Non-scientific siblings. Deliberately outside the hash: a verifier must be
    # able to recompute scientific_content_sha256 from an artifact produced on a
    # different day, on a different machine, with an unrelated file dirty.
    # Proven by --verify reconstructing the hash exactly.
    body["run_provenance"] = dict(run_provenance,
                                  created_at=datetime.now(timezone.utc).isoformat())
    return body


def recompute_scientific_sha(artifact):
    """Recompute the content hash of an existing artifact, for verification."""
    body = {k: v for k, v in artifact.items()
            if k not in ("scientific_content_sha256", "run_provenance")}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def serialize(artifact):
    """The exact bytes written to disk, and their own digest."""
    payload = canonical_json(artifact).encode("utf-8")
    return payload, hashlib.sha256(payload).hexdigest()


# ---- the pure live scorer: read-only over the frozen artifact ----
def score(signals, artifact):
    """P(hit) = sum_k P(PA=k | context) * P(hit | PA=k). Returns None when the
    batting order is unavailable; the caller substitutes the champion probability
    as a neutral rank fallback and never drops the candidate."""
    tables = artifact["tables"]
    joint, order_t = tables["joint_pa_table"], tables["order_pa_table"]
    hr = tables["hit_rate_given_pa"]
    k = joint_key(signals or {})
    dist = joint.get(_cell_token(k)) if k else None
    if dist is None:
        order = derive_batting_order((signals or {}).get("lineup_slot"))
        dist = order_t.get(str(order)) if order is not None else None
    if not dist:
        return None
    tot = w = 0.0
    for s in artifact["pa_states"]:
        p_pa, p_hit = dist.get(s, 0.0), hr.get(s)
        if p_hit is None or p_pa <= 0:
            continue
        tot += p_pa * p_hit
        w += p_pa
    return round(tot / w, 6) if w > 0 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows")
    ap.add_argument("--out")
    ap.add_argument("--effective-from")
    ap.add_argument("--train-cutoff")
    ap.add_argument("--verify", metavar="ARTIFACT",
                    help="recompute an existing artifact's scientific hash and exit")
    ap.add_argument("--authoritative", action="store_true",
                    help="enforce the certified-input and clean-fitter gates; "
                         "required for the one real PA-v1 freeze")
    a = ap.parse_args()
    if a.verify:
        art = json.load(open(a.verify))
        claimed = art.get("scientific_content_sha256")
        actual = recompute_scientific_sha(art)
        print(f"claimed  = {claimed}")
        print(f"recomputed = {actual}")
        print("VERIFIED" if claimed == actual else "MISMATCH")
        return 0 if claimed == actual else 1
    if not a.rows or not a.out:
        raise SystemExit("--rows and --out are required unless --verify is used")
    art = build_artifact(a.rows, a.effective_from, a.train_cutoff, a.authoritative)
    payload, file_sha = serialize(art)
    with open(a.out, "wb") as fh:
        fh.write(payload)
    print(f"scientific_content_sha256   = {art['scientific_content_sha256']}")
    print(f"serialized_file_sha256      = {file_sha}")
    print(f"authoritative               = {a.authoritative}")
    print(f"certified input verified    = {art['certified_input_contract']['verified']}")
    print(f"fitter file sha256          = {art['fitting_code']['fitter_file_sha256']}")
    print(f"fitter worktree clean       = {art['fitting_code']['fitter_worktree_clean']}")
    print(f"repo head sha               = {art['fitting_code']['repo_head_sha']}")
    print(f"effective_from              = {art['effective_from']}")
    print(f"created_at (not hashed)     = {art['run_provenance']['created_at']}")
    print(f"train_cutoff           = {art['training_input']['train_cutoff_inclusive']}")
    print(f"joint cells / order    = {art['tables']['joint_cells_fit']} / {art['tables']['order_cells_fit']}")
    print(f"train player-games     = {art['tables']['train_player_games']}")


if __name__ == "__main__":
    main()

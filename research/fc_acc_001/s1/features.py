"""Minimal point-in-time candidate feature snapshot for Codex's three ranked
hypotheses (pitcher workload; confirmed-lineup playing time; contact matchup
vs expected starter exposure). Values are copied from the scored candidate the
model actually used at decision time; anything absent is the literal
'UNKNOWN' -- never imputed, never zero.

PROVENANCE LIMIT: the model-candidate values (projected_pa, scaled signals)
are NOT independently archived source observations -- production keeps no raw
bytes for them -- so every snapshot carries
``model_values_provenance: UNVERIFIED_PRODUCTION_VALUES``. Only ``workload``
is bound to an archived source, and it carries that source's observation time.
"""
import sources as S

UNKNOWN = "UNKNOWN"
BATTER_SIGNALS = ("lineup_slot", "platoon", "platoon_xwoba", "platoon_barrel_pct", "woba_ahead", "bullpen_fatigue")
PITCHER_SIGNALS = ("opp_team_k_pct", "same_hand_ratio", "l14_k_pct", "season_k_pct", "tto_penalty",
                   "avg_outs_per_start", "outs_rate")
REQUIRED_BY_FAMILY = {"hits": ("projected_pa", "lineup_assumed"),
                      "hits_runs_rbis": ("projected_pa", "lineup_assumed"),
                      "strikeouts": ("workload",), "pitcher_outs": ("workload",)}


class FeatureError(ValueError):
    pass


def snapshot(candidate_id, stat, raw_candidate, workload=None, *, captured_at, model_versions):
    S.ts(captured_at, "captured_at")                     # required, timezone-aware
    raw = raw_candidate or {}
    sig = raw.get("signals") or {}
    names = PITCHER_SIGNALS if stat in ("strikeouts", "pitcher_outs") else BATTER_SIGNALS
    feats = {
        "projected_pa": raw["projected_pa"] if raw.get("projected_pa") is not None else UNKNOWN,
        "lineup_assumed": raw["lineup_assumed"] if isinstance(raw.get("lineup_assumed"), bool) else UNKNOWN,
        "signals_scaled_0_100": {n: (sig[n] if n in sig else UNKNOWN) for n in names},
        "workload": workload if workload else UNKNOWN,
    }
    missing = [k for k in REQUIRED_BY_FAMILY.get(stat, ())
               if feats[k] == UNKNOWN or (k == "workload" and isinstance(feats[k], dict)
                                         and feats[k].get("status") == "UNAVAILABLE")]
    return {"candidate_id": candidate_id, "stat": stat, "features": feats, "captured_at": captured_at,
            "source": "model_scored_candidate" if raw_candidate else UNKNOWN,
            "model_values_provenance": "UNVERIFIED_PRODUCTION_VALUES",
            "model_versions": dict(model_versions or {}),
            "required_missing": missing, "status": "COMPLETE" if not missing else "MISSING_REQUIRED"}


def check(snap, *, candidate_id, stat, decision_at, archive=None, player_id=None, game_date=None):
    """None if the snapshot is admissible pregame evidence, else a rejection code.
    A workload block is re-derived from its archived source record; a pasted or
    edited workload dict is rejected."""
    if not isinstance(snap, dict) or snap.get("candidate_id") != candidate_id or snap.get("stat") != stat:
        return "FEATURE_IDENTITY_MISMATCH"
    try:
        if S.ts(snap.get("captured_at"), "captured_at") > S.ts(decision_at):
            return "FEATURE_AFTER_DECISION"
        wl = (snap.get("features") or {}).get("workload")
        if isinstance(wl, dict):
            import workload as W
            if S.ts(wl.get("source_observed_at"), "workload observed_at") > S.ts(decision_at):
                return "WORKLOAD_OBSERVED_AFTER_DECISION"
            if archive is None or wl.get("player_id") != str(player_id) or wl.get("game_date") != game_date:
                return "WORKLOAD_IDENTITY_MISMATCH"
            try:
                if W.pregame_workload(archive, wl.get("source_record"), player_id, game_date,
                                      decision_at=decision_at) != wl:
                    return "WORKLOAD_NOT_REDERIVABLE"
            except W.WorkloadError:
                return "WORKLOAD_SOURCE_REJECTED"
    except S.SourceError:
        return "FEATURE_TIME_INVALID"
    return None

"""Minimal point-in-time candidate feature snapshot for Codex's three ranked
hypotheses (pitcher workload; confirmed-lineup playing time; contact matchup
vs expected starter exposure). Values are copied from the scored candidate the
model actually used at decision time; anything absent is recorded as the
literal 'UNKNOWN' -- never imputed, never zero."""

UNKNOWN = "UNKNOWN"

# Batter: playing time + contact matchup. Pitcher: workload/K context.
# These are the model's own recorded 0-100 signal values (_sig stores SCALED
# values only; raw inputs are not retained by production).
BATTER_SIGNALS = ("lineup_slot", "platoon", "platoon_xwoba", "platoon_barrel_pct", "woba_ahead", "bullpen_fatigue")
PITCHER_SIGNALS = ("opp_team_k_pct", "same_hand_ratio", "l14_k_pct", "season_k_pct", "tto_penalty",
                   "avg_outs_per_start", "outs_rate")
REQUIRED_BY_FAMILY = {"hits": ("projected_pa", "lineup_assumed"),
                      "hits_runs_rbis": ("projected_pa", "lineup_assumed"),
                      "strikeouts": ("workload",), "pitcher_outs": ("workload",)}


def snapshot(candidate_id, stat, raw_candidate, workload=None, *, captured_at, model_versions):
    raw = raw_candidate or {}
    sig = raw.get("signals") or {}
    names = PITCHER_SIGNALS if stat in ("strikeouts", "pitcher_outs") else BATTER_SIGNALS
    feats = {
        "projected_pa": raw.get("projected_pa", UNKNOWN) if raw.get("projected_pa") is not None else UNKNOWN,
        "lineup_assumed": raw["lineup_assumed"] if isinstance(raw.get("lineup_assumed"), bool) else UNKNOWN,
        "signals_scaled_0_100": {n: (sig[n] if n in sig else UNKNOWN) for n in names},
        "workload": workload if workload else UNKNOWN,
    }
    missing = [k for k in REQUIRED_BY_FAMILY.get(stat, ())
               if feats[k] == UNKNOWN or (k == "workload" and isinstance(feats[k], dict)
                                         and feats[k].get("status") == "UNAVAILABLE")]
    return {"candidate_id": candidate_id, "stat": stat, "features": feats, "captured_at": captured_at,
            "source": "model_scored_candidate" if raw_candidate else UNKNOWN,
            "model_versions": dict(model_versions or {}),
            "required_missing": missing, "status": "COMPLETE" if not missing else "MISSING_REQUIRED"}

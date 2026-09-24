"""touchdown_opportunity_challenger: the connected consumer of F4_RED_ZONE.

RESEARCH ONLY. Predicts P(anytime rushing-or-receiving TD) for a player-game.
It is NOT a validated anytime-TD pricing system and nothing here changes B0,
a pick, or a selector.

Outcome definition (the harness ``anytime_td`` market): the player records
at least one RUSHING or RECEIVING touchdown. Passing TDs are out of scope
(a thrower does not "score"). Return, defensive and fumble-recovery TDs are
NOT in this outcome although FanDuel's anytime-TD market settles them as a
win -- see the README settlement section for the measured size of that gap.

Development history on DEV_2016_2022 (kept, not hidden):

1. First declared form ``rz_share_x_team``:
       E[opp_b] = share_b * team_plays_b
       lambda   = c * sum_b E[opp_b] * conv[pos][b]
   over the eight DISJOINT buckets {target, carry} x {1-5, 6-10, 11-20,
   21-99 yards from the end zone}. ``share_b``: the player's L8 shrunk share
   of his team's plays of that bucket (player opportunity); ``team_plays_b``:
   the team's L8 shrunk plays of that bucket per game (team scoring
   opportunity); ``conv[pos][b]``: DEV league TD rate per opportunity for the
   position group (conversion efficiency). On DEV it LOST to ``volume_only``
   (the same machinery with no red-zone split): log loss 0.5090 vs 0.5043.
2. A DEV probe showed the red-zone tilt points the right way (players the RZ
   form rates far above volume do score more often than volume predicts)
   but the unpooled RZ form over-reacts roughly 3-4x. A zone-split variant
   shrunk toward the positional mix preferred ever-larger shrinkage
   (m_frac grid monotone), i.e. the player-specific split alone is noise.
3. PRIMARY (declared after DEV development, before any holdout scoring):
   ``rz_blend``, a geometric pool of the two DEV-fitted pieces,
       lambda = c * lambda_volume^(1 - w) * lambda_rz_share_x_team^w
   with ``w`` and ``c`` fitted jointly on DEV. ``w = 0`` would mean the
   red-zone factor carries nothing; ``w`` is reported.

Probability links (all variants):
    P(1+ TD) = 1 - exp(-lambda)                (Poisson link)
    P(2+ TD) = 1 - exp(-lambda) * (1 + lambda)   (RESEARCH distribution only;
               always <= P(1+), not a validated 2+ TD pricing model)

Ablations (each with its own DEV-fitted ``c``):

* ``rz_share_x_team`` -- the first declared form (above).
* ``rz_direct``      -- E[opp_b] = the player's own L8 shrunk per-game rate.
* ``volume_only``    -- NO red-zone information: L8 targets/game and
                        carries/game x the position's all-field TD rate per
                        target/carry. The control that says whether the
                        red-zone split itself carries information.
* ``rz_zone_split``  -- the player's L8 targets/carries per game split across
                        the four zones by HIS OWN zone mix, shrunk toward the
                        positional mix with ``m_frac`` pseudo-opportunities
                        (m_frac -> inf equals ``volume_only`` exactly).
* ``rz_team_conv``   -- ``rz_share_x_team`` with red-zone buckets scaled by
                        the team's shrunk TD-per-trip relative to the league.
* ``closing_line_proxy`` -- PRIMARY x (implied team total / DEV mean)^gamma
                        from games.csv CLOSING spread/total. Label
                        CLOSING_LINE_PROXY_RETROSPECTIVE: a closing line is
                        not available at a pregame decision time.

UNKNOWN handling: if any input a variant needs is UNKNOWN (no prior
appearance, no prior team game, no prior league history), the challenger
falls back to B0 for that row and records the fallback; it never treats
UNKNOWN as zero.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from nfl.research.tier1 import contract as C
from nfl.research.tier1.touchdown_features import BUCKETS, RZ_BUCKETS, TYPES, ZONES

CONSUMER_ID = "touchdown_opportunity_challenger"
PRIMARY = "rz_blend"
VARIANTS = ("rz_blend", "rz_share_x_team", "rz_direct", "volume_only", "rz_zone_split",
            "rz_team_conv", "closing_line_proxy")
CLOSING_LABEL = "CLOSING_LINE_PROXY_RETROSPECTIVE"
POSITIONS = ("RB", "WR", "TE", "QB", "OTHER")

# ---------------------------------------------------------------------------
# DEV_2016_2022-fitted parameters (fitted 2026-09-24, before any holdout
# scoring). Filled by
# `touchdown_evaluate.py --stage fit` and committed BEFORE the holdout run;
# `--stage full` refits on DEV and refuses to run if these drift.
# ---------------------------------------------------------------------------
PARAMS: dict[str, Any] = {   'fit_partition': 'DEV_2016_2022',
    'conv': {   'RB': {   'tgt_Z5': 0.448421,
                          'tgt_Z10': 0.242303,
                          'tgt_Z20': 0.092699,
                          'tgt_OUT': 0.008011,
                          'car_Z5': 0.408529,
                          'car_Z10': 0.111569,
                          'car_Z20': 0.043403,
                          'car_OUT': 0.00468},
                'WR': {   'tgt_Z5': 0.442781,
                          'tgt_Z10': 0.294747,
                          'tgt_Z20': 0.147898,
                          'tgt_OUT': 0.020128,
                          'car_Z5': 0.450704,
                          'car_Z10': 0.192308,
                          'car_Z20': 0.104895,
                          'car_OUT': 0.01414},
                'TE': {   'tgt_Z5': 0.5068,
                          'tgt_Z10': 0.325077,
                          'tgt_Z20': 0.164749,
                          'tgt_OUT': 0.011458,
                          'car_Z5': 0.537037,
                          'car_Z10': 0.25,
                          'car_Z20': 0.019231,
                          'car_OUT': 0.008475},
                'QB': {   'tgt_Z5': 0.416667,
                          'tgt_Z10': 0.666667,
                          'tgt_Z20': 0.411765,
                          'tgt_OUT': 0.0,
                          'car_Z5': 0.537037,
                          'car_Z10': 0.215164,
                          'car_Z20': 0.067337,
                          'car_OUT': 0.004903},
                'OTHER': {   'tgt_Z5': 0.735294,
                             'tgt_Z10': 0.4,
                             'tgt_Z20': 0.3,
                             'tgt_OUT': 0.05,
                             'car_Z5': 0.5,
                             'car_Z10': 0.0,
                             'car_Z20': 0.0,
                             'car_OUT': 0.0}},
    'conv_all': {   'RB': {'tgt': 0.029972, 'car': 0.031328},
                    'WR': {'tgt': 0.04781, 'car': 0.037907},
                    'TE': {'tgt': 0.055391, 'car': 0.084677},
                    'QB': {'tgt': 0.164706, 'car': 0.056403},
                    'OTHER': {'tgt': 0.268657, 'car': 0.026786}},
    'scale_c': {   'rz_blend': 0.98,
                   'rz_share_x_team': 0.98,
                   'rz_direct': 0.97,
                   'volume_only': 0.97,
                   'rz_zone_split': 0.98,
                   'rz_team_conv': 0.97,
                   'closing_line_proxy': 0.97},
    'closing_mean_implied': 22.8882,
    'closing_gamma': 1.05,
    'm_frac': 160.0,
    'w_blend': 0.2}


def p_anytime(lam: float) -> float:
    return 1.0 - math.exp(-lam)


def p_two_plus(lam: float) -> float:
    return 1.0 - math.exp(-lam) * (1.0 + lam)


def _known(*values: Any) -> bool:
    return not any(C.is_unknown(v) for v in values)


def expected_opportunities(features: Mapping[str, Any], form: str) -> dict[str, Any] | None:
    """bucket -> expected opportunities this game, or None if any input is UNKNOWN."""
    out: dict[str, Any] = {}
    for b in BUCKETS:
        if form == "share_x_team":
            share, team = features[f"L8_share_{b}"], features[f"team_L8_plays_pg_{b}"]
            if not _known(share, team):
                return None
            out[b] = share * team
        elif form == "direct":
            value = features[f"L8_pg_{b}"]
            if not _known(value):
                return None
            out[b] = value
        else:
            raise ValueError(form)
    return out


def raw_lambda(features: Mapping[str, Any], variant: str, params: Mapping[str, Any],
               implied_ratio: float | None = None) -> float | None:
    """Unscaled lambda (before ``c``) for one row, or None when it cannot be formed."""
    pos = features["position_group"]
    if variant in ("rz_blend", "closing_line_proxy"):
        vol = raw_lambda(features, "volume_only", params)
        rz = raw_lambda(features, "rz_share_x_team", params)
        if vol is None or rz is None:
            return None
        w = params["w_blend"]
        lam = vol ** (1.0 - w) * rz ** w
        if variant == "closing_line_proxy":
            if implied_ratio is None:
                return None
            lam *= implied_ratio ** params["closing_gamma"]
        return lam
    if variant == "volume_only":
        total = 0.0
        for kind in TYPES:
            values = [features[f"L8_pg_{kind}_{z}"] for z in ZONES]
            if not _known(*values):
                return None
            total += sum(values) * params["conv_all"][pos][kind]
        return total
    if variant == "rz_zone_split":
        total = 0.0
        conv = params["conv"][pos]
        m = params["m_frac"]
        for kind in TYPES:
            bs = [f"{kind}_{z}" for z in ZONES]
            vol = [features[f"L8_pg_{b}"] for b in bs]
            raw = [features[f"L8_raw_{b}"] for b in bs]
            prior = [features[f"pos_frac_{b}"] for b in bs]
            if not _known(*vol, *raw, *prior):
                return None
            n = sum(raw)
            for b, r, pr in zip(bs, raw, prior):
                frac = (r + m * pr) / (n + m)
                total += sum(vol) * frac * conv[b]
        return total
    form = "direct" if variant == "rz_direct" else "share_x_team"
    opps = expected_opportunities(features, form)
    if opps is None:
        return None
    conv = params["conv"][pos]
    rz_mult = 1.0
    if variant == "rz_team_conv":
        team, league = features["team_L8_rz_td_per_trip"], features["league_rz_td_per_trip"]
        if not _known(team, league) or league <= 0:
            return None
        rz_mult = team / league
    return sum(opps[b] * conv[b] * (rz_mult if b in RZ_BUCKETS else 1.0) for b in BUCKETS)


def predict(feature_rows: Mapping[tuple, Mapping[str, Any]], scored: list[Mapping[str, Any]],
            variant: str, params: Mapping[str, Any] = PARAMS, *,
            implied: Mapping[tuple, float] | None = None, scale: float | None = None
            ) -> tuple[dict[tuple, float], dict[tuple, float], dict[str, int]]:
    """Predictions for the harness rows in ``scored``.

    Returns (p_anytime by row_key, lambda by row_key (model rows only),
    counts). Rows whose inputs are UNKNOWN fall back to B0 (when B0 exists)
    and are counted as ``fallback_b0``; they carry no lambda.
    """
    c = params["scale_c"].get(variant, 1.0) if scale is None else scale
    preds: dict[tuple, float] = {}
    lams: dict[tuple, float] = {}
    counts = {"model": 0, "fallback_b0": 0, "no_prediction": 0}
    for r in scored:
        key = (r["season"], r["week"], r["game_id"], r["player_id"])
        row = feature_rows.get(key)
        ratio = None
        if variant == "closing_line_proxy" and implied is not None and key in implied:
            ratio = implied[key] / params["closing_mean_implied"]
        lam = raw_lambda(row["features"], variant, params, ratio) if row else None
        if lam is None:
            if r.get("b0") is not None:
                preds[key] = r["b0"]
                counts["fallback_b0"] += 1
            else:
                counts["no_prediction"] += 1
            continue
        lams[key] = c * lam
        preds[key] = p_anytime(c * lam)
        counts["model"] += 1
    return preds, lams, counts

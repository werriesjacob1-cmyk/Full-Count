"""Tier 1 workstream C consumer: `team_context_challenger`.

Pre-declared form (fixed BEFORE any holdout scoring; parameters fitted on
DEV_2016_2022 only and frozen in `team_context_dev_params.json`):

    team level   E_S[y] = OLS_DEV( y ~ 1 + base_y_pg + features(S) )
                 y = team dropbacks (receptions) or team gross passing
                 yards (passing_yards, receiving_yards); S subset of
                 {F1, F5, F6, F7}; base_y_pg = the team's strictly-prior
                 per-game y over its last 10 REG games.
    player level pred = k * B0 * clip(E_S / hist_y, 0.5, 2) ** alpha
                                 * F10_index ** beta
                 k      = the harness DEV scale control (so "vs scale
                          control" isolates the factor multipliers),
                 hist_y = mean actual team y in the games that formed the
                          player's B0 window (team volume the B0 already
                          reflects),
                 F10_index = opponent-adjusted allowed index for the
                          player's position (WR/TE/RB receptions or yards;
                          QB passing yards) shrunk toward 1 with m
                          pseudo-games.
                 alpha, beta, m: DEV grid search of MAE.
                 F6 columns: dome, retractable, turf, and for OUTDOOR games
                 the GFS MOS forecast wind (kt) and 6-h PoP from the 12Z run
                 of the day before the game (`team_context_weather`).

Ablation configs: VOLUME_BASE (S empty: team-volume ratio only, no Tier 1
factor), F1, F5, F6, F7 (each with the volume base), F10 (index only),
ALL_NO_MARKET (F5+F6+F7+F10) and ALL (F1+F5+F6+F7+F10).

UNKNOWN handling: when any input a config needs is UNKNOWN, that row's
multiplier is 1 (prediction = k * B0, the consumer's base) and the fallback
reason is recorded. Nothing UNKNOWN is ever treated as 0.

Market/model separation: every config containing F1 has
``uses_market_input=True``. Historically F1 is a CLOSING-line proxy
(retrospective); such a config must never be used as independent evidence of
value against the same book's player prices.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any, Iterable, Mapping

import numpy as np

from nfl.research.tier1 import harness
from nfl.research.tier1.contract import UNKNOWN
from nfl.research.tier1.team_context_data import norm_team
from nfl.research.tier1.team_context_features import shrunk_index

TEAM_FACTORS = ("F1", "F5", "F6", "F7")
CONFIGS: dict[str, tuple[str, ...]] = {
    "VOLUME_BASE": (),
    "F1": ("F1",), "F5": ("F5",), "F6": ("F6",), "F7": ("F7",),
    "F10": ("F10",),
    "ALL_NO_MARKET": ("F5", "F6", "F7", "F10"),
    "ALL": ("F1", "F5", "F6", "F7", "F10"),
}
FACTOR_IDS = {"F1": "F1_GAME_CONTEXT", "F5": "F5_PASS_TENDENCY_PACE", "F6": "F6_ENVIRONMENT",
              "F7": "F7_REST_TRAVEL", "F10": "F10_OPPONENT_POSITION"}
MARKET_TEAM_TARGET = {"receptions": "dropbacks", "receiving_yards": "pass_yards",
                      "passing_yards": "pass_yards"}
MARKET_F10_STAT = {"receptions": "{pos}_receptions", "receiving_yards": "{pos}_yards",
                   "passing_yards": "pass_yards"}
RATIO_CLIP = (0.5, 2.0)
ALPHA_GRID = [i / 20 for i in range(0, 31)]       # 0.00 .. 1.50
BETA_GRID = [i / 20 for i in range(0, 31)]
PSEUDO_GAMES_GRID = [1, 2, 4, 8, 16, 32, 64, 128]
ATTRIBUTION_EPS = 0.005                           # |log multiplier| that counts as "changed"


def uses_market_input(config: str) -> bool:
    return "F1" in CONFIGS[config]


# --------------------------------------------------------------------------- team design
def _known(*values: Any) -> bool:
    return all(v != UNKNOWN for v in values)


def factor_columns(factor: str, ctx: Mapping[str, Any], tf: Mapping[str, Any]) -> list[float] | None:
    """Numeric design columns for one team factor; None when any input is UNKNOWN."""
    if factor == "F1":
        vals = (ctx["f1_implied_team_total_closing"], ctx["f1_team_margin_closing"])
        return [float(v) for v in vals] if _known(*vals) else None
    if factor == "F5":
        vals = tuple(tf.get(k, UNKNOWN) for k in ("f5_proe", "f5_neutral_dropback_rate",
                                                  "f5_plays_pg", "f5_neutral_sec_per_play"))
        return [float(v) for v in vals] if _known(*vals) else None
    if factor == "F6":
        roof, surface = ctx["f6_roof_type"], ctx["f6_surface"]
        if not _known(roof, surface):
            return None
        wind = pop = 0.0  # no weather exposure under a fixed or retractable roof
        if roof == "outdoors":
            wind, pop = ctx.get("f6_fcst_wind_kt", UNKNOWN), ctx.get("f6_fcst_pop6", UNKNOWN)
            if not _known(wind, pop):
                return None  # outdoor game without a provably pre-kickoff forecast
        return [float(roof == "dome"), float(roof == "retractable"), float(surface == "turf"),
                float(wind), float(pop) / 100.0]
    if factor == "F7":
        keys = ("f7_rest_days", "f7_short_week", "f7_post_bye", "f7_rest_diff", "f7_travel_km",
                "f7_abs_tz_change", "f7_west_to_east_early", "f7_international")
        vals = tuple(ctx[k] for k in keys)
        if not _known(*vals):
            return None
        rest, short, bye, diff, km, tz, w2e, intl = vals
        return [min(max(float(rest), 4.0), 14.0), float(short), float(bye),
                min(max(float(diff), -7.0), 7.0), float(km) / 1000.0, float(tz), float(w2e),
                float(intl)]
    raise ValueError(factor)


def team_design_row(ctx: Mapping[str, Any], tf: Mapping[str, Any], target: str,
                    factors: Iterable[str]) -> tuple[list[float] | None, str | None]:
    base = tf.get(f"base_{target}_pg", UNKNOWN)
    if base == UNKNOWN:
        return None, "TEAM_PRIOR_HISTORY_UNKNOWN"
    row = [1.0, float(base)]
    for factor in factors:
        cols = factor_columns(factor, ctx, tf)
        if cols is None:
            return None, f"{factor}_UNKNOWN"
        row.extend(cols)
    return row, None


def fit_volume_model(team_rows: list[Mapping[str, Any]], target: str, factors: tuple[str, ...],
                     dev: tuple[int, int] = harness.PARTITIONS["DEV_2016_2022"]) -> list[float]:
    """OLS coefficients on DEV REG team-games with every input known."""
    X, y = [], []
    for r in team_rows:
        if not (dev[0] <= r["season"] <= dev[1]) or r["season_type"] != "REG":
            continue
        x, _why = team_design_row(r["ctx"], r["tf"], target, factors)
        if x is None:
            continue
        X.append(x)
        y.append(r[target])
    coef, *_ = np.linalg.lstsq(np.asarray(X), np.asarray(y), rcond=None)
    return [float(c) for c in coef]


def predict_volume(ctx, tf, target, factors, coef) -> tuple[Any, str | None]:
    x, why = team_design_row(ctx, tf, target, factors)
    if x is None:
        return UNKNOWN, why
    return max(1.0, float(np.dot(x, coef))), None


# --------------------------------------------------------------------------- B0 windows
def b0_windows(rows: list[Mapping[str, Any]], market: str, *, window: int = 5,
               min_appearances: int = 3) -> dict[tuple, list[tuple[str, str]]]:
    """row_key -> [(game_id, team)] of the appearances that formed B0.

    Mirrors `harness.b0_rolling_mean` exactly (same deque, same REG/POST
    rule); `check_b0_parity` proves it on the real population."""
    _actual_fn, role_fn = harness.MARKETS[market]
    history: dict[str, deque] = defaultdict(lambda: deque(maxlen=window))
    out = {}
    for row in rows:
        if role_fn(row) <= 0:
            continue
        past = history[row["player_id"]]
        if row["season_type"] == "REG" and len(past) >= min_appearances:
            out[harness.row_key(row)] = list(past)
        past.append((row["game_id"], norm_team(row["team"])))
    return out


def check_b0_parity(scored: list[Mapping[str, Any]], windows: Mapping[tuple, list],
                    rows: list[Mapping[str, Any]], market: str) -> int:
    """Recompute B0 from the windows and require equality with the harness."""
    actual_fn, _ = harness.MARKETS[market]
    actual = {(r["game_id"], r["player_id"]): actual_fn(r) for r in rows}
    checked = 0
    for r in scored:
        if r["b0"] is None:
            continue
        win = windows[harness.row_key(r)]
        b0 = sum(actual[(g, r["player_id"])] for g, _t in win) / len(win)
        if abs(b0 - r["b0"]) > 1e-9:
            raise AssertionError(f"B0 window parity failed for {harness.row_key(r)}")
        checked += 1
    return checked


# --------------------------------------------------------------------------- player inputs
def player_inputs(scored: list[Mapping[str, Any]], windows: Mapping[tuple, list], market: str,
                  team_actual: Mapping[tuple[str, str], Mapping[str, float]],
                  volume_pred: Mapping[str, Mapping[tuple[str, str], Any]],
                  def_feats: Mapping[tuple[str, str], Mapping[str, Any]],
                  positions: Mapping[str, str]) -> list[dict[str, Any]]:
    """One record per scored row with B0: hist_y, E_S per config, F10 sums."""
    target = MARKET_TEAM_TARGET[market]
    out = []
    for r in scored:
        if r["b0"] is None:
            continue
        key = harness.row_key(r)
        team = norm_team(r["team"])
        rec: dict[str, Any] = {"key": key, "season": r["season"], "week": r["week"],
                               "game_id": r["game_id"], "team": team, "b0": r["b0"],
                               "actual": r["actual"], "position": r["position"]}
        vals = [team_actual.get(gt, {}).get(target) for gt in windows[key]]
        rec["hist_y"] = sum(vals) / len(vals) if vals and None not in vals else UNKNOWN
        rec["E"] = {name: preds.get((r["game_id"], team), UNKNOWN)
                    for name, preds in volume_pred.items()}
        pos = positions.get(r["player_id"], r["position"])
        pos = "RB" if pos in ("FB", "HB") else pos
        opp = norm_team(r["opponent_team"])
        df = def_feats.get((r["game_id"], opp))
        stat = None
        if market == "passing_yards" and pos == "QB":
            stat = "pass_yards"
        elif market != "passing_yards" and pos in ("WR", "TE", "RB"):
            stat = MARKET_F10_STAT[market].format(pos=pos)
        if stat is None or df is None:
            rec["f10"] = UNKNOWN
            rec["f10_reason"] = "POSITION_NOT_COVERED" if stat is None else "OPPONENT_HISTORY_UNKNOWN"
        else:
            a, e = df[f"f10_{stat}_adj_allowed_sum"], df[f"f10_{stat}_adj_expected_sum"]
            rec["f10"] = (a, e, df["def_adjusted_games_n"]) if _known(a, e) and e > 0 else UNKNOWN
            rec["f10_reason"] = None if rec["f10"] != UNKNOWN else "OPPONENT_HISTORY_UNKNOWN"
        out.append(rec)
    return out


def _team_log_ratio(rec: Mapping[str, Any], vol_key: str) -> float | None:
    e, h = rec["E"].get(vol_key, UNKNOWN), rec["hist_y"]
    if e == UNKNOWN or h == UNKNOWN or h <= 0:
        return None
    return math.log(min(max(e / h, RATIO_CLIP[0]), RATIO_CLIP[1]))


def _f10_log_index(rec: Mapping[str, Any], m: float) -> float | None:
    if rec["f10"] == UNKNOWN:
        return None
    a, e, n = rec["f10"]
    idx = shrunk_index(a, e, n, m)
    return math.log(idx) if idx != UNKNOWN and idx > 0 else None


def vol_key_for(config: str) -> str | None:
    """Name of the volume model a config uses (None: no team-volume ratio)."""
    team = tuple(f for f in CONFIGS[config] if f in TEAM_FACTORS)
    if config == "F10":
        return None
    return "+".join(team) if team else "VOLUME_BASE"


def multipliers(recs: list[Mapping[str, Any]], config: str, alpha: float, beta: float, m: float
                ) -> tuple[np.ndarray, list[str | None]]:
    """Multiplicative factor per record (1.0 on fallback) and fallback reasons."""
    vk = vol_key_for(config)
    use_f10 = "F10" in CONFIGS[config]
    mult, reasons = np.ones(len(recs)), []
    for i, rec in enumerate(recs):
        log_m, why = 0.0, None
        if vk is not None:
            lr = _team_log_ratio(rec, vk)
            if lr is None:
                why = "TEAM_VOLUME_UNKNOWN"
            else:
                log_m += alpha * lr
        if use_f10 and why is None:
            li = _f10_log_index(rec, m)
            if li is None:
                why = rec.get("f10_reason") or "F10_UNKNOWN"
            else:
                log_m += beta * li
        if why is not None:
            log_m = 0.0  # whole-row fallback to k*B0, recorded
        mult[i] = math.exp(log_m)
        reasons.append(why)
    return mult, reasons


def fit_config(recs_dev: list[Mapping[str, Any]], config: str, k: float) -> dict[str, float]:
    """DEV grid search of (alpha, beta, m) minimizing MAE of k*B0*mult."""
    b0 = np.array([r["b0"] for r in recs_dev]) * k
    act = np.array([r["actual"] for r in recs_dev])
    vk = vol_key_for(config)
    use_f10 = "F10" in CONFIGS[config]
    team_lr = np.array([(_team_log_ratio(r, vk) if vk else 0.0) or 0.0 for r in recs_dev])
    team_ok = np.array([(vk is None) or (_team_log_ratio(r, vk) is not None) for r in recs_dev])
    best = (float("inf"), 0.0, 0.0, 0.0)
    for m in (PSEUDO_GAMES_GRID if use_f10 else [0.0]):
        if use_f10:
            li = [_f10_log_index(r, m) for r in recs_dev]
            f10_lr = np.array([v or 0.0 for v in li])
            ok = team_ok & np.array([v is not None for v in li])
        else:
            f10_lr, ok = np.zeros(len(recs_dev)), team_ok
        for alpha in (ALPHA_GRID if vk else [0.0]):
            for beta in (BETA_GRID if use_f10 else [0.0]):
                logm = np.where(ok, alpha * team_lr + beta * f10_lr, 0.0)
                mae = float(np.mean(np.abs(b0 * np.exp(logm) - act)))
                if mae < best[0] - 1e-12:
                    best = (mae, alpha, beta, float(m))
    return {"dev_mae": best[0], "alpha": best[1], "beta": best[2], "pseudo_games": best[3]}


def predict_config(recs: list[Mapping[str, Any]], config: str, params: Mapping[str, float], k: float
                   ) -> tuple[dict[tuple, float], dict[tuple, str | None]]:
    mult, reasons = multipliers(recs, config, params["alpha"], params["beta"],
                                params["pseudo_games"])
    preds = {rec["key"]: k * rec["b0"] * float(mult[i]) for i, rec in enumerate(recs)}
    fallbacks = {rec["key"]: reasons[i] for i, rec in enumerate(recs)}
    return preds, fallbacks


def attribution_rows(recs: list[Mapping[str, Any]], config: str, params: Mapping[str, float]
                     ) -> dict[tuple, dict[str, float]]:
    """Per-row log-multiplier contribution by factor for a config.

    Team factor f: alpha * (log E_S - log E_{S without f}) using the fitted
    leave-one-out volume models; the volume base itself is reported as
    'VOLUME_BASE'; F10: beta * log(index). Rows that fell back get {}.
    """
    vk = vol_key_for(config)
    team = [f for f in CONFIGS[config] if f in TEAM_FACTORS]
    out = {}
    mult, reasons = multipliers(recs, config, params["alpha"], params["beta"], params["pseudo_games"])
    for i, rec in enumerate(recs):
        if reasons[i] is not None:
            out[rec["key"]] = {}
            continue
        contrib: dict[str, float] = {}
        if vk is not None:
            full = _team_log_ratio(rec, vk) or 0.0
            base = _team_log_ratio(rec, "VOLUME_BASE")
            contrib["VOLUME_BASE"] = params["alpha"] * (base or 0.0)
            for f in team:
                rest = "+".join(x for x in team if x != f) or "VOLUME_BASE"
                loo = _team_log_ratio(rec, rest)
                if loo is not None:
                    contrib[f] = params["alpha"] * (full - loo)
        if "F10" in CONFIGS[config]:
            contrib["F10"] = params["beta"] * (_f10_log_index(rec, params["pseudo_games"]) or 0.0)
        out[rec["key"]] = contrib
    return out


def changed_by(attrib: Mapping[tuple, Mapping[str, float]]) -> dict[tuple, list[str]]:
    return {key: [FACTOR_IDS.get(f, f) for f, v in c.items() if abs(v) > ATTRIBUTION_EPS]
            for key, c in attrib.items()}

"""F17 advanced receiving efficiency -> receiving-yards research challenger.

Research only; never B0. Mechanism
----------------------------------
B0 (the live rule: mean receiving yards over the last 5 role appearances,
min 3) factors EXACTLY as ``tgt5 * ypt5``: tgt5 = mean targets and ypt5 =
yards per target over the same 5 games. ypt5 rests on ~20-30 targets and is
noisy. F17 replaces part of ypt5 with an efficiency estimate built only from
targets in games strictly before the target game:

* ``xypt_recent`` -- nflfastR-EXPECTED yards per target over the player's last
  RECENT_GAMES games with a target, shrunk (K_X targets) toward the prior
  season's position mean. This is role/usage efficiency: how deep and how
  catchable his targets are, with expected YAC. (Derived from model outputs.)
* ``skill`` -- persistent yards OVER expectation per target (catch-over-
  expected + YAC-over-expected in one number), from seasons S, S-1, S-2 with
  weights 1.0 / 0.6 / 0.3, shrunk to 0 with K_S targets. (Observed minus
  derived.)
* ``ypt_F17_FULL = xypt_recent + skill``; ``ypt_F17_DEPTH = xypt_recent``.

Control SIMPLE_EFF: ``ypt_simple`` = same long window, RAW yards per target,
shrunk (K_S) toward the prior-season position mean -- a better-estimated
efficiency WITHOUT the advanced information. F17 must beat it to be credited.

Prediction for mode m: ``k * tgt5 * ((1 - w_m) * ypt5 + w_m * ypt_m)`` with
k the harness DEV scale control and w_m in [0, 1] fitted on DEV only; w_m = 0
reproduces ``k * B0`` exactly. A row lacking the mode's inputs falls back to
exactly ``k * B0`` and its reason is recorded.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from typing import Iterable, Mapping

from nfl.research.tier2.player_efficiency_f17_data import POSITIONS

RECENT_GAMES = 8
K_X = 20.0
K_S = 150.0
SEASON_WEIGHTS = {0: 1.0, 1: 0.6, 2: 0.3}
B0_WINDOW, B0_MIN = 5, 3
MODES = ("SIMPLE_EFF", "F17_DEPTH", "F17_FULL")
W_GRID = [i / 20 for i in range(21)]


class B0Index:
    """Harness-rule B0 at any (player, season, week) from strictly earlier role
    appearances (role = max(targets, receptions) > 0; REG and POST history)."""

    def __init__(self, weekly_rows: Iterable[Mapping]):
        self.hist: dict[str, list] = defaultdict(list)
        for r in weekly_rows:
            if max(r["targets"], r["receptions"]) > 0:
                self.hist[r["player_id"]].append(((r["season"], r["week"]), r["receiving_yards"], r["targets"]))
        for v in self.hist.values():
            v.sort(key=lambda x: x[0])
        self.keys = {p: [x[0] for x in v] for p, v in self.hist.items()}

    def state(self, pid: str, season: int, week: int) -> dict | None:
        v = self.hist.get(pid)
        if not v:
            return None
        i = bisect.bisect_left(self.keys[pid], (season, week))
        win = v[max(0, i - B0_WINDOW):i]
        if len(win) < B0_MIN:
            return None
        n, ys, ts = len(win), sum(x[1] for x in win), sum(x[2] for x in win)
        return {"b0": ys / n, "tgt5": ts / n, "ypt5": ys / ts if ts > 0 else None, "n": n}


class EfficiencyIndex:
    """Strictly prior per-player efficiency from per-game target aggregates."""

    def __init__(self, game_rows: Iterable[Mapping], positions: Mapping[str, str]):
        self.games: dict[str, list] = defaultdict(list)
        pos_season = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])   # T, Y, TX, XY
        for g in game_rows:
            self.games[g["player_id"]].append(g)
            pos = positions.get(g["player_id"])
            if pos in POSITIONS:
                c = pos_season[(g["season"], pos)]
                c[0] += g["T"]; c[1] += g["Y"]; c[2] += g["TX"]; c[3] += g["XY"]
        for v in self.games.values():
            v.sort(key=lambda g: (g["season"], g["week"]))
        self.keys = {p: [(g["season"], g["week"]) for g in v] for p, v in self.games.items()}
        self.pos_prior = {k: {"ypt": c[1] / c[0], "xypt": c[3] / c[2]}
                          for k, c in pos_season.items() if c[0] > 0 and c[2] > 0}

    def features(self, pid: str, pos: str, season: int, week: int) -> dict:
        prior = self.pos_prior.get((season - 1, pos))
        if pos not in POSITIONS or prior is None:
            return {"status": "NO_POSITION_PRIOR"}
        v = self.games.get(pid, [])
        i = bisect.bisect_left(self.keys.get(pid, []), (season, week))
        past = [g for g in v[:i] if season - g["season"] in SEASON_WEIGHTS]
        if not past:
            return {"status": "NO_PRIOR_TARGETS"}
        recent = [g for g in past if g["TX"] > 0][-RECENT_GAMES:]
        tx_r, xy_r = sum(g["TX"] for g in recent), sum(g["XY"] for g in recent)
        w = [(SEASON_WEIGHTS[season - g["season"]], g) for g in past]
        wT = sum(a * g["T"] for a, g in w)
        wY = sum(a * g["Y"] for a, g in w)
        wTX = sum(a * g["TX"] for a, g in w)
        wOE = sum(a * (g["YX"] - g["XY"]) for a, g in w)
        wCOE = sum(a * (g["CX"] - g["CP"]) for a, g in w)
        wYOE = sum(a * g["YOE_C"] for a, g in w)
        xypt = (xy_r + K_X * prior["xypt"]) / (tx_r + K_X)
        skill = wOE / (wTX + K_S)
        return {"status": "OK", "xypt_recent": xypt, "skill": skill,
                "ypt_simple": (wY + K_S * prior["ypt"]) / (wT + K_S),
                "catch_over_expected_per_target": wCOE / (wTX + K_S),
                "yac_over_expected_per_target": wYOE / (wTX + K_S),
                "recent_targets": tx_r, "weighted_targets": wT, "position_prior": prior}


def mode_ypt(feat: Mapping, mode: str) -> float | None:
    if feat.get("status") != "OK":
        return None
    if mode == "SIMPLE_EFF":
        return feat["ypt_simple"]
    if mode == "F17_DEPTH":
        return feat["xypt_recent"]
    if mode == "F17_FULL":
        return feat["xypt_recent"] + feat["skill"]
    raise ValueError(mode)


def predict(b0s: Mapping, feat: Mapping, mode: str, k: float, w: float) -> tuple[float, str]:
    """(prediction, reason). Falls back to exactly k*B0 when inputs are missing."""
    base = k * b0s["b0"]
    ypt = mode_ypt(feat, mode)
    if ypt is None:
        return base, feat.get("status", "NO_FEATURES")
    if b0s["ypt5"] is None:
        return base, "B0_WINDOW_HAS_NO_TARGETS"
    return k * b0s["tgt5"] * ((1.0 - w) * b0s["ypt5"] + w * ypt), "OK"

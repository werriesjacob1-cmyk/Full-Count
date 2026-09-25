"""Tier 1 workstream C: strictly-prior team-context feature builders.

Factors F1 (game context), F5 (pass tendency / pace), F6 (environment),
F7 (rest / travel) and F10 (opponent allowed-by-position).

Every historical feature for a target (season, week) is computed from games
that sort STRICTLY before (season, week) (`contract.assert_strictly_prior` is
called on every history slice). Values the sources do not support are
`contract.UNKNOWN`.

Scope statements that must travel with these features:

* F1 historical values are nflverse CLOSING lines ->
  ``CLOSING_LINE_PROXY_RETROSPECTIVE``: they could not have been used by a
  pre-kickoff prediction; a consumer reading them sets
  ``uses_market_input=True`` and may never be used as independent evidence
  of value against that same book's player prices.
* F10 is opponent DEFENSE allowed-by-receiver-POSITION (WR/TE/RB) and QB
  passing yards allowed. It is NOT an individual WR-vs-CB matchup, and no
  slot/wide split exists (no alignment data in these sources).
* F6 roof/surface are static stadium facts; games.csv temp/wind are OBSERVED
  weather of unknown vintage and are never used here.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from typing import Any, Iterable, Mapping

from nfl.research.tier1.contract import (HISTORICAL_PRIOR_WEEKS, UNKNOWN, assert_strictly_prior,
                                         feature_row)

TEAM_WINDOW = 10      # prior REG team-games for F5 / team volume base (pre-declared)
DEFENSE_WINDOW = 16   # prior REG games of a defense for F10 (pre-declared)
MIN_TEAM_GAMES = 4    # below this the team features are UNKNOWN
MIN_DEF_GAMES = 1
POS_GROUPS = ("WR", "TE", "RB")
POS_STATS = ("targets", "receptions", "yards", "epa")
F1_LABEL = "CLOSING_LINE_PROXY_RETROSPECTIVE"


def _key(row: Mapping[str, Any]) -> tuple[int, int]:
    return (int(row["season"]), int(row["week"]))


class _History:
    """Per-entity REG game history sorted by (season, week), for prior slicing."""

    def __init__(self, rows: Iterable[Mapping[str, Any]], entity: str):
        self.by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            if row.get("season_type", "REG") == "REG":
                self.by[row[entity]].append(row)
        self.keys: dict[str, list[tuple[int, int]]] = {}
        for ent, items in self.by.items():
            items.sort(key=_key)
            self.keys[ent] = [_key(r) for r in items]

    def prior(self, entity: str, target: tuple[int, int], window: int) -> list[Mapping[str, Any]]:
        keys = self.keys.get(entity, [])
        end = bisect.bisect_left(keys, target)
        out = self.by.get(entity, [])[max(0, end - window):end]
        assert_strictly_prior((_key(r) for r in out), target)
        return out


# --------------------------------------------------------------------------- F5 + team volume base
def team_prior_features(team_games: list[Mapping[str, Any]],
                        targets: Iterable[tuple[str, str, int, int]],
                        *, window: int = TEAM_WINDOW) -> dict[tuple[str, str], dict[str, Any]]:
    """(game_id, team) -> strictly-prior team volume base + F5 tendencies.

    `targets` = (game_id, team, season, week). Base volume: prior dropbacks
    and gross passing yards per game. F5: pass rate over expected (mean of
    pass - xpass), neutral dropback rate, offensive plays per game, neutral
    seconds per play.
    """
    hist = _History(team_games, "team")
    out = {}
    for game_id, team, season, week in targets:
        prior = hist.prior(team, (season, week), window)
        n = len(prior)
        feats: dict[str, Any] = {"prior_games_n": n}
        if n < MIN_TEAM_GAMES:
            for name in ("base_dropbacks_pg", "base_pass_yards_pg", "base_completions_pg",
                         "f5_proe", "f5_neutral_dropback_rate", "f5_plays_pg",
                         "f5_neutral_sec_per_play"):
                feats[name] = UNKNOWN
        else:
            s = {f: sum(r[f] for r in prior) for f in (
                "dropbacks", "pass_yards", "completions", "plays", "proe_sum", "proe_n",
                "neutral_plays", "neutral_dropbacks", "neutral_sec_sum", "neutral_sec_n")}
            feats.update({
                "base_dropbacks_pg": s["dropbacks"] / n,
                "base_pass_yards_pg": s["pass_yards"] / n,
                "base_completions_pg": s["completions"] / n,
                "f5_proe": s["proe_sum"] / s["proe_n"] if s["proe_n"] else UNKNOWN,
                "f5_neutral_dropback_rate": (s["neutral_dropbacks"] / s["neutral_plays"]
                                             if s["neutral_plays"] else UNKNOWN),
                "f5_plays_pg": s["plays"] / n,
                "f5_neutral_sec_per_play": (s["neutral_sec_sum"] / s["neutral_sec_n"]
                                            if s["neutral_sec_n"] else UNKNOWN),
            })
        out[(game_id, team)] = feats
    return out


# --------------------------------------------------------------------------- F10
def _offense_expectations(team_games, pos_rows, *, window: int = TEAM_WINDOW):
    """(game_id, offense team) -> strictly-prior per-game offense production.

    Used to opponent-adjust what a defense allowed: in game g the defense
    faced offense O, whose own prior per-game output is the expectation.
    """
    by_pos: dict[tuple[str, str], dict[str, dict[str, float]]] = defaultdict(dict)
    for r in pos_rows:
        by_pos[(r["game_id"], r["team"])][r["pos"]] = r
    off_rows = []
    for tg in team_games:
        row = {"game_id": tg["game_id"], "team": tg["team"], "season": tg["season"],
               "week": tg["week"], "season_type": tg["season_type"], "pass_yards": tg["pass_yards"]}
        for pos in POS_GROUPS:
            src = by_pos.get((tg["game_id"], tg["team"]), {}).get(pos, {})
            for stat in POS_STATS:
                row[f"{pos}_{stat}"] = float(src.get(stat, 0.0))
        off_rows.append(row)
    hist = _History(off_rows, "team")
    expect = {}
    for row in off_rows:
        prior = hist.prior(row["team"], _key(row), window)
        if len(prior) < MIN_TEAM_GAMES:
            continue
        n = len(prior)
        expect[(row["game_id"], row["team"])] = {
            f: sum(p[f] for p in prior) / n for f in row
            if f not in ("game_id", "team", "season", "week", "season_type")}
    return off_rows, expect


def defense_prior_features(team_games: list[Mapping[str, Any]], pos_rows: list[Mapping[str, Any]],
                           targets: Iterable[tuple[str, str, int, int]],
                           *, window: int = DEFENSE_WINDOW) -> dict[tuple[str, str], dict[str, Any]]:
    """(game_id, defense team) -> strictly-prior allowed-by-position sums.

    For each stat in {WR,TE,RB} x {targets, receptions, yards, epa} and QB
    `pass_yards`: raw allowed per game over the defense's last `window` REG
    games, plus opponent-adjusted sums (allowed, expected) over the games
    whose offense had a strictly-prior expectation. The consumer shrinks
    allowed/expected toward 1 with DEV-fitted pseudo-games.
    """
    off_rows, expect = _offense_expectations(team_games, pos_rows)
    # defense view: in offense row (game, O) the defense is O's opponent
    opp_of = {(tg["game_id"], tg["team"]): tg["opponent"] for tg in team_games}
    def_rows = []
    for row in off_rows:
        d = opp_of.get((row["game_id"], row["team"]))
        if d is None:
            continue
        def_rows.append({**row, "defense": d, "offense": row["team"]})
    hist = _History(def_rows, "defense")
    stats = ["pass_yards"] + [f"{p}_{s}" for p in POS_GROUPS for s in POS_STATS]
    out = {}
    for game_id, defense, season, week in targets:
        prior = hist.prior(defense, (season, week), window)
        feats: dict[str, Any] = {"def_prior_games_n": len(prior)}
        adj = [(r, expect.get((r["game_id"], r["offense"]))) for r in prior]
        adj = [(r, e) for r, e in adj if e is not None]
        feats["def_adjusted_games_n"] = len(adj)
        for stat in stats:
            if len(prior) < MIN_DEF_GAMES:
                feats[f"f10_{stat}_allowed_pg"] = UNKNOWN
            else:
                feats[f"f10_{stat}_allowed_pg"] = sum(r[stat] for r in prior) / len(prior)
            if adj:
                feats[f"f10_{stat}_adj_allowed_sum"] = sum(r[stat] for r, _e in adj)
                feats[f"f10_{stat}_adj_expected_sum"] = sum(e[stat] for _r, e in adj)
            else:
                feats[f"f10_{stat}_adj_allowed_sum"] = UNKNOWN
                feats[f"f10_{stat}_adj_expected_sum"] = UNKNOWN
        out[(game_id, defense)] = feats
    return out


def shrunk_index(allowed_sum: Any, expected_sum: Any, n_games: int, pseudo_games: float) -> Any:
    """Opponent-adjusted allowed index shrunk toward 1.0 (league-neutral).

    index = (allowed + m * e_bar) / (expected + m * e_bar), e_bar = expected
    per game. UNKNOWN in -> UNKNOWN out; non-positive expectation -> UNKNOWN.
    """
    if UNKNOWN in (allowed_sum, expected_sum) or n_games <= 0:
        return UNKNOWN
    if expected_sum <= 0:
        return UNKNOWN
    e_bar = expected_sum / n_games
    return (allowed_sum + pseudo_games * e_bar) / (expected_sum + pseudo_games * e_bar)


# --------------------------------------------------------------------------- contract rows
F1_KEYS = ("f1_team_margin_closing", "f1_total_closing", "f1_implied_team_total_closing")
F5_KEYS = ("f5_proe", "f5_neutral_dropback_rate", "f5_plays_pg", "f5_neutral_sec_per_play")
F6_KEYS = ("f6_roof_type", "f6_surface")
F7_KEYS = ("f7_rest_days", "f7_rest_diff", "f7_short_week", "f7_post_bye", "f7_travel_km",
           "f7_tz_shift_east_hours", "f7_abs_tz_change", "f7_west_to_east_early",
           "f7_international", "f7_body_clock_kickoff_hour")


def contract_rows(context: Mapping[tuple[str, str], Mapping[str, Any]],
                  team_feats: Mapping[tuple[str, str], Mapping[str, Any]],
                  def_feats: Mapping[tuple[str, str], Mapping[str, Any]],
                  keys: Iterable[tuple[str, str]], *, source_ids: Mapping[str, list[str]],
                  information_cutoff: str = HISTORICAL_PRIOR_WEEKS,
                  f1_override: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
                  f6_weather: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
                  ) -> list[dict[str, Any]]:
    """Validated `contract.feature_row`s (team rows, gsis_id=None) per factor."""
    rows = []
    for game_id, team in keys:
        ctx = context[(game_id, team)]
        season, week = int(ctx["season"]), int(ctx["week"])
        base = dict(season=season, week=week, game_id=game_id, team=team, gsis_id=None)
        if f1_override is not None:
            # live path: timestamped FanDuel lines (or UNKNOWN when not captured)
            f1 = dict(f1_override.get((game_id, team)) or {
                "f1_team_margin": UNKNOWN, "f1_total": UNKNOWN, "f1_implied_team_total": UNKNOWN,
                "f1_label": "LIVE_LINE_NOT_CAPTURED"})
        else:
            f1 = {k: ctx[k] for k in F1_KEYS}
            f1["f1_label"] = F1_LABEL
        f1["uses_market_input"] = True
        rows.append(feature_row("F1_GAME_CONTEXT", **base, features=f1, source_ids=source_ids["F1"],
                                information_cutoff=information_cutoff))
        tf = team_feats.get((game_id, team), {})
        rows.append(feature_row("F5_PASS_TENDENCY_PACE", **base,
                                features={k: tf.get(k, UNKNOWN) for k in
                                          ("prior_games_n", "base_dropbacks_pg", "base_pass_yards_pg")
                                          + F5_KEYS},
                                source_ids=source_ids["F5"], information_cutoff=information_cutoff))
        f6 = {k: ctx[k] for k in F6_KEYS}
        if f6_weather is not None:
            f6.update(f6_weather.get((game_id, team)) or {"f6_weather_forecast": UNKNOWN})
        else:
            f6["f6_weather_forecast"] = UNKNOWN
        rows.append(feature_row("F6_ENVIRONMENT", **base, features=f6, source_ids=source_ids["F6"],
                                information_cutoff=information_cutoff))
        rows.append(feature_row("F7_REST_TRAVEL", **base, features={k: ctx[k] for k in F7_KEYS},
                                source_ids=source_ids["F7"], information_cutoff=information_cutoff))
        df = def_feats.get((game_id, ctx["opponent"]), {})
        f10 = {"opponent_defense": ctx["opponent"], **{k: v for k, v in df.items()}} if df else {
            "opponent_defense": ctx["opponent"], "def_prior_games_n": 0}
        f10["scope"] = "OPPONENT_ALLOWED_BY_POSITION_NOT_INDIVIDUAL_MATCHUP_NO_SLOT_SPLIT"
        rows.append(feature_row("F10_OPPONENT_POSITION", **base, features=f10,
                                source_ids=source_ids["F10"], information_cutoff=information_cutoff))
    return rows

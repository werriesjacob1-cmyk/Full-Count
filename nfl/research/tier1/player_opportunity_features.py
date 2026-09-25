"""Tier 1 workstream B: player-opportunity feature builders (F2, F3, F8, F9).

Research only. Nothing here changes authoritative B0, a live workflow, the
official inactives gate, a pick, or a selector.

Factors (see `nfl.research.tier1.contract.FACTORS`):

* F2_SNAP_SHARE_ROLE -- strictly-prior offensive snap share (last 3, last 5,
  season to date) plus a trend (last 3 minus the up-to-5 snap games that
  precede them). Built from nflverse `snap_counts` (pfr ids) joined to gsis
  ids ONLY through the nflverse `players` crosswalk, parsed by the reused
  `role_intelligence_data_prep.parse_snap_counts_csv` (which quarantines an
  unmatched pfr id as `player_id=None`, never a name join). The unmatched
  rate is reported by `snap_join_diagnostics`. Teammate-absence role-change
  events are the reused `role_intelligence_features.
  build_teammate_absence_trigger_events` (top WR by target share / top RB by
  carry share, pregame OUT/DOUBTFUL only).
* F3_TARGET_AIR_YARDS -- strictly-prior target share, air-yards share, aDOT
  (air yards per target), WOPR, catch rate, yards per target, and team
  target volume from the audited weekly stats. There is NO route data in
  this build: targets are targets, never routes, and no feature pretends
  otherwise (`ROUTES_NOTE`).
* F8_INJURY_PRACTICE -- the player's own weekly injury report row (game
  status, practice status, primary injury), with explicit NOT_LISTED,
  UNKNOWN and contradiction states. Availability rule: `INJURY_AVAILABILITY_RULE`.
* F9_ABSENCE_REDISTRIBUTION -- opportunity vacated by teammates listed OUT on
  the pregame report, redistributed to the remaining recent pass catchers /
  ball carriers in proportion to their prior shares under a team budget,
  with a same-position-first replacement hierarchy, a per-player cap, and
  mass-balance accounting. Trigger rule: `ABSENCE_TRIGGER_RULE`.

Every historical feature for (season, week) reads only rows whose
(season, week) sorts strictly before it; each builder calls
`contract.assert_strictly_prior` on the history it actually used. The one
deliberate exception is the injury report for the target week itself, which
is a PREGAME source (filed before kickoff) and is labelled as such.
"""
from __future__ import annotations

import bisect
import csv
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from nfl.research import role_intelligence_data_prep as prep
from nfl.research import role_intelligence_features as rif
from nfl.research.tier1 import contract

UNKNOWN = contract.UNKNOWN

# nflverse weekly stats use the franchise's current abbreviation for every
# season; snap_counts and injuries keep the abbreviation used at the time.
TEAM_ALIASES = {"SD": "LAC", "OAK": "LV", "STL": "LA"}

ROUTES_NOTE = ("No route-participation source is ingested in this build; targets are "
               "targets, not routes. route share is UNKNOWN, never inferred from targets.")

INJURY_AVAILABILITY_RULE = (
    "Each nflverse injuries row is treated as that team-week's FINAL pregame report "
    "(Friday for Sunday games; the last report before kickoff for Thursday/Saturday/"
    "Monday games). Historical rows carry no exact filing timestamp (2016-2024 carry "
    "only a `date_modified`, 2025-2026 carry none), so the row is used only for the "
    "game of its own (season, week) and is never assumed available earlier than the "
    "last report day. A blank game status means 'no game designation' ONLY on a final "
    "report; for a live week whose team has not yet filed its final report the status "
    "is UNKNOWN_FINAL_REPORT_NOT_YET_FILED. Practice DNP is not a game-day inactive: "
    "the official inactives gate in the live workflow is separate, fail-closed and "
    "untouched by this module.")

ABSENCE_TRIGGER_RULE = (
    "A teammate is 'absent' for (season, week) only if the pregame injury report lists "
    "him OUT for that week. This misses: players on IR/PUP/NFI (removed from the "
    "report), suspensions, healthy scratches, trades/releases, Questionable/Doubtful "
    "players declared inactive on game day, and in-game injuries. 'Did not appear in "
    "the box score' is postgame information and is never used as a trigger.")

REPORT_STATUSES = {"OUT": "OUT", "DOUBTFUL": "DOUBTFUL", "QUESTIONABLE": "QUESTIONABLE"}
PRACTICE_STATUSES = {
    "did not participate in practice": "DNP",
    "limited participation in practice": "LIMITED",
    "full participation in practice": "FULL",
}
NOT_LISTED = "NOT_LISTED"
NONE_DESIGNATED = "NONE_DESIGNATED"
UNKNOWN_TEAM_WEEK = "UNKNOWN_TEAM_WEEK_NOT_IN_SOURCE"
UNKNOWN_NOT_FILED = "UNKNOWN_FINAL_REPORT_NOT_YET_FILED"
UNKNOWN_PRACTICE = "UNKNOWN_PRACTICE"

PASS_CATCHER_POSITIONS = frozenset({"WR", "TE", "RB", "FB"})
ROLE_TREND_THRESHOLD = rif.LARGE_USAGE_CHANGE_THRESHOLD  # 0.15, reused, pre-declared
RECIPIENT_LOOKBACK_TEAM_GAMES = 3
REDISTRIBUTION_PER_PLAYER_CAP = 0.5  # no single player receives > 50% of a vacated share


def norm_team(team: str) -> str:
    team = (team or "").strip().upper()
    return TEAM_ALIASES.get(team, team)


def pos_group(position: str) -> str:
    position = (position or "").upper()
    return "RB" if position in ("RB", "FB", "HB") else position


# ---------------------------------------------------------------------------
# Loading (local, hash-recorded files only; no network)
# ---------------------------------------------------------------------------

def load_crosswalk(players_csv: Path) -> dict[str, str]:
    return prep.parse_players_crosswalk_csv(players_csv.read_text(encoding="utf-8"))


def load_snap_rows(snap_dir: Path, seasons: Iterable[int], crosswalk: Mapping[str, str]
                   ) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for season in seasons:
        path = snap_dir / f"snap_counts_{season}.csv"
        if not path.exists():
            continue
        for row in prep.parse_snap_counts_csv(path.read_text(encoding="utf-8"), season,
                                              dict(crosswalk)):
            row["team"] = norm_team(row["team"])
            row["opponent"] = norm_team(row["opponent"])
            rows.append(row)
    return rows


def snap_join_diagnostics(snap_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Unmatched pfr->gsis rate (rows with offense snaps > 0), overall and skill positions."""
    out: dict[str, Any] = {}
    for label, keep in (("all_offense", lambda r: True),
                        ("skill_WR_TE_RB_FB", lambda r: r["position"] in ("WR", "TE", "RB", "FB"))):
        pool = [r for r in snap_rows if r["offense_snaps"] > 0 and keep(r)]
        unmatched = [r for r in pool if r["player_id"] is None]
        out[label] = {"rows": len(pool), "unmatched": len(unmatched),
                      "unmatched_rate": (len(unmatched) / len(pool)) if pool else UNKNOWN,
                      "unmatched_examples": sorted({r["pfr_player_id"] for r in unmatched})[:10]}
    out["join_rule"] = "nflverse players.csv pfr_id -> gsis_id only; no name join"
    return out


def _practice(value: str) -> str:
    return PRACTICE_STATUSES.get((value or "").strip().lower(), UNKNOWN_PRACTICE)


def load_injury_rows(injury_dir: Path, seasons: Iterable[int]) -> list[dict[str, Any]]:
    """REG-season injury rows with normalized statuses; blank gsis ids dropped (counted)."""
    rows: list[dict[str, Any]] = []
    for season in seasons:
        path = injury_dir / f"injuries_{season}.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            for raw in csv.DictReader(handle):
                if (raw.get("game_type") or "").strip().upper() != "REG":
                    continue
                gsis = (raw.get("gsis_id") or "").strip()
                if not gsis:
                    continue
                status_raw = (raw.get("report_status") or "").strip().upper()
                rows.append({
                    "season": int(raw["season"]), "week": int(raw["week"]),
                    "team": norm_team(raw.get("team") or ""), "player_id": gsis,
                    "position": (raw.get("position") or "").strip().upper(),
                    "report_status": REPORT_STATUSES.get(status_raw, NONE_DESIGNATED
                                                         if not status_raw else "OTHER_" + status_raw),
                    "practice_status": _practice(raw.get("practice_status") or ""),
                    "primary_injury": ((raw.get("report_primary_injury") or "").strip()
                                       or (raw.get("practice_primary_injury") or "").strip()
                                       or UNKNOWN),
                    "date_modified": (raw.get("date_modified") or "").strip() or UNKNOWN,
                })
    return rows


# ---------------------------------------------------------------------------
# Generic strictly-prior history helper
# ---------------------------------------------------------------------------

class History:
    """Per-key time-ordered records with a strictly-prior slice accessor."""

    def __init__(self) -> None:
        self._keys: dict[Any, list[tuple[int, int]]] = defaultdict(list)
        self._vals: dict[Any, list[Any]] = defaultdict(list)
        self._sorted = True

    def add(self, key: Any, season: int, week: int, value: Any) -> None:
        self._keys[key].append((season, week))
        self._vals[key].append(value)
        self._sorted = False

    def finalize(self) -> "History":
        for key in list(self._keys):
            order = sorted(range(len(self._keys[key])), key=lambda i: self._keys[key][i])
            self._keys[key] = [self._keys[key][i] for i in order]
            self._vals[key] = [self._vals[key][i] for i in order]
        self._sorted = True
        return self

    def prior(self, key: Any, season: int, week: int, n: int | None = None) -> list[Any]:
        if not self._sorted:
            raise RuntimeError("History.finalize() not called")
        keys = self._keys.get(key)
        if not keys:
            return []
        idx = bisect.bisect_left(keys, (season, week))
        start = 0 if n is None else max(0, idx - n)
        contract.assert_strictly_prior(keys[start:idx], (season, week))
        return self._vals[key][start:idx]

    def season_prior(self, key: Any, season: int, week: int) -> list[Any]:
        """This season's records strictly before `week` (season to date)."""
        keys = self._keys.get(key)
        if not keys:
            return []
        lo, hi = bisect.bisect_left(keys, (season, -1)), bisect.bisect_left(keys, (season, week))
        contract.assert_strictly_prior(keys[lo:hi], (season, week))
        return self._vals[key][lo:hi]


def _mean(values: list[float]) -> float | str:
    return statistics.fmean(values) if values else UNKNOWN


# ---------------------------------------------------------------------------
# F2 snap share
# ---------------------------------------------------------------------------

def build_snap_history(snap_rows: list[dict[str, Any]]) -> History:
    """player_id -> [(season, week) -> {'team', 'share'}] from REG snap rows (gsis-joined)."""
    team_max: dict[tuple[int, int, str], float] = {}
    per_player: dict[tuple[int, int, str, str], float] = defaultdict(float)
    for row in snap_rows:
        tk = (row["season"], row["week"], row["team"])
        team_max[tk] = max(team_max.get(tk, 0.0), row["offense_snaps"])
        if row["player_id"] is not None:
            per_player[(row["season"], row["week"], row["team"], row["player_id"])] += row["offense_snaps"]
    hist = History()
    for (season, week, team, pid), snaps in per_player.items():
        denom = team_max.get((season, week, team), 0.0)
        if denom > 0:
            hist.add(pid, season, week, {"team": team, "share": snaps / denom})
    return hist.finalize()


def snap_features(hist: History, player_id: str, season: int, week: int) -> dict[str, Any]:
    prior = hist.prior(player_id, season, week, n=8)
    shares = [p["share"] for p in prior]
    last3, last5 = shares[-3:], shares[-5:]
    base = shares[:-3][-5:] if len(shares) > 3 else []
    std_vals = [v["share"] for v in hist.season_prior(player_id, season, week)]
    l3 = _mean(last3) if len(last3) == 3 else UNKNOWN
    trend = (statistics.fmean(last3) - statistics.fmean(base)) if (len(last3) == 3 and base) else UNKNOWN
    return {
        "snap_share_last3": l3,
        "snap_share_last5": _mean(last5) if len(last5) >= 3 else UNKNOWN,
        "snap_share_season_to_date": _mean(std_vals),
        "snap_share_trend_last3_minus_prior": trend,
        "snap_games_prior_n": len(shares),
    }


# ---------------------------------------------------------------------------
# F3 target / air yards (weekly stats)
# ---------------------------------------------------------------------------

def team_game_totals(player_weeks: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, float]]:
    totals: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"targets": 0.0, "carries": 0.0})
    for row in player_weeks:
        acc = totals[(row["game_id"], row["team"])]
        acc["targets"] += row["targets"]
        acc["carries"] += row["carries"]
    return dict(totals)


def build_usage_histories(player_weeks: list[dict[str, Any]]) -> tuple[History, History]:
    """Player and team histories from the audited weekly rows (REG + POST, like B0)."""
    totals = team_game_totals(player_weeks)
    player_hist, team_hist = History(), History()
    seen_team_games: set[tuple[str, str]] = set()
    for row in player_weeks:
        tt = totals[(row["game_id"], row["team"])]
        player_hist.add(row["player_id"], row["season"], row["week"], {
            "game_id": row["game_id"], "team": row["team"], "position": row["position"],
            "targets": row["targets"], "receptions": row["receptions"],
            "receiving_yards": row["receiving_yards"], "air_yards": row["receiving_air_yards"],
            "carries": row["carries"], "rushing_yards": row["rushing_yards"],
            "target_share_col": row["target_share"], "air_yards_share_col": row["air_yards_share"],
            "wopr_col": row["wopr"], "team_targets": tt["targets"], "team_carries": tt["carries"]})
        key = (row["game_id"], row["team"])
        if key not in seen_team_games:
            seen_team_games.add(key)
            team_hist.add(row["team"], row["season"], row["week"],
                          {"game_id": row["game_id"], "targets": tt["targets"], "carries": tt["carries"]})
    return player_hist.finalize(), team_hist.finalize()


def target_features(player_hist: History, team_hist: History, player_id: str, team: str,
                    season: int, week: int, window: int = 5) -> dict[str, Any]:
    prior = player_hist.prior(player_id, season, week, n=window)
    team_prior = team_hist.prior(team, season, week, n=window)
    tgt = sum(p["targets"] for p in prior)
    team_tgt = sum(p["team_targets"] for p in prior)
    car = sum(p["carries"] for p in prior)
    team_car = sum(p["team_carries"] for p in prior)
    rec = sum(p["receptions"] for p in prior)
    yds = sum(p["receiving_yards"] for p in prior)
    air = sum(p["air_yards"] for p in prior)
    ryd = sum(p["rushing_yards"] for p in prior)
    std = player_hist.season_prior(player_id, season, week)
    std_tgt, std_team = sum(p["targets"] for p in std), sum(p["team_targets"] for p in std)
    return {
        "games_prior_n": len(prior),
        "targets_last5": tgt, "receptions_last5": rec, "receiving_yards_last5": yds,
        "air_yards_last5": air, "carries_last5": car, "rushing_yards_last5": ryd,
        "target_share_last5": tgt / team_tgt if team_tgt > 0 else UNKNOWN,
        "target_share_season_to_date": std_tgt / std_team if std_team > 0 else UNKNOWN,
        "carry_share_last5": car / team_car if team_car > 0 else UNKNOWN,
        "air_yards_share_last5": _mean([p["air_yards_share_col"] for p in prior]),
        "wopr_last5": _mean([p["wopr_col"] for p in prior]),
        "adot_last5": air / tgt if tgt > 0 else UNKNOWN,
        "yards_per_target_last5": yds / tgt if tgt > 0 else UNKNOWN,
        "catch_rate_last5": rec / tgt if tgt > 0 else UNKNOWN,
        "yards_per_carry_last5": ryd / car if car > 0 else UNKNOWN,
        "team_targets_per_game_last5": _mean([t["targets"] for t in team_prior]),
        "team_carries_per_game_last5": _mean([t["carries"] for t in team_prior]),
        "route_share": UNKNOWN,
    }


# ---------------------------------------------------------------------------
# F8 injury / practice
# ---------------------------------------------------------------------------

class InjuryIndex:
    def __init__(self, injury_rows: list[dict[str, Any]], *, final_report_team_weeks:
                 set[tuple[int, int, str]] | None = None) -> None:
        """`final_report_team_weeks`: for a LIVE week, the team-weeks whose final report is
        known to be filed. None means every covered team-week is a final report (history)."""
        self.by_key: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
        self.team_weeks: set[tuple[int, int, str]] = set()
        self.by_player_season: dict[tuple[str, int], list[tuple[int, str]]] = defaultdict(list)
        self.status_by_team_week: dict[tuple[int, int, str], dict[str, str]] = defaultdict(dict)
        for row in injury_rows:
            self.status_by_team_week[(row["season"], row["week"], row["team"])][row["player_id"]] = \
                row["report_status"]
            self.by_key[(row["season"], row["week"], row["player_id"])].append(row)
            self.team_weeks.add((row["season"], row["week"], row["team"]))
            self.by_player_season[(row["player_id"], row["season"])].append(
                (row["week"], row["report_status"]))
        for v in self.by_player_season.values():
            v.sort()
        self.final_report_team_weeks = final_report_team_weeks

    def is_final(self, season: int, week: int, team: str) -> bool:
        return self.final_report_team_weeks is None or (season, week, team) in self.final_report_team_weeks

    def team_listed(self, season: int, week: int, team: str, statuses: Iterable[str]) -> list[str]:
        """Players the team's report lists with one of `statuses` (pregame)."""
        wanted = set(statuses)
        return sorted(pid for pid, st in self.status_by_team_week.get((season, week, team), {}).items()
                      if st in wanted)


def injury_features(index: InjuryIndex, player_id: str, team: str, season: int, week: int
                    ) -> dict[str, Any]:
    rows = [r for r in index.by_key.get((season, week, player_id), []) if r["team"] == team]
    covered = (season, week, team) in index.team_weeks
    final = index.is_final(season, week, team)
    contradictions: list[str] = []
    if not covered:
        status, practice, injury = UNKNOWN_TEAM_WEEK, UNKNOWN_TEAM_WEEK, UNKNOWN
    elif not rows:
        status, practice, injury = (NOT_LISTED if final else UNKNOWN_NOT_FILED), NOT_LISTED, UNKNOWN
    else:
        statuses = {r["report_status"] for r in rows}
        practices = {r["practice_status"] for r in rows}
        if len(statuses) > 1 or len(practices) > 1:
            contradictions.append("DUPLICATE_ROWS_CONFLICT")
        status = sorted(statuses)[0] if len(statuses) == 1 else "CONFLICT"
        practice = sorted(practices)[0] if len(practices) == 1 else "CONFLICT"
        injury = rows[0]["primary_injury"]
        if status == NONE_DESIGNATED and not final:
            status = UNKNOWN_NOT_FILED
        if status == "OUT" and practice == "FULL":
            contradictions.append("OUT_WITH_FULL_PRACTICE")
    prev = [(w, s) for (w, s) in index.by_player_season.get((player_id, season), []) if week - 2 <= w < week]
    returning = any(s == "OUT" for _w, s in prev)
    return {"report_status": status, "practice_status": practice, "primary_injury": injury,
            "listed_out_in_prior_two_weeks": returning,
            "contradiction": ";".join(contradictions) if contradictions else "NONE",
            "availability_rule": "FINAL_PREGAME_REPORT_OF_SAME_WEEK"}


def injury_category(features: Mapping[str, Any]) -> str:
    status, practice = features["report_status"], features["practice_status"]
    if status in (UNKNOWN_TEAM_WEEK, UNKNOWN_NOT_FILED, "CONFLICT") or practice == "CONFLICT":
        return UNKNOWN
    if status == NOT_LISTED:
        return "RETURNING_FROM_OUT" if features["listed_out_in_prior_two_weeks"] else NOT_LISTED
    return f"{status}|{practice}"


# ---------------------------------------------------------------------------
# F9 absence redistribution
# ---------------------------------------------------------------------------

def redistribute(absent: list[dict[str, Any]], recipients: list[dict[str, Any]], *,
                 retention: float, same_position_weight: float,
                 cap: float = REDISTRIBUTION_PER_PLAYER_CAP) -> dict[str, Any]:
    """Share the vacated opportunity among recipients in proportion to prior shares.

    absent:     [{'player_id', 'group', 'vacated_share'}]  vacated_share = prior share
    recipients: [{'player_id', 'group', 'share', 'overlap': {absent_id: fraction}}]
        overlap = fraction of the recipient's own B0-window games in which the absent
        player also played (so an absence already inside the B0 window is not re-added).
    Returns per-recipient gains and the mass balance. Never gives any one player more
    than `cap` of a single absent player's vacated share; the excess is unallocated.
    """
    gains = {r["player_id"]: 0.0 for r in recipients}
    budget = allocated = 0.0
    for a in absent:
        v = a["vacated_share"]
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        budget += retention * v
        eligible = [r for r in recipients if isinstance(r["share"], (int, float)) and r["share"] > 0]
        same = [r for r in eligible if r["group"] == a["group"]]
        other = [r for r in eligible if r["group"] != a["group"]]
        pools = ([(same, same_position_weight), (other, 1.0 - same_position_weight)]
                 if same and other else [(same or other, 1.0)])
        for pool, weight in pools:
            total = sum(r["share"] for r in pool)
            if total <= 0:
                continue
            for r in pool:
                gain = retention * v * weight * (r["share"] / total) * r["overlap"].get(a["player_id"], 0.0)
                gain = min(gain, cap * v)
                gains[r["player_id"]] += gain
                allocated += gain
    return {"gains": gains, "retained_budget": budget, "allocated": allocated,
            "unallocated_residual": max(0.0, budget - allocated),
            "over_allocation": max(0.0, allocated - budget)}


__all__ = [
    "ABSENCE_TRIGGER_RULE", "INJURY_AVAILABILITY_RULE", "ROUTES_NOTE", "History", "InjuryIndex",
    "build_snap_history", "build_usage_histories", "injury_category", "injury_features",
    "load_crosswalk", "load_injury_rows", "load_snap_rows", "norm_team", "pos_group",
    "redistribute", "snap_features", "snap_join_diagnostics", "target_features",
]

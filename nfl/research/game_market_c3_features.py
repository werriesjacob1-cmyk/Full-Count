"""Join QB-continuity/starter-availability features onto C2's margin rows.

C2 (`game_market_c2_features.py`/`game_market_c2_model.py`) was rejected on
margin specifically: held margin MAE improved over B0 but its bootstrap
interval crossed zero, and the improvement was driven largely by one season
(2022). The project owner's own named hypothesis is that C2's margin
weakness is partly due to missing starter/regime/availability information.
C3 tests that hypothesis directly by adding three strictly-prior features to
C2's existing 8 margin features:

- `qb_diff_tenure_starts` = home minus away `features.qb_tenure_starts`
  (`qb_continuity_features.build_prior_qb_continuity_features`) -- how many
  more consecutive prior starts the home incumbent QB has made than the
  away incumbent.
- `qb_diff_games_since_change` = home minus away
  `features.games_since_qb_change` from the same module. As of this
  module's current implementation `qb_tenure_starts` and
  `games_since_qb_change` are computed as the literal same integer for every
  row (see `qb_continuity_features.build_prior_qb_continuity_features`,
  which sets both from the same `tenure` local variable) -- this feature is
  therefore an exact duplicate of `qb_diff_tenure_starts` on every row in
  this dataset. That redundancy is disclosed here rather than silently
  dropping either name, because the task names both fields explicitly and a
  ridge fit tolerates exact collinearity gracefully (the L2 penalty simply
  splits weight between the two identical columns; see
  `QB_TENURE_GAMES_SINCE_CHANGE_DUPLICATE_NOTE`).
- `availability_diff_starter_out` = (away `starter_out_feature` as 0/1)
  minus (home `starter_out_feature` as 0/1), from
  `injury_availability_features.build_prior_starter_availability_features`.
  Positive means the AWAY incumbent was listed Out/Doubtful and the home
  incumbent was not -- an away disadvantage, i.e. the same "positive means
  home advantage" sign convention `game_market_c2_features.MARGIN_FEATURES`
  already uses for every other diff feature.

This module is a *join/assembly* layer only, exactly like
`game_market_c2_features.py` and `game_matchup_features.py`: it never
recomputes QB continuity or injury history itself, never touches a market
line/price/current-game outcome, and reads ONLY the `features` block of
`qb_continuity_features.py`'s rows -- never `target`, which holds that same
game's own realized starter identity (current-game information; see that
module's own leakage-safety proof). `injury_availability_features.py`'s
rows have no `target` block of their own (they are already strictly prior),
so the only leakage surface in this join is `qb_continuity_features.py`'s
`target`, and this module never reads it.

A team-game whose QB-continuity row has no prior starter identity yet
(`features.qb_tenure_starts is None`, i.e. that team's first observed game
in the source), or whose availability row is `SEASON_NOT_COVERED_BY_SOURCE`
/ `UNKNOWN_NO_PRIOR_STARTER_IDENTITY` (`starter_out_feature is None`), is
excluded from C3 and reported by `game_id` -- never imputed to a favorable
zero/false. This mirrors `game_market_c2_features.py`'s own discipline for
its `INSUFFICIENT_HISTORY`/`data_gap_reason` population exactly.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

QB_TENURE_GAMES_SINCE_CHANGE_DUPLICATE_NOTE = (
    "qb_continuity_features.build_prior_qb_continuity_features sets both "
    "features.qb_tenure_starts and features.games_since_qb_change from the "
    "same 'tenure' local variable, so qb_diff_tenure_starts and "
    "qb_diff_games_since_change are numerically identical on every row of "
    "this dataset. Both are retained because the task names both fields "
    "explicitly; the ridge fit tolerates the exact collinearity (L2 "
    "regularization, not OLS) by splitting weight between the two columns "
    "rather than failing to converge."
)

NEW_MARGIN_FEATURES = (
    "qb_diff_tenure_starts",
    "qb_diff_games_since_change",
    "availability_diff_starter_out",
)

QB_PLAYER_STAT_MISSING_TEAM_IDENTITY_BUG_NOTE = (
    "One real 1999-2025 nflverse stats_player_week row has a genuine source "
    "data-quality gap: player_id 00-0001471 (Steve Bono), 1999 week 9, "
    "game_id 1999_09_PHI_CAR, carries real production (1 pass attempt, 2 "
    "carries, -2 rushing yards, non-zero passing_epa) but blank team/"
    "opponent_team fields in nflverse's own release -- independently "
    "verified against the live source on 2026-09-18. "
    "qb_continuity_features.infer_team_week_starters fails closed on a "
    "blank team (by design, via its own `_text` validator), so this row is "
    "excluded here before that call rather than fabricating a team "
    "identity from the game_id side nflverse itself left ambiguous. This "
    "mirrors game_market_c2_features.filter_team_offense_rows_for_negative_"
    "value_bug's own exclude-and-disclose pattern for a different, "
    "similarly out-of-scope source-quality gap."
)


INJURY_REPORT_DUPLICATE_STATUS_UPDATE_NOTE = (
    "nflverse's own injuries release contains a small number of genuine "
    "same-week duplicate (season, week, team, gsis_id) rows where a "
    "player's filed status was updated within that week's reporting window "
    "(e.g. 2024 week 15, HOU TE 00-0039359: Questionable at "
    "2024-12-15T03:34:33Z, then Out at 2024-12-15T14:17:06Z) -- "
    "independently verified against the live source on 2026-09-18 (2 "
    "duplicate keys across the full 2009-2025 REG population of 87,208 "
    "rows). injury_availability_features._validate_and_index_injury_rows "
    "fails closed on any duplicate key (by design, one row per player/"
    "week), so this module resolves the duplicate BEFORE that call by "
    "keeping only the row with the lexicographically greatest ISO-8601 "
    "`date_modified` -- i.e. the last status actually filed before that "
    "week's games, which is the correct pregame-safe choice, not an "
    "arbitrary tie-break. This mirrors game_market_c2_features's own "
    "exclude-and-disclose pattern for a different out-of-scope source "
    "issue; no row's `report_status` value is changed, only which of two "
    "real filed rows for the same player/week is kept."
)


INJURY_REPORT_PRE_2016_PROBABLE_VOCABULARY_NOTE = (
    "nflverse's real injuries release uses the NFL's own historical "
    "'Probable' practice-report designation in seasons 2009-2015 (16,412 "
    "of 87,208 REG rows, independently verified against the live source on "
    "2026-09-18, concentrated exactly in 2009-2015 and absent from 2016 "
    "onward) -- the NFL discontinued the 'Probable' tier league-wide "
    "starting with the 2016 season, which is exactly why "
    "injury_availability_features.py's known vocabulary "
    "(GAME_AFFECTING_STATUSES = {OUT, DOUBTFUL}, "
    "NOT_GAME_AFFECTING_STATUSES = {QUESTIONABLE, ''}) never lists it: that "
    "module was written against the current, post-2016 vocabulary. "
    "'Probable' historically meant a player was expected to play (a milder "
    "designation than even 'Questionable'), so it is excluded here before "
    "indexing rather than raising -- the same team/week then falls through "
    "to `NOT_LISTED_GAME_AFFECTING_STATUS` (`starter_out_feature = False`), "
    "which is the semantically correct outcome for a non-game-affecting "
    "historical status, not a favorable zero invented for this task. A "
    "further 6 rows (all 2024) carry the non-standard value 'Note', an "
    "apparent source artifact rather than a real status; those are excluded "
    "the same way and reported separately."
)


QB_PLAYER_STAT_FRANCHISE_RELOCATION_NORMALIZATION_NOTE = (
    "nflverse's stats_player_week source normalizes team/opponent_team to a "
    "franchise's CURRENT abbreviation even in historical rows -- e.g. a "
    "real 2009 San Diego @ Oakland game (game_id 2009_01_SD_OAK) shows "
    "team=LAC/opponent=LV for both quarterbacks, not the historical SD/OAK "
    "codes the schedule (and game_id itself) uses -- independently "
    "verified against the live source on 2026-09-18. This is exactly the "
    "same class of issue game_market_c2_data_prep.py already found and "
    "fixed for PBP posteam/defteam (1999 St. Louis Rams appearing as 'LA'), "
    "just in a different nflverse release. Left unfixed, every historical "
    "Oakland/San Diego/St. Louis Rams team-week silently disappears from "
    "the QB-continuity join (425 of 2009-2025's team-weeks, verified by "
    "direct comparison against the schedule before this fix existed), "
    "which looks identical to a real data gap rather than an identity bug. "
    "`normalize_qb_player_stat_team_identity` fixes this by re-deriving the "
    "true historical team from `game_id` plus the already-trusted schedule "
    "(`game_market_b0_research`'s pinned `games.csv`) instead of trusting "
    "this source's own team/opponent_team fields, mirroring C2's own "
    "re-derive-from-game_id fix exactly."
)

FRANCHISE_RELOCATION_EQUIVALENTS = {
    "LV": {"LV", "OAK"},
    "LAC": {"LAC", "SD"},
    "LA": {"LA", "STL"},
}


def _historical_equivalents(code: str) -> set[str]:
    return FRANCHISE_RELOCATION_EQUIVALENTS.get(code, {code})


class GameMarketC3FeatureError(ValueError):
    """Raised when the C3 join finds an identity or leakage problem."""


def normalize_qb_player_stat_team_identity(
    qb_player_stat_rows: Iterable[Mapping[str, Any]],
    schedule_rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Re-derive `team`/`opponent_team` from `game_id` plus the trusted schedule.

    See `QB_PLAYER_STAT_FRANCHISE_RELOCATION_NORMALIZATION_NOTE`. Returns
    `(normalized_rows, unresolved_rows)`. A row whose `game_id` is not found
    in `schedule_rows`, or whose current-normalized `team` cannot be matched
    to exactly one side of that game via `FRANCHISE_RELOCATION_EQUIVALENTS`,
    is returned UNCHANGED in `normalized_rows` and also listed in
    `unresolved_rows` for disclosure -- never guessed.
    """
    schedule_index = {
        row["game_id"]: (str(row["home_team"]).upper(), str(row["away_team"]).upper())
        for row in schedule_rows
    }

    normalized: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for source in qb_player_stat_rows:
        row = dict(source)
        game_id = str(row.get("game_id") or "").strip()
        pair = schedule_index.get(game_id)
        if pair is None:
            normalized.append(row)
            continue
        home_team, away_team = pair
        current_team = str(row.get("team") or "").strip().upper()
        current_opponent = str(row.get("opponent_team") or "").strip().upper()
        if current_team in (home_team, away_team) and current_opponent in (home_team, away_team):
            normalized.append(row)  # already matches the historical codes
            continue
        candidates = [code for code in (home_team, away_team) if code in _historical_equivalents(current_team)]
        if len(candidates) != 1:
            normalized.append(row)
            unresolved.append(row)
            continue
        true_team = candidates[0]
        true_opponent = away_team if true_team == home_team else home_team
        row["team"] = true_team
        row["opponent_team"] = true_opponent
        normalized.append(row)
    return normalized, unresolved


def filter_injury_rows_to_known_status_vocabulary(
    injury_rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split injury rows into (kept, excluded-for-vocabulary-gap).

    See `INJURY_REPORT_PRE_2016_PROBABLE_VOCABULARY_NOTE`.
    `injury_availability_features._validate_and_index_injury_rows` fails
    closed (raises) on any `report_status` value outside its known
    vocabulary, by design. This filters out rows using a status value that
    module does not recognize BEFORE that call, so a real, disclosed,
    dated vocabulary difference does not stop the whole pipeline -- and so
    those player-weeks correctly fall through to "not listed" rather than
    being silently coerced to a value the source row never actually used.
    """
    known = {"", "QUESTIONABLE", "DOUBTFUL", "OUT"}
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for source in injury_rows:
        row = dict(source)
        normalized = str(row.get("report_status") or "").strip().upper()
        if normalized in known:
            kept.append(row)
        else:
            excluded.append(row)
    return kept, excluded


def filter_injury_rows_keep_latest_status_update(
    injury_rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve same-week duplicate injury rows to the latest filed status.

    See `INJURY_REPORT_DUPLICATE_STATUS_UPDATE_NOTE`. Returns
    `(kept, dropped)` where `dropped` holds the earlier-`date_modified` row
    for every (season, week, team, gsis_id) key that had more than one row.
    Every input row must carry `date_modified` (an ISO-8601 string); this is
    beyond `injury_availability_features.REQUIRED_COLUMNS`; a row without a
    usable `date_modified` is treated as sorting before every dated row
    (never silently preferred) rather than raising, since the vast majority
    of rows are not duplicates at all and do not need this field.
    """
    by_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for source in injury_rows:
        row = dict(source)
        key = (
            str(row.get("season")), str(row.get("week")),
            str(row.get("team")), str(row.get("gsis_id")),
        )
        by_key.setdefault(key, []).append(row)

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for key, rows in by_key.items():
        if len(rows) == 1:
            kept.append(rows[0])
            continue
        ranked = sorted(rows, key=lambda r: str(r.get("date_modified") or ""))
        kept.append(ranked[-1])
        dropped.extend(ranked[:-1])
    return kept, dropped


def filter_qb_player_stat_rows_for_missing_team_identity(
    qb_player_stat_rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split QB player-stat rows into (kept, excluded-for-known-bug).

    See `QB_PLAYER_STAT_MISSING_TEAM_IDENTITY_BUG_NOTE`. Excluded rows are
    real historical observations, not fabricated ones; excluding them keeps
    `qb_continuity_features.infer_team_week_starters`'s existing fail-closed
    validation intact instead of loosening it.
    """
    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for source in qb_player_stat_rows:
        row = dict(source)
        if str(row.get("team") or "").strip() and str(row.get("opponent_team") or "").strip():
            kept.append(row)
        else:
            excluded.append(row)
    return kept, excluded


def _index_qb_continuity_by_team_week(
    qb_continuity_rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[int, int, str], Mapping[str, Any]]:
    index: dict[tuple[int, int, str], Mapping[str, Any]] = {}
    for row in qb_continuity_rows:
        key = (int(row["season"]), int(row["week"]), str(row["team"]).upper())
        if key in index:
            raise GameMarketC3FeatureError(f"duplicate QB-continuity row: {key}")
        index[key] = row
    return index


def _index_availability_by_team_week(
    availability_rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[int, int, str], Mapping[str, Any]]:
    index: dict[tuple[int, int, str], Mapping[str, Any]] = {}
    for row in availability_rows:
        key = (int(row["season"]), int(row["week"]), str(row["team"]).upper())
        if key in index:
            raise GameMarketC3FeatureError(f"duplicate availability row: {key}")
        index[key] = row
    return index


def _team_side_gap_reason(
    qb_row: Mapping[str, Any] | None,
    avail_row: Mapping[str, Any] | None,
) -> str | None:
    if qb_row is None:
        return "QB_CONTINUITY_ROW_MISSING_FOR_TEAM_WEEK"
    if avail_row is None:
        return "AVAILABILITY_ROW_MISSING_FOR_TEAM_WEEK"
    # Read ONLY the strictly-prior `features` block -- never `target`, which
    # holds that same game's own realized starter identity.
    if qb_row["features"]["qb_tenure_starts"] is None:
        return "UNKNOWN_NO_PRIOR_STARTER_IDENTITY"
    if avail_row["starter_out_feature"] is None:
        # availability_status is one of SEASON_NOT_COVERED_BY_SOURCE or
        # UNKNOWN_NO_PRIOR_STARTER_IDENTITY whenever the flag is None; carry
        # the exact source label forward rather than inventing a new one.
        return str(avail_row["availability_status"])
    return None


def build_c3_game_rows(
    c2_rows: Iterable[Mapping[str, Any]],
    qb_continuity_rows: Iterable[Mapping[str, Any]],
    availability_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return C2's rows augmented with C3's 3 additional strictly-prior margin features.

    Every field `game_market_c2_features.build_c2_game_rows` already
    produces is carried forward unchanged (including `margin_features`,
    which keeps C2's original 8-feature dict so a C2-only control model can
    still be fit/applied on this same row set). This function adds
    `c3_eligibility` (`"ELIGIBLE"`, `"INELIGIBLE_BASE_C2"`, or
    `"INSUFFICIENT_AVAILABILITY_HISTORY"`), `c3_data_gap_reason`, and, only
    for `ELIGIBLE` rows, `c3_margin_features` (C2's 8 features plus the 3
    new ones from `NEW_MARGIN_FEATURES`).
    """
    qb_index = _index_qb_continuity_by_team_week(qb_continuity_rows)
    avail_index = _index_availability_by_team_week(availability_rows)

    output: list[dict[str, Any]] = []
    for source in c2_rows:
        row = dict(source)
        if row.get("eligibility") != "ELIGIBLE":
            output.append({
                **row,
                "c3_eligibility": "INELIGIBLE_BASE_C2",
                "c3_data_gap_reason": row.get("data_gap_reason") or "C2_BASE_INSUFFICIENT_HISTORY",
                "c3_margin_features": None,
            })
            continue

        season = int(row["season"])
        week = int(row["week"])
        home_team = str(row["home_team"]).upper()
        away_team = str(row["away_team"]).upper()

        home_qb = qb_index.get((season, week, home_team))
        away_qb = qb_index.get((season, week, away_team))
        home_avail = avail_index.get((season, week, home_team))
        away_avail = avail_index.get((season, week, away_team))

        home_gap = _team_side_gap_reason(home_qb, home_avail)
        away_gap = _team_side_gap_reason(away_qb, away_avail)
        gap_reason = home_gap or away_gap

        if gap_reason is not None:
            output.append({
                **row,
                "c3_eligibility": "INSUFFICIENT_AVAILABILITY_HISTORY",
                "c3_data_gap_reason": gap_reason,
                "c3_margin_features": None,
            })
            continue

        home_tenure = home_qb["features"]["qb_tenure_starts"]
        away_tenure = away_qb["features"]["qb_tenure_starts"]
        home_gsc = home_qb["features"]["games_since_qb_change"]
        away_gsc = away_qb["features"]["games_since_qb_change"]
        home_out = 1.0 if home_avail["starter_out_feature"] else 0.0
        away_out = 1.0 if away_avail["starter_out_feature"] else 0.0

        c3_margin_features = dict(row["margin_features"])
        c3_margin_features.update({
            "qb_diff_tenure_starts": float(home_tenure) - float(away_tenure),
            "qb_diff_games_since_change": float(home_gsc) - float(away_gsc),
            "availability_diff_starter_out": away_out - home_out,
        })

        output.append({
            **row,
            "c3_eligibility": "ELIGIBLE",
            "c3_data_gap_reason": None,
            "c3_margin_features": c3_margin_features,
        })

    output.sort(key=lambda r: (r["season"], r["week"], r["game_id"]))
    return output


def summarize_c3_exclusions(c3_rows: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Return {gap_reason: [game_id, ...]} for every non-ELIGIBLE C3 row.

    Reported explicitly by `game_id`, matching
    `game_market_c2_features.py`'s/`game_market_c2_research.py`'s own
    exclusion-reporting discipline -- never a silent drop.
    """
    by_reason: dict[str, list[str]] = {}
    for row in c3_rows:
        if row.get("c3_eligibility") == "ELIGIBLE":
            continue
        reason = str(row.get("c3_data_gap_reason"))
        by_reason.setdefault(reason, []).append(row["game_id"])
    for game_ids in by_reason.values():
        game_ids.sort()
    return by_reason

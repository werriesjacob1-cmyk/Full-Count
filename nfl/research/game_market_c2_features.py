"""Assemble strictly-prior NFL game-level features for the C2 challenger.

C2 asks whether the merged-but-unused feature substrate
(`team_prior_features`, `defense_prior_features`, `game_matchup_features`,
`pbp_prior_tendencies`) adds real accuracy on top of B0's strictly-prior
scoring baseline. This module is a *join/assembly* layer only: it calls the
existing point-in-time-safe builders unchanged and combines their outputs
into one home/away feature row per game. It never recomputes rolling
history itself and never touches a market line, price, or current-game
outcome.

Feature design (deliberately simple, main-effects, no interactions beyond
what `game_matchup_features` already provides):

- MARGIN features are home-minus-away differences of paired strictly-prior
  quantities, so a home/away relabeling of the same game flips every sign.
- TOTAL features are home-plus-away sums of the same quantities, which are
  invariant to home/away relabeling.

`ol_continuity_prior` (PFR snap counts) is intentionally excluded from the
fitted feature set. Real snap-count coverage from nflverse's own
`snap_counts_<season>.csv` release is empty for the 2012 season and only
becomes usable in 2013, which would leave roughly 13 of the 20
`development_2000_2019` seasons (2000-2012) with no OL-continuity signal at
all. Fitting a coefficient on a feature missing for the majority of the
development population would mean the coefficient is driven almost entirely
by imputation rather than data, so it is reported as a documented coverage
gap (see `OL_CONTINUITY_EXCLUSION_REASON`) rather than silently backfilled.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from nfl.research import defense_prior_features as defense_mod
from nfl.research import game_matchup_features as matchup_mod
from nfl.research import pbp_prior_tendencies as pbp_mod
from nfl.research import scoring_prior_features as scoring_mod
from nfl.research import team_prior_features as team_mod


class GameMarketC2FeatureError(ValueError):
    """Raised when C2 feature assembly finds an identity or join problem."""


OL_CONTINUITY_EXCLUSION_REASON = (
    "ol_continuity_prior requires PFR snap counts, real coverage begins in "
    "2013 (nflverse's snap_counts_2012.csv release is empty); excluded from "
    "the fitted C2 feature set because most of development_2000_2019 (2000-"
    "2012) would have no observed value, documented rather than imputed."
)

MARGIN_FEATURES = (
    "scoring_diff_points_for",
    "scoring_diff_points_against",
    "matchup_diff_ypp",
    "matchup_diff_play_volume",
    "epa_diff_offense",
    "epa_diff_defense_allowed",
    "pbp_diff_neutral_dropback_rate",
    "pbp_diff_scrimmage_plays_per_game",
)

TOTAL_FEATURES = (
    "scoring_sum_points_for",
    "scoring_sum_game_total",
    "matchup_sum_ypp",
    "matchup_sum_play_volume",
    "epa_sum_offense",
    "epa_sum_defense_allowed",
    "pbp_sum_neutral_dropback_rate",
    "pbp_sum_scrimmage_plays_per_game",
)


TEAM_OFFENSE_NEGATIVE_VALUE_BUG_NOTE = (
    "team_prior_features.build_prior_team_features (and, transitively, "
    "defense_prior_features.build_prior_defense_features) reject any input "
    "row where attempts, passing_yards, sacks_suffered, carries, or "
    "rushing_yards is negative, and reject it by raising for the *entire* "
    "call rather than skipping the one row. Real single-game team passing "
    "or rushing yardage can be negative (e.g. several negative-yardage "
    "completions or stuffed/scrambled carries netting a loss on a rare game "
    "log). A handful of real 1999-2025 nflverse play-by-play team-games have "
    "this property. This module excludes those specific (game_id, team) "
    "rows before calling either builder and reports them explicitly; it "
    "does not clip or fabricate a non-negative value. This looks like a "
    "genuine latent validation bug in a file outside C2's edit scope, so it "
    "is reported rather than patched."
)

_TEAM_OFFENSE_NON_NEGATIVE_FIELDS = (
    "attempts", "passing_yards", "sacks_suffered", "carries", "rushing_yards",
)


def filter_team_offense_rows_for_negative_value_bug(
    team_offense_rows: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split team-offense rows into (kept, excluded-for-known-bug).

    See `TEAM_OFFENSE_NEGATIVE_VALUE_BUG_NOTE`. Excluded rows are real
    historical observations, not fabricated ones; excluding them makes the
    affected games ineligible for C2 (surfaced as
    `TEAM_OFFENSE_OR_DEFENSE_STATS_MISSING_FOR_GAME`) rather than silently
    coercing a true negative value to zero.
    """
    rows = [dict(source) for source in team_offense_rows]
    bad_game_ids = {
        row["game_id"]
        for row in rows
        if any(float(row[field]) < 0 for field in _TEAM_OFFENSE_NON_NEGATIVE_FIELDS)
    }
    # defense_prior_features requires exactly two reciprocal rows per game, so
    # a bad row on one side also forces excluding the otherwise-fine other
    # team's row for the same game -- the whole game becomes a data gap.
    kept = [row for row in rows if row["game_id"] not in bad_game_ids]
    excluded = [row for row in rows if row["game_id"] in bad_game_ids]
    return kept, excluded


def build_c2_game_rows(
    scoring_rows: Iterable[Mapping[str, Any]],
    team_offense_rows: Iterable[Mapping[str, Any]],
    pbp_play_rows: Iterable[Mapping[str, Any]],
    *,
    rolling_window: int = 5,
    min_prior_games: int = 3,
) -> list[dict[str, Any]]:
    """Return one C2 candidate row per REG game in `scoring_rows`.

    `scoring_rows` must be the same historical scoring-history rows B0/C1 use
    (`game_market_b0_research.load_pinned_historical_rows`'s scoring output).
    `team_offense_rows` and `pbp_play_rows` are independently sourced from
    nflverse play-by-play (see the C2 research runner for the exact pinned
    assets); this function performs no network access and fabricates no
    missing value.
    """
    scoring_rows = [dict(row) for row in scoring_rows]
    schedule_rows = [
        {
            "game_id": row["game_id"],
            "season": row["season"],
            "week": row["week"],
            "season_type": row["game_type"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
        }
        for row in scoring_rows
    ]

    scoring_features = scoring_mod.build_prior_scoring_features(
        scoring_rows, rolling_window=rolling_window
    )
    offense_features = team_mod.build_prior_team_features(
        team_offense_rows, rolling_window=rolling_window
    )
    defense_features = defense_mod.build_prior_defense_features(
        team_offense_rows, rolling_window=rolling_window
    )
    pbp_features = pbp_mod.build_prior_pbp_tendencies(
        pbp_play_rows, rolling_window=rolling_window
    )

    try:
        matchup_features = matchup_mod.build_game_matchup_features(
            schedule_rows, offense_features, defense_features
        )
    except matchup_mod.GameMatchupFeatureError:
        # A schedule game with no team-offense/defense join anywhere is a
        # real data gap. Fall back to a per-game try/except below so a single
        # missing game does not fail the entire population.
        matchup_features = None

    scoring_idx: dict[tuple[str, str], dict[str, Any]] = {
        (row["game_id"], row["team"]): row for row in scoring_features
    }
    pbp_idx: dict[tuple[str, str], dict[str, Any]] = {
        (row["game_id"], row["team"]): row for row in pbp_features
    }
    matchup_idx: dict[str, dict[str, Any]] = {}
    if matchup_features is not None:
        for row in matchup_features:
            if row["game_id"] in matchup_idx:
                raise GameMarketC2FeatureError(f"duplicate matchup row: {row['game_id']}")
            matchup_idx[row["game_id"]] = row
    else:
        # Rebuild per-game to isolate exactly which games have a real gap,
        # instead of losing the whole population to one bad join.
        offense_by_key = {(r["game_id"], r["team"]): r for r in offense_features}
        defense_by_key = {(r["game_id"], r["team"]): r for r in defense_features}
        for sched in schedule_rows:
            gid, home, away = sched["game_id"], sched["home_team"], sched["away_team"]
            keys = [(gid, home), (gid, away)]
            if all(k in offense_by_key for k in keys) and all(k in defense_by_key for k in keys):
                try:
                    matchup_idx[gid] = matchup_mod.build_game_matchup_features(
                        [sched],
                        [offense_by_key[(gid, home)], offense_by_key[(gid, away)]],
                        [defense_by_key[(gid, home)], defense_by_key[(gid, away)]],
                    )[0]
                except matchup_mod.GameMatchupFeatureError:
                    pass

    output: list[dict[str, Any]] = []
    for row in scoring_rows:
        game_id = row["game_id"]
        home_team = row["home_team"]
        away_team = row["away_team"]
        home_scoring = scoring_idx.get((game_id, home_team))
        away_scoring = scoring_idx.get((game_id, away_team))
        if home_scoring is None or away_scoring is None:
            raise GameMarketC2FeatureError(f"scoring feature identity missing for {game_id}")

        matchup = matchup_idx.get(game_id)
        home_pbp = pbp_idx.get((game_id, home_team))
        away_pbp = pbp_idx.get((game_id, away_team))

        data_gap_reason = None
        if matchup is None:
            data_gap_reason = "TEAM_OFFENSE_OR_DEFENSE_STATS_MISSING_FOR_GAME"
        elif home_pbp is None or away_pbp is None:
            data_gap_reason = "PBP_TENDENCY_STATS_MISSING_FOR_GAME"

        prior_counts = {
            "scoring_home": home_scoring["prior_games_n"],
            "scoring_away": away_scoring["prior_games_n"],
        }
        if matchup is not None:
            prior_counts.update({
                "offense_home": matchup["home_offense_prior_games_n"],
                "offense_away": matchup["away_offense_prior_games_n"],
                "defense_home": matchup["home_defense_prior_games_n"],
                "defense_away": matchup["away_defense_prior_games_n"],
            })
        if home_pbp is not None and away_pbp is not None:
            prior_counts["pbp_home"] = home_pbp["prior_games_n"]
            prior_counts["pbp_away"] = away_pbp["prior_games_n"]

        sufficient_history = (
            data_gap_reason is None
            and all(n >= min_prior_games for n in prior_counts.values())
        )

        margin_features: dict[str, float] | None = None
        total_features: dict[str, float] | None = None
        if sufficient_history:
            raw = {
                "home_ppf": home_scoring["prior_mean_points_for"],
                "away_ppf": away_scoring["prior_mean_points_for"],
                "home_ppa": home_scoring["prior_mean_points_against"],
                "away_ppa": away_scoring["prior_mean_points_against"],
                "home_game_total": home_scoring["prior_mean_game_total"],
                "away_game_total": away_scoring["prior_mean_game_total"],
                "home_ypp_delta": matchup["home_ypp_matchup_delta"],
                "away_ypp_delta": matchup["away_ypp_matchup_delta"],
                "home_playvol_blend": matchup["home_play_volume_matchup_blend"],
                "away_playvol_blend": matchup["away_play_volume_matchup_blend"],
                "home_off_epa": matchup["home_offense_prior_mean_passing_epa"],
                "away_off_epa": matchup["away_offense_prior_mean_passing_epa"],
                "home_def_epa_allowed": matchup["home_defense_prior_mean_opp_passing_epa_allowed"],
                "away_def_epa_allowed": matchup["away_defense_prior_mean_opp_passing_epa_allowed"],
                "home_neutral_db": home_pbp["prior_neutral_dropback_rate_v1"],
                "away_neutral_db": away_pbp["prior_neutral_dropback_rate_v1"],
                "home_plays_pg": home_pbp["prior_mean_scrimmage_plays_per_game"],
                "away_plays_pg": away_pbp["prior_mean_scrimmage_plays_per_game"],
            }
            if any(value is None for value in raw.values()):
                data_gap_reason = "DERIVED_FEATURE_VALUE_UNAVAILABLE"
                sufficient_history = False
            else:
                margin_features = {
                    "scoring_diff_points_for": raw["home_ppf"] - raw["away_ppf"],
                    "scoring_diff_points_against": raw["home_ppa"] - raw["away_ppa"],
                    "matchup_diff_ypp": raw["home_ypp_delta"] - raw["away_ypp_delta"],
                    "matchup_diff_play_volume": raw["home_playvol_blend"] - raw["away_playvol_blend"],
                    "epa_diff_offense": raw["home_off_epa"] - raw["away_off_epa"],
                    "epa_diff_defense_allowed": raw["home_def_epa_allowed"] - raw["away_def_epa_allowed"],
                    "pbp_diff_neutral_dropback_rate": raw["home_neutral_db"] - raw["away_neutral_db"],
                    "pbp_diff_scrimmage_plays_per_game": raw["home_plays_pg"] - raw["away_plays_pg"],
                }
                total_features = {
                    "scoring_sum_points_for": raw["home_ppf"] + raw["away_ppf"],
                    "scoring_sum_game_total": raw["home_game_total"] + raw["away_game_total"],
                    "matchup_sum_ypp": raw["home_ypp_delta"] + raw["away_ypp_delta"],
                    "matchup_sum_play_volume": raw["home_playvol_blend"] + raw["away_playvol_blend"],
                    "epa_sum_offense": raw["home_off_epa"] + raw["away_off_epa"],
                    "epa_sum_defense_allowed": raw["home_def_epa_allowed"] + raw["away_def_epa_allowed"],
                    "pbp_sum_neutral_dropback_rate": raw["home_neutral_db"] + raw["away_neutral_db"],
                    "pbp_sum_scrimmage_plays_per_game": raw["home_plays_pg"] + raw["away_plays_pg"],
                }

        home_score = row.get("home_score")
        away_score = row.get("away_score")
        is_final = row["final_status"] == "FINAL" and home_score is not None and away_score is not None

        output.append({
            "game_id": game_id,
            "season": row["season"],
            "week": row["week"],
            "game_type": row["game_type"],
            "home_team": home_team,
            "away_team": away_team,
            "target_final_status": row["final_status"],
            "eligibility": "ELIGIBLE" if sufficient_history else "INSUFFICIENT_HISTORY",
            "data_gap_reason": data_gap_reason,
            "prior_games_n": dict(prior_counts),
            "min_prior_games": min_prior_games,
            "margin_features": margin_features,
            "total_features": total_features,
            "actual_margin": float(home_score - away_score) if is_final else None,
            "actual_total": float(home_score + away_score) if is_final else None,
            "feature_semantics": "STRICTLY_PRIOR_C2_ASSEMBLED_MATCHUP",
            "uses_market_line_as_feature": False,
            "uses_current_game_outcome_as_feature": False,
            "ol_continuity_prior_used": False,
            "ol_continuity_prior_exclusion_reason": OL_CONTINUITY_EXCLUSION_REASON,
        })

    output.sort(key=lambda r: (r["season"], r["week"], r["game_id"]))
    return output

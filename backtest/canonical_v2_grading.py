#!/usr/bin/env python3
"""Canonical-v2 outcome grading against the exact bound Statcast artifact.

Production grade_results remains untouched. This adapter exists only for
historical canonical v2 so batted-ball outcome families never issue a second
Baseball Savant request during grading.

For hard-hit/moonshot candidates:
- MLB box score proves the player appeared;
- the exact bound Statcast day proves whether the game is covered;
- a qualifying HR event is a hit;
- appearing with no qualifying event is a legitimate miss;
- missing box/game coverage remains ungraded rather than fabricated.
"""
from __future__ import annotations

import grade_results as gr
from backtest.http_provenance import get_active_ledger


class FrozenOutcomeGrader:
    def __init__(
        self,
        store,
        *,
        outcome_only_frame=None,
        outcome_only_date=None,
    ):
        self.store = store
        self.outcome_only_date = str(outcome_only_date) if outcome_only_date else None
        self.outcome_only_frame = (
            outcome_only_frame.copy(deep=True)
            if outcome_only_frame is not None
            else None
        )
        self._day_cache = {}
        self._original_grade_pick = gr.grade_pick
        self._original_fetch_game_statuses = gr.fetch_game_statuses

    def day_frame(self, day):
        day = str(day)
        if day not in self._day_cache:
            if day == self.outcome_only_date:
                if self.outcome_only_frame is None:
                    self._day_cache[day] = (None, "missing_outcome_only_statcast")
                else:
                    frame = self.outcome_only_frame
                    if "game_date" in frame.columns:
                        frame = frame[
                            frame["game_date"].astype(str).str[:10] == day
                        ]
                    self._day_cache[day] = (
                        frame.copy(deep=True),
                        "bound_outcome_only_statcast_parquet",
                    )
            else:
                self._day_cache[day] = (
                    self.store.window(day, day),
                    "bound_predictor_statcast_parquet",
                )
        return self._day_cache[day]

    def _special_grade(self, pick, game_statuses, date, allow_in_progress):
        game_pk = pick.get("game_pk")
        player_id = pick.get("player_id")
        if not game_pk or not player_id:
            return {**pick, "grade": "ungraded", "reason": "missing game_pk/player_id"}

        status = game_statuses.get(game_pk)
        final = gr.is_final(status)
        if not final and not allow_in_progress:
            detail = (status or {}).get("detailedState", "unknown")
            return {
                **pick,
                "grade": "ungraded",
                "reason": f"game not final yet (status: {detail})",
            }
        if not date:
            return {
                **pick,
                "grade": "ungraded",
                "reason": "no date supplied for frozen Statcast grading",
            }

        row, err = gr.get_box_line(game_pk, player_id, is_pitcher=False)
        if row is None:
            return {
                **pick,
                "grade": "ungraded",
                "reason": err or "player not found in box score",
                **(gr.opportunity_context(pick, None, game_pk) if final else {}),
            }

        frame, outcome_source = self.day_frame(date)
        required = {"batter", "game_pk", "events", "launch_speed", "hit_distance_sc"}
        if frame is None or frame.empty or not required.issubset(frame.columns):
            return {
                **pick,
                "grade": "ungraded",
                "reason": "bound Statcast source lacks required same-day batted-ball coverage",
                **gr.opportunity_context(pick, row, game_pk),
            }

        game_rows = frame[frame["game_pk"] == int(game_pk)]
        if game_rows.empty:
            return {
                **pick,
                "grade": "ungraded",
                "reason": "bound Statcast source has no rows for this game",
                **gr.opportunity_context(pick, row, game_pk),
            }

        batter_rows = game_rows[game_rows["batter"] == int(player_id)]
        stat = (pick.get("projection") or {}).get("stat")

        if stat in ("hard_hit_105", "hard_hit_110"):
            threshold = 105 if stat == "hard_hit_105" else 110
            qualifying = batter_rows[
                (batter_rows["events"] == "home_run")
                & batter_rows["launch_speed"].notna()
                & (batter_rows["launch_speed"] >= threshold)
            ]
            hit = not qualifying.empty
            return {
                **pick,
                "grade": "hit" if hit else "miss",
                "actual": hit,
                "actual_stat": "hr_at_exit_velocity",
                "threshold": threshold,
                "canonical_v2_outcome_source": outcome_source,
                **gr.opportunity_context(pick, row, game_pk),
            }

        if stat == "moonshot_420":
            qualifying = batter_rows[
                (batter_rows["events"] == "home_run")
                & batter_rows["hit_distance_sc"].notna()
                & (batter_rows["hit_distance_sc"] >= 420)
            ]
            hit = not qualifying.empty
            return {
                **pick,
                "grade": "hit" if hit else "miss",
                "actual": hit,
                "actual_stat": "moonshot_420",
                "threshold": 420,
                "canonical_v2_outcome_source": outcome_source,
                **gr.opportunity_context(pick, row, game_pk),
            }

        raise AssertionError(f"unsupported special stat {stat!r}")

    def fetch_game_statuses(self, *args, **kwargs):
        ledger = get_active_ledger()
        if ledger is not None:
            ledger.set_phase("outcome_grading")
        return self._original_fetch_game_statuses(*args, **kwargs)

    def grade_pick(
        self,
        pick,
        game_statuses,
        date=None,
        allow_in_progress=False,
    ):
        stat = (pick.get("projection") or {}).get("stat")
        if stat in ("hard_hit_105", "hard_hit_110", "moonshot_420"):
            return self._special_grade(
                pick,
                game_statuses,
                date,
                allow_in_progress,
            )
        return self._original_grade_pick(
            pick,
            game_statuses,
            date=date,
            allow_in_progress=allow_in_progress,
        )

    def install(self):
        gr.grade_pick = self.grade_pick
        gr.fetch_game_statuses = self.fetch_game_statuses

    def uninstall(self):
        gr.grade_pick = self._original_grade_pick
        gr.fetch_game_statuses = self._original_fetch_game_statuses

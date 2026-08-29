#!/usr/bin/env python3
"""Canonical-v2 historical team identity boundary.

Production behavior is intentionally untouched. Canonical v2 installs this
adapter only while replaying historical dates so no feature can resolve a
2024/2025 team through today's active franchise directory.

Rules:
- the slate's away/home MLB team IDs come from the date-addressed schedule;
- team-directory lookups are explicitly season-addressed;
- bullpen fatigue uses those stable IDs directly, never statsapi.lookup_team;
- engine's name->abbr cache is reset for every simulated date/year;
- missing or contradictory team identity fails closed.
"""
from __future__ import annotations

import functools
from concurrent.futures import ThreadPoolExecutor

import backtest.engine as engine
import generate_picks as gp
import mlb_daily as m


class HistoricalTeamIdentityError(RuntimeError):
    pass


class HistoricalTeamIdentity:
    def __init__(self):
        self._season_cache = {}
        self._original_get_team_ids = m.get_team_ids
        self._original_fetch_bullpen_scores = gp.fetch_bullpen_scores
        self._installed = False
        self._active_year = None
        self._active_date = None

    def _year(self):
        try:
            year = int(m.YEAR)
        except (TypeError, ValueError) as exc:
            raise HistoricalTeamIdentityError(
                f"canonical-v2 cannot resolve simulated MLB season from YEAR={m.YEAR!r}"
            ) from exc
        if year < 1900 or year > 2100:
            raise HistoricalTeamIdentityError(
                f"canonical-v2 invalid simulated MLB season {year}"
            )
        return year

    def get_team_ids_for_season(self, year):
        year = int(year)
        if year in self._season_cache:
            return self._season_cache[year]

        response = m.retry_get(
            "https://statsapi.mlb.com/api/v1/teams",
            params={"sportId": 1, "season": year},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
        raw = (response.json() or {}).get("teams") or []

        rows = []
        seen_ids = set()
        for team in raw:
            team_id = team.get("id")
            name = team.get("name")
            abbr = team.get("abbreviation")
            if team_id is None or not name or not abbr:
                continue
            team_id = int(team_id)
            if team_id in seen_ids:
                raise HistoricalTeamIdentityError(
                    f"season {year} team directory duplicates MLB team id {team_id}"
                )
            seen_ids.add(team_id)
            rows.append({
                "id": team_id,
                "abbr": str(abbr),
                "name": str(name),
            })

        # MLB has had 30 clubs throughout the 2024-2026 canonical range.
        # Anything else is a source failure/schema surprise, not a reason to
        # silently score with an incomplete team map.
        if len(rows) != 30 or len(seen_ids) != 30:
            raise HistoricalTeamIdentityError(
                f"season {year} team directory expected 30 unique MLB clubs, "
                f"got {len(rows)}"
            )

        self._season_cache[year] = rows
        return rows

    def get_team_ids(self):
        return self.get_team_ids_for_season(self._year())

    def prepare_date(self, day):
        try:
            year = int(str(day)[:4])
        except (TypeError, ValueError) as exc:
            raise HistoricalTeamIdentityError(
                f"invalid canonical date {day!r}"
            ) from exc

        self._active_year = year
        self._active_date = str(day)

        # engine._team_abbr() memoizes name->abbr for speed. That cache must
        # never cross a season boundary because 2024 Oakland Athletics and
        # later Athletics are not the same string even though MLB team id 133
        # is stable.
        engine._ABBR_BY_NAME = None

        # Force the season directory to be proven while the current date's
        # HTTP provenance ledger is active, so the exact mapping consumed by
        # this replay date is content-bound.
        teams = self.get_team_ids_for_season(year)
        ids = {row["id"] for row in teams}
        if len(ids) != 30:
            raise HistoricalTeamIdentityError(
                f"season {year} team directory lost identity uniqueness"
            )


    def _safe_bullpen_schedule(
        self,
        date=None,
        start_date=None,
        end_date=None,
        team="",
        opponent="",
        sportId=1,
        game_id=None,
        leagueId=None,
        season=None,
        include_series_status=True,
    ):
        """Return only games conclusively completed before simulated D.

        MLB's historical schedule endpoint is mutable around postponements.
        A request whose date BLOCK ends at D-1 can still contain a gamePk
        whose current officialDate is D (or later); fetching that gamePk's
        current box score would then import future bullpen usage.  The public
        statsapi.schedule() wrapper discards officialDate, so canonical v2
        reads the same raw schedule response and applies the missing temporal
        gate before the unchanged bullpen worker sees any game IDs.
        """
        if self._active_date is None:
            raise HistoricalTeamIdentityError(
                "canonical-v2 bullpen schedule used without active simulated date"
            )
        if (
            date is not None
            or game_id is not None
            or opponent not in ("", None)
            or leagueId is not None
            or season is not None
            or not start_date
            or not end_date
            or team in ("", None)
        ):
            raise HistoricalTeamIdentityError(
                "canonical-v2 bullpen schedule received unexpected query shape"
            )

        hydrate = (
            "decisions,probablePitcher(note),linescore,broadcasts,"
            "game(content(media(epg))),seriesStatus"
        )
        response = m.retry_get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={
                "startDate": start_date,
                "endDate": end_date,
                "teamId": str(team),
                "sportId": str(sportId),
                "hydrate": hydrate,
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json() or {}

        games = []
        for date_block in payload.get("dates") or []:
            for game in date_block.get("games") or []:
                official_date = str(game.get("officialDate") or "")
                if not official_date:
                    raise HistoricalTeamIdentityError(
                        f"gamePk {game.get('gamePk')}: historical schedule lacks officialDate"
                    )

                # Strictly pre-D. This is the decisive guard for a postponed
                # D-1 schedule entry whose gamePk was ultimately played on D.
                if official_date >= self._active_date:
                    continue

                status = game.get("status") or {}
                final_code = str(
                    status.get("codedGameState")
                    or status.get("statusCode")
                    or ""
                )
                if final_code not in {"F", "O"}:
                    # Never ask today's immutable feed for a game that was
                    # postponed/suspended/incomplete in the bounded schedule.
                    continue

                game_pk = game.get("gamePk")
                if game_pk is None:
                    raise HistoricalTeamIdentityError(
                        "historical bullpen schedule contains game without gamePk"
                    )
                games.append({
                    "game_id": int(game_pk),
                    "game_datetime": game.get("gameDate"),
                    "game_date": official_date,
                    "game_num": int(game.get("gameNumber") or 1),
                })
        return games

    def fetch_bullpen_scores(self, game_meta, pit_season_df=None):
        if not game_meta:
            return {}

        year = self._year()
        season_teams = self.get_team_ids_for_season(year)
        valid_ids = {row["id"] for row in season_teams}

        jobs = []
        seen_ids = set()
        for gm in game_meta:
            for side in ("away", "home"):
                team_name = gm.get(f"{side}_team")
                raw_team_id = gm.get(f"{side}_team_id")
                if not team_name or raw_team_id is None:
                    raise HistoricalTeamIdentityError(
                        f"gamePk {gm.get('game_pk')}: missing stable {side} team "
                        "name/id from date-addressed schedule"
                    )
                try:
                    team_id = int(raw_team_id)
                except (TypeError, ValueError) as exc:
                    raise HistoricalTeamIdentityError(
                        f"gamePk {gm.get('game_pk')}: invalid {side} team id "
                        f"{raw_team_id!r}"
                    ) from exc
                if team_id not in valid_ids:
                    raise HistoricalTeamIdentityError(
                        f"gamePk {gm.get('game_pk')}: team id {team_id} is absent "
                        f"from season {year} MLB directory"
                    )
                if team_id in seen_ids:
                    continue
                seen_ids.add(team_id)
                jobs.append((str(team_name), team_id))

        out = {}
        if not jobs:
            return out

        role_classifier = gp._bullpen_role_classifier(pit_season_df)
        fetch_one = functools.partial(
            m._bullpen_fetch_one,
            is_rotation_starter=role_classifier,
        )
        original_schedule = m.statsapi.schedule
        m.statsapi.schedule = self._safe_bullpen_schedule
        try:
            with ThreadPoolExecutor(max_workers=min(10, len(jobs))) as pool:
                for team_name, usage, err in pool.map(fetch_one, jobs):
                    if err:
                        # The production path degrades here. Canonical evidence
                        # must not silently erase a feature because its historical
                        # source failed.
                        raise HistoricalTeamIdentityError(
                            f"historical bullpen fetch failed for {team_name}: {err}"
                        )
                    if not usage:
                        # Preserve production semantics exactly: a genuinely
                        # empty recent-usage set contributes no bullpen feature.
                        # Identity resolution has already succeeded above; do not
                        # turn "no data" into an invented zero-fatigue signal.
                        continue
                    fatigued = sum(
                        1 for item in usage.values()
                        if item.get("pitches", 0) > 60
                    )
                    out[team_name] = {
                        "fatigued_relievers": fatigued,
                        "tracked": len(usage),
                        "relievers": gp._reliever_detail(usage),
                    }
        finally:
            m.statsapi.schedule = original_schedule

        return out

    def install(self):
        if self._installed:
            return
        m.get_team_ids = self.get_team_ids
        gp.fetch_bullpen_scores = self.fetch_bullpen_scores
        self._installed = True

    def uninstall(self):
        if not self._installed:
            return
        m.get_team_ids = self._original_get_team_ids
        gp.fetch_bullpen_scores = self._original_fetch_bullpen_scores
        engine._ABBR_BY_NAME = None
        self._installed = False

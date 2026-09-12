#!/usr/bin/env python3
"""Parse archived official NFL game-specific inactive-report HTML.

This parser is intentionally downstream of raw capture. It never fetches live
NFL pages and never rewrites the byte-exact archive. Because raw HTML is
preserved, this parser can be replaced or versioned later without losing the
original observation.

The output keeps source-local structure only:
- source team label,
- listed position,
- player display name,
- NFL player href/slug,
- free-text note,
- whether the source note literally says "emergency third QB".

No canonical NFL player ID, game ID, starter identity, or model feature is
inferred here.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

PARSER_CONTRACT_VERSION = 1


def report_published_at(body: bytes) -> str | None:
    """Read the article's publication clock, never its later modification time."""
    values = set()
    for script in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', body.decode('utf-8', errors='replace'), re.I | re.S):
        try:
            data = json.loads(script)
        except (ValueError, TypeError):
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get('@type') in ('NewsArticle', 'Article') and node.get('datePublished'):
                values.add(str(node['datePublished']))
    return next(iter(values)) if len(values) == 1 else None


def _clean(parts) -> str:
    text = " ".join(str(part).replace("\xa0", " ") for part in parts)
    return " ".join(text.split()).strip()


def _player_path(href: str) -> str | None:
    href = str(href or "").strip()
    if not href:
        return None
    parts = urlsplit(href)
    if parts.scheme:
        if parts.scheme.lower() != "https":
            return None
        if parts.netloc.lower() != "www.nfl.com":
            return None
    path = parts.path or ""
    if not path.startswith("/players/"):
        return None
    slug = path.rstrip("/").split("/")[-1]
    if not slug:
        return None
    return path


class _ReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.h1_titles: list[str] = []
        self._in_h1 = False
        self._h1_parts: list[str] = []

        self._in_h3 = False
        self._h3_parts: list[str] = []
        self.current_team: str | None = None

        self._team_list_depth = 0
        self._in_li = False
        self._li_prefix: list[str] = []
        self._li_name: list[str] = []
        self._li_suffix: list[str] = []
        self._player_href: str | None = None
        self._player_slug: str | None = None
        self._in_player_anchor = False
        self._saw_player_anchor = False

        self.team_players: OrderedDict[str, list[dict]] = OrderedDict()

    def handle_starttag(self, tag, attrs):
        tag = str(tag).lower()

        if tag in ("h1", "h2"):
            self.current_team = None

        if tag == "h1":
            self._in_h1 = True
            self._h1_parts = []
            return

        if tag == "h3":
            self._in_h3 = True
            self._h3_parts = []
            return

        if tag == "ul":
            if self.current_team:
                self._team_list_depth += 1
            return

        if tag == "li" and self._team_list_depth > 0:
            self._in_li = True
            self._li_prefix = []
            self._li_name = []
            self._li_suffix = []
            self._player_href = None
            self._player_slug = None
            self._in_player_anchor = False
            self._saw_player_anchor = False
            return

        if tag == "a" and self._in_li:
            href = None
            for key, value in attrs:
                if str(key).lower() == "href":
                    href = str(value or "")
                    break
            path = _player_path(href or "")
            if path is not None:
                self._player_href = href
                self._player_slug = path.rstrip("/").split("/")[-1]
                self._in_player_anchor = True
                self._saw_player_anchor = True

    def handle_endtag(self, tag):
        tag = str(tag).lower()

        if tag == "h1" and self._in_h1:
            title = _clean(self._h1_parts)
            if title:
                self.h1_titles.append(title)
            self._in_h1 = False
            self._h1_parts = []
            return

        if tag == "h3" and self._in_h3:
            team = _clean(self._h3_parts)
            self.current_team = team or None
            self._in_h3 = False
            self._h3_parts = []
            return

        if tag == "a" and self._in_li:
            self._in_player_anchor = False
            return

        if tag == "li" and self._in_li:
            self._finish_li()
            self._in_li = False
            return

        if tag == "ul" and self._team_list_depth > 0:
            self._team_list_depth -= 1
            return

    def handle_data(self, data):
        if self._in_h1:
            self._h1_parts.append(data)

        if self._in_h3:
            self._h3_parts.append(data)

        if not self._in_li:
            return

        if self._in_player_anchor:
            self._li_name.append(data)
        elif not self._saw_player_anchor:
            self._li_prefix.append(data)
        else:
            self._li_suffix.append(data)

    def _finish_li(self):
        if not self.current_team or not self._saw_player_anchor:
            return

        position = _clean(self._li_prefix)
        name = _clean(self._li_name)
        note = _clean(self._li_suffix)
        href = str(self._player_href or "").strip()
        slug = str(self._player_slug or "").strip()

        if not position or not name or not href or not slug:
            return

        players = self.team_players.setdefault(self.current_team, [])
        if any(p["source_player_href"] == href for p in players):
            raise ValueError(
                f"duplicate source player within team {self.current_team}: {href}"
            )

        normalized_note = note or None
        players.append({
            "listed_position": position,
            "player_name": name,
            "source_player_href": href,
            "source_player_slug": slug,
            "note": normalized_note,
            "emergency_third_qb": (
                normalized_note is not None
                and "emergency third qb" in normalized_note.casefold()
            ),
            "listed_inactive": True,
        })


def parse_report(body: bytes) -> dict:
    """Parse one archived official NFL inactive-report article.

    Fails closed if the bytes do not look like an inactive article or no player
    sections can be recovered. A parser failure is not evidence that no players
    were inactive.
    """
    if not isinstance(body, (bytes, bytearray)) or not body:
        raise ValueError("inactive report body must be non-empty bytes")

    parser = _ReportParser()
    parser.feed(bytes(body).decode("utf-8", errors="replace"))
    parser.close()

    title = next(
        (
            title for title in parser.h1_titles
            if "inactive" in title.casefold()
        ),
        None,
    )
    if title is None:
        raise ValueError("document is not an inactive report")

    teams = [
        {
            "source_team_label": team,
            "players": players,
        }
        for team, players in parser.team_players.items()
        if players
    ]
    player_count = sum(len(team["players"]) for team in teams)
    if player_count == 0:
        raise ValueError("inactive report contains no parsed player rows")

    return {
        "parser_contract_version": PARSER_CONTRACT_VERSION,
        "report_title": title,
        "report_published_at": report_published_at(bytes(body)),
        "team_count": len(teams),
        "player_count": player_count,
        "teams": teams,
        "canonical_game_id": None,
        "canonical_player_ids_bound": False,
    }

#!/usr/bin/env python3
"""dashboard/build_dashboard.py — builds the Full Count Board (the research
dashboard, distinct from the curated top-10 board generate_picks.py ships)
in one pass: a live, isolated re-run of the real scoring pipeline to
capture EVERY qualifying candidate per prop family (not just the single
winner select_best_by_category/select_moonshots normally keep for the
curated board), then writes it as a small static site: dashboard/static/
's hand-written index.html/app.css/app.js copied unchanged, plus a fresh
docs/data.json.

PHASE 4 REBUILD (2026-08-16). The page used to be one ~4.7MB self-
contained HTML file (fonts and the ENTIRE data payload both base64/JSON-
embedded inline, then the same data payload written AGAIN as a separate
~4.4MB data.json for polling to fetch) that force-reloaded itself every 30
minutes. It is now: a small static shell + stylesheet + script (changes
only when this site's own code changes, cacheable across daily data
refreshes), a single deduplicated data.json (every prop appears exactly
once; recommendation_status/stat are real fields the client filters on,
not separate server-built lists), and a small live.json the client polls
for price/grade/status deltas instead of re-fetching the whole board. See
results in this project's Phase 4 report.

Read-only against the real pipeline: OUTPUT_DIR/PLAYERS_DIR are redirected
to a throwaway temp directory for the whole run, so nothing here ever
touches output/, data/players/, or any file this repo actually commits.

    python3 dashboard/build_dashboard.py [--out-dir PATH]

Intended to be run once a day (a fresh live pass takes several minutes and
makes real calls to FanGraphs/Statcast/FanDuel -- this is not something to
run every few minutes). The caller is responsible for publishing the
resulting directory wherever it needs to go; this script only builds it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone

try:
    from .live_state import (GAME_FIELDS, PUBLICATION_FIELDS, SETTLEMENT_FIELDS,
                             IDENTITY_SCHEMA_VERSION, SCHEMA_VERSION,
                             apply_live_overlay, atomic_write_json,
                             before_betting_cutoff, canonical_prop_id, game_state,
                             load_live_state, market_side_token, prop_identity_key,
                             stable_prop_id, utc_now, validate_payload_identities)
    from .publication_registry import (DEFAULT_REGISTRY_PATH, all_published_snapshots,
                                       load_registry)
except ImportError:  # direct script execution: python dashboard/build_dashboard.py
    from live_state import (GAME_FIELDS, PUBLICATION_FIELDS, SETTLEMENT_FIELDS,
                            IDENTITY_SCHEMA_VERSION, SCHEMA_VERSION,
                            apply_live_overlay, atomic_write_json,
                            before_betting_cutoff, canonical_prop_id, game_state,
                            load_live_state, market_side_token, prop_identity_key,
                            stable_prop_id, utc_now, validate_payload_identities)
    from publication_registry import (DEFAULT_REGISTRY_PATH, all_published_snapshots,
                                      load_registry)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD_DIR = os.path.join(REPO_ROOT, "dashboard")


def log(msg):
    print(msg, flush=True)


def _game_schedule(date):
    """Return start time and raw MLB status keyed by game id for ``date``.

    New candidate generation uses ``started`` to remain pregame-only. The
    raw status also drives publication-lifecycle reconciliation so an exact
    previously published Top Pick remains visible for settlement. Non-fatal
    on failure (empty dict): timestamps and prior state still fail closed,
    and a schedule outage must not erase the public board.
    """
    import mlb_daily as m
    try:
        r = m.retry_get("https://statsapi.mlb.com/api/v1/schedule", params={"sportId": 1, "date": date},
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        games = r.json().get("dates", [{}])[0].get("games", [])
        return {g["gamePk"]: {"started": g.get("status", {}).get("abstractGameState") != "Preview",
                              "start": g.get("gameDate"), "status": g.get("status", {})}
                for g in games}
    except Exception as e:
        log(f"  (couldn't fetch game schedule/status: {e} -- game-start filtering skipped this build)")
        return {}


STREAK_MIN = 3  # showing every player with a 1-game "streak" would be everyone who got a
                # hit last night -- noise, not a trend. 3+ consecutive games/starts is the
                # common threshold real sports coverage treats as notable.


# Every real per-game batter field batter_recent_game_log() returns, i.e.
# every batter market a real streak can be built on. Direct follow-up
# request: "broaden the streaks to any relevant prop" -- not just
# hits/total_bases. "moonshot_400"-style keys are deliberately excluded:
# select_moonshots() scores those as their own distance-threshold family,
# never through this stat name, so they can't double-count against
# "home_runs" here.
BATTER_STREAK_STATS = ("hits", "total_bases", "runs", "rbis", "doubles", "triples",
                       "home_runs", "stolen_base", "singles", "hits_runs_rbis")


def _compute_streaks(all_priced, max_workers=12):
    """Real active streaks tied to tonight's board. Direct request,
    verbatim: "STREAKS. Hits in a row, 2+ bases in a row, over X
    strikeouts in a row, any trends that are useful," broadened by a
    direct follow-up to "any relevant prop" -- every batter market in
    BATTER_STREAK_STATS, each checked against that candidate's own real
    line (projection.needs), not a hardcoded per-stat threshold.

    Only computed for players who already have a real candidate on
    tonight's board (all_priced -- the same deduped moonshot+best-of-
    category pool the schedule breakdown above already built), same
    design principle as that feature: every streak entry ties back to an
    actual bettable prop tonight, not a generic league-wide trending list
    nobody can act on.

    Real per-game logs, not the aggregate empirical rate table (that
    table deliberately discards game order -- see mlb_sources.
    batter_recent_game_log's own docstring for why a streak needs a
    second, separate fetch rather than reusing it). Threaded, same
    pattern empirical_batter_prop_rates already uses, since this is one
    network call per streak-eligible player -- ONE fetch even when a
    player carries several different-stat candidate rows, since a single
    game log has every field needed to check all of them.

    Real bug, found live 2026-08-15: without a market_odds check, a
    streak got built against the model's own internally-chosen analysis
    threshold even when FanDuel has no matching line posted at all yet
    (market_odds is None) -- Jacob Misiorowski's real K line runs ~9;
    "15 straight starts clearing Over 5.5" was true and also nearly
    meaningless, since 5.5 was never a real number anyone could bet.
    This tab's own panel description promises "every entry here is a
    real prop you can actually bet tonight" -- enforce that instead of
    just claiming it."""
    import mlb_sources as msrc
    from concurrent.futures import ThreadPoolExecutor

    # Group by player_id (not deduped down to one row): a player can
    # carry several real candidate rows across different stat categories
    # (hits AND doubles AND rbis, say, via best-of-category) and each is
    # a genuinely different, real streak worth surfacing -- "more
    # substantial... more data" was the direct ask. Still only ONE game-
    # log fetch per player either way.
    batters, pitchers = defaultdict(list), {}
    for r in all_priced:
        if r.get("market_odds") is None:
            continue
        stat = (r.get("projection") or {}).get("stat")
        pid = r.get("player_id")
        if not pid:
            continue
        if r.get("type") == "batter" and stat in BATTER_STREAK_STATS:
            batters[pid].append(r)
        elif r.get("type") == "pitcher" and stat == "strikeouts" and pid not in pitchers:
            pitchers[pid] = r

    batter_logs, pitcher_logs = {}, {}
    if batters:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for pid, log_ in ex.map(lambda pid: (pid, msrc.batter_recent_game_log(pid)), batters.keys()):
                batter_logs[pid] = log_
    if pitchers:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for pid, log_ in ex.map(lambda pid: (pid, msrc.pitcher_recent_starts(pid)), pitchers.keys()):
                pitcher_logs[pid] = log_

    def _streak_len(games, clears):
        n = 0
        for g in games:
            if clears(g):
                n += 1
            else:
                break
        return n

    entries = []
    for pid, rows in batters.items():
        games = batter_logs.get(pid) or []
        for r in rows:
            stat = (r.get("projection") or {}).get("stat")
            needs = (r.get("projection") or {}).get("needs")
            if needs is None:
                continue
            n = _streak_len(games, lambda g, stat=stat, needs=needs: g.get(stat, 0) >= needs)
            if n >= STREAK_MIN:
                # PHASE 4: a lean {id, streak, streak_stat} reference, not a
                # third full copy of the row (see build_payload()'s single-
                # array data model) -- the client looks the rest up by id in
                # PAYLOAD.props. `r` here is already clean()'d (this
                # function is called with all_priced, built via clean()),
                # so a real id already exists on it.
                entries.append({"id": r["id"], "streak": n, "streak_stat": stat})
    for pid, r in pitchers.items():
        needs = (r.get("projection") or {}).get("needs")
        if needs is None:
            continue
        games = pitcher_logs.get(pid) or []
        n = _streak_len(games, lambda g: g["strikeouts"] >= needs)
        if n >= STREAK_MIN:
            entries.append({"id": r["id"], "streak": n, "streak_stat": "strikeouts"})

    entries.sort(key=lambda r: r["streak"], reverse=True)
    return entries[:15]


def run_live_fetch():
    """Isolated live re-run of generate_picks.py's scoring pass. Returns the
    same shape fetch_full_depth.py (the scratch prototype this was promoted
    from) produced: {"generated_at", "date", "moonshot": [...], "<stat>": [...]}.
    """
    scratch = tempfile.mkdtemp(prefix="fullcount_dashboard_")
    os.environ["OUTPUT_DIR"] = os.path.join(scratch, "output")
    os.environ["PLAYERS_DIR"] = os.path.join(scratch, "players")
    os.makedirs(os.environ["OUTPUT_DIR"], exist_ok=True)
    os.makedirs(os.environ["PLAYERS_DIR"], exist_ok=True)

    sys.path.insert(0, REPO_ROOT)
    os.chdir(REPO_ROOT)

    import generate_picks as gp
    import recommendation as gprec

    log("Starting isolated live scoring pass...")
    result = gp._build_and_score()
    if result is None:
        log("No games / nothing bettable right now.")
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "date": gp.m.TODAY,
                "_game_schedule": _game_schedule(gp.m.TODAY)}

    candidates, ctx = result
    game_meta = ctx["game_meta"]; park_wx = ctx["park_wx"]
    emp_pitchers = ctx["emp_pitchers"]
    early_po_prices = ctx.get("po_prices")
    log(f"Scored {len(candidates)} raw candidates across {len(game_meta)} bettable games.")

    candidates, _qc_rejected, assumed_lineup = gp.quality_control(candidates, game_meta, park_wx, emp_pitchers)
    log(f"{len(candidates)} candidates have a confirmed lineup; {len(assumed_lineup)} more will be "
        f"included with a 'lineup not confirmed' badge (Rotowire projection or last-known order).")

    signal_trust = gp.load_signal_trust()
    gp.apply_signal_weights(candidates, trust=signal_trust)

    import odds_fanduel as fd
    # Real request: "odds timestamp" as part of the freshness/versioning
    # rebuild. odds_fanduel itself carries no fetch-time field (a real gap
    # the audit found -- fetch_prop_prices() returns only a bare price
    # dict), so this captures it here instead: every price this run
    # attaches came from THIS ONE fetch, so one timestamp for the whole run
    # is the real, honest granularity rather than a per-row fabrication.
    odds_fetched_at = datetime.now(timezone.utc).isoformat()
    prices = fd.fetch_prop_prices()
    try:
        k_prices = fd.fetch_pitcher_strikeouts()
    except Exception:
        k_prices = {}
    try:
        fi_prices = fd.fetch_first_inning_totals()
    except Exception:
        fi_prices = {}
    po_prices = early_po_prices or {}
    combined_k_prices = ctx.get("combined_k_prices") or {}

    fd.attach_market_prices(candidates, prices=prices, k_prices=k_prices,
                            fi_prices=fi_prices, po_prices=po_prices,
                            combined_k_prices=combined_k_prices)
    log("Market prices attached to primary-family candidates.")

    # Direct request: "we shouldn't have to wait for lineups to at least get
    # a lean... there are still props for almost every player posted."
    # FanDuel prices most props well before lineups lock, so a real price
    # can attach to an early-look candidate the same way it does a
    # confirmed one -- the batting SLOT is still a guess (that's why
    # quality_control() held it out of `candidates`), but a real posted
    # price makes the lean worth more than a bare probability.
    fd.attach_market_prices(assumed_lineup, prices=prices, k_prices=k_prices,
                            fi_prices=fi_prices, po_prices=po_prices,
                            combined_k_prices=combined_k_prices)

    # Computed here, not after clean() below, because it needs player_id/
    # game_pk to screen out same-player and negatively/redundantly
    # correlated legs -- fields clean() strips before the dashboard payload
    # ever sees a row (kept lean since PAYLOAD is embedded in a page anyone
    # with the link can open). parlay_builder.py is the real, tested,
    # correlation-aware engine already built for this -- reused as-is
    # rather than reimplemented, since a naive client-side leg combiner
    # would silently drop the one thing that engine exists to get right.
    suggested_parlay = _build_suggested_parlay(candidates)

    # Direct follow-up request: "I want you to broaden the streaks to any
    # relevant prop. Our system should use assumed lineups... we would
    # just scratch the ones who don't end up on the final roster... this
    # way we can have a more substantial streak setting and more data
    # early in the day. We shouldn't have to wait for lineups." Assumed-
    # lineup candidates now flow through the SAME moonshot/category
    # selection as confirmed ones (still individually tagged
    # lineup_assumed=True -- see clean() and pickRow()'s earlier badge --
    # so nothing about this is silent), rather than being walled off in a
    # separate tab nobody browses by default. suggested_parlay above is
    # deliberately NOT extended this way: compounding several guessed
    # lineups into one multi-leg bet is a materially bigger risk than one
    # flagged single pick, and nothing in this request asked for that.
    #
    # "Scratch the ones who don't make the roster" already happens
    # structurally, not as a patch: every rebuild is a fresh, from-
    # scratch scoring pass (see this function's own docstring), so a
    # player who doesn't end up in a real lineup simply isn't generated
    # as a candidate on the NEXT rebuild at all -- lineup-watch triggers
    # that rebuild within ~10 minutes of the real lineup posting or
    # changing. A player who DOES make the roster gets re-scored with
    # real inputs (real batting slot, real platoon matchup) on that same
    # rebuild, which is what should move his confidence -- no separate
    # "confirm/scratch" step to build.
    combined_candidates = candidates + assumed_lineup

    moonshots_full = gp.select_moonshots(combined_candidates, prices, fd, n=9999)
    log(f"{len(moonshots_full)} total home_run candidates.")

    # min_score=0: direct report, verbatim -- "Even if a prop doesn't make
    # the main board I still want to show at least something. I don't want
    # to see NO lasers." Without this, select_best_by_category's default
    # MIN_QUALITY_SCORE floor could empty out an entire market for the
    # night (exactly what happened to Laser the night the HR-conditioning
    # fix landed and real Laser probabilities dropped) -- and an empty
    # category here means its tab never even gets added to tabs_order
    # below, so it vanishes from the site entirely rather than showing a
    # thin or unattractive slate. This site is meant to give bettors
    # options, not gatekeep them -- the "clears" vs "pass" price styling
    # already tells them which rows are genuinely good value.
    by_category_full = gp.select_best_by_category(combined_candidates, prices, fd, n_per_category=9999,
                                                   k_prices=k_prices, min_score=0)
    for stat, entries in by_category_full.items():
        log(f"  {stat}: {len(entries)} candidates")

    # GAME-START FILTERING. Candidate generation/recommendation remains
    # pregame-only: drop games already underway by the moment this scoring
    # pass runs, and carry game_pk/game_start through clean() so the client
    # can hide ordinary research rows that cross first pitch between builds.
    # reconcile_public_lifecycle() later restores only exact props proven to
    # have been public Top Picks; those must remain visible for settlement.
    schedule = _game_schedule(gp.m.TODAY)
    started = {pk for pk, info in schedule.items() if info["started"]}
    if started:
        before_ms = len(moonshots_full)
        moonshots_full = [r for r in moonshots_full if r.get("game_pk") not in started]
        before_cat = sum(len(v) for v in by_category_full.values())
        by_category_full = {stat: [r for r in entries if r.get("game_pk") not in started]
                            for stat, entries in by_category_full.items()}
        by_category_full = {stat: entries for stat, entries in by_category_full.items() if entries}
        after_cat = sum(len(v) for v in by_category_full.values())
        log(f"Game-start filter: removed {before_ms - len(moonshots_full)} moonshot(s) and "
            f"{before_cat - after_cat} category candidate(s) whose games are already underway.")

    # RECOMMENDATION LAYER -- the 2026-08-15 rebuild. Classifies every real
    # candidate into top_pick/lean/value/neutral via recommendation.py's
    # single classifier, using the SAME real hard floor everywhere on this
    # site (no separate ad-hoc "Locks"/"Top Picks" criteria anymore -- see
    # recommendation.py's own module docstring for why that mattered: a
    # Triple prop at 1.2% probability was shipping as a "High Confidence
    # Lock" under the old per-tab logic this replaces). Run BEFORE clean()
    # strips fields down, on the full raw candidate dicts -- classification
    # needs prob_ci/lift/reliability, all real fields that exist here and
    # get filtered out of the public payload.
    board_generated_at = datetime.now(timezone.utc).isoformat()
    all_candidates_for_rec = list(moonshots_full)
    for entries in by_category_full.values():
        all_candidates_for_rec.extend(entries)
    gprec.attach_recommendations(all_candidates_for_rec, odds_fetched_at=odds_fetched_at,
                                 board_generated_at=board_generated_at)

    def clean(rows):
        out = []
        for r in rows:
            game_pk = r.get("game_pk")
            proj = r.get("projection") or {}
            stat = proj.get("stat")
            combo = r.get("combo_player_ids")
            # PHASE 4: a real, stable identity for this exact prop -- the
            # single-array data model (see build_payload()) needs one, and
            # the fragile (name, prop) STRING matching refresh_prices.py/
            # refresh_grades.py/mergePriceUpdate() all used before this
            # (three independent reimplementations of "find this same row
            # again") collapses into one real key everywhere once every row
            # carries it directly. game_pk + player (or combo) + stat +
            # threshold is the same identity grade_pick() itself keys a
            # settlement on -- reusing it here rather than inventing a
            # separate one.
            market_side = market_side_token(r)
            cleaned = {
                "identity_version": IDENTITY_SCHEMA_VERSION,
                "type": r.get("type"), "name": r.get("name"), "team": r.get("team"),
                "matchup": r.get("matchup"), "side": r.get("side"), "prop": r.get("prop"),
                "projection": proj, "stat": stat, "lean": r.get("lean"),
                "market_side": market_side,
                "score": r.get("score"), "confidence": r.get("confidence"),
                "hit_probability": r.get("hit_probability"),
                "market_odds": r.get("market_odds"), "market_implied": r.get("market_implied"),
                "market_edge": r.get("market_edge"), "price_clears": r.get("price_clears"),
                # market_hold: PHASE 3 addition (see eval_lib.market_probability)
                # -- present (a real number) only on the genuinely two-sided
                # markets (strikeouts/pitcher_outs/nrfi_combined), where it is
                # the EXACT measured hold from both real posted sides, not an
                # assumed one. Surfaced so the detail view can say "exact
                # market price" instead of "estimated" where it's actually true.
                "market_hold": r.get("market_hold"),
                "reliability": r.get("reliability"), "reliability_note": r.get("reliability_note"),
                "sample_n": r.get("sample_n"),
                "why": (r.get("why") or [])[:4],
                "watchouts": (r.get("watchouts") or [])[:2],
                "base_rate": r.get("base_rate"), "lift": r.get("lift"),
                # prob_ci: real bug, found in the same audit -- this field
                # was computed (attach_reliability) and never even reached
                # the live dashboard's payload at all, so a mismatched CI
                # bug this exact field would have exposed stayed invisible
                # here (it only showed on the static board). Now correctly
                # scoped per exact line (see generate_picks._batter_options'
                # own fix) before it ever reaches this boundary.
                "prob_ci": r.get("prob_ci"),
                # The single field this whole rebuild exists to add: which
                # of the four real recommendation states this row earned,
                # and why -- computed once, above, by recommendation.py,
                # and carried through here rather than re-derived or
                # dropped at the serialization boundary (the exact "computed,
                # then discarded" failure this rebuild's own audit found
                # repeatedly elsewhere in this codebase).
                "recommendation_status": r.get("status"),
                "status_reasons": r.get("status_reasons"),
                "stale": r.get("stale", False),
                "game_pk": game_pk, "game_start": (schedule.get(game_pk) or {}).get("start"),
                # player_id/combo_player_ids: not used anywhere in this page's
                # own rendering, but required by grade_results.grade_pick() --
                # dashboard/refresh_grades.py reshapes a row back into a
                # candidate dict to grade it live, and needs these to find the
                # real box score line. Direct request: "for the top picks,
                # them to show when it's cashed... make the pick yellow when
                # the game is happening... green if it cashes, red if it
                # doesn't."
                "player_id": r.get("player_id"),
                "combo_player_ids": combo,
                # Early Look: True only for a candidate whose batting-order
                # slot is GUESSED (Rotowire projection or last-known
                # lineup), never a real posted one -- see quality_control()
                # in generate_picks.py. Carried on the row itself (not
                # inferred from which tab it's rendered in) so the client
                # can visibly flag it no matter where it ends up.
                "lineup_assumed": r.get("lineup_assumed"),
            }
            cleaned["id"] = canonical_prop_id(cleaned)
            out.append(cleaned)
        return out

    out = {"generated_at": board_generated_at, "date": gp.m.TODAY,
          "odds_fetched_at": odds_fetched_at,
          "_game_schedule": schedule,
          "recommendation_metadata": gprec.build_metadata(odds_fetched_at=odds_fetched_at,
                                                          board_generated_at=board_generated_at),
          "moonshot": clean(moonshots_full), "suggested_parlay": suggested_parlay}
    for stat, entries in by_category_full.items():
        out[stat] = clean(entries)

    # Per-game schedule breakdown. Direct request: "I want people to be able
    # to click on a game on the schedule, and get a breakdown of why X
    # props might be best for A B C reasons. Think time, weather, etc."
    # Built from the exact same weather/umpire data score_batter() already
    # used to score tonight's candidates -- this isn't a second, separate
    # read, just exposing the real reasoning instead of leaving it buried
    # inside the model. Already-started games are omitted from this schedule
    # research surface because it is built only from the new pregame scoring
    # pass. Published live picks remain visible on the pick surfaces through
    # the lifecycle reconciliation below.
    all_priced = clean(moonshots_full)
    for entries in by_category_full.values():
        all_priced.extend(clean(entries))
    picks_by_game = defaultdict(list)
    seen_pick_keys = set()
    for r in all_priced:
        pk = r.get("game_pk")
        if not pk or r.get("hit_probability") is None:
            continue
        key = (r.get("name"), r.get("prop"))
        if key in seen_pick_keys:
            continue  # moonshot/best-of-category can overlap on the same player+prop
        seen_pick_keys.add(key)
        picks_by_game[pk].append(r)

    ump_kbb = ctx.get("ump_kbb") or {}
    game_context = []
    for gm in game_meta:
        pk = gm.get("game_pk")
        if pk in started:
            continue
        wx = park_wx.get(gm.get("matchup")) or {}
        weather = None
        if wx.get("dome"):
            weather = {"dome": True}
        elif wx.get("temp") is not None:
            weather = {"dome": False, "temp": wx.get("temp"), "wind_mph": wx.get("wind_mph"),
                      "wind_effect": wx.get("wind_effect"), "park_hr_index": wx.get("park_hr_index"),
                      "precip_prob": wx.get("precip_prob")}
        uk = ump_kbb.get(gm.get("hp_ump")) or {}
        umpire = None
        if uk.get("k_pct") is not None:
            umpire = {"name": gm.get("hp_ump"), "k_pct": uk.get("k_pct"),
                     "bb_pct": uk.get("bb_pct"), "league_k_pct": uk.get("league_k_pct"),
                     "league_bb_pct": uk.get("league_bb_pct")}
        game_picks = sorted(picks_by_game.get(pk, []),
                           key=lambda r: r.get("hit_probability") or 0, reverse=True)[:6]
        game_context.append({
            "game_pk": pk, "matchup": gm.get("matchup"),
            "away_team": gm.get("away_team"), "home_team": gm.get("home_team"),
            "away_sp": gm.get("away_sp"), "home_sp": gm.get("home_sp"),
            "hp_ump": gm.get("hp_ump") if gm.get("hp_ump") != "TBD" else None,
            "game_start": (schedule.get(pk) or {}).get("start"),
            "weather": weather, "umpire": umpire,
            "is_getaway": bool(gm.get("is_getaway")), "is_opener": bool(gm.get("is_opener")),
            "picks": [{"name": r["name"], "prop": r["prop"], "hit_probability": r["hit_probability"],
                      "market_odds": r.get("market_odds"), "price_clears": r.get("price_clears"),
                      "why": (r.get("why") or [None])[0]} for r in game_picks],
        })
    out["game_context"] = game_context
    out["streaks"] = _compute_streaks(all_priced)
    return out


def _decimal_to_american(dec):
    """Standard decimal-to-American conversion -- prop_probability.py only
    ever goes the other direction (american->decimal, via decimal_odds),
    since every other price this file handles starts life as a FanDuel
    American price. A parlay's combined price starts life as a product of
    decimal odds instead, so this is the one place the board needs the
    reverse."""
    if dec is None:
        return None
    if dec >= 2.0:
        return round((dec - 1) * 100)
    return round(-100 / (dec - 1))


def _build_suggested_parlay(candidates):
    """A real, correlation-screened 3-leg parlay at the "safest" risk tier,
    computed by parlay_builder.py's own build_best_available_parlay --
    the same engine a customer's typed request would run through, not a
    simplified reimplementation. price_legs=False because these candidates
    already carry real FanDuel prices from attach_market_prices() above;
    re-fetching would just cost another round of live calls for the same
    answer.

    Returns None rather than a padded/partial parlay if the engine can't
    put together at least two real legs tonight -- an honest "nothing to
    suggest" beats a one-leg parlay dressed up as one."""
    try:
        import parlay_builder as pb
    except Exception as e:
        log(f"Suggested parlay: import failed ({e}), skipping.")
        return None
    # REAL BUG, found live 2026-08-12 running the actual pipeline: this used
    # to pass the raw `candidates` list straight through. parlay_builder.py's
    # own load_todays_pool() -- the normal way its pool gets built -- always
    # pre-filters to hit_probability is not None ("Only candidates with a
    # real hit_probability are included", per its own docstring); nothing
    # downstream in build_best_available_parlay/risk_band re-checks that.
    # quality_control() rejects on lineup/rain/opener grounds, not on
    # whether a probability could be computed at all, so plenty of real
    # candidates reach here with hit_probability=None -- and risk_band's
    # `lo <= c["hit_probability"] < hi` crashed the instant one of them did,
    # silently no-oping the whole feature via the except below on every real
    # run rather than ever actually producing a parlay.
    # REAL BUG, found live 2026-08-14 from a direct report: a candidate with
    # a real model hit_probability but no FanDuel line posted for it yet
    # could still be selected as a parlay leg -- a recommendation for a bet
    # nobody can actually place. hit_probability is not None only means the
    # MODEL has a read; market_odds is not None is the separate, additional
    # fact that FanDuel has actually priced it. parlay_builder.py's own
    # combined-odds math already treats unpriced legs correctly downstream
    # (priced_legs filters them out of the odds multiply), but that
    # protects the MATH, not the SELECTION -- an unpriced leg could still
    # win a slot in the 3 legs shown to Jacob.
    priced_pool = [c for c in candidates
                  if c.get("hit_probability") is not None and c.get("market_odds") is not None]
    try:
        result = pb.build_best_available_parlay(pool=priced_pool, n=3, risk_level=0, price_legs=False)
    except Exception as e:
        log(f"Suggested parlay: build failed ({e}), skipping.")
        return None
    legs = result.get("legs") or []
    if len(legs) < 2:
        log(f"Suggested parlay: only {len(legs)} real leg(s) available tonight, skipping.")
        return None
    return {
        "legs": [
            {"name": l.get("name"), "team": l.get("team"), "prop": l.get("prop"),
             "market_odds": l.get("market_odds"), "hit_probability": l.get("hit_probability"),
             "confidence": l.get("confidence")}
            for l in legs
        ],
        "naive_combined_probability": result.get("naive_combined_probability"),
        "naive_probability_note": result.get("naive_probability_note"),
        "combined_american_odds": _decimal_to_american(result.get("combined_decimal_odds")),
        "correlation_notes": result.get("correlation_notes") or [],
    }


CATEGORY_LABELS = {
    "hits": "Hits", "total_bases": "Total Bases", "home_runs": "Home Runs",
    "runs": "Runs", "rbis": "RBIs", "hits_runs_rbis": "Hits+Runs+RBIs",
    "singles": "Singles", "doubles": "Doubles", "triples": "Triples",
    "stolen_base": "Stolen Base", "strikeouts": "Strikeouts (K Props)",
    "nrfi_combined": "NRFI/YRFI (Both Teams)",
    "hard_hit_105": "Laser (105+ MPH)", "hard_hit_110": "Laser (110+ MPH)",
    "pitcher_outs": "Pitcher Outs Recorded",
    "combined_strikeouts": "Combined Starter Strikeouts",
    "moonshot": "Home Runs",
}

# Direct request: "Home runs should be more accessible and viewable.
# Shouldn't be so far down the bar." moonshot used to sit 9th here (14th
# tab overall, after locks/schedule/streaks/all/hits_runs_rbis/hits/
# total_bases/singles/doubles/triples/runs/rbis) -- moved up to 3rd, right
# behind the two most-bet main-board categories, instead of buried behind
# every counting stat. Only consumer of this order is the tab-building
# loop below (list(tabs.keys()) feeds straight into tabs_order); nothing
# else in the codebase reads CATEGORY_ORDER.
CATEGORY_ORDER = [
    "hits_runs_rbis", "hits", "moonshot", "total_bases", "singles",
    "doubles", "triples", "runs", "rbis", "stolen_base", "strikeouts",
    "combined_strikeouts", "pitcher_outs", "hard_hit_105", "hard_hit_110",
    "nrfi_combined",
]


def load_track_record(path=None):
    """PHASE 4: the site's Performance page must show the CURRENT
    (2026-08-15+ recommendation-layer) record and the LEGACY (pre-rebuild)
    record as two clearly separate things -- direct instruction: "Do not
    imply that legacy -26.8% ROI represents the current recommendation
    architecture... do not pretend the new architecture has proven itself
    before it has enough observations." Reads exactly the fields Phase 3
    built for this: deployment-proven public_top_pick_totals / top_pick_
    hit_rate / last_14_days_top_pick_hit_rate (current) alongside the
    pre-existing by_category_totals.main / main_hit_rate / last_14_days_
    hit_rate (legacy) -- see results/ANALYSIS.md for the full tier
    definitions this maps onto.

    Kept as a real file read separate from build_payload() so build_payload
    stays a pure function of its `result` argument, testable without a real
    history.json on disk.

    "current" and/or "legacy" are None (never a fabricated 0%/blank record)
    when that tier genuinely has no graded picks yet -- an honest "no track
    record yet" beats a fake one. As of this rebuild, "current" is None by
    construction: zero days have been graded under the new architecture."""
    path = path or os.path.join(REPO_ROOT, "results", "history.json")
    try:
        with open(path) as f:
            h = json.load(f)
    except Exception:
        return {"current": None, "legacy": None}

    tp = h.get("public_top_pick_totals") or {}
    tp_n = (tp.get("hits") or 0) + (tp.get("misses") or 0)
    current = None
    if tp_n > 0 and h.get("top_pick_hit_rate") is not None:
        current = {
            "hit_rate": h["top_pick_hit_rate"], "n": tp_n,
            "hits": tp.get("hits", 0), "misses": tp.get("misses", 0),
            "last_14d_hit_rate": h.get("last_14_days_top_pick_hit_rate"),
            "last_14d_n": h.get("last_14_days_top_pick_n"),
        }

    main = (h.get("by_category_totals") or {}).get("main") or {}
    main_n = (main.get("hits") or 0) + (main.get("misses") or 0)
    legacy = None
    if main_n > 0 and h.get("main_hit_rate") is not None:
        legacy = {
            "hit_rate": h["main_hit_rate"], "n": main_n,
            "hits": main.get("hits", 0), "misses": main.get("misses", 0),
            "last_14d_hit_rate": h.get("last_14_days_hit_rate"),
        }

    return {"current": current, "legacy": legacy}


def build_payload(result, track_record=None):
    """PHASE 4 REBUILD (2026-08-16): ONE canonical `props` array, no
    duplication. The pre-rebuild version of this function serialized every
    row up to FOUR times -- once in its own stat tab, once again in "all",
    and again in whichever of top_picks/leans/best_value it qualified for
    -- which is the exact, measured root cause of docs/data.json's ~4.4MB
    size (verified: summing every per-stat tab's length equalled "all"'s
    length exactly). recommendation_status/stat are already real fields on
    every row (see clean()), so "which bucket is this in" is now a client-
    side FILTER over one array, not a server-side copy into a second list.
    See dashboard/app.js's TOP_PICKS/LEANS/BEST_VALUE view functions."""
    import prop_probability as pp

    def add_estimated_odds(rows):
        for r in rows:
            p = r.get("hit_probability")
            r["estimated_odds"] = pp.american_odds(p) if p is not None else None
        return rows

    # select_best_by_category's own CATEGORY_LABELS includes "home_runs" (a
    # 2026-08-12 audit fix in generate_picks.py), so it produces the exact
    # same home-run field select_moonshots() already does under "moonshot"
    # -- verified live (identical names, order, probabilities). Drop the
    # duplicate rather than double-count "Home Runs".
    result = dict(result)
    result.pop("home_runs", None)

    meta_keys = {"generated_at", "date", "suggested_parlay", "game_context", "streaks",
                "odds_fetched_at", "recommendation_metadata", "_game_schedule"}
    all_rows = []
    family_counts = {}
    for stat, rows in result.items():
        if stat in meta_keys or not isinstance(rows, list):
            continue
        rows = [r for r in rows if r.get("hit_probability") is not None]
        if not rows:
            continue
        add_estimated_odds(rows)
        all_rows.extend(rows)
        family_counts[stat] = family_counts.get(stat, 0) + len(rows)

    # Real FanDuel line first, then everything without one -- the exact
    # "ranked = priced + unpriced" split generate_picks.py's own top10
    # selection already uses, applied here for the same reason. Sorting by
    # raw model probability alone let an unpriced candidate -- a real
    # player and a real projection, but no market FanDuel has actually
    # posted yet -- rank ABOVE genuinely bettable picks just for having a
    # bigger number attached, which reads as "this is a recommendation"
    # when it's not currently a bet anyone can place. Found live
    # 2026-08-12: David Peterson's Outs Recorded read (63.2%, no line) was
    # sorting above several real, priced, lower-probability Strikeouts
    # candidates for exactly this reason. Within that, Top Pick first (the
    # one state that's an actual recommendation), then by edge -- a
    # sensible default order for anyone rendering the raw payload order,
    # even though every real view in app.js re-sorts explicitly anyway.
    _STATUS_RANK = {"top_pick": 0, "lean": 1, "value": 2, "neutral": 3, None: 4}

    def _default_order(r):
        return (r.get("market_odds") is None, _STATUS_RANK.get(r.get("recommendation_status"), 4),
               -(r.get("market_edge") or 0))

    all_rows.sort(key=_default_order)

    families = [{"stat": stat, "label": CATEGORY_LABELS.get(stat, stat.replace("_", " ").title()),
                "count": count}
               for stat, count in sorted(family_counts.items(), key=lambda kv: -kv[1])]

    n_top_pick = sum(1 for r in all_rows if r.get("recommendation_status") == "top_pick")
    n_lean = sum(1 for r in all_rows if r.get("recommendation_status") == "lean")
    n_value = sum(1 for r in all_rows if r.get("recommendation_status") == "value")

    return {
        "schema_version": SCHEMA_VERSION,
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "date": result.get("date"),
        "generated_at": result.get("generated_at"),
        "odds_fetched_at": result.get("odds_fetched_at"),
        "recommendation_metadata": result.get("recommendation_metadata"),
        "families": families,
        "summary": {"n_props": len(all_rows), "n_top_pick": n_top_pick, "n_lean": n_lean,
                   "n_value": n_value, "n_games": len(result.get("game_context") or [])},
        "props": all_rows,
        "schedule": result.get("game_context") or [],
        "streaks": result.get("streaks") or [],
        "track_record": track_record,
        "suggested_parlay": result.get("suggested_parlay"),
    }


def _status_for(schedule, game_pk):
    entry = (schedule or {}).get(game_pk) or {}
    return entry.get("status") or {}


def _recount_payload(payload):
    props = payload.get("props") or []
    summary = payload.setdefault("summary", {})
    summary["n_props"] = len(props)
    summary["n_top_pick"] = sum(r.get("recommendation_status") == "top_pick" for r in props)
    summary["n_lean"] = sum(r.get("recommendation_status") == "lean" for r in props)
    summary["n_value"] = sum(r.get("recommendation_status") == "value" for r in props)


def _publication_provenance(row):
    return {key: row.get(key) for key in (
        "published_top_pick_at", "publication_artifact_id", "publication_source_commit",
        "publication_run_id", "publication_deployment_id",
    ) if row.get(key) is not None}


def _publication_snapshot(row):
    """Return the immutable registry snapshot without deployment fields."""
    return {key: value for key, value in row.items() if key not in PUBLICATION_FIELDS}


def _with_base_lifecycle(row, state, observed_at, source="mlb_schedule"):
    row["game_state"] = state
    row["game_state_observed_at"] = observed_at
    row["game_state_source"] = source
    row.setdefault("settlement_state", "open")
    row.setdefault("settlement_authority", "none")
    row.setdefault("settlement_observed_at", observed_at)
    row.setdefault("settlement_source", "dashboard_builder")
    return row


def reconcile_public_lifecycle(payload, prior_payload=None, live=None, schedule=None,
                               now=None, registry=None):
    """Apply the final publication gate and carry deployment-proven Top Picks.

    ``prior_payload`` is accepted only for call compatibility; it is never
    treated as publication proof. The durable registry is the sole proof that
    a wager actually reached a successful Pages deployment.
    """
    del prior_payload
    now = now or utc_now()
    schedule = schedule or {}
    live = live or {"schema_version": SCHEMA_VERSION,
                    "identity_schema_version": IDENTITY_SCHEMA_VERSION, "props": {}}
    registry = registry or load_registry(DEFAULT_REGISTRY_PATH)
    published = all_published_snapshots(registry)
    published_by_identity = {prop_identity_key(row): row for row in published}

    reconciled = []
    seen_identities = set()
    frozen_by_id = {}
    for source_row in payload.get("props") or []:
        row = dict(source_row)
        stable_prop_id(row)
        identity = prop_identity_key(row)
        if identity in seen_identities:
            raise ValueError(f"duplicate settlement identity during dashboard build: {identity!r}")
        registered = published_by_identity.get(identity)
        status = _status_for(schedule, row.get("game_pk"))
        state = game_state(status, row=row, now=now)
        before_cutoff = before_betting_cutoff(row, now)

        # A registry-backed row from a PRIOR slate no longer belongs on the
        # current board unless its game is verifiably still incomplete --
        # apply the same rule the carry-forward loop below applies to
        # registry entries not already present in payload["props"]. Without
        # this, a pick correctly carried forward once (e.g. while its game
        # was still live, before settlement was known) gets baked into a
        # future payload/props list and then stays stuck on every board
        # after it forever, because -- unlike the carry-forward loop -- this
        # loop had no age/staleness check of its own. Verified live
        # 2026-08-20: a graded Aug 18 Top Pick was still showing as today's
        # #1 Top Pick because of exactly this gap.
        #
        # Deliberately keyed on GAME state, not settlement state: once a
        # prior-slate pick drops off the current board, live.json's own
        # compact_live_state() correctly prunes its now-durably-recorded
        # settlement fact on the very next live-update cycle (it is no
        # longer in current_ids). A settlement-state check would then find
        # nothing, default to "open", and immediately readmit the pick --
        # reproduced live 2026-08-20 on this exact identity. Game state
        # doesn't have that failure mode: a still-incomplete game is never
        # eligible for that compaction (it can't hold an official_final
        # settlement yet), so "live"/"suspended"/"postponed" stays reliably
        # observable for as long as it's true, with no durable-archive
        # fallback needed.
        if registered is not None and registered.get("slate_date") != payload.get("date"):
            existing = apply_live_overlay({"props": [dict(row)]}, live)["props"][0]
            observed = state if state != "unknown" else (existing.get("game_state") or "unknown")
            if observed not in ("live", "suspended", "postponed"):
                continue

        # Unknown/non-pregame status and the scheduled start are independent
        # fail-closed gates. A local Top Pick is not proof of publication.
        if (state != "pregame" or not before_cutoff) and registered is None:
            continue

        if registered is not None and (state != "pregame" or not before_cutoff):
            # At the wagering boundary the immutable exposure snapshot wins;
            # later rescoring/repricing cannot mutate the bet users saw.
            frozen = dict(registered)
            if state == "unknown":
                state = frozen.get("game_state") or "unknown"
            row = frozen
        elif registered is not None:
            # Pregame display may legitimately reflect a later demotion or
            # quote, while the immutable first-exposure record remains intact.
            row.update(_publication_provenance(registered))
        if registered is not None:
            row["publication_snapshot"] = _publication_snapshot(registered)

        source = "mlb_schedule" if status else "mlb_status_unavailable"
        _with_base_lifecycle(row, state, now, source=source)
        if registered is not None and (state != "pregame" or not before_cutoff):
            frozen_by_id[stable_prop_id(row)] = dict(registered)
        reconciled.append(row)
        seen_identities.add(identity)

    # A full scoring pass deliberately excludes started games. Restore only
    # exact registry snapshots after their cutoff; never infer from archives,
    # names, or an old recommendation_status field.
    for registered in published:
        identity = prop_identity_key(registered)
        if identity in seen_identities:
            continue
        status = _status_for(schedule, registered.get("game_pk"))
        observed = game_state(status, row=registered, now=now)
        existing = apply_live_overlay({"props": [registered]}, live)["props"][0]
        if observed == "unknown":
            observed = existing.get("game_state") or "unknown"
        crossed = not before_betting_cutoff(registered, now) or observed != "pregame"
        if not crossed:
            # A demoted/withdrawn pregame recommendation remains in history,
            # but need not remain on the current wagering board before start.
            continue
        # Keyed on game state, not settlement state, for the same reason as
        # the identical check above: a prior-slate pick's settlement fact in
        # live.json is legitimately pruned by compact_live_state() once it
        # drops off the current board, and a settlement-based check would
        # then default to "open" and readmit it forever.
        if registered.get("slate_date") != payload.get("date") and observed not in (
                "live", "suspended", "postponed"):
            continue
        carried = dict(registered)
        carried["publication_snapshot"] = _publication_snapshot(registered)
        _with_base_lifecycle(
            carried, observed, now,
            source="mlb_schedule" if status else existing.get("game_state_source", "last_known_good"),
        )
        frozen_by_id[stable_prop_id(carried)] = dict(registered)
        reconciled.append(carried)
        seen_identities.add(identity)

    payload["schema_version"] = SCHEMA_VERSION
    payload["identity_schema_version"] = IDENTITY_SCHEMA_VERSION
    payload["props"] = reconciled
    payload = apply_live_overlay(payload, live)
    if frozen_by_id:
        # The immutable exposure snapshot is the only recommendation/price
        # truth after the wagering boundary. Reapply only independent game
        # and settlement facts from live state; all price/reclassification
        # fields remain frozen at first public exposure.
        for index, row in enumerate(payload["props"]):
            frozen = frozen_by_id.get(row.get("id"))
            if frozen is None:
                continue
            lifecycle = {
                field: row[field] for field in (GAME_FIELDS | SETTLEMENT_FIELDS)
                if field in row
            }
            payload["props"][index] = {
                **frozen,
                "publication_snapshot": _publication_snapshot(frozen),
                **lifecycle,
            }
    validate_payload_identities(payload)
    _recount_payload(payload)
    return payload



# PHASE 4 REBUILD (2026-08-16): the page itself -- HTML shell, CSS, JS -- is
# no longer a Python string template. It moved to dashboard/static/
# (index.html, app.css, app.js) as real, plain, editable files: no more
# {{ }} escaping every brace in a CSS/JS block just to survive
# str.format(), and critically, no more embedding data.json's entire
# contents a SECOND time inside index.html (measured before this change:
# docs/index.html was 4.7MB and docs/data.json was 4.4MB, nearly
# identical, because build_payload()'s output was serialized into BOTH --
# once inline via PAGE_TEMPLATE.format(payload_json=...) so the page had
# data on first paint, and again as the standalone file pollPrices()
# fetched). The shell now fetches data.json once itself; nothing is
# embedded twice. See dashboard/static/app.js's boot sequence.
#
# Fonts: the old page embedded ~148KB of base64-encoded Archivo/IBM Plex
# Sans/IBM Plex Mono directly in the HTML (base64 inflates binary ~33%, so
# that was ~110KB of real font data costing ~148KB on the wire, parsed
# before first paint, uncacheable separately from the page). Replaced with
# a deliberate system-font stack (see app.css) -- zero font requests, zero
# FOUT/FOIT, and a data-dense stats page benefits more from instant text
# than from a custom display face. dashboard/fonts_b64.json and the
# --fonts flag are retired along with PAGE_TEMPLATE.
STATIC_DIR = os.path.join(DASHBOARD_DIR, "static")
STATIC_FILES = ("index.html", "app.css", "app.js")


def copy_static_assets(out_dir):
    """Copies the hand-written shell/CSS/JS into the deploy directory
    unchanged -- these do not depend on today's data at all, so they are
    not regenerated per run. Browsers (and GitHub Pages' own CDN) can cache
    them across runs; only data.json/live.json change every cycle."""
    os.makedirs(out_dir, exist_ok=True)
    for name in STATIC_FILES:
        src = os.path.join(STATIC_DIR, name)
        dst = os.path.join(out_dir, name)
        with open(src, encoding="utf-8") as f:
            content = f.read()
        with open(dst, "w", encoding="utf-8") as f:
            f.write(content)
    return [os.path.join(out_dir, name) for name in STATIC_FILES]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=os.path.join(REPO_ROOT, "docs"),
                    help="deploy directory (default: docs/, GitHub Pages' own root)")
    ap.add_argument("--data-out", default=None,
                    help="also write the raw JSON payload here (default: data.json inside "
                         "--out-dir); frequent price/grade changes are written separately "
                         "to live.json, see dashboard/static/app.js's pollLive()")
    args = ap.parse_args()

    data_out = args.data_out or os.path.join(args.out_dir, "data.json")
    live_out = os.path.join(os.path.dirname(data_out) or ".", "live.json")

    prior_payload = None
    if os.path.exists(data_out):
        try:
            with open(data_out, encoding="utf-8") as handle:
                prior_payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"refusing to replace unreadable public board {data_out}: {exc}"
            ) from exc
    live = load_live_state(live_out)

    result = run_live_fetch()
    track_record = load_track_record()
    payload = build_payload(result, track_record=track_record)
    # Final pre-publication observation. Candidate generation's earlier
    # schedule response may be stale after a multi-minute scoring pass.
    final_schedule = _game_schedule(payload.get("date"))
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    payload = reconcile_public_lifecycle(
        payload,
        prior_payload=prior_payload,
        live=live,
        schedule=final_schedule,
        now=utc_now(),
        registry=registry,
    )

    written = copy_static_assets(args.out_dir)
    for path in written:
        print(f"Wrote {path} ({os.path.getsize(path)} bytes)")

    os.makedirs(os.path.dirname(os.path.abspath(data_out)) or ".", exist_ok=True)
    atomic_write_json(data_out, payload)

    summary = payload["summary"]
    print(f"Wrote {data_out} ({os.path.getsize(data_out)} bytes, {summary['n_props']} props, "
          f"{summary['n_top_pick']} top picks, {summary['n_lean']} leans, "
          f"{summary['n_value']} value)")

    # Ownership boundary: the full rebuild owns data.json; the consolidated
    # live updater owns live.json.  Never clear the overlay here -- it may
    # contain a newer terminal result or price than this build's checkout.
    print(f"Preserved {live_out}; full rebuild does not own live state.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
backtest/engine.py — point-in-time replay of the pick pipeline.

WHAT THIS DOES

For each date in a past range, reconstruct the inputs the pipeline WOULD have
had on the morning of that date, run the REAL scoring functions over them, and
grade the resulting candidates against what actually happened. One JSONL row
per candidate, shaped exactly as backtest/SCHEMA.md specifies, so
backtest/signals.py and backtest/calibration.py consume the output with no
adaptation.

Every candidate on the slate is emitted, not just the top 10. The top 10 is a
ranking decision; signal fitting needs the whole distribution, including the
picks the ranker rejected.

    python backtest/engine.py --start 2026-06-01 --end 2026-06-30 \
                              --out backtest/rows.jsonl
    python backtest/engine.py --verify 2026-06-15     # the leakage proof


THE ONLY THING THAT MATTERS: NO LOOKAHEAD
─────────────────────────────────────────

A season-to-date stat pulled today contains the game you are predicting. Score
2026-06-15 with a season line fetched now and the model already knows the
answer; the hit rate comes back spectacular and means nothing. So this module
does not *try* to avoid lookahead by being careful at each call site. It makes
lookahead structurally impossible and then proves it:

1.  ONE GUARDED DATA SOURCE. Every Statcast read during a simulated date is
    routed through a wrapper that slices a single pre-loaded season frame and
    RAISES if the requested end date is not strictly before the simulated
    date. The real fetchers in generate_picks.py (fetch_l7_batter_form,
    fetch_l14_pitcher_form, fetch_first_inning_form, fetch_bat_speed_trends)
    run unmodified against that wrapper — they read mlb_daily's L7_/L14_
    window globals, which are repointed to windows ending the day before.

2.  THE GUARD CANNOT BE SWALLOWED. LookaheadError derives from BaseException,
    not Exception, precisely because every fetcher in this codebase wraps its
    body in `except Exception`. A leak that degraded into a warning and a
    neutral default is the failure this whole module exists to prevent, so the
    guard is deliberately outside the reach of those handlers.

3.  SEASON LEADERBOARDS ARE POISONED, NOT AVOIDED. pybaseball's season
    leaderboard endpoints (expected stats, exit velo, pitch arsenals, sprint
    speed, pop time) have no as-of parameter — they always mean "now". Rather
    than trust that nothing calls them, they are replaced with functions that
    raise. An accidental call surfaces as an error, not as silent leakage.

4.  SEASON LINES COME FROM A DATE-BOUNDED ENDPOINT. MLB's own
    /api/v1/stats?stats=byDateRange serves an arbitrary window, so season-to-
    date batting and pitching are recomputed with endDate = D-1 rather than
    taken from a "season" query. Rate stats Statcast alone carries (Barrel%,
    HardHit%, xBA, xwOBA, CSW%) are rebuilt from the same bounded frame.

5.  EVERY READ IS LOGGED AND ASSERTED. The wrapper records (start, end,
    max game_date actually returned) for every access. `--verify` replays a
    date and asserts that no input frame contains a row dated on or after it —
    with a POSITIVE CONTROL showing the unrestricted store does contain such
    rows, so the assertion is proving a filter works rather than proving a
    frame is empty.

The single input keyed to D itself is the slate: which games are played, the
posted batting orders, the probable starters, the assigned umpire crew. Those
are published before first pitch and are what a real morning run would have
had. They are not lookahead, and `--verify` excludes them by design.


WHAT COULD NOT BE RECONSTRUCTED POINT-IN-TIME (stated, not silently faked)
─────────────────────────────────────────────────────────────────────────

Each of these is passed to the scorers as MISSING, so the scorer's own
degrade-to-neutral path handles it, and the corresponding signal is simply
absent from the emitted rows:

  * sprint speed / catcher pop time — Savant serves these as season-final
    leaderboards with no date window, and neither is derivable from
    pitch-by-pitch data. score_stolen_base() returns None without a sprint
    speed, so STOLEN BASE PROPS ARE ABSENT FROM THIS BACKTEST ENTIRELY. That
    prop type is unvalidated here, and cannot be validated by this engine.
  * umpire accuracy/consistency — UmpScorecards publishes season-to-date
    aggregates only. Dropped, so `ump_accuracy` never fires. This costs the
    walks prop its context component and the strikeouts prop its context
    component; both fall back to production's own neutral 50.
  * wRC+ and Stuff+ — FanGraphs-only composites with no date-windowed source.
    Dropped, so `wrc_plus` and `stuff_plus` never fire. wRC+ is 40% of the
    batter BASELINE SKILL component, which is the single biggest known gap
    between this replay and a live run.
  * all market signals — betting lines, sharp/public splits, implied team
    totals. Out of scope per SCHEMA.md: line history only began being captured
    2026-08-05 and cannot be reconstructed backwards. `implied_total` is
    therefore always absent, so the ENVIRONMENT component is park/weather only
    and projected PA uses the league-average run environment.
  * empirical prop hit rates — generate_picks.py blends a measured
    game-log rate into each probability. Rebuilding those per-date is possible
    in principle but the game-log endpoint is season-scoped, so `predicted_prob`
    here is the purely MODELLED probability. That is also what SCHEMA.md asks
    for (uncalibrated model output), but it is not identical to what the live
    board ships.

ONE RESIDUAL LEAK THAT CANNOT BE REMOVED, ONLY DISCLOSED
────────────────────────────────────────────────────────

generate_picks.py carries hardcoded league constants that were MEASURED on
2026 data — LEAGUE_YRFI_RATE, LEAGUE_AVG_TB_PA, AB_PER_PA,
LEAGUE_AVG_BF_PER_START, BF_SHRINK_N0, LEAGUE_TEAM_RUNS_MEAN/VAR, the wOBA
percentile band — and the measurement windows overlap the dates being
replayed. Backtesting the real scoring code means backtesting those constants,
and rolling them back per-date would mean forking every scorer, which defeats
the point of reusing them.

The exposure is small and worth naming precisely rather than waving at: these
are single league-wide scalars, not player-specific or game-specific values.
Knowing that the league scores in the first inning 29.4% of the season cannot
tell the model whether THIS starter is scored on tonight. It shifts a prior,
not a prediction. But it is not zero, and a hit rate should not be read to the
last decimal place because of it.

And one approximation that is used rather than dropped, flagged because it is
a genuine departure:

  * weather is the Open-Meteo ARCHIVE (what was observed) rather than the
    forecast that would have been available hours before first pitch. Observed
    conditions are a mild optimism about forecast accuracy, not knowledge of
    the game result: the park HR index depends on temperature, wind and air
    density, none of which is a function of what the batters did. Set
    --no-weather to drop it entirely and score every park neutral.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import date as _date, datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mlb_daily as m           # noqa: E402
import generate_picks as gp     # noqa: E402
import grade_results as gr      # noqa: E402
import mlb_sources as msrc      # noqa: E402

CACHE_DIR = os.environ.get("BACKTEST_CACHE", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache"))
os.makedirs(CACHE_DIR, exist_ok=True)

# Regular season effectively starts here; a couple of weeks of slack costs
# nothing (the endpoints just return no rows) and covers an early opener.
SEASON_START_MMDD = "-03-01"

# Politeness between per-date passes. statsapi.mlb.com and Baseball Savant are
# free public services with no published rate limit and no API key; the
# courteous read of that is "do not hammer them", not "there is no limit".
DEFAULT_SLEEP = 1.0


class LookaheadError(BaseException):
    """Raised when something asks for data at or past the simulated date.

    Derives from BaseException ON PURPOSE. Every fetcher in this codebase is
    written as `try: ...pull... except Exception: warn(); return {}` — which is
    correct for a flaky network and catastrophic for a leak guard, because a
    swallowed leak becomes a neutral default and a backtest that silently used
    the future. Sitting outside `except Exception` is the whole point.
    """


# ══════════════════════════════════════════════════════════════════════════
#  DATES
# ══════════════════════════════════════════════════════════════════════════

def dparse(s) -> _date:
    if isinstance(s, _date):
        return s
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def dstr(d) -> str:
    return dparse(d).strftime("%Y-%m-%d")


def shift(d, days: int) -> str:
    return dstr(dparse(d) + timedelta(days=days))


def date_range(start, end):
    a, b = dparse(start), dparse(end)
    if b < a:
        raise ValueError(f"end {end} precedes start {start}")
    out, cur = [], a
    while cur <= b:
        out.append(dstr(cur))
        cur += timedelta(days=1)
    return out


# ══════════════════════════════════════════════════════════════════════════
#  THE SEASON STATCAST STORE — one pull, sliced per date
# ══════════════════════════════════════════════════════════════════════════
#
# Pulled once for the whole span and cached to parquet, then SLICED for every
# simulated date. This is not only cheaper than re-pulling per date; it is what
# makes the no-lookahead guarantee checkable, because there is exactly one
# place where a date filter is applied and exactly one place to assert on.
#
# Columns are pruned on ingest. The full Savant frame is 119 columns and a
# season is ~500K pitches; keeping only what the scorers actually read holds
# this to a few hundred MB. The set below is the union of every column touched
# by generate_picks.fetch_l7_batter_form / fetch_l14_pitcher_form /
# fetch_first_inning_form / fetch_bat_speed_trends and by the season and
# arsenal rebuilds in this file — verified by running them against the pruned
# frame, not by reading.

STATCAST_COLUMNS = [
    "game_date", "game_pk", "batter", "pitcher", "events", "description", "type",
    "pitch_type", "launch_speed", "launch_angle", "launch_speed_angle",
    "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
    "woba_value", "woba_denom", "delta_run_exp", "bat_speed",
    "at_bat_number", "inning", "inning_topbot", "bat_score", "post_bat_score",
    "home_team", "away_team", "stand", "p_throws", "balls", "strikes",
    # hc_x/hc_y (batted-ball coordinates): added when pull_rates() was wired
    # into the backtest extras. Without them mlb_sources.pull_rates() always
    # degrades to {} on this store -- confirmed live via --verify, which
    # warned "Pull%: Statcast is missing batted-ball coordinate columns" the
    # first time pull was wired in, before this line was added. Any cached
    # parquet built before this line was added still lacks them and must be
    # regenerated (delete it; StatcastStore.load() re-pulls automatically).
    "hc_x", "hc_y",
    # fielder_2/zone: added when catcher_framing() was wired into the
    # backtest extras (missed in the same pass that added hc_x/hc_y above).
    # Without them mlb_sources.catcher_framing() always degrades to {} on
    # this store, same failure mode as pull_rates() without hc_x/hc_y.
    "fielder_2", "zone",
]

HIT_EVENTS = ("single", "double", "triple", "home_run")
# Events that do not consume an at-bat (they are plate appearances but not ABs)
NON_AB_EVENTS = {
    "walk", "intent_walk", "hit_by_pitch", "sac_fly", "sac_bunt",
    "sac_fly_double_play", "sac_bunt_double_play", "catcher_interf",
}
WHIFF_DESCRIPTIONS = {"swinging_strike", "swinging_strike_blocked"}
SWING_DESCRIPTIONS = WHIFF_DESCRIPTIONS | {
    "foul", "foul_tip", "hit_into_play", "foul_bunt", "missed_bunt", "bunt_foul_tip",
}


class StatcastStore:
    """Leaguewide pitch-by-pitch for one season, pruned and cached on disk."""

    def __init__(self, year: int, through: str, cache_dir: str = CACHE_DIR, verbose: bool = True):
        self.year = int(year)
        self.through = dstr(through)
        self.cache_dir = cache_dir
        self.verbose = verbose
        self._df = None

    @property
    def path(self) -> str:
        return os.path.join(self.cache_dir, f"statcast_{self.year}_through_{self.through}.parquet")

    def _log(self, msg):
        if self.verbose:
            print(f"  [statcast] {msg}", flush=True)

    def load(self) -> pd.DataFrame:
        if self._df is not None:
            return self._df
        # Reuse any cached pull that already covers this span. A wider cache is
        # fine -- the per-date slice bounds it anyway -- and re-downloading a
        # season to add three days would be gratuitous load on a free service.
        best = None
        for fn in sorted(os.listdir(self.cache_dir)):
            if not fn.startswith(f"statcast_{self.year}_through_") or not fn.endswith(".parquet"):
                continue
            covered = fn[len(f"statcast_{self.year}_through_"):-len(".parquet")]
            try:
                if dparse(covered) >= dparse(self.through):
                    if best is None or dparse(covered) < dparse(best[1]):
                        best = (os.path.join(self.cache_dir, fn), covered)
            except ValueError:
                continue
        if best:
            self._log(f"cache hit: {os.path.basename(best[0])}")
            self._df = pd.read_parquet(best[0])
            return self._df

        start = f"{self.year}{SEASON_START_MMDD}"
        self._log(f"pulling {start} .. {self.through} (one time, then cached)")
        frames = []
        cur = dparse(start)
        end = dparse(self.through)
        while cur <= end:
            chunk_end = min(end, cur + timedelta(days=13))
            try:
                raw = m.pyb.statcast(start_dt=dstr(cur), end_dt=dstr(chunk_end), verbose=False)
            except TypeError:            # older pybaseball without verbose=
                raw = m.pyb.statcast(start_dt=dstr(cur), end_dt=dstr(chunk_end))
            if raw is not None and not raw.empty:
                keep = [c for c in STATCAST_COLUMNS if c in raw.columns]
                frames.append(raw[keep].copy())
                self._log(f"  {dstr(cur)}..{dstr(chunk_end)}: {len(raw)} pitches")
            cur = chunk_end + timedelta(days=1)
            time.sleep(0.3)
        if not frames:
            raise RuntimeError(f"no Statcast data for {self.year} through {self.through}")
        df = pd.concat(frames, ignore_index=True)
        df["game_date"] = pd.to_datetime(df["game_date"]).dt.strftime("%Y-%m-%d")
        df.to_parquet(self.path, index=False)
        self._log(f"stored {len(df)} pitches -> {os.path.basename(self.path)}")
        self._df = df
        return df

    def window(self, start_dt=None, end_dt=None, pitcher=None, batter=None) -> pd.DataFrame:
        """Inclusive [start_dt, end_dt] slice. No date guard here on purpose —
        the guard lives in the patched entry points, so this stays usable for
        the positive control in verify_no_lookahead()."""
        df = self.load()
        mask = pd.Series(True, index=df.index)
        if start_dt is not None:
            mask &= df["game_date"] >= dstr(start_dt)
        if end_dt is not None:
            mask &= df["game_date"] <= dstr(end_dt)
        if pitcher is not None:
            mask &= df["pitcher"] == int(pitcher)
        if batter is not None:
            mask &= df["batter"] == int(batter)
        return df[mask].copy()


# ══════════════════════════════════════════════════════════════════════════
#  THE GUARD — patched data entry points for one simulated date
# ══════════════════════════════════════════════════════════════════════════

class AccessLog:
    """Every point-in-time data read taken while simulating one date.

    Kept so `--verify` can assert on what was ACTUALLY fetched rather than on
    what the code appears to fetch. The two diverge the moment someone adds a
    call site, which is exactly when a leak gets introduced.
    """

    def __init__(self, asof_date: str):
        self.asof = dstr(asof_date)
        self.reads = []      # (source, start, end, n_rows, max_game_date)

    def record(self, source, start, end, df=None, max_date=None):
        n = None if df is None else len(df)
        if max_date is None and df is not None and len(df) and "game_date" in df.columns:
            max_date = str(df["game_date"].max())[:10]
        self.reads.append({"source": source, "start": start, "end": end,
                           "rows": n, "max_game_date": max_date})
        return df

    def violations(self):
        """Reads whose requested end, or whose returned data, touches the
        simulated date or later."""
        bad = []
        for r in self.reads:
            if r["end"] and dstr(r["end"]) >= self.asof:
                bad.append((r, f"requested end_dt {r['end']} is not before {self.asof}"))
            elif r["max_game_date"] and r["max_game_date"] >= self.asof:
                bad.append((r, f"returned a row dated {r['max_game_date']}, not before {self.asof}"))
        return bad


_POISONED_LEADERBOARDS = [
    "statcast_batter_expected_stats", "statcast_pitcher_expected_stats",
    "statcast_batter_exitvelo_barrels", "statcast_pitcher_exitvelo_barrels",
    "statcast_batter_pitch_arsenal", "statcast_pitcher_pitch_arsenal",
    "statcast_sprint_speed", "statcast_catcher_poptime",
    "batting_stats", "pitching_stats", "fg_batting_data", "fg_pitching_data",
    "team_batting", "team_pitching", "fg_team_batting_data",
    "batting_stats_range", "pitching_stats_range",
]


class PointInTime:
    """Context manager that makes a simulated date the edge of the world.

    On entry: repoints mlb_daily's date globals at windows ending the day
    before D, swaps pybaseball's Statcast readers for guarded slices of the
    season store, and replaces every season leaderboard with a function that
    raises. On exit: restores all of it, so importing this module never changes
    how the live pipeline behaves.
    """

    def __init__(self, asof_date: str, store: StatcastStore):
        self.date = dstr(asof_date)
        self.cutoff = shift(self.date, -1)     # last day whose data may be used
        self.store = store
        self.log = AccessLog(self.date)
        self._saved = {}

    # ---- guarded replacements -------------------------------------------
    def _guard(self, source, end_dt):
        if end_dt is None:
            raise LookaheadError(f"{source}: unbounded request while simulating {self.date}")
        if dstr(end_dt) >= self.date:
            raise LookaheadError(
                f"{source}: asked for data through {dstr(end_dt)} while simulating "
                f"{self.date} — that window contains the games being predicted")

    def _statcast(self, start_dt=None, end_dt=None, **kw):
        self._guard("statcast", end_dt)
        df = self.store.window(start_dt, end_dt)
        return self.log.record("statcast", start_dt, end_dt, df)

    def _statcast_pitcher(self, start_dt=None, end_dt=None, player_id=None, **kw):
        self._guard("statcast_pitcher", end_dt)
        df = self.store.window(start_dt, end_dt, pitcher=player_id)
        return self.log.record(f"statcast_pitcher[{player_id}]", start_dt, end_dt, df)

    def _statcast_batter(self, start_dt=None, end_dt=None, player_id=None, **kw):
        self._guard("statcast_batter", end_dt)
        df = self.store.window(start_dt, end_dt, batter=player_id)
        return self.log.record(f"statcast_batter[{player_id}]", start_dt, end_dt, df)

    def _poison(self, name):
        def _raise(*a, **kw):
            raise LookaheadError(
                f"pybaseball.{name} is a season-to-date leaderboard with no date "
                f"window — calling it while simulating {self.date} would read "
                f"stats that include the games being predicted")
        return _raise

    # ---- lifecycle -------------------------------------------------------
    def __enter__(self):
        pyb = m.pyb
        for attr, repl in (("statcast", self._statcast),
                           ("statcast_pitcher", self._statcast_pitcher),
                           ("statcast_batter", self._statcast_batter)):
            if hasattr(pyb, attr):
                self._saved[("pyb", attr)] = getattr(pyb, attr)
                setattr(pyb, attr, repl)
        for name in _POISONED_LEADERBOARDS:
            if hasattr(pyb, name):
                self._saved[("pyb", name)] = getattr(pyb, name)
                setattr(pyb, name, self._poison(name))

        # mlb_daily's module-level date globals. Every rolling-window fetcher
        # in generate_picks.py reads these at call time, so repointing them is
        # what makes those functions point-in-time without touching them.
        # Windows END THE DAY BEFORE D: an L7 window ending on D would include
        # the games being predicted.
        for key, val in (("TODAY", self.cutoff),
                         ("YESTERDAY", shift(self.date, -2)),
                         ("YEAR", dparse(self.date).year),
                         ("L7_END", self.cutoff), ("L7_START", shift(self.date, -8)),
                         ("L14_END", self.cutoff), ("L14_START", shift(self.date, -15)),
                         ("L30_END", self.cutoff), ("L30_START", shift(self.date, -31))):
            self._saved[("m", key)] = getattr(m, key, None)
            setattr(m, key, val)
        # A season-wide Statcast cache populated by a live run would be a leak
        # straight past every guard above.
        self._saved[("m", "_SEASON_STATCAST_CACHE")] = getattr(m, "_SEASON_STATCAST_CACHE", None)
        m._SEASON_STATCAST_CACHE = None
        # mlb_sources.league_base_rates() has the exact same hazard and was
        # missed the first time this class was written: it caches on its own
        # module-level dict and, unlike _SEASON_STATCAST_CACHE above, nothing
        # was clearing it. Left alone, the FIRST simulated date's league rate
        # would silently freeze for every later date in the same run --
        # not a forward leak (its own read is properly cutoff-guarded via the
        # swapped pyb.statcast above), but a staleness bug in the other
        # direction: date 5 would see date 1's league rate, not its own.
        self._saved[("msrc", "_LEAGUE_RATES_CACHE")] = dict(msrc._LEAGUE_RATES_CACHE)
        msrc._LEAGUE_RATES_CACHE.clear()
        return self

    def __exit__(self, exc_type, exc, tb):
        for (mod, attr), val in self._saved.items():
            if mod == "pyb":
                target = m.pyb
            elif mod == "msrc":
                target = msrc
            else:
                target = m
            if mod == "msrc":
                getattr(target, attr).clear()
                getattr(target, attr).update(val)
            else:
                setattr(target, attr, val)
        self._saved.clear()
        return False


# ══════════════════════════════════════════════════════════════════════════
#  SEASON-TO-DATE TABLES, REBUILT AS OF A CUTOFF
# ══════════════════════════════════════════════════════════════════════════

def _f(v):
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def statcast_batter_rates(df: pd.DataFrame) -> dict:
    """Barrel%, HardHit%, xBA, xwOBA, wOBA per batter from bounded pitch data.

    These five live only on Savant's season leaderboards in the live pipeline,
    and those leaderboards have no date window. Rebuilt here from the same
    definitions Savant publishes:

      Barrel%   launch_speed_angle == 6, over batted-ball events
      HardHit%  launch_speed >= 95 mph, over batted-ball events
      wOBA      sum(woba_value) / sum(woba_denom) — Savant's own per-PA fields
      xwOBA     same denominator, with batted balls replaced by their
                estimated_woba_using_speedangle and non-contact events keeping
                their actual value (a walk is worth its walk weight either way)
      xBA       estimated_ba_using_speedangle over at-bats, strikeouts as 0

    NOT reconstructed: wRC+ (needs league/park factors FanGraphs does not
    expose per-date) — see this module's docstring.
    """
    out = {}
    if df is None or df.empty:
        return out
    pa = df[df["events"].notna()].copy()
    if pa.empty:
        return out
    pa["_is_bbe"] = pa["launch_speed"].notna()
    pa["_barrel"] = (pa["launch_speed_angle"] == 6)
    pa["_hard"] = (pa["launch_speed"] >= 95)
    pa["_ab"] = ~pa["events"].isin(NON_AB_EVENTS)
    # xBA numerator: expected BA on contact, zero on a strikeout, and nothing
    # at all on a PA that never consumed an at-bat.
    pa["_xba_num"] = pa["estimated_ba_using_speedangle"].fillna(0.0).where(pa["_ab"], 0.0)
    pa["_xwoba_num"] = pa["estimated_woba_using_speedangle"]
    pa["_xwoba_num"] = pa["_xwoba_num"].fillna(pa["woba_value"])

    for bid, g in pa.groupby("batter"):
        bbe = int(g["_is_bbe"].sum())
        ab = int(g["_ab"].sum())
        denom = _f(g["woba_denom"].sum()) or 0.0
        row = {}
        if bbe >= 10:
            row["Barrel%"] = round(float(g["_barrel"].sum()) / bbe * 100, 1)
            row["HardHit%"] = round(float(g["_hard"].sum()) / bbe * 100, 1)
        if ab >= 20:
            row["xBA"] = round(float(g["_xba_num"].sum()) / ab, 3)
        if denom >= 20:
            row["wOBA"] = round(float(g["woba_value"].sum()) / denom, 3)
            row["xwOBA"] = round(float(g["_xwoba_num"].sum()) / denom, 3)
        if row:
            out[int(bid)] = row
    return out


def statcast_pitcher_rates(df: pd.DataFrame) -> dict:
    """CSW% per pitcher from bounded pitch data.

    CSW = called strikes + whiffs, over total pitches. The one FanGraphs-only
    pitcher input with an exact pitch-level definition, so it survives the
    rebuild where Stuff+ (a proprietary model) does not.
    """
    out = {}
    if df is None or df.empty:
        return out
    d = df[df["description"].notna()]
    if d.empty:
        return out
    called = d["description"].eq("called_strike")
    whiff = d["description"].isin(WHIFF_DESCRIPTIONS)
    tmp = pd.DataFrame({"pitcher": d["pitcher"], "csw": (called | whiff).astype(int)})
    g = tmp.groupby("pitcher")["csw"].agg(["sum", "count"])
    for pid, r in g.iterrows():
        if r["count"] >= 200:
            out[int(pid)] = {"CSW%": round(float(r["sum"]) / float(r["count"]) * 100, 1)}
    return out


def arsenals_asof(df: pd.DataFrame, min_batter_pitches=40, min_pitcher_pitches=100,
                  min_usage_pct=15.0):
    """Point-in-time rebuild of fetch_pitch_type_exploits()'s two lookups.

    Savant's arsenal leaderboards are season-final; these are the same
    quantities computed from bounded pitch-by-pitch, so find_pitch_type_exploit
    (the real one, imported) works against them unchanged.

    run_value_per_100 sign was verified numerically against the data rather
    than assumed from the column name: on real 2026 rows, home runs average
    delta_run_exp +1.50 and strikeouts -0.22, i.e. delta_run_exp is already
    from the BATTER's perspective, matching Savant's batter run-value
    convention (positive = good for the hitter). No sign flip is applied.
    """
    batter_arsenal = defaultdict(dict)
    pitcher_arsenal = defaultdict(list)
    if df is None or df.empty:
        return dict(batter_arsenal), dict(pitcher_arsenal)

    d = df[df["pitch_type"].notna() & (df["pitch_type"] != "")].copy()
    if d.empty:
        return dict(batter_arsenal), dict(pitcher_arsenal)

    d["_rv"] = d["delta_run_exp"].fillna(0.0)
    d["_swing"] = d["description"].isin(SWING_DESCRIPTIONS)
    d["_whiff"] = d["description"].isin(WHIFF_DESCRIPTIONS)
    d["_hard"] = d["launch_speed"] >= 95
    d["_bbe"] = d["launch_speed"].notna()

    grp = d.groupby(["batter", "pitch_type"])
    agg = grp.agg(n=("_rv", "size"), rv=("_rv", "sum"),
                  swings=("_swing", "sum"), whiffs=("_whiff", "sum"),
                  bbe=("_bbe", "sum"), hard=("_hard", "sum"))
    agg = agg[agg["n"] >= min_batter_pitches]
    for (bid, ptype), r in agg.iterrows():
        n = float(r["n"])
        batter_arsenal[int(bid)][str(ptype)] = {
            "run_value_per_100": round(float(r["rv"]) / n * 100, 2),
            "hard_hit_percent": (round(float(r["hard"]) / float(r["bbe"]) * 100, 1)
                                 if r["bbe"] >= 5 else None),
            "whiff_percent": (round(float(r["whiffs"]) / float(r["swings"]) * 100, 1)
                              if r["swings"] >= 10 else None),
        }

    counts = d.groupby(["pitcher", "pitch_type"]).size().rename("n").reset_index()
    totals = counts.groupby("pitcher")["n"].sum()
    for pid, sub in counts.groupby("pitcher"):
        total = float(totals.loc[pid])
        if total < min_pitcher_pitches:
            continue
        for _, r in sub.iterrows():
            usage = float(r["n"]) / total * 100
            if usage >= min_usage_pct:
                pitcher_arsenal[int(pid)].append((str(r["pitch_type"]).upper(), round(usage, 1)))
    return dict(batter_arsenal), dict(pitcher_arsenal)


def season_tables_asof(cutoff: str, year: int, sc_window: pd.DataFrame, log: AccessLog = None):
    """Season-to-date batting/pitching/team tables as of end-of-day `cutoff`.

    Two sources, joined:
      * MLB's own stats?stats=byDateRange (via mlb_sources.fetch_player_stats,
        which now takes a window) for the counting and traditional rate stats.
        This is authoritative and genuinely date-bounded.
      * The bounded Statcast frame for the batted-ball and plate-discipline
        rates MLB's endpoint does not carry.

    Returns (batter_by_name, pitcher_by_name, team_k_pct, pitcher_df).
    """
    season_start = f"{year}{SEASON_START_MMDD}"
    if log is not None:
        log.record("statsapi.byDateRange/hitting", season_start, cutoff, max_date=cutoff)
        log.record("statsapi.byDateRange/pitching", season_start, cutoff, max_date=cutoff)
    hit_rows = msrc.fetch_player_stats("hitting", limit=2000,
                                       start_date=season_start, end_date=cutoff)
    pit_rows = msrc.fetch_player_stats("pitching", limit=2000,
                                       start_date=season_start, end_date=cutoff)

    sc_bat = statcast_batter_rates(sc_window)
    sc_pit = statcast_pitcher_rates(sc_window)

    batters = {}
    team_k = defaultdict(lambda: [0, 0])   # team name -> [strikeouts, PA]
    for r in hit_rows:
        st = r.get("stat") or {}
        player = r.get("player") or {}
        pid, name = player.get("id"), player.get("fullName")
        pa = _i(st.get("plateAppearances"))
        if not pid or not name or pa <= 0:
            continue
        team_name = ((r.get("team") or {}).get("name"))
        if team_name:
            team_k[team_name][0] += _i(st.get("strikeOuts"))
            team_k[team_name][1] += pa
        avg, obp, slg = _f(st.get("avg")), _f(st.get("obp")), _f(st.get("slg"))
        row = {
            "player_id": int(pid), "Name": name, "pa": pa,
            "AVG": avg, "OBP": obp, "slg": slg,
            "ISO": (round(slg - avg, 3) if (slg is not None and avg is not None) else None),
            "K%": round(_i(st.get("strikeOuts")) / pa * 100, 1),
            "BB%": round(_i(st.get("baseOnBalls")) / pa * 100, 1),
            "SB": _i(st.get("stolenBases")),
            # Not reconstructable point-in-time; left absent so scale() treats
            # it as neutral and signals.py sees the signal as not fired.
            "wRC+": None,
        }
        row.update(sc_bat.get(int(pid), {}))
        # ACTUAL minus EXPECTED, matching the sign score_batter's regression
        # adjustment was verified against (positive = outperforming contact
        # quality = fade candidate).
        if row.get("AVG") is not None and row.get("xBA") is not None:
            row["est_ba_minus_ba_diff"] = round(row["AVG"] - row["xBA"], 4)
        if row.get("wOBA") is not None and row.get("xwOBA") is not None:
            row["est_woba_minus_woba_diff"] = round(row["wOBA"] - row["xwOBA"], 4)
        batters[name] = row

    pitchers = {}
    pit_records = []
    for r in pit_rows:
        st = r.get("stat") or {}
        player = r.get("player") or {}
        pid, name = player.get("id"), player.get("fullName")
        bf = _i(st.get("battersFaced"))
        if not pid or not name or bf <= 0:
            continue
        ip = _f(st.get("inningsPitched")) or 0.0
        row = {
            "player_id": int(pid), "Name": name,
            "ERA": _f(st.get("era")),
            "K%": round(_i(st.get("strikeOuts")) / bf * 100, 1),
            "BB%": round(_i(st.get("baseOnBalls")) / bf * 100, 1),
            "IP": ip, "G": _i(st.get("gamesPlayed")), "GS": _i(st.get("gamesStarted")),
            "BF": bf,
            # Proprietary FanGraphs model, no date-windowed source.
            "Stuff+": None,
        }
        row.update(sc_pit.get(int(pid), {}))
        pitchers[name] = row
        pit_records.append({**row, "Team": _team_abbr((r.get("team") or {}).get("name"))})

    team_k_pct = {t: round(k / pa * 100, 1) for t, (k, pa) in team_k.items() if pa >= 200}
    pit_df = pd.DataFrame(pit_records) if pit_records else pd.DataFrame()
    return batters, pitchers, team_k_pct, pit_df


_ABBR_BY_NAME = None

def _team_abbr(team_name):
    """Official abbreviation for a full team name. compute_bullpen_era() keys
    its output by team NAME but reads a `Team` column of abbreviations, so the
    round trip has to go through the same table it uses."""
    global _ABBR_BY_NAME
    if _ABBR_BY_NAME is None:
        _ABBR_BY_NAME = {t["name"]: t["abbr"] for t in m.get_team_ids()}
    return _ABBR_BY_NAME.get(team_name)


# ══════════════════════════════════════════════════════════════════════════
#  WEATHER — historical observations, not a forecast
# ══════════════════════════════════════════════════════════════════════════

def park_weather_asof(game_meta, date, sleep=0.2):
    """Park HR index per matchup from Open-Meteo's ARCHIVE endpoint.

    The forecast endpoint the live path uses only serves the future, so a
    replay cannot have the forecast that existed hours before first pitch.
    Archive observations are used instead and the difference is stated rather
    than hidden: this is a mild optimism about forecast accuracy, not
    knowledge of the outcome — temperature, wind and air density are not
    functions of what the batters did. `--no-weather` drops it entirely.

    Scoring itself is generate_picks.park_hr_index(), the same function the
    live path calls, so the index means the same thing on both sides.
    """
    out, seen = {}, {}
    for gmeta in game_meta:
        venue = gmeta["venue"]
        sk = None
        for k in m.STADIUMS:
            if k.lower() in venue.lower() or venue.lower() in k.lower():
                sk = k
                break
        if not sk:
            continue
        if sk in seen:
            out[gmeta["matchup"]] = seen[sk]
            continue
        (lat, lon, dome, team, cf_deg, elev, lf, cf_d, rf,
         lfw, cfw, rfw, foul, surf, humidor, eye, retract) = m.STADIUMS[sk]
        if dome:
            entry = {"dome": True, "park_hr_index": 50, "wind_effect": "dome", "temp": None}
            seen[sk] = entry
            out[gmeta["matchup"]] = entry
            continue
        try:
            r = m.retry_get("https://archive-api.open-meteo.com/v1/archive", params={
                "latitude": lat, "longitude": lon, "start_date": dstr(date), "end_date": dstr(date),
                "hourly": "temperature_2m,windspeed_10m,winddirection_10m,relativehumidity_2m,precipitation",
                "temperature_unit": "fahrenheit", "windspeed_unit": "mph", "timezone": "auto",
            }, timeout=25, retries=2)
            r.raise_for_status()
            h = r.json()["hourly"]
            idx = min(max(gmeta["hour"], 0), 23)
            temp = h["temperature_2m"][idx]
            wsp = h["windspeed_10m"][idx]
            wdir = h["winddirection_10m"][idx]
            humid = h["relativehumidity_2m"][idx]
            if temp is None or wsp is None or wdir is None:
                raise ValueError("archive returned nulls for that hour")
            score, wind_effect = gp.park_hr_index(temp, wsp, wdir, humid or 50, cf_deg, elev, dome)
            entry = {"dome": False, "park_hr_index": score, "wind_effect": wind_effect,
                     "temp": temp, "wind_mph": wsp, "wx_disagreement": None,
                     "precip_prob": None, "source": "open-meteo archive (observed)"}
        except Exception as e:
            m.warn(f"Backtest weather {sk} {dstr(date)}: {e}")
            entry = {"dome": False, "park_hr_index": 50, "wind_effect": "unknown", "temp": None}
        seen[sk] = entry
        out[gmeta["matchup"]] = entry
        time.sleep(sleep)
    return out


# ══════════════════════════════════════════════════════════════════════════
#  ONE SIMULATED DATE
# ══════════════════════════════════════════════════════════════════════════

class DateResult:
    def __init__(self, date):
        self.date = date
        self.rows = []
        self.status = "ok"
        self.reason = None
        self.n_games = 0
        self.n_candidates = 0
        self.n_ungraded = 0
        self.ungraded_reasons = defaultdict(int)
        self.log = None


def build_inputs(date, store, use_weather=True, use_bullpen=True, verbose=True):
    """Assemble every scoring input for `date` using only pre-`date` data.

    Returns (game_meta, kwargs_for_build_candidates, comp_table, pit_df, access_log).
    Must be called inside a PointInTime context.
    """
    cutoff = shift(date, -1)
    year = dparse(date).year

    # The slate itself. This is the ONE call keyed to D rather than D-1, and
    # deliberately so: who is playing, the posted batting order, the probable
    # starters and the umpire crew are all published before first pitch. This
    # is the information a real morning run had, not lookahead.
    _text, game_meta, _pids = m.fetch_lineups(date)
    if not game_meta:
        return None, None, None, None, None

    sc_window = store.window(f"{year}{SEASON_START_MMDD}", cutoff)
    log = AccessLog(date)
    log.record("season-to-date statcast slice", f"{year}{SEASON_START_MMDD}", cutoff, sc_window)

    batter_lookup, pitcher_lookup, team_k, pit_df = season_tables_asof(
        cutoff, year, sc_window, log=log)
    batter_arsenal, pitcher_arsenal = arsenals_asof(sc_window)

    # Rolling-window form: the REAL fetchers, reading the repointed L7/L14
    # globals and hitting the guarded statcast wrapper.
    l7_form = gp.fetch_l7_batter_form()
    bat_speed_trend = gp.fetch_bat_speed_trends()
    starter_ids = {}
    for gmeta in game_meta:
        if gmeta.get("away_sp_id"):
            starter_ids[gmeta["away_sp"]] = gmeta["away_sp_id"]
        if gmeta.get("home_sp_id"):
            starter_ids[gmeta["home_sp"]] = gmeta["home_sp_id"]
    l14_pitcher_form = gp.fetch_l14_pitcher_form(starter_ids)
    fi_form = gp.fetch_first_inning_form(starter_ids)

    # Bullpen fatigue reads mlb_daily's L7_START/TODAY (repointed) through
    # statsapi schedules and box scores, so it is already point-in-time.
    bullpen_scores = gp.fetch_bullpen_scores(game_meta) if use_bullpen else {}
    bullpen_quality = gp.compute_bullpen_era(pit_df) if not pit_df.empty else {}

    park_wx = park_weather_asof(game_meta, date) if use_weather else {}

    comp_table = msrc.batter_pa_composition(start_date=f"{year}{SEASON_START_MMDD}",
                                            end_date=cutoff)
    comp_table = {int(k): v for k, v in comp_table.items()}

    # Laser (hard_hit_105/110): routes through fetch_season_statcast, already
    # point-in-time safe via the swapped pyb.statcast + repointed TODAY above
    # -- no asof plumbing needed, same reasoning as fi_form/l7_form.
    hard_hit = msrc.hard_hit_game_rates()
    # Moonshot (420+ FT): same routing, same point-in-time safety, no new
    # plumbing needed -- built 2026-08-14 alongside score_moonshot.
    moonshot = msrc.moonshot_rates()
    # Pitcher Outs Recorded: this source has NO built-in date guard (raw
    # gameLog endpoint, no window param) -- asof=cutoff is required here or
    # this would silently read starts after the date being simulated. See
    # mlb_sources._empirical_pitcher_outs_one's own comment.
    pitcher_outs = msrc.empirical_pitcher_outs_rates(starter_ids.values(), asof=cutoff)
    # pull / park_hand / platoon_qoc: same fetch_season_statcast() route as
    # hard_hit above, so already point-in-time safe with zero extra plumbing.
    # These were live-scored-and-recorded signals (pull_park_synergy,
    # park_hand_index, platoon_barrel_pct/platoon_xwoba) that this backtest
    # simply never fetched, so signals.py could never measure their real
    # separation power. Genuinely fixable, unlike bvp/sp_rp/ump_env below.
    pull = msrc.pull_rates()
    park_hand = msrc.park_hand_factors()
    platoon_qoc = msrc.platoon_quality_of_contact()
    # framing: same fetch_season_statcast() route as pull/park_hand/
    # platoon_qoc above -- missed in the same original pass that added
    # those three (found 2026-08-12 while updating measure_signals.py's
    # docstring, which still claimed no signal could reach backtest). Feeds
    # build_candidates()'s own framing_by_team derivation (already present
    # in that function), so no separate derivation logic is needed here --
    # just supplying the raw table is enough.
    framing = msrc.catcher_framing()
    # ump_kbb: Statcast half is the same safe route; its schedule-hydrate
    # half already bounds with endDate=m.TODAY (repointed), so it too needed
    # no new plumbing -- just never wired into this dict.
    ump_kbb = msrc.umpire_k_bb_rates()
    # rest: fixed alongside this wiring -- see mlb_sources.rest_and_usage's
    # own asof comment. Without asof=cutoff this would silently read games
    # after the date being simulated, the same exposure pitcher_outs above
    # already had to guard against.
    rest = msrc.rest_and_usage(game_meta, asof=cutoff)
    # team_bat/team_field: CHECKED, not fixable the way pull/park_hand/
    # platoon_qoc/framing were, despite looking the same shape at first.
    # mlb_sources.fetch_team_stats now supports stats=byDateRange (verified
    # live 2026-08-12: real, different hitting numbers for a partial window
    # vs season-to-date -- same mechanism fetch_player_stats already uses).
    # But the ONE thing actually consumed downstream, score_stolen_base's
    # opp_cs_pct (via extras["cs_pct_by_team"], derived in generate_picks.py
    # from team_field's "CS%" column), can't be reconstructed this way: a
    # live byDateRange fielding pull is MISSING caughtStealingPercentage/
    # stolenBases/passedBall entirely (confirmed live -- the season query's
    # field set has 21 keys, the byDateRange query for the identical teams
    # has 13, and every catching-specific stat is among the 8 missing ones).
    # This is not a partial/degraded result to work around; the API simply
    # does not expose these fields for a date-bounded fielding query. Left
    # out of extras below for that reason -- adding team_bat/team_field
    # without cs_pct_by_team would supply data nothing reads (team_bat's own
    # generate_picks.py derivation, extras["team_k_pct"], is itself dead --
    # never read anywhere either) while implying this gap is closed when it
    # isn't. Same permanent-exclusion bucket as bvp/sp_rp/ump_env, for a
    # different underlying reason (missing fields, not a live-only source).
    extras = {
        "hard_hit": hard_hit, "moonshot": moonshot, "pitcher_outs": pitcher_outs,
        "pull": pull, "park_hand": park_hand, "platoon_qoc": platoon_qoc,
        "framing": framing, "ump_kbb": ump_kbb, "rest": rest,
    }
    # bvp, sp_rp, ump_env are DELIBERATELY left out, same bucket as the
    # market signals (line_move/combined_k_prices/pitcher_outs_prices):
    # not merely unwired, structurally impossible to reconstruct as-of a
    # historical cutoff with the sources this pipeline has.
    #   - bvp (mlb_sources.fetch_bvp, MLB Stats API stats=vsPlayer) and
    #     sp_rp (mlb_sources.fetch_batter_sit_split, stats=statSplits) are
    #     both live current-state aggregates. Verified live (2026-08-12):
    #     passing date=<historical date> to either endpoint returns BYTE-
    #     IDENTICAL totals to no date param at all -- the API silently
    #     ignores it. There is no way to ask either endpoint "as of D".
    #   - ump_env (mlb_sources.fetch_umpire_run_environment) pulls
    #     umpscorecards.com/api/umpires, a third-party snapshot with no
    #     date parameter anywhere in its API -- current state only, no
    #     historical archive exists to reconstruct from.
    # Any of the three could in principle become backtest-safe if this
    # pipeline started archiving its own daily snapshots going forward, but
    # that is a new data-collection project, not a wiring fix -- do not
    # "fix" this block by adding them without that archive existing first.

    kwargs = dict(
        batter_lookup=batter_lookup, pitcher_lookup=pitcher_lookup,
        team_k_lookup=team_k, park_wx=park_wx,
        # Dropped, not faked — see this module's docstring.
        ump_scores={}, sharp_bias={}, sprint_speed={}, catcher_poptime={},
        bullpen_scores=bullpen_scores, bullpen_quality=bullpen_quality,
        l7_form=l7_form, bat_speed_trend=bat_speed_trend,
        batter_arsenal=batter_arsenal, pitcher_arsenal=pitcher_arsenal,
        l14_pitcher_form=l14_pitcher_form, fi_form=fi_form, extras=extras,
    )
    if verbose:
        print(f"  inputs: {len(batter_lookup)} batters / {len(pitcher_lookup)} pitchers "
              f"season-to-date, {len(l7_form)} L7 form, {len(l14_pitcher_form)} L14 starters, "
              f"{len(batter_arsenal)} batter arsenals, {len(comp_table)} PA compositions",
              flush=True)
    return game_meta, kwargs, comp_table, pit_df, log


PROP_TYPE_BY_STAT = {
    "hits": "hits",
    "total_bases": "total_bases",
    "home_runs": "home_run",
    "home_run": "home_run",
    "strikeouts": "strikeouts",
    "walks": "walks",
    "stolen_base": "stolen_base",
    "first_inning_run": "first_inning_run",
    "nrfi_combined": "nrfi_combined",
    "hard_hit_105": "hard_hit_105", "hard_hit_110": "hard_hit_110",
    "moonshot_420": "moonshot_420",
    "pitcher_outs": "pitcher_outs",
    # Real, live board markets (select_best_by_category / _batter_options)
    # that were never added here -- every one of them was being scored,
    # priced and shown on the board every night while silently falling out
    # of both backtest AND live grading (grade_results.grade_pick had no
    # branch for any of them either, fixed alongside this). combined_strikeouts
    # deliberately absent: that market only ever produces a candidate when a
    # REAL FanDuel price was fetched, and backtest never fetches live prices,
    # so build_candidates() naturally never generates one during a backtest
    # run -- nothing to map.
    "runs": "runs", "rbis": "rbis", "hits_runs_rbis": "hits_runs_rbis",
    "singles": "singles", "doubles": "doubles", "triples": "triples",
}


def _line_and_needs(pick):
    """The line the pick was actually recommended at, and the integer count it
    needed. Mirrors grade_results.grade_pick's own threshold logic so the row
    and the grade can never disagree."""
    proj = pick.get("projection") or {}
    stat = proj.get("stat")
    needs = proj.get("needs")
    value = proj.get("value")
    if stat in ("first_inning_run", "nrfi_combined"):
        # first_inning_run: a one-sided team market (does the opposing lineup
        # score off him in the 1st). nrfi_combined: the real both-teams
        # market. Both expressed as over/under 0.5 runs, with `lean` carrying
        # which side was recommended.
        return 0.5, 1
    if needs is not None:
        return (float(needs) - 0.5), int(needs)
    if value is None:
        return None, None
    if stat == "total_bases":
        return 1.5, 2
    if stat in ("walks", "stolen_base"):
        # Fixed lines, matching the text these scorers actually display
        # ("Over 0.5 Walks", "To Steal a Base"). Their `value` fields are 0.7
        # and 1, chosen so grade_pick's proj-0.5 threshold lands correctly --
        # reporting `line` as 0.2 from that arithmetic would be a number no
        # book posts and no reader would recognise. Same grade either way:
        # actual > 0.2 and actual > 0.5 are the same condition on an integer.
        return 0.5, 1
    line = float(value) - 0.5
    return line, int(math.ceil(line))


class Unusable(Exception):
    """A candidate that cannot become a row, with the reason, so coverage
    reporting can say WHY rather than just showing a smaller number."""


def to_row(date, pick, graded, keep_unpriced=False):
    """One backtest row, per backtest/SCHEMA.md. Raises Unusable with a
    reason instead of returning None, so nothing is dropped silently."""
    stat = ((pick.get("projection") or {}).get("stat"))
    prop_type = PROP_TYPE_BY_STAT.get(stat)
    if prop_type is None:
        raise Unusable(f"prop type not in the schema's vocabulary: {stat!r}")
    grade = graded.get("grade")
    if grade not in ("hit", "miss"):
        # SCHEMA: ungradeable rows are omitted entirely, never encoded as 0.
        raise Unusable(str(graded.get("reason") or "ungraded")[:80])
    if pick.get("hit_probability") is None and not keep_unpriced:
        # The pipeline could not price this candidate (almost always a batter
        # under the 30-PA floor the PA-composition table needs). Emitting the
        # row with a null predicted_prob is not free: backtest/calibration.py
        # reads that field with float() and would raise on the first null, so
        # a null here is not 'honest missing data', it is an output that does
        # not plug into its own consumer. Dropped and counted instead --
        # --keep-unpriced restores them for signal-fitting-only use, which
        # tolerates the null.
        raise Unusable("no predicted_prob (pipeline could not price this candidate)")
    line, needs = _line_and_needs(pick)
    actual = graded.get("actual")
    row = {
        "date": date,
        "game_pk": pick.get("game_pk"),
        "player_id": pick.get("player_id"),
        "player_name": pick.get("name"),
        "prop_type": prop_type,
        "line": line,
        "needs": needs,
        "signals": pick.get("signals") or {},
        "score": pick.get("score"),
        # Raw 0-100 category components (batters/pitchers only -- see
        # score_batter/score_pitcher's return dicts) BEFORE the hand-set
        # 35/25/15/15/10 weighting, so fit_score_weights.py can fit real
        # weights against outcome without re-deriving them from `signals`.
        # None for prop types that don't use this framework (pitcher_outs,
        # combined_strikeouts, stolen_base, laser, walk, first_inning).
        "cat_matchup": pick.get("cat_matchup"),
        "cat_recent_form": pick.get("cat_recent_form"),
        "cat_environment": pick.get("cat_environment"),
        "cat_baseline_skill": pick.get("cat_baseline_skill"),
        "cat_context": pick.get("cat_context"),
        # score_stolen_base's own 3-category scheme (skill/matchup/context,
        # weighted 50/28/22) -- see the matching comment in its return dict.
        "sb_cat_skill": pick.get("sb_cat_skill"),
        "sb_cat_matchup": pick.get("sb_cat_matchup"),
        "sb_cat_context": pick.get("sb_cat_context"),
        "predicted_prob": pick.get("hit_probability"),
        "outcome": 1 if grade == "hit" else 0,
        "actual": actual,
        # Kept per-row and NEVER pre-filtered, per SCHEMA.md. A pick that got
        # two pinch-hit PAs is evidence about circumstance, not about the
        # model, and the consumer decides whether to include it.
        "fair_test": graded.get("fair_test"),
        "actual_pa": graded.get("actual_pa_est"),
    }
    if pick.get("lean"):
        row["lean"] = pick["lean"]
    if pick.get("team"):
        row["team"] = pick["team"]
    if graded.get("actual_ip") is not None:
        row["actual_ip"] = graded["actual_ip"]
    return row


def simulate_date(date, store, use_weather=True, use_bullpen=True, keep_unpriced=False,
                  verbose=True) -> DateResult:
    """Reconstruct, score and grade one past date. Never raises for data
    problems — a failed date is reported, not fatal, so a 60-day run does not
    die on one bad slate."""
    res = DateResult(date)
    cutoff = shift(date, -1)
    try:
        with PointInTime(date, store) as pit:
            game_meta, kwargs, comp_table, _pit_df, log = build_inputs(
                date, store, use_weather=use_weather, use_bullpen=use_bullpen,
                verbose=verbose)
            if not game_meta:
                res.status = "no_games"
                res.reason = "no games on the schedule"
                res.log = pit.log
                return res
            res.n_games = len(game_meta)
            # Merge the explicitly-recorded reads into the guard's own log so
            # --verify sees everything in one place.
            pit.log.reads.extend(log.reads)
            res.log = pit.log

            candidates = gp.build_candidates(game_meta, **kwargs)
            # Probabilities. comp_table is rebuilt point-in-time.
            #
            # emp_batters/emp_pitchers USED TO BE HARDCODED {}, {} HERE,
            # unconditionally, with the comment "season-scoped and therefore
            # passed empty" -- true of the underlying game-log source before
            # its own asof fix (see empirical_batter_prop_rates/
            # empirical_pitcher_k_rates' docstrings), but not true anymore,
            # and leaving it hardcoded meant no backtest run had EVER
            # exercised the actual empirical+modelled blend live scoring
            # uses for hits/total_bases/home_runs/runs/rbis/stolen_base/
            # singles/doubles/triples/hits_runs_rbis -- every run validated a
            # modelled-only stand-in instead of what actually ships. Fetched
            # here the same way generate_picks.py's own live path does:
            # AFTER build_candidates, from the ids that actually produced a
            # candidate, not the whole slate.
            bat_ids = [c["player_id"] for c in candidates
                       if c.get("type") == "batter" and c.get("player_id")]
            pit_ids = [c["player_id"] for c in candidates
                       if c.get("type") == "pitcher" and c.get("player_id")]
            emp_batters = msrc.empirical_batter_prop_rates(bat_ids, asof=cutoff)
            emp_pitchers = msrc.empirical_pitcher_k_rates(pit_ids, asof=cutoff)
            # league_rates WAS missing here entirely (verified live: this is
            # why generate_picks.py's league-only fallback and the
            # SHRINK_MODEL_K toggle were both structurally inert in every
            # backtest run so far -- 0 candidates ever took either path,
            # confirmed by instrumenting a real date). Safe to call here
            # specifically because league_base_rates() reads through
            # m.fetch_season_statcast(), which routes through the swapped,
            # cutoff-guarded pyb.statcast (see PointInTime.__enter__) --
            # unlike statcast_sprint_speed/statcast_catcher_poptime, it is
            # NOT on the _POISONED_LEADERBOARDS list, because it has no
            # season-aggregate-with-no-date-window shape to poison. Its own
            # module-level cache is cleared per-date by PointInTime now too
            # (see __enter__/__exit__), so date 5 cannot see date 1's rate.
            league_rates = msrc.league_base_rates()
            gp.attach_hit_probabilities(candidates, comp_table, emp_batters, emp_pitchers, league_rates)
            res.n_candidates = len(candidates)
    except LookaheadError:
        raise                            # never degrade a leak into a warning
    except Exception as e:
        res.status = "failed"
        res.reason = f"input assembly: {type(e).__name__}: {e}"
        return res

    # Grading happens OUTSIDE the point-in-time context on purpose: it is
    # supposed to read the future — that is what an outcome is.
    try:
        statuses = gr.fetch_game_statuses(date)
        for c in candidates:
            try:
                graded = gr.grade_pick(c, statuses, date=date)
            except Exception as e:
                res.n_ungraded += 1
                res.ungraded_reasons[f"grader error: {type(e).__name__}"] += 1
                continue
            try:
                res.rows.append(to_row(date, c, graded, keep_unpriced=keep_unpriced))
            except Unusable as u:
                res.n_ungraded += 1
                res.ungraded_reasons[str(u)] += 1
    except Exception as e:
        res.status = "failed"
        res.reason = f"grading: {type(e).__name__}: {e}"
        return res
    return res


# ══════════════════════════════════════════════════════════════════════════
#  THE LEAKAGE PROOF
# ══════════════════════════════════════════════════════════════════════════

def verify_no_lookahead(date, store, use_weather=False, use_bullpen=False, verbose=True):
    """Runnable proof that the inputs assembled for `date` predate `date`.

    Six checks. The first is the claim; the rest exist because the first one is
    also what a completely broken engine would report.

      1. Every logged data read requested an end date strictly before D, and
         every frame returned contains no row dated on or after D.
      2. POSITIVE CONTROL — the unrestricted store DOES contain rows on and
         after D. Without this, check 1 passes trivially on an empty dataset,
         which is the failure mode that makes a leakage test worthless.
      3. THE BOUND CHANGES THE ANSWER — season lines queried through D-1 differ
         from the same lines queried through D for a real player. If they were
         identical the window would be decorative.
      4. The rolling windows really end the day before D.
      5. The season leaderboards are unreachable: calling one raises rather
         than quietly returning today's numbers.
      6. Graded games are all ON D. Inputs must predate D; outcomes must not.

    Returns (ok, list_of_(passed, description)).
    """
    checks = []

    def ok(cond, desc):
        checks.append((bool(cond), desc))
        if verbose:
            print(f"  [{'PASS' if cond else 'FAIL'}] {desc}", flush=True)
        return bool(cond)

    date = dstr(date)
    cutoff = shift(date, -1)
    year = dparse(date).year
    if verbose:
        print(f"\nLEAKAGE VERIFICATION for {date} (cutoff {cutoff})\n" + "-" * 72, flush=True)

    # ---- assemble the date for real -------------------------------------
    with PointInTime(date, store) as pit:
        game_meta, kwargs, comp_table, _pdf, log = build_inputs(
            date, store, use_weather=use_weather, use_bullpen=use_bullpen, verbose=verbose)
        if not game_meta:
            print(f"  {date} has no games — pick a date with a slate.")
            return False, [(False, "no games on that date")]
        pit.log.reads.extend(log.reads)
        reads = list(pit.log.reads)
        candidates = gp.build_candidates(game_meta, **kwargs)

        # ---- 5. leaderboards are poisoned -------------------------------
        poisoned_ok = False
        try:
            m.pyb.statcast_sprint_speed(year)
        except LookaheadError:
            poisoned_ok = True
        except Exception:
            poisoned_ok = False
        ok(poisoned_ok,
           "season leaderboards raise LookaheadError instead of returning "
           "season-to-date numbers")

        # A direct attempt to read through D must also be refused.
        guard_ok = False
        try:
            m.pyb.statcast(start_dt=cutoff, end_dt=date)
        except LookaheadError:
            guard_ok = True
        except Exception:
            guard_ok = False
        ok(guard_ok, f"a Statcast read with end_dt={date} is refused by the guard")

        # Every mlb_daily date global, checked as a group. This is what makes
        # the inputs that DON'T go through the Statcast wrapper safe -- most
        # importantly bullpen fatigue, which reads L7_START/TODAY straight out
        # of this module dict to bound a statsapi schedule + box-score walk,
        # inside worker threads. Asserting the globals covers every such
        # consumer at once, including ones added later.
        globals_now = {k: getattr(m, k, None) for k in
                       ("TODAY", "YESTERDAY", "L7_START", "L7_END",
                        "L14_START", "L14_END", "L30_START", "L30_END")}
        bad_globals = {k: v for k, v in globals_now.items() if v and dstr(v) >= date}
        ok(not bad_globals,
           f"every mlb_daily date global points before {date} "
           f"(TODAY={globals_now['TODAY']}, L7={globals_now['L7_START']}..{globals_now['L7_END']}, "
           f"L14={globals_now['L14_START']}..{globals_now['L14_END']})"
           + (f" — VIOLATIONS: {bad_globals}" if bad_globals else ""))

        # ---- 3. the bound changes the answer -----------------------------
        # REAL BUG, found verifying a 2024 date (never surfaced backtesting
        # 2026 only): this used to run AFTER the `with PointInTime` block
        # closed, by which point __exit__ had already restored m.YEAR to the
        # REAL current year. fetch_player_stats() sends season=m.YEAR
        # alongside the explicit startDate/endDate computed from `year`
        # (this date's own year) -- for a same-year backtest those always
        # matched by coincidence, so the mismatch was never exercised. For
        # any OTHER year the season param and the date range disagree, and
        # MLB's byDateRange endpoint returns ZERO rows for a mismatched
        # season (verified live: season=2026 + 2024 dates -> 0 rows, both
        # calls, so "0 players gained PA" was comparing two empty lists, not
        # detecting real leakage). Moved inside the block so m.YEAR is still
        # correctly this date's own year when the call is made.
        thru_cutoff = msrc.fetch_player_stats("hitting", limit=2000,
                                              start_date=f"{year}{SEASON_START_MMDD}",
                                              end_date=cutoff)
        time.sleep(0.5)
        thru_date = msrc.fetch_player_stats("hitting", limit=2000,
                                            start_date=f"{year}{SEASON_START_MMDD}",
                                            end_date=date)

        def pa_by_id(rows):
            return {(r.get("player") or {}).get("id"): _i((r.get("stat") or {}).get("plateAppearances"))
                    for r in rows}

        a, b = pa_by_id(thru_cutoff), pa_by_id(thru_date)
        moved = [pid for pid in a if pid in b and b[pid] > a[pid]]
        example = ""
        if moved:
            pid = moved[0]
            name = next(((r.get("player") or {}).get("fullName") for r in thru_date
                         if (r.get("player") or {}).get("id") == pid), pid)
            example = f" e.g. {name}: {a[pid]} PA through {cutoff} vs {b[pid]} through {date}"
        ok(len(moved) > 0,
           f"the season-line window is load-bearing: {len(moved)} players gained PA "
           f"between endDate={cutoff} and endDate={date}.{example}")

    # ---- 1. no read touched D or later ----------------------------------
    violations = []
    for r in reads:
        if r["end"] and dstr(r["end"]) >= date:
            violations.append(f"{r['source']}: requested end {r['end']}")
        if r["max_game_date"] and r["max_game_date"] >= date:
            violations.append(f"{r['source']}: returned data dated {r['max_game_date']}")
    latest = max([r["max_game_date"] for r in reads if r["max_game_date"]] or ["(none)"])
    ok(not violations,
       f"all {len(reads)} logged input reads end before {date} "
       f"(latest row seen anywhere in the inputs: {latest})")
    if violations and verbose:
        for v in violations[:10]:
            print(f"        ! {v}")

    # ---- 2. positive control --------------------------------------------
    full = store.load()
    on_or_after = full[full["game_date"] >= date]
    ok(len(on_or_after) > 0,
       f"POSITIVE CONTROL: the unrestricted store holds {len(on_or_after)} pitches "
       f"dated {date} or later — check 1 is a filter working, not an empty dataset")
    same_day = full[full["game_date"] == date]
    ok(len(same_day) > 0,
       f"POSITIVE CONTROL: {len(same_day)} of those pitches are from {date} itself, "
       f"and none of them reached any input frame")

    # ---- 4. rolling windows end the day before --------------------------
    rolling = [r for r in reads if r["source"] == "statcast" or r["source"].startswith("statcast_")]
    ends = {dstr(r["end"]) for r in rolling if r["end"]}
    ok(ends and all(e <= cutoff for e in sorted(ends)),
       f"every rolling Statcast window ends on or before {cutoff} "
       f"(observed ends: {sorted(ends) if ends else 'none'})")

    # ---- 4b. the SEASON-WIDE Statcast pull is bounded too ---------------
    # Named explicitly because it is the newest and least obvious lookahead
    # surface in the pipeline. fetch_first_inning_form() was rewritten to read
    # m.fetch_season_statcast(), which issues pyb.statcast(start_dt=YEAR-03-15,
    # end_dt=TODAY) and memoises the result in a module global. Two things had
    # to be true for that to be safe here and both are asserted rather than
    # assumed: TODAY is repointed to D-1 so the pull itself is bounded, and the
    # module-level cache is cleared on entering each simulated date so a frame
    # fetched for a later date can never be reused for an earlier one.
    #
    # This check requires the read to have HAPPENED. A version of it that only
    # said "no season pull went past the cutoff" would pass trivially if the
    # call were skipped, which is exactly how a leak hides.
    season_pulls = [r for r in rolling
                    if r["start"] and dstr(r["start"]) <= f"{year}-03-20" and r["rows"]]
    ok(season_pulls and all(dstr(r["end"]) <= cutoff for r in season_pulls),
       f"the season-wide Statcast pull behind first-inning form is bounded: "
       f"{len(season_pulls)} pull(s), ending "
       f"{sorted({dstr(r['end']) for r in season_pulls}) if season_pulls else 'never issued'} "
       f"(latest row {max((r['max_game_date'] or '-') for r in season_pulls) if season_pulls else '-'})")
    ok(getattr(m, "_SEASON_STATCAST_CACHE", None) is None
       or str(pd.DataFrame(m._SEASON_STATCAST_CACHE)["game_date"].max())[:10] < date
       if getattr(m, "_SEASON_STATCAST_CACHE", None) is not None else True,
       "mlb_daily's season-Statcast memo holds nothing dated on or after "
       f"{date} (it is cleared on entering every simulated date, so a frame "
       f"pulled for a later date cannot be reused for an earlier one)")

    # ---- 6. outcomes are on D -------------------------------------------
    pks = {c.get("game_pk") for c in candidates if c.get("game_pk")}
    sched_pks = set(gr.fetch_game_statuses(date).keys())
    ok(pks and pks.issubset(sched_pks),
       f"all {len(pks)} games being graded are on {date} itself — inputs predate "
       f"the date, outcomes do not")

    passed = all(c for c, _ in checks)
    if verbose:
        print("-" * 72)
        print(f"  {sum(1 for c, _ in checks if c)}/{len(checks)} checks passed — "
              f"{'NO LOOKAHEAD DETECTED' if passed else 'LEAKAGE DETECTED'}\n", flush=True)
    return passed, checks


# ══════════════════════════════════════════════════════════════════════════
#  RESUMABLE RUN
# ══════════════════════════════════════════════════════════════════════════

def state_path(out_path):
    return out_path + ".state.json"


def load_state(out_path):
    p = state_path(out_path)
    if not os.path.exists(p):
        return {"dates": {}}
    try:
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
        st.setdefault("dates", {})
        return st
    except (json.JSONDecodeError, OSError):
        return {"dates": {}}


def save_state(out_path, state):
    tmp = state_path(out_path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, state_path(out_path))


def dates_already_in_output(out_path):
    """Dates present in the JSONL itself.

    Belt and braces against the state file: if a crash lands between appending
    rows and writing state, the state file says 'not done' while the rows are
    already there, and a naive resume would duplicate them. Trusting the data
    over the bookkeeping is the safer direction.
    """
    seen = set()
    if not os.path.exists(out_path):
        return seen
    with open(out_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                seen.add(json.loads(line)["date"])
            except (json.JSONDecodeError, KeyError):
                continue
    return seen


def run_backtest(start, end, out_path, store=None, sleep=DEFAULT_SLEEP,
                 use_weather=True, use_bullpen=True, keep_unpriced=False,
                 force=False, verbose=True):
    dates = date_range(start, end)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)

    state = load_state(out_path)
    in_output = dates_already_in_output(out_path)
    if force:
        state, in_output = {"dates": {}}, set()

    if store is None:
        store = StatcastStore(dparse(dates[0]).year, shift(dates[-1], -1), verbose=verbose)
        store.load()

    # Dedupe boxscore fetches across the whole run: bullpen fatigue and grading
    # both hammer the same handful of games, and this is a free public API.
    _orig_box = m.statsapi.boxscore_data
    _box_cache = {}

    def _cached_box(game_id, *a, **kw):
        if game_id not in _box_cache:
            _box_cache[game_id] = _orig_box(game_id, *a, **kw)
        return _box_cache[game_id]
    m.statsapi.boxscore_data = _cached_box

    summary = {"completed": [], "skipped": [], "no_games": [], "failed": {}, "rows": 0}
    try:
        for i, d in enumerate(dates, 1):
            prior = state["dates"].get(d, {})
            if not force and (d in in_output or prior.get("status") in ("ok", "no_games")):
                summary["skipped"].append(d)
                if verbose:
                    print(f"[{i}/{len(dates)}] {d}  already done ({prior.get('rows', 'n/a')} rows) — skipping",
                          flush=True)
                continue
            if verbose:
                print(f"\n[{i}/{len(dates)}] {d}", flush=True)
            t0 = time.time()
            res = simulate_date(d, store, use_weather=use_weather,
                                use_bullpen=use_bullpen, keep_unpriced=keep_unpriced,
                                verbose=verbose)
            elapsed = round(time.time() - t0, 1)

            if res.status == "no_games":
                state["dates"][d] = {"status": "no_games", "rows": 0, "seconds": elapsed}
                summary["no_games"].append(d)
            elif res.status == "failed":
                state["dates"][d] = {"status": "failed", "reason": res.reason, "seconds": elapsed}
                summary["failed"][d] = res.reason
                if verbose:
                    print(f"  FAILED: {res.reason}", flush=True)
            else:
                # Append only after the whole date succeeded. A partially
                # written date is the one thing resumability cannot repair,
                # because there is no way to tell a partial date from a
                # complete one after the fact.
                with open(out_path, "a", encoding="utf-8") as f:
                    for row in res.rows:
                        f.write(json.dumps(row) + "\n")
                state["dates"][d] = {
                    "status": "ok", "rows": len(res.rows), "games": res.n_games,
                    "candidates": res.n_candidates, "ungraded": res.n_ungraded,
                    "ungraded_reasons": dict(res.ungraded_reasons), "seconds": elapsed,
                }
                summary["completed"].append(d)
                summary["rows"] += len(res.rows)
                if verbose:
                    hits = sum(r["outcome"] for r in res.rows)
                    rate = f"{hits / len(res.rows):.3f}" if res.rows else "n/a"
                    print(f"  {res.n_games} games, {res.n_candidates} candidates -> "
                          f"{len(res.rows)} graded rows ({res.n_ungraded} ungraded), "
                          f"hit rate {rate}, {elapsed}s", flush=True)
            save_state(out_path, state)
            if i < len(dates):
                time.sleep(sleep)
    finally:
        m.statsapi.boxscore_data = _orig_box
    return summary, state


# ══════════════════════════════════════════════════════════════════════════
#  COVERAGE REPORT
# ══════════════════════════════════════════════════════════════════════════

def coverage_report(out_path, state=None):
    state = state or load_state(out_path)
    rows = []
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    lines = ["", "=" * 74, "BACKTEST COVERAGE", "=" * 74]
    by_status = defaultdict(list)
    for d, info in sorted(state.get("dates", {}).items()):
        by_status[info.get("status", "?")].append(d)
    for status in sorted(by_status):
        ds = by_status[status]
        lines.append(f"  {status:<10} {len(ds):>4} date(s)"
                     + (f"   {ds[0]} .. {ds[-1]}" if ds else ""))
    failed = {d: state["dates"][d].get("reason") for d in by_status.get("failed", [])}
    if failed:
        lines.append("")
        lines.append("  FAILURES (date -> why):")
        for d, why in sorted(failed.items()):
            lines.append(f"    {d}  {why}")

    ungraded = defaultdict(int)
    total_cand = total_ungraded = 0
    for info in state.get("dates", {}).values():
        total_cand += info.get("candidates", 0) or 0
        total_ungraded += info.get("ungraded", 0) or 0
        for reason, n in (info.get("ungraded_reasons") or {}).items():
            ungraded[reason] += n
    if total_cand:
        lines += ["",
                  f"  candidates scored : {total_cand}",
                  f"  rows emitted      : {len(rows)}",
                  f"  candidates dropped: {total_ungraded} "
                  f"({total_ungraded / total_cand * 100:.1f}%)"]
        if ungraded:
            lines.append("  why they were dropped (SCHEMA.md: ungradeable rows are omitted, "
                         "never encoded as 0):")
            for reason, n in sorted(ungraded.items(), key=lambda kv: -kv[1])[:10]:
                lines.append(f"    {n:>6}  {reason}")

    if rows:
        lines += ["", "-" * 74, "HIT RATE BY PROP TYPE", "-" * 74,
                  f"  {'prop_type':<20}{'n':>7}{'hits':>7}{'rate':>9}"
                  f"{'n_fair':>8}{'fair rate':>11}"]
        by_type = defaultdict(list)
        for r in rows:
            by_type[r.get("prop_type", "?")].append(r)
        for pt in sorted(by_type):
            rs = by_type[pt]
            hits = sum(r["outcome"] for r in rs)
            fair = [r for r in rs if r.get("fair_test")]
            fh = sum(r["outcome"] for r in fair)
            lines.append(f"  {pt:<20}{len(rs):>7}{hits:>7}{hits / len(rs):>9.3f}"
                         f"{len(fair):>8}"
                         + (f"{fh / len(fair):>11.3f}" if fair else f"{'n/a':>11}"))
        hits = sum(r["outcome"] for r in rows)
        lines.append(f"  {'ALL':<20}{len(rows):>7}{hits:>7}{hits / len(rows):>9.3f}")
        lines += ["",
                  "  Real MLB prop hit rates live in the 50-65% band. A pooled rate above",
                  "  ~75% is a symptom of leakage, not a result — if you see one, stop and",
                  "  run --verify before believing any of it.",
                  "",
                  "  NOT VALIDATED HERE: market signals (no reconstructable line history),",
                  "  stolen-base props (no point-in-time sprint speed), and the wRC+,",
                  "  Stuff+ and umpire inputs (no date-windowed source). See the module",
                  "  docstring."]
    lines.append("=" * 74)
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Point-in-time backtest of the MLB prop pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Re-running the same command resumes: completed dates are skipped "
               "and rows are never duplicated.")
    ap.add_argument("--start", help="first date to simulate (YYYY-MM-DD)")
    ap.add_argument("--end", help="last date to simulate (YYYY-MM-DD)")
    ap.add_argument("--out", default="backtest/rows.jsonl", help="JSONL output path")
    ap.add_argument("--verify", metavar="DATE",
                    help="run the no-lookahead proof for one date and exit")
    ap.add_argument("--report", action="store_true",
                    help="print the coverage/hit-rate report for --out and exit")
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP,
                    help=f"seconds between dates (default {DEFAULT_SLEEP})")
    ap.add_argument("--no-weather", action="store_true",
                    help="skip historical weather; every park scores neutral")
    ap.add_argument("--no-bullpen", action="store_true",
                    help="skip bullpen fatigue (the slowest input, ~150 box scores/date)")
    ap.add_argument("--keep-unpriced", action="store_true",
                    help="keep candidates the pipeline could not price. They carry a "
                         "null predicted_prob, which backtest/calibration.py cannot "
                         "read — useful for signal fitting only")
    ap.add_argument("--force", action="store_true",
                    help="ignore existing state and re-simulate every date "
                         "(truncates the output file)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    verbose = not args.quiet

    if args.report:
        print(coverage_report(args.out))
        return 0

    if args.verify:
        d = dstr(args.verify)
        store = StatcastStore(dparse(d).year, d, verbose=verbose)
        store.load()
        passed, _ = verify_no_lookahead(d, store, use_weather=False,
                                        use_bullpen=False, verbose=verbose)
        return 0 if passed else 1

    if not args.start or not args.end:
        ap.error("--start and --end are required (or use --verify / --report)")

    if args.force and os.path.exists(args.out):
        os.remove(args.out)

    dates = date_range(args.start, args.end)
    # The store must cover through the day before the LAST simulated date --
    # that is the newest data any input is allowed to see. The verification's
    # positive control needs rows on and after a simulated date, so pull one
    # extra day rather than stopping exactly at the boundary.
    store = StatcastStore(dparse(dates[0]).year, dates[-1], verbose=verbose)
    store.load()

    summary, state = run_backtest(
        args.start, args.end, args.out, store=store, sleep=args.sleep,
        use_weather=not args.no_weather, use_bullpen=not args.no_bullpen,
        keep_unpriced=args.keep_unpriced, force=args.force, verbose=verbose)

    print(coverage_report(args.out, state))
    print(f"\nWrote {summary['rows']} new rows to {args.out}")
    print(f"State: {state_path(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

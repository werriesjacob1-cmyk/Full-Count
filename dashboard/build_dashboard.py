#!/usr/bin/env python3
"""dashboard/build_dashboard.py — builds the standalone Gridiron Board HTML
(the tabbed prop-explorer dashboard, distinct from the curated top-10 board
generate_picks.py ships) in one pass: a live, isolated re-run of the real
scoring pipeline to capture EVERY qualifying candidate per prop family (not
just the single winner select_best_by_category/select_moonshots normally
keep for the curated board), then renders it into one self-contained HTML
file with fonts and data embedded.

Read-only against the real pipeline: OUTPUT_DIR/PLAYERS_DIR are redirected
to a throwaway temp directory for the whole run, so nothing here ever
touches output/, data/players/, or any file this repo actually commits.

    python3 dashboard/build_dashboard.py [--out PATH]

Intended to be run once a day (a fresh live pass takes several minutes and
makes real calls to FanGraphs/Statcast/FanDuel -- this is not something to
run every few minutes). The caller is responsible for publishing the
resulting HTML file wherever it needs to go; this script only builds it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD_DIR = os.path.join(REPO_ROOT, "dashboard")


def log(msg):
    print(msg, flush=True)


def run_live_fetch():
    """Isolated live re-run of generate_picks.py's scoring pass. Returns the
    same shape fetch_full_depth.py (the scratch prototype this was promoted
    from) produced: {"generated_at", "date", "moonshot": [...], "<stat>": [...]}.
    """
    scratch = tempfile.mkdtemp(prefix="gridiron_dashboard_")
    os.environ["OUTPUT_DIR"] = os.path.join(scratch, "output")
    os.environ["PLAYERS_DIR"] = os.path.join(scratch, "players")
    os.makedirs(os.environ["OUTPUT_DIR"], exist_ok=True)
    os.makedirs(os.environ["PLAYERS_DIR"], exist_ok=True)

    sys.path.insert(0, REPO_ROOT)
    os.chdir(REPO_ROOT)

    import generate_picks as gp

    log("Starting isolated live scoring pass...")
    result = gp._build_and_score()
    if result is None:
        log("No games / nothing bettable right now.")
        return {"generated_at": datetime.now().isoformat(), "date": gp.m.TODAY}

    candidates, ctx = result
    game_meta = ctx["game_meta"]; park_wx = ctx["park_wx"]
    emp_pitchers = ctx["emp_pitchers"]
    early_po_prices = ctx.get("po_prices")
    log(f"Scored {len(candidates)} raw candidates across {len(game_meta)} bettable games.")

    candidates, _qc_rejected, _assumed_lineup = gp.quality_control(candidates, game_meta, park_wx, emp_pitchers)
    log(f"{len(candidates)} candidates survive quality_control.")

    signal_trust = gp.load_signal_trust()
    gp.apply_signal_weights(candidates, trust=signal_trust)

    import odds_fanduel as fd
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

    moonshots_full = gp.select_moonshots(candidates, prices, fd, n=9999)
    log(f"{len(moonshots_full)} total home_run candidates.")

    by_category_full = gp.select_best_by_category(candidates, prices, fd, n_per_category=9999, k_prices=k_prices)
    for stat, entries in by_category_full.items():
        log(f"  {stat}: {len(entries)} candidates")

    def clean(rows):
        out = []
        for r in rows:
            out.append({
                "type": r.get("type"), "name": r.get("name"), "team": r.get("team"),
                "matchup": r.get("matchup"), "side": r.get("side"), "prop": r.get("prop"),
                "projection": r.get("projection"), "lean": r.get("lean"),
                "score": r.get("score"), "confidence": r.get("confidence"),
                "hit_probability": r.get("hit_probability"),
                "market_odds": r.get("market_odds"), "market_implied": r.get("market_implied"),
                "market_edge": r.get("market_edge"), "price_clears": r.get("price_clears"),
                "reliability": r.get("reliability"), "sample_n": r.get("sample_n"),
                "why": (r.get("why") or [])[:4],
                "watchouts": (r.get("watchouts") or [])[:2],
                "base_rate": r.get("base_rate"), "lift": r.get("lift"),
            })
        return out

    out = {"generated_at": datetime.now().isoformat(), "date": gp.m.TODAY,
          "moonshot": clean(moonshots_full)}
    for stat, entries in by_category_full.items():
        out[stat] = clean(entries)
    return out


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

CATEGORY_ORDER = [
    "hits_runs_rbis", "hits", "total_bases", "singles", "doubles", "triples",
    "runs", "rbis", "moonshot", "stolen_base", "strikeouts",
    "combined_strikeouts", "pitcher_outs", "hard_hit_105", "hard_hit_110",
    "nrfi_combined",
]


def build_payload(result):
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
    # duplicate rather than show two "Home Runs" tabs.
    result = dict(result)
    result.pop("home_runs", None)

    meta_keys = {"generated_at", "date"}
    tabs = {}
    for stat in CATEGORY_ORDER:
        rows = result.get(stat)
        if not rows:
            continue
        rows = [r for r in rows if r.get("hit_probability") is not None]
        rows.sort(key=lambda r: r["hit_probability"], reverse=True)
        if rows:
            tabs[stat] = add_estimated_odds(rows)

    for stat, rows in result.items():
        if stat in meta_keys or stat in tabs or stat in CATEGORY_ORDER:
            continue
        rows = [r for r in rows if r.get("hit_probability") is not None]
        rows.sort(key=lambda r: r["hit_probability"], reverse=True)
        if rows:
            tabs[stat] = add_estimated_odds(rows)

    all_rows = []
    for rows in tabs.values():
        all_rows.extend(rows)
    all_rows.sort(key=lambda r: r["hit_probability"], reverse=True)

    # "Top Picks" -- the board's real favorites. Ranked by genuine edge over
    # the market among picks that actually clear the price, not by raw
    # probability (which just rewards the easiest, most-chalk market every
    # time). Not padded to a fixed count.
    top_picks = [r for r in all_rows if r.get("price_clears") is True]
    top_picks.sort(key=lambda r: r.get("market_edge") or 0, reverse=True)
    top_picks = top_picks[:10]

    return {
        "date": result.get("date"),
        "generated_at": result.get("generated_at"),
        "tabs_order": ["top_picks", "all"] + list(tabs.keys()),
        "labels": {
            "top_picks": "Top Picks", "all": "All Props",
            **{stat: CATEGORY_LABELS.get(stat, stat.replace("_", " ").title()) for stat in tabs},
        },
        "data": {"top_picks": top_picks, "all": all_rows, **tabs},
    }


PAGE_TEMPLATE = """<meta charset="utf-8">
<title>Gridiron Board</title>
<style>
@font-face {{
  font-family: 'Archivo Var';
  font-weight: 100 900;
  font-style: normal;
  font-display: swap;
  src: url(data:font/woff2;base64,{archivo}) format('woff2');
}}
@font-face {{
  font-family: 'Plex Sans Var';
  font-weight: 100 900;
  font-style: normal;
  font-display: swap;
  src: url(data:font/woff2;base64,{plexsans}) format('woff2');
}}
@font-face {{
  font-family: 'Plex Mono';
  font-weight: 500;
  font-style: normal;
  font-display: swap;
  src: url(data:font/woff2;base64,{plexmono500}) format('woff2');
}}
@font-face {{
  font-family: 'Plex Mono';
  font-weight: 600;
  font-style: normal;
  font-display: swap;
  src: url(data:font/woff2;base64,{plexmono600}) format('woff2');
}}

:root {{
  --ground: #F1F2F6;
  --surface: #FFFFFF;
  --surface-2: #F6F7FB;
  --surface-raised: #ECEFF5;
  --line: #DBDFE9;
  --line-soft: #E7E9F0;
  --ink: #0F1220;
  --ink-dim: #545D75;
  --ink-faint: #8992A6;
  --accent: #A6690A;
  --accent-bright: #8F5806;
  --accent-ink: #FFFFFF;
  --accent-soft: #F4E6C9;
  --good: #1F9A63;
  --good-soft: #E1F5EC;
  --bad: #D63A54;
  --bad-soft: #FCE8EB;
  --shadow: 0 1px 2px rgba(15, 18, 32, 0.05), 0 6px 16px -8px rgba(15, 18, 32, 0.14);
  --shadow-lift: 0 2px 4px rgba(15, 18, 32, 0.07), 0 12px 28px -10px rgba(15, 18, 32, 0.20);

  --font-display: 'Archivo Var', 'Archivo', system-ui, sans-serif;
  --font-body: 'Plex Sans Var', 'IBM Plex Sans', system-ui, sans-serif;
  --font-mono: 'Plex Mono', 'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, monospace;
}}

@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground: #080A11; --surface: #10141F; --surface-2: #151A29;
    --surface-raised: #1A2033; --line: #232C40; --line-soft: #1B2233;
    --ink: #F4F6FB; --ink-dim: #8F99B2; --ink-faint: #5B6480;
    --accent: #F0B429; --accent-bright: #FFC94A; --accent-ink: #1A1200;
    --accent-soft: rgba(240, 180, 41, 0.14);
    --good: #33D689; --good-soft: rgba(51, 214, 137, 0.13);
    --bad: #FF5C72; --bad-soft: rgba(255, 92, 114, 0.13);
    --shadow: 0 1px 2px rgba(0,0,0,0.35), 0 8px 20px -10px rgba(0,0,0,0.6);
    --shadow-lift: 0 2px 6px rgba(0,0,0,0.4), 0 16px 36px -12px rgba(0,0,0,0.7);
  }}
}}
:root[data-theme="dark"] {{
  --ground: #080A11; --surface: #10141F; --surface-2: #151A29;
  --surface-raised: #1A2033; --line: #232C40; --line-soft: #1B2233;
  --ink: #F4F6FB; --ink-dim: #8F99B2; --ink-faint: #5B6480;
  --accent: #F0B429; --accent-bright: #FFC94A; --accent-ink: #1A1200;
  --accent-soft: rgba(240, 180, 41, 0.14);
  --good: #33D689; --good-soft: rgba(51, 214, 137, 0.13);
  --bad: #FF5C72; --bad-soft: rgba(255, 92, 114, 0.13);
  --shadow: 0 1px 2px rgba(0,0,0,0.35), 0 8px 20px -10px rgba(0,0,0,0.6);
  --shadow-lift: 0 2px 6px rgba(0,0,0,0.4), 0 16px 36px -12px rgba(0,0,0,0.7);
}}

* {{ box-sizing: border-box; }}
html {{ color-scheme: light dark; }}
body {{
  margin: 0; background: var(--ground); color: var(--ink);
  font-family: var(--font-body); font-size: 15px; line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}}
::selection {{ background: var(--accent-soft); color: var(--ink); }}

.wrap {{ max-width: 980px; margin: 0 auto; padding: 24px 20px 64px; }}

/* ---------- masthead ---------- */
.masthead {{
  display: flex; align-items: flex-end; justify-content: space-between;
  gap: 16px; padding-bottom: 16px; border-bottom: 2px solid var(--ink);
  margin-bottom: 18px; flex-wrap: wrap;
}}
.brand {{ display: flex; align-items: baseline; gap: 10px; }}
.brand .mark {{ font-family: var(--font-display); font-weight: 800; font-size: 29px; color: var(--ink); letter-spacing: -0.01em; }}
.brand .mark em {{ font-style: normal; color: var(--accent); }}
.brand .tag {{
  font-family: var(--font-mono); font-size: 10.5px; font-weight: 500;
  letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-faint);
  border: 1px solid var(--line); border-radius: 4px; padding: 3px 7px;
}}
.meta {{ text-align: right; display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }}
.meta .date {{ font-family: var(--font-mono); font-size: 12.5px; color: var(--ink); font-weight: 600; }}
.live-pill {{
  display: inline-flex; align-items: center; gap: 6px;
  font-family: var(--font-mono); font-size: 10.5px; font-weight: 600;
  letter-spacing: 0.03em; color: var(--ink-dim);
  background: var(--surface-2); border: 1px solid var(--line);
  border-radius: 999px; padding: 3px 9px 3px 7px;
}}
.live-pill .dot {{ width: 6px; height: 6px; border-radius: 50%; background: var(--good); flex: 0 0 auto; }}
@media (prefers-reduced-motion: no-preference) {{
  .live-pill .dot {{ animation: pulse-dot 2.2s ease-in-out infinite; }}
}}
@keyframes pulse-dot {{
  0%, 100% {{ opacity: 1; box-shadow: 0 0 0 0 var(--good-soft); }}
  50% {{ opacity: 0.7; box-shadow: 0 0 0 4px transparent; }}
}}

/* ---------- summary ---------- */
.summary {{
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px;
  background: var(--line); border: 1px solid var(--line); border-radius: 9px;
  overflow: hidden; margin-bottom: 14px; box-shadow: var(--shadow);
}}
.stat {{ background: var(--surface); padding: 13px 16px; display: flex; flex-direction: column; gap: 3px; }}
.stat .n {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-weight: 700; font-size: 23px; color: var(--ink); letter-spacing: -0.01em; }}
.stat .n.accent {{ color: var(--accent); }}
.stat .l {{ font-size: 10.5px; letter-spacing: 0.07em; text-transform: uppercase; color: var(--ink-faint); font-weight: 600; }}

.caveat {{
  font-size: 11.5px; color: var(--ink-faint); margin: 0 0 20px;
  border-left: 2px solid var(--line); padding-left: 10px;
}}

/* ---------- tabs: sticky terminal-style underline strip ---------- */
.tabbar-wrap {{
  position: sticky; top: 0; z-index: 20;
  background: color-mix(in srgb, var(--ground) 92%, transparent);
  -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
  margin: 0 -20px 18px; padding: 8px 20px 0;
  border-bottom: 1px solid var(--line);
}}
.tabbar {{
  display: flex; gap: 2px; overflow-x: auto;
  scrollbar-width: thin;
}}
.tabbar::-webkit-scrollbar {{ height: 4px; }}
.tabbar::-webkit-scrollbar-thumb {{ background: var(--line); border-radius: 3px; }}
.tab {{
  font-family: var(--font-mono); font-size: 12px; font-weight: 600;
  letter-spacing: 0.01em;
  color: var(--ink-faint); background: transparent; border: none;
  border-bottom: 2px solid transparent;
  padding: 10px 12px 9px; white-space: nowrap; cursor: pointer;
  display: flex; align-items: center; gap: 6px; flex: 0 0 auto;
  transition: color 0.12s ease, border-color 0.12s ease;
}}
.tab:hover {{ color: var(--ink); }}
.tab .cnt {{
  font-family: var(--font-mono); font-size: 10px; font-weight: 600;
  background: var(--surface-2); color: var(--ink-faint); border-radius: 999px;
  padding: 1px 6px;
}}
.tab.active {{ color: var(--ink); border-bottom-color: var(--accent); }}
.tab.active .cnt {{ background: var(--accent-soft); color: var(--accent); }}
.tab.top-picks {{ color: var(--accent); }}
.tab.top-picks:hover {{ color: var(--accent-bright); }}
.tab.top-picks.active {{ border-bottom-color: var(--accent); }}

.panel {{ display: none; }}
.panel.active {{ display: block; }}
.panel-head {{
  display: flex; align-items: baseline; justify-content: space-between; gap: 10px;
  margin-bottom: 12px; flex-wrap: wrap;
}}
.panel-head h2 {{ font-family: var(--font-display); font-weight: 700; font-size: 18px; margin: 0; letter-spacing: -0.005em; }}
.panel-head .n {{ font-family: var(--font-mono); font-size: 11.5px; color: var(--ink-faint); }}
.panel-desc {{ font-size: 12.5px; color: var(--ink-dim); margin: -8px 0 14px; max-width: 62ch; }}

/* ---------- pick row ---------- */
.picks {{ display: flex; flex-direction: column; gap: 7px; }}
.pick {{
  background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
  padding: 12px 15px; display: grid;
  grid-template-columns: 32px 1.05fr 1.55fr 158px;
  align-items: center; gap: 14px; box-shadow: var(--shadow);
  cursor: pointer; position: relative;
  transition: border-color 0.12s ease, box-shadow 0.12s ease, transform 0.12s ease;
}}
.pick:hover {{ border-color: var(--accent); box-shadow: var(--shadow-lift); transform: translateY(-1px); }}
.pick:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
@media (prefers-reduced-motion: no-preference) {{
  .pick {{ animation: rise 0.28s ease backwards; }}
}}
@keyframes rise {{ from {{ opacity: 0; transform: translateY(3px); }} to {{ opacity: 1; transform: translateY(0); }} }}

.pick .rank {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; color: var(--ink-faint); font-size: 12.5px; font-weight: 600; }}
.pick .who {{ min-width: 0; }}
.pick .who .name {{ font-family: var(--font-display); font-weight: 700; font-size: 14.5px; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.pick .who .sub {{ font-size: 11.5px; color: var(--ink-faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 1px; }}
.pick .prop-col {{ min-width: 0; }}
.pick .prop {{ font-size: 13.5px; font-weight: 600; color: var(--ink); }}
.pick .odds-col {{ display: flex; flex-direction: column; align-items: flex-end; gap: 5px; width: 100%; }}
.odds-line {{ display: flex; align-items: baseline; gap: 7px; }}
.odds-line .price {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-weight: 700; font-size: 16.5px; color: var(--ink); letter-spacing: -0.01em; }}
.odds-line .price.none {{ color: var(--ink-faint); font-weight: 500; font-size: 13px; }}
.odds-line .fair {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 11px; color: var(--ink-faint); }}
.badges {{ display: flex; gap: 6px; align-items: center; justify-content: flex-end; flex-wrap: wrap; }}
.chip {{ font-family: var(--font-mono); font-size: 10px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; padding: 2.5px 7px; border-radius: 4px; white-space: nowrap; }}
.chip.conf-high {{ background: var(--accent-soft); color: var(--accent); }}
.chip.conf-medium {{ background: var(--surface-2); color: var(--ink-dim); border: 1px solid var(--line); }}
.chip.conf-low {{ background: var(--surface-2); color: var(--ink-faint); border: 1px solid var(--line); }}
.chip.love-badge {{ background: var(--accent); color: var(--accent-ink); font-weight: 700; }}
.pick.love {{ border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft), var(--shadow); }}
.pick.love:hover {{ box-shadow: 0 0 0 2px var(--accent-soft), var(--shadow-lift); }}
.pick.love .who .name {{ color: var(--accent); }}
.meter {{ width: 100%; height: 4px; background: var(--line-soft); border-radius: 2px; margin-top: 6px; position: relative; overflow: visible; }}
.meter .fill {{ position: absolute; inset: 0 auto 0 0; background: var(--accent); border-radius: 2px; }}
.meter .fill.clears {{ background: var(--good); }}
.meter .fill.pass {{ background: var(--bad); }}
.meter .mark {{ position: absolute; top: -2px; width: 2px; height: 8px; background: var(--ink-faint); border-radius: 1px; }}
.prob-row {{ display: flex; align-items: baseline; justify-content: space-between; width: 100%; font-family: var(--font-mono); font-size: 10.5px; color: var(--ink-faint); margin-top: 7px; }}
.prob-row .our {{ color: var(--ink-dim); font-weight: 600; }}
.prob-row .edge {{ font-weight: 700; }}
.prob-row .edge.edge-pos {{ color: var(--good); }}
.prob-row .edge.edge-neg {{ color: var(--bad); }}

.more-btn {{
  display: block; width: 100%; margin-top: 10px; padding: 11px;
  font-family: var(--font-body); font-size: 12.5px; font-weight: 600;
  color: var(--ink-dim); background: var(--surface); border: 1px dashed var(--line);
  border-radius: 8px; cursor: pointer; text-align: center;
  transition: border-color 0.12s, color 0.12s;
}}
.more-btn:hover {{ border-color: var(--accent); color: var(--ink); }}
.pick.hidden-row {{ display: none; }}

.pick .chev {{
  position: absolute; right: 15px; bottom: 12px;
  width: 16px; height: 16px; color: var(--ink-faint);
  transition: transform 0.15s ease;
  pointer-events: none;
}}
.pick.expanded .chev {{ transform: rotate(180deg); color: var(--accent); }}
.explain {{
  grid-column: 1 / -1;
  max-height: 0; overflow: hidden; opacity: 0;
  transition: max-height 0.2s ease, opacity 0.15s ease, margin-top 0.2s ease;
  font-size: 13px; line-height: 1.6; color: var(--ink-dim);
  border-top: 1px dashed var(--line-soft);
}}
.pick.expanded .explain {{
  max-height: 700px; opacity: 1; margin-top: 12px; padding-top: 12px;
}}
.explain b {{ color: var(--ink); font-weight: 600; }}

.empty-state {{
  text-align: center; padding: 48px 20px; color: var(--ink-faint);
  font-size: 13.5px; border: 1px dashed var(--line); border-radius: 8px;
}}

/* A short candidate list explained, not left as blank space below it --
   real context on why a market is thin tonight, not a trimmed list. */
.thin-note {{
  margin-top: 14px; padding: 16px 18px;
  background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px;
  display: flex; flex-direction: column; gap: 10px; align-items: flex-start;
}}
.thin-note p {{ margin: 0; font-size: 12.5px; line-height: 1.55; color: var(--ink-dim); max-width: 62ch; }}
.thin-link {{
  font-family: var(--font-mono); font-size: 11.5px; font-weight: 600;
  color: var(--accent); background: transparent; border: 1px solid var(--accent-soft);
  border-radius: 999px; padding: 6px 12px; cursor: pointer;
  transition: background 0.12s, border-color 0.12s;
}}
.thin-link:hover {{ background: var(--accent-soft); }}

@media (max-width: 680px) {{
  .pick {{ grid-template-columns: 1fr auto; grid-template-areas: "who odds" "prop odds"; row-gap: 8px; }}
  .pick .rank {{ display: none; }}
  .pick .who {{ grid-area: who; }}
  .pick .prop-col {{ grid-area: prop; }}
  .pick .odds-col {{ grid-area: odds; min-width: 112px; }}
  .summary {{ grid-template-columns: repeat(2, 1fr); }}
  .tabbar-wrap {{ margin: 0 -20px 18px; }}
}}

.foot {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--line); font-size: 11.5px; color: var(--ink-faint); line-height: 1.7; }}
.foot strong {{ color: var(--ink-dim); }}
</style>

<div class="wrap">
  <header class="masthead">
    <div class="brand">
      <span class="mark">GRID<em>IRON</em></span>
      <span class="tag">FanDuel &middot; MLB Props</span>
    </div>
    <div class="meta">
      <div class="date" id="board-date">&mdash;</div>
      <span class="live-pill"><span class="dot"></span><span id="board-time">Live-scored &mdash;</span></span>
    </div>
  </header>

  <section class="summary" id="summary"></section>
  <p class="caveat" id="caveat"></p>

  <div class="tabbar-wrap">
    <nav class="tabbar" id="tabbar"></nav>
  </div>
  <main id="panels"></main>

  <footer class="foot">
    <strong>How to read this.</strong> Every tab is one FanDuel prop market, every candidate the
    pipeline scored tonight for it, ranked by the model's calibrated chance of hitting &mdash; not just
    whichever single pick made a curated top-10. &ldquo;Model&rdquo; is that calibrated probability;
    the colored percentage next to it is the edge over FanDuel's posted price. A colored bar means
    the price still clears the pipeline's ROI floor at the pessimistic end of its confidence interval
    (green) or doesn't (red) &mdash; shade shows by how much. Games already underway when this was
    generated are excluded &mdash; their lines are closed. Not financial advice.
  </footer>
</div>

<script>
const PAYLOAD = {payload_json};
const SHOW_N = 25;

function fmtOdds(v) {{
  if (v === null || v === undefined) return null;
  return v > 0 ? "+" + v : String(v);
}}
function pct(v) {{
  if (v === null || v === undefined) return "—";
  return (v * 100).toFixed(1) + "%";
}}
function esc(s) {{
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}}
function confClass(c) {{
  return "conf-" + (c || "medium").toLowerCase();
}}

// ---- plain-English explanations -------------------------------------
// Rewrites the pipeline's own real reasoning strings (why[]/watchouts[])
// into short, flowing sentences instead of technical shorthand. Numbers
// always come straight from the data; nothing here is invented -- a
// bullet this doesn't recognize passes through unchanged rather than
// being guessed at.
const REASON_RULES = [
  [/^Opposing SP ERA ([\\d.]+)$/, m => `the opposing starting pitcher has a ${{m[1]}} ERA`],
  [/^L7 avg EV ([\\d.]+)mph \\(league ~([\\d.]+)\\)$/, m => `over his last 7 days his average exit velocity is ${{m[1]}}mph, a bit below the league average of about ${{m[2]}}mph`],
  [/^L7 barrel% ([\\d.]+)$/, m => `${{m[1]}}% of his batted balls over the last 7 days have been barreled up`],
  [/^Season barrel% ([\\d.]+)/, m => `he's barreling up ${{m[1]}}% of his batted balls this season`],
  [/^Platoon: L bat vs LHP \\((\\w+)\\)$/, m => `he's a lefty hitter facing a left-handed pitcher tonight, ${{m[1] === "unfavorable" ? "typically a tougher matchup" : "typically a good matchup for him"}}`],
  [/^Platoon: R bat vs RHP \\((\\w+)\\)$/, m => `he's a righty hitter facing a right-handed pitcher tonight, ${{m[1] === "unfavorable" ? "typically a tougher matchup" : "typically a good matchup for him"}}`],
  [/^Platoon: L bat vs RHP \\((\\w+)\\)$/, m => `he's a lefty hitter facing a right-handed pitcher tonight, ${{m[1] === "favorable" ? "usually the easier side of the platoon for him" : "a tougher matchup than his platoon splits suggest"}}`],
  [/^Platoon: R bat vs LHP \\((\\w+)\\)$/, m => `he's a righty hitter facing a lefty tonight, ${{m[1] === "favorable" ? "usually the easier side of the platoon for him" : "a tougher matchup than his platoon splits suggest"}}`],
  [/^Market implied team total ([\\d.]+) runs/, m => `the betting market expects his team to score about ${{m[1]}} runs tonight`],
  [/^Wind blowing OUT \\((\\d+)mph\\)/, m => `the wind is blowing out at ${{m[1]}}mph, which helps the ball carry`],
  [/^Wind blowing IN \\((\\d+)mph\\)/, m => `the wind is blowing in at ${{m[1]}}mph, which knocks the ball down`],
  [/^Opposing bullpen fatigue: (\\d+)\\/(\\d+) relievers over 60 pitches in L7/, m => `${{m[1]}} of the other team's last ${{m[2]}} relievers used have been worked hard recently, which tends to favor hitters late`],
  [/^Season SB: (\\d+)$/, m => `he already has ${{m[1]}} stolen bases this season`],
  [/^Sprint speed ([\\d.]+)ft\\/s \\(league ~([\\d.]+)\\)$/, m => `he's a genuinely fast runner (${{m[1]}} ft/s, vs. a league-average runner around ${{m[2]}})`],
  [/^Opposing catcher pop time ([\\d.]+)s to 2B \\(league ~([\\d.]+)s\\)$/, m => `the catcher behind the plate tonight is slow getting the ball to second (${{m[1]}}s, vs. a league-average catcher around ${{m[2]}}s)`],
  [/^Opposing team throws out (\\d+)% of runners/, m => `the opposing team throws out ${{m[1]}}% of runners who try to steal, a genuinely tough team to run on`],
  [/^AVG vs xBA: ([\\d.]+) vs ([\\d.]+) \\(([+-][\\d.]+)\\)/, m => `his batting average (${{m[1]}}) is running ${{parseFloat(m[3]) > 0 ? "a bit above" : "a bit below"}} what the quality of his contact suggests (${{m[2]}}), ${{parseFloat(m[3]) > 0 ? "a mild regression risk" : "a sign he may be due for better luck"}}`],
];
function humanizeReason(s) {{
  for (const [re, fn] of REASON_RULES) {{
    const m = s.match(re);
    if (m) return fn(m);
  }}
  return s.charAt(0).toLowerCase() + s.slice(1);
}}
function capSentence(s) {{
  if (!s) return s;
  const t = s.charAt(0).toUpperCase() + s.slice(1);
  return /[.!?]$/.test(t) ? t : t + ".";
}}
function buildExplanation(p) {{
  const probPct = p.hit_probability != null ? Math.round(p.hit_probability * 100) : null;
  const mktPct = p.market_implied != null ? Math.round(p.market_implied * 100) : null;
  const subject = (p.type === "game" || p.type === "pitcher_combo") ? `this one` : p.name;
  const parts = [];

  if (probPct === null) {{
    parts.push(capSentence(`No usable probability could be computed for this one`));
  }} else if (mktPct !== null) {{
    parts.push(capSentence(
      `The model gives ${{subject}} about ${{probPct}}% to cash "${{p.prop}}" tonight -- FanDuel's price implies roughly ${{mktPct}}%, so this ${{p.price_clears
        ? "is currently rated as real value at the posted line"
        : "isn't rated as strong value at tonight's price, even though the model likes the read"}}`
    ));
  }} else {{
    parts.push(capSentence(`The model gives ${{subject}} about ${{probPct}}% to cash "${{p.prop}}" tonight -- FanDuel hasn't posted a line for this exact prop yet, so there's no price to compare it against`));
  }}

  const reasons = (p.why || []).slice(0, 3).map(humanizeReason);
  if (reasons.length) {{
    reasons.forEach(r => parts.push(capSentence(r)));
  }} else if (p.base_rate != null) {{
    parts.push(capSentence(`he's cleared a bet like this in about ${{Math.round(p.base_rate * 100)}}% of his own games this season, and tonight's matchup is part of why the model likes this spot`));
  }}

  if (p.watchouts && p.watchouts.length) {{
    parts.push(`<b>Worth noting:</b> ${{capSentence(humanizeReason(p.watchouts[0]))}}`);
  }}

  if (p.sample_n != null && p.sample_n > 0 && p.sample_n < 30) {{
    parts.push(capSentence(`this read leans on a smaller sample (${{p.sample_n}} games), so treat it with a little extra caution`));
  }}

  return parts.join(" ");
}}

function pickRow(p, rank) {{
  const marketOdds = fmtOdds(p.market_odds);
  const fairOdds = fmtOdds(p.estimated_odds);
  const oddsClass = p.market_odds === null || p.market_odds === undefined ? "none" : "";
  const oddsText = marketOdds === null ? "NO LINE" : marketOdds;

  const confChip = p.confidence ? `<span class="chip ${{confClass(p.confidence)}}">${{esc(p.confidence)}}</span>` : "";

  const marketPct = p.market_implied !== null && p.market_implied !== undefined ? p.market_implied * 100 : null;
  const ourPct = p.hit_probability !== null && p.hit_probability !== undefined ? p.hit_probability * 100 : 0;

  let fillClass = "";
  let fillOpacity = 1;
  if (p.price_clears === true) fillClass = "clears";
  else if (p.price_clears === false) fillClass = "pass";
  if (fillClass && p.market_edge !== null && p.market_edge !== undefined) {{
    fillOpacity = Math.max(0.4, Math.min(1, Math.abs(p.market_edge) / 0.10));
  }}

  const subLine = p.type === "game" ? "Team prop" : (p.type === "pitcher_combo" ? "Combined · " + (p.matchup || "") : (p.team || p.matchup || ""));

  let edgeHtml = "";
  if (p.market_edge !== null && p.market_edge !== undefined) {{
    const edgePts = p.market_edge * 100;
    const edgeCls = p.price_clears === true ? "edge-pos" : (p.price_clears === false ? "edge-neg" : "");
    const edgeText = (edgePts >= 0 ? "+" : "") + edgePts.toFixed(1) + "%";
    edgeHtml = `<span class="edge ${{edgeCls}}">${{edgeText}} vs mkt</span>`;
  }}

  const isLove = p.confidence === "High" && p.price_clears === true;
  const loveBadge = isLove ? `<span class="chip love-badge">&#128293; Loves This</span>` : "";

  return `
  <div class="pick${{isLove ? " love" : ""}}" tabindex="0" role="button" aria-expanded="false">
    <div class="rank">${{String(rank).padStart(2, "0")}}</div>
    <div class="who">
      <div class="name">${{esc(p.name)}}</div>
      <div class="sub">${{esc(subLine)}}</div>
    </div>
    <div class="prop-col">
      <div class="prop">${{esc(p.prop)}}</div>
    </div>
    <div class="odds-col">
      <div class="odds-line">
        <span class="price ${{oddsClass}}">${{oddsText}}</span>
        ${{fairOdds !== null ? `<span class="fair">fair ${{fairOdds}}</span>` : ""}}
      </div>
      <div class="badges">${{loveBadge}}${{confChip}}</div>
      <div class="meter">
        <div class="fill ${{fillClass}}" style="width:${{ourPct}}%; opacity:${{fillOpacity}}"></div>
        ${{marketPct !== null ? `<div class="mark" style="left:${{marketPct}}%"></div>` : ""}}
      </div>
      <div class="prob-row">
        <span class="our">${{pct(p.hit_probability)}} model</span>
        ${{edgeHtml}}
      </div>
    </div>
    <svg class="chev" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M5 7.5L10 12.5L15 7.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
    <div class="explain">${{buildExplanation(p)}}</div>
  </div>`;
}}

function animateCount(el, target) {{
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || !Number.isFinite(target)) {{ el.textContent = target; return; }}
  const start = performance.now();
  const dur = 600;
  function step(now) {{
    const t = Math.min(1, (now - start) / dur);
    const eased = 1 - Math.pow(1 - t, 3);
    el.textContent = Math.round(target * eased);
    if (t < 1) requestAnimationFrame(step);
    else el.textContent = target;
  }}
  requestAnimationFrame(step);
}}

function renderSummary() {{
  const all = PAYLOAD.data.all;
  let clears = 0, priced = 0;
  for (const p of all) {{
    if (p.price_clears) clears++;
    if (p.market_odds !== null && p.market_odds !== undefined) priced++;
  }}
  const tiles = [
    {{ n: all.length, l: "Candidates Scored" }},
    {{ n: PAYLOAD.tabs_order.length - 2, l: "Prop Markets" }},
    {{ n: priced, l: "With a Live Line" }},
    {{ n: clears, l: "Clear the Price", accent: true }},
  ];
  const el = document.getElementById("summary");
  el.innerHTML = tiles.map((t, i) =>
    `<div class="stat"><div class="n${{t.accent ? " accent" : ""}}" id="stat-n-${{i}}">0</div><div class="l">${{t.l}}</div></div>`
  ).join("");
  tiles.forEach((t, i) => animateCount(document.getElementById("stat-n-" + i), t.n));
}}

function renderTabs() {{
  const bar = document.getElementById("tabbar");
  bar.innerHTML = PAYLOAD.tabs_order.map((key, i) => {{
    const label = PAYLOAD.labels[key];
    const count = PAYLOAD.data[key].length;
    const icon = key === "top_picks" ? "&#127942; " : "";
    return `<button class="tab${{i === 0 ? " active" : ""}}${{key === "top_picks" ? " top-picks" : ""}}" data-tab="${{esc(key)}}">${{icon}}${{esc(label)}} <span class="cnt">${{count}}</span></button>`;
  }}).join("");
  bar.querySelectorAll(".tab").forEach(btn => {{
    btn.addEventListener("click", () => {{
      bar.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
      document.getElementById("panel-" + btn.dataset.tab).classList.add("active");
    }});
  }});
}}

const PANEL_DESC = {{
  top_picks: "The board's real favorites tonight: High-confidence picks that still clear FanDuel's price, ranked by genuine edge over the market -- not just raw probability.",
}};

// Some markets are structurally thin -- not a bug, not a trimmed list, just
// how few real candidates that market produces on a given slate. Explained
// honestly instead of leaving a wall of blank space that reads as broken.
const THIN_NOTES = {{
  strikeouts: {{
    text: "FanDuel posts exactly one strikeout line per starter, and only some of tonight's are priced yet -- every real one that is shows here, not a trimmed list.",
  }},
  combined_strikeouts: {{
    text: "A rarer market by nature: it needs BOTH starters in a game individually priced by FanDuel. Most slates only produce a couple of real matchups like this.",
    related: "strikeouts",
  }},
  pitcher_outs: {{
    text: "Same story as strikeouts -- FanDuel posts one Outs Recorded line per starter, and this is every real one priced tonight.",
    related: "strikeouts",
  }},
  nrfi_combined: {{
    text: "The real combined NRFI/YRFI price needs FanDuel's first-inning market posted for that specific game, which isn't up for every matchup yet.",
  }},
  stolen_base: {{
    text: "Bounded by real speed, not by coverage -- only players who clear a genuine sprint-speed threshold ever become a stolen-base candidate at all.",
  }},
}};
const THIN_THRESHOLD = 10;

function renderPanels() {{
  const el = document.getElementById("panels");
  let html = "";
  PAYLOAD.tabs_order.forEach((key, i) => {{
    const rows = PAYLOAD.data[key];
    const label = PAYLOAD.labels[key];
    const visible = rows.slice(0, SHOW_N);
    const rest = rows.slice(SHOW_N);
    const desc = PANEL_DESC[key] ? `<p class="panel-desc">${{esc(PANEL_DESC[key])}}</p>` : "";
    let body;
    if (rows.length) {{
      const thin = rows.length < THIN_THRESHOLD ? THIN_NOTES[key] : null;
      const thinNote = thin
        ? `<div class="thin-note">
             <p>${{esc(thin.text)}}</p>
             ${{thin.related && PAYLOAD.data[thin.related] ? `<button class="thin-link" data-goto="${{esc(thin.related)}}">Browse ${{esc(PAYLOAD.labels[thin.related])}} instead &rarr;</button>` : ""}}
           </div>`
        : "";
      body = `<div class="picks">
          ${{visible.map((p, j) => pickRow(p, j + 1)).join("")}}
          ${{rest.map((p, j) => pickRow(p, j + 1 + SHOW_N).replace('class="pick', 'class="pick hidden-row')).join("")}}
        </div>
        ${{rest.length ? `<button class="more-btn" data-more="${{esc(key)}}">Show all ${{rows.length}} &darr;</button>` : ""}}
        ${{thinNote}}`;
    }} else {{
      body = `<div class="empty-state">Nothing here right now -- no candidate tonight both clears High confidence and the live price.</div>`;
    }}
    html += `
    <div class="panel${{i === 0 ? " active" : ""}}" id="panel-${{esc(key)}}">
      <div class="panel-head"><h2>${{esc(label)}}</h2><span class="n">${{rows.length}} candidate${{rows.length === 1 ? "" : "s"}}, ranked by ${{key === "top_picks" ? "edge over the market" : "model probability"}}</span></div>
      ${{desc}}
      ${{body}}
    </div>`;
  }});
  el.innerHTML = html;
  el.querySelectorAll(".more-btn").forEach(btn => {{
    btn.addEventListener("click", () => {{
      const panel = document.getElementById("panel-" + btn.dataset.more);
      panel.querySelectorAll(".hidden-row").forEach(r => r.classList.remove("hidden-row"));
      btn.remove();
    }});
  }});
  el.querySelectorAll(".thin-link").forEach(btn => {{
    btn.addEventListener("click", () => {{
      document.querySelector(`.tab[data-tab="${{btn.dataset.goto}}"]`)?.click();
      document.querySelector(".tabbar-wrap")?.scrollIntoView({{ behavior: "smooth", block: "start" }});
    }});
  }});

  function toggleExplain(row) {{
    const open = row.classList.toggle("expanded");
    row.setAttribute("aria-expanded", open ? "true" : "false");
  }}
  el.addEventListener("click", e => {{
    const row = e.target.closest(".pick");
    if (row) toggleExplain(row);
  }});
  el.addEventListener("keydown", e => {{
    if (e.key !== "Enter" && e.key !== " ") return;
    const row = e.target.closest(".pick");
    if (!row) return;
    e.preventDefault();
    toggleExplain(row);
  }});
}}

function renderHeader() {{
  const gen = new Date(PAYLOAD.generated_at);
  const dateStr = gen.toLocaleDateString("en-US", {{ weekday: "long", month: "long", day: "numeric", year: "numeric", timeZone: "America/New_York" }});
  const timeStr = gen.toLocaleTimeString("en-US", {{ hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }});
  document.getElementById("board-date").textContent = dateStr;
  document.getElementById("board-time").textContent = "Updated " + timeStr + " ET";
  document.getElementById("caveat").textContent =
    "Scored fresh against tonight's still-open games only — any game already underway when this ran is excluded, since its FanDuel lines are closed.";
}}

renderHeader();
renderSummary();
renderTabs();
renderPanels();
</script>
"""


def render_html(payload, fonts):
    return PAGE_TEMPLATE.format(
        payload_json=json.dumps(payload, separators=(",", ":")),
        archivo=fonts["archivo"], plexsans=fonts["plexsans"],
        plexmono500=fonts["plexmono500"], plexmono600=fonts["plexmono600"],
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.path.join(DASHBOARD_DIR, "gridiron_board.html"),
                    help="output HTML path (gitignored -- this is generated, not committed)")
    ap.add_argument("--fonts", default=os.path.join(DASHBOARD_DIR, "fonts_b64.json"),
                    help="path to the cached base64 font payload")
    args = ap.parse_args()

    fonts = json.load(open(args.fonts))
    result = run_live_fetch()
    payload = build_payload(result)
    html = render_html(payload, fonts)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    n_total = len(payload["data"]["all"])
    n_top = len(payload["data"]["top_picks"])
    print(f"Wrote {args.out} ({len(html)} bytes, {n_total} candidates, {n_top} top picks)")


if __name__ == "__main__":
    main()

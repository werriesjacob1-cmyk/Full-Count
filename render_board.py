#!/usr/bin/env python3
"""render_board.py — turns output/picks_<date>.json into a single-file HTML
board: a scannable dashboard instead of the dense prose write_markdown()
produces, grouped by section and ranked high-to-low confidence throughout.

    /tmp/mlbvenv/bin/python3 render_board.py [date] [--out PATH]

Reads only the JSON (never re-derives anything), so it can run standalone
against any past day's file. Fields this depends on that are newer than the
file you're pointing it at (why/watchouts, added the same day as this
script) just don't render for that section -- same "absent, not guessed"
rule as everywhere else in this codebase.
"""
import html
import json
import os
import sys
from datetime import datetime

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")

# Mirrors generate_picks.CATEGORY_LABELS. Not imported directly -- same
# reasoning as parlay_builder.py's own duplicated MIN_LINE_PROB: generate_picks
# is a heavy module and this script should stay runnable standalone against
# just a JSON file.
CATEGORY_LABELS = {
    "hits": "Hits", "total_bases": "Total Bases",
    "runs": "Runs", "rbis": "RBIs", "hits_runs_rbis": "Hits+Runs+RBIs",
    "singles": "Singles", "doubles": "Doubles", "triples": "Triples",
    "stolen_base": "Stolen Base", "strikeouts": "Strikeouts",
    "walks": "Walks", "first_inning_run": "First Inning",
    "nrfi_combined": "NRFI/YRFI (Both Teams)", "home_runs": "Home Runs",
}

CONFIDENCE_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def _pct(p):
    return f"{p * 100:.1f}%" if isinstance(p, (int, float)) else "—"


def _odds(n):
    if not isinstance(n, (int, float)):
        return None
    return f"+{int(n)}" if n > 0 else str(int(n))


def _e(s):
    return html.escape(str(s), quote=True)


def _conf_class(conf):
    return {"High": "hi", "Medium": "md", "Low": "lo"}.get(conf, "lo")


def _price_line(p):
    """One honest line about the market price, or None if there isn't one."""
    if p.get("market_odds") is not None:
        clears = p.get("price_clears")
        verdict = "clears" if clears else ("misses" if clears is False else "")
        tag = f'<span class="tag {"good" if clears else "bad"}">{verdict}</span>' if verdict else ""
        return (f'Market {_odds(p["market_odds"])} '
                f'(implied {_pct(p.get("market_implied"))}) {tag}')
    if p.get("estimated_odds") is not None:
        return f'Fair value ~{_odds(p["estimated_odds"])} (no market price found)'
    return None


def _reason_block(p):
    """Real prose reasoning if the JSON carries it (why/watchouts, added
    alongside this script), else a compact fallback from the raw signal
    count so older files still show something rather than nothing."""
    why = p.get("why") or []
    watch = p.get("watchouts") or []
    if why or watch:
        items = "".join(f"<li>{_e(w)}</li>" for w in why)
        out = f'<ul class="why">{items}</ul>' if items else ""
        if watch:
            witems = "".join(f"<li>{_e(w)}</li>" for w in watch)
            out += f'<ul class="watch">{witems}</ul>'
        return out
    n = p.get("notable_signals")
    if n:
        return f'<p class="fallback-reason">{n} converging signal{"s" if n != 1 else ""} behind this number (full reasoning not carried on this file — regenerate to include it).</p>'
    return ""


def _card(p, big=False):
    conf = p.get("confidence") or "Low"
    cls = _conf_class(conf)
    price = _price_line(p)
    reason = _reason_block(p)
    sample = p.get("sample_n")
    rel = p.get("reliability")
    sample_bit = f'{sample} sample · grade {rel}' if sample and rel else (f'{sample} sample' if sample else "")
    lift = p.get("lift")
    lift_bit = (f'<span class="lift {"pos" if lift >= 0 else "neg"}">{"+" if lift >= 0 else ""}{lift * 100:.1f} pts vs base rate</span>'
                if isinstance(lift, (int, float)) else "")
    return f"""
    <article class="card {cls}{' big' if big else ''}">
      <div class="card-top">
        <span class="pill {cls}">{_e(conf)}</span>
        <span class="pct">{_pct(p.get('hit_probability'))}</span>
      </div>
      <h3 class="player">{_e(p.get('name', '—'))}</h3>
      <p class="prop">{_e(p.get('prop', '—'))}</p>
      <p class="matchup">{_e(p.get('team') or '')} · {_e(p.get('matchup', '—'))}</p>
      <div class="meta">
        {f'<span>{_e(price)}</span>' if price else ''}
        {lift_bit}
        {f'<span class="sample">{_e(sample_bit)}</span>' if sample_bit else ''}
      </div>
      {f'<details class="reason"><summary>Why</summary>{reason}</details>' if reason else ''}
    </article>"""


def render(payload, date):
    picks = payload.get("picks", [])
    main_board = sorted([p for p in picks if p.get("category") is None],
                        key=lambda p: (p.get("hit_probability") or 0), reverse=True)
    moonshots = sorted([p for p in picks if p.get("category") == "moonshot"],
                       key=lambda p: (p.get("hit_probability") or 0), reverse=True)
    cat_picks = [p for p in picks if p.get("category") == "best_of_category"]

    by_cat = {}
    for p in cat_picks:
        stat = (p.get("projection") or {}).get("stat")
        by_cat.setdefault(stat, []).append(p)
    # One card per category already (n_per_category=1 upstream), but sort
    # defensively in case that ever changes, then rank the CATEGORIES
    # themselves high-to-low by their best pick's confidence.
    cat_rows = []
    for stat, entries in by_cat.items():
        entries.sort(key=lambda p: (p.get("hit_probability") or 0), reverse=True)
        cat_rows.append((stat, entries[0]))
    cat_rows.sort(key=lambda row: (row[1].get("hit_probability") or 0), reverse=True)

    n_games = len({p.get("game_pk") for p in picks if p.get("game_pk")})
    generated = payload.get("generated", "")
    try:
        gen_fmt = datetime.fromisoformat(generated).strftime("%-I:%M %p")
    except Exception:
        gen_fmt = generated

    category_cards = "\n".join(
        f'<section class="cat-block"><h2>{_e(CATEGORY_LABELS.get(stat, stat or "Other"))}</h2>{_card(top_pick)}</section>'
        for stat, top_pick in cat_rows) or '<p class="empty">No category picks cleared the quality floor today.</p>'

    board_rows = "\n".join(_card(p, big=(i < 3)) for i, p in enumerate(main_board)) or '<p class="empty">No picks yet.</p>'
    moon_rows = "\n".join(_card(p) for p in moonshots) or '<p class="empty">No moonshots today.</p>'

    return TEMPLATE.format(
        date=_e(date), n_games=n_games, gen_fmt=_e(gen_fmt),
        n_board=len(main_board), n_moon=len(moonshots), n_cat=len(cat_rows),
        category_cards=category_cards, board_rows=board_rows, moon_rows=moon_rows,
    )


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Picks Board — {date}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{
  --bg: #f2f5ef; --surface: #ffffff; --surface-2: #e9ede4;
  --text: #16201a; --text-dim: #5b685e; --line: rgba(22,32,26,0.12);
  --accent: #2f8f52; --accent-bg: rgba(47,143,82,0.10);
  --gold: #a9761e; --gold-bg: rgba(169,118,30,0.12);
  --low: #78877e; --low-bg: rgba(120,135,126,0.12);
  --bad: #a1432b;
  --font-display: "Roboto Condensed", "Arial Narrow", "Noto Sans Condensed", sans-serif;
  --font-body: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --font-mono: ui-monospace, "SF Mono", "Cascadia Mono", "Roboto Mono", monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #0f1613; --surface: #161e19; --surface-2: #1d2620;
    --text: #edf1e8; --text-dim: #93a196; --line: rgba(255,255,255,0.10);
    --accent: #5fbf77; --accent-bg: rgba(95,191,119,0.14);
    --gold: #e0ac4c; --gold-bg: rgba(224,172,76,0.14);
    --low: #6f7d73; --low-bg: rgba(111,125,115,0.16);
    --bad: #d97a5c;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #0f1613; --surface: #161e19; --surface-2: #1d2620;
  --text: #edf1e8; --text-dim: #93a196; --line: rgba(255,255,255,0.10);
  --accent: #5fbf77; --accent-bg: rgba(95,191,119,0.14);
  --gold: #e0ac4c; --gold-bg: rgba(224,172,76,0.14);
  --low: #6f7d73; --low-bg: rgba(111,125,115,0.16);
  --bad: #d97a5c;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--text);
  font-family: var(--font-body); line-height: 1.45;
}}
header {{
  position: sticky; top: 0; z-index: 5;
  background: var(--surface); border-bottom: 1px solid var(--line);
  padding: 14px clamp(16px, 4vw, 40px);
  display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap;
}}
header h1 {{
  font-family: var(--font-display); text-transform: uppercase; letter-spacing: 0.04em;
  font-size: clamp(20px, 3vw, 26px); margin: 0; font-weight: 700;
}}
header .stats {{
  font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  color: var(--text-dim); font-size: 13px;
}}
main {{ max-width: 1180px; margin: 0 auto; padding: 28px clamp(16px, 4vw, 40px) 60px; }}
h2 {{
  font-family: var(--font-display); text-transform: uppercase; letter-spacing: 0.05em;
  font-size: 15px; font-weight: 700; color: var(--text-dim); margin: 0 0 10px;
}}
section.stack {{ margin-bottom: 40px; }}
.section-head {{ display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 14px; }}
.section-note {{ font-size: 12.5px; color: var(--text-dim); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }}
.cat-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px 12px; }}
.cat-block h2 {{ margin-bottom: 6px; }}
.card {{
  background: var(--surface); border: 1px solid var(--line); border-radius: 6px;
  padding: 14px; display: flex; flex-direction: column; gap: 6px;
}}
.card.hi {{ border-left: 3px solid var(--accent); }}
.card.md {{ border-left: 3px solid var(--gold); }}
.card.lo {{ border-left: 3px solid var(--low); }}
.card.big {{ background: var(--surface-2); }}
.card-top {{ display: flex; align-items: center; justify-content: space-between; }}
.pill {{
  font-family: var(--font-display); text-transform: uppercase; letter-spacing: 0.04em;
  font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 100px;
}}
.pill.hi {{ background: var(--accent-bg); color: var(--accent); }}
.pill.md {{ background: var(--gold-bg); color: var(--gold); }}
.pill.lo {{ background: var(--low-bg); color: var(--low); }}
.pct {{
  font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  font-size: 22px; font-weight: 600;
}}
.player {{ margin: 2px 0 0; font-size: 16.5px; font-weight: 700; text-wrap: balance; }}
.prop {{ margin: 0; color: var(--text); font-size: 14px; }}
.matchup {{ margin: 0; color: var(--text-dim); font-size: 12.5px; }}
.meta {{
  display: flex; flex-wrap: wrap; gap: 6px 12px; margin-top: 4px;
  font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 12px; color: var(--text-dim);
}}
.tag {{ font-family: var(--font-body); font-size: 11px; padding: 1px 6px; border-radius: 4px; margin-left: 4px; }}
.tag.good {{ background: var(--accent-bg); color: var(--accent); }}
.tag.bad {{ background: rgba(161,67,43,0.12); color: var(--bad); }}
.lift.pos {{ color: var(--accent); }}
.lift.neg {{ color: var(--bad); }}
details.reason {{ margin-top: 4px; font-size: 13px; }}
details.reason summary {{
  cursor: pointer; color: var(--text-dim); font-size: 12px;
  text-transform: uppercase; letter-spacing: 0.04em; font-family: var(--font-display); font-weight: 700;
}}
details.reason summary:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
ul.why, ul.watch {{ margin: 6px 0 0; padding-left: 18px; }}
ul.why li {{ margin-bottom: 3px; }}
ul.watch {{ color: var(--bad); }}
p.fallback-reason {{ margin: 6px 0 0; color: var(--text-dim); font-size: 12.5px; }}
p.empty {{ color: var(--text-dim); font-style: italic; }}
footer {{ max-width: 1180px; margin: 0 auto; padding: 0 clamp(16px, 4vw, 40px) 40px; color: var(--text-dim); font-size: 12px; }}
</style>
</head>
<body>
<header>
  <h1>Picks Board — {date}</h1>
  <div class="stats">{n_games} games · generated {gen_fmt} · {n_board} top board · {n_cat} categories · {n_moon} moonshots</div>
</header>
<main>
  <section class="stack">
    <div class="section-head"><h2>Best pick in every category</h2><span class="section-note">ranked high → low confidence</span></div>
    <div class="cat-grid">
      {category_cards}
    </div>
  </section>

  <section class="stack">
    <div class="section-head"><h2>Top board</h2><span class="section-note">ranked high → low confidence</span></div>
    <div class="grid">
      {board_rows}
    </div>
  </section>

  <section class="stack">
    <div class="section-head"><h2>Moonshots</h2><span class="section-note">home-run bets, ranked high → low confidence</span></div>
    <div class="grid">
      {moon_rows}
    </div>
  </section>
</main>
<footer>No sportsbook odds were used to generate these probabilities where no market price is shown. Verify current lines and availability before betting.</footer>
</body>
</html>
"""


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    date = args[0] if args else datetime.now().strftime("%Y-%m-%d")
    out_path = None
    for a in sys.argv[1:]:
        if a.startswith("--out="):
            out_path = a.split("=", 1)[1]
    in_path = os.path.join(OUTPUT_DIR, f"picks_{date}.json")
    with open(in_path, encoding="utf-8") as f:
        payload = json.load(f)
    html_out = render(payload, date)
    out_path = out_path or os.path.join(OUTPUT_DIR, f"board_{date}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()

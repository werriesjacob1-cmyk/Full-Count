#!/usr/bin/env python3
"""render_full_board.py — every real scored candidate tonight (not just the
10-30 that make the curated picks board), filterable by prop type, each
filter sorted high-to-low confidence. Built because the curated board
(render_board.py) only ever shows ONE pick per category -- this shows all
of them, so a genuinely strong candidate that didn't happen to be #1 in its
category is still visible instead of invisible.

    /tmp/mlbvenv/bin/python3 render_full_board.py [date] [--out PATH]

Reads the same full pool parlay_builder.py already reads (data/players/*.json,
persisted by generate_picks.py's persist_player_snapshots -- every candidate
that was scored, whether or not it made the curated board). No market prices
here: persist_player_snapshots deliberately never carries market_odds (see
its own docstring reasoning), same as parlay_builder's pool -- this is a
research/browsing view of the model's own read, not a bet slip.
"""
import html
import os
import sys
from collections import defaultdict
from datetime import datetime

import parlay_builder as pb

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")

# Mirrors generate_picks.CATEGORY_LABELS / render_board.py's own copy --
# duplicated rather than imported so this stays runnable standalone against
# just the persisted player files (same reasoning as parlay_builder's own
# duplicated MIN_LINE_PROB).
CATEGORY_LABELS = {
    "hits": "Hits", "total_bases": "Total Bases",
    "runs": "Runs", "rbis": "RBIs", "hits_runs_rbis": "Hits+Runs+RBIs",
    "singles": "Singles", "doubles": "Doubles", "triples": "Triples",
    "stolen_base": "Stolen Base", "strikeouts": "Strikeouts",
    "nrfi_combined": "NRFI/YRFI (Both Teams)",
    "hard_hit_105": "Laser (105+ MPH)", "hard_hit_110": "Laser (110+ MPH)",
    "pitcher_outs": "Pitcher Outs Recorded", "home_runs": "Home Runs",
}


def _e(s):
    return html.escape(str(s), quote=True)


def _pct(p):
    return f"{p * 100:.1f}%" if isinstance(p, (int, float)) else "—"


def _confidence(score):
    # Same High >=70 / Medium >=55 / Low bucketing every scorer in
    # generate_picks.py already uses -- not persisted per-candidate, so
    # derived here the same way rather than invented fresh.
    if not isinstance(score, (int, float)):
        return "Low"
    return "High" if score >= 70 else ("Medium" if score >= 55 else "Low")


def _conf_class(conf):
    return {"High": "hi", "Medium": "md", "Low": "lo"}.get(conf, "lo")


def _card(c):
    conf = _confidence(c.get("score"))
    cls = _conf_class(conf)
    lift = c.get("lift")
    lift_bit = ""
    if isinstance(lift, (int, float)):
        lift_bit = (f'<span class="lift {"pos" if lift >= 0 else "neg"}">'
                   f'{"+" if lift >= 0 else ""}{lift * 100:.1f} pts vs base rate</span>')
    sample = c.get("sample_n")
    rel = c.get("reliability")
    sample_bit = f'{sample} sample · grade {rel}' if sample and rel else (f'{sample} sample' if sample else "")
    return f"""
    <article class="card {cls}">
      <div class="card-top">
        <span class="pill {cls}">{_e(conf)}</span>
        <span class="pct">{_pct(c.get('hit_probability'))}</span>
      </div>
      <h3 class="player">{_e(c.get('name', '—'))}</h3>
      <p class="prop">{_e(c.get('prop', '—'))}</p>
      <p class="matchup">{_e(c.get('team') or '')} · {_e(c.get('matchup', '—'))}</p>
      <div class="meta">
        {lift_bit}
        {f'<span class="sample">{_e(sample_bit)}</span>' if sample_bit else ''}
      </div>
    </article>"""


def render(pool, date):
    by_stat = defaultdict(list)
    for c in pool:
        stat = (c.get("projection") or {}).get("stat")
        if stat and c.get("hit_probability") is not None:
            by_stat[stat].append(c)
    for stat in by_stat:
        by_stat[stat].sort(key=lambda c: c["hit_probability"], reverse=True)

    # Tabs ordered by each category's own best confidence, highest first --
    # same "hottest category leads" convention render_board.py's category
    # board already uses, so the two views read consistently.
    tabs = sorted(by_stat.keys(), key=lambda s: by_stat[s][0]["hit_probability"], reverse=True)

    all_sorted = sorted(pool, key=lambda c: c.get("hit_probability") or 0, reverse=True)
    all_sorted = [c for c in all_sorted if c.get("hit_probability") is not None]

    tab_buttons = ['<button class="tab active" data-tab="all">All <span class="cnt">'
                  f'{len(all_sorted)}</span></button>']
    for stat in tabs:
        label = CATEGORY_LABELS.get(stat, stat.replace("_", " ").title())
        tab_buttons.append(f'<button class="tab" data-tab="{_e(stat)}">{_e(label)} '
                          f'<span class="cnt">{len(by_stat[stat])}</span></button>')

    panels = [f'<div class="panel active" data-panel="all"><div class="grid">'
             f'{"".join(_card(c) for c in all_sorted)}</div></div>']
    for stat in tabs:
        label = CATEGORY_LABELS.get(stat, stat.replace("_", " ").title())
        panels.append(f'<div class="panel" data-panel="{_e(stat)}"><div class="grid">'
                     f'{"".join(_card(c) for c in by_stat[stat])}</div></div>')

    return TEMPLATE.format(
        date=_e(date), n_total=len(all_sorted), n_types=len(tabs),
        tab_buttons="\n".join(tab_buttons), panels="\n".join(panels),
    )


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Full Board — {date}</title>
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
  padding: 14px clamp(16px, 4vw, 40px) 0;
}}
header .head-row {{
  display: flex; align-items: baseline; justify-content: space-between; gap: 16px;
  flex-wrap: wrap; padding-bottom: 12px;
}}
header h1 {{
  font-family: var(--font-display); text-transform: uppercase; letter-spacing: 0.04em;
  font-size: clamp(18px, 3vw, 24px); margin: 0; font-weight: 700;
}}
header .stats {{
  font-family: var(--font-mono); font-variant-numeric: tabular-nums;
  color: var(--text-dim); font-size: 13px;
}}
.tabs {{
  display: flex; gap: 6px; overflow-x: auto; padding-bottom: 12px;
  scrollbar-width: thin;
}}
.tab {{
  flex: 0 0 auto; font-family: var(--font-display); text-transform: uppercase;
  letter-spacing: 0.03em; font-size: 12.5px; font-weight: 700; white-space: nowrap;
  background: var(--surface-2); color: var(--text-dim); border: 1px solid var(--line);
  border-radius: 100px; padding: 7px 14px; cursor: pointer;
}}
.tab .cnt {{ font-family: var(--font-mono); font-weight: 400; opacity: 0.75; margin-left: 3px; }}
.tab.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
.tab:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
main {{ max-width: 1180px; margin: 0 auto; padding: 20px clamp(16px, 4vw, 40px) 60px; }}
.panel {{ display: none; }}
.panel.active {{ display: block; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }}
.card {{
  background: var(--surface); border: 1px solid var(--line); border-radius: 6px;
  padding: 14px; display: flex; flex-direction: column; gap: 6px;
}}
.card.hi {{ border-left: 3px solid var(--accent); }}
.card.md {{ border-left: 3px solid var(--gold); }}
.card.lo {{ border-left: 3px solid var(--low); }}
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
.player {{ margin: 2px 0 0; font-size: 16px; font-weight: 700; text-wrap: balance; }}
.prop {{ margin: 0; color: var(--text); font-size: 13.5px; }}
.matchup {{ margin: 0; color: var(--text-dim); font-size: 12px; }}
.meta {{
  display: flex; flex-wrap: wrap; gap: 6px 12px; margin-top: 4px;
  font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 11.5px; color: var(--text-dim);
}}
.lift.pos {{ color: var(--accent); }}
.lift.neg {{ color: var(--bad); }}
p.empty {{ color: var(--text-dim); font-style: italic; padding: 20px 0; }}
</style>
</head>
<body>
<header>
  <div class="head-row">
    <h1>Full Board — {date}</h1>
    <div class="stats">{n_total} candidates · {n_types} prop types</div>
  </div>
  <div class="tabs" role="tablist">
    {tab_buttons}
  </div>
</header>
<main>
  {panels}
</main>
<script>
document.querySelectorAll('.tab').forEach(function(btn) {{
  btn.addEventListener('click', function() {{
    document.querySelectorAll('.tab').forEach(function(b) {{ b.classList.remove('active'); }});
    document.querySelectorAll('.panel').forEach(function(p) {{ p.classList.remove('active'); }});
    btn.classList.add('active');
    document.querySelector('.panel[data-panel="' + btn.dataset.tab + '"]').classList.add('active');
  }});
}});
</script>
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
    pool = pb.load_todays_pool(date=date)
    html_out = render(pool, date)
    out_path = out_path or os.path.join(OUTPUT_DIR, f"full_board_{date}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"Wrote {out_path} ({len(pool)} candidates)")


if __name__ == "__main__":
    main()

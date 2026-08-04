#!/usr/bin/env python3
"""
generate_picks.py — reads the day's mlb_daily.py research package and asks
Claude to synthesize it into the day's top 10 MLB player-prop picks.

Deliberately a separate script from mlb_daily.py: that script's job is
exhaustive, unopinionated data generation; this one's job is the opinionated
synthesis step layered on top. If this step fails for any reason (no API key
yet, a transient API error, a malformed response), it must NOT block the data
package itself from being committed — data generation succeeding is the more
important half of this pipeline, and picks generation is additive on top of it.

Run standalone:  ANTHROPIC_API_KEY=... python3 generate_picks.py
"""
import os, sys
from datetime import datetime

TODAY = datetime.now().strftime("%Y-%m-%d")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")
RESEARCH_FILE = os.environ.get("RESEARCH_FILE") or os.path.join(OUTPUT_DIR, f"mlb_daily_{TODAY}.txt")
PICKS_FILE = os.path.join(OUTPUT_DIR, f"top10_picks_{TODAY}.md")
MODEL = os.environ.get("PICKS_MODEL", "claude-sonnet-5")
MAX_OUTPUT_TOKENS = 6000

SYSTEM_PROMPT = """You are an expert MLB analyst producing today's shortlist of player-prop \
betting ideas for personal research use (Fanatics Sportsbook props), grounded entirely in the \
statistical research package the user provides. You are not placing bets or advising on stake \
sizing — you are ranking and explaining candidate props.

CRITICAL CONSTRAINTS
- The research package does NOT include live sportsbook odds or lines. You cannot compute a \
real betting edge or EV% against an actual price, and you must not state or imply one. Every \
pick must end with a line-check note: "Verify current line and availability on Fanatics before \
betting — no live odds were used to generate this pick."
- Ground every pick in specific data points from the package (cite section numbers/titles and \
the actual numbers, e.g. "L7 avg exit velo 94.2mph (Section 15), CSW% 31.4% (Section 33)"). \
Never invent a stat that isn't in the provided data.
- The document opens with a run summary listing which sections failed or came back empty. \
Do not build a pick on a section marked failed, and treat an empty section as missing \
information, not as a favorable or unfavorable signal.
- Respect the sample-size confidence flags already in the data (🔴 LOW SAMPLE under 50 PA, \
🟡 MED 50-150 PA, 🟢 HIGH 150+ PA). Never build a pick primarily on a 🔴-flagged number — use it \
as color at most.

WHAT TO OPTIMIZE FOR
The user wants picks driven primarily by trends and data convergence — how many independent \
signals point the same direction — not narrowly by a single computed statistical edge. \
Likelihood of the outcome should still inform your confidence rating, just don't let one extreme \
number alone carry a pick. Favor props where multiple independent data layers agree (recent form \
+ matchup + environment + baseline skill), even when no single signal is extreme, over a prop \
that only looks good on one metric.

METHODOLOGY (weight each candidate roughly like this)
  35% MATCHUP        — pitch arsenal vs. batter tendencies, platoon/BvP splits, TTO position
  25% RECENT FORM     — L3/L7/L14 rolling performance and its trend direction
  15% ENVIRONMENT     — weather, wind vs. park orientation, park factors, humidor
  15% BASELINE SKILL   — season-long and multi-year established skill level (not just hot streaks)
  10% CONTEXT         — umpire zone size, bullpen fatigue, lineup slot/protection, rest/travel

Look for convergence (stacking), not addition:
  K PROPS   — high CSW%/Stuff+ starter vs. a high-K% opposing bat, quick pitcher tempo, a \
tight/small-zone umpire, a batter facing him in a TTO-unfavorable slot
  HR/TB PROPS — high Barrel%/HardHit% hitter, park HR factor favorable, wind blowing out, \
favorable platoon split, opposing pitcher with high FB% or elevated hard-contact allowed
  HIT PROPS — high Contact%, a BABIP-regression-due hitter (xBA notably above BA), opposing \
pitcher allowing soft contact, and/or weak opposing defense at the batted-ball profile's landing spots

Actively screen out or downweight negative-edge patterns:
  - Hot recent form contradicted by declining expected stats (xwOBA/xBA trending down) — that's \
BABIP luck, not skill, and is more likely to fade than continue
  - A good ERA undercut by high barrel%/hard-hit% allowed — regression risk
  - An extreme, obvious tailwind (huge wind-out day, get-away-day letdown spot) that a sportsbook \
has almost certainly already priced in — still worth naming, but flag that the market likely \
already reflects it

OUTPUT FORMAT — respond in Markdown, following this structure exactly:

# MLB Top 10 Picks — {date}

One short paragraph (3-5 sentences) on tonight's slate context: weather headlines, notable \
injuries/absences, and the 1-2 standout matchups of the night.

Then exactly 10 numbered picks, ranked by conviction (most confident first):

### N. [Player Name] — [Prop type, e.g. "Over 1.5 Total Bases"]
- **Matchup:** [Away Team @ Home Team], [Away SP] vs [Home SP]
- **Confidence:** High / Medium / Low
- **Why:** 2-4 sentences citing specific numbers and section references from the data.
- **Watch-outs:** what could break this pick before first pitch (lineup change, weather shift, \
bullpen-day risk, late scratch).
- **Line check:** Verify current line/availability on Fanatics before betting — no live odds \
were used to generate this pick.

Close with a short "**What I'd skip tonight**" note: 1-2 props that looked tempting on a single \
strong stat but failed the convergence check, and why — this is meant to show your reasoning, \
not just your conclusions.

If the slate genuinely doesn't support 10 well-grounded picks (very short slate, heavy \
postponements, most lineups unconfirmed), say so explicitly and give fewer, rather than padding \
the list with weak ones just to hit 10."""


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY not set — skipping picks generation. "
              "(Data package is unaffected; add the secret in repo Settings > Secrets and "
              "variables > Actions to enable this step.)")
        return 0

    if not os.path.exists(RESEARCH_FILE):
        print(f"Research file not found: {RESEARCH_FILE} — skipping picks generation.")
        return 0

    with open(RESEARCH_FILE, "r", encoding="utf-8") as f:
        research = f.read()

    kb = len(research) / 1024
    print(f"Research package: {kb:.0f} KB, {len(research)} chars")
    if len(research.strip()) < 500:
        print("Research package looks too small to be a real run — skipping picks generation.")
        return 0

    try:
        import anthropic
    except ImportError:
        print("anthropic package not installed (check requirements.txt) — skipping picks generation.")
        return 0

    client = anthropic.Anthropic(api_key=api_key)
    user_content = (
        f"Today's date: {TODAY}\n\n"
        f"Below is today's full MLB research package ({len(research)} characters). "
        f"Produce today's top 10 picks per your instructions.\n\n"
        f"{research}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT.replace("{date}", TODAY),
            messages=[{"role": "user", "content": user_content}],
        )
        picks_text = "".join(block.text for block in response.content if hasattr(block, "text"))
        if not picks_text.strip():
            raise ValueError("Model returned an empty response")
    except Exception as e:
        print(f"Picks generation failed: {e}")
        with open(PICKS_FILE, "w", encoding="utf-8") as f:
            f.write(f"# MLB Top 10 Picks — {TODAY}\n\n"
                     f"Picks generation failed this run: {e}\n\n"
                     f"The underlying research package is still available at `{RESEARCH_FILE}`.\n")
        return 0

    if getattr(response, "stop_reason", None) == "max_tokens":
        picks_text += ("\n\n---\n*Note: this response hit the output token limit "
                        f"({MAX_OUTPUT_TOKENS}) and may be cut off mid-pick.*\n")
        print(f"WARNING: response truncated at max_tokens={MAX_OUTPUT_TOKENS}")

    with open(PICKS_FILE, "w", encoding="utf-8") as f:
        f.write(picks_text)

    usage = getattr(response, "usage", None)
    if usage:
        print(f"Token usage: {usage.input_tokens} in / {usage.output_tokens} out")
    print(f"Picks written to {PICKS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

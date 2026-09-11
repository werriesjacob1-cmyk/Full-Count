#!/usr/bin/env python3
"""Run one raw NFL world-state capture vintage.

    python3 -m nfl.archive.capture --vintage morning
    python3 -m nfl.archive.capture --vintage inactive-window --slate-date 2026-09-13
    python3 -m nfl.archive.capture --vintage ad-hoc --event-limit 2   # cheap probe

WHAT THIS IS. Priority-ordered raw preservation of what was knowable before
kickoff, per source, with provenance, append-only. What the world looked like is
archived; nothing is interpreted into a number.

WHAT THIS IS NOT. There is no normalization, no canonical candidate identity, no
scoring, no probability, no pick, and no publication. It cannot write outside
nfl/raw/ -- nfl/paths.py refuses -- and it holds no reference to any MLB module,
file, or estate, so no failure here can alter MLB evidence.

FAIL-OPEN THROUGHOUT. A source that breaks yields a SOURCE_FAILED record and the
run continues. The process exits 0 on a partial capture ON PURPOSE: a capture
job that goes red on one flaky feed teaches an operator to ignore it, and a
missing vintage is permanent while a partial one is still evidence. Exit 1 is
reserved for failing to preserve anything at all, which is the only outcome
indistinguishable from never having run.

SOURCE ORDER follows the archival priority order deliberately: the most
perishable state is captured first, so that if the job is killed partway the
thing that survives is the thing that could never have been recovered.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import requests

from nfl.archive import store
from nfl.archive.provenance import (
    CONCLUSIVE_OUTCOMES, Fetched, PARTIAL, SOURCE_FAILED, coverage_summary, utcnow,
)
from nfl.archive.sources import (
    coaching_staff, espn_nfl, fanduel_nfl, media_discovery, official_nfl,
    weather_nws,
)

# (priority, source_id, callable). Priority mirrors the archival order; it is
# recorded in the manifest notes so a later reader can see what this run
# considered most perishable.
SOURCES = (
    (1, "fanduel_nfl", "sportsbook / player-prop state"),
    (2, "official_nfl", "official practice/injury state, transactions, schedule"),
    (3, "espn_nfl", "injury/roster aggregation, venue, consensus market numbers"),
    (6, "coaching_staff_wikipedia", "HC/OC/DC identity, point-in-time reconstructable"),
    (7, "weather_nws", "weather forecast vintages"),
    (9, "media_discovery", "coach/player media discovery + recorded non-coverage"),
)


def _run(label: str, fn, *args, **kwargs) -> list[Fetched]:
    """Call one source. A source that raises must not end the run."""
    try:
        return list(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001 -- fail-open by contract
        return [Fetched(
            source_id=label, artifact="source_module_raised", url="",
            outcome=SOURCE_FAILED,
            failure_reason=f"{type(exc).__name__}: {exc}",
            context={"note": "the source module itself raised; no artifacts "
                             "from it were preserved this run"},
        )]


def run_capture(
    vintage: str,
    slate_date: str | None = None,
    event_limit: int | None = None,
    summary_limit: int | None = None,
    root: str = store.ARCHIVE_ROOT,
) -> tuple[str, dict]:
    slate_date = slate_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    session = requests.Session()
    started_at = utcnow()

    records: list[Fetched] = []
    records += _run("fanduel_nfl", fanduel_nfl.capture,
                    session=session, event_limit=event_limit)
    records += _run("official_nfl", official_nfl.capture, session=session)
    records += _run("espn_nfl", espn_nfl.capture,
                    session=session, summary_event_limit=summary_limit)
    records += _run("coaching_staff_wikipedia", coaching_staff.capture, session=session)
    records += _run("weather_nws", weather_nws.capture, session=session)
    records += _run("media_discovery", media_discovery.capture, session=session)

    notes = {
        "started_at": started_at,
        "finished_at": utcnow(),
        "source_priority_order": [
            {"priority": p, "source_id": s, "covers": c} for p, s, c in SOURCES
        ],
        "scope": {
            "normalization": "none -- payloads stored as received",
            "canonical_candidate_identity": "none, deliberately: raw archival is "
                "decoupled from identity so no later identity decision can "
                "contradict an already-written archive",
            "scoring": "none",
            "picks": "none",
            "publication": "none",
        },
        "known_gaps": [
            "The ACTUAL PLAY CALLER is not established by any source captured "
            "here. Coordinator identity is, via Wikipedia; who calls plays is a "
            "separate and harder fact. See nfl/docs/PLAY_CALLER.md.",
            "No authoritative machine-readable source for the official pregame "
            "inactive list was established. That list is the highest-value NFL "
            "information timestamp and remains an open gap.",
            "Weather forecast vintages are blocked on a verified venue "
            "coordinate table, which NFL-01 did not invent.",
            "Press-conference transcripts and captions are not retrieved; terms "
            "for automated access are UNKNOWN-REQUIRES-REVIEW.",
        ],
    }

    path = store.write_capture(
        slate_date, vintage, records, root=root, notes=notes,
    )
    return path, coverage_summary(records)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--vintage", required=True, choices=store.VINTAGES)
    parser.add_argument("--slate-date", default=None,
                        help="UTC date label for the archive directory (default: today)")
    parser.add_argument("--event-limit", type=int, default=None,
                        help="cap FanDuel events (probe runs only)")
    parser.add_argument("--summary-limit", type=int, default=None,
                        help="cap ESPN per-event summaries")
    parser.add_argument("--root", default=store.ARCHIVE_ROOT)
    args = parser.parse_args(argv[1:])

    path, coverage = run_capture(
        args.vintage, args.slate_date, args.event_limit, args.summary_limit,
        args.root,
    )

    print(f"capture written: {path}")
    print(json.dumps(coverage, indent=2, sort_keys=True))
    problems = store.verify_capture(path)
    if problems:
        print("\nINTEGRITY PROBLEMS:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("\nintegrity: every archived digest re-derived and matched")

    # Only a capture that preserved NOTHING is a failure. A partial capture is
    # still evidence, and a missing vintage is permanent.
    preserved = sum(
        n for outcome, n in coverage["totals"].items()
        if outcome in CONCLUSIVE_OUTCOMES
    )
    if not preserved:
        print("\nFAIL  no source produced a conclusive observation; nothing was "
              "preserved. This run is indistinguishable from never having run.")
        return 1
    if coverage["sources_with_no_conclusive_observation"]:
        print("\nWARNING  these sources produced NO conclusive observation this "
              "run; their silence must not be read as 'nothing to report':")
        for source in coverage["sources_with_no_conclusive_observation"]:
            print(f"  {source}")
    if coverage.get("sources_with_partial_observation"):
        print("\nWARNING  these sources contain PARTIAL observations: bytes were "
              "preserved, but some expected semantic coverage is ambiguous:")
        for source in coverage["sources_with_partial_observation"]:
            print(f"  {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

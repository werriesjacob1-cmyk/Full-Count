"""live_brain/envelopes.py -- formal EventEnvelope/DeltaEnvelope contracts.

FOUNDATION ONLY. Architecture, contracts, fixtures, small pure primitives --
NOT a new prediction formula, NOT a production deployment. See
`live_brain/README.md` for how this relates to the existing, more mature
design work in `backtest/alive_brain_design.md` and the real measurements
in `backtest/alive_brain_prototype.py`/`backtest/fanduel_live_observer.py`/
`backtest/event_targeted_observer.py` -- this module does NOT re-derive that
architecture; it formalizes the envelope shapes that design describes only
informally (by example payload), and nothing here should be read as
superseding it.

Every field below is grounded in what the existing prototypes ALREADY prove
is real and fetchable (see the field-level comments citing the source), not
invented for this design pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """SUPPORTED BY CURRENT SOURCES vs FUTURE CAPABILITY, kept explicitly
    separate per the governing instruction -- never fake source capability.
    """

    # --- Supported today, proven by alive_brain_prototype.fetch_mlb_state()
    # reading MLB's real live feed (liveData.linescore, liveData.plays.currentPlay).
    GAME_STATE_CHANGE = "game_state_change"          # inning/half/outs/score/baserunners
    PLATE_APPEARANCE_COMPLETE = "plate_appearance_complete"  # from result.eventType/event
    BATTER_CHANGE = "batter_change"                  # matchup.batter changed
    PITCHER_CHANGE = "pitcher_change"                 # matchup.pitcher changed
    LINEUP_TURNOVER = "lineup_turnover"               # offense.battingOrder changed
    FINAL = "final"                                    # gameData.status.abstractGameState == "Final"

    # --- Supported today, proven by fanduel_live_observer.py's real, sustained
    # observation (market_status_change: 286 real transitions in one run).
    ODDS_MARKET_STATUS_CHANGE = "odds_market_status_change"  # suspend / reopen
    ODDS_MARKET_PRICE_CHANGE = "odds_market_price_change"     # NOT YET CONFIRMED --
    # see live_brain/README.md: zero real odds/line VALUE changes have been
    # observed in ~130 minutes of combined real monitoring across three runs
    # (backtest/fanduel_observer_final_report.md). This event type is defined
    # because the DATA SHAPE is known (diff_market_state() already detects it
    # in the prototype), not because the event has been confirmed to occur.

    # --- FUTURE SOURCE CAPABILITY -- not wired to any real fetcher today.
    # Listed so the envelope's event_type field has a place for these WHEN a
    # real source is integrated, never to imply they already work.
    INJURY_STATUS_CHANGE = "injury_status_change"       # future: MLB transactions endpoint exists (mlb_sources.py) but not live-wired
    DELAY = "delay"                                       # future
    RESUME = "resume"                                     # future
    SUSPENSION = "suspension"                             # future
    POSTPONEMENT = "postponement"                         # future
    WEATHER_ROOF_CHANGE = "weather_roof_change"           # future


FUTURE_ONLY_EVENT_TYPES = frozenset({
    EventType.INJURY_STATUS_CHANGE, EventType.DELAY, EventType.RESUME,
    EventType.SUSPENSION, EventType.POSTPONEMENT, EventType.WEATHER_ROOF_CHANGE,
})


@dataclass(frozen=True)
class EventEnvelope:
    """One observed event, from any source, in a common shape.

    Field-by-field grounding (never invented):
      event_id           -- new: a stable id for THIS observation, not the
                             source's own id (a source may not have one).
      source              -- e.g. "mlb_statsapi", "fanduel". Matches the
                             existing module names (mlb_sources.py,
                             odds_fanduel.py) -- do not invent new source
                             names disconnected from the real fetchers.
      source_event_id      -- MLB's own play/event id when present (currentPlay
                             has no single stable id field today per
                             alive_brain_prototype.py -- honestly absent, not
                             fabricated); FanDuel's marketId/runnerId for
                             market events.
      source_sequence       -- optional; MLB's feed does not expose a monotonic
                             sequence number today (verified: fetch_mlb_state()
                             has no such field) -- None is the honest value,
                             not 0.
      observed_at            -- when THIS process fetched the source (wall clock
                             at fetch time, same as alive_brain_prototype's own
                             `elapsed`-timed fetch pattern).
      effective_at             -- when the source claims the event happened, if
                             it says (MLB's feed does not timestamp
                             currentPlay to the second in what's fetched
                             today) -- None when genuinely unknown, never
                             backfilled from observed_at.
      ingested_at                -- when this envelope was constructed (may
                             differ from observed_at if there's a processing
                             queue later).
      game_pk                     -- MLB's real game identifier, used
                             throughout this codebase already (recommendation.py,
                             grade_results.py, etc.) -- reuse, don't rename.
      event_type                   -- see EventType above.
      player_ids                    -- MLB player ids if the event names
                             specific players (batter/pitcher change); empty
                             tuple, not None, when genuinely not applicable.
      team_ids                       -- similarly.
      payload                         -- the raw, source-shaped data (e.g.
                             fetch_mlb_state()'s own dict, or a FanDuel
                             market dict) -- never lossily summarized here;
                             downstream code decides what it needs.
      provenance                       -- free-form dict: fetch latency,
                             which endpoint, anything worth keeping for a
                             future replay/audit (see ordering/replay
                             invariants in test_live_brain_ordering.py).
      schema_version                    -- this envelope shape's own version,
                             bumped on any breaking field change.
    """

    event_id: str
    source: str
    source_event_id: str | None
    observed_at: str  # ISO8601
    ingested_at: str  # ISO8601
    game_pk: int
    event_type: EventType
    payload: dict[str, Any]
    schema_version: str = "1.0.0"
    source_sequence: int | None = None
    effective_at: str | None = None
    player_ids: tuple[int, ...] = field(default_factory=tuple)
    team_ids: tuple[int, ...] = field(default_factory=tuple)
    provenance: dict[str, Any] = field(default_factory=dict)

    def dedupe_key(self) -> tuple[str, str | None, EventType]:
        """Two envelopes with the same (source, source_event_id, event_type)
        are the SAME real-world event observed possibly-twice (e.g. two
        polls landing on either side of a slow response). `source_event_id`
        being None (honestly missing for some MLB event types today) means
        dedupe by event_id alone -- see test_live_brain_ordering.py's
        duplicate-event fixture for what this implies about idempotency
        when source_event_id isn't available.
        """
        return (self.source, self.source_event_id, self.event_type)


@dataclass(frozen=True)
class DeltaEnvelope:
    """A tiny, ordered patch describing what changed for ONE candidate,
    never a full-slate payload. Shape grounded in
    alive_brain_prototype.run()'s real delta_payload (261-391 bytes/cycle,
    measured) -- this formalizes that shape, doesn't invent a new one.

      delta_id            -- new: stable id for this delta.
      schema_version        -- this shape's own version.
      game_pk                -- as above.
      candidate_id             -- Full Count's own existing candidate
                             identity (see candidate_dataset.py /
                             test_candidate_dataset.py's dedupe-identity
                             contract) -- REUSE that identity system, do not
                             invent a second one for Live Brain.
      changed_fields             -- ONLY the fields that changed, e.g.
                             {"market_edge": {"old": 0.03, "new": 0.05}} --
                             never the full candidate row.
      source_event_id              -- the EventEnvelope.event_id that caused
                             this delta, for traceability/replay.
      event_version                  -- monotonically increasing per
                             candidate_id, used for the ordering invariant
                             "newer event version never -> older state"
                             (see test_live_brain_ordering.py).
      reason_codes                    -- e.g. ["settlement_final",
                             "price_moved"] -- machine-readable, not prose.
      created_at                       -- ISO8601.
    """

    delta_id: str
    game_pk: int
    candidate_id: str
    changed_fields: dict[str, dict[str, Any]]
    source_event_id: str
    event_version: int
    reason_codes: tuple[str, ...]
    created_at: str
    schema_version: str = "1.0.0"

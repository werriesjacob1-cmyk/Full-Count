"""Amendment A3: the unit cutoff commitment.

A unit's decision cutoff is fixed ONCE, from an archived raw statsapi schedule
observed no later than a pre-registered, schedule-independent COMMITMENT
DEADLINE (09:00 America/New_York for DAY, 15:00 for NIGHT), and never later than
the cutoff it commits to:

    C_u = E_u(commitment schedule) - 30 minutes

The commitment is content-addressed (``commitment_sha256``) and re-derivable
from its archived schedule. A later schedule observation can never move C_u
later; it can only shorten the unit's lead time or invalidate the unit (see
manifest.build). A commitment built from a schedule observed after the
deadline is refused, so a delay seen later cannot be used to manufacture a
later cutoff.

Independent verifiability: ``anchor.commitment_statement`` is the line to
publish to the external append-only log before the deadline, and
``anchor.verify_commitment_anchor`` checks it. Nothing publishes here; a
commitment without a verified external anchor is not prospective proof, and
every manifest carries CUTOFF_COMMITMENT_NOT_EXTERNALLY_ANCHORED until then.
"""
from datetime import datetime, time, timedelta

import fanduel as FDC
import schedule as SCH
import sources as S

VERSION = "fc-acc-001-s1-cutoff-commitment-1"
DECISION_LEAD = timedelta(minutes=30)
COMMIT_DEADLINE_LOCAL = {"DAY": time(9, 0), "NIGHT": time(15, 0)}


class CutoffError(ValueError):
    pass


def deadline_utc(unit_date, window):
    if window not in COMMIT_DEADLINE_LOCAL:
        raise CutoffError("window must be DAY or NIGHT")
    d = datetime.fromisoformat(unit_date).date()
    local = datetime.combine(d, COMMIT_DEADLINE_LOCAL[window], tzinfo=SCH.UNIT_TZ)
    return local.astimezone(S.ts("2000-01-01T00:00:00Z").tzinfo)


def _z(t):
    return t.isoformat().replace("+00:00", "Z")


def unit_games(sched, unit_date, window):
    return {pk: g["first_pitch_utc"] for pk, g in sched.items()
            if SCH.window_of(g["first_pitch_utc"], unit_date) == window}


def make(archive, schedule_source, unit):
    date, window = unit.get("date"), unit.get("window")
    dl = deadline_utc(date, window)
    try:
        sched = SCH.load(schedule_source, archive, date, not_after=_z(dl))
        games = unit_games(sched, date, window)
    except SCH.ScheduleError as exc:
        raise CutoffError(f"no admissible commitment schedule (observed by the {window} deadline {_z(dl)}): {exc}") from exc
    if not games:
        raise CutoffError("commitment schedule has no games in this unit")
    earliest = min(games.values(), key=S.ts)
    cutoff = S.ts(earliest) - DECISION_LEAD
    if S.ts(schedule_source["completed_at"]) > cutoff:
        raise CutoffError("commitment schedule observed after the cutoff it would commit to")
    body = {"commitment_version": VERSION, "unit": {"date": date, "window": window},
            "schedule_source": schedule_source, "unit_games": dict(sorted(games.items())),
            "earliest_first_pitch_utc": earliest, "cutoff_utc": _z(cutoff),
            "commitment_deadline_utc": _z(dl), "lead_minutes": int(DECISION_LEAD.total_seconds() // 60)}
    return {**body, "commitment_sha256": FDC.canonical_sha256(body)}


def verify(commitment, archive, unit):
    if not isinstance(commitment, dict):
        raise CutoffError("cutoff commitment missing")
    body = {k: v for k, v in commitment.items() if k != "commitment_sha256"}
    if FDC.canonical_sha256(body) != commitment.get("commitment_sha256"):
        raise CutoffError("cutoff commitment hash does not rebuild")
    if commitment.get("unit") != {"date": unit.get("date"), "window": unit.get("window")}:
        raise CutoffError("cutoff commitment is for a different unit")
    if make(archive, commitment["schedule_source"], unit) != commitment:
        raise CutoffError("cutoff commitment does not re-derive from its archived schedule")
    return commitment

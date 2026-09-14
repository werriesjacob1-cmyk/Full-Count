from copy import deepcopy

import pytest

from nfl.prospective.game_market_snapshot import (
    GameMarketSnapshotError,
    seal_game_market_snapshot,
)

SHA = "b" * 64
EVENT = "35601246"
CAPTURED = "2026-09-14T23:30:00Z"
KICKOFF = "2026-09-15T00:15:00Z"
SEALED = "2026-09-14T23:31:00Z"


def record(canonical_market, **overrides):
    value = {
        "sportsbook": "FANDUEL",
        "sport": "NFL",
        "canonical_market": canonical_market,
        "source_market_type": "TYPE",
        "source_market_name": "Primary",
        "event_id": EVENT,
        "market_id": f"{canonical_market}-1",
        "market_time": KICKOFF,
        "captured_at": CAPTURED,
        "source_payload_sha256": SHA,
        "market_status": "OPEN",
        "in_play": False,
    }
    value.update(overrides)
    return value


def full_records():
    return [record("moneyline"), record("spread"), record("game_total")]


def seal(records=None, failures=None, **kwargs):
    return seal_game_market_snapshot(
        full_records() if records is None else records,
        [] if failures is None else failures,
        event_id=kwargs.pop("event_id", EVENT),
        sealed_at=kwargs.pop("sealed_at", SEALED),
        **kwargs,
    )


def test_complete_snapshot_is_deterministic_and_research_only():
    first = seal()
    second = seal(records=list(reversed(full_records())))
    assert first == second
    assert first["research_only"] is True
    assert first["public_eligible"] is False
    assert len(first["snapshot_sha256"]) == 64


def test_partial_success_requires_explicit_failure_for_missing_market():
    records = [record("moneyline"), record("spread")]
    failures = [{"event_id": EVENT, "canonical_market": "game_total", "reason": "MALFORMED"}]
    result = seal(records=records, failures=failures)
    assert len(result["records"]) == 2
    assert result["failures"][0]["canonical_market"] == "game_total"


def test_unaccounted_market_fails_closed():
    with pytest.raises(GameMarketSnapshotError, match="unaccounted primary markets"):
        seal(records=[record("moneyline"), record("spread")])


def test_duplicate_normalized_market_fails_closed():
    with pytest.raises(GameMarketSnapshotError, match="duplicate normalized market"):
        seal(records=[record("moneyline"), record("moneyline"), record("spread")])


def test_record_and_failure_overlap_fails_closed():
    failures = [{"event_id": EVENT, "canonical_market": "spread", "reason": "X"}]
    with pytest.raises(GameMarketSnapshotError, match="both normalized and failed"):
        seal(failures=failures)


def test_event_mismatch_fails_closed_for_record_and_failure():
    with pytest.raises(GameMarketSnapshotError, match="record event_id mismatch"):
        seal(records=[record("moneyline", event_id="other"), record("spread"), record("game_total")])
    failures = [{"event_id": "other", "canonical_market": "game_total", "reason": "X"}]
    with pytest.raises(GameMarketSnapshotError, match="failure event_id mismatch"):
        seal(records=[record("moneyline"), record("spread")], failures=failures)


def test_records_must_share_payload_hash_and_capture_time():
    rows = full_records()
    rows[1]["source_payload_sha256"] = "c" * 64
    with pytest.raises(GameMarketSnapshotError, match="share one source payload SHA"):
        seal(records=rows)

    rows = full_records()
    rows[1]["captured_at"] = "2026-09-14T23:29:00Z"
    with pytest.raises(GameMarketSnapshotError, match="share one captured_at"):
        seal(records=rows)


def test_record_capture_and_seal_must_be_pregame():
    rows = full_records()
    rows[0]["captured_at"] = KICKOFF
    with pytest.raises(GameMarketSnapshotError, match="captured strictly before market_time"):
        seal(records=rows)
    with pytest.raises(GameMarketSnapshotError, match="sealed strictly before market_time"):
        seal(sealed_at=KICKOFF)


def test_seal_cannot_precede_capture():
    with pytest.raises(GameMarketSnapshotError, match="cannot precede captured_at"):
        seal(sealed_at="2026-09-14T23:29:59Z")


def test_in_play_or_closed_market_fails_closed():
    rows = full_records()
    rows[2]["in_play"] = True
    with pytest.raises(GameMarketSnapshotError, match="OPEN and pregame"):
        seal(records=rows)


def test_bad_hash_and_naive_time_fail_closed():
    rows = full_records()
    rows[0]["source_payload_sha256"] = "bad"
    with pytest.raises(GameMarketSnapshotError, match="64 lowercase hex"):
        seal(records=rows)

    rows = full_records()
    rows[0]["captured_at"] = "2026-09-14T23:30:00"
    with pytest.raises(GameMarketSnapshotError, match="timezone-aware"):
        seal(records=rows)


def test_inputs_are_not_mutated():
    records = full_records()
    failures = []
    before_records = deepcopy(records)
    before_failures = deepcopy(failures)
    seal(records=records, failures=failures)
    assert records == before_records
    assert failures == before_failures

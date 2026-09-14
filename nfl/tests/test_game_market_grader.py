from copy import deepcopy

import pytest

from nfl.prospective.game_market_grader import GameMarketGradeError, grade_game_market


SHA = "a" * 64


def market(**overrides):
    value = {
        "event_id": "35601246",
        "market_type": "SPREAD",
        "side": "HOME",
        "line": -2.5,
        "captured_at": "2026-09-14T23:30:00Z",
        "kickoff_at": "2026-09-15T00:15:00Z",
        "source_payload_sha256": SHA,
    }
    value.update(overrides)
    return value


def outcome(**overrides):
    value = {
        "event_id": "35601246",
        "home_score": 27,
        "away_score": 20,
        "final_status": "FINAL",
    }
    value.update(overrides)
    return value


def test_moneyline_home_hit_and_away_miss():
    assert grade_game_market(market(market_type="MONEYLINE", side="HOME", line=None), outcome())["settlement"] == "HIT"
    assert grade_game_market(market(market_type="MONEYLINE", side="AWAY", line=None), outcome())["settlement"] == "MISS"


def test_moneyline_tie_is_unresolved_not_guessed():
    result = grade_game_market(
        market(market_type="MONEYLINE", side="HOME", line=None),
        outcome(home_score=20, away_score=20),
    )
    assert result["settlement"] == "UNRESOLVED_TIE"


@pytest.mark.parametrize(
    "line,expected",
    [(-2.5, "HIT"), (-7.0, "PUSH"), (-7.5, "MISS")],
)
def test_home_spread_hit_push_miss(line, expected):
    assert grade_game_market(market(line=line), outcome())["settlement"] == expected


def test_away_spread_orientation():
    assert grade_game_market(market(side="AWAY", line=7.5), outcome())["settlement"] == "HIT"


@pytest.mark.parametrize(
    "side,line,expected",
    [
        ("OVER", 46.5, "HIT"),
        ("OVER", 47.0, "PUSH"),
        ("UNDER", 47.5, "HIT"),
        ("UNDER", 46.5, "MISS"),
    ],
)
def test_total_settlements(side, line, expected):
    result = grade_game_market(market(market_type="TOTAL", side=side, line=line), outcome())
    assert result["settlement"] == expected


def test_event_mismatch_fails_closed():
    with pytest.raises(GameMarketGradeError, match="event_id mismatch"):
        grade_game_market(market(), outcome(event_id="other"))


def test_capture_must_be_strictly_pregame():
    with pytest.raises(GameMarketGradeError, match="strictly before kickoff"):
        grade_game_market(market(captured_at="2026-09-15T00:15:00Z"), outcome())


def test_naive_time_fails_closed():
    with pytest.raises(GameMarketGradeError, match="timezone-aware"):
        grade_game_market(market(captured_at="2026-09-14T23:30:00"), outcome())


def test_nonfinal_outcome_fails_closed():
    with pytest.raises(GameMarketGradeError, match="not final"):
        grade_game_market(market(), outcome(final_status="IN_PROGRESS"))


@pytest.mark.parametrize("bad_score", [-1, 20.5, True, "20"])
def test_invalid_scores_fail_closed(bad_score):
    with pytest.raises(GameMarketGradeError, match="non-negative integer"):
        grade_game_market(market(), outcome(home_score=bad_score))


def test_bad_market_side_pair_fails_closed():
    with pytest.raises(GameMarketGradeError, match="unsupported side"):
        grade_game_market(market(market_type="TOTAL", side="HOME", line=47.5), outcome())


def test_moneyline_rejects_numeric_line():
    with pytest.raises(GameMarketGradeError, match="moneyline line must be null"):
        grade_game_market(market(market_type="MONEYLINE", side="HOME", line=0), outcome())


def test_spread_requires_numeric_line():
    with pytest.raises(GameMarketGradeError, match="line must be numeric"):
        grade_game_market(market(line=None), outcome())


def test_bad_payload_hash_fails_closed():
    with pytest.raises(GameMarketGradeError, match="64 lowercase hex"):
        grade_game_market(market(source_payload_sha256="xyz"), outcome())


def test_inputs_are_not_mutated_and_grade_is_deterministic():
    m = market()
    o = outcome()
    before_m = deepcopy(m)
    before_o = deepcopy(o)
    first = grade_game_market(m, o)
    second = grade_game_market(m, o)
    assert m == before_m
    assert o == before_o
    assert first == second
    assert len(first["grade_sha256"]) == 64

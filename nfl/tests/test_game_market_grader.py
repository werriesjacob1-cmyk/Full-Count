from copy import deepcopy

import pytest

from nfl.prospective.game_market_grader import GameMarketGradeError, grade_game_market

SHA = "a" * 64


def market(**overrides):
    value = {
        "sportsbook": "FANDUEL",
        "sport": "NFL",
        "canonical_market": "spread",
        "source_market_type": "MATCH_HANDICAP_(2-WAY)",
        "source_market_name": "Point Spread",
        "event_id": "35601246",
        "market_id": "spread-1",
        "market_time": "2026-09-15T00:15:00Z",
        "captured_at": "2026-09-14T23:30:00Z",
        "source_payload_sha256": SHA,
        "market_status": "OPEN",
        "in_play": False,
        "away_handicap": 2.5,
        "home_handicap": -2.5,
        "away_odds": -115,
        "home_odds": -105,
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


def moneyline():
    return market(
        canonical_market="moneyline",
        source_market_type="MONEY_LINE",
        market_id="ml-1",
        away_handicap=None,
        home_handicap=None,
    )


def total(line=47.0):
    return market(
        canonical_market="game_total",
        source_market_type="TOTAL_POINTS_(OVER/UNDER)",
        market_id="total-1",
        total=line,
        away_handicap=None,
        home_handicap=None,
    )


def test_moneyline_home_hit_and_away_miss():
    assert grade_game_market(moneyline(), outcome(), side="HOME")["settlement"] == "HIT"
    assert grade_game_market(moneyline(), outcome(), side="AWAY")["settlement"] == "MISS"


def test_moneyline_tie_is_unresolved_not_guessed():
    result = grade_game_market(moneyline(), outcome(home_score=20, away_score=20), side="HOME")
    assert result["settlement"] == "UNRESOLVED_TIE"


@pytest.mark.parametrize(
    "home_line,expected",
    [(-2.5, "HIT"), (-7.0, "PUSH"), (-7.5, "MISS")],
)
def test_home_spread_hit_push_miss(home_line, expected):
    m = market(home_handicap=home_line, away_handicap=-home_line)
    assert grade_game_market(m, outcome(), side="HOME")["settlement"] == expected


def test_away_spread_orientation():
    m = market(home_handicap=-7.5, away_handicap=7.5)
    assert grade_game_market(m, outcome(), side="AWAY")["settlement"] == "HIT"


@pytest.mark.parametrize(
    "side,line,expected",
    [("OVER", 46.5, "HIT"), ("OVER", 47.0, "PUSH"), ("UNDER", 47.5, "HIT"), ("UNDER", 46.5, "MISS")],
)
def test_total_settlements(side, line, expected):
    assert grade_game_market(total(line), outcome(), side=side)["settlement"] == expected


def test_event_mismatch_fails_closed():
    with pytest.raises(GameMarketGradeError, match="event_id mismatch"):
        grade_game_market(market(), outcome(event_id="other"), side="HOME")


def test_capture_must_be_strictly_pregame():
    with pytest.raises(GameMarketGradeError, match="strictly before market_time"):
        grade_game_market(market(captured_at="2026-09-15T00:15:00Z"), outcome(), side="HOME")


def test_naive_time_fails_closed():
    with pytest.raises(GameMarketGradeError, match="timezone-aware"):
        grade_game_market(market(captured_at="2026-09-14T23:30:00"), outcome(), side="HOME")


def test_nonfinal_outcome_fails_closed():
    with pytest.raises(GameMarketGradeError, match="not final"):
        grade_game_market(market(), outcome(final_status="IN_PROGRESS"), side="HOME")


@pytest.mark.parametrize("bad_score", [-1, 20.5, True, "20"])
def test_invalid_scores_fail_closed(bad_score):
    with pytest.raises(GameMarketGradeError, match="non-negative integer"):
        grade_game_market(market(), outcome(home_score=bad_score), side="HOME")


def test_bad_market_side_pair_fails_closed():
    with pytest.raises(GameMarketGradeError, match="unsupported side"):
        grade_game_market(total(47.5), outcome(), side="HOME")


def test_spread_handicaps_must_be_opposites():
    with pytest.raises(GameMarketGradeError, match="must be opposites"):
        grade_game_market(market(home_handicap=-2.5, away_handicap=3.0), outcome(), side="HOME")


def test_total_requires_numeric_line():
    with pytest.raises(GameMarketGradeError, match="total must be numeric"):
        grade_game_market(total(None), outcome(), side="OVER")


def test_market_must_be_open_and_pregame():
    with pytest.raises(GameMarketGradeError, match="OPEN and pregame"):
        grade_game_market(market(in_play=True), outcome(), side="HOME")


def test_bad_payload_hash_fails_closed():
    with pytest.raises(GameMarketGradeError, match="64 lowercase hex"):
        grade_game_market(market(source_payload_sha256="xyz"), outcome(), side="HOME")


def test_inputs_are_not_mutated_and_grade_is_deterministic():
    m = market()
    o = outcome()
    before_m = deepcopy(m)
    before_o = deepcopy(o)
    first = grade_game_market(m, o, side="HOME")
    second = grade_game_market(m, o, side="HOME")
    assert m == before_m
    assert o == before_o
    assert first == second
    assert len(first["grade_sha256"]) == 64


def test_result_preserves_normalized_market_identity():
    result = grade_game_market(market(), outcome(), side="AWAY")
    assert result["event_id"] == "35601246"
    assert result["market_id"] == "spread-1"
    assert result["canonical_market"] == "spread"
    assert result["line"] == 2.5

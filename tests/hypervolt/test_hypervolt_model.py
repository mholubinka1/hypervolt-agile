from datetime import UTC, datetime, time

import pytest
from common.model import ChargeSession
from hypervolt.model import DayOfWeek, HypervoltSession, weekday_to_dayofweek

LONDON = "Europe/London"


def _response(days: list[str]) -> dict:
    return {
        "days": days,
        "start_time": "08:00",
        "end_time": "16:00",
        "mode": "boost",
    }


def test_weekday_to_dayofweek_raises_for_an_out_of_contract_weekday_index() -> None:
    with pytest.raises(KeyError):
        weekday_to_dayofweek(7)


@pytest.mark.parametrize(
    ("weekday_index", "expected_day"),
    [
        (0, DayOfWeek.monday),
        (1, DayOfWeek.tuesday),
        (2, DayOfWeek.wednesday),
        (3, DayOfWeek.thursday),
        (4, DayOfWeek.friday),
        (5, DayOfWeek.saturday),
        (6, DayOfWeek.sunday),
    ],
)
def test_weekday_to_dayofweek_returns_the_matching_day_for_each_valid_weekday_index(
    weekday_index: int, expected_day: DayOfWeek
) -> None:
    assert weekday_to_dayofweek(weekday_index) == expected_day


def test_parse_from_response_sets_the_day_of_week_from_the_single_day_entry() -> None:
    session = HypervoltSession.parse_from_response(_response(["Wednesday"]))

    assert session.day_of_week == DayOfWeek.wednesday


@pytest.mark.parametrize(
    "days",
    [
        [],
        ["Wednesday", "Thursday"],
    ],
)
def test_parse_from_response_raises_when_days_is_not_exactly_one_entry(
    days: list[str],
) -> None:
    with pytest.raises(ValueError):
        HypervoltSession.parse_from_response(_response(days))


def test_create_from_charge_session_returns_a_single_session_when_start_and_end_are_on_the_same_local_day() -> (
    None
):
    # 2024-06-15 is a Saturday; 10:00-11:00 UTC is 11:00-12:00 BST in Europe/London,
    # still Saturday locally.
    charge_session = ChargeSession(
        start=datetime(2024, 6, 15, 10, 0, tzinfo=UTC),
        end=datetime(2024, 6, 15, 11, 0, tzinfo=UTC),
        average_price_per_kwh=0.25,
    )

    sessions = HypervoltSession.create_from_charge_session(
        charge_session, timezone=LONDON
    )

    assert len(sessions) == 1
    assert sessions[0].day_of_week == DayOfWeek.saturday
    assert sessions[0].start == time(11, 0)
    assert sessions[0].end == time(12, 0)


def test_create_from_charge_session_splits_into_two_sessions_when_straddling_local_midnight() -> (
    None
):
    # 22:30-23:30 UTC on 2024-06-15 is 23:30 Saturday to 00:30 Sunday in Europe/London
    # (BST, UTC+1) -- straddling local midnight.
    charge_session = ChargeSession(
        start=datetime(2024, 6, 15, 22, 30, tzinfo=UTC),
        end=datetime(2024, 6, 15, 23, 30, tzinfo=UTC),
        average_price_per_kwh=0.25,
    )

    sessions = HypervoltSession.create_from_charge_session(
        charge_session, timezone=LONDON
    )

    assert len(sessions) == 2
    assert sessions[0].day_of_week == DayOfWeek.saturday
    assert sessions[0].start == time(23, 30)
    assert sessions[0].end == time(0, 0)
    assert sessions[1].day_of_week == DayOfWeek.sunday
    assert sessions[1].start == time(0, 0)
    assert sessions[1].end == time(0, 30)

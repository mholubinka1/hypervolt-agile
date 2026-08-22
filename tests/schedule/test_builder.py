from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from common.model import Price
from schedule.builder import ScheduleBuilder

_DAY = datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC"))


def _half_hour_price(value_exc_vat: float, slot_index: int) -> Price:
    start = _DAY + timedelta(minutes=30 * slot_index)
    return Price(
        value_exc_vat=value_exc_vat,
        valid_from=start,
        valid_to=start + timedelta(minutes=30),
    )


def test_build_merges_contiguous_cheapest_periods_into_one_session() -> None:
    prices = [
        _half_hour_price(10, 0),  # 00:00-00:30, cheapest
        _half_hour_price(20, 1),  # 00:30-01:00, second cheapest
        _half_hour_price(30, 2),  # 01:00-01:30
        _half_hour_price(40, 3),  # 01:30-02:00
    ]
    builder = ScheduleBuilder(duration_hrs=1, limit_exc_vat=100)

    sessions, average_price = builder.build(prices)

    assert len(sessions) == 1
    session = sessions[0]
    assert session.start == _DAY + timedelta(minutes=1)
    assert session.end == _DAY + timedelta(hours=1) - timedelta(minutes=1)
    assert session.average_price_per_kwh == 0.1575
    assert average_price == 0.1575


def test_build_keeps_non_contiguous_cheapest_periods_as_separate_sessions() -> None:
    prices = [
        _half_hour_price(3, 1),  # 00:30-01:00
        _half_hour_price(5, 0),  # 00:00-00:30
        _half_hour_price(20, 2),  # 01:00-01:30, excluded by the limit below
        _half_hour_price(4, 3),  # 01:30-02:00
    ]
    builder = ScheduleBuilder(duration_hrs=1, limit_exc_vat=10)

    sessions, _ = builder.build(prices)

    assert len(sessions) == 2
    assert sessions[0].start == _DAY + timedelta(minutes=31)
    assert sessions[0].end == _DAY + timedelta(hours=1) - timedelta(minutes=1)
    assert sessions[1].start == _DAY + timedelta(hours=1, minutes=31)
    assert sessions[1].end == _DAY + timedelta(hours=2) - timedelta(minutes=1)


def test_build_returns_no_sessions_when_nothing_is_under_the_limit() -> None:
    prices = [_half_hour_price(50, 0), _half_hour_price(60, 1)]
    builder = ScheduleBuilder(duration_hrs=1, limit_exc_vat=10)

    sessions, average_price = builder.build(prices)

    assert sessions == []
    assert average_price is None


def test_build_rounds_up_fractional_half_hour_periods() -> None:
    # 0.75 hours -> 1.5 half-hour periods, which must round up to 2 rather than
    # truncate, so a genuinely fractional duration still gets fully covered.
    builder = ScheduleBuilder(duration_hrs=0.75, limit_exc_vat=100)
    prices = [_half_hour_price(1, 0), _half_hour_price(2, 1), _half_hour_price(3, 2)]

    sessions, _ = builder.build(prices)

    assert len(sessions) == 1
    assert sessions[0].end - sessions[0].start == timedelta(hours=1, minutes=-2)

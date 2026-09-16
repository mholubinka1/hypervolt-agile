from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from common.calendar_window import parse_window_date, window_for_year

_LONDON = ZoneInfo("Europe/London")


def test_parse_window_date_defaults_time_to_midnight() -> None:
    assert parse_window_date("10-31") == (10, 31, 0, 0)


def test_parse_window_date_parses_explicit_time() -> None:
    assert parse_window_date("12-31 06:00") == (12, 31, 6, 0)


def test_parse_window_date_parses_leap_day() -> None:
    assert parse_window_date("02-29") == (2, 29, 0, 0)


@pytest.mark.parametrize("value", ["13-45", "not-a-date", "10-31 25:99", ""])
def test_parse_window_date_rejects_malformed_input(value: str) -> None:
    with pytest.raises(ValueError):
        parse_window_date(value)


@pytest.mark.parametrize("value", ["2-3", "10-3", "3-31", "10-31 6:00", "10-31 06:0"])
def test_parse_window_date_rejects_non_zero_padded_input(value: str) -> None:
    # strptime accepts single-digit month/day/hour/minute even though the
    # documented grammar is exactly "MM-DD" / "MM-DD HH:MM" -- a config typo
    # like this must still fail fast, not silently parse to the wrong thing.
    with pytest.raises(ValueError):
        parse_window_date(value)


def test_window_for_year_keeps_the_end_in_the_same_year_when_end_month_is_not_earlier() -> (
    None
):
    _start, _end = window_for_year((10, 31, 0, 0), (11, 1, 6, 0), anchor_year=2026)

    assert _start == datetime(2026, 10, 31, 0, 0, tzinfo=_LONDON)
    assert _end == datetime(2026, 11, 1, 6, 0, tzinfo=_LONDON)


def test_window_for_year_wraps_the_end_into_the_following_year_when_end_month_is_earlier() -> (
    None
):
    # party_mode-shaped window: spans New Year's Eve, so the end month (1)
    # is earlier than the start month (12) and must roll into anchor_year + 1.
    _start, _end = window_for_year((12, 31, 6, 0), (1, 1, 6, 0), anchor_year=2026)

    assert _start == datetime(2026, 12, 31, 6, 0, tzinfo=_LONDON)
    assert _end == datetime(2027, 1, 1, 6, 0, tzinfo=_LONDON)

import pytest
from hypervolt.led import parse_window_date


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

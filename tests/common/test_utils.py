from datetime import timedelta

from common.utils import format_duration, is_null_or_empty


def test_none_is_null_or_empty() -> None:
    assert is_null_or_empty(None) is True


def test_empty_string_is_null_or_empty() -> None:
    assert is_null_or_empty("") is True


def test_whitespace_only_string_is_null_or_empty() -> None:
    assert is_null_or_empty("   ") is True


def test_non_empty_string_is_not_null_or_empty() -> None:
    assert is_null_or_empty("value") is False


def test_string_with_surrounding_whitespace_is_not_null_or_empty() -> None:
    assert is_null_or_empty("  value  ") is False


# Scenario 8: format_duration renders a timedelta for the LED theme log lines.
def test_duration_of_hours_and_minutes_drops_seconds() -> None:
    assert format_duration(timedelta(hours=2, minutes=58)) == "2h58m"


def test_duration_under_an_hour_is_whole_minutes() -> None:
    assert format_duration(timedelta(minutes=47)) == "47m"


def test_duration_under_a_minute_is_whole_seconds() -> None:
    assert format_duration(timedelta(seconds=38)) == "38s"


def test_exact_hour_pads_minutes_to_two_digits() -> None:
    assert format_duration(timedelta(hours=2)) == "2h00m"


def test_zero_duration_is_zero_seconds() -> None:
    assert format_duration(timedelta(0)) == "0s"


def test_negative_duration_is_zero_seconds() -> None:
    assert format_duration(timedelta(seconds=-5)) == "0s"

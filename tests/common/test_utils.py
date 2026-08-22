from common.utils import is_null_or_empty


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

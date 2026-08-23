from datetime import datetime
from zoneinfo import ZoneInfo

from hypervolt.led import resolve_theme

_LONDON = ZoneInfo("Europe/London")


def test_resolve_theme_returns_halloween_mode_during_its_window() -> None:
    now = datetime(2026, 10, 31, 12, 0, tzinfo=_LONDON)

    theme = resolve_theme(now)

    assert theme is not None
    assert theme.effect_name == "halloween_mode"


def test_resolve_theme_returns_christmas_mode_during_its_window() -> None:
    now = datetime(2026, 12, 25, 9, 0, tzinfo=_LONDON)

    theme = resolve_theme(now)

    assert theme is not None
    assert theme.effect_name == "christmas_mode"


def test_resolve_theme_returns_party_mode_before_midnight_new_years_eve() -> None:
    now = datetime(2026, 12, 31, 20, 0, tzinfo=_LONDON)

    theme = resolve_theme(now)

    assert theme is not None
    assert theme.effect_name == "party_mode"


def test_resolve_theme_returns_party_mode_after_midnight_new_year() -> None:
    # Window wraps the year boundary: still party_mode a few hours into 1 Jan.
    now = datetime(2027, 1, 1, 3, 0, tzinfo=_LONDON)

    theme = resolve_theme(now)

    assert theme is not None
    assert theme.effect_name == "party_mode"


def test_resolve_theme_returns_none_outside_all_windows() -> None:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=_LONDON)

    assert resolve_theme(now) is None


def test_resolve_theme_returns_none_just_after_halloween_window_ends() -> None:
    now = datetime(2026, 11, 1, 6, 0, tzinfo=_LONDON)

    assert resolve_theme(now) is None

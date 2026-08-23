from datetime import datetime
from zoneinfo import ZoneInfo

from hypervolt.led import LedTheme, resolve_theme

_LONDON = ZoneInfo("Europe/London")

_PEACE = LedTheme(effect_name="peace", leds=[{"r": 0.0, "g": 0.34, "b": 0.72}])


def test_resolve_theme_returns_custom_theme_during_its_window() -> None:
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    custom_themes = [(_PEACE, (3, 14, 0, 0), (3, 16, 0, 0))]

    theme = resolve_theme(now, custom_themes=custom_themes)

    assert theme == _PEACE


def test_resolve_theme_prefers_custom_theme_over_built_in_on_same_date() -> None:
    # halloween_mode's window is 31 Oct 00:00 -> 1 Nov 06:00; overlap it deliberately.
    now = datetime(2026, 10, 31, 12, 0, tzinfo=_LONDON)
    custom_themes = [(_PEACE, (10, 30, 0, 0), (11, 2, 0, 0))]

    theme = resolve_theme(now, custom_themes=custom_themes)

    assert theme == _PEACE


def test_resolve_theme_falls_through_to_built_in_when_no_custom_theme_matches() -> None:
    now = datetime(2026, 10, 31, 12, 0, tzinfo=_LONDON)
    custom_themes = [(_PEACE, (3, 14, 0, 0), (3, 16, 0, 0))]

    theme = resolve_theme(now, custom_themes=custom_themes)

    assert theme is not None
    assert theme.effect_name == "halloween_mode"


def test_resolve_theme_uses_config_list_order_when_custom_windows_overlap() -> None:
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    _st_george = LedTheme(
        effect_name="st_george", leds=[{"r": 1.0, "g": 1.0, "b": 1.0}]
    )
    custom_themes = [
        (_PEACE, (3, 14, 0, 0), (3, 16, 0, 0)),
        (_st_george, (3, 14, 0, 0), (3, 16, 0, 0)),
    ]

    theme = resolve_theme(now, custom_themes=custom_themes)

    assert theme == _PEACE

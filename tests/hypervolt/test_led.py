from datetime import datetime
from zoneinfo import ZoneInfo

from hypervolt.led import DEFAULT_BUILT_IN_THEMES, LedTheme, resolve_theme

_LONDON = ZoneInfo("Europe/London")


async def test_resolve_theme_returns_a_defensive_copy_of_the_leds_array() -> None:
    # A stored theme (built-in or custom) is matched by reference internally
    # on every call -- resolve_theme must hand back a copy, not the stored
    # instance, so a caller mutating the returned leds array can't corrupt
    # what the next call returns.
    stored = LedTheme(effect_name="peace", leds=[{"r": 0.0, "g": 0.0, "b": 0.0}])
    custom_themes = [(stored, (3, 14, 0, 0), (3, 16, 0, 0))]
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)

    result_1 = await resolve_theme(now, custom_themes=custom_themes)
    assert result_1 is not None
    assert result_1.leds is not None
    result_1.leds[0]["r"] = 1.0

    result_2 = await resolve_theme(now, custom_themes=custom_themes)
    assert result_2 is not None
    assert result_2.leds is not None
    assert result_2.leds[0]["r"] == 0.0


async def test_resolve_theme_returns_halloween_mode_during_its_window() -> None:
    now = datetime(2026, 10, 31, 12, 0, tzinfo=_LONDON)

    theme = await resolve_theme(now, built_in_themes=DEFAULT_BUILT_IN_THEMES)

    assert theme is not None
    assert theme.effect_name == "halloween_mode"


async def test_resolve_theme_returns_christmas_mode_during_its_window() -> None:
    now = datetime(2026, 12, 25, 9, 0, tzinfo=_LONDON)

    theme = await resolve_theme(now, built_in_themes=DEFAULT_BUILT_IN_THEMES)

    assert theme is not None
    assert theme.effect_name == "christmas_mode"


async def test_resolve_theme_returns_party_mode_before_midnight_new_years_eve() -> None:
    now = datetime(2026, 12, 31, 20, 0, tzinfo=_LONDON)

    theme = await resolve_theme(now, built_in_themes=DEFAULT_BUILT_IN_THEMES)

    assert theme is not None
    assert theme.effect_name == "party_mode"


async def test_resolve_theme_returns_party_mode_after_midnight_new_year() -> None:
    # Window wraps the year boundary: still party_mode a few hours into 1 Jan.
    now = datetime(2027, 1, 1, 3, 0, tzinfo=_LONDON)

    theme = await resolve_theme(now, built_in_themes=DEFAULT_BUILT_IN_THEMES)

    assert theme is not None
    assert theme.effect_name == "party_mode"


async def test_resolve_theme_returns_none_outside_all_windows() -> None:
    now = datetime(2026, 6, 15, 12, 0, tzinfo=_LONDON)

    assert await resolve_theme(now, built_in_themes=DEFAULT_BUILT_IN_THEMES) is None


async def test_resolve_theme_returns_none_just_after_halloween_window_ends() -> None:
    now = datetime(2026, 11, 1, 6, 0, tzinfo=_LONDON)

    assert await resolve_theme(now, built_in_themes=DEFAULT_BUILT_IN_THEMES) is None


async def test_resolve_theme_returns_none_just_before_halloween_window_starts() -> None:
    now = datetime(2026, 10, 30, 23, 59, tzinfo=_LONDON)

    assert await resolve_theme(now, built_in_themes=DEFAULT_BUILT_IN_THEMES) is None


async def test_resolve_theme_returns_halloween_mode_at_exact_window_start() -> None:
    now = datetime(2026, 10, 31, 0, 0, tzinfo=_LONDON)

    theme = await resolve_theme(now, built_in_themes=DEFAULT_BUILT_IN_THEMES)

    assert theme is not None
    assert theme.effect_name == "halloween_mode"


async def test_resolve_theme_returns_none_just_before_christmas_window_starts() -> None:
    now = datetime(2026, 12, 23, 23, 59, tzinfo=_LONDON)

    assert await resolve_theme(now, built_in_themes=DEFAULT_BUILT_IN_THEMES) is None


async def test_resolve_theme_hands_off_from_christmas_to_party_at_the_boundary() -> (
    None
):
    # christmas_mode ends and party_mode starts at the exact same instant.
    now = datetime(2026, 12, 31, 6, 0, tzinfo=_LONDON)

    theme = await resolve_theme(now, built_in_themes=DEFAULT_BUILT_IN_THEMES)

    assert theme is not None
    assert theme.effect_name == "party_mode"

    _just_before = datetime(2026, 12, 31, 5, 59, tzinfo=_LONDON)
    _theme_before = await resolve_theme(
        _just_before, built_in_themes=DEFAULT_BUILT_IN_THEMES
    )
    assert _theme_before is not None
    assert _theme_before.effect_name == "christmas_mode"


async def test_resolve_theme_returns_none_just_after_party_window_ends() -> None:
    now = datetime(2027, 1, 1, 6, 0, tzinfo=_LONDON)

    assert await resolve_theme(now, built_in_themes=DEFAULT_BUILT_IN_THEMES) is None


async def test_resolve_theme_returns_none_during_built_in_window_when_not_configured() -> (
    None
):
    # Proves built-in themes are opt-in: a date that matches a built-in's
    # hardcoded default window still resolves to nothing when the caller
    # doesn't pass built_in_themes at all -- there is no implicit fallback.
    now = datetime(2026, 10, 31, 12, 0, tzinfo=_LONDON)

    assert await resolve_theme(now) is None

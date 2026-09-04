import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from hypervolt.led import ExtensionWrapper, LedTheme, resolve_theme

_LONDON = ZoneInfo("Europe/London")

_PEACE = LedTheme(effect_name="peace", leds=[{"r": 0.0, "g": 0.34, "b": 0.72}])


class _FallbackOnlyProvider:
    """A provider that never matches in the primary pass but offers a
    lower-priority fallback theme (the Saints extension's shape)."""

    def __init__(self, fallback: LedTheme | None) -> None:
        self._fallback = fallback

    async def resolve(self, now: datetime) -> LedTheme | None:
        return None

    async def resolve_fallback(self, now: datetime) -> LedTheme | None:
        return self._fallback


class _PrimaryAndFallbackProvider:
    """Matches in the primary pass and also records every fallback consultation,
    so a test can assert the fallback pass was skipped entirely."""

    def __init__(self, primary: LedTheme | None, fallback: LedTheme | None) -> None:
        self._primary = primary
        self._fallback = fallback
        self.fallback_calls = 0

    async def resolve(self, now: datetime) -> LedTheme | None:
        return self._primary

    async def resolve_fallback(self, now: datetime) -> LedTheme | None:
        self.fallback_calls += 1
        return self._fallback


async def test_resolve_theme_skips_the_fallback_pass_when_the_primary_walk_matches() -> (
    None
):
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    _in_window = LedTheme(effect_name="peace", leds=[{"r": 0.0, "g": 0.34, "b": 0.72}])
    _provider = _PrimaryAndFallbackProvider(primary=_in_window, fallback=_PEACE)
    extensions = [ExtensionWrapper(name="saints_fc", provider=_provider)]

    theme = await resolve_theme(now, extensions=extensions)

    assert theme is not None
    assert theme.effect_name == "peace"
    assert _provider.fallback_calls == 0


async def test_resolve_theme_fallback_uses_config_list_order_when_two_extensions_offer_one() -> (
    None
):
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    _first = LedTheme(effect_name="saints_fc", leds=[{"r": 0.84, "g": 0.1, "b": 0.13}])
    _second = LedTheme(effect_name="other_extension")
    extensions = [
        ExtensionWrapper(name="saints_fc", provider=_FallbackOnlyProvider(_first)),
        ExtensionWrapper(name="other", provider=_FallbackOnlyProvider(_second)),
    ]

    theme = await resolve_theme(now, extensions=extensions)

    assert theme is not None
    assert theme.effect_name == "saints_fc"


class _RaisingFallbackProvider:
    async def resolve(self, now: datetime) -> LedTheme | None:
        return None

    async def resolve_fallback(self, now: datetime) -> LedTheme | None:
        raise RuntimeError("fixtures API unreachable")


async def test_resolve_theme_isolates_a_raising_fallback_and_consults_the_next_extension(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    _strip = LedTheme(effect_name="other_extension")
    extensions = [
        ExtensionWrapper(name="broken", provider=_RaisingFallbackProvider()),
        ExtensionWrapper(name="other", provider=_FallbackOnlyProvider(_strip)),
    ]

    with caplog.at_level(logging.WARNING):
        theme = await resolve_theme(now, extensions=extensions)

    assert theme is not None
    assert theme.effect_name == "other_extension"
    assert len(caplog.records) == 1
    assert "broken" in caplog.records[0].message
    assert "RuntimeError" in caplog.records[0].message


class _PrimaryOnlyProvider:
    """A provider with no resolve_fallback hook at all (a built-in-only
    extension shape)."""

    def __init__(self, theme: LedTheme | None) -> None:
        self._theme = theme

    async def resolve(self, now: datetime) -> LedTheme | None:
        return self._theme


async def test_resolve_theme_fallback_ignores_an_extension_without_the_hook(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    _strip = LedTheme(effect_name="saints_fc")
    extensions = [
        ExtensionWrapper(name="plain", provider=_PrimaryOnlyProvider(None)),
        ExtensionWrapper(name="saints_fc", provider=_FallbackOnlyProvider(_strip)),
    ]

    with caplog.at_level(logging.WARNING):
        theme = await resolve_theme(now, extensions=extensions)

    assert theme is not None
    assert theme.effect_name == "saints_fc"
    assert len(caplog.records) == 0


class _BadTypeFallbackProvider:
    async def resolve(self, now: datetime) -> LedTheme | None:
        return None

    async def resolve_fallback(self, now: datetime) -> object:
        return "nope"


async def test_resolve_theme_treats_a_non_ledtheme_fallback_return_as_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    _strip = LedTheme(effect_name="other_extension")
    extensions = [
        ExtensionWrapper(name="malformed", provider=_BadTypeFallbackProvider()),
        ExtensionWrapper(name="other", provider=_FallbackOnlyProvider(_strip)),
    ]

    with caplog.at_level(logging.WARNING):
        theme = await resolve_theme(now, extensions=extensions)

    assert theme is not None
    assert theme.effect_name == "other_extension"
    assert len(caplog.records) == 1
    assert "malformed" in caplog.records[0].message
    assert "resolve_fallback()" in caplog.records[0].message


async def test_resolve_theme_uses_an_extension_fallback_when_nothing_else_resolves() -> (
    None
):
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    _strip = LedTheme(
        effect_name="saints_fc",
        leds=[{"r": 0.84, "g": 0.1, "b": 0.13}],
        always_on=True,
    )
    extensions = [
        ExtensionWrapper(name="saints_fc", provider=_FallbackOnlyProvider(_strip))
    ]

    theme = await resolve_theme(now, extensions=extensions)

    assert theme is not None
    assert theme.effect_name == "saints_fc"
    assert theme.always_on is True
    assert theme is not _strip  # defensively copied

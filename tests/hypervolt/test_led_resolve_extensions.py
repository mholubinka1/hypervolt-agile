import dataclasses
from datetime import datetime
from zoneinfo import ZoneInfo

from hypervolt.led import ExtensionWrapper, LedTheme, resolve_theme

_LONDON = ZoneInfo("Europe/London")

_PEACE = LedTheme(effect_name="peace", leds=[{"r": 0.0, "g": 0.34, "b": 0.72}])
_SAINTS = LedTheme(
    effect_name="saints_fc_matchday", leds=[{"r": 1.0, "g": 0.0, "b": 0.0}]
)


class _StaticProvider:
    def __init__(self, theme: LedTheme | None) -> None:
        self._theme = theme

    async def resolve(self, now: datetime) -> LedTheme | None:
        return self._theme


class _RaisingProvider:
    async def resolve(self, now: datetime) -> LedTheme | None:
        raise RuntimeError("fixtures API unreachable")


async def test_resolve_theme_prefers_extension_over_matching_custom_theme() -> None:
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    extensions = [ExtensionWrapper(name="saints_fc", provider=_StaticProvider(_SAINTS))]
    custom_themes = [(_PEACE, (3, 14, 0, 0), (3, 16, 0, 0))]

    theme = await resolve_theme(now, extensions=extensions, custom_themes=custom_themes)

    assert theme == _SAINTS


async def test_resolve_theme_carries_the_extensions_active_until_through_the_copy() -> (
    None
):
    # ADR 0020: whatever active_until the provider stamped on its LedTheme
    # survives resolve_theme's defensive copy.
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    _ends_at = datetime(2026, 3, 15, 18, 0, tzinfo=_LONDON)
    _with_end = LedTheme(
        effect_name="saints_fc",
        leds=[{"r": 1.0, "g": 0.0, "b": 0.0}],
        always_on=True,
        active_until=_ends_at,
    )
    extensions = [
        ExtensionWrapper(name="saints_fc", provider=_StaticProvider(_with_end))
    ]

    theme = await resolve_theme(now, extensions=extensions)

    assert theme is not None
    assert theme.active_until == _ends_at


async def test_resolve_theme_leaves_active_until_none_when_the_extension_sets_none() -> (
    None
):
    # ADR 0020: a provider that reports no predicted end (its LedTheme carries
    # active_until=None) resolves to a theme whose active_until is still None
    # -- resolve_theme lifts the provider's value verbatim, it does not invent
    # one.
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    extensions = [ExtensionWrapper(name="saints_fc", provider=_StaticProvider(_SAINTS))]

    theme = await resolve_theme(now, extensions=extensions)

    assert theme is not None
    assert theme.active_until is None


async def test_resolve_theme_uses_config_list_order_when_extensions_both_match() -> (
    None
):
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    _other = LedTheme(effect_name="other_extension")
    extensions = [
        ExtensionWrapper(name="saints_fc", provider=_StaticProvider(_SAINTS)),
        ExtensionWrapper(name="other", provider=_StaticProvider(_other)),
    ]

    theme = await resolve_theme(now, extensions=extensions)

    assert theme == _SAINTS


async def test_resolve_theme_falls_through_to_custom_themes_when_no_extension_matches() -> (
    None
):
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    extensions = [ExtensionWrapper(name="saints_fc", provider=_StaticProvider(None))]
    custom_themes = [(_PEACE, (3, 14, 0, 0), (3, 16, 0, 0))]

    theme = await resolve_theme(now, extensions=extensions, custom_themes=custom_themes)

    assert theme == dataclasses.replace(
        _PEACE, active_until=datetime(2026, 3, 16, 0, 0, tzinfo=_LONDON)
    )


async def test_resolve_theme_falls_through_to_custom_themes_when_extension_raises() -> (
    None
):
    # A regression in the extension loop (e.g. an exception propagating past
    # ExtensionWrapper's isolation instead of being converted to None) must
    # not be able to suppress every lower-priority theme -- only the
    # misbehaving extension itself should ever be affected.
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    extensions = [ExtensionWrapper(name="broken", provider=_RaisingProvider())]
    custom_themes = [(_PEACE, (3, 14, 0, 0), (3, 16, 0, 0))]

    theme = await resolve_theme(now, extensions=extensions, custom_themes=custom_themes)

    assert theme == dataclasses.replace(
        _PEACE, active_until=datetime(2026, 3, 16, 0, 0, tzinfo=_LONDON)
    )

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


async def test_resolve_theme_prefers_extension_over_matching_custom_theme() -> None:
    now = datetime(2026, 3, 15, 12, 0, tzinfo=_LONDON)
    extensions = [ExtensionWrapper(name="saints_fc", provider=_StaticProvider(_SAINTS))]
    custom_themes = [(_PEACE, (3, 14, 0, 0), (3, 16, 0, 0))]

    theme = await resolve_theme(now, extensions=extensions, custom_themes=custom_themes)

    assert theme == _SAINTS


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

    assert theme == _PEACE

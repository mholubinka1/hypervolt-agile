from __future__ import annotations

import logging.config
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from logging import Logger, getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml

# Redundant "as Window" alias, deliberate: mypy's implicit_reexport=false would
# otherwise treat Window as private to this module, breaking external
# importers that still do `from hypervolt.led import Window` (e.g.
# schedule/coordinator.py) now that Window is only imported here, not defined.
from common.calendar_window import DEFAULT_BUILT_IN_THEME_WINDOWS
from common.calendar_window import Window as Window  # noqa: PLC0414
from common.calendar_window import parse_window_date, window_for_year
from common.constants import APP_NAME
from common.extensions import ExtensionWrapper as _GenericExtensionWrapper
from common.extensions import load_extensions as _load_generic_extensions
from common.logging import config

if TYPE_CHECKING:
    from config import BuiltInLedTheme, CustomLedTheme, ExtensionEntry, LedConfig

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

_LED_COUNT = 51
# Custom-theme colour maps live in a `themes/` directory at the repo root
# (this file is app/hypervolt/led.py, so the root is three parents up). The
# app reads from here, not the operator's config directory -- see ADR
# "custom themes move to a repo themes/ directory".
THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"


@dataclass(frozen=True)
class LedTheme:
    # DEFAULT_BUILT_IN_THEMES and load_custom_themes() both hold long-lived singleton
    # instances internally -- freezing this stops a caller from reassigning a
    # field on one of those singletons (resolve_theme()'s defensive copy is
    # what stops nested `leds` list mutation from reaching them, see below).
    effect_name: str
    leds: list[dict[str, float]] | None = None
    # Per-theme display gate (ADR 0014). True: light the charger for the theme's
    # whole active window regardless of charge *or* plug state. False
    # (default): light only while the car is actively charging.
    always_on: bool = False
    # When the resolved theme is expected to stop applying (ADR 0020) -- the
    # matched window's end for a calendar theme, kick-off + 3h for the Saints
    # extension, None when the matching source has no firm end (a charge-gated
    # fallback). Meaningful only on a resolve_theme() result, never on a
    # catalogue entry, so it defaults to None and stays absent on
    # DEFAULT_BUILT_IN_THEMES and the (LedTheme, Window, Window) tuples.
    active_until: datetime | None = None


def _hex_to_rgb(hex_colour: str) -> dict[str, float]:
    _hex = hex_colour.lstrip("#")
    if len(_hex) != 6:
        raise ValueError(f"Invalid hex colour {hex_colour!r}: expected '#RRGGBB'.")
    _r, _g, _b = (int(_hex[i : i + 2], 16) for i in (0, 2, 4))
    return {"r": _r / 255, "g": _g / 255, "b": _b / 255}


def load_custom_effect(path: Path) -> list[dict[str, float]]:
    _content = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(_content, dict) or "default_colour" not in _content:
        raise ValueError(f"{path}: missing required 'default_colour'.")
    _default = _hex_to_rgb(_content["default_colour"])
    _leds = [dict(_default) for _ in range(_LED_COUNT)]
    for segment in _content.get("segments", []):
        if "colour" not in segment:
            raise ValueError(f"{path}: segment missing required 'colour'.")
        _colour = _hex_to_rgb(segment["colour"])
        _indices = list(segment.get("indices", []))
        for _range_start, _range_end in segment.get("ranges", []):
            if _range_start > _range_end:
                raise ValueError(
                    f"{path}: range [{_range_start}, {_range_end}] has end before start."
                )
            if not (0 <= _range_start < _LED_COUNT and 0 <= _range_end < _LED_COUNT):
                raise IndexError(
                    f"{path}: range [{_range_start}, {_range_end}] out of range "
                    f"(0-{_LED_COUNT - 1})."
                )
            _indices.extend(range(_range_start, _range_end + 1))
        for _index in _indices:
            if not 0 <= _index < _LED_COUNT:
                raise IndexError(
                    f"{path}: LED index {_index} out of range (0-{_LED_COUNT - 1})."
                )
            _leds[_index] = dict(_colour)
    return _leds


# DEFAULT_BUILT_IN_THEMES is derived from the shared raw catalog in
# common.calendar_window rather than hand-written here -- this file no longer
# owns the date-window shape, but its own DEFAULT_BUILT_IN_THEMES export is
# still what other modules depend on (tests/hypervolt/test_led.py and
# tests/hypervolt/test_led_resolve_custom_themes.py both import it, and
# resolve_theme()/load_built_in_themes() elsewhere in this file work with
# (LedTheme, Window, Window) tuples), so it's rebuilt here as LedTheme-wrapped
# tuples rather than dropped.
DEFAULT_BUILT_IN_THEMES: list[tuple[LedTheme, Window, Window]] = [
    (LedTheme(effect_name=name), start, end)
    for name, start, end in DEFAULT_BUILT_IN_THEME_WINDOWS
]


def _resolve_from(
    now: datetime, entries: Sequence[tuple[LedTheme, Window, Window]]
) -> tuple[LedTheme, datetime] | None:
    # Returns the matched theme together with the end of the window that
    # matched -- resolve_theme stamps that end onto the copy it hands back as
    # the theme's active_until (ADR 0020).
    for theme, start, end in entries:
        for anchor_year in (now.year, now.year - 1):
            _start, _end = window_for_year(start, end, anchor_year)
            if _start <= now < _end:
                return theme, _end
    return None


async def resolve_theme(
    now: datetime,
    extensions: Sequence[ExtensionWrapper] = (),
    custom_themes: Sequence[tuple[LedTheme, Window, Window]] = (),
    built_in_themes: Sequence[tuple[LedTheme, Window, Window]] = (),
) -> LedTheme | None:
    _match: LedTheme | None = None
    # Set only on a calendar (custom / built-in) match -- the end of the window
    # that matched. An extension match reports its own end via the active_until
    # already on the LedTheme it returned (see below).
    _window_end: datetime | None = None
    for _extension in extensions:
        _match = await _extension.resolve(now)
        if _match is not None:
            break
    if _match is None:
        _calendar_match = _resolve_from(now, custom_themes)
        if _calendar_match is None:
            _calendar_match = _resolve_from(now, built_in_themes)
        if _calendar_match is not None:
            _match, _window_end = _calendar_match
    if _match is None:
        for _extension in extensions:
            _match = await _extension.resolve_fallback(now)
            if _match is not None:
                break
    if _match is None:
        return None
    # When the resolved theme is expected to stop applying (ADR 0020): the
    # matched window's end for a calendar theme, else whatever active_until the
    # matching extension stamped on the theme it returned (may be None).
    _active_until = _window_end if _window_end is not None else _match.active_until
    # _match above is the stored/cached LedTheme itself -- from the caller's
    # custom_themes, built_in_themes, or an extension's own internal cache --
    # not a copy -- freezing the dataclass only stops field reassignment, not
    # mutation of the nested `leds` list, so building a fresh LedTheme with a
    # deep-copied `leds` below is the only real protection against a caller
    # corrupting what a later cycle resolves to.
    return LedTheme(
        effect_name=_match.effect_name,
        leds=[dict(led) for led in _match.leds] if _match.leds is not None else None,
        always_on=_match.always_on,
        active_until=_active_until,
    )


class LedThemeProvider(Protocol):
    def __init__(self, config: dict[str, Any]) -> None: ...

    async def resolve(self, now: datetime) -> LedTheme | None: ...

    # A returned LedTheme's `active_until`, when set, should be a timezone-aware
    # datetime (ADR 0020) -- the coordinator compares it against an aware `now`.
    # A naive value is tolerated, not required: the coordinator drops the
    # predicted-end portion of its log line rather than raising.
    #
    # start() and stop() are optional lifecycle hooks (ADR 0005) -- deliberately
    # not declared here, since a Protocol member would make them structurally
    # required. Callers check `hasattr` instead of relying on isinstance.
    #
    # `async def resolve_fallback(self, now: datetime) -> LedTheme | None` is an
    # optional second-pass hook (ADR 0015), declared the same way -- a comment,
    # not a Protocol member -- and reached via `hasattr`. resolve_theme consults
    # it only when the primary walk (extensions -> custom -> built-in themes)
    # found nothing, so an extension implementing it can rank *below* the theme
    # tiers on a fallback pass while its resolve() still ranks above them.


def _validate_led_theme_result(result: Any, method_name: str) -> LedTheme | None:
    # Raised here so a misbehaving extension's bad return value is funnelled
    # through the generic wrapper's own isolation/dedup handling (it calls
    # this via _LedThemeValidatingProvider below) rather than propagating to
    # crash resolve_theme's own .effect_name access.
    if result is not None and not isinstance(result, LedTheme):
        raise TypeError(
            f"{method_name}() returned {type(result).__name__}, expected "
            "LedTheme or None"
        )
    return result


class _LedThemeValidatingProvider:
    # Wraps a LedThemeProvider so its results are validated before the
    # generic ExtensionWrapper's isolation/dedup logic ever sees them -- the
    # generic wrapper has no notion of LedTheme, so this is where that check
    # has to live. Any other attribute (start, stop, ...) is delegated to the
    # real provider untouched.
    def __init__(self, provider: LedThemeProvider) -> None:
        self._provider = provider

    async def resolve(self, now: datetime) -> LedTheme | None:
        return _validate_led_theme_result(await self._provider.resolve(now), "resolve")

    async def resolve_fallback(self, now: datetime) -> LedTheme | None:
        # resolve_fallback is deliberately not a Protocol member (see
        # LedThemeProvider above) -- narrow via hasattr so mypy knows the
        # attribute exists on this branch, same as the ExtensionWrapper-level
        # guard that decides whether to call this method at all.
        if not hasattr(self._provider, "resolve_fallback"):
            return None
        return _validate_led_theme_result(
            await self._provider.resolve_fallback(now), "resolve_fallback"
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)


# Preserves the original "LED theme extension ..." log wording verbatim
# now that the shared wrapper's messages are kind-labelled rather than
# LED-specific by default (ADR 0017) -- a second provider kind would supply
# its own label instead of this one.
_LED_EXTENSION_KIND = "LED theme extension"


class ExtensionWrapper:
    # A thin LED-specific adapter over common.extensions.ExtensionWrapper --
    # the dynamic-import machinery, path-traversal guard, and per-method
    # isolation/dedup logging all live in the shared module now (ADR 0017).
    # This layer keeps the LED-typed `.resolve(now)` / `.resolve_fallback(now)`
    # call sites main.py and the coordinator already depend on.
    def __init__(self, name: str, provider: LedThemeProvider) -> None:
        self.name = name
        self._provider = provider
        self._generic = _GenericExtensionWrapper(
            name=name,
            provider=_LedThemeValidatingProvider(provider),
            kind=_LED_EXTENSION_KIND,
        )

    async def resolve(self, now: datetime) -> LedTheme | None:
        return await self._generic.invoke("resolve", now)

    async def resolve_fallback(self, now: datetime) -> LedTheme | None:
        # Optional second-pass hook (ADR 0015) -- absent on most providers, so
        # guarded like stop() rather than assumed present. Checked against the
        # real provider, not the validating wrapper, which always defines the
        # method regardless of whether the wrapped provider does.
        if not hasattr(self._provider, "resolve_fallback"):
            return None
        return await self._generic.invoke("resolve_fallback", now)

    async def stop(self) -> None:
        await self._generic.stop()


async def load_extensions(
    entries: Sequence[ExtensionEntry], extensions_dir: Path
) -> list[ExtensionWrapper]:
    _generic_wrappers = await _load_generic_extensions(
        entries, extensions_dir, marker_method="resolve", kind=_LED_EXTENSION_KIND
    )
    return [
        ExtensionWrapper(name=_wrapper.name, provider=_wrapper.provider)
        for _wrapper in _generic_wrappers
    ]


def load_custom_themes(
    entries: Sequence[CustomLedTheme], themes_dir: Path
) -> list[tuple[LedTheme, Window, Window]]:
    _loaded: list[tuple[LedTheme, Window, Window]] = []
    for entry in entries:
        try:
            _leds = load_custom_effect(themes_dir / f"{entry.effect}.yaml")
        except Exception as e:
            logger.error(
                f"Failed to load custom LED theme {entry.effect!r}: {type(e).__name__}: {e}."
            )
            continue
        _theme = LedTheme(
            effect_name=entry.effect, leds=_leds, always_on=entry.always_on
        )
        _loaded.append(
            (_theme, parse_window_date(entry.start), parse_window_date(entry.end))
        )
    return _loaded


def load_custom_themes_for_config(
    led_config: LedConfig | None, themes_dir: Path
) -> list[tuple[LedTheme, Window, Window]]:
    if led_config is None:
        return []
    return load_custom_themes(led_config.custom_themes, themes_dir)


def load_built_in_themes(
    entries: Sequence[BuiltInLedTheme],
) -> list[tuple[LedTheme, Window, Window]]:
    return [
        (
            LedTheme(effect_name=entry.effect, always_on=entry.always_on),
            parse_window_date(entry.start),
            parse_window_date(entry.end),
        )
        for entry in entries
    ]


def load_built_in_themes_for_config(
    led_config: LedConfig | None,
) -> list[tuple[LedTheme, Window, Window]]:
    if led_config is None:
        return []
    return load_built_in_themes(led_config.built_in_themes)

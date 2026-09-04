from __future__ import annotations

import importlib.util
import inspect
import logging.config
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from logging import Logger, getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from zoneinfo import ZoneInfo

import yaml
from common.constants import APP_NAME, TIMEZONE
from common.logging import config

if TYPE_CHECKING:
    from config import BuiltInLedTheme, CustomLedTheme, ExtensionEntry, LedConfig

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

_LOCAL_TZ = ZoneInfo(TIMEZONE)
_LED_COUNT = 51
# Custom-theme colour maps live in a `themes/` directory at the repo root
# (this file is app/hypervolt/led.py, so the root is three parents up). The
# app reads from here, not the operator's config directory -- see ADR
# "custom themes move to a repo themes/ directory".
THEMES_DIR = Path(__file__).resolve().parents[2] / "themes"
# A fixed leap year, shared by parse_window_date (so "02-29" parses) and by
# config.py's end_must_be_after_start validator (so its chronological check
# uses the same reference year as everything else that materialises a Window
# into real dates) -- one named constant instead of the literal 2000 living
# in two places.
REFERENCE_ANCHOR_YEAR = 2000


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


# (theme, (start_month, start_day, start_hour, start_minute), (end_month, end_day, end_hour, end_minute))
# Year-agnostic, London local time. A window whose end month is earlier than its
# start month wraps into the following year (e.g. party_mode spans New Year's Eve).
Window = tuple[int, int, int, int]
DEFAULT_BUILT_IN_THEMES: list[tuple[LedTheme, Window, Window]] = [
    (LedTheme(effect_name="halloween_mode"), (10, 31, 0, 0), (11, 1, 6, 0)),
    (LedTheme(effect_name="christmas_mode"), (12, 24, 0, 0), (12, 31, 6, 0)),
    (LedTheme(effect_name="party_mode"), (12, 31, 6, 0), (1, 1, 6, 0)),
]


def parse_window_date(value: str) -> Window:
    # REFERENCE_ANCHOR_YEAR avoids Python's day-without-year parsing ambiguity
    # (deprecated in 3.15) and lets "02-29" parse as a valid window boundary.
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            # Naive is fine here -- only the calendar fields are used, the
            # result is never compared as an actual instant.
            _parsed = datetime.strptime(  # noqa: DTZ007
                f"{REFERENCE_ANCHOR_YEAR}-{value}", fmt
            )
            # strptime accepts single-digit fields ("2-3", "10-31 6:0") even
            # though the documented grammar is strictly zero-padded -- a
            # round-trip through the same format catches that silently-loose
            # input, since re-formatting a valid one always yields it back.
            if _parsed.strftime(fmt) != f"{REFERENCE_ANCHOR_YEAR}-{value}":
                continue
            return (_parsed.month, _parsed.day, _parsed.hour, _parsed.minute)
        except ValueError:
            continue
    raise ValueError(
        f"Invalid date window {value!r}: expected 'MM-DD' or 'MM-DD HH:MM'."
    )


def window_for_year(
    start: Window, end: Window, anchor_year: int
) -> tuple[datetime, datetime]:
    # Public: also called from config.py's end_must_be_after_start validator
    # to check chronological ordering at config-load time, not just here.
    start_month, start_day, start_hour, start_minute = start
    end_month, end_day, end_hour, end_minute = end
    _start = datetime(
        anchor_year, start_month, start_day, start_hour, start_minute, tzinfo=_LOCAL_TZ
    )
    _end_year = anchor_year + 1 if end_month < start_month else anchor_year
    _end = datetime(
        _end_year, end_month, end_day, end_hour, end_minute, tzinfo=_LOCAL_TZ
    )
    return _start, _end


def _resolve_from(
    now: datetime, entries: Sequence[tuple[LedTheme, Window, Window]]
) -> LedTheme | None:
    for theme, start, end in entries:
        for anchor_year in (now.year, now.year - 1):
            _start, _end = window_for_year(start, end, anchor_year)
            if _start <= now < _end:
                return theme
    return None


async def resolve_theme(
    now: datetime,
    extensions: Sequence[ExtensionWrapper] = (),
    custom_themes: Sequence[tuple[LedTheme, Window, Window]] = (),
    built_in_themes: Sequence[tuple[LedTheme, Window, Window]] = (),
) -> LedTheme | None:
    _match: LedTheme | None = None
    for _extension in extensions:
        _match = await _extension.resolve(now)
        if _match is not None:
            break
    if _match is None:
        _match = _resolve_from(now, custom_themes)
    if _match is None:
        _match = _resolve_from(now, built_in_themes)
    if _match is None:
        for _extension in extensions:
            _match = await _extension.resolve_fallback(now)
            if _match is not None:
                break
    if _match is None:
        return None
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
    )


class LedThemeProvider(Protocol):
    def __init__(self, config: dict[str, Any]) -> None: ...

    async def resolve(self, now: datetime) -> LedTheme | None: ...

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


class ExtensionWrapper:
    def __init__(self, name: str, provider: LedThemeProvider) -> None:
        self.name = name
        self._provider = provider
        self._last_exception: Exception | None = None

    async def resolve(self, now: datetime) -> LedTheme | None:
        return await self._invoke("resolve", now)

    async def resolve_fallback(self, now: datetime) -> LedTheme | None:
        # Optional second-pass hook (ADR 0015) -- absent on most providers, so
        # guarded like stop() rather than assumed present.
        if not hasattr(self._provider, "resolve_fallback"):
            return None
        return await self._invoke("resolve_fallback", now)

    async def _invoke(self, method_name: str, now: datetime) -> LedTheme | None:
        # resolve() and resolve_fallback() share this body -- and the one
        # self._last_exception -- so a provider that misbehaves the same way
        # through either entry point is warned about once, and a success
        # through either clears the other's recorded failure.
        try:
            _result = await getattr(self._provider, method_name)(now)
            # Raised inside this try so a misbehaving extension's bad return
            # value is funnelled through the same isolation/dedup handling
            # below as any other failure, rather than propagating to crash
            # resolve_theme's own .effect_name access.
            if _result is not None and not isinstance(_result, LedTheme):
                raise TypeError(
                    f"{method_name}() returned {type(_result).__name__}, expected "
                    "LedTheme or None"
                )
        except Exception as e:
            if type(e) is not type(self._last_exception) or str(e) != str(
                self._last_exception
            ):
                logger.warning(
                    f"LED theme extension {self.name!r} failed: {type(e).__name__}: {e}."
                )
            self._last_exception = e
            return None
        if self._last_exception is not None:
            logger.info(f"LED theme extension {self.name!r} recovered.")
            self._last_exception = None
        return _result

    async def stop(self) -> None:
        if not hasattr(self._provider, "stop"):
            return
        try:
            await self._provider.stop()
        except Exception as e:
            logger.warning(
                f"LED theme extension {self.name!r} failed to stop cleanly: "
                f"{type(e).__name__}: {e}."
            )


def _load_provider_class(module_path: Path) -> type[LedThemeProvider]:
    _spec = importlib.util.spec_from_file_location(
        f"_hypervolt_extension.{module_path.stem}", module_path
    )
    if _spec is None or _spec.loader is None:
        raise ImportError(f"Could not load module spec for {module_path}.")
    _module = importlib.util.module_from_spec(_spec)
    # module_from_spec() alone does not register the module in sys.modules --
    # unlike a normal import, so anything the module's own top-level code
    # relies on sys.modules for (e.g. @dataclass, via dataclasses._is_type,
    # looks up sys.modules[cls.__module__] directly with no default) would
    # otherwise crash during exec_module below. Matches importlib's own
    # documented recipe for loading a module from a file path. The name is
    # namespaced under "_hypervolt_extension." so an extension file that
    # happens to share a name with a real module (e.g. "config.py") can
    # never clobber -- or be clobbered by -- that module's sys.modules entry.
    sys.modules[_spec.name] = _module
    try:
        _spec.loader.exec_module(_module)
        _candidates = [
            _cls
            for _, _cls in inspect.getmembers(_module, inspect.isclass)
            if _cls.__module__ == _module.__name__ and hasattr(_cls, "resolve")
        ]
        if len(_candidates) != 1:
            raise ValueError(
                f"{module_path}: expected exactly one class implementing "
                f"LedThemeProvider, found {len(_candidates)}."
            )
    except Exception:
        del sys.modules[_spec.name]
        raise
    return _candidates[0]


async def load_extensions(
    entries: Sequence[ExtensionEntry], extensions_dir: Path
) -> list[ExtensionWrapper]:
    _loaded: list[ExtensionWrapper] = []
    _extensions_dir = extensions_dir.resolve()
    for entry in entries:
        _provider: LedThemeProvider | None = None
        try:
            _module_path = (extensions_dir / f"{entry.name}.py").resolve()
            if not _module_path.is_relative_to(_extensions_dir):
                raise ValueError(
                    f"{entry.name!r} resolves outside extensions_dir {extensions_dir}."
                )
            _provider_class = _load_provider_class(_module_path)
            _provider = _provider_class(entry.config)
            if hasattr(_provider, "start"):
                await _provider.start()
        except Exception as e:
            logger.error(
                f"Failed to load LED theme extension {entry.name!r}: {type(e).__name__}: {e}."
            )
            # __init__ succeeding but start() raising can still leave a
            # resource open (e.g. an httpx.AsyncClient) -- best-effort clean
            # it up via the same isolated stop() path a fully-loaded
            # extension gets, so a failed load never leaks.
            if _provider is not None:
                await ExtensionWrapper(name=entry.name, provider=_provider).stop()
            continue
        _loaded.append(ExtensionWrapper(name=entry.name, provider=_provider))
    return _loaded


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

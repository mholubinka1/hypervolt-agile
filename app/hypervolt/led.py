from __future__ import annotations

import logging.config
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from logging import Logger, getLogger
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import yaml
from common.constants import APP_NAME, TIMEZONE
from common.logging import config

if TYPE_CHECKING:
    from config import CustomLedTheme

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

_LOCAL_TZ = ZoneInfo(TIMEZONE)
_LED_COUNT = 51


@dataclass(frozen=True)
class LedTheme:
    # BUILT_IN_THEMES holds singleton instances returned by reference on every
    # resolve_theme() call, so this must stay immutable -- a mutating caller
    # would otherwise corrupt state shared across scheduler cycles.
    effect_name: str
    leds: list[dict[str, float]] | None = None


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
        _colour = _hex_to_rgb(segment["colour"])
        _indices = list(segment.get("indices", []))
        for _range_start, _range_end in segment.get("ranges", []):
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
BUILT_IN_THEMES: list[tuple[LedTheme, Window, Window]] = [
    (LedTheme(effect_name="halloween_mode"), (10, 31, 0, 0), (11, 1, 6, 0)),
    (LedTheme(effect_name="christmas_mode"), (12, 24, 0, 0), (12, 31, 6, 0)),
    (LedTheme(effect_name="party_mode"), (12, 31, 6, 0), (1, 1, 6, 0)),
]


def parse_window_date(value: str) -> Window:
    # A fixed leap year avoids Python's day-without-year parsing ambiguity
    # (deprecated in 3.15) and lets "02-29" parse as a valid window boundary.
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            # Naive is fine here -- only the calendar fields are used, the
            # result is never compared as an actual instant.
            _parsed = datetime.strptime(f"2000-{value}", fmt)  # noqa: DTZ007
            return (_parsed.month, _parsed.day, _parsed.hour, _parsed.minute)
        except ValueError:
            continue
    raise ValueError(
        f"Invalid date window {value!r}: expected 'MM-DD' or 'MM-DD HH:MM'."
    )


def _window_for_year(
    start: Window, end: Window, anchor_year: int
) -> tuple[datetime, datetime]:
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
            _start, _end = _window_for_year(start, end, anchor_year)
            if _start <= now < _end:
                return theme
    return None


def resolve_theme(
    now: datetime,
    extensions: Sequence[object] = (),
    custom_themes: Sequence[tuple[LedTheme, Window, Window]] = (),
) -> LedTheme | None:
    _match = _resolve_from(now, custom_themes)
    if _match is not None:
        return _match
    return _resolve_from(now, BUILT_IN_THEMES)


def load_custom_themes(
    entries: Sequence[CustomLedTheme], led_effects_dir: Path
) -> list[tuple[LedTheme, Window, Window]]:
    _loaded: list[tuple[LedTheme, Window, Window]] = []
    for entry in entries:
        try:
            _leds = load_custom_effect(led_effects_dir / f"{entry.effect}.yaml")
        except Exception as e:
            logger.error(
                f"Failed to load custom LED theme {entry.effect!r}: {type(e).__name__}: {e}."
            )
            continue
        _theme = LedTheme(effect_name=entry.effect, leds=_leds)
        _loaded.append(
            (_theme, parse_window_date(entry.start), parse_window_date(entry.end))
        )
    return _loaded

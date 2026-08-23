from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from common.constants import TIMEZONE

_LOCAL_TZ = ZoneInfo(TIMEZONE)


@dataclass
class LedTheme:
    effect_name: str


# (effect_name, (start_month, start_day, start_hour, start_minute), (end_month, end_day, end_hour, end_minute))
# Year-agnostic, London local time. A window whose end month is earlier than its
# start month wraps into the following year (e.g. party_mode spans New Year's Eve).
_Window = tuple[int, int, int, int]
BUILT_IN_THEMES: list[tuple[str, _Window, _Window]] = [
    ("halloween_mode", (10, 31, 0, 0), (11, 1, 6, 0)),
    ("christmas_mode", (12, 24, 0, 0), (12, 31, 6, 0)),
    ("party_mode", (12, 31, 6, 0), (1, 1, 6, 0)),
]


def _window_for_year(
    start: _Window, end: _Window, anchor_year: int
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


def resolve_theme(
    now: datetime,
    extensions: Sequence[object] = (),
    custom_themes: Sequence[object] = (),
) -> LedTheme | None:
    for effect_name, start, end in BUILT_IN_THEMES:
        for anchor_year in (now.year, now.year - 1):
            _start, _end = _window_for_year(start, end, anchor_year)
            if _start <= now < _end:
                return LedTheme(effect_name=effect_name)
    return None

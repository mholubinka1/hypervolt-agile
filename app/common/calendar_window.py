from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from common.constants import TIMEZONE

_LOCAL_TZ = ZoneInfo(TIMEZONE)

# A fixed leap year, shared by parse_window_date (so "02-29" parses) and by
# config.py's end_must_be_after_start validator (so its chronological check
# uses the same reference year as everything else that materialises a Window
# into real dates) -- one named constant instead of the literal 2000 living
# in two places.
REFERENCE_ANCHOR_YEAR = 2000

# (start_month, start_day, start_hour, start_minute), (end_month, end_day, end_hour, end_minute)
Window = tuple[int, int, int, int]

# (name, (start_month, start_day, start_hour, start_minute), (end_month, end_day, end_hour, end_minute))
# Year-agnostic, London local time. A window whose end month is earlier than its
# start month wraps into the following year (e.g. party_mode spans New Year's Eve).
DEFAULT_BUILT_IN_THEME_WINDOWS: list[tuple[str, Window, Window]] = [
    ("halloween_mode", (10, 31, 0, 0), (11, 1, 6, 0)),
    ("christmas_mode", (12, 24, 0, 0), (12, 31, 6, 0)),
    ("party_mode", (12, 31, 6, 0), (1, 1, 6, 0)),
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

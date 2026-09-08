from datetime import timedelta


def is_null_or_empty(s: str | None) -> bool:
    if not s:
        return True
    return s.strip() == ""


def format_duration(td: timedelta) -> str:
    _total_seconds = int(td.total_seconds())
    if _total_seconds <= 0:
        return "0s"
    _minutes, _seconds = divmod(_total_seconds, 60)
    _hours, _minutes = divmod(_minutes, 60)
    if _hours >= 1:
        return f"{_hours}h{_minutes:02d}m"
    if _minutes >= 1:
        return f"{_minutes}m"
    return f"{_seconds}s"

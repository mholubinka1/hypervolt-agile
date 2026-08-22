from datetime import datetime
from zoneinfo import ZoneInfo

from common.model import ChargeSession


def test_format_same_day_session_omits_repeated_day_name() -> None:
    session = ChargeSession(
        start=datetime(2026, 1, 5, 1, 30, tzinfo=ZoneInfo("UTC")),
        end=datetime(2026, 1, 5, 4, 0, tzinfo=ZoneInfo("UTC")),
        average_price_per_kwh=0.15,
    )

    result = session.format("UTC")

    assert result == "Monday, 01:30 → 04:00 @ £0.1500/kWh inc. VAT"


def test_format_overnight_session_includes_both_day_names() -> None:
    session = ChargeSession(
        start=datetime(2026, 1, 5, 23, 30, tzinfo=ZoneInfo("UTC")),
        end=datetime(2026, 1, 6, 4, 0, tzinfo=ZoneInfo("UTC")),
        average_price_per_kwh=0.2,
    )

    result = session.format("UTC")

    assert result == "Monday, 23:30 → Tuesday, 04:00 @ £0.2000/kWh inc. VAT"


def test_format_converts_to_requested_timezone() -> None:
    # 5 July 2026 is within British Summer Time (UTC+1), so this also verifies
    # the conversion is applied and not just a passthrough of the UTC values.
    session = ChargeSession(
        start=datetime(2026, 7, 5, 23, 30, tzinfo=ZoneInfo("UTC")),
        end=datetime(2026, 7, 6, 1, 0, tzinfo=ZoneInfo("UTC")),
        average_price_per_kwh=0.1,
    )

    result = session.format("Europe/London")

    # Both instants land on the same local (London) calendar date, 6 July, even
    # though they're on different UTC dates — so the day name is not repeated.
    assert result == "Monday, 00:30 → 02:00 @ £0.1000/kWh inc. VAT"

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass
class Price:
    value_exc_vat: float
    valid_from: datetime
    valid_to: datetime


@dataclass
class ChargeSession:
    start: datetime
    end: datetime

    def format(self, timezone: str) -> str:
        _tz = ZoneInfo(timezone)
        _local_start = self.start.astimezone(_tz)
        _local_end = self.end.astimezone(_tz)
        return f"{_local_start.strftime('%A, %H:%M')} → {_local_end.strftime('%H:%M')}"

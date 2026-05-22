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
    average_price_per_kwh: float

    def format(self, timezone: str) -> str:
        _tz = ZoneInfo(timezone)
        _local_start = self.start.astimezone(_tz)
        _local_end = self.end.astimezone(_tz)
        _cost = f" @ £{self.average_price_per_kwh:.4f}/kWh inc. VAT"
        if _local_start.date() == _local_end.date():
            return f"{_local_start.strftime('%A, %H:%M')} → {_local_end.strftime('%H:%M')}{_cost}"
        return f"{_local_start.strftime('%A, %H:%M')} → {_local_end.strftime('%A, %H:%M')}{_cost}"

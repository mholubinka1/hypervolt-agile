from dataclasses import dataclass
from datetime import datetime


@dataclass
class Price:
    value_exc_vat: float
    valid_from: datetime
    valid_to: datetime


@dataclass
class ChargeSession:
    start: datetime
    end: datetime

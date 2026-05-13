from dataclasses import dataclass
from datetime import time
from enum import Enum

from common.model import ChargeSession


class LockStatus(Enum):
    unlocked = (0,)
    pending_lock = (1,)
    locked = (2,)


class ChargingMode(Enum):
    boost = (0,)
    eco = (1,)
    super_eco = (2,)


class ActivationMode(Enum):
    plug_and_charge = (0,)
    schedule = (1,)
    octopus = (2,)


class DayOfWeek(Enum):
    monday = (1,)
    tuesday = (2,)
    wednesday = (4,)
    thursday = (8,)
    friday = ((16),)
    saturday = (32,)
    sunday = (64,)
    all = (127,)


def weekday_to_dayofweek(weekday: int) -> DayOfWeek:
    mapping = {
        0: DayOfWeek.monday,
        1: DayOfWeek.tuesday,
        2: DayOfWeek.wednesday,
        3: DayOfWeek.thursday,
        4: DayOfWeek.friday,
        5: DayOfWeek.saturday,
        6: DayOfWeek.sunday,
    }
    return mapping.get(weekday, DayOfWeek.all)


@dataclass
class HypervoltCharger:
    id: str
    maj_version: int


@dataclass
class HypervoltSession:
    start: time
    end: time
    day_of_week: DayOfWeek
    charge_mode: ChargingMode = ChargingMode.boost

    @staticmethod
    def create_from_charge_session(
        charge_session: ChargeSession,
        charge_mode: ChargingMode = ChargingMode.boost,
    ) -> "HypervoltSession":
        return HypervoltSession(
            start=charge_session.start.time(),
            end=charge_session.end.time(),
            charge_mode=charge_mode,
            day_of_week=weekday_to_dayofweek(charge_session.start.weekday()),
        )

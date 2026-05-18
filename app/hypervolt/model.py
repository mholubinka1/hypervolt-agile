from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Dict, List
from zoneinfo import ZoneInfo

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


class ReleaseState(Enum):
    DEFAULT = "DEFAULT"
    RELEASED = "RELEASED"


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

    def __str__(self) -> str:
        return (
            f"{self.day_of_week.name.capitalize()}, "
            f"{self.start.strftime('%H:%M')} → {self.end.strftime('%H:%M')}, "
            f"[charge_mode={self.charge_mode.name}]"
        )

    @staticmethod
    def create_from_charge_session(
        charge_session: ChargeSession,
        timezone: str,
        charge_mode: ChargingMode = ChargingMode.boost,
    ) -> List["HypervoltSession"]:
        _tz = ZoneInfo(timezone)
        _local_start = charge_session.start.astimezone(_tz)
        _local_end = charge_session.end.astimezone(_tz)
        if _local_start.date() == _local_end.date():
            return [
                HypervoltSession(
                    start=_local_start.time(),
                    end=_local_end.time(),
                    charge_mode=charge_mode,
                    day_of_week=weekday_to_dayofweek(_local_start.weekday()),
                )
            ]
        _local_midnight = datetime.combine(
            _local_start.date() + timedelta(days=1),
            datetime.min.time(),
            tzinfo=_tz,
        )
        return [
            HypervoltSession(
                start=_local_start.time(),
                end=time(0, 0),
                charge_mode=charge_mode,
                day_of_week=weekday_to_dayofweek(_local_start.weekday()),
            ),
            HypervoltSession(
                start=time(0, 0),
                end=_local_end.time(),
                charge_mode=charge_mode,
                day_of_week=weekday_to_dayofweek(_local_midnight.weekday()),
            ),
        ]

    @classmethod
    def parse_from_response(cls, session: Dict) -> "HypervoltSession":
        _days = session.get("days", [])
        if len(_days) != 1:
            raise ValueError(f"Expected exactly one day per session, got: {_days}")
        _end_str = session["end_time"]
        if _end_str == "24:00":
            _end_str = "00:00"
        return cls(
            start=time.fromisoformat(session["start_time"]),
            end=time.fromisoformat(_end_str),
            day_of_week=DayOfWeek[_days[0].lower()],
            charge_mode=ChargingMode[session["mode"].lower()],
        )

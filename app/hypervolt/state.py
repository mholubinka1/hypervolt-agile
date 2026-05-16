from dataclasses import dataclass, field, fields
from typing import List, Optional

from hypervolt.model import (
    ActivationMode,
    ChargingMode,
    HypervoltCharger,
    HypervoltSession,
    LockStatus,
    ReleaseState,
)


@dataclass
class HypervoltChargerStateDelta:
    lock_status: Optional[LockStatus] = field(default=None)
    charging_mode: Optional[ChargingMode] = field(default=None)
    activation_mode: Optional[ActivationMode] = field(default=None)
    release_state: Optional[ReleaseState] = field(default=None)
    is_charging: Optional[bool] = field(default=None)
    car_plugged: Optional[bool] = field(default=None)
    led_brightness: Optional[float] = field(default=None)
    current_schedule: Optional[List[HypervoltSession]] = field(default=None)


class HypervoltChargerState:
    def __init__(self, charger: HypervoltCharger) -> None:
        self.id = charger.id
        self.maj_version = charger.maj_version

        self.lock_status: Optional[LockStatus] = None
        self.charging_mode: Optional[ChargingMode] = None

        self.activation_mode: Optional[ActivationMode] = None
        self.release_state: Optional[ReleaseState] = None

        self.is_charging: Optional[bool] = None
        self.car_plugged: Optional[bool] = None

        self.led_brightness: Optional[float] = None
        self.current_schedule: Optional[List[HypervoltSession]] = None

    def update(self, delta: HypervoltChargerStateDelta) -> bool:
        _changed = False
        for f in fields(delta):
            _value = getattr(delta, f.name)
            if f.name == "current_schedule":
                if _value is not None and _value != self.current_schedule:
                    self.current_schedule = list(_value)
                    _changed = True
            elif _value is not None and _value != getattr(self, f.name):
                setattr(self, f.name, _value)
                _changed = True
        return _changed

    def __str__(self) -> str:
        _schedule = (
            f"{len(self.current_schedule)} sessions"
            if self.current_schedule is not None
            else None
        )
        return (
            f"lock_status={self.lock_status.name if self.lock_status else None}, "
            f"charging_mode={self.charging_mode.name if self.charging_mode else None}, "
            f"car_plugged={self.car_plugged}, "
            f"is_charging={self.is_charging}, "
            f"scheduler_paused={self.release_state == ReleaseState.RELEASED if self.release_state is not None else None}, "
            f"activation_mode={self.activation_mode.name if self.activation_mode else None}, "
            f"schedule={_schedule}"
        )

from dataclasses import dataclass, field, fields
from typing import Optional

from hypervolt.model import (
    ActivationMode,
    ChargingMode,
    HypervoltCharger,
    LockStatus,
    ReleaseState,
)


@dataclass
class HypervoltChargeStateDelta:
    lock_status: Optional[LockStatus] = field(default=None)
    charging_mode: Optional[ChargingMode] = field(default=None)
    activation_mode: Optional[ActivationMode] = field(default=None)
    release_state: Optional[ReleaseState] = field(default=None)
    is_charging: Optional[bool] = field(default=None)
    car_plugged: Optional[bool] = field(default=None)
    led_brightness: Optional[float] = field(default=None)


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

    def update(self, delta: HypervoltChargeStateDelta) -> None:
        for f in fields(delta):
            _value = getattr(delta, f.name)
            if _value is not None:
                setattr(self, f.name, _value)

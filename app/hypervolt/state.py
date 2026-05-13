from typing import Optional

from hypervolt.model import ChargingMode, HypervoltCharger, LockStatus


class HypervoltChargerState:
    def __init__(self, charger: HypervoltCharger) -> None:
        self.id = charger.id
        self.maj_version = charger.maj_version

        self.lock_status: Optional[LockStatus] = None
        self.charging_mode: Optional[ChargingMode] = None

        self.is_charging: Optional[bool] = None
        self.car_plugged: Optional[bool] = None

        self.led_brightness: Optional[float] = None

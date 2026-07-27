from dataclasses import dataclass, field

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
    lock_status: LockStatus | None = field(default=None)
    charging_mode: ChargingMode | None = field(default=None)
    activation_mode: ActivationMode | None = field(default=None)
    release_state: ReleaseState | None = field(default=None)
    is_charging: bool | None = field(default=None)
    car_plugged: bool | None = field(default=None)
    led_brightness: float | None = field(default=None)
    current_schedule: list[HypervoltSession] | None = field(default=None)
    clear_current_schedule: bool = field(default=False)


class HypervoltChargerState:
    def __init__(self, charger: HypervoltCharger) -> None:
        self.id = charger.id
        self.maj_version = charger.maj_version

        self.lock_status: LockStatus | None = None
        self.charging_mode: ChargingMode | None = None

        self.activation_mode: ActivationMode | None = None
        self.release_state: ReleaseState | None = None

        self.is_charging: bool | None = None
        self.car_plugged: bool | None = None

        self.led_brightness: float | None = None
        self.current_schedule: list[HypervoltSession] | None = None

    def update(self, delta: HypervoltChargerStateDelta) -> bool:
        _changed = False
        for attr in (
            "lock_status",
            "charging_mode",
            "activation_mode",
            "release_state",
            "is_charging",
            "car_plugged",
            "led_brightness",
        ):
            _value = getattr(delta, attr)
            if _value is not None and _value != getattr(self, attr):
                setattr(self, attr, _value)
                _changed = True
        if delta.clear_current_schedule and self.current_schedule is not None:
            self.current_schedule = None
            _changed = True
        elif (
            delta.current_schedule is not None
            and delta.current_schedule != self.current_schedule
        ):
            self.current_schedule = list(delta.current_schedule)
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

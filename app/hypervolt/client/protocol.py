from __future__ import annotations

import asyncio
import logging.config
from datetime import datetime
from logging import Logger, getLogger
from typing import Awaitable, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from common.constants import APP_NAME, PILOT_UNPLUG_CONFIRMATION_SECS
from common.logging import config
from hypervolt.model import (
    ActivationMode,
    ChargingMode,
    HypervoltSession,
    LockStatus,
    ReleaseState,
)
from hypervolt.state import HypervoltChargerStateDelta

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

HypervoltChargerStateUpdateCallback = Callable[
    [HypervoltChargerStateDelta], Awaitable[None]
]


def _generate_id() -> str:
    return str(int(datetime.now(ZoneInfo("UTC")).timestamp() * 1000000))


class HypervoltProtocol:
    def __init__(
        self,
        send_message: Callable[[Dict], Awaitable[None]],
        on_state_update: HypervoltChargerStateUpdateCallback,
        is_connected: asyncio.Event,
    ) -> None:
        self._send_message = send_message
        self._on_state_update = on_state_update
        self._is_connected = is_connected
        self._consecutive_lock_failures: int = 0
        self._pilot_unplugged_at: Optional[datetime] = None

        self._handlers: Dict[str, Callable[..., Awaitable[None]]] = {
            "login": self._on_login_response,
            "sync.snapshot": self._on_sync_response,
            "sync.apply": self._on_sync_response,
            "get.session": self._on_session_response,
            "get.pilot_status": self._on_pilot_status_response,
            "schedules.get": self._on_schedules_get_response,
            "schedule.set": self._on_schedule_set_response,
            "get.solar_inhibit_charge": self._on_ignored_message,
            "get.thermal_ilimit": self._on_ignored_message,
            "get.site_limit": self._on_ignored_message,
            "get.AC_present": self._on_ignored_message,
            "get.schedule_inhibit": self._on_ignored_message,
            "get.connection_state": self._on_ignored_message,
            "get.composite_pilot_status": self._on_pilot_status_response,
            "flex.status": self._on_ignored_message,
        }

    async def handle(self, method: str, result: Dict, id: Optional[str]) -> None:
        _handler = self._handlers.get(method)
        if not _handler:
            logger.debug(f"No handler implemented for method {method}.")
            return
        await _handler(result, id)

    # region Requests

    async def login(self, token: str) -> None:
        await self._send_message(
            {
                "id": _generate_id(),
                "method": "login",
                "params": {"token": token, "version": 3},
            }
        )

    async def sync(self) -> None:
        await self._send_message(
            {
                "id": _generate_id(),
                "method": "sync.snapshot",
            }
        )

    async def get_charging_schedule(self) -> None:
        await self._send_message(
            {
                "id": _generate_id(),
                "method": "schedules.get",
            }
        )

    # endregion

    # region Handlers

    async def on_error(self, method: str, error: Dict) -> None:
        if method == "schedule.set":
            logger.warning(f"schedule.set error: {error}.")
            await self._on_state_update(
                HypervoltChargerStateDelta(clear_current_schedule=True)
            )
        elif method == "sync.apply":
            self._consecutive_lock_failures += 1
            if self._consecutive_lock_failures >= 3:
                logger.error(
                    f"Lock command failed ({self._consecutive_lock_failures} consecutive failures): {error}."
                )
            else:
                logger.warning(
                    f"Lock command failed ({self._consecutive_lock_failures} consecutive failures): {error}."
                )
        else:
            logger.warning(f"Websocket error for method {method}: {error}.")

    async def _on_login_response(self, result: Dict, id: Optional[str] = None) -> None:
        if result.get("authenticated"):
            logger.info("Websocket login successful.")
            self._consecutive_lock_failures = 0
            self._is_connected.set()
            await self.sync()
            await self.get_charging_schedule()
        else:
            logger.error("Websocket login failed.")

    async def _on_sync_response(
        self, result: List[Dict[str, str]], id: Optional[str] = None
    ) -> None:
        _response_dict = {key: value for d in result for key, value in d.items()}
        _delta = HypervoltChargerStateDelta(
            lock_status=(
                LockStatus[_response_dict["lock_state"]]
                if "lock_state" in _response_dict
                else None
            ),
            charging_mode=(
                ChargingMode[_response_dict["solar_mode"]]
                if "solar_mode" in _response_dict
                else None
            ),
            led_brightness=(
                float(_response_dict["brightness"])
                if "brightness" in _response_dict
                else None
            ),
            release_state=(
                ReleaseState[_response_dict["release_state"].upper()]
                if "release_state" in _response_dict
                else None
            ),
        )
        if "lock_state" in _response_dict:
            self._consecutive_lock_failures = 0
        logger.debug(
            f"sync: lock_status={_delta.lock_status.name if _delta.lock_status else None}, "
            f"charging_mode={_delta.charging_mode.name if _delta.charging_mode else None}, "
            f"release_state={_delta.release_state.name if _delta.release_state else None}."
        )
        await self._on_state_update(_delta)

    async def _on_session_response(
        self, result: Dict, id: Optional[str] = None
    ) -> None:
        _delta = HypervoltChargerStateDelta(is_charging=result.get("charging"))
        await self._on_state_update(_delta)

    async def _on_pilot_status_response(
        self, result: Dict, id: Optional[str] = None
    ) -> None:
        _pilot = (
            result.get("pilot_status") or result.get("composite_pilot_status", "")[:1]
        )
        if not _pilot or _pilot in ("E", "F"):
            return
        if _pilot in ("B", "C", "D"):
            self._pilot_unplugged_at = None
            await self._on_state_update(HypervoltChargerStateDelta(car_plugged=True))
        else:
            _now = datetime.now(ZoneInfo("UTC"))
            if self._pilot_unplugged_at is None:
                self._pilot_unplugged_at = _now
            elif (
                _now - self._pilot_unplugged_at
            ).total_seconds() >= PILOT_UNPLUG_CONFIRMATION_SECS:
                self._pilot_unplugged_at = None
                await self._on_state_update(
                    HypervoltChargerStateDelta(car_plugged=False)
                )

    async def _on_schedule_set_response(
        self, result: Dict, id: Optional[str] = None
    ) -> None:
        if result.get("applied"):
            _sessions = []
            try:
                _sessions = self._parse_sessions(result["applied"])
                _delta = HypervoltChargerStateDelta(current_schedule=_sessions)
            except ValueError as e:
                logger.warning(
                    f"Failed to parse sessions from schedule.set response: {e}"
                )
                _delta = HypervoltChargerStateDelta(current_schedule=[])
            if id:
                await self._on_state_update(_delta)
                if _sessions:
                    for session in _sessions:
                        logger.info(f"Schedule confirmed: {session}.")
                else:
                    logger.info("Schedule confirmed: cleared.")
            else:
                pass
        else:
            logger.error(f"Schedule not applied: id={id}, result={result}")

    async def _on_schedules_get_response(
        self, result: Dict, id: Optional[str] = None
    ) -> None:
        _applied = result.get("applied") if result else None
        if not _applied:
            logger.debug("schedules.get: no schedule on charger.")
            await self._on_state_update(HypervoltChargerStateDelta(current_schedule=[]))
            return

        _enabled = _applied.get("enabled")
        if _enabled and _applied.get("type") == "octopus":
            _activation_mode = ActivationMode.octopus
        elif _enabled:
            _activation_mode = ActivationMode.schedule
        else:
            _activation_mode = ActivationMode.plug_and_charge

        try:
            _sessions = self._parse_sessions(_applied)
            logger.debug(f"schedules.get: {len(_sessions)} session(s) on charger.")
            await self._on_state_update(
                HypervoltChargerStateDelta(
                    activation_mode=_activation_mode,
                    current_schedule=_sessions,
                )
            )
        except ValueError as e:
            logger.warning(f"schedules.get parse error: {e}")
            await self._on_state_update(
                HypervoltChargerStateDelta(
                    activation_mode=_activation_mode,
                    current_schedule=[],
                )
            )

    async def _on_ignored_message(self, result: Dict, id: Optional[str] = None) -> None:
        pass

    # endregion

    # region Helpers

    def _parse_sessions(self, applied: Dict) -> List[HypervoltSession]:
        _sessions = []
        for _s in applied.get("sessions", []):
            try:
                _sessions.append(HypervoltSession.parse_from_response(_s))
            except Exception as e:
                raise ValueError(f"Failed to parse session: {_s}") from e
        return _sessions

    # endregion

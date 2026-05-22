import logging.config
from datetime import datetime, timedelta
from logging import Logger, getLogger
from typing import Optional
from zoneinfo import ZoneInfo

from common.constants import APP_NAME, SESSION_CLOCK_OFFSET_MINS
from common.logging import config
from hypervolt.charger import HypervoltChargerClient
from hypervolt.model import HypervoltSession, LockStatus, ReleaseState
from schedule import Scheduler

from config import AppConfig

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


class ScheduleCoordinator:
    def __init__(self, scheduler: Scheduler, config: AppConfig) -> None:
        self._scheduler = scheduler
        self._config = config
        self._charger_client: Optional[HypervoltChargerClient] = None
        self._car_was_plugged: Optional[bool] = None
        self._was_connected: Optional[bool] = None
        self._disconnected_at: Optional[datetime] = None

    async def close(self) -> None:
        if self._charger_client:
            await self._charger_client.close()

    async def run(self) -> None:
        if not self._charger_client:
            try:
                self._charger_client = await HypervoltChargerClient.create(
                    config=self._config
                )
            except Exception as e:
                logger.exception(f"Failed to initialise charger client: {e}")
                return
        try:
            _car_plugged = self._charger_client.charger_state.car_plugged
            if self._car_was_plugged is False and _car_plugged:
                self._scheduler.invalidate()
            self._car_was_plugged = _car_plugged
            await self._scheduler.update()
            _is_connected = self._charger_client.is_connected
            if self._was_connected is True and not _is_connected:
                self._disconnected_at = datetime.now(ZoneInfo("UTC"))
            elif (
                self._was_connected is False and _is_connected and self._disconnected_at
            ):
                _duration = (
                    datetime.now(ZoneInfo("UTC")) - self._disconnected_at
                ).total_seconds()
                logger.info(f"Websocket reconnected after {_duration:.0f}s.")
                self._disconnected_at = None
            self._was_connected = _is_connected
            if not _is_connected:
                return
            await self._charger_client.refresh()
            if self._scheduler.should_verify():
                await self._charger_client.verify_schedule()
            if self._can_push():
                await self._apply_charging_schedule()
                await self._lock_control()
        except Exception as e:
            logger.exception(f"Error in schedule coordinator run loop: {e}")

    def _can_push(self) -> bool:
        if self._charger_client is None:
            return False
        _state = self._charger_client.charger_state
        return (
            _state.car_plugged is True and _state.release_state != ReleaseState.RELEASED
        )

    def _should_unlock(self) -> bool:
        _now = datetime.now(ZoneInfo("UTC"))
        _lookahead = timedelta(minutes=SESSION_CLOCK_OFFSET_MINS)
        return any(
            s.start - _lookahead <= _now < s.end for s in self._scheduler.schedule
        )

    async def _lock_control(self) -> None:
        if self._charger_client is None:
            return
        _desired_locked = not self._should_unlock()
        _current = self._charger_client.charger_state.lock_status
        if _current is not None:
            if _desired_locked and _current in (
                LockStatus.locked,
                LockStatus.pending_lock,
            ):
                return
            if not _desired_locked and _current == LockStatus.unlocked:
                return
        if _desired_locked:
            await self._charger_client.lock()
        else:
            await self._charger_client.unlock()

    async def _apply_charging_schedule(self) -> None:
        if self._charger_client is None:
            raise RuntimeError(
                "Charger client not initialised before applying schedule."
            )
        _hypervolt_sessions = [
            hypervolt_session
            for session in self._scheduler.schedule
            for hypervolt_session in HypervoltSession.create_from_charge_session(
                session, self._scheduler.timezone
            )
        ]
        _pushed = await self._charger_client.apply_schedule(_hypervolt_sessions)
        if _pushed and _hypervolt_sessions:
            logger.info(
                f"Sending {len(_hypervolt_sessions)} sessions, avg £{self._scheduler.average_price_per_kwh:.4f}/kWh inc. VAT."
            )

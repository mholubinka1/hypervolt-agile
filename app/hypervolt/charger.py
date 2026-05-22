from __future__ import annotations

import logging.config
from asyncio import create_task
from datetime import time
from logging import Logger, getLogger
from typing import List, Optional

from common.constants import APP_NAME
from common.logging import config
from hypervolt.client.rest import HypervoltRestClient
from hypervolt.client.websocket import HypervoltWebSocketClient
from hypervolt.model import HypervoltCharger, HypervoltSession
from hypervolt.state import HypervoltChargerState, HypervoltChargerStateDelta

from config import AppConfig

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


class HypervoltChargerClient:
    _polling_interval: int

    _charger: HypervoltCharger
    _charger_state: HypervoltChargerState

    _rest_client: HypervoltRestClient
    _ws_client: HypervoltWebSocketClient

    @classmethod
    async def create(cls, config: AppConfig) -> HypervoltChargerClient:
        rest_client = await HypervoltRestClient.create(
            username=config.hypervolt.username,
            password=config.hypervolt.password,
        )
        self = cls(rest_client=rest_client, polling_interval=config.schedule.poll)
        if self._charger.maj_version < 3:
            raise NotImplementedError(
                f"Hypervolt v{self._charger.maj_version} chargers are not currently supported."
            )
        self._ws_client._connect_task = create_task(self._ws_client.connect())
        _initialised = False
        try:
            await self._ws_client.wait_until_connected(timeout=30)
            await self.clear_schedule()
            if not self.is_connected:
                raise RuntimeError("Websocket disconnected during initialisation.")
            _initialised = True
        finally:
            if not _initialised:
                await self._ws_client.disconnect()
        return self

    def __init__(
        self,
        rest_client: HypervoltRestClient,
        polling_interval: int,
    ) -> None:
        self._polling_interval = polling_interval
        self._rest_client = rest_client
        self._charger = rest_client.charger
        self._last_pushed_sessions: Optional[List[HypervoltSession]] = None

        self._charger_state = HypervoltChargerState(self._charger)
        self._ws_client = HypervoltWebSocketClient(
            charger=self._charger,
            access_token_callback=self._rest_client.get_access_token,
            on_state_update=self._on_state_update,
        )

    async def _on_state_update(
        self,
        delta: HypervoltChargerStateDelta,
    ) -> None:
        _car_was_plugged = self._charger_state.car_plugged
        if self._charger_state.update(delta):
            if delta.current_schedule is not None:
                self._last_pushed_sessions = None
            if (
                _car_was_plugged is not None
                and self._charger_state.car_plugged != _car_was_plugged
            ):
                if self._charger_state.car_plugged:
                    logger.info("Car plugged in.")
                else:
                    logger.info("Car unplugged.")
            logger.debug(f"charger_state: {self._charger_state}.")

    @property
    def is_connected(self) -> bool:
        return self._ws_client.is_connected

    @property
    def charger_state(self) -> HypervoltChargerState:
        return self._charger_state

    async def _refresh_auth(self) -> None:
        if self._rest_client.is_token_expiring(self._polling_interval * 2):
            await self._rest_client.authenticate()
            await self._ws_client.reconnect()

    async def refresh(self) -> None:
        await self._refresh_auth()
        await self._ws_client.sync_charger_state()

    async def verify_schedule(self) -> None:
        await self._ws_client.check_charging_schedule()

    async def apply_schedule(self, schedule: List[HypervoltSession]) -> bool:
        _current_schedule = self._charger_state.current_schedule
        _proposed_sorted = sorted(
            schedule, key=lambda s: (s.start, s.day_of_week.value[0])
        )
        if _current_schedule is not None:
            _current_sorted = sorted(
                _current_schedule, key=lambda s: (s.start, s.day_of_week.value[0])
            )
            if _proposed_sorted == _current_sorted:
                logger.debug("Schedule unchanged, skipping apply.")
                return False
        _last_sorted = (
            sorted(
                self._last_pushed_sessions,
                key=lambda s: (s.start, s.day_of_week.value[0]),
            )
            if self._last_pushed_sessions is not None
            else None
        )
        _is_retry = (
            _current_schedule is None
            and _last_sorted is not None
            and _proposed_sorted == _last_sorted
        )
        if _is_retry:
            logger.debug(f"Retrying schedule push ({len(schedule)} session(s)).")
        else:
            if schedule:
                for session in schedule:
                    logger.info(f"Sending session: {session}.")
            else:
                logger.info("Sending empty schedule to charger.")
            self._last_pushed_sessions = list(schedule)
        sessions = [
            {
                "session_type": "recurring",
                "start_time": s.start.strftime("%H:%M"),
                "end_time": "24:00" if s.end == time(0, 0) else s.end.strftime("%H:%M"),
                "mode": s.charge_mode.name.lower(),
                "days": [s.day_of_week.name],
            }
            for s in schedule
        ]
        await self._ws_client.set_charging_schedule(sessions)
        return True

    async def lock(self) -> None:
        logger.info("Locking charger.")
        await self._ws_client.set_lock_state(locked=True)

    async def unlock(self) -> None:
        logger.info("Unlocking charger.")
        await self._ws_client.set_lock_state(locked=False)

    async def clear_schedule(self) -> None:
        logger.info("Clearing charger schedule.")
        await self._ws_client.set_charging_schedule([])

    async def close(self) -> None:
        await self._ws_client.disconnect()
        await self._rest_client.close()

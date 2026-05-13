from __future__ import annotations

import asyncio
from asyncio import create_task
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from hypervolt.client.rest import HypervoltRestClient
from hypervolt.client.websocket import HypervoltWebSocketClient
from hypervolt.model import HypervoltCharger, HypervoltSession
from hypervolt.state import HypervoltChargerState

from config import AppConfig


class HypervoltCoordinator:
    _polling_interval: int

    _charger: HypervoltCharger
    _charger_state: Optional[HypervoltChargerState]

    _rest_client: HypervoltRestClient
    _ws_client: HypervoltWebSocketClient

    @classmethod
    async def create(cls, config: AppConfig) -> HypervoltCoordinator:
        self = cls(config)
        if self._charger.maj_version < 3:
            raise NotImplementedError(
                f"Hypervolt v{self._charger.maj_version} chargers are not currently supported."
            )
        self._ws_client._connect_task = create_task(self._ws_client.connect())
        await asyncio.wait_for(self._ws_client._is_connected.wait(), timeout=30)
        self._charger_state = self._ws_client._charger_state
        return self

    def __init__(
        self,
        config: AppConfig,
    ) -> None:
        self._polling_interval = config.schedule.poll

        self._rest_client = HypervoltRestClient(
            username=config.hypervolt.username,
            password=config.hypervolt.password,
        )
        self._charger = self._rest_client.charger

        self._ws_client = HypervoltWebSocketClient(
            charger=self._charger,
            access_token_callback=self._rest_client.get_access_token,
        )
        self._charger_state = None

    async def get_charger_state(self) -> HypervoltChargerState:
        await self.update()
        if self._charger_state is None:
            raise Exception("Charger state is not initialized")
        return self._charger_state

    async def update(self) -> None:
        _seconds_to_expiry = (
            self._rest_client.access_token_expiry_time - datetime.now(ZoneInfo("UTC"))
        ).total_seconds()
        if _seconds_to_expiry < self._polling_interval * 2:
            self._rest_client.authenticate()
            await self._ws_client.reconnect()

    async def apply_schedule(self, schedule: List[HypervoltSession]) -> None:
        sessions = [
            {
                "session_type": "recurring",
                "start_time": s.start.strftime("%H:%M"),
                "end_time": s.end.strftime("%H:%M"),
                "mode": s.charge_mode.name.lower(),
                "days": [s.day_of_week.name],
            }
            for s in schedule
        ]
        await self._ws_client.set_charging_schedule(sessions)

    async def clear_completed_session(
        self, completed_session: HypervoltSession
    ) -> None:
        pass

    async def clear_schedule(self, schedule: List[HypervoltSession]) -> None:
        pass

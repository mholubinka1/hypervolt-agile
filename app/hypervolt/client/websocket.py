from __future__ import annotations

import asyncio
import json
import logging.config
from asyncio import CancelledError
from copy import deepcopy
from datetime import datetime
from logging import Logger, getLogger
from typing import Awaitable, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

import websockets
from common.constants import APP_NAME
from common.logging import config
from hypervolt.model import ActivationMode, ChargingMode, HypervoltCharger, LockStatus
from hypervolt.state import HypervoltChargerState
from websockets import Data

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)

_RECONNECT_DELAY_SECS = 5


class HypervoltWebSocketClient:
    is_connected: asyncio.Event

    _origin: websockets.Origin = websockets.Origin("https://hypervolt.co.uk")
    _host: str = "api.hypervolt.co.uk"
    _base_wss_url: str = "wss://api.hypervolt.co.uk/ws/charger"

    _charger: HypervoltCharger

    _access_token_callback: Callable[[], str]

    _websocket: Optional[websockets.ClientConnection] = None
    _authenticated: bool = False
    _last_activity: Optional[datetime]
    _stop_requested: bool = True

    _connect_task: Optional[asyncio.Task] = None
    _is_connected: asyncio.Event

    _messages: Dict[str, str]
    _handlers: Dict[str, Callable[..., Awaitable[None]]]

    def __init__(
        self,
        charger: HypervoltCharger,
        access_token_callback: Callable[[], str],
    ) -> None:
        self._charger = charger
        self._charger_state = HypervoltChargerState(charger)
        self._access_token_callback = access_token_callback

        self._websocket = None
        self._last_activity = None
        self._stop_requested = False

        self._connect_task = None
        self._is_connected = asyncio.Event()

        self._messages: Dict[str, str] = {}
        self._handlers = {
            "login": self._on_login_response,
            "sync.snapshot": self._on_sync_response,
            "sync.apply": self._on_sync_response,
        }

    def _get_access_token(self) -> str:
        return self._access_token_callback()

    # region Helpers

    def _get_user_agent(self) -> str:
        return "home-assistant-hypervolt-charger/0.0.0"

    def _generate_id_from_timestamp(self) -> str:
        _timestamp = datetime.now(ZoneInfo("UTC")).timestamp()
        return str(int(_timestamp * 1000000))

    # endregion

    # region Connection

    async def connect(self) -> None:
        _url = f"{self._base_wss_url}/{self._charger.id}/sync"
        self._stop_requested = False
        while not self._stop_requested:
            try:
                async with websockets.connect(
                    _url,
                    origin=self._origin,
                    user_agent_header=self._get_user_agent(),
                ) as websocket:
                    self._websocket = websocket
                    await self._login()
                    await self._receive_messages_worker()
            except CancelledError:
                logger.debug("WebSocket connect task cancelled")
                raise
            except Exception:
                logger.warning(
                    "WebSocket connection lost, reconnecting in %ss",
                    _RECONNECT_DELAY_SECS,
                    exc_info=True,
                )
                self._websocket = None
                await asyncio.sleep(_RECONNECT_DELAY_SECS)

    async def _receive_messages_worker(self) -> None:
        if not self._websocket:
            return
        async for message in self._websocket:
            await self._receive_message(message)

    async def reconnect(self) -> None:
        if self._websocket:
            await self._websocket.close()

    async def disconnect(self) -> None:
        self._stop_requested = True
        if self._websocket:
            await self._websocket.close()
            self._websocket = None
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except CancelledError:
                logger.debug("Connect task cancelled")

    # endregion

    # region Message

    async def _send_message(self, message: Dict) -> None:
        if self._websocket:
            if "jsonrpc" not in message:
                message["jsonrpc"] = "2.0"
            _loggable_message = deepcopy(message)
            if "params" in _loggable_message and "token" in _loggable_message["params"]:
                _loggable_message["params"]["token"] = "****"  # nosec B105

            logger.debug(
                f"Sending message to websocket: {json.dumps(_loggable_message)}"
            )
            _json_message = json.dumps(message)
            await self._websocket.send(_json_message)
            self._messages[message["id"]] = message["method"]
        else:
            logger.error("WebSocket is not connected, cannot send message")

    async def _receive_message(self, message: Data) -> None:
        self._last_activity = datetime.now(ZoneInfo("UTC"))
        try:
            _json_message = json.loads(message)
        except Exception as e:
            logger.error(f"Failed to parse websocket message: {e}")
            return

        if "error" in _json_message:
            logger.warning(
                "WebSocket error response for method %s: %s",
                self._messages.get(_json_message.get("id", ""), "unknown"),
                _json_message["error"],
            )
            return

        _method = _json_message.get("method") or self._messages.get(
            _json_message.get("id", "")
        )
        if not _method:
            logger.warning(
                f"Received message with unknown method and id: {_json_message}"
            )
            return

        _handler = self._handlers.get(_method)
        if not _handler:
            logger.error(f"No handler implemented for method {_method}")
            return

        _result = _json_message.get("result") or _json_message.get("params")
        await _handler(_result)

    # endregion

    # region Message Handlers

    async def _on_login_response(self, result: Dict) -> None:
        if result.get("authenticated"):
            logger.info("WebSocket login successful.")
            await self._sync()
        else:
            logger.error("WebSocket login failed.")

    async def _on_sync_response(self, result: list) -> None:
        for item in result:
            if "lock_state" in item:
                self._charger_state.lock_status = LockStatus[item["lock_state"]]
            if "solar_mode" in item:
                self._charger_state.charging_mode = ChargingMode[item["solar_mode"]]
            if "brightness" in item:
                self._charger_state.led_brightness = item["brightness"]
        self._is_connected.set()

    # endregion

    # region Requests

    async def _login(self) -> bool:
        if self._websocket:
            message = {
                "id": self._generate_id_from_timestamp(),
                "method": "login",
                "params": {"token": self._get_access_token(), "version": 3},
            }
            await self._send_message(message)
            return True
        return False

    async def _sync(self) -> None:
        await self._send_message(
            {
                "id": self._generate_id_from_timestamp(),
                "method": "sync.snapshot",
            }
        )

    # endregion

    # region Public Methods

    async def get_charger_state(self) -> HypervoltChargerState:
        return self._charger_state

    async def set_charging_schedule(
        self,
        schedule: List[Dict],
        activation_mode: ActivationMode = ActivationMode.schedule,
    ) -> None:
        message = {
            "id": self._generate_id_from_timestamp(),
            "method": "schedule.set",
            "params": {
                "enabled": activation_mode == ActivationMode.schedule,
                "is_default": False,
                "type": "hypervolt",
                "sessions": schedule,
            },
        }
        await self._send_message(message)

    async def get_charging_schedule(self) -> None:
        message = {
            "id": self._generate_id_from_timestamp(),
            "method": "schedules.get",
        }
        await self._send_message(message)

    # endregion

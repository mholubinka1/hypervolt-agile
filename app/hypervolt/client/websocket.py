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
from hypervolt.client.protocol import (
    HypervoltChargerStateUpdateCallback,
    HypervoltProtocol,
    _generate_id,
)
from hypervolt.model import ActivationMode, HypervoltCharger
from websockets import Data

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_NAME)


_RECONNECT_DELAY_SECS = 5


class HypervoltWebSocketClient:
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

    def __init__(
        self,
        charger: HypervoltCharger,
        access_token_callback: Callable[[], str],
        on_state_update: HypervoltChargerStateUpdateCallback,
        on_clear_schedule: Callable[[], Awaitable[None]],
    ) -> None:
        self._charger = charger
        self._access_token_callback = access_token_callback

        self._websocket = None
        self._last_activity = None
        self._stop_requested = False

        self._connect_task = None
        self._is_connected = asyncio.Event()

        self._messages: Dict[str, str] = {}

        self._protocol = HypervoltProtocol(
            send_message=self._send_message,
            on_state_update=on_state_update,
            on_clear_schedule=on_clear_schedule,
            is_connected=self._is_connected,
        )

    # region Helpers

    def _get_access_token(self) -> str:
        return self._access_token_callback()

    def _get_user_agent(self) -> str:
        return "home-assistant-hypervolt-charger/0.0.0"

    # endregion

    # region Public Methods

    @property
    def is_connected(self) -> bool:
        return self._is_connected.is_set()

    async def sync_charger_state(self) -> None:
        await self._protocol.sync()

    async def set_lock_state(self, locked: bool) -> None:
        await self._send_message(
            {
                "id": _generate_id(),
                "method": "sync.apply",
                "params": {"is_locked": locked},
            }
        )

    async def set_charging_schedule(
        self,
        schedule: List[Dict],
        activation_mode: ActivationMode = ActivationMode.schedule,
    ) -> None:
        message = {
            "id": _generate_id(),
            "method": "schedule.set",
            "params": {
                "enabled": activation_mode == ActivationMode.schedule,
                "is_default": False,
                "type": "hypervolt",
                "sessions": schedule,
            },
        }
        await self._send_message(message)

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
                    await self._protocol.login(self._get_access_token())
                    await self._receive_messages_worker()
                if not self._stop_requested:
                    logger.info("Websocket connection closed, reconnecting.")
                    self._clear_connection_state()
                    await asyncio.sleep(_RECONNECT_DELAY_SECS)
            except CancelledError:
                logger.debug("Websocket connect task cancelled.")
                raise
            except Exception:
                logger.warning(
                    f"Websocket connection lost, reconnecting in {_RECONNECT_DELAY_SECS}s.",
                    exc_info=True,
                )
                self._clear_connection_state()
                await asyncio.sleep(_RECONNECT_DELAY_SECS)

    async def _receive_messages_worker(self) -> None:
        if not self._websocket:
            return
        async for message in self._websocket:
            await self._receive_message(message)

    def _clear_connection_state(self) -> None:
        self._websocket = None
        self._is_connected.clear()
        self._messages.clear()

    async def reconnect(self) -> None:
        if self._websocket:
            await self._websocket.close()

    async def disconnect(self) -> None:
        self._stop_requested = True
        if self._websocket:
            await self._websocket.close()
        self._clear_connection_state()
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except CancelledError:
                logger.debug("Websocket connect task cancelled.")

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
            try:
                await self._websocket.send(_json_message)
            except websockets.ConnectionClosed:
                logger.warning("Websocket closed during send.")
                self._clear_connection_state()
                return
            self._messages[message["id"]] = message["method"]
        else:
            logger.warning("Websocket is not connected, unable to send message.")

    async def _receive_message(self, message: Data) -> None:
        self._last_activity = datetime.now(ZoneInfo("UTC"))
        try:
            _json_message = json.loads(message)
        except Exception as e:
            logger.error(f"Failed to parse websocket message: {e}")
            return

        if "error" in _json_message:
            _error_message_id = _json_message.get("id")
            _error_message_method = (
                self._messages.pop(_error_message_id, "unknown")
                if _error_message_id
                else "unknown"
            )
            logger.warning(
                f"Websocket error response for method {_error_message_method}: {_json_message['error']}"
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

        _id: Optional[str] = _json_message.get("id")
        _result = _json_message.get("result") or _json_message.get("params")
        if _id:
            self._messages.pop(_id, None)
        await self._protocol.handle(_method, _result, _id)

    # endregion
